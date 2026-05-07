import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(frontendRoot, "..");
const runtimeRoot = path.join(frontendRoot, "src-tauri", "resources", "noofy-runtime");

function relative(filePath) {
  return path.relative(repoRoot, filePath) || ".";
}

function firstExisting(candidates) {
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

function requireFile(filePath, label) {
  if (!existsSync(filePath)) {
    throw new Error(`${label} is missing: ${relative(filePath)}`);
  }
}

function packagedPythonCandidates() {
  if (process.platform === "win32") {
    return [
      path.join(runtimeRoot, "python", "python.exe"),
      path.join(runtimeRoot, "python", "Scripts", "python.exe"),
    ];
  }
  return [
    path.join(runtimeRoot, "python", "bin", "python3"),
    path.join(runtimeRoot, "python", "bin", "python"),
  ];
}

function packagedUvCandidates() {
  if (process.platform === "win32") {
    return [
      path.join(runtimeRoot, "python", "uv.exe"),
      path.join(runtimeRoot, "python", "Scripts", "uv.exe"),
      path.join(runtimeRoot, "tools", "uv.exe"),
    ];
  }
  return [
    path.join(runtimeRoot, "python", "bin", "uv"),
    path.join(runtimeRoot, "tools", "uv"),
  ];
}

try {
  if (process.env.NOOFY_SKIP_PACKAGED_RUNTIME_CHECK === "1") {
    console.warn("Skipping packaged runtime verification because NOOFY_SKIP_PACKAGED_RUNTIME_CHECK=1.");
    process.exit(0);
  }

  requireFile(path.join(frontendRoot, "dist", "index.html"), "Built frontend entrypoint");
  requireFile(path.join(repoRoot, "backend", "app", "__main__.py"), "Backend module entrypoint");
  requireFile(path.join(repoRoot, "third_party", "comfyui", "main.py"), "Bundled ComfyUI entrypoint");
  requireFile(path.join(repoRoot, "third_party", "comfyui", "requirements.txt"), "Bundled ComfyUI requirements");

  const python = firstExisting(packagedPythonCandidates());
  if (!python) {
    throw new Error(
      [
        "Packaged Python runtime is missing.",
        `Expected one of: ${packagedPythonCandidates().map(relative).join(", ")}`,
        "Final installers must bundle a Noofy-owned Python runtime; they must not rely on system Python, Homebrew, Conda, or backend/.venv.",
      ].join("\n"),
    );
  }
  const pythonCheck = spawnSync(
    python,
    [
      "-c",
      [
        "import ensurepip, venv",
        "import app",
        "import cryptography, fastapi, httpx, pydantic, uvicorn, websockets",
      ].join("; "),
    ],
    {
      env: { ...process.env, PYTHONPATH: path.join(repoRoot, "backend") },
      encoding: "utf-8",
    },
  );
  if (pythonCheck.status !== 0) {
    throw new Error(
      [
        "Packaged Python runtime cannot import Noofy backend dependencies.",
        `Python: ${relative(python)}`,
        pythonCheck.error?.message || pythonCheck.stderr || pythonCheck.stdout || `exit status ${pythonCheck.status}`,
      ].join("\n"),
    );
  }

  const uv = firstExisting(packagedUvCandidates());
  if (!uv) {
    throw new Error(
      [
        "Packaged uv executable is missing.",
        `Expected one of: ${packagedUvCandidates().map(relative).join(", ")}`,
        "Noofy uses this bundled uv for isolated workflow dependency environments instead of a global developer tool.",
      ].join("\n"),
    );
  }

  console.log(`Packaged runtime verified: ${relative(python)}; ${relative(uv)}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
