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
- `POST /api/engine/comfyui/start`: request startup of the local ComfyUI sidecar.
- `POST /api/engine/comfyui/stop`: stop the ComfyUI process if this backend started it.
- `GET /api/workflows`: list available workflow packages.
- `POST /api/workflows/{workflow_id}/validate`: validate package bindings and required models.
- `POST /api/workflows/{workflow_id}/run`: run a workflow through the active `EngineAdapter`.
- `GET /api/jobs/{job_id}/progress`: get the latest normalized job progress.
- `GET /api/jobs/{job_id}/events`: stream frontend-ready progress and result events as `text/event-stream`.

## Environment Overrides

- `COMFYUI_BASE_URL`: default `http://127.0.0.1:8188`
- `COMFYUI_WS_URL`: default `ws://127.0.0.1:8188/ws`
- `COMFYUI_PYTHON_EXECUTABLE`: default `python3`

These overrides are development conveniences. Product builds should use app-managed runtime paths and ports.

Workflow model validation uses the active `EngineAdapter`. In ComfyUI dev mode, that means the backend asks the forwarded/running ComfyUI API which models are available instead of reading a hardcoded local models folder.
