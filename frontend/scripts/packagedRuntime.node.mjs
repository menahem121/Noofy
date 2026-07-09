import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import process from "node:process";

import {
  backendArtifactMetadata,
  frontendRoot,
  readJson,
  runtimeTargetFor,
  runtimeManifestName,
  sha256File,
  verifyTauriLinuxBundleTargets,
  verifyUvExcludesCapability,
  verifyPackagedRuntime,
} from "./packagedRuntime.mjs";

const testRuntimeTarget = "linux-x64";
const tauriConfig = readJson(path.join(frontendRoot, "src-tauri", "tauri.conf.json"));
const testProductName = tauriConfig.productName || "Noofy";
const testAppVersion = tauriConfig.version;

test("verifyPackagedRuntime rejects a missing manifest", () => {
  const runtimeRoot = tempDir("missing-manifest");

  assert.throws(
    () => verifyPackagedRuntime({ runtimeRoot, target: testRuntimeTarget, requireBuiltFrontend: false }),
    /Packaged runtime manifest is missing/,
  );
});

test("runtimeTargetFor rejects unsupported desktop packaging platforms", () => {
  assert.throws(
    () => runtimeTargetFor("darwin", "x64"),
    /Unsupported packaged runtime target/,
  );
  assert.throws(
    () => runtimeTargetFor("linux", "arm64"),
    /Unsupported packaged runtime target/,
  );
});

test("Linux release bundles include AppImage alongside deb", () => {
  assert.doesNotThrow(() => verifyTauriLinuxBundleTargets());
});

test("renameReleaseArtifacts normalizes release artifact names", () => {
  const bundleRoot = tempDir("release-artifacts");
  const artifacts = [
    ["nsis", `${testProductName}_${testAppVersion}_x64-setup.exe`, `${testProductName}_Windows_x64-setup.exe`],
    ["dmg", `${testProductName}_${testAppVersion}_aarch64.dmg`, `${testProductName}_MACOS_aarch64.dmg`],
    ["deb", `${testProductName}_${testAppVersion}_amd64.deb`, `${testProductName}_LINUX_amd64.deb`],
    ["appimage", `${testProductName}_${testAppVersion}_amd64.AppImage`, `${testProductName}_LINUX_amd64.AppImage`],
  ];
  for (const [dir, source] of artifacts) {
    writeFixture(path.join(bundleRoot, dir, source), "installer");
  }
  writeFixture(path.join(bundleRoot, "appimage", `${testProductName}_${testAppVersion}_amd64.AppImage.sig`), "signature");

  const result = spawnSync(
    process.execPath,
    [path.resolve("scripts", "renameReleaseArtifacts.mjs")],
    {
      cwd: path.resolve("."),
      encoding: "utf-8",
      env: {
        ...process.env,
        NOOFY_RELEASE_BUNDLE_ROOT: bundleRoot,
      },
    },
  );

  assert.equal(result.status, 0, result.stderr || result.stdout);
  for (const [dir, source, destination] of artifacts) {
    assert.equal(existsSync(path.join(bundleRoot, dir, source)), false);
    assert.equal(existsSync(path.join(bundleRoot, dir, destination)), true);
  }
  assert.equal(
    existsSync(path.join(bundleRoot, "appimage", `${testProductName}_LINUX_amd64.AppImage.sig`)),
    true,
  );
});

test("renameReleaseArtifacts accepts Tauri bundle args", () => {
  const bundleRoot = tempDir("release-artifacts-args");
  const source = `${testProductName}_${testAppVersion}_x64-setup.exe`;
  writeFixture(path.join(bundleRoot, "nsis", source), "installer");

  const result = spawnSync(
    process.execPath,
    [path.resolve("scripts", "renameReleaseArtifacts.mjs"), "--bundles", "nsis"],
    {
      cwd: path.resolve("."),
      encoding: "utf-8",
      env: {
        ...process.env,
        NOOFY_RELEASE_BUNDLE_ROOT: bundleRoot,
      },
    },
  );

  assert.equal(result.status, 0, result.stderr || result.stdout);
  assert.equal(existsSync(path.join(bundleRoot, "nsis", source)), false);
  assert.equal(
    existsSync(path.join(bundleRoot, "nsis", `${testProductName}_Windows_x64-setup.exe`)),
    true,
  );
});

test("verifyPackagedRuntime accepts a manifest with matching executable checksums", { skip: process.platform === "win32" }, () => {
  const runtimeRoot = tempDir("valid-runtime");
  const python = fakeExecutable(path.join(runtimeRoot, "python", "bin", "python3"), {
    versionCommand: "Python 3.13.7",
  });
  const uv = fakeExecutable(path.join(runtimeRoot, "python", "bin", "uv"), {
    versionCommand: "uv 0.11.10",
  });
  writeManifest(runtimeRoot, python, uv);

  const result = verifyPackagedRuntime({
    runtimeRoot,
    target: testRuntimeTarget,
    requireBuiltFrontend: false,
  });

  assert.equal(result.python, python);
  assert.equal(result.uv, uv);
});

test("verifyPackagedRuntime rejects checksum drift", { skip: process.platform === "win32" }, () => {
  const runtimeRoot = tempDir("checksum-drift");
  const python = fakeExecutable(path.join(runtimeRoot, "python", "bin", "python3"), {
    versionCommand: "Python 3.13.7",
  });
  const uv = fakeExecutable(path.join(runtimeRoot, "python", "bin", "uv"), {
    versionCommand: "uv 0.11.10",
  });
  writeManifest(runtimeRoot, python, uv);
  writeFileSync(python, `${fakeExecutableSource("Python 3.13.7")}\n// changed\n`);

  assert.throws(
    () => verifyPackagedRuntime({ runtimeRoot, target: testRuntimeTarget, requireBuiltFrontend: false }),
    /checksum mismatch/,
  );
});

