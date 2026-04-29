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

## Implementation Tasks

- Create an app-managed ComfyUI Python environment.
- Add startup logic that chooses a free localhost port.
- Start ComfyUI hidden as a subprocess.
- Capture and expose ComfyUI logs through backend diagnostics.
- Add health checks and startup timeout handling.
- Validate workflow models against the running sidecar through `ComfyUIEngineAdapter`.
- Add crash detection and controlled restart behavior.
- Stop ComfyUI when the app exits.
- Keep `COMFYUI_BASE_URL` support as development mode only.

## Acceptance Check

A user can install and open the desktop app, run the first workflow, and close the app without manually installing Python packages or launching ComfyUI.
