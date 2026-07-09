import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

export const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const repoRoot = path.resolve(frontendRoot, "..");
export const defaultRuntimeRoot = path.join(
  frontendRoot,
  "src-tauri",
  "resources",
  "noofy-runtime",
);
export const runtimeManifestName = "runtime-manifest.json";
export const runtimeLayoutVersion = 1;
export const backendSourceRoot = path.join(repoRoot, "backend");
export const backendPackagedPath = "backend";
export const backendAppPackagedPath = "backend/app";
export const backendPyprojectPackagedPath = "backend/pyproject.toml";
const runtimeToolVersions = readJson(
  path.join(backendSourceRoot, "app", "runtime", "runtime_tool_versions.json"),
);
export const supportedUvVersion = runtimeToolVersions.uv;

export const requiredBackendImports = [
  "app",
  "app.main",
  "cryptography",
  "ensurepip",
  "fastapi",
  "httpx",
  "pydantic",
  "uvicorn",
  "venv",
  "websockets",
];

export function relative(filePath, root = repoRoot) {
  return path.relative(root, filePath) || ".";
}

export function currentRuntimeTarget() {
  const explicitTarget = process.env.NOOFY_PACKAGED_RUNTIME_TARGET;
  if (explicitTarget) {
    assertSupportedRuntimeTarget(explicitTarget);
    return explicitTarget;
  }
  return runtimeTargetFor(process.platform, process.arch);
}

export function runtimeTargetFor(platform, arch) {
  if (platform === "darwin" && arch === "arm64") {
    return "macos-arm64";
  }
  if (platform === "win32" && arch === "x64") {
    return "windows-x64";
  }
  if (platform === "linux" && arch === "x64") {
    return "linux-x64";
  }
  throw new Error(
    `Unsupported packaged runtime target: platform=${platform}, arch=${arch}. ` +
      "Noofy currently packages macOS Apple Silicon, Windows x64, and Linux x64 runtimes.",
  );
}

function assertSupportedRuntimeTarget(target) {
  if (!["macos-arm64", "windows-x64", "linux-x64"].includes(target)) {
    throw new Error(
      `Unsupported packaged runtime target: ${target}. ` +
        "Noofy currently packages macOS Apple Silicon, Windows x64, and Linux x64 runtimes.",
    );
  }
}

export function firstExisting(candidates) {
  return candidates.find((candidate) => existsSync(candidate)) ?? null;
}

export function packagedPythonCandidates(runtimeRoot = defaultRuntimeRoot, target = currentRuntimeTarget()) {
  if (target === "windows-x64") {
    return [
      path.join(runtimeRoot, "python", "python.exe"),
      path.join(runtimeRoot, "python", "Scripts", "python.exe"),
      path.join(runtimeRoot, "backend-python", "python.exe"),
      path.join(runtimeRoot, "backend-python", "Scripts", "python.exe"),
    ];
  }
  return [
    path.join(runtimeRoot, "python", "bin", "python3"),
    path.join(runtimeRoot, "python", "bin", "python"),
    path.join(runtimeRoot, "backend-python", "bin", "python3"),
    path.join(runtimeRoot, "backend-python", "bin", "python"),
  ];
}

