# Packaged Python and uv Runtime

Packaged Noofy builds must ship a Noofy-owned Python runtime and a Noofy-owned
`uv` executable. Release installers must not depend on system Python,
Homebrew, Conda, `PATH`, `backend/.venv`, global `uv`, Node, or npm at runtime.

## Runtime Shape

Each platform release prepares this resource tree before `tauri build`:

```text
frontend/src-tauri/resources/noofy-runtime/
  runtime-manifest.json
  python/
    bin/python3          # macOS/Linux
    bin/uv               # macOS/Linux, preferred
    python.exe           # Windows, accepted
    Scripts/python.exe   # Windows, accepted
    Scripts/uv.exe       # Windows, preferred
```

Tauri also bundles app-owned backend files and the ComfyUI source snapshot into
the same installed resource root:

```text
noofy-runtime/backend/app/
noofy-runtime/backend/pyproject.toml
noofy-runtime/comfyui/
```

The packaged Tauri shell launches the backend from `noofy-runtime/backend` in
release mode. The Python launch command is `python -m app --port 0`, with the
current directory set to the packaged backend directory. Packaged mode removes
developer backend overrides such as `NOOFY_BACKEND_DIR`, `NOOFY_BACKEND_PYTHON`,
`PYTHONPATH`, `VIRTUAL_ENV`, and `CONDA_PREFIX`, so it cannot silently run the
repo checkout or `backend/.venv`.

The bundled Python runtime is for the trusted backend and bootstrap tooling.
Managed ComfyUI, PyTorch, custom nodes, and community workflow dependencies are
installed later into app-data runtime directories and isolated dependency
environments. They must not be installed into this trusted bundled Python.

## Supported Targets

The current verifier accepts these release targets:

- `macos-arm64` for macOS Apple Silicon `.dmg`
- `windows-x64` for Windows `.exe`
- `linux-x64` for Linux `.deb`

macOS Intel is intentionally not a release target.

## Manifest Contract

`runtime-manifest.json` is generated, not hand-written. It records:

- runtime schema and layout version
- target platform
- exact Python version and Noofy Python build id
- Python executable path and SHA-256 checksum
- exact `uv` version
- `uv` executable path and SHA-256 checksum
- backend version, installed resource paths, file count, and deterministic
  SHA-256 artifact hash over `backend/app` plus `backend/pyproject.toml`
- optional upstream archive URLs and archive checksums
- the dependency boundary between trusted backend, managed ComfyUI, and
  isolated community workflow environments

`npm run tauri:verify-runtime` fails unless the manifest exists, matches the
current target, points only inside the packaged runtime root, matches executable
checksums, the backend source artifact hash matches the manifest, Tauri maps
the backend into `noofy-runtime/backend`, and the bundled Python can import the
backend plus FastAPI runtime dependencies.

## Release Preparation

The release pipeline should prepare one explicit runtime artifact per target.
The artifact source can be produced by a dedicated runtime-builder job, but the
Tauri package job must consume it explicitly rather than discovering tools from
the machine.

For local release-smoke preparation or CI jobs that are allowed to fetch
upstream runtime artifacts directly, use:

```bash
cd frontend
npm run tauri:download-runtime -- --target macos-arm64
npm run tauri:verify-runtime
npm run tauri:smoke-backend
```

`tauri:download-runtime` selects only supported target assets from
`astral-sh/python-build-standalone` and `astral-sh/uv`, verifies the downloaded
archive SHA-256 digests published by GitHub, installs trusted backend
dependencies into the bundled Python, and then delegates to
`tauri:prepare-runtime` to write the final manifest.

```bash
cd frontend
npm run tauri:prepare-runtime -- \
  --source "$NOOFY_PACKAGED_RUNTIME_SOURCE_DIR" \
  --python-build-id "cpython-3.13-noofy-v1" \
  --python-source-url "$PYTHON_ARTIFACT_URL" \
  --python-source-sha256 "$PYTHON_ARTIFACT_SHA256" \
  --uv-source-url "$UV_ARTIFACT_URL" \
  --uv-source-sha256 "$UV_ARTIFACT_SHA256"

npm run tauri:verify-runtime
npm run tauri:build
```

`tauri:prepare-runtime` refuses obvious developer runtimes such as
`backend/.venv`, the active `VIRTUAL_ENV`, or `CONDA_PREFIX`.

## CI Gate

Release CI should run in this order for each target:

1. Download or build the target-specific Noofy runtime artifact.
2. Verify the downloaded archive checksum before extraction.
3. Run `npm run tauri:prepare-runtime`.
4. Run `npm run tauri:verify-runtime`.
5. Run `npm run tauri:smoke-backend` on a runner that can execute the target
   runtime.
6. Run `npm run tauri:build`.

The Tauri `beforeBuildCommand` already runs `npm run build &&
npm run tauri:verify-runtime`, so missing runtime artifacts fail the installer
build by default.
