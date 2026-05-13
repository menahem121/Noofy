# backend/app/models — Agent Map

User-facing model (AI model file) management: inventory, downloads, imports, tags, paths, and ownership.

## Submodules

| File | Owns |
|------|------|
| `inventory.py` | `ModelInventoryService` — model listing, validation, metadata, import orchestration |
| `schemas.py` | Shared Pydantic schemas used across model management (`ModelTag`, `ModelEntry`, `ModelImportRequest`, …) |
| `downloads.py` | `ModelDownloadJobService` — background download jobs, progress, cancellation |
| `imports.py` | `ModelImportService` — staged import preview, file-based import transactions |
| `paths.py` | Path utilities: `model_key`, `parse_model_key`, `ensure_inside`, canonical model directory helpers |
| `tags.py` | `ModelTagStore` — per-model tag CRUD |
| `ownership.py` | `ModelOwnershipStore` — tracks which workspace/runner owns a model copy |
| `folders.py` | Noofy Models folder settings, external model folder linkage, ComfyUI extra model paths config |

## What it must NOT own

- Runner-visible model materialization and GC (belongs in `runtime/models/`)
- Workflow package parsing (belongs in `workflows/`)
- Engine adapter or ComfyUI model paths (belongs in `engine/`)

## Import Rule

Import model-management code from `app.models.*`. Do not add root-level `app/model_*.py` re-export helpers for unreleased internal paths.
