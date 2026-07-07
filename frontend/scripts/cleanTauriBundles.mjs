import { existsSync, rmSync } from "node:fs";
import path from "node:path";
import process from "node:process";

import { frontendRoot, relative } from "./packagedRuntime.mjs";

const bundleRoot = path.join(frontendRoot, "src-tauri", "target", "release", "bundle");
const aliases = {
  appimage: ["appimage", "appimage_deb"],
  deb: ["deb"],
};

try {
  const requested = process.argv.slice(2);
  const bundleTypes = requested.length > 0 ? requested : Object.keys(aliases);
  for (const bundleType of bundleTypes) {
    const targets = aliases[bundleType];
    if (!targets) {
      throw new Error(`Unknown Tauri bundle staging target: ${bundleType}`);
    }
    for (const target of targets) {
      const targetPath = path.join(bundleRoot, target);
      if (existsSync(targetPath)) {
        rmSync(targetPath, { recursive: true, force: true });
        console.log(`Removed stale Tauri bundle staging: ${relative(targetPath)}`);
      }
    }
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
