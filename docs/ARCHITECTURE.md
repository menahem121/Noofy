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

## Key Decisions

- Wrap ComfyUI first. Do not fork ComfyUI now.
- Do not rebuild a minimal AI engine for the first version.
- Do not let the frontend call ComfyUI directly.
- Treat ComfyUI graphs as opaque execution data where possible.
- Keep the app API stable enough that future adapters can replace or supplement ComfyUI.
- Own the ComfyUI lifecycle in the app: start, stop, health checks, port selection, logs, crash recovery, and clear errors.

## Future Engine Direction

The first adapter is `ComfyUIEngineAdapter`.

Future adapters may include:

- `MacNativeEngineAdapter` for Apple-native acceleration through Core ML, Metal, or MLX.
- `WindowsNativeEngineAdapter` if a native Windows path becomes useful.

Distribution must account for ComfyUI's GPLv3 license when bundling or modifying it.
