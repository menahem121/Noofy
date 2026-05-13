# runtime/models — Agent Map

Runner-visible model views: materialization into isolated workspaces.

## What this package owns

| File | Owns |
|------|------|
| `model_store.py` | Runner-visible model symlinks/copies into workspace |
| `model_gc.py` | GC for stale materialized model views |

## What it must NOT own

- App-level model inventory, downloads, or ownership (belongs in `app/models/`)
- Workflow-required model availability checks (belongs in `workflows/model_availability.py`)
