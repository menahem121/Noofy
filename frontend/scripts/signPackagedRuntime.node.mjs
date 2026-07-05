import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdirSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  codesignArgsForFile,
  macosPythonEntitlementsPath,
  usesMacosPythonEntitlements,
} from "./signPackagedRuntime.mjs";

test("macOS signing applies library-validation entitlement to the real packaged Python binary", {
  skip: process.platform === "win32",
}, () => {
  const runtimeRoot = tempRuntimeRoot("macos-python-entitlement");
  const pythonBin = path.join(runtimeRoot, "python", "bin");
  mkdirSync(pythonBin, { recursive: true });
  const realPython = path.join(pythonBin, "python3.13");
  const pythonSymlink = path.join(pythonBin, "python3");
  writeFileSync(realPython, "fake python", { mode: 0o755 });
  symlinkSync("python3.13", pythonSymlink);

  assert.equal(
    usesMacosPythonEntitlements(realPython, runtimeRoot, "macos-arm64"),
    true,
  );
  assert.deepEqual(
    codesignArgsForFile(realPython, "Developer ID Application: Test", {
      runtimeRoot,
      target: "macos-arm64",
      macosPythonEntitlementsPath: "/tmp/python-entitlements.plist",
    }),
    [
      "--force",
      "--timestamp",
      "--options",
      "runtime",
      "--entitlements",
      "/tmp/python-entitlements.plist",
      "--sign",
      "Developer ID Application: Test",
      realPython,
    ],
  );
});

test("macOS signing does not apply Python entitlements to non-Python runtime binaries", {
  skip: process.platform === "win32",
}, () => {
  const runtimeRoot = tempRuntimeRoot("macos-non-python-entitlement");
  const pythonBin = path.join(runtimeRoot, "python", "bin");
  mkdirSync(pythonBin, { recursive: true });
  const realPython = path.join(pythonBin, "python3.13");
  const uv = path.join(pythonBin, "uv");
  writeFileSync(realPython, "fake python", { mode: 0o755 });
  symlinkSync("python3.13", path.join(pythonBin, "python3"));
  writeFileSync(uv, "fake uv", { mode: 0o755 });

  assert.equal(usesMacosPythonEntitlements(uv, runtimeRoot, "macos-arm64"), false);
  assert.equal(
    codesignArgsForFile(uv, "Developer ID Application: Test", {
      runtimeRoot,
      target: "macos-arm64",
      macosPythonEntitlementsPath: "/tmp/python-entitlements.plist",
    }).includes("--entitlements"),
    false,
  );
});

test("packaged Python entitlements plist exists in the Tauri source tree", () => {
  assert.equal(existsSync(macosPythonEntitlementsPath()), true);
});

function tempRuntimeRoot(name) {
  return path.join(
    tmpdir(),
    `noofy-${name}-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  );
}
