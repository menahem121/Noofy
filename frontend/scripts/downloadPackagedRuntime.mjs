import { createHash } from "node:crypto";
import {
  cpSync,
  copyFileSync,
  createWriteStream,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  symlinkSync,
} from "node:fs";
import https from "node:https";
import path from "node:path";
import process from "node:process";
import { pipeline } from "node:stream/promises";

import {
  currentRuntimeTarget,
  defaultRuntimeRoot,
  frontendRoot,
  canExecute,
  relative,
  repoRoot,
  runChecked,
} from "./packagedRuntime.mjs";

const pythonReleaseApi = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest";
const uvReleaseApi = "https://api.github.com/repos/astral-sh/uv/releases/latest";

const targetSpecs = {
  "macos-arm64": {
    pythonAsset: /^cpython-3\.13\.[0-9]+\+[0-9]+-aarch64-apple-darwin-install_only\.tar\.gz$/,
    uvAsset: /^uv-aarch64-apple-darwin\.tar\.gz$/,
    pythonExecutable: ["bin", "python3"],
    uvExecutableName: "uv",
  },
  "windows-x64": {
    pythonAsset: /^cpython-3\.13\.[0-9]+\+[0-9]+-x86_64-pc-windows-msvc-install_only\.tar\.gz$/,
    uvAsset: /^uv-x86_64-pc-windows-msvc\.zip$/,
    pythonExecutable: ["python.exe"],
    uvExecutableName: "uv.exe",
  },
  "linux-x64": {
    pythonAsset: /^cpython-3\.13\.[0-9]+\+[0-9]+-x86_64-unknown-linux-gnu-install_only\.tar\.gz$/,
    uvAsset: /^uv-x86_64-unknown-linux-gnu\.tar\.gz$/,
    pythonExecutable: ["bin", "python3"],
    uvExecutableName: "uv",
  },
};

const args = parseArgs(process.argv.slice(2));
const target = args.target || process.env.NOOFY_PACKAGED_RUNTIME_TARGET || currentRuntimeTarget();
const spec = targetSpecs[target];
const workRoot = path.resolve(args.workDir || path.join(repoRoot, ".noofy-runtime", "packaged-runtime", target));
const output = path.resolve(args.output || defaultRuntimeRoot);

