import { copyFileSync, cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import { spawn } from "node:child_process";

import {
  backendSourceRoot,
  canExecute,
  defaultRuntimeRoot,
  firstExisting,
  packagedPythonCandidates,
  readJson,
  relative,
  runtimeManifestName,
  verifyPackagedRuntime,
} from "./packagedRuntime.mjs";

const args = parseArgs(process.argv.slice(2));
const runtimeRoot = path.resolve(args.runtimeRoot || defaultRuntimeRoot);
const allowCrossArchSkip =
  args.allowCrossArchSkip === "1" ||
  args.allowCrossArchSkip === "true" ||
  process.env.NOOFY_ALLOW_CROSS_ARCH_BACKEND_SMOKE_SKIP === "1";

try {
  const verified = verifyPackagedRuntime({ runtimeRoot, requireBuiltFrontend: false });
  const python = firstExisting(packagedPythonCandidates(runtimeRoot, verified.target));
  if (!python) {
    throw new Error("Packaged smoke test could not find bundled Python.");
  }
  if (!canExecute(python)) {
    const message = `Packaged backend smoke cannot execute ${verified.target} Python on this host: ${relative(python)}`;
    if (allowCrossArchSkip) {
      console.warn(message);
      process.exit(0);
    }
    throw new Error(message);
  }

  const stageRoot = mkdtempSync(path.join(tmpdir(), "noofy-packaged-backend-smoke-"));
  try {
    stageRuntime(stageRoot, runtimeRoot);
    await startAndStopBackend(stageRoot, verified.target);
    console.log("Packaged backend smoke passed.");
  } finally {
    removeStageRuntime(stageRoot);
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

function stageRuntime(stageRoot, runtimeRoot) {
  cpSync(path.join(runtimeRoot, "python"), path.join(stageRoot, "python"), {
    recursive: true,
    force: true,
    verbatimSymlinks: false,
  });
  copyFileSync(path.join(runtimeRoot, runtimeManifestName), path.join(stageRoot, runtimeManifestName));
  mkdirSync(path.join(stageRoot, "backend"), { recursive: true });
  mkdirSync(path.join(stageRoot, "comfyui"), { recursive: true });
  writeFileSync(path.join(stageRoot, "comfyui", "main.py"), "");
  writeFileSync(path.join(stageRoot, "comfyui", "requirements.txt"), "");
  cpSync(path.join(backendSourceRoot, "app"), path.join(stageRoot, "backend", "app"), {
    recursive: true,
    force: true,
    verbatimSymlinks: false,
    filter: (source) => !source.includes("__pycache__") && !source.endsWith(".pyc"),
  });
  copyFileSync(path.join(backendSourceRoot, "pyproject.toml"), path.join(stageRoot, "backend", "pyproject.toml"));
}

async function startAndStopBackend(stageRoot, target) {
  const manifest = readJson(path.join(stageRoot, runtimeManifestName));
  const python = path.join(stageRoot, manifest.python.executable);
  const uv = path.join(stageRoot, manifest.uv.executable);
  const backendDir = path.join(stageRoot, "backend");
  const child = spawn(python, ["-m", "app", "--port", "0"], {
    cwd: backendDir,
    env: packagedBackendEnv(stageRoot, uv, target),
    stdio: ["ignore", "pipe", "pipe"],
  });

  let output = "";
  const timeout = setTimeout(() => {
    child.kill();
  }, 20_000);
  try {
    await new Promise((resolve, reject) => {
      child.stdout.on("data", (chunk) => {
        output += chunk.toString("utf8");
        if (output.includes("NOOFY_BACKEND_API_BASE_URL=")) {
          resolve();
        }
      });
      child.stderr.on("data", (chunk) => {
        output += chunk.toString("utf8");
      });
      child.on("error", reject);
      child.on("exit", (code) => {
        reject(new Error(`Packaged backend exited before handoff with code ${code}.\n${output}`));
      });
    });
  } finally {
    clearTimeout(timeout);
    await stopBackend(child);
  }
}

async function stopBackend(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return;
  }
  await new Promise((resolve) => {
    child.once("exit", resolve);
    child.kill();
    setTimeout(resolve, 5_000);
  });
}

function removeStageRuntime(stageRoot) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      rmSync(stageRoot, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
      return;
    } catch (error) {
      if (attempt === 4 || !isWindowsPermissionCleanupError(error)) {
        throw error;
      }
    }
  }
}

function isWindowsPermissionCleanupError(error) {
  return process.platform === "win32" && error && ["EBUSY", "ENOTEMPTY", "EPERM"].includes(error.code);
}

function packagedBackendEnv(stageRoot, uv, target) {
  const env = { ...process.env };
  for (const key of [
    "COMFYUI_BASE_URL",
    "COMFYUI_MANAGED_HOST",
    "COMFYUI_MANAGED_PORT",
    "COMFYUI_REPO_DIR",
    "COMFYUI_PYTHON_EXECUTABLE",
    "COMFYUI_RUNTIME_MODE",
    "COMFYUI_WS_URL",
    "CONDA_PREFIX",
    "NOOFY_BACKEND_DIR",
    "NOOFY_BACKEND_PYTHON",
    "NOOFY_BACKEND_SIDECAR",
    "NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES",
    "NOOFY_FORCE_PACKAGED_BACKEND",
    "NOOFY_PACKAGED_RUNTIME_DIR",
    "PYTHONHOME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
  ]) {
    delete env[key];
  }
  env.COMFYUI_RUNTIME_MODE = "managed";
  env.COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE = path.join(stageRoot, "python", target === "windows-x64" ? "python.exe" : "bin/python3");
  env.NOOFY_UV_EXECUTABLE = uv;
  env.NOOFY_BUNDLED_RESOURCE_DIR = stageRoot;
  env.NOOFY_BUNDLED_COMFYUI_DIR = path.join(stageRoot, "comfyui");
  env.NOOFY_BUNDLED_WORKFLOWS_DIR = path.join(stageRoot, "backend", "app", "workflows", "packages");
  env.PYTHONNOUSERSITE = "1";
  return env;
}

function parseArgs(argv) {
  const parsed = {};
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) {
      throw new Error(`Unexpected argument: ${key}`);
    }
    const normalized = key
      .slice(2)
      .replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    const value = argv[index + 1];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for ${key}`);
    }
    parsed[normalized] = value;
    index += 1;
  }
  return parsed;
}
