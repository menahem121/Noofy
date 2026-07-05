import { spawnSync } from "node:child_process";
import {
  existsSync,
  lstatSync,
  readdirSync,
  readFileSync,
  realpathSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";

import {
  currentRuntimeTarget,
  defaultRuntimeRoot,
  frontendRoot,
  packagedPythonCandidates,
  packagedUvCandidates,
  relative,
  resolveManifestPath,
  runtimeManifestName,
  sha256File,
} from "./packagedRuntime.mjs";

const defaultMacosPythonEntitlementsPath = path.join(
  frontendRoot,
  "src-tauri",
  "entitlements",
  "macos-python-runtime.plist",
);

if (isCliEntrypoint()) {
  try {
    const identity = process.env.APPLE_SIGNING_IDENTITY;
    const target = process.env.NOOFY_PACKAGED_RUNTIME_TARGET || currentRuntimeTarget();

    if (process.platform !== "darwin" || target !== "macos-arm64") {
      console.log(`Skipping packaged runtime signing for ${target} on ${process.platform}.`);
      process.exit(0);
    }
    if (!identity) {
      console.warn("Skipping packaged runtime signing because APPLE_SIGNING_IDENTITY is not set.");
      process.exit(0);
    }

    signPackagedRuntime(defaultRuntimeRoot, identity, { target });
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  }
}

export function signPackagedRuntime(
  runtimeRoot,
  signingIdentity,
  {
    target = currentRuntimeTarget(),
    macosPythonEntitlementsPath = defaultMacosPythonEntitlementsPath,
  } = {},
) {
  if (!existsSync(runtimeRoot)) {
    throw new Error(`Packaged runtime root is missing: ${relative(runtimeRoot)}`);
  }
  if (target === "macos-arm64" && !existsSync(macosPythonEntitlementsPath)) {
    throw new Error(
      `macOS packaged Python entitlements file is missing: ${relative(macosPythonEntitlementsPath)}`,
    );
  }

  const manifestPath = path.join(runtimeRoot, runtimeManifestName);
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const nativeFiles = listFiles(runtimeRoot)
    .filter(isMachO)
    .sort((left, right) => right.length - left.length);

  if (nativeFiles.length === 0) {
    throw new Error(`No Mach-O files found under ${relative(runtimeRoot)}.`);
  }

  let pythonEntitlementSignatures = 0;
  for (const file of nativeFiles) {
    const codesignArgs = codesignArgsForFile(file, signingIdentity, {
      runtimeRoot,
      target,
      macosPythonEntitlementsPath,
    });
    if (codesignArgs.includes("--entitlements")) {
      pythonEntitlementSignatures += 1;
    }
    signFile(file, codesignArgs);
  }

  if (target === "macos-arm64" && pythonEntitlementSignatures === 0) {
    throw new Error(
      "No packaged Python Mach-O executable was signed with macOS runtime entitlements.",
    );
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
  if (pythonEntitlementSignatures > 0) {
    console.log(
      `Applied macOS Python runtime entitlements to ${pythonEntitlementSignatures} file(s).`,
    );
  }
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

export function codesignArgsForFile(
  filePath,
  signingIdentity,
  {
    runtimeRoot = defaultRuntimeRoot,
    target = currentRuntimeTarget(),
    macosPythonEntitlementsPath = defaultMacosPythonEntitlementsPath,
  } = {},
) {
  const args = ["--force", "--timestamp", "--options", "runtime"];
  if (usesMacosPythonEntitlements(filePath, runtimeRoot, target)) {
    args.push("--entitlements", macosPythonEntitlementsPath);
  }
  args.push("--sign", signingIdentity, filePath);
  return args;
}

export function usesMacosPythonEntitlements(filePath, runtimeRoot, target) {
  if (target !== "macos-arm64") {
    return false;
  }
  return packagedPythonExecutableRealPaths(runtimeRoot, target).has(
    realpathSync(filePath),
  );
}

export function macosPythonEntitlementsPath() {
  return defaultMacosPythonEntitlementsPath;
}

function packagedPythonExecutableRealPaths(runtimeRoot, target) {
  const realPaths = new Set();
  for (const candidate of packagedPythonCandidates(runtimeRoot, target)) {
    if (!existsSync(candidate)) {
      continue;
    }
    realPaths.add(realpathSync(candidate));
  }
  return realPaths;
}

function signFile(filePath, codesignArgs) {
  const result = spawnSync("codesign", codesignArgs, {
    encoding: "utf8",
  });
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

function isCliEntrypoint() {
  return Boolean(
    process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href,
  );
}
