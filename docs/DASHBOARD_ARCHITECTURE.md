# Dashboard Architecture

Status: current architecture/reference.

Noofy turns an engine workflow into a curated dashboard. The ComfyUI graph stays engine-specific execution data; the dashboard is the public workflow interface that normal users see.

## Core Invariants

- The frontend calls only the Noofy backend API. It never calls ComfyUI directly.
- Workflow packages are the unit the app imports, stores, validates, runs, and exports.
- Dashboard widgets expose only values the creator/importer intentionally selected.
- The backend applies widget bindings to the graph before execution through the active `EngineAdapter`.
- Runtime/model readiness and dashboard readiness are separate states.
- Normal app use never silently mutates the original imported `.noofy` archive.
- Local user values, layout overrides, assets, memory observations, and install state live in app data, not in the portable workflow package.

## Package And Dashboard Files

Imported packages are normalized into the app-owned workflow store. Bundled starter workflows are read-only; user-imported packages are editable internal copies.

Important package files:

- `package.json`: package identity, source/trust metadata, required models, inputs, outputs, and engine metadata.
- `comfyui_graph.json`: ComfyUI API graph used as opaque execution data.
- `dashboard.json`: dashboard schema. Dashboard authoring writes this file only.
- `capsule.lock.json`: resolved immutable runtime facts for isolated workflow installs when present.
- `install-state.json`: mutable local preparation state when present.

Exporting creates a new portable archive. Re-exported user packages strip original trust signatures and are treated as local/user-authored unless a later publishing process signs them again.

## Dashboard Schema

`dashboard.json` is the source of truth for the user interface. It contains:

- `status`: typically `configured` or `not_configured`.
- `inputs[]`: workflow inputs that widgets may bind to.
- `outputs[]`: output records that result widgets bind to.
- `sections[].controls[]`: renderable controls with type, title, binding, default value, validation/display metadata, layout, and optional output metadata.

Common control types include `slider`, `int_field`, `string_field`, `textarea`, `toggle`, `load_image`, `display_image`, `result_image`, `seed_widget`, `lora_loader`, and `select`.

Each input control binds to a workflow input ID, which maps to an engine graph node ID and input name. Each output control uses `output_id`, which maps to a `WorkflowOutput` and then to result entries from the job.

## Import And Routing

Import accepts `.noofy` archives and raw ComfyUI JSON. A `.noofy` archive may already include package metadata, required models, custom-node records, export observations, and a dashboard schema. Raw JSON is a degraded import and usually needs dashboard setup.

When an archive declares required models, the import now goes through a staged preview/commit flow so the user can review missing models, optionally download them with progress and cancel, and only then commit the import. Pending sessions expire after 1 hour and return `410`. Full behavior, endpoints, and invariants are in [MODEL_RESOLUTION_AND_DOWNLOADS.md](MODEL_RESOLUTION_AND_DOWNLOADS.md).

Routing rules:

- Valid configured dashboard: open the run page.
- Missing, empty, invalid, or `not_configured` dashboard: open Dashboard Builder.
- Widgets selected but layout incomplete: open the layout step.
- Configured dashboard but missing models/runtime: open the dashboard with a preparation or blocked-run state.
- Unresolved runtime inputs: require widget/binding setup before the workflow can be considered ready.

Import and setup inspect workflow files as data. Community custom-node imports, compatibility checks, and smoke tests happen only inside isolated runner processes.

## Dashboard Builder

The builder has two responsibilities:

- Choose widgets: inspect bindable workflow values and output-capable nodes, select the values to expose, choose widget types, set user-facing labels/defaults/validation, and create bindings.
- Arrange widgets: place selected widgets on a responsive 32-column grid and save the final layout.

Saving a dashboard goes through the backend, validates bindings, writes only `dashboard.json` in the internal package copy, and then routes the workflow according to its updated setup state. Failed saves keep the local builder draft and must not navigate to the run page.

## Run Dashboard

Configured workflows open in the canvas dashboard by default.

Canvas behavior:

- Uses the saved 32-column grid layout from `dashboard.json`.
- Uses builder-compatible canvas presentation components and widget sizing.
- Locks layout in normal mode: no drag handles, resize handles, selection outlines, or preset chips.
- Provides a canvas action group with Run, Cancel, and workflow customization menu.
- Allows layout editing only through Edit Dashboard Layout mode; moves/resizes are grid-snapped and saved as user layout overrides.

Classic mode is a UI preference stored in `localStorage["noofy.prefs"].viewMode`. It keeps the simple two-panel list view for users who prefer it.

## User State And Assets

Creator defaults stay in `dashboard.json`. User-specific state is separate:

- Values, layout overrides, and output widget preferences live under `{data_dir}/user-state/{workflow_id}.json`.
- `WorkflowUserState.output_preferences` stores per-control Gallery Auto Save preferences. Missing preferences mean Auto Save is off.
- Image inputs upload to `{data_dir}/dashboard-assets/{asset_id}` through `POST /api/workflows/{id}/assets/image`.
- ComfyUI `input/` is staging-only. The backend stages dashboard assets into the runner-visible input directory immediately before execution.
- Asset serving is behind the same local API token policy as other `/api/*` routes. Frontend image widgets fetch asset bytes through the API helper and render Blob URLs.
- Generated result media is also served through the backend API. Job results contain app-owned output URLs such as `/api/jobs/{job_id}/outputs/view?...`, while the selected `EngineAdapter` performs any engine-specific file retrieval.

