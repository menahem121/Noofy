import { existsSync, renameSync, rmSync } from "node:fs";
import path from "node:path";
import process from "node:process";

import { frontendRoot, readJson, relative } from "./packagedRuntime.mjs";

const tauriConfig = readJson(path.join(frontendRoot, "src-tauri", "tauri.conf.json"));
const productName = tauriConfig.productName || "Noofy";
const version = tauriConfig.version;

if (!version) {
  console.error("Tauri version is missing from src-tauri/tauri.conf.json.");
  process.exit(1);
}

const bundleRoot =
  process.env.NOOFY_RELEASE_BUNDLE_ROOT ||
  path.join(frontendRoot, "src-tauri", "target", "release", "bundle");
const artifactGroups = {
  windows: [
    {
      dir: path.join(bundleRoot, "nsis"),
      from: `${productName}_${version}_x64-setup.exe`,
      to: `${productName}_${version}_Windows_x64-setup.exe`,
    },
  ],
  macos: [
    {
      dir: path.join(bundleRoot, "dmg"),
      from: `${productName}_${version}_aarch64.dmg`,
      to: `${productName}_${version}_MACOS_aarch64.dmg`,
    },
  ],
  linux: [
    {
      dir: path.join(bundleRoot, "deb"),
      from: `${productName}_${version}_amd64.deb`,
      to: `${productName}_${version}_LINUX_amd64.deb`,
    },
    {
      dir: path.join(bundleRoot, "appimage"),
      from: `${productName}_${version}_amd64.AppImage`,
      to: `${productName}_${version}_LINUX_amd64.AppImage`,
    },
  ],
};

const groupAliases = {
  appimage: "linux",
  deb: "linux",
  dmg: "macos",
  nsis: "windows",
};

const requestedGroups = parseRequestedGroups(process.argv.slice(2));
const groups = requestedGroups.length > 0 ? requestedGroups : Object.keys(artifactGroups);
let renamed = 0;

try {
  for (const group of groups) {
    const artifacts = artifactGroups[group];
    if (!artifacts) {
      throw new Error(`Unknown release artifact group: ${group}`);
    }
    for (const artifact of artifacts) {
      renamed += renameArtifact(artifact);
    }
  }
  if (renamed === 0) {
    console.log("No release artifacts needed renaming.");
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

function parseRequestedGroups(args) {
  const groups = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--bundles" || arg === "-b") {
      const value = args[index + 1];
      if (!value || value.startsWith("-")) {
        throw new Error(`Missing value for ${arg}`);
      }
      groups.push(...value.split(",").map((entry) => entry.trim()).filter(Boolean));
      index += 1;
      continue;
    }
    if (arg.startsWith("--")) {
      continue;
    }
    groups.push(...arg.split(",").map((entry) => entry.trim()).filter(Boolean));
  }
  return [...new Set(groups.map((group) => groupAliases[group] || group))];
}

function renameArtifact({ dir, from, to }) {
  if (!existsSync(dir)) {
    return 0;
  }
  const source = path.join(dir, from);
  const destination = path.join(dir, to);
  if (!existsSync(source)) {
    return 0;
  }
  if (source === destination) {
    return 0;
  }
  if (existsSync(destination)) {
    rmSync(destination, { force: true });
  }
  renameSync(source, destination);
  renameSiblingSignature(dir, from, to);
  console.log(`Renamed release artifact: ${relative(source)} -> ${relative(destination)}`);
  return 1;
}

function renameSiblingSignature(dir, from, to) {
  const source = path.join(dir, `${from}.sig`);
  if (!existsSync(source)) {
    return;
  }
  const destination = path.join(dir, `${to}.sig`);
  if (existsSync(destination)) {
    rmSync(destination, { force: true });
  }
  renameSync(source, destination);
}
