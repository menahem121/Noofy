import {
  cpSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";
import process from "node:process";

import {
  assertBackendImports,
  assertBackendDependencyFiles,
  backendArtifactMetadata,
  canExecute,
  currentRuntimeTarget,
  defaultRuntimeRoot,
  firstExisting,
  isPathInside,
  packagedPythonCandidates,
  packagedUvCandidates,
  pythonVersion,
  relative,
  relativeManifestPath,
  repoRoot,
  runtimeLayoutVersion,
  runtimeManifestName,
  sha256File,
  uvVersion,
  verifyPackagedRuntime,
} from "./packagedRuntime.mjs";

const args = parseArgs(process.argv.slice(2));
const source = args.source || process.env.NOOFY_PACKAGED_RUNTIME_SOURCE_DIR;
const output = path.resolve(args.output || defaultRuntimeRoot);
const target = args.target || process.env.NOOFY_PACKAGED_RUNTIME_TARGET || currentRuntimeTarget();
const pythonBuildId = args.pythonBuildId || process.env.NOOFY_PACKAGED_PYTHON_BUILD_ID;
const skipExecution = args.skipExecution === "1" || args.skipExecution === "true";

try {
  if (!source) {
    throw new Error(
      [
        "Missing packaged runtime source directory.",
        "Pass --source <dir> or set NOOFY_PACKAGED_RUNTIME_SOURCE_DIR.",
        "The source must be an explicit Noofy-owned release artifact, not backend/.venv, Conda, Homebrew, or PATH Python.",
      ].join("\n"),
    );
  }
  if (!pythonBuildId) {
    throw new Error(
      "Missing Python build id. Pass --python-build-id <id> or set NOOFY_PACKAGED_PYTHON_BUILD_ID.",
    );
  }

  const sourceRoot = path.resolve(source);
  if (sourceRoot === output) {
    throw new Error("Packaged runtime source and output directories must be different.");
  }
  rejectDeveloperRuntimeSource(sourceRoot);
  prepareRuntime({ sourceRoot, output, target, pythonBuildId, args });
  console.log(`Packaged runtime prepared: ${relative(output)}/${runtimeManifestName}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

function prepareRuntime({ sourceRoot, output, target, pythonBuildId, args }) {
  const sourcePythonDir = path.join(sourceRoot, "python");
  const outputPythonDir = path.join(output, "python");
  const placeholderReadme = path.join(outputPythonDir, "README.md");
  const existingReadme = existsSync(placeholderReadme) ? readFileSync(placeholderReadme, "utf8") : null;

  rmSync(outputPythonDir, { recursive: true, force: true });
  mkdirSync(output, { recursive: true });
  cpSync(sourcePythonDir, outputPythonDir, {
    recursive: true,
    force: true,
    verbatimSymlinks: false,
  });
  normalizePythonBinSymlinks(path.join(outputPythonDir, "bin"));
  pruneUnusedPackagedPythonModules(outputPythonDir);
  if (existingReadme !== null) {
    writeFileSync(placeholderReadme, existingReadme);
  }

  if (path.join(sourceRoot, "tools") !== path.join(output, "tools")) {
    rmSync(path.join(output, "tools"), { recursive: true, force: true });
    try {
      cpSync(path.join(sourceRoot, "tools"), path.join(output, "tools"), {
        recursive: true,
        force: true,
        verbatimSymlinks: false,
      });
    } catch (error) {
      if (error.code !== "ENOENT") {
        throw error;
      }
    }
  }

  const python = firstExisting(packagedPythonCandidates(output, target));
  if (!python) {
    throw new Error(
      `Prepared runtime is missing Python. Expected one of: ${packagedPythonCandidates(output, target)
        .map((candidate) => relative(candidate))
        .join(", ")}`,
    );
  }
  const uv = firstExisting(packagedUvCandidates(output, target));
  if (!uv) {
    throw new Error(
      `Prepared runtime is missing uv. Expected one of: ${packagedUvCandidates(output, target)
        .map((candidate) => relative(candidate))
        .join(", ")}`,
    );
  }

  if (skipExecution || !canExecute(python)) {
    if (!args.pythonVersion || !args.uvVersion) {
      throw new Error(
        "--python-version and --uv-version are required when preparing a runtime that cannot execute on this host.",
      );
    }
    assertBackendDependencyFiles(output, args.pythonVersion, target);
  } else {
    assertBackendImports(python);
  }
  const resolvedPythonVersion = args.pythonVersion || pythonVersion(python);
  const resolvedUvVersion = args.uvVersion || uvVersion(uv);

  const manifest = {
    schemaVersion: 1,
    layoutVersion: runtimeLayoutVersion,
    runtimeId: `noofy-runtime-${target}-python-${resolvedPythonVersion}-uv-${resolvedUvVersion}`,
    target,
    createdAt: new Date().toISOString(),
    python: {
      implementation: "CPython",
      version: resolvedPythonVersion,
      buildId: pythonBuildId,
      executable: relativeManifestPath(python, output),
      sha256: sha256File(python),
      source: sourceMetadata(args, "python"),
    },
    uv: {
      version: resolvedUvVersion,
      executable: relativeManifestPath(uv, output),
      sha256: sha256File(uv),
      source: sourceMetadata(args, "uv"),
    },
    backend: backendArtifactMetadata(),
    dependencyBoundaries: {
      trustedBackend:
        "Installed into the bundled Python runtime and limited to backend/pyproject.toml runtime dependencies.",
      managedComfyUI:
        "Prepared later in app data by the backend EngineAdapter using isolated managed environments.",
      communityWorkflows:
        "Installed only into isolated dependency environments with the bundled uv executable.",
    },
  };

  writeFileSync(path.join(output, runtimeManifestName), `${JSON.stringify(manifest, null, 2)}\n`);
  verifyPackagedRuntime({ runtimeRoot: output, target, requireBuiltFrontend: false });
}

function sourceMetadata(args, prefix) {
  const url = args[`${prefix}SourceUrl`] || process.env[`NOOFY_PACKAGED_${prefix.toUpperCase()}_SOURCE_URL`];
  const archiveSha256 =
    args[`${prefix}SourceSha256`] ||
    process.env[`NOOFY_PACKAGED_${prefix.toUpperCase()}_SOURCE_SHA256`];
  const metadata = {};
  if (url) {
    metadata.url = url;
  }
  if (archiveSha256) {
    if (!/^[a-f0-9]{64}$/i.test(archiveSha256)) {
      throw new Error(`${prefix} source SHA-256 must be 64 hexadecimal characters.`);
    }
    metadata.archiveSha256 = archiveSha256;
  }
  return metadata;
}

function normalizePythonBinSymlinks(binDir) {
  if (!existsSync(binDir)) {
    return;
  }
  relink(path.join(binDir, "python"), "python3.13");
  relink(path.join(binDir, "python3"), "python3.13");
  relink(path.join(binDir, "python3-config"), "python3.13-config");
}

function relink(linkPath, target) {
  if (!existsSync(path.join(path.dirname(linkPath), target))) {
    return;
  }
  rmSync(linkPath, { force: true });
  symlinkSync(target, linkPath);
}

function pruneUnusedPackagedPythonModules(pythonRoot) {
  removeMatchingChildren(path.join(pythonRoot, "lib"), (name) =>
    /^tcl\d/.test(name) ||
    /^tk\d/.test(name) ||
    /^itcl\d/.test(name) ||
    /^thread\d/.test(name) ||
    /^libtcl.*\.(so|dylib|dll)$/i.test(name) ||
    /^libtk.*\.(so|dylib|dll)$/i.test(name),
  );
  removeMatchingChildren(path.join(pythonRoot, "DLLs"), (name) =>
    name === "_tkinter.pyd" || /^tcl.*\.dll$/i.test(name) || /^tk.*\.dll$/i.test(name),
  );
  for (const stdlibRoot of pythonStdlibRoots(pythonRoot)) {
    for (const name of ["idlelib", "tkinter", "turtledemo"]) {
      rmSync(path.join(stdlibRoot, name), { recursive: true, force: true });
    }
    removeMatchingChildren(path.join(stdlibRoot, "lib-dynload"), (name) =>
      name === "_tkinter.pyd" || name.startsWith("_tkinter."),
    );
  }
}

function pythonStdlibRoots(pythonRoot) {
  const roots = [];
  const windowsStdlib = path.join(pythonRoot, "Lib");
  if (existsSync(windowsStdlib)) {
    roots.push(windowsStdlib);
  }
  const unixLib = path.join(pythonRoot, "lib");
  if (existsSync(unixLib)) {
    for (const entry of readdirSync(unixLib, { withFileTypes: true })) {
      if (entry.isDirectory() && /^python\d+\.\d+$/.test(entry.name)) {
        roots.push(path.join(unixLib, entry.name));
      }
    }
  }
  return roots;
}

function removeMatchingChildren(dir, predicate) {
  if (!existsSync(dir)) {
    return;
  }
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (predicate(entry.name)) {
      rmSync(path.join(dir, entry.name), { recursive: true, force: true });
    }
  }
}

function rejectDeveloperRuntimeSource(sourceRoot) {
  const sourceReal = existsSync(sourceRoot) ? realpathSync(sourceRoot) : path.resolve(sourceRoot);
  const blocked = [
    path.join(repoRoot, "backend", ".venv"),
    process.env.VIRTUAL_ENV,
    process.env.CONDA_PREFIX,
  ]
    .filter(Boolean)
    .map((candidate) => (existsSync(candidate) ? realpathSync(candidate) : path.resolve(candidate)));

  for (const candidate of blocked) {
    if (isPathInside(sourceReal, candidate)) {
      throw new Error(
        `Refusing to package developer runtime source ${sourceReal}. Use a Noofy-owned release runtime artifact instead.`,
      );
    }
  }
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
