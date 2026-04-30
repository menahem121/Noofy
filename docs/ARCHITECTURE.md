# Architecture

The app is a local desktop AI workflow tool for macOS and Windows. It should hide ComfyUI complexity from end users while keeping ComfyUI's workflow power available behind the scenes.

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

For product v1, ComfyUI is a managed sidecar. The app must start it as a hidden local subprocess using an app-managed isolated Python environment. Users should not manually open ComfyUI or install ComfyUI dependencies into their system Python.

External ComfyUI URLs such as `http://127.0.0.1:8188` are development mode only.

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
| `models_dir` | `data_dir/models` | Downloaded AI models |
| `user_workflows_dir` | `data_dir/workflows` | User-imported workflow packages |
| `outputs_dir` | `data_dir/outputs` | Generated output files |
| `logs_dir` | `data_dir/logs` | Diagnostic logs |
| `cache_dir` | `data_dir/cache` | Transient cache |
| `temp_dir` | `data_dir/temp` | Temporary files |
| `bundled_workflows_dir` | `backend/app/workflows/packages` | Read-only starter workflows |
| `comfyui_repo_dir` | `ComfyUI-official-repo` (project root) | ComfyUI source / checkout |

### Environment variable overrides

| Variable | Scope |
|----------|-------|
| `NOOFY_DATA_DIR` | Overrides the base directory; all sub-dirs follow |
| `NOOFY_RUNTIME_DIR` | Overrides only `runtime_dir` (backward-compatible) |
| `NOOFY_MODELS_DIR` | Overrides only `models_dir` |
| `NOOFY_WORKFLOWS_DIR` | Overrides only `user_workflows_dir` |
| `NOOFY_OUTPUTS_DIR` | Overrides only `outputs_dir` |
| `NOOFY_LOGS_DIR` | Overrides only `logs_dir` |
| `NOOFY_CACHE_DIR` | Overrides only `cache_dir` |
| `NOOFY_TEMP_DIR` | Overrides only `temp_dir` |
| `COMFYUI_REPO_DIR` | Overrides ComfyUI checkout location |

### Bundled vs user workflows

Bundled starter workflows are read-only and ship inside the repo at `backend/app/workflows/packages`. User-imported or user-created workflow packages live in `user_workflows_dir`. If both contain a package with the same `metadata.id`, the user copy wins so imports can intentionally replace a starter.

### Diagnostics

`GET /api/paths` returns all resolved directory paths with `exists` and `writable` status. It is protected by the same optional `NOOFY_API_TOKEN` as all other `/api/*` routes.

### Tauri integration (future)

When Tauri owns the process lifecycle, it will pass `NOOFY_DATA_DIR` only if it needs a custom location. Otherwise the backend resolves platform defaults at startup.

## Future Engine Direction

The first adapter is `ComfyUIEngineAdapter`.

Future adapters may include:

- `MacNativeEngineAdapter` for Apple-native acceleration through Core ML, Metal, or MLX.
- `WindowsNativeEngineAdapter` if a native Windows path becomes useful.

Distribution must account for ComfyUI's GPLv3 license when bundling or modifying it.