try {
  if (!spec) {
    throw new Error(
      `Unsupported packaged runtime target: ${target}. Supported targets: ${Object.keys(targetSpecs).join(", ")}`,
    );
  }

  const pythonRelease = await getJson(pythonReleaseApi);
  const uvRelease = await getJson(uvReleaseApi);
  const pythonAsset = selectAsset(pythonRelease, spec.pythonAsset, "Python");
  const uvAsset = selectAsset(uvRelease, spec.uvAsset, "uv");

  rmSync(workRoot, { recursive: true, force: true });
  mkdirSync(workRoot, { recursive: true });
  const downloads = path.join(workRoot, "downloads");
  const extract = path.join(workRoot, "extract");
  const source = path.join(workRoot, "source");
  mkdirSync(downloads, { recursive: true });
  mkdirSync(extract, { recursive: true });
  mkdirSync(source, { recursive: true });

  const pythonArchive = path.join(downloads, pythonAsset.name);
  const uvArchive = path.join(downloads, uvAsset.name);
  await downloadAsset(pythonAsset.browser_download_url, pythonArchive);
  await downloadAsset(uvAsset.browser_download_url, uvArchive);
  assertDigest(pythonArchive, pythonAsset.digest, pythonAsset.name);
  assertDigest(uvArchive, uvAsset.digest, uvAsset.name);

  const pythonExtract = path.join(extract, "python");
  const uvExtract = path.join(extract, "uv");
  mkdirSync(pythonExtract, { recursive: true });
  mkdirSync(uvExtract, { recursive: true });
  runChecked("tar", ["-xzf", pythonArchive, "-C", pythonExtract]);
  runChecked("tar", ["-xf", uvArchive, "-C", uvExtract]);

  const extractedPython = firstExistingPath([
    path.join(pythonExtract, "python", "install"),
    path.join(pythonExtract, "python"),
  ]);
  if (!existsSync(extractedPython)) {
    throw new Error(`Unexpected Python artifact layout under ${relative(pythonExtract)}.`);
  }
  copyDirectory(extractedPython, path.join(source, "python"));
  normalizePythonBinSymlinks(path.join(source, "python", "bin"));
  const uvExecutable = findFile(uvExtract, spec.uvExecutableName);
  copyFileSync(uvExecutable, path.join(source, "python", ...uvDestination(spec)));

  const sourcePython = path.join(source, "python", ...spec.pythonExecutable);
  const sourceUv = path.join(source, "python", ...uvDestination(spec));
  const pythonVersionValue = pythonVersionFromAsset(pythonAsset.name);
  const uvVersionValue = uvRelease.tag_name.replace(/^v/, "");
  const canExecuteTargetPython = canExecute(sourcePython);
  if (canExecuteTargetPython) {
    runChecked(sourcePython, ["-m", "ensurepip", "--upgrade"]);
    runChecked(sourcePython, ["-m", "pip", "install", "--upgrade", "pip"]);
    runChecked(sourcePython, ["-m", "pip", "install", path.join(repoRoot, "backend")]);
    runChecked(sourceUv, ["--version"]);
  } else {
    installBackendDependenciesForTarget({
      target,
      pythonVersionValue,
      sitePackages: targetSitePackages(source, target, pythonVersionValue),
    });
  }

  const pythonBuildId = `cpython-${pythonVersionValue}-python-build-standalone-${pythonRelease.tag_name}`;
  runChecked(
    process.execPath,
    [
      path.join("scripts", "preparePackagedRuntime.mjs"),
      "--source",
      source,
      "--output",
      output,
      "--target",
      target,
      "--python-build-id",
      pythonBuildId,
      "--python-version",
      pythonVersionValue,
      "--uv-version",
      uvVersionValue,
      "--skip-execution",
      canExecuteTargetPython ? "0" : "1",
      "--python-source-url",
      pythonAsset.browser_download_url,
      "--python-source-sha256",
      digestHex(pythonAsset.digest),
      "--uv-source-url",
      uvAsset.browser_download_url,
      "--uv-source-sha256",
      digestHex(uvAsset.digest),
    ],
    { cwd: frontendRoot },
  );

  console.log(`Downloaded packaged runtime for ${target}.`);
  console.log(`Python: ${pythonAsset.name}`);
  console.log(`uv: ${uvAsset.name}`);
  console.log(`Output: ${relative(output)}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

function uvDestination(spec) {
  if (spec.uvExecutableName === "uv.exe") {
    return ["Scripts", "uv.exe"];
  }
  return ["bin", "uv"];
}

function targetSitePackages(source, target, pythonVersionValue) {
  const pythonMinor = pythonVersionValue.split(".").slice(0, 2).join(".");
  if (target === "windows-x64") {
    return path.join(source, "python", "Lib", "site-packages");
  }
  return path.join(source, "python", "lib", `python${pythonMinor}`, "site-packages");
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

function installBackendDependenciesForTarget({ target, pythonVersionValue, sitePackages }) {
  mkdirSync(sitePackages, { recursive: true });
  const hostPython = process.env.NOOFY_BUILD_PYTHON || "python3";
  const pythonMinor = pythonVersionValue.split(".").slice(0, 2).join(".");
  const abi = `cp${pythonMinor.replace(".", "")}`;
  const platform = pipPlatformForTarget(target);
  const dependencies = backendRuntimeDependencies();
  runChecked(hostPython, [
    "-m",
    "pip",
    "install",
    "--target",
    sitePackages,
    "--platform",
    platform,
    "--python-version",
    pythonMinor,
    "--implementation",
    "cp",
    "--abi",
    abi,
    "--only-binary=:all:",
    "--upgrade",
    ...dependencies,
  ]);
}

function pipPlatformForTarget(target) {
  if (target === "macos-arm64") {
    return "macosx_11_0_arm64";
  }
  if (target === "windows-x64") {
    return "win_amd64";
  }
  if (target === "linux-x64") {
    return "manylinux_2_28_x86_64";
  }
  throw new Error(`Unsupported pip platform target: ${target}`);
}

function backendRuntimeDependencies() {
  const pyproject = readFileSync(path.join(repoRoot, "backend", "pyproject.toml"), "utf8");
  const dependencies = [];
  let inDependencies = false;
  for (const line of pyproject.split(/\r?\n/)) {
    if (!inDependencies && /^\s*dependencies\s*=\s*\[\s*$/.test(line)) {
      inDependencies = true;
      continue;
    }
    if (inDependencies && /^\s*\]\s*$/.test(line)) {
      break;
    }
    if (inDependencies) {
      const match = line.match(/"([^"]+)"/);
      if (match) {
        dependencies.push(match[1]);
      }
    }
  }
  if (dependencies.length === 0) {
    throw new Error("Could not find backend runtime dependencies in backend/pyproject.toml.");
  }
  return dependencies;
}

function firstExistingPath(candidates) {
  return candidates.find((candidate) => existsSync(candidate)) || candidates[0];
}

function selectAsset(release, pattern, label) {
  const assets = Array.isArray(release.assets) ? release.assets : [];
  const matches = assets.filter((asset) => pattern.test(asset.name));
  if (matches.length !== 1) {
    throw new Error(
      `${label} asset selection expected one match for ${pattern}, found ${matches.length}.`,
    );
  }
  const asset = matches[0];
  if (!asset.digest?.startsWith("sha256:")) {
    throw new Error(`${label} asset ${asset.name} is missing a GitHub SHA-256 digest.`);
  }
  return asset;
}

async function getJson(url) {
  const response = await request(url);
  return JSON.parse(response.toString("utf8"));
}

async function downloadAsset(url, destination) {
  await pipeline(await requestStream(url), createWriteStreamLazy(destination));
}

function request(url, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    https
      .get(url, requestOptions(), (response) => {
        if (isRedirect(response.statusCode) && response.headers.location) {
          response.resume();
          if (redirectCount > 5) {
            reject(new Error(`Too many redirects while fetching ${url}`));
          } else {
            resolve(request(new URL(response.headers.location, url).toString(), redirectCount + 1));
          }
          return;
        }
        if (response.statusCode !== 200) {
          response.resume();
          reject(new Error(`HTTP ${response.statusCode} while fetching ${url}`));
          return;
        }
        const chunks = [];
        response.on("data", (chunk) => chunks.push(chunk));
        response.on("end", () => resolve(Buffer.concat(chunks)));
      })
      .on("error", reject);
  });
}

function requestStream(url, redirectCount = 0) {
  return new Promise((resolve, reject) => {
    https
      .get(url, requestOptions(), (response) => {
        if (isRedirect(response.statusCode) && response.headers.location) {
          response.resume();
          if (redirectCount > 5) {
            reject(new Error(`Too many redirects while downloading ${url}`));
          } else {
            resolve(requestStream(new URL(response.headers.location, url).toString(), redirectCount + 1));
          }
          return;
        }
        if (response.statusCode !== 200) {
          response.resume();
          reject(new Error(`HTTP ${response.statusCode} while downloading ${url}`));
          return;
        }
        resolve(response);
      })
      .on("error", reject);
  });
}

function requestOptions() {
  return {
    headers: {
      "User-Agent": "noofy-runtime-builder",
      Accept: "application/vnd.github+json",
    },
  };
}

function isRedirect(statusCode) {
  return statusCode === 301 || statusCode === 302 || statusCode === 303 || statusCode === 307 || statusCode === 308;
}

function createWriteStreamLazy(destination) {
  mkdirSync(path.dirname(destination), { recursive: true });
  return createWriteStream(destination);
}

function assertDigest(filePath, expectedDigest, label) {
  const expected = digestHex(expectedDigest);
  const actual = sha256File(filePath);
  if (actual !== expected) {
    throw new Error(`${label} checksum mismatch: expected ${expected}, got ${actual}.`);
  }
}

function digestHex(digest) {
  const value = digest.startsWith("sha256:") ? digest.slice("sha256:".length) : digest;
  if (!/^[a-f0-9]{64}$/i.test(value)) {
    throw new Error(`Invalid SHA-256 digest: ${digest}`);
  }
  return value.toLowerCase();
}

function sha256File(filePath) {
  const hash = createHash("sha256");
  hash.update(readFileSync(filePath));
  return hash.digest("hex");
}

function copyDirectory(source, destination) {
  rmSync(destination, { recursive: true, force: true });
  mkdirSync(path.dirname(destination), { recursive: true });
  cpSync(source, destination, { recursive: true, force: true, verbatimSymlinks: false });
}

function findFile(root, filename) {
  for (const entry of readdirSync(root)) {
    const candidate = path.join(root, entry);
    const stat = statSync(candidate);
    if (stat.isFile() && entry === filename) {
      return candidate;
    }
    if (stat.isDirectory()) {
      const nested = findFile(candidate, filename);
      if (nested) {
        return nested;
      }
    }
  }
  return null;
}

function pythonVersionFromAsset(name) {
  const match = name.match(/^cpython-([0-9]+\.[0-9]+\.[0-9]+)\+/);
  if (!match) {
    throw new Error(`Could not parse Python version from asset name: ${name}`);
  }
  return match[1];
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
