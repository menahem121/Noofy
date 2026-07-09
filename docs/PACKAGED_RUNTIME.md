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
Packaging preparation prunes CPython's optional Tk/Tkinter GUI components from
the trusted runtime. Noofy does not use them, and keeping them causes AppImage
dependency deployment to chase unrelated Tcl/Tk shared libraries.
When a managed ComfyUI runtime profile pins a Python ABI, the bootstrap Python
used to create that managed runner must match the profile instead of following
the developer or system interpreter that launched the backend.
In source-checkout development, a missing profile-matching Python is reported as
a developer setup issue. In packaged Noofy, the bundled runtime is the fix; end
users should reinstall or update Noofy if the bundled Python is missing or has
the wrong ABI.

On macOS, the packaged Python executable is Developer ID signed with Hardened
Runtime plus `com.apple.security.cs.disable-library-validation`. Managed ComfyUI
and workflow dependency environments install wheel native extensions later under
app data, so those extensions cannot be pre-signed with Noofy's Team ID inside
the app bundle. Without this entitlement, macOS library validation rejects
imports such as `blake3` before ComfyUI can finish startup.

## Supported Targets

The current verifier accepts these release targets:

- `macos-arm64` for macOS Apple Silicon `.dmg`
- `windows-x64` for Windows `.exe`
- `linux-x64` for Linux `.deb` and `.AppImage`

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
set -euo pipefail
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
set -euo pipefail
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

On Linux, the same `linux-x64` packaged runtime is used for both Linux
installers. Build both release artifacts with:

```bash
set -euo pipefail
cd frontend
npm run tauri:build:linux
```

The Linux build command renames the Tauri outputs to the release filenames
`Noofy_LINUX_amd64.deb` and
`Noofy_LINUX_amd64.AppImage`.

To produce only the portable AppImage after preparing and verifying the Linux
runtime, use:

```bash
set -euo pipefail
cd frontend
npm run tauri:build:appimage
```

`tauri:prepare-runtime` refuses obvious developer runtimes such as
`backend/.venv`, the active `VIRTUAL_ENV`, or `CONDA_PREFIX`.

## In-App Runtime Updates

Packaged Noofy builds may update the packaged Noofy runtime from GitHub without
mutating the bundled fallback runtime. This updater is separate from the managed
ComfyUI updater and must not write to `third_party/comfyui/`, source checkout
files, or app bundle resources.

Runtime updates are available only when all of the following are true:

- Noofy is running from packaged resources.
- `NOOFY_RUNTIME_UPDATE_REPO=owner/repo` is configured for the release build.
- Developer/runtime overrides such as `NOOFY_BACKEND_DIR`,
  `NOOFY_BACKEND_PYTHON`, `NOOFY_BACKEND_SIDECAR`,
  `NOOFY_PACKAGED_RUNTIME_DIR`, `COMFYUI_REPO_DIR`, or
  `COMFYUI_PYTHON_EXECUTABLE` are not active.

Settings does not query GitHub on open. The `Noofy Runtime` panel performs a
GitHub request only after the user presses `Check for Updates`.

Each GitHub release used for runtime updates must publish one target-specific
runtime archive:

```text
noofy-runtime-macos-arm64.zip
noofy-runtime-windows-x64.zip
noofy-runtime-linux-x64.zip
```

The archive must contain a top-level `noofy-runtime/` directory with the same
layout and `runtime-manifest.json` contract used by bundled release resources.
The updater requires the GitHub asset `sha256:` digest, verifies the archive,
rejects unsafe archive members, extracts into transaction staging, validates the
runtime manifest and backend artifact hash, launches a staged backend smoke test,
and only then records the update as pending.

Validated runtimes live under app data:

```text
{data_dir}/runtime-store/noofy-runtime/runtimes/{runtime_id}/noofy-runtime/
{data_dir}/runtime-store/noofy-runtime/pending-runtime.json
{data_dir}/runtime-store/noofy-runtime/active-runtime.json
```

Activation is manual. Pressing `Activate on Next Launch` atomically writes the
active pointer after revalidating the pending runtime. The running backend is not
hot-swapped; the Tauri launcher reads the active pointer on the next app launch.
If the active pointer is missing, invalid, wrong-target, or outside app-managed
runtime storage, the launcher falls back to the bundled `noofy-runtime` resource.
Failed update transactions remain quarantined under
`{data_dir}/runtime-store/transactions/`, and the current working runtime remains
unchanged.

## CI Gate

Release CI should run in this order for each target:

1. Download or build the target-specific Noofy runtime artifact.
2. Verify the downloaded archive checksum before extraction.
3. Run `npm run tauri:prepare-runtime`.
4. Run `npm run tauri:verify-runtime`.
5. Run `npm run tauri:smoke-backend` on a runner that can execute the target
   runtime.
6. Run `npm run tauri:build` for macOS and Windows. On Linux, run
   `npm run tauri:build:linux` so the release produces both `.deb` and
   `.AppImage` artifacts.

Release build scripts normalize artifact filenames after Tauri bundling:

- `Noofy_Windows_x64-setup.exe`
- `Noofy_MACOS_aarch64.dmg`
- `Noofy_LINUX_amd64.deb`
- `Noofy_LINUX_amd64.AppImage`

The Tauri `beforeBuildCommand` already runs `npm run build &&
npm run tauri:verify-runtime`, so missing runtime artifacts fail the installer
build by default.
