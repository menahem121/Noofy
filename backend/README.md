# Backend

This is the app-owned Python backend.

It exposes the desktop app API, owns the `EngineAdapter` contract, validates workflow packages, and translates workflow runs into the active engine implementation.

The first engine implementation is `ComfyUIEngineAdapter`, which talks to a local ComfyUI service through HTTP and WebSocket APIs.

During development, that service may be either Noofy's managed sidecar or an externally launched ComfyUI instance such as `http://127.0.0.1:8188`.

For product v1, external ComfyUI is not a requirement. The desktop app must run ComfyUI as an app-managed sidecar in an isolated Python environment.

## Development Entry Point

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload
```

For the desktop shell handoff, the backend can also be started as a module. Port `0` selects a free localhost port and prints the API base URL on stdout for the shell to inject into `window.__NOOFY_RUNTIME_CONFIG__`:

```bash
python -m app --port 0
```

The backend is intentionally separate from the vendored ComfyUI source at `third_party/comfyui/`. ComfyUI is treated as the first sidecar engine, not as the public API of this app.

## ComfyUI Runtime Modes

External mode remains available for development when you already have a ComfyUI instance running:

```bash
COMFYUI_RUNTIME_MODE=external \
COMFYUI_BASE_URL=http://127.0.0.1:8188 \
python -m uvicorn app.main:app --reload
```

Managed mode uses Noofy's app-owned ComfyUI source snapshot at `third_party/comfyui/` by default:

```bash
COMFYUI_RUNTIME_MODE=managed python -m uvicorn app.main:app --reload
```

Before first managed startup on a machine, prepare the app-owned ComfyUI Python environment through `POST /api/engine/comfyui/bootstrap` or the equivalent UI action. Managed mode runs normal ComfyUI hidden/no-browser and writes models, inputs, outputs, temp files, ComfyUI user state, logs, and caches into Noofy app-data paths, not into `third_party/comfyui/`.

## Useful Endpoints

- `GET /api/health`: backend status, ComfyUI reachability, workflow package count, and missing model summary.
- `GET /api/runtime`: lightweight runtime status for UI polling without workflow validation.
- `GET /api/logs`: list recent backend, engine, ComfyUI, and workflow diagnostics.
- `POST /api/engine/comfyui/bootstrap`: create the app-owned ComfyUI virtual environment and install `requirements.txt`.
- `GET /api/engine/comfyui/status`: lightweight ComfyUI runtime status for UI polling.
- `POST /api/engine/comfyui/start`: request startup of the local ComfyUI sidecar.
- `POST /api/engine/comfyui/stop`: stop the ComfyUI process if this backend started it.
- `GET /api/workflows`: list available workflow packages.
- `POST /api/workflows/{workflow_id}/validate`: validate package bindings and required models.
- `POST /api/workflows/{workflow_id}/run`: run a workflow through the active `EngineAdapter`.
- `GET /api/jobs/{job_id}/progress`: get the latest normalized job progress.
- `GET /api/jobs/{job_id}/events`: stream frontend-ready progress and result events as `text/event-stream`.
- `GET /api/jobs/{job_id}/logs`: list recent diagnostics for a single job.

Log endpoints accept optional `level` and `limit` query parameters.

## Environment Overrides

- `COMFYUI_RUNTIME_MODE`: `external` for a manually launched development ComfyUI, or `managed` for backend-owned sidecar startup. Defaults to `external`.
- `COMFYUI_BASE_URL`: external-mode URL, default `http://127.0.0.1:8188`
- `COMFYUI_WS_URL`: optional external-mode WebSocket URL. Defaults from the active base URL when unset.
- `NOOFY_RUNTIME_DIR`: optional app-owned runtime directory override. By default, runtime data lives under the platform app-data directory.
- `NOOFY_INPUT_DIR`: optional ComfyUI input/staging directory override. Defaults to `<data_dir>/input`.
- `COMFYUI_PYTHON_EXECUTABLE`: optional runtime Python override. When unset, managed mode uses the app-owned virtual environment under the resolved `runtime_dir`.
- `COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE`: Python used to create the managed virtual environment. Defaults to `python3`.
- `COMFYUI_TORCH_CUDA_INDEX_URL`: optional override for the PyTorch CUDA wheel index. When unset, the backend chooses from detected NVIDIA CUDA capability.
- `COMFYUI_TORCH_CPU_INDEX_URL`: PyTorch CPU wheel index for CPU-only Linux/Windows installs. Defaults to `https://download.pytorch.org/whl/cpu`.
- `COMFYUI_MANAGED_HOST`: managed sidecar bind host, default `127.0.0.1`
- `COMFYUI_MANAGED_PORT`: optional managed sidecar port. When unset, the backend selects a free localhost port.
- `COMFYUI_STARTUP_TIMEOUT_SECONDS`: managed startup health polling timeout, default `60`
- `COMFYUI_HEALTH_POLL_INTERVAL_SECONDS`: managed startup health polling interval, default `0.5`
- `NOOFY_API_TOKEN`: optional local API bearer token. When set, every `/api/*` request must include `Authorization: Bearer <token>`, except job event streams may pass `?token=<token>` for browser `EventSource` support.
- `NOOFY_CORS_ORIGINS`: optional comma-separated list of allowed frontend origins. Defaults include Vite dev/preview and Tauri app origins.
- `NOOFY_BACKEND_HOST`: host used by `python -m app`, default `127.0.0.1`.
- `NOOFY_BACKEND_PORT`: port used by `python -m app`, default `0` for a free port.
- `NOOFY_BACKEND_LOG_LEVEL`: uvicorn log level used by `python -m app`, default `info`.

External-mode overrides are development conveniences. Product builds should use `COMFYUI_RUNTIME_MODE=managed` with app-managed runtime paths and ports. Managed bootstrap detects OS, architecture, and available GPU backend before installing PyTorch. macOS Intel gets standard CPU-capable macOS wheels, Apple Silicon gets standard macOS wheels with MPS available when supported, Linux/Windows without NVIDIA use CPU wheels, and NVIDIA machines use the detected CUDA driver capability to select a CUDA wheel index. Managed startup checks the ComfyUI source, `main.py`, `requirements.txt`, runtime directory writability, runtime Python availability, and initial imports for `torch` and `aiohttp` before starting the sidecar.

`COMFYUI_REPO_DIR` can override the app-owned source path for custom development or future packaged runtime artifacts. The default is `third_party/comfyui/`; it is not the user's external ComfyUI installation.

Workflow model validation uses the active `EngineAdapter`. In ComfyUI dev mode, that means the backend asks the forwarded/running ComfyUI API which models are available instead of reading a hardcoded local models folder.

## Local API Token

During browser development, `NOOFY_API_TOKEN` can be left unset and the backend will accept localhost API requests without a token.

For desktop builds, the Tauri shell should generate a random per-launch token, pass it to the backend as `NOOFY_API_TOKEN`, and inject the same value into the frontend runtime config. The token should never be stored or logged.
