# runtime/storage — Agent Map

Runtime storage management: runner workspaces, garbage collection.

## What this package owns

| File | Owns |
|------|------|
| `storage_gc.py` | `RuntimeStorageGarbageCollector` — GC for orphaned runner workspaces and dep envs |
| `workspace_store.py` | Runner workspace directory management |
| `workspace_preparer.py` | Preparing isolated runner workspace for execution |

## What it must NOT own

- App-level model downloads or model folder settings (belongs in `models/`)
- Capsule installation (belongs in `runtime/capsule_installer.py`)
