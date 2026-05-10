# Managed ComfyUI Sidecar

The v1 product must not require users to launch ComfyUI manually.

External ComfyUI URLs such as `http://127.0.0.1:8188` are allowed for development and quick generation tests only.

Status: current architecture/reference.

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

## Backend Runtime Foundation

The backend now has two layers:

- `RuntimeEnvironment`: resolves the ComfyUI repo, app-owned runtime directory, virtual environment Python, logs/cache directories, `requirements.txt`, detected hardware profile, PyTorch install plan, and required import status.
- `RuntimeManager`: owns runtime mode, free-port selection, process startup, health polling, stdout/stderr capture, failure reporting, and stop behavior.
- `ComfyUISidecarService`: owns the application-facing ComfyUI sidecar operations exposed by `/api/engine/comfyui/*`, including start/stop/bootstrap, launch settings, upstream update/rebuild/status, and repair after managed startup failures.

Managed startup checks the environment before spawning ComfyUI. If the repo, entrypoint, requirements file, Python executable, runtime directory, or required imports are missing, `/api/health` includes the environment failure and `/api/logs` records the state transition.

`POST /api/engine/comfyui/bootstrap` creates the app-owned virtual environment, installs PyTorch for the detected machine, then installs `third_party/comfyui/requirements.txt` for managed runtime flows. Noofy supports managed runtimes on macOS Apple Silicon, Windows, and Linux. macOS Intel is unsupported and fails closed before runtime preparation. Apple Silicon uses standard macOS wheels with MPS available when PyTorch supports it, Linux/Windows without NVIDIA use CPU wheels, and NVIDIA machines use `nvidia-smi` CUDA capability to choose a CUDA wheel index. Exact CUDA policy remains overrideable through `COMFYUI_TORCH_CUDA_INDEX_URL` as PyTorch support changes.

`third_party/comfyui/` is Noofy's app-owned vendored ComfyUI source snapshot. It is not the user's external ComfyUI installation, and it is the only source checkout Noofy should maintain in the repo. Product managed sidecars should eventually launch from a clean reproducible ComfyUI source artifact under the app runtime store, such as `runtime-store/core-engines/comfyui-core-<version>-<source-hash>/`, produced by the packaging pipeline from the vendored source.

Users may also install newer stable upstream ComfyUI releases from Noofy's
settings screen. Those self-updated sources are stored under
`runtime-store/core-engines/`, with per-version environments under
`runtime-store/core-envs/`. A downloaded version is activated only after local
startup/API/workflow/WebSocket/path-isolation smoke checks pass. The updater never
mutates `third_party/comfyui/`; see [COMFYUI_UPDATES.md](COMFYUI_UPDATES.md).

Managed sidecar startup runs normal ComfyUI hidden/no-browser and points writable ComfyUI paths at Noofy app data:

- `--disable-auto-launch`
- `--dont-print-server`
- `--base-directory <data_dir>`
- `--input-directory <input_dir>`
- `--output-directory <outputs_dir>`
- `--temp-directory <data_dir>` (ComfyUI appends its `temp` child internally)
- `--user-directory <user_state_dir>/comfyui`
- `--database-url sqlite:///<user_state_dir>/comfyui/comfyui.db`

The settings screen also lets users choose a managed ComfyUI VRAM launch mode. `Normal VRAM` is the default and passes no VRAM flag, preserving ComfyUI's default behavior. Other options map to ComfyUI's existing launch flags: `--gpu-only`, `--highvram`, `--lowvram`, `--novram`, or `--cpu`. The setting is stored in Noofy runtime storage and applies only to managed mode; when changed while the managed sidecar is running, Noofy stops and restarts that sidecar through the backend.

This keeps models, input staging, outputs, temp files, custom nodes, ComfyUI user/database state, logs, cache, dashboard assets, and workflow state out of `third_party/comfyui/`.

The managed sidecar also sees Noofy's user-visible model storage through a generated `extra-model-paths.yaml` under the runtime store. The configured Noofy Models folder (default `~/Documents/Noofy Models`) is registered as the default category root, and an optional user-connected ComfyUI `models/` folder is registered as a secondary read/reuse-only root. Downloads always land in the Noofy Models folder; Noofy must never write models into the external ComfyUI folder or `third_party/comfyui/`. See [MODEL_RESOLUTION_AND_DOWNLOADS.md](MODEL_RESOLUTION_AND_DOWNLOADS.md).

`GET /api/runtime` and `GET /api/engine/comfyui/status` return lightweight runtime status for UI polling without running workflow validation or model checks. FastAPI shutdown stops a managed ComfyUI process so the backend does not leave an owned sidecar running after app exit.

## Runtime Modes

The runtime foundation supports `external` and `managed` modes.

`external` observes a manually launched development ComfyUI URL. It is a developer convenience only.

`managed` selects a localhost port when one is not configured, starts `main.py` from the active app-owned ComfyUI source, polls `/system_stats` until ready or timed out, records startup failures in `/api/health` and `/api/logs`, and stops the process it started.

Workflow model validation must go through `ComfyUIEngineAdapter` and the running sidecar/API. It must not read a hardcoded local ComfyUI source `models` folder.

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
