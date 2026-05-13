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
| `api/` | HTTP routing, deps, errors, request/response translation | `api/routes.py` |
| `engine/` | `EngineAdapter` contract, `ComfyUIEngineAdapter` | `engine/adapter.py`, `engine/service.py` |
| `runtime/` | ComfyUI sidecar, runners, deps, memory, storage, hardware, profiles | `runtime/AGENTS.md` |
| `workflows/` | Workflow packages, import, authoring, library, user state, assets, exporting | `workflows/AGENTS.md` |
| `models/` (root files now) | App model inventory, downloads, imports, tags, ownership | `model_inventory.py`, `model_download_jobs.py` |
| `settings/` | User settings and API keys | `settings/api_keys.py`, `settings/model_folders.py` |
| `gallery.py` | Gallery persistence | `gallery.py` |
| `core/` | Config loading, auth middleware, logging, path resolution | `core/config.py`, `core/paths.py` |
| `trust.py` / `source_policy.py` | Trust roots, source policy | `trust.py` |
| `composition.py` | App-wide wiring/DI | `composition.py` |

**Migration target** (see [NEW_AGENT_FIRST_ARCHITECTURE_IMPLEMENTATION_PLAN.md](NEW_AGENT_FIRST_ARCHITECTURE_IMPLEMENTATION_PLAN.md)):

- `engine/diagnostics.py` → future `diagnostics/` package (Phase 2)
- Root `model_*.py` files → future `models/` package (Phase 5)
- `api/routes.py` → split into `api/routes/` by domain (Phase 1)
- `engine/service.py` → split into domain services (Phase 3)
- `runtime/` flat files → subdomain packages (Phase 4)

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

**Migration target** (Phase 6): Split `lib/api/noofyApi.ts` into `api/` domain modules.

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
