# Codebase Map

Quick orientation for agents and contributors. Each section names what a domain owns, its read-first entry point, and what it must NOT own.

---

## Repository Roots

```
backend/         Python FastAPI backend — do not rename; tied to Tauri, Makefile, packaging
frontend/        Vite + React + Tauri desktop frontend — do not rename
third_party/     ComfyUI source snapshot managed by Noofy (read: docs/MANAGED_COMFYUI_SIDECAR.md)
comfyui_export2noofy_node/  ComfyUI custom node for .noofy export
docs/            Architecture and design docs — start here for concepts
scripts/         Dev/release scripts
test_workflows/  .noofy workflow fixtures used in validation tests
```

Ignore when searching: `.noofy-runtime/`, `graphify-out/`, `frontend/dist/`, `frontend/node_modules/`, `frontend/src-tauri/target/`, `backend/.venv/`, `.pytest_cache/`, `__pycache__/`.

---

## Backend (`backend/app/`)

Entry: [backend/app/AGENTS.md](../backend/app/AGENTS.md)

| Package | Owns | Read-first |
|---------|------|------------|
| `api/` | HTTP routing, deps, errors, request/response translation | `api/router.py`, `api/routes/` |
| `engine/` | `EngineAdapter` contract, `ComfyUIEngineAdapter`; temporary `EngineService` facade for remaining migration seams | `engine/adapter.py`, `engine/comfyui_adapter.py`, `engine/service.py` |
| `runs/` | Workflow run orchestration, job status, cancellation, outputs, run logs/results | `runs/orchestrator.py`, `runs/job_service.py`, `runs/result_service.py` |
| `runtime/` | ComfyUI sidecar, runners, deps, memory, storage, hardware, profiles | `runtime/AGENTS.md` |
| `workflows/` | Workflow packages, import, authoring, library, user state, assets, exporting | `workflows/AGENTS.md` |
| `models/` | App model inventory, downloads, imports, tags, ownership, model folders | `models/AGENTS.md`, `models/inventory.py`, `models/downloads.py`, `models/folders.py` |
| `settings/` | User settings and API keys | `settings/api_keys.py` |
| `gallery.py` | Mixed-media Gallery persistence, migration, and background save coordination | `gallery.py` |
| `core/` | Config loading, auth middleware, logging, path resolution | `core/config.py`, `core/paths.py` |
| `trust.py` / `source_policy.py` | Trust roots, source policy | `trust.py` |
| `composition.py` | App-wide wiring/DI | `composition.py` |

**Architecture status** (see [NEW_AGENT_FIRST_ARCHITECTURE_IMPLEMENTATION_PLAN.md](NEW_AGENT_FIRST_ARCHITECTURE_IMPLEMENTATION_PLAN.md)):

- Diagnostics, API routes, model services, run services, and most runtime domains now live in their owning packages.
- `engine/service.py` remains only as a temporary migration facade for callers that have not moved directly to domain services.
- Do not add new compatibility helpers for unreleased internal paths; update imports to the owning package instead.

---

## Frontend (`frontend/src/`)

Entry: [frontend/src/AGENTS.md](../frontend/src/AGENTS.md)

| Path | Owns |
|------|------|
| `App.tsx`, `main.tsx` | App shell and router |
| `features/app/` | Layout, runtime status provider |
| `features/home/` | Home page and workflow library provider |
| `features/workflows/` | Workflow library, run page, canvas view, dashboard builder |
| `features/models/` | Models page, import panel, download tracking |
| `features/settings/` | Engine settings page |
| `features/gallery/` | Gallery page |
| `features/history/` | History page |
| `features/dashboard-builder/` | Dashboard builder and layout pages |
| `features/dashboard-canvas/` | Canvas run presentation |
| `lib/api/noofyApi.ts` | All backend API types and request functions (split target: Phase 6) |
| `lib/` | Shared hooks and utilities |
| `styles/global.css` | All CSS (split target: Phase 7) |

**Core rule**: Frontend must call `/api/*` on the Noofy backend only. Never call ComfyUI endpoints directly.

**Architecture status**: `lib/api/noofyApi.ts` is a temporary barrel over domain API modules. New frontend imports should prefer the owning domain module when practical.

---

## Key Architecture Docs

| Topic | Doc |
|-------|-----|
| Stack, process boundaries | [docs/ARCHITECTURE.md](ARCHITECTURE.md) |
| Engine contract and job lifecycle | [docs/ENGINE_CONTRACT.md](ENGINE_CONTRACT.md) |
| Workflow packages | [docs/WORKFLOW_PACKAGES.md](WORKFLOW_PACKAGES.md) |
| Dashboard architecture | [docs/DASHBOARD_ARCHITECTURE.md](DASHBOARD_ARCHITECTURE.md) |
| Model resolution and downloads | [docs/MODEL_RESOLUTION_AND_DOWNLOADS.md](MODEL_RESOLUTION_AND_DOWNLOADS.md) |
| Runtime isolation (community workflows) | [docs/RUNTIME_ISOLATION_ARCHITECTURE.md](RUNTIME_ISOLATION_ARCHITECTURE.md) |
| ComfyUI managed sidecar | [docs/MANAGED_COMFYUI_SIDECAR.md](MANAGED_COMFYUI_SIDECAR.md) |
| Memory governor | [docs/MEMORY_GOVERNOR.md](MEMORY_GOVERNOR.md) |
| Trust and publishing | [docs/NOOFY_VERIFIED_PUBLISHING.md](NOOFY_VERIFIED_PUBLISHING.md) |

---

## Acceptance Invariants

These must hold across all refactors:

- `python -m app --port 0` still launches the backend.
- `make run` and `make test` still work.
- All API URLs and payload shapes are unchanged.
- Frontend never calls ComfyUI directly.
- `.noofy` package schemas are unchanged.
- Tauri resource mapping still resolves `noofy-runtime/backend/app`.
- Diagnostics use one shared store (no private per-subsystem stores).
- Trusted backend never imports community custom-node modules.
- Model validation uses the active `EngineAdapter`, not a hardcoded ComfyUI source folder.