export function packagedUvCandidates(runtimeRoot = defaultRuntimeRoot, target = currentRuntimeTarget()) {
  if (target === "windows-x64") {
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

export function requireFile(filePath, label) {
  if (!existsSync(filePath)) {
    throw new Error(`${label} is missing: ${relative(filePath)}`);
  }
  if (!statSync(filePath).isFile()) {
    throw new Error(`${label} is not a file: ${relative(filePath)}`);
  }
}

export function requireContainedFile(filePath, runtimeRoot, label) {
  requireFile(filePath, label);
  const rootReal = realpathSync(runtimeRoot);
  const fileReal = realpathSync(filePath);
  if (!isPathInside(fileReal, rootReal)) {
    throw new Error(
      `${label} resolves outside the packaged runtime root: ${relative(filePath)} -> ${fileReal}`,
    );
  }
}

export function isPathInside(child, parent) {
  const relativePath = path.relative(parent, child);
  return relativePath === "" || (!relativePath.startsWith("..") && !path.isAbsolute(relativePath));
}

export function relativeManifestPath(filePath, runtimeRoot) {
  const value = path.relative(runtimeRoot, filePath).split(path.sep).join("/");
  if (!value || value.startsWith("../") || path.isAbsolute(value)) {
    throw new Error(`Packaged runtime path is outside runtime root: ${filePath}`);
  }
  return value;
}

export function resolveManifestPath(runtimeRoot, manifestPath, label) {
  if (typeof manifestPath !== "string" || manifestPath.length === 0) {
    throw new Error(`${label} path is missing from ${runtimeManifestName}.`);
  }
  if (path.isAbsolute(manifestPath) || manifestPath.split(/[\\/]/).includes("..")) {
    throw new Error(`${label} path must be relative to the packaged runtime root: ${manifestPath}`);
  }
  return path.join(runtimeRoot, manifestPath);
}

export function sha256File(filePath) {
  const hash = createHash("sha256");
  hash.update(readFileSync(filePath));
  return hash.digest("hex");
}

export function readJson(filePath) {
  try {
    return JSON.parse(readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new Error(`Could not read ${relative(filePath)}: ${error.message}`);
  }
}

export function runChecked(command, args, options = {}) {
  const result = spawnSync(command, args, {
    ...options,
    encoding: "utf-8",
  });
  if (result.status !== 0) {
    throw new Error(
      [
        `Command failed: ${relative(command)} ${args.join(" ")}`,
        result.error?.message || result.stderr || result.stdout || `exit status ${result.status}`,
      ].join("\n"),
    );
  }
  return result;
}

export function runMaybe(command, args, options = {}) {
  return spawnSync(command, args, {
    ...options,
    encoding: "utf-8",
  });
}

export function pythonVersion(python) {
  const result = runChecked(python, ["--version"]);
  const output = `${result.stdout} ${result.stderr}`.trim();
  const match = output.match(/\bPython\s+([0-9][^\s]*)/);
  if (!match) {
    throw new Error(`Could not parse Python version from: ${output}`);
  }
  return match[1];
}

export function uvVersion(uv) {
  const result = runChecked(uv, ["--version"]);
  const output = `${result.stdout} ${result.stderr}`.trim();
  const match = output.match(/\buv\s+([0-9][^\s]*)/i);
  if (!match) {
    throw new Error(`Could not parse uv version from: ${output}`);
  }
  return match[1];
}

export function assertBackendImports(python, backendRoot = backendSourceRoot) {
  runChecked(
    python,
    [
      "-c",
      requiredBackendImports.map((moduleName) => `import ${moduleName}`).join("; "),
    ],
    {
      env: {
        ...process.env,
        PYTHONPATH: backendRoot,
        PYTHONNOUSERSITE: "1",
      },
    },
  );
}

export function verifyPackagedRuntime({
  runtimeRoot = defaultRuntimeRoot,
  target,
  requireBuiltFrontend = true,
} = {}) {
  if (process.env.NOOFY_SKIP_PACKAGED_RUNTIME_CHECK === "1") {
    return {
      skipped: true,
      message: "Skipping packaged runtime verification because NOOFY_SKIP_PACKAGED_RUNTIME_CHECK=1.",
    };
  }
  const expectedTarget = target || currentRuntimeTarget();

  if (requireBuiltFrontend) {
    requireFile(path.join(frontendRoot, "dist", "index.html"), "Built frontend entrypoint");
  }
  verifyBackendSourceFiles();
  verifyTauriBackendResourceMapping();
  requireFile(path.join(repoRoot, "third_party", "comfyui", "main.py"), "Bundled ComfyUI entrypoint");
  requireFile(
    path.join(repoRoot, "third_party", "comfyui", "requirements.txt"),
    "Bundled ComfyUI requirements",
  );

  const manifestPath = path.join(runtimeRoot, runtimeManifestName);
  requireFile(manifestPath, "Packaged runtime manifest");
  const manifest = readJson(manifestPath);
  verifyRuntimeManifest(manifest, { runtimeRoot, target: expectedTarget });

  const python = resolveManifestPath(runtimeRoot, manifest.python.executable, "Packaged Python");
  const uv = resolveManifestPath(runtimeRoot, manifest.uv.executable, "Packaged uv");
  verifyBackendManifest(manifest.backend);
  const backendRoot = bundledBackendRoot(runtimeRoot);
  const importBackendRoot = existsSync(path.join(backendRoot, "app", "__main__.py"))
    ? backendRoot
    : backendSourceRoot;
  if (canExecute(python)) {
    assertBackendImports(python, importBackendRoot);
  } else {
    assertBackendDependencyFiles(runtimeRoot, manifest.python.version, resultTargetFromManifest(manifest));
  }

  return {
    skipped: false,
    runtimeRoot,
    target: expectedTarget,
    python,
    uv,
    manifest,
  };
}

export function verifyRuntimeManifest(manifest, { runtimeRoot = defaultRuntimeRoot, target } = {}) {
  const expectedTarget = target || currentRuntimeTarget();
  assertSupportedRuntimeTarget(expectedTarget);
  if (!manifest || typeof manifest !== "object") {
    throw new Error(`${runtimeManifestName} must contain a JSON object.`);
  }
  if (manifest.schemaVersion !== 1) {
    throw new Error(`${runtimeManifestName} schemaVersion must be 1.`);
  }
  if (manifest.layoutVersion !== runtimeLayoutVersion) {
    throw new Error(`${runtimeManifestName} layoutVersion must be ${runtimeLayoutVersion}.`);
  }
  if (manifest.target !== expectedTarget) {
    throw new Error(
      `Packaged runtime target mismatch: expected ${expectedTarget}, found ${manifest.target ?? "<missing>"}.`,
    );
  }
  if (!manifest.python || typeof manifest.python !== "object") {
    throw new Error(`${runtimeManifestName} is missing python metadata.`);
  }
  if (!manifest.uv || typeof manifest.uv !== "object") {
    throw new Error(`${runtimeManifestName} is missing uv metadata.`);
  }
  if (!manifest.backend || typeof manifest.backend !== "object") {
    throw new Error(`${runtimeManifestName} is missing backend metadata.`);
  }
  if (!manifest.python.version || !manifest.python.buildId) {
    throw new Error(`${runtimeManifestName} must record python.version and python.buildId.`);
  }
  if (!manifest.uv.version) {
    throw new Error(`${runtimeManifestName} must record uv.version.`);
  }
  if (manifest.uv.version !== supportedUvVersion) {
    throw new Error(
      `Packaged uv version must be ${supportedUvVersion}, found ${manifest.uv.version}.`,
    );
  }

  const python = resolveManifestPath(runtimeRoot, manifest.python.executable, "Packaged Python");
  const uv = resolveManifestPath(runtimeRoot, manifest.uv.executable, "Packaged uv");
  requireContainedFile(python, runtimeRoot, "Packaged Python executable");
  requireContainedFile(uv, runtimeRoot, "Packaged uv executable");

  assertSha256(python, manifest.python.sha256, "Packaged Python executable");
  assertSha256(uv, manifest.uv.sha256, "Packaged uv executable");

  if (canExecute(python)) {
    const actualPythonVersion = pythonVersion(python);
    if (actualPythonVersion !== manifest.python.version) {
      throw new Error(
        `Packaged Python version mismatch: manifest has ${manifest.python.version}, executable reports ${actualPythonVersion}.`,
      );
    }
  } else {
    assertTargetArchitecture(python, expectedTarget, "Packaged Python executable");
  }

  if (canExecute(uv)) {
    const actualUvVersion = uvVersion(uv);
    if (actualUvVersion !== manifest.uv.version) {
      throw new Error(
        `Packaged uv version mismatch: manifest has ${manifest.uv.version}, executable reports ${actualUvVersion}.`,
      );
    }
  } else {
    assertTargetArchitecture(uv, expectedTarget, "Packaged uv executable");
  }
}

export function verifyUvExcludesCapability(uv) {
  const workDir = mkdtempSync(path.join(tmpdir(), "noofy-uv-excludes-"));
  try {
    const input = path.join(workDir, "requirements.in");
    const excludes = path.join(workDir, "excludes.txt");
    const output = path.join(workDir, "requirements.lock");
    writeFileSync(input, "requests-cache==1.2.1\n");
    writeFileSync(excludes, "requests\n");
    runChecked(
      uv,
      [
        "pip",
        "compile",
        input,
        "--generate-hashes",
        "--excludes",
        excludes,
        "--python-version",
        "3.13",
        "--default-index",
        "https://pypi.org/simple",
        "--no-sources",
        "--no-config",
        "--no-progress",
        "--output-file",
        output,
      ],
      {
        cwd: workDir,
        env: {
          ...process.env,
          UV_CACHE_DIR: path.join(workDir, "uv-cache"),
          UV_NO_PYTHON_DOWNLOADS: "1",
        },
      },
    );
    const lock = readFileSync(output, "utf8");
    if (!/^requests-cache==1\.2\.1\b/m.test(lock) || /^requests==/m.test(lock)) {
      throw new Error(
        "Bundled uv does not provide the required transitive --excludes behavior.",
      );
    }
  } finally {
    rmSync(workDir, { recursive: true, force: true });
  }
}

export function bundledBackendRoot(runtimeRoot = defaultRuntimeRoot) {
  return path.join(runtimeRoot, backendPackagedPath);
}

export function backendArtifactMetadata() {
  verifyBackendSourceFiles();
  return {
    version: backendVersion(),
    sourcePath: "backend",
    packagedPath: backendPackagedPath,
    appPath: backendAppPackagedPath,
    pyprojectPath: backendPyprojectPackagedPath,
    sha256: backendArtifactHash(),
    fileCount: backendArtifactFiles().length,
  };
}

export function verifyBackendManifest(backend) {
  if (!backend || typeof backend !== "object") {
    throw new Error(`${runtimeManifestName} is missing backend metadata.`);
  }
  if (backend.packagedPath !== backendPackagedPath) {
    throw new Error(
      `Backend packaged path mismatch: expected ${backendPackagedPath}, found ${backend.packagedPath ?? "<missing>"}.`,
    );
  }
  if (backend.appPath !== backendAppPackagedPath) {
    throw new Error(
      `Backend app path mismatch: expected ${backendAppPackagedPath}, found ${backend.appPath ?? "<missing>"}.`,
    );
  }
  if (backend.pyprojectPath !== backendPyprojectPackagedPath) {
    throw new Error(
      `Backend pyproject path mismatch: expected ${backendPyprojectPackagedPath}, found ${backend.pyprojectPath ?? "<missing>"}.`,
    );
  }
  verifyBackendSourceFiles();
  const actualHash = backendArtifactHash();
  if (backend.sha256 !== actualHash) {
    throw new Error(
      [
        `Backend artifact hash mismatch: expected ${backend.sha256}, got ${actualHash}.`,
        "The packaged runtime manifest was generated for different backend sources.",
        "Refresh the packaged runtime with `npm run tauri:download-runtime -- --target <target>` or `npm run tauri:prepare-runtime ...`, then rerun verification.",
      ].join("\n"),
    );
  }
}

export function verifyTauriBackendResourceMapping() {
  const configPath = path.join(frontendRoot, "src-tauri", "tauri.conf.json");
  const config = readJson(configPath);
  const resources = config?.bundle?.resources;
  if (!resources || typeof resources !== "object") {
    throw new Error("Tauri bundle.resources must map Noofy runtime resources into noofy-runtime.");
  }
  const expected = {
    "resources/noofy-runtime": "noofy-runtime",
    "../../backend/app": `noofy-runtime/${backendAppPackagedPath}`,
    "../../backend/pyproject.toml": `noofy-runtime/${backendPyprojectPackagedPath}`,
    "../../third_party/comfyui": "noofy-runtime/comfyui",
  };
  for (const [source, destination] of Object.entries(expected)) {
    if (resources[source] !== destination) {
      throw new Error(
        `Tauri runtime resource mapping is missing: ${source} must bundle to ${destination}.`,
      );
    }
  }
  verifyTauriLinuxBundleTargets();
}

export function verifyTauriLinuxBundleTargets() {
  const configPath = path.join(frontendRoot, "src-tauri", "tauri.linux.conf.json");
  const config = readJson(configPath);
  const targets = config?.bundle?.targets;
  if (!Array.isArray(targets)) {
    throw new Error("Tauri Linux bundle targets must explicitly include deb and appimage.");
  }
  for (const target of ["deb", "appimage"]) {
    if (!targets.includes(target)) {
      throw new Error(`Tauri Linux bundle target is missing: ${target}.`);
    }
  }
}

export function verifyBackendSourceFiles(backendRoot = backendSourceRoot) {
  for (const [filePath, label] of [
    [path.join(backendRoot, "app", "__init__.py"), "Backend package marker"],
    [path.join(backendRoot, "app", "__main__.py"), "Backend module entrypoint"],
    [path.join(backendRoot, "app", "main.py"), "Backend FastAPI app module"],
    [path.join(backendRoot, "app", "api", "router.py"), "Backend API router"],
    [path.join(backendRoot, "app", "api", "routes", "__init__.py"), "Backend API routes package"],
    [path.join(backendRoot, "pyproject.toml"), "Backend project metadata"],
  ]) {
    requireFile(filePath, label);
  }
}

export function backendVersion() {
  const pyproject = readFileSync(path.join(backendSourceRoot, "pyproject.toml"), "utf8");
  const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
  if (!match) {
    throw new Error("Could not read backend version from backend/pyproject.toml.");
  }
  return match[1];
}

export function backendArtifactHash() {
  const hash = createHash("sha256");
  for (const file of backendArtifactFiles()) {
    const relativePath = path.relative(backendSourceRoot, file).split(path.sep).join("/");
    hash.update(relativePath);
    hash.update("\0");
    hash.update(sha256File(file));
    hash.update("\0");
  }
  return hash.digest("hex");
}

export function backendArtifactFiles() {
  return [
    path.join(backendSourceRoot, "pyproject.toml"),
    ...walkFiles(path.join(backendSourceRoot, "app")),
  ].sort();
}

function walkFiles(root) {
  const files = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (entry.name === "__pycache__" || entry.name === ".DS_Store" || entry.name.endsWith(".pyc")) {
      continue;
    }
    const filePath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...walkFiles(filePath));
    } else if (entry.isFile()) {
      files.push(filePath);
    }
  }
  return files;
}

