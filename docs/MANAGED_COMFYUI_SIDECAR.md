# Managed ComfyUI Sidecar

The v1 product must not require users to launch ComfyUI manually.

External ComfyUI URLs such as `http://127.0.0.1:8188` are allowed for development and quick generation tests only.

## Product Requirement

In the shipped desktop app:

- The user launches only this desktop app.
- The desktop shell starts the app-owned FastAPI backend.
- The backend or desktop shell starts ComfyUI as a hidden local subprocess.
- ComfyUI listens on localhost as an internal engine service.
- The frontend talks only to FastAPI.
- FastAPI talks to ComfyUI through `ComfyUIEngineAdapter`.
- The app owns ComfyUI lifecycle: start, stop, health, port selection, logs, crash recovery, and user-facing errors.

## Isolation Requirement

Do not rely on the user's system Python having ComfyUI dependencies.

ComfyUI must run from an app-managed isolated Python environment, with its own dependencies such as `torch`, `aiohttp`, and model/runtime packages.

## Current Backend Foundation

The backend now has two layers:

- `RuntimeEnvironment`: resolves the ComfyUI repo, app-owned runtime directory, virtual environment Python, logs/cache directories, `requirements.txt`, detected hardware profile, PyTorch install plan, and required import status.
- `RuntimeManager`: owns runtime mode, free-port selection, process startup, health polling, stdout/stderr capture, failure reporting, and stop behavior.

Managed startup checks the environment before spawning ComfyUI. If the repo, entrypoint, requirements file, Python executable, runtime directory, or required imports are missing, `/api/health` includes the environment failure and `/api/logs` records the state transition.

`POST /api/engine/comfyui/bootstrap` currently creates the app-owned virtual environment, installs PyTorch for the detected machine, then installs `third_party/comfyui/requirements.txt` for managed runtime flows. macOS Intel uses standard macOS PyTorch wheels without CUDA, Apple Silicon uses standard macOS wheels with MPS available when PyTorch supports it, Linux/Windows without NVIDIA use CPU wheels, and NVIDIA machines use `nvidia-smi` CUDA capability to choose a CUDA wheel index. Exact CUDA policy remains overrideable through `COMFYUI_TORCH_CUDA_INDEX_URL` as PyTorch support changes.

`third_party/comfyui/` is Noofy's app-owned vendored ComfyUI source snapshot. It is not the user's external ComfyUI installation, and it is the only source checkout Noofy should maintain in the repo. Product managed sidecars should eventually launch from a clean reproducible ComfyUI source artifact under the app runtime store, such as `runtime-store/core-engines/comfyui-core-<version>-<source-hash>/`, produced by the packaging pipeline from the vendored source.

Managed sidecar startup runs normal ComfyUI hidden/no-browser and points writable ComfyUI paths at Noofy app data:

- `--disable-auto-launch`
- `--dont-print-server`
- `--base-directory <data_dir>`
- `--input-directory <input_dir>`
- `--output-directory <outputs_dir>`
- `--temp-directory <data_dir>` (ComfyUI appends its `temp` child internally)
- `--user-directory <user_state_dir>/comfyui`
- `--database-url sqlite:///<user_state_dir>/comfyui/comfyui.db`

This keeps models, input staging, outputs, temp files, custom nodes, ComfyUI user/database state, logs, cache, dashboard assets, and workflow state out of `third_party/comfyui/`.

`GET /api/runtime` and `GET /api/engine/comfyui/status` return lightweight runtime status for UI polling without running workflow validation or model checks. FastAPI shutdown stops a managed ComfyUI process so the backend does not leave an owned sidecar running after app exit.

## Implementation Tasks

- Create an app-managed ComfyUI Python environment. Initial backend support exists through `RuntimeEnvironment` and the bootstrap endpoint.
- ~~Add startup logic that chooses a free localhost port.~~ ✅ Implemented in `RuntimeManager`.
- ~~Start ComfyUI hidden as a subprocess.~~ ✅ Implemented for managed mode.
- ~~Capture and expose ComfyUI stdout/stderr through backend diagnostics.~~ ✅ Implemented.
- ~~Add health checks and startup timeout handling.~~ ✅ Implemented.
- Validate workflow models against the running sidecar through `ComfyUIEngineAdapter`.
- ~~Add crash detection and controlled restart behavior.~~ ✅ Implemented (see below).
- ~~Stop ComfyUI when the app exits.~~ ✅ FastAPI lifespan shutdown calls `EngineService.shutdown()`.
- Keep `COMFYUI_BASE_URL` support as development mode only.

The first runtime foundation supports `external` and `managed` modes. `external` observes a manually launched development ComfyUI URL. `managed` selects a localhost port when one is not configured, starts `main.py` from `third_party/comfyui` by default, polls `/system_stats` until ready or timed out, records startup failures in `/api/health` and `/api/logs`, and stops the process it started.

## Crash Detection and Restart

`RuntimeManager` runs a background watchdog `asyncio.Task` that awaits the managed ComfyUI process. When the process exits unexpectedly (not due to an explicit `stop()` call):

1. The crash is recorded: `crash_count` increments, `last_crash_at` is set.
2. A controlled restart loop begins with exponential backoff: `base * 2^(attempt-1)`.
3. Each restart attempt selects a new free port (unless a port was explicitly configured) and notifies the `EngineAdapter` of the new endpoint via an `on_restart` callback.
4. On successful restart (health check passes), `restart_attempt` resets to 0. `crash_count` remains cumulative.
5. If `max_restart_attempts` is exhausted, the manager records a terminal error and stops retrying.

Configuration (environment variables):

| Variable | Default | Description |
|---|---|---|
| `COMFYUI_MAX_RESTART_ATTEMPTS` | `3` | Maximum restart attempts before giving up |
| `COMFYUI_RESTART_BACKOFF_BASE` | `2.0` | Base seconds for exponential backoff |

## Orphan Process Cleanup

`RuntimeManager` writes the managed process PID to `<runtime_dir>/comfyui.pid` on start and removes it on stop. If the backend was killed (e.g., SIGKILL) and left an orphan ComfyUI process, the next startup detects the stale PID file, checks whether the process is still alive, and terminates it before starting a new instance.

## Crash State in API

`GET /api/runtime` and `GET /api/engine/comfyui/status` include:

- `crash_count`: cumulative crashes since last backend start
- `restart_attempt`: current restart attempt (0 when stable)
- `max_restart_attempts`: configured limit
- `uptime_seconds`: seconds since last successful start (null when not running)
- `last_crash_at`: ISO timestamp of last crash (null if no crashes)

## Acceptance Check

A user can install and open the desktop app, run the first workflow, and close the app without manually installing Python packages or launching ComfyUI.
