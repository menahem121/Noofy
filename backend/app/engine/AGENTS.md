# backend/app/engine — Agent Map

The engine layer owns two things: the app-owned `EngineAdapter` contract and its first implementation (`ComfyUIEngineAdapter`). Everything else in this folder is either a migration target or a dependency of the service orchestrator.

## What this package owns

- `adapter.py` — `EngineAdapter` ABC defining the contract all engine implementations must satisfy
- `comfyui_adapter.py` — `ComfyUIEngineAdapter`: translates app workflow requests into ComfyUI API calls
- `service.py` — `EngineService`: temporary migration facade while routes finish moving to domain services
- `job_store.py` — in-memory job registry shared across service methods
- `models.py` — engine-level data models (job status, run results, etc.)
- `memory_observation.py` — memory observation helpers used by the service
- `process_manager.py` — low-level process utilities
- `diagnostics.py` — temporary migration re-export for `app/diagnostics/`
- `factory.py` — `create_default_engine_service()` factory (also imported by `scripts/noofy.py`)

## What it must NOT own

- HTTP routing or request/response logic (that belongs in `api/`)
- Runtime mechanics — sidecar lifecycle, runner processes, hardware detection, dependency environments (those belong in `runtime/`)
- Workflow package parsing, import, or authoring logic (those belong in `workflows/`)
- App model inventory, downloads, or import (those belong in `app/models/`)
- Global diagnostics concerns (diagnostics store belongs in `app/diagnostics/`)

## Read-first files

| Question | File |
|----------|------|
| What operations does the engine contract define? | `adapter.py` |
| How does the app translate a workflow run into ComfyUI? | `comfyui_adapter.py` |
| Where is the run_workflow orchestration? | `runs/orchestrator.py` |
| How are job results stored? | `job_store.py` |
| How does the diagnostics store work? | `diagnostics/store.py` |
| How is the default EngineService built? | `factory.py` |

## EngineService Facade

`service.py` is a temporary migration facade. Do not add new domains to it.
Prefer wiring routes and internal callers to the domain services directly.
Remove facade methods once they no longer reduce migration risk.

Already extracted:
- `workflows/library_service.py`
- `workflows/import_orchestrator.py`
- `runs/orchestrator.py`, `runs/job_service.py`, `runs/result_service.py`
- `runtime/memory/service.py`
- `runtime/runners/lifecycle_service.py`
- `runtime/comfyui/*`

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests/test_comfyui_adapter.py
backend/.venv/bin/python -m pytest backend/tests/test_engine_service_install.py
backend/.venv/bin/python -m pytest backend/tests/test_diagnostics.py
```