`WorkflowUserState.dashboard_version` is compared with the active dashboard schema version. When the schema changes, stale values, layout overrides, and removed-control output preferences are pruned, new controls use creator defaults, and the cleaned state is saved back.

## Auto Save Gallery

Auto Save is decided at run submission. The frontend sends the current output preference snapshot with `POST /api/workflows/{id}/run`; the backend validates it against the active dashboard schema and stores it with the job context. Later toggle changes affect future runs only.

Completed jobs save only final images whose `control_id -> output_id -> node_id` mapping matches an Auto Save-enabled output widget from the stored run snapshot. Gallery metadata and idempotency state live in `{data_dir}/outputs/gallery/gallery.db`; full images and thumbnails are stored in flat `images/` and `thumbnails/` folders. `GalleryStore` uses SQLite `BEGIN IMMEDIATE` write transactions as the cross-process serialization point for Gallery metadata and file allocation, so correctness does not depend on an undocumented single-backend-process guarantee for a data directory.

## Backend API Surface

Important dashboard APIs:

- `POST /api/workflows/import`: import a workflow package directly (no staged preview).
- `POST /api/workflows/import/preview`: stage a `.noofy` import so the model summary can be reviewed before commit. Returns an `import_session_id` when models need review, or commits immediately if the archive has no required models.
- `POST /api/workflows/import/{session}/download-models` / `GET .../{job_id}` / `POST .../{job_id}/cancel`: start, poll, and cancel the background model download job for a staged import.
- `POST /api/workflows/import/{session}/commit` / `DELETE /api/workflows/import/{session}`: finalize or discard a staged import.
- `GET /api/workflows/{id}/package`: return the normalized package and dashboard schema.
- `GET /api/workflows/{id}/model-summary`: return identity-verified required-model availability for the installed workflow.
- `GET /api/workflows/{id}/bindable-inputs`: expose bindable graph inputs for builder suggestions.
- `GET /api/workflows/{id}/unresolved-inputs`: expose unresolved runtime inputs that need setup.
- `GET /api/workflows/{id}/output-nodes`: expose output-capable nodes for result widgets.
- `PUT /api/workflows/{id}/dashboard`: save a configured dashboard.
- `POST /api/workflows/{id}/assets/image`: store a Noofy dashboard image asset.
- `POST /api/workflows/{id}/uploads/image`: upload or stage a workflow image input through the workflow-selected engine adapter.
- `GET /api/assets/{asset_id}`: serve a dashboard asset.
- `GET /api/jobs/{job_id}/outputs/view`: serve generated job output media through the job-bound engine adapter.
- `GET/PUT /api/workflows/{id}/user-state`: read/write values and layout overrides.
- `DELETE /api/workflows/{id}/user-state/values`: restore creator defaults.
- `DELETE /api/workflows/{id}/user-state/layout`: reset user layout overrides.
- `GET /api/gallery`, `GET /api/gallery/{item_id}`, `GET /api/gallery/{item_id}/image`, `GET /api/gallery/{item_id}/thumbnail`, `DELETE /api/gallery/{item_id}`, `PUT /api/gallery/{item_id}/favorite`: manage saved Gallery records and media.

## Code Map

- Backend package/dashboard models: [backend/app/workflows/package.py](../backend/app/workflows/package.py)
- Import/authoring/export: [backend/app/workflows/importer.py](../backend/app/workflows/importer.py), [backend/app/workflows/authoring.py](../backend/app/workflows/authoring.py), [backend/app/workflows/exporter.py](../backend/app/workflows/exporter.py)
- User state and assets: [backend/app/workflows/user_state.py](../backend/app/workflows/user_state.py), [backend/app/workflows/assets.py](../backend/app/workflows/assets.py)
- API routes: [backend/app/api/routes/](../backend/app/api/routes/)
- Canvas run view: [frontend/src/features/workflows/CanvasDashboardView.tsx](../frontend/src/features/workflows/CanvasDashboardView.tsx)
- Shared canvas presentation: [frontend/src/features/dashboard-canvas/DashboardCanvasPresentation.tsx](../frontend/src/features/dashboard-canvas/DashboardCanvasPresentation.tsx)
- Builder: [frontend/src/features/dashboard-builder/](../frontend/src/features/dashboard-builder/)
- User state/preference hooks: [frontend/src/lib/useWorkflowUserState.ts](../frontend/src/lib/useWorkflowUserState.ts), [frontend/src/lib/useAppPreferences.ts](../frontend/src/lib/useAppPreferences.ts)

## Focused Tests

- Backend dashboard authoring/routing/persistence/export: `backend/tests/test_dashboard_authoring.py`, `test_dashboard_routing.py`, `test_dashboard_persistence.py`, `test_exporter.py`
- Backend user state/assets/staging: `backend/tests/test_user_state.py`, `test_dashboard_assets.py`, relevant `test_comfyui_adapter.py`
- Frontend builder/run canvas: `frontend/src/features/dashboard-builder/*.test.tsx`, `frontend/src/features/workflows/WorkflowRunPage.test.tsx`, `frontend/src/features/dashboard-canvas/DashboardCanvasPresentation.test.ts`
- Frontend state/preferences: `frontend/src/lib/useWorkflowUserState.test.ts`, `frontend/src/lib/useAppPreferences.test.ts`
