#!/usr/bin/env node
/**
 * Boundary check: frontend must never call ComfyUI endpoints directly.
 *
 * Scans frontend/src for fetch() or WebSocket() calls that target port 8188
 * or known ComfyUI-internal URL paths, instead of routing through the Noofy
 * backend API at /api/*.
 *
 * Run:  node scripts/check-no-comfyui-calls.mjs
 * Exit: 0 = clean, 1 = violations found
 */

import { readFileSync, readdirSync, statSync } from "fs";
import { join, relative } from "path";
import { fileURLToPath } from "url";

const ROOT = join(fileURLToPath(import.meta.url), "../../frontend/src");

// Each pattern flags a category of direct ComfyUI network call.
const VIOLATION_PATTERNS = [
  {
    label: "fetch() to ComfyUI port 8188",
    pattern: /fetch\s*\(\s*[`"'](https?:\/\/[^`"']*:8188)/,
  },
  {
    label: "WebSocket() to ComfyUI port 8188",
    pattern: /new\s+WebSocket\s*\(\s*[`"'](wss?:\/\/[^`"']*:8188)/,
  },
  {
    label: "fetch() to ComfyUI-internal endpoint bypassing /api/",
    // ComfyUI-only paths: /prompt /queue /history /object_info /system_stats /upload/image /upload/mask
    // Flag only when used directly in fetch(), not prefixed by /api/
    pattern: /fetch\s*\(\s*[`"'](?!\/api\/)(\/prompt|\/queue|\/history|\/object_info|\/system_stats|\/upload\/image|\/upload\/mask)\b/,
  },
];

const SKIP_DIRS = new Set(["node_modules", "dist", "__pycache__", ".venv"]);

function walkSync(dir, results = []) {
  for (const entry of readdirSync(dir)) {
    if (entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIRS.has(entry)) walkSync(full, results);
    } else if (/\.(tsx?|jsx?)$/.test(entry) && entry !== "vite-env.d.ts") {
      results.push(full);
    }
  }
  return results;
}

const violations = [];

for (const file of walkSync(ROOT)) {
  const rel = relative(ROOT, file);
  const lines = readFileSync(file, "utf8").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*(\/\/|\/\*|\*)/.test(line)) continue; // skip comment lines
    for (const { label, pattern } of VIOLATION_PATTERNS) {
      if (pattern.test(line)) {
        violations.push({ file: rel, lineNum: i + 1, line: line.trim(), label });
      }
    }
  }
}

if (violations.length === 0) {
  console.log("✓ No direct ComfyUI calls found in frontend/src.");
  process.exit(0);
} else {
  console.error(`✗ ${violations.length} direct ComfyUI call(s) found in frontend/src:\n`);
  for (const v of violations) {
    console.error(`  ${v.file}:${v.lineNum}  [${v.label}]`);
    console.error(`    ${v.line}`);
  }
  console.error(`\nFrontend must call /api/* on the Noofy backend only.`);
  console.error(`See frontend/src/AGENTS.md for the boundary rule.`);
  process.exit(1);
}