export function canExecute(executable) {
  const result = runMaybe(executable, ["--version"]);
  return result.status === 0;
}

export function assertBackendDependencyFiles(runtimeRoot, pythonVersionValue, target = currentRuntimeTarget()) {
  const pythonMinor = pythonVersionValue.split(".").slice(0, 2).join(".");
  const sitePackages =
    target === "windows-x64"
      ? path.join(runtimeRoot, "python", "Lib", "site-packages")
      : path.join(runtimeRoot, "python", "lib", `python${pythonMinor}`, "site-packages");
  for (const moduleName of ["cryptography", "fastapi", "httpx", "pydantic", "uvicorn", "websockets"]) {
    const packagePath = path.join(sitePackages, moduleName);
    if (!existsSync(packagePath)) {
      throw new Error(
        `Packaged Python dependency is missing from bundled site-packages: ${relative(packagePath)}`,
      );
    }
  }
}

function resultTargetFromManifest(manifest) {
  return typeof manifest.target === "string" ? manifest.target : currentRuntimeTarget();
}

function assertTargetArchitecture(executable, target, label) {
  if (target !== "macos-arm64") {
    throw new Error(`${label} could not be executed on this host for target ${target}.`);
  }
  const result = runChecked("file", [realpathSync(executable)]);
  const output = `${result.stdout} ${result.stderr}`;
  if (!output.includes("arm64")) {
    throw new Error(`${label} is not an arm64 Mach-O binary: ${output.trim()}`);
  }
}

function assertSha256(filePath, expected, label) {
  if (typeof expected !== "string" || !/^[a-f0-9]{64}$/i.test(expected)) {
    throw new Error(`${label} sha256 is missing or invalid in ${runtimeManifestName}.`);
  }
  const actual = sha256File(filePath);
  if (actual.toLowerCase() !== expected.toLowerCase()) {
    throw new Error(
      `${label} checksum mismatch: expected ${expected.toLowerCase()}, got ${actual}.`,
    );
  }
}
