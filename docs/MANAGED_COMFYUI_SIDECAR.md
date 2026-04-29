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

- `RuntimeEnvironment`: resolves the ComfyUI repo, app-owned runtime directory, virtual environment Python, logs/cache directories, `requirements.txt`, and required import status.
- `RuntimeManager`: owns runtime mode, free-port selection, process startup, health polling, stdout/stderr capture, failure reporting, and stop behavior.

Managed startup checks the environment before spawning ComfyUI. If the repo, entrypoint, requirements file, Python executable, runtime directory, or required imports are missing, `/api/health` includes the environment failure and `/api/logs` records the state transition.

`POST /api/engine/comfyui/bootstrap` creates the app-owned virtual environment and installs `ComfyUI-official-repo/requirements.txt`. This is the first bootstrap pass; platform-specific GPU dependency packaging is still intentionally deferred.

## Implementation Tasks

- Create an app-managed ComfyUI Python environment. Initial backend support exists through `RuntimeEnvironment` and the bootstrap endpoint.
- Add startup logic that chooses a free localhost port. Initial backend support lives in `RuntimeManager`.
- Start ComfyUI hidden as a subprocess. Initial backend support exists for managed mode.
- Capture and expose ComfyUI stdout/stderr through backend diagnostics. Initial backend support exists.
- Add health checks and startup timeout handling. Initial backend support exists.
- Validate workflow models against the running sidecar through `ComfyUIEngineAdapter`.
- Add crash detection and controlled restart behavior.
- Stop ComfyUI when the app exits.
- Keep `COMFYUI_BASE_URL` support as development mode only.

The first runtime foundation supports `external` and `managed` modes. `external` observes a manually launched development ComfyUI URL. `managed` selects a localhost port when one is not configured, starts `main.py`, polls `/system_stats` until ready or timed out, records startup failures in `/api/health` and `/api/logs`, and stops the process it started.

## Acceptance Check

A user can install and open the desktop app, run the first workflow, and close the app without manually installing Python packages or launching ComfyUI.
