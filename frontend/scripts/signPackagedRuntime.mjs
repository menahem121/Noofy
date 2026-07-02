import { spawnSync } from "node:child_process";
import {
  existsSync,
  lstatSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  currentRuntimeTarget,
  defaultRuntimeRoot,
  packagedPythonCandidates,
  packagedUvCandidates,
  relative,
  resolveManifestPath,
  runtimeManifestName,
  sha256File,
} from "./packagedRuntime.mjs";

const identity = process.env.APPLE_SIGNING_IDENTITY;
const target = process.env.NOOFY_PACKAGED_RUNTIME_TARGET || currentRuntimeTarget();

try {
  if (process.platform !== "darwin" || target !== "macos-arm64") {
    console.log(`Skipping packaged runtime signing for ${target} on ${process.platform}.`);
    process.exit(0);
  }
  if (!identity) {
    console.warn("Skipping packaged runtime signing because APPLE_SIGNING_IDENTITY is not set.");
    process.exit(0);
  }

  signPackagedRuntime(defaultRuntimeRoot, identity);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

function signPackagedRuntime(runtimeRoot, signingIdentity) {
  if (!existsSync(runtimeRoot)) {
    throw new Error(`Packaged runtime root is missing: ${relative(runtimeRoot)}`);
  }

  const manifestPath = path.join(runtimeRoot, runtimeManifestName);
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const nativeFiles = listFiles(runtimeRoot)
    .filter(isMachO)
    .sort((left, right) => right.length - left.length);

  if (nativeFiles.length === 0) {
    throw new Error(`No Mach-O files found under ${relative(runtimeRoot)}.`);
  }

  for (const file of nativeFiles) {
    signFile(file, signingIdentity);
  }

  const python = resolveManifestPath(runtimeRoot, manifest.python.executable, "Packaged Python");
  const uv = resolveManifestPath(runtimeRoot, manifest.uv.executable, "Packaged uv");
  const pythonCandidate = packagedPythonCandidates(runtimeRoot, target).find((candidate) =>
    samePath(candidate, python),
  );
  const uvCandidate = packagedUvCandidates(runtimeRoot, target).find((candidate) =>
    samePath(candidate, uv),
  );
  if (!pythonCandidate || !uvCandidate) {
    throw new Error(`${runtimeManifestName} does not point at the expected Python/uv runtime paths.`);
  }

  manifest.python.sha256 = sha256File(python);
  manifest.uv.sha256 = sha256File(uv);
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);

  console.log(
    `Signed ${nativeFiles.length} packaged runtime Mach-O files with ${signingIdentity}.`,
  );
}

function listFiles(root) {
  const files = [];
  const stack = [root];
  while (stack.length > 0) {
    const current = stack.pop();
    for (const entry of readdirSync(current)) {
      const filePath = path.join(current, entry);
      const stats = lstatSync(filePath);
      if (stats.isSymbolicLink()) {
        continue;
      }
      if (stats.isDirectory()) {
        stack.push(filePath);
      } else if (stats.isFile()) {
        files.push(filePath);
      }
    }
  }
  return files;
}

function isMachO(filePath) {
  const result = spawnSync("file", ["-b", filePath], { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`Could not inspect ${relative(filePath)}: ${result.stderr || result.stdout}`);
  }
  return result.stdout.includes("Mach-O");
}

function signFile(filePath, signingIdentity) {
  const result = spawnSync(
    "codesign",
    [
      "--force",
      "--timestamp",
      "--options",
      "runtime",
      "--sign",
      signingIdentity,
      filePath,
    ],
    { encoding: "utf8" },
  );
  if (result.status !== 0) {
    throw new Error(
      [
        `Could not sign packaged runtime binary: ${relative(filePath)}`,
        result.stderr || result.stdout || `exit status ${result.status}`,
      ].join("\n"),
    );
  }
}

function samePath(left, right) {
  return path.resolve(left) === path.resolve(right);
}
