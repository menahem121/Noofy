# Backend

This is the app-owned Python backend.

It exposes the desktop app API, owns the `EngineAdapter` contract, validates workflow packages, and translates workflow runs into the active engine implementation.

The first engine implementation is `ComfyUIEngineAdapter`, which talks to a local ComfyUI service through HTTP and WebSocket APIs.

During development, that service may be an externally launched ComfyUI instance such as `http://127.0.0.1:8188`.

For product v1, external ComfyUI is not a requirement. The desktop app must run ComfyUI as an app-managed sidecar in an isolated Python environment.

## Development Entry Point

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload
```

The backend is intentionally separate from `ComfyUI-official-repo/`. ComfyUI is treated as the first sidecar engine, not as the public API of this app.

## Useful Endpoints

- `GET /api/health`: backend status, ComfyUI reachability, workflow package count, and missing model summary.
- `GET /api/logs`: list recent backend, engine, ComfyUI, and workflow diagnostics.
- `POST /api/engine/comfyui/bootstrap`: create the app-owned ComfyUI virtual environment and install `requirements.txt`.
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
- `NOOFY_RUNTIME_DIR`: app-owned runtime directory. Defaults to `.noofy-runtime` under the repo root.
- `COMFYUI_PYTHON_EXECUTABLE`: optional runtime Python override. When unset, managed mode uses the app-owned virtual environment under `NOOFY_RUNTIME_DIR`.
- `COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE`: Python used to create the managed virtual environment. Defaults to `python3`.
- `COMFYUI_MANAGED_HOST`: managed sidecar bind host, default `127.0.0.1`
- `COMFYUI_MANAGED_PORT`: optional managed sidecar port. When unset, the backend selects a free localhost port.
- `COMFYUI_STARTUP_TIMEOUT_SECONDS`: managed startup health polling timeout, default `60`
- `COMFYUI_HEALTH_POLL_INTERVAL_SECONDS`: managed startup health polling interval, default `0.5`

External-mode overrides are development conveniences. Product builds should use `COMFYUI_RUNTIME_MODE=managed` with app-managed runtime paths and ports. Managed startup checks the ComfyUI repo, `main.py`, `requirements.txt`, runtime directory writability, runtime Python availability, and initial imports for `torch` and `aiohttp` before starting the sidecar.

Workflow model validation uses the active `EngineAdapter`. In ComfyUI dev mode, that means the backend asks the forwarded/running ComfyUI API which models are available instead of reading a hardcoded local models folder.
