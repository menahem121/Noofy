# backend/app — Agent Map

Python package root. Must remain named `app` — tied to Tauri launch (`python -m app`), packaging scripts, and Tauri bundle resource mappings.

## What this package owns

- App startup and composition (`main.py`, `__main__.py`, `composition.py`)
- HTTP API (`api/`)
- Engine adapter contract and ComfyUI adapter (`engine/`)
- Runtime mechanics — sidecar, runners, deps, memory, hardware (`runtime/`)
- Workflow packages, import, authoring, library, user state (`workflows/`)
- App model inventory, downloads, imports, tags, ownership (root `model_*.py` files; migration target: `models/`)
- Gallery persistence (`gallery.py`)
- User settings and API key settings (`settings/`)
- Trust roots and source policy (`trust.py`, `source_policy.py`)
- Core config, auth, logging, path resolution (`core/`)

## What it must NOT own

- ComfyUI source code (that lives in `third_party/comfyui/`)
- Community custom-node Python code — never imported, only treated as data
- Hardcoded model-folder paths from ComfyUI source checkout

## Read-first files

| Question | File |
|----------|------|
| How does the app start? | `main.py`, `composition.py` |
| How is the backend API structured? | `api/routes.py` (946 lines — Phase 1 target: split into `api/routes/`) |
| What does the main backend service do? | `engine/service.py` (3 300 lines — Phase 3 target: split into domain services) |
| What models does the app know about? | `model_inventory.py`, `model_inventory_schemas.py` |
| How does a workflow run? | `engine/service.py` → search `run_workflow` |
| How does the ComfyUI sidecar start? | `runtime/comfyui_sidecar_service.py` |
| How does runtime isolation work? | `runtime/isolation.py`, `runtime/capsule_installer.py` |

## Package structure

```
app/
  main.py              FastAPI app factory, lifespan, middleware
  __main__.py          `python -m app` entry point
  composition.py       All service/store construction and wiring

  api/
    routes.py          All routes — large, Phase 1 split target
    schemas.py         Shared Pydantic schemas

  engine/
    adapter.py         EngineAdapter ABC
    comfyui_adapter.py ComfyUIEngineAdapter
    diagnostics.py     Diagnostics store/sink/events — Phase 2 move target
    service.py         EngineService orchestrator — Phase 3 split target
    job_store.py       In-memory job registry
    models.py          Engine-level data models

  runtime/             See runtime/AGENTS.md

  workflows/           See workflows/AGENTS.md

  settings/
    api_keys.py        Provider API key settings
    model_folders.py   User model folder settings

  core/
    config.py          App configuration from env/args
    auth.py            Token auth middleware
    logging.py         Structured logging filters
    paths.py           Path resolution helpers

  model_inventory.py         App model inventory service
  model_inventory_schemas.py Pydantic schemas for model inventory
  model_download_jobs.py     Background model download jobs
  model_imports.py           Model import service
  model_paths.py             Model path resolution
  model_tags.py              Model tag store
  model_ownership.py         Model ownership tracking
  gallery.py                 Gallery persistence
  trust.py                   Trust roots
  source_policy.py           Workflow source policy
  artifacts.py               Packaged-runtime artifact helpers
```

## Active migration plan

[docs/NEW_AGENT_FIRST_ARCHITECTURE_IMPLEMENTATION_PLAN.md](../../docs/NEW_AGENT_FIRST_ARCHITECTURE_IMPLEMENTATION_PLAN.md)

Current phase: **Phase 0 — Navigation and Safety Rails** (adding AGENTS.md files, no behavior change).

## Tests

Backend tests live in `backend/tests/`. Focused runs:

```bash
backend/.venv/bin/python -m pytest backend/tests/test_api_runtime.py
backend/.venv/bin/python -m pytest backend/tests/test_diagnostics.py
backend/.venv/bin/python -m pytest backend/tests/test_model_inventory.py
backend/.venv/bin/python -m pytest backend/tests/test_backend_launcher.py
```

Full suite: `make test`
