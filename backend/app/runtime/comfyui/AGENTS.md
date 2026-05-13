# runtime/comfyui — Agent Map

Managed ComfyUI process: sidecar lifecycle, launch settings, update/rebuild pipeline.

## What this package owns

| File | Owns |
|------|------|
| `comfyui_sidecar_service.py` | `ComfyUISidecarService` — start/stop/status/settings/update delegates |
| `comfyui_updates.py` | `ComfyUIUpdateService` — update/rebuild orchestration |
| `comfyui_update_archive.py` | Archive download and verification |
| `comfyui_update_records.py` | Update history records |
| `comfyui_update_releases.py` | Release manifest fetching |
| `comfyui_update_smoke.py` | Post-update smoke validation |
| `launch_settings.py` | `ComfyUILaunchSettings` data model and store |

## What it must NOT own

- Runner process lifecycle (belongs in `runners/`)
- Workflow execution or job tracking (belongs in `runs/` or `engine/`)
- Model store or model resolution (belongs in `models/`)