test("preparePackagedRuntime copies an explicit source artifact and writes a manifest", { skip: process.platform === "win32" }, () => {
  const sourceRoot = tempDir("prepare-source");
  const outputRoot = tempDir("prepare-output");
  fakeExecutable(path.join(sourceRoot, "python", "bin", "python3"), {
    versionCommand: "Python 3.13.7",
  });
  fakeExecutable(path.join(sourceRoot, "python", "bin", "uv"), {
    versionCommand: "uv 0.11.10",
  });
  writeFixture(path.join(sourceRoot, "python", "lib", "tcl9.0", "init.tcl"), "package require Tcl");
  writeFixture(path.join(sourceRoot, "python", "lib", "tk9.0", "tk.tcl"), "package require Tk");
  writeFixture(path.join(sourceRoot, "python", "lib", "itcl4.3.5", "pkgIndex.tcl"), "package require Itcl");
  writeFixture(path.join(sourceRoot, "python", "lib", "thread3.0.4", "pkgIndex.tcl"), "package require Thread");
  writeFixture(path.join(sourceRoot, "python", "lib", "libtcl9.0.so"), "");
  writeFixture(path.join(sourceRoot, "python", "lib", "libtcl9tk9.0.so"), "");
  writeFixture(
    path.join(sourceRoot, "python", "lib", "python3.13", "lib-dynload", "_tkinter.test.so"),
    "",
  );
  writeFixture(path.join(sourceRoot, "python", "lib", "python3.13", "tkinter", "__init__.py"), "");

  const result = spawnSync(
    process.execPath,
    [
      path.resolve("scripts", "preparePackagedRuntime.mjs"),
      "--source",
      sourceRoot,
      "--output",
      outputRoot,
      "--target",
      testRuntimeTarget,
      "--python-build-id",
      "cpython-3.13-noofy-test",
    ],
    {
      cwd: path.resolve("."),
      encoding: "utf-8",
    },
  );

  assert.equal(result.status, 0, result.stderr || result.stdout);
  const manifestPath = path.join(outputRoot, runtimeManifestName);
  assert.match(result.stdout, /Packaged runtime prepared/);
  assert.doesNotThrow(() =>
    verifyPackagedRuntime({
      runtimeRoot: outputRoot,
      target: testRuntimeTarget,
      requireBuiltFrontend: false,
    }),
  );
  assert.equal(existsSync(path.join(outputRoot, "python", "lib", "tcl9.0")), false);
  assert.equal(existsSync(path.join(outputRoot, "python", "lib", "tk9.0")), false);
  assert.equal(existsSync(path.join(outputRoot, "python", "lib", "itcl4.3.5")), false);
  assert.equal(existsSync(path.join(outputRoot, "python", "lib", "thread3.0.4")), false);
  assert.equal(existsSync(path.join(outputRoot, "python", "lib", "libtcl9.0.so")), false);
  assert.equal(existsSync(path.join(outputRoot, "python", "lib", "libtcl9tk9.0.so")), false);
  assert.equal(
    existsSync(path.join(outputRoot, "python", "lib", "python3.13", "lib-dynload", "_tkinter.test.so")),
    false,
  );
  assert.equal(existsSync(path.join(outputRoot, "python", "lib", "python3.13", "tkinter")), false);
  assert.match(manifestPath, /runtime-manifest\.json$/);
});

test("verifyUvExcludesCapability rejects uv that retains an excluded dependency", { skip: process.platform === "win32" }, () => {
  const uv = path.join(tempDir("uv-excludes"), "uv");
  writeFileSync(
    uv,
    `#!/usr/bin/env node
const args = process.argv.slice(2);
const output = args[args.indexOf("--output-file") + 1];
require("node:fs").writeFileSync(
  output,
  "requests-cache==1.2.1\\n    --hash=sha256:${"a".repeat(64)}\\n" +
    "requests==2.32.5\\n    --hash=sha256:${"b".repeat(64)}\\n",
);
`,
    { mode: 0o755 },
  );

  assert.throws(
    () => verifyUvExcludesCapability(uv),
    /required transitive --excludes behavior/,
  );
});

function tempDir(name) {
  return mkdtempSync(path.join(tmpdir(), `noofy-${name}-`));
}

function fakeExecutable(filePath, { versionCommand }) {
  mkdirSync(path.dirname(filePath), { recursive: true });
  writeFileSync(filePath, fakeExecutableSource(versionCommand), { mode: 0o755 });
  return filePath;
}

function writeFixture(filePath, contents) {
  mkdirSync(path.dirname(filePath), { recursive: true });
  writeFileSync(filePath, contents);
}

function fakeExecutableSource(versionCommand) {
  return `#!/usr/bin/env node
if (process.argv[2] === "--version") {
  console.log(${JSON.stringify(versionCommand)});
  process.exit(0);
}
if (process.argv[2] === "-c") {
  process.exit(0);
}
process.exit(1);
`;
}

function writeManifest(runtimeRoot, python, uv) {
  writeFileSync(
    path.join(runtimeRoot, runtimeManifestName),
    `${JSON.stringify(
      {
        schemaVersion: 1,
        layoutVersion: 1,
        target: testRuntimeTarget,
        python: {
          implementation: "CPython",
          version: "3.13.7",
          buildId: "cpython-3.13-noofy-test",
          executable: "python/bin/python3",
          sha256: sha256File(python),
        },
        uv: {
          version: "0.11.10",
          executable: "python/bin/uv",
          sha256: sha256File(uv),
        },
        backend: backendArtifactMetadata(),
      },
      null,
      2,
    )}\n`,
  );
}
