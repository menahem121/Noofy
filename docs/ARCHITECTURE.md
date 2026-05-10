# Architecture

The app is a local AI workflow tool for Linux, Windows, and macOS Apple Silicon. It should hide ComfyUI complexity from end users while keeping ComfyUI's workflow power available behind the scenes. macOS Intel is unsupported for managed ComfyUI runtime preparation.

## Stack

- Desktop shell: Tauri / Rust
- Frontend: TypeScript + React
- Backend: Python + FastAPI
- First AI engine: ComfyUI as a local sidecar service
- Communication: local HTTP and WebSocket APIs

## Process Flow

```text
React frontend
  -> Python FastAPI app backend
  -> EngineAdapter
  -> ComfyUIEngineAdapter
  -> ComfyUI HTTP/WebSocket API
```

The Tauri shell starts and manages the local backend process. The backend starts or connects to the local ComfyUI service.

The FastAPI route layer is backed by composed application services. `EngineService` owns workflow execution orchestration, while user state and dashboard asset persistence stay in smaller application services. Default runtime, workflow, adapter, trust, diagnostics, and dashboard collaborators are wired by the backend composition/factory code rather than by route handlers.

For product v1, ComfyUI is a managed sidecar. The app must start it as a hidden local subprocess using an app-managed isolated Python environment. Users should not manually open ComfyUI or install ComfyUI dependencies into their system Python.

External ComfyUI URLs such as `http://127.0.0.1:8188` are development mode only.

Generated media URLs returned to the frontend are Noofy backend API URLs. ComfyUI upload and `/view` endpoints are adapter implementation details and must not become frontend contracts.

Community workflows from the internet are a first-class product direction. Noofy should automatically prepare custom nodes and normal Python dependencies when they can be resolved into isolated workflow capsules. These installs must never mutate the trusted core runtime or another installed workflow. Unverified community workflows are not guaranteed to be safe, trustworthy, or compatible.

## Local API Security

The backend must bind only to localhost for product builds. The frontend must continue to call only the app backend API, never ComfyUI directly.

When the desktop shell is added, Tauri owns the local API session token:

- Generate a cryptographically random token for each app launch.
- Start the backend with `NOOFY_API_TOKEN` set to that token.
- Inject the same token into the frontend runtime config before React starts:

```ts
window.__NOOFY_RUNTIME_CONFIG__ = {
  apiBaseUrl: "http://127.0.0.1:<port>/api",
  apiToken: "<per-launch-token>"
}
```

The token is not user authentication. It is local API hardening so unrelated webpages cannot freely control the local workflow backend. It must not be persisted to disk or written to logs.

For browser development, token enforcement is optional. The backend only requires a token when `NOOFY_API_TOKEN` is set.

## Desktop Runtime Config

Tauri must not create or navigate the main webview until the backend API base URL and launch token are known.

Startup order:

1. Generate the per-launch API token.
2. Start the backend with `NOOFY_API_TOKEN` and a free localhost port.
3. Read the backend API base URL from the backend startup handoff.
4. Inject `window.__NOOFY_RUNTIME_CONFIG__` as an initialization script before the frontend entry module runs.
5. Load the Vite dev server in development or the built frontend assets in production.

The frontend reads this runtime config in `frontend/src/lib/api/noofyApi.ts`. If the initialization script is unavailable for a dev-server webview, the frontend asks Tauri for the same config through the `noofy_runtime_config` command before rendering React. If both desktop config paths are absent, browser development falls back to `/api` and the Vite proxy.

## Key Decisions

- Wrap ComfyUI first. Do not fork ComfyUI now.
- Do not rebuild a minimal AI engine for the first version.
- Do not let the frontend call ComfyUI directly.
- Treat ComfyUI graphs as opaque execution data where possible.
- Keep the app API stable enough that future adapters can replace or supplement ComfyUI.
- Own the ComfyUI lifecycle in the app: start, stop, health checks, port selection, logs, crash recovery, and clear errors.
- Own diagnostics at the backend composition root: runtime/backend subsystems emit structured events through an injected diagnostics sink, while API-facing services read the shared diagnostics store for logs, job logs, health latest error, and troubleshooting payloads.
- Use the accepted [runtime isolation architecture](RUNTIME_ISOLATION_ARCHITECTURE.md) for community workflow imports, custom node dependencies, workflow capsules, and runner isolation. The runtime isolation foundation (paths, schemas, runner supervision, verified and registry-resolved installs, trust signing, source policy, smoke gating, GC) is implemented in [backend/app/runtime/](../backend/app/runtime/). Packaged builds prepare and verify the trusted Python and bundled `uv` artifact through [PACKAGED_RUNTIME.md](PACKAGED_RUNTIME.md).
- Use the [dashboard architecture](DASHBOARD_ARCHITECTURE.md) for workflow import, dashboard authoring, canvas rendering, user values/layout state, and dashboard assets.
- Use the [Memory Governor](MEMORY_GOVERNOR.md) for v1 RAM/VRAM decisions, runner co-residence, memory-risk recovery, and user-facing memory states.

## App Data Directories

The backend owns a canonical set of per-user directories so the app never relies on repo-local runtime/model/output folders.

### Platform defaults

| Platform | Default base |
|----------|-------------|
| macOS | `~/Library/Application Support/Noofy` |
| Windows | `%APPDATA%\Noofy` |
| Linux | `~/.local/share/noofy` |

### Directory layout

