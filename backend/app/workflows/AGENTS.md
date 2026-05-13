# backend/app/workflows — Agent Map

Workflow package domain: everything about what a workflow *is* and how it is imported, stored, authored, exported, and presented to users. Job execution lifecycle belongs in `runs/` (future Phase 3), not here.

## What this package owns

| File | Owns |
|------|------|
| `package.py` | `WorkflowPackage` data model (canonical workflow definition) |
| `package_persistence.py` | Persisting and loading workflow packages to/from disk |
| `library.py` | `WorkflowLibraryStore` — persisted metadata and run history |
| `library_service.py` | `WorkflowLibraryService` — listing, details, metadata update, removal, model availability, ComfyUI JSON export |
| `loader.py` | Loading workflow package data (merge, resolve) |
| `importer.py` | Staged import orchestration: preview → commit → cancel |
| `import_normalization.py` | Normalise raw import data into app-owned format |
| `import_policy.py` | Import trust/source policy evaluation |
| `import_capsule_lock.py` | Capsule lock derivation from import data |
| `import_runtime_profile.py` | Profile selection during import |
| `archive_validation.py` | .noofy archive signature and structure validation |
| `capsule.py` | Workflow capsule data model (immutable install snapshot) |
| `model_availability.py` | Workflow-specific model requirement checks |
| `authoring.py` | Dashboard authoring: bindable inputs, unresolved inputs, dashboard schema validation |
| `user_state.py` | Per-workflow user state (input values, layout) |
| `assets.py` | Dashboard asset management (uploaded images bound to workflows) |
| `exporter.py` | Export workflow as .noofy archive or ComfyUI JSON |
| `store_paths.py` | Filesystem path conventions for the workflow store |
| `validator.py` | Workflow package validation rules |

## What it must NOT own

- Job lifecycle, run orchestration, progress, cancellation (those belong in `runs/` — Phase 3)
- Runner process management or sidecar lifecycle (belongs in `runtime/`)
- App model inventory or downloads (belongs in root `model_*.py` files, future `models/`)
- HTTP routing (belongs in `api/`)
- Runner-visible materialized model views (belongs in `runtime/model_store.py`)

## Model boundary

`model_availability.py` answers: "Does this workflow's required model exist in the app model inventory?" This is a **workflow** concern (what does this package need?), not a `models/` concern. Keep it here.

Runner-visible model materialization (symlinks into workspace) belongs in `runtime/model_store.py`.

## Read-first files

| Question | File |
|----------|------|
| What is a workflow package? | `package.py` |
| How does .noofy import work? | `importer.py` |
| How is the dashboard schema validated? | `authoring.py` |
| How are workflow models checked? | `model_availability.py` |
| How are workflows stored on disk? | `store_paths.py`, `package_persistence.py` |
| How is a workflow exported? | `exporter.py` |

## Phase 3 migration status

- ✅ `library_service.py` — `WorkflowLibraryService` extracted from `EngineService`. Routes use `WorkflowLibraryServiceDep` directly. `EngineService` delegates.
- ✅ `import_orchestrator.py` — `WorkflowImportOrchestrator` extracted. Import routes use `WorkflowImportOrchestratorDep` directly. Temporary proxy properties on `EngineService` exist only for tests still moving to the domain service.
- ✅ `authoring` routes wired: `get_unresolved_inputs`, `validate_dashboard`, `save_dashboard` use `DashboardAuthoringServiceDep` directly. `get_bindable_inputs` stays on `EngineService` (needs live ComfyUI object_info).

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests/test_workflow_packages.py
backend/.venv/bin/python -m pytest backend/tests/test_noofy_importer.py
backend/.venv/bin/python -m pytest backend/tests/test_workflow_library_api.py
backend/.venv/bin/python -m pytest backend/tests/test_dashboard_authoring.py
backend/.venv/bin/python -m pytest backend/tests/test_model_availability.py
backend/.venv/bin/python -m pytest backend/tests/test_exporter.py
backend/.venv/bin/python -m pytest backend/tests/test_user_state.py
backend/.venv/bin/python -m pytest backend/tests/test_dashboard_assets.py
```