| Name | Default path | Purpose |
|------|-------------|---------|
| `data_dir` | *base* | Root app-data directory |
| `runtime_dir` | `data_dir/runtime` | venv, process state |
| `models_dir` | `data_dir/models` | Legacy/app-owned model directory override surface |
| `model_store_dir` | `data_dir/model-store` | Shared model blobs, refs, and materialized model views |
| `user_workflows_dir` | `data_dir/workflows` | Backward-compatible user workflow path override surface |
| `workflow_store_dir` | `data_dir/workflow-store` | Internal imported workflow package store |
| `workflow_packages_store_dir` | `data_dir/workflow-store/packages` | Normalized package copies by publisher/package/version |
| `outputs_dir` | `data_dir/outputs` | Generated output files |
| `logs_dir` | `data_dir/logs` | Diagnostic logs |
| `cache_dir` | `data_dir/cache` | Transient cache |
| `temp_dir` | `data_dir/temp` | Temporary files |
| `user_state_dir` | `data_dir/user-state` | User-specific workflow values/layout and ComfyUI user state |
| `dashboard_assets_dir` | `data_dir/dashboard-assets` | Durable image assets uploaded through dashboard widgets |
| `trust_dir` | `data_dir/trust` | Trust keyring and future trust metadata |
| `trust_keys_file` | `data_dir/trust/trusted-keys.json` | Trust roots for imported package verification |
| `bundled_workflows_dir` | `backend/app/workflows/packages` | Read-only starter workflows |
| `comfyui_repo_dir` | `third_party/comfyui` (project root) | App-owned vendored ComfyUI source snapshot used by local managed mode and packaging input |
| `input_dir` | `data_dir/input` | ComfyUI input/staging files |
| `comfyui_custom_nodes_dir` | `data_dir/custom_nodes` | App-owned core custom-node location for the managed sidecar |
| `comfyui_user_dir` | `data_dir/user-state/comfyui` | ComfyUI user/database state |
| `comfyui_database_file` | `data_dir/user-state/comfyui/comfyui.db` | ComfyUI database for the managed sidecar |
| `python_cache_dir` | `data_dir/cache/python` | Python bytecode cache for managed sidecar processes |

`third_party/comfyui/` is the app-owned ComfyUI source snapshot. It is not the user's external ComfyUI installation, and Noofy should not maintain a second developer-only/reference checkout. Product runtime profiles should eventually launch from clean reproducible ComfyUI source artifacts materialized under the app runtime store, for example `runtime-store/core-engines/comfyui-core-<version>-<source-hash>/`, generated from the vendored source through the packaging pipeline. Local runtime folders such as `models/`, `custom_nodes/`, `input/`, `output/`, `temp/`, and `user/` must not live in or affect the source snapshot.

### Environment variable overrides

| Variable | Scope |
|----------|-------|
| `NOOFY_DATA_DIR` | Overrides the base directory; all sub-dirs follow |
| `NOOFY_RUNTIME_DIR` | Overrides only `runtime_dir` (backward-compatible) |
| `NOOFY_MODELS_DIR` | Overrides only `models_dir` |
| `NOOFY_WORKFLOWS_DIR` | Overrides only `user_workflows_dir` |
| `NOOFY_INPUT_DIR` | Overrides only `input_dir` |
| `NOOFY_OUTPUTS_DIR` | Overrides only `outputs_dir` |
| `NOOFY_LOGS_DIR` | Overrides only `logs_dir` |
| `NOOFY_CACHE_DIR` | Overrides only `cache_dir` |
| `NOOFY_TEMP_DIR` | Overrides only `temp_dir` |
| `NOOFY_TRUST_KEYS_FILE` | Overrides only the trust keyring file |
| `COMFYUI_REPO_DIR` | Overrides ComfyUI checkout location |

### Bundled vs user workflows

Bundled starter workflows are read-only and ship inside the repo at `backend/app/workflows/packages`. User-imported or user-created workflow packages live in `user_workflows_dir`.

Development tooling may allow local overrides during iteration. Product workflow identity must include namespace/publisher and trust metadata. User-imported packages must not silently replace Noofy Verified built-ins by matching `metadata.id`; conflicts must install under a distinct namespace or require an explicit replacement action.

### Diagnostics

`GET /api/paths` returns all resolved directory paths with `exists` and `writable` status. It is protected by the same optional `NOOFY_API_TOKEN` as all other `/api/*` routes.

Backend diagnostics are structured in-memory events exposed through app-owned API routes such as `/api/logs`, `/api/jobs/{job_id}/logs`, `/api/health`, and `/api/diagnostics`. Runtime subsystems should not instantiate private diagnostic stores; they receive the shared sink from the backend service factory so install failures, smoke tests, ComfyUI crashes, Memory Governor decisions, runner lifecycle events, model actions, updates, and workflow imports remain visible to the UI.

### Tauri integration (future)

When Tauri owns the process lifecycle, it will pass `NOOFY_DATA_DIR` only if it needs a custom location. Otherwise the backend resolves platform defaults at startup.

## Future Engine Direction

The first adapter is `ComfyUIEngineAdapter`.

Future adapters may include:

- `MacNativeEngineAdapter` for Apple-native acceleration through Core ML, Metal, or MLX.
- `WindowsNativeEngineAdapter` if a native Windows path becomes useful.
- `LinuxCudaEngineAdapter` or another Linux-native adapter if direct CUDA integration becomes useful beyond the managed ComfyUI runtime.

Distribution must account for ComfyUI's GPLv3 license when bundling or modifying it.
