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

Imported packages are normalized into the app-owned workflow store. Bundled starter workflow source files are read-only, but users may still customize their dashboards through app-data overrides. User-imported packages are editable internal copies.

Important package files:

- `package.json`: package identity, source/trust metadata, required models, inputs, outputs, and engine metadata.
- `comfyui_graph.json`: ComfyUI API graph used as opaque execution data.
- `dashboard.json`: dashboard schema. Dashboard authoring writes this file only for editable internal package copies.
- `capsule.lock.json`: resolved immutable runtime facts for isolated workflow installs when present.
- `install-state.json`: mutable local preparation state when present.

Exporting creates a new portable archive. Re-exported user packages strip original trust signatures and are treated as local/user-authored unless a later publishing process signs them again.

## Dashboard Schema

`dashboard.json` is the source of truth for the user interface. It contains:

- `status`: typically `configured` or `not_configured`.
- `inputs[]`: workflow inputs that widgets may bind to.
- `outputs[]`: output records that result widgets bind to.
- `sections[].controls[]`: renderable controls with type, title, optional binding, default value, validation/display metadata, standalone layout, and optional output metadata.
- `sections[].groups[]`: visual containers with group title, helper description, ordered control IDs, and group layout. Groups do not merge control values or bindings; each child control remains independently bound.

Common control types include `slider`, `int_field`, `string_field`, `textarea`, `note`, `toggle`, `load_image`, `load_audio`, `load_video`, `load_3d`, `load_file`, `display_image`, `display_audio`, `display_video`, `display_3d`, `display_file`, `result_image`, `seed_widget`, `lora_loader`, and `select`. `display_3d` is the canonical 3D output widget.

Media output records include a media `kind`: `image`, `audio`, `video`, `3d`, `text`, or `file`, and keep the legacy `type` field for compatibility. Result renderers should read `kind` first and fall back to `type` for older packages. The schema intentionally accepts declared non-image output kinds even before every kind has a dedicated dashboard widget; widget validation must still reject non-image outputs for `display_image` and `result_image`.

Each input control binds to a workflow input ID, which maps to an engine graph node ID and input name. Each output control uses `output_id`, which maps to a `WorkflowOutput` and then to result entries from the job. Informational `note` controls may be dashboard-only: they store creator-authored multi-line text without an executable workflow binding.

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

Exported packages may declare unresolved runtime inputs for creator-local image, audio, video, 3D, text, and generic file loader values. Import normalizes those records as setup prompts, retaining only safe metadata such as node ID, node type, input name, expected kind, required flag, and extension/MIME hints. If the creator explicitly includes an input asset, the archive stores it as `assets/input-defaults/...` and the dashboard input default references it with `source: "package_asset"`. Private paths, absolute paths, temp/output paths, unchecked file bytes, base64 media, generated output filenames, and ComfyUI runtime bucket identities must not be persisted.

Duplicate package identity is an explicit user decision. If an imported
archive has the same publisher/package/version identity as an existing local
workflow, Noofy stages the import and offers three choices:

- Replace existing workflow: deliberately replaces the internal package copy.
  The original imported archive remains unchanged, and stale local dashboard
  values, layout overrides, output preferences, install state, and other
  package-local state are not silently reused.
- Import as copy: creates a separate local workflow with a distinct internal
  identity and understandable copy name. Because the identity changes, trust
  and signature metadata are not presented as if the copied package were still
  the original signed identity.
- Cancel import: removes the staged session and leaves the existing workflow
  untouched.

## Dashboard Builder

Bindable-input discovery uses app-owned contracts for bundled ComfyUI media loaders and portable metadata stored with imported workflows. The portable snapshot includes declared input groups and types, upload flags, safe combo choices, labels, hints, and output types; hidden inputs and frontend helpers are not offered as dashboard controls. When a raw custom-node workflow has not produced a complete snapshot yet, the API reports `controls_preparing`; Dashboard Builder keeps its normal loading skeleton and retries while isolated preparation captures the schema. Routine preparation is not surfaced as a notice or warning. A calm blocking dialog is reserved for terminal cases where the managed workflow engine or required add-ons are unavailable.

The builder has two responsibilities:

- Choose widgets: inspect bindable workflow values and output-capable nodes, select the values to expose, choose widget types, set user-facing labels/defaults/validation, and create bindings.
- Arrange widgets: place selected widgets on a responsive tile canvas and save the final layout.

Dashboard layout coordinates are tile-based, not pixel-based. Widgets persist
`x`, `y`, `w`, and `h` in the dashboard schema. The canvas uses 32 columns and a
stable 24-row visible area for responsive dashboards. `rowHeight` remains
fallback/design metadata: when `responsive` is true, builder and run view derive
the rendered row height from the available canvas height; when `responsive` is
false, they use the fixed `rowHeight`. This keeps a widget whose bottom edge is
on row 24 attached to the bottom of the usable canvas on taller screens without
rewriting saved layout coordinates.

Saving a dashboard goes through the backend, validates bindings, then writes only dashboard schema data. Imported workflows write `dashboard.json` in the internal package copy. Bundled native workflows write a user-owned dashboard override under `{data_dir}/workflow-store/dashboard-overrides/{workflow_id}/dashboard.json`, leaving bundled source files immutable. Failed saves keep the local builder draft and must not navigate to the run page.

## Run Dashboard

Configured workflows open in the canvas dashboard by default.

Canvas behavior:

- Uses the saved tile layout from `dashboard.json`; `x`, `y`, `w`, and `h`
  remain the source of truth.
- Uses builder-compatible canvas presentation components, row-height derivation, visible-row clamping, and widget sizing.
- Locks layout in normal mode: no drag handles, resize handles, selection outlines, or preset chips.
- Provides a canvas action group with Run, Cancel, and workflow customization menu.
- Allows layout editing only through Edit Dashboard Layout mode; moves/resizes are grid-snapped and saved as user layout overrides.

Classic mode is a UI preference stored in `localStorage["noofy.prefs"].viewMode`. It keeps the simple two-panel list view for users who prefer it.

## User State And Assets

Creator defaults stay in `dashboard.json`. User-specific state is separate:

- Values, layout overrides, and output widget preferences live under `{data_dir}/user-state/{workflow_id}.json`.
- `WorkflowUserState.output_preferences` stores per-control Gallery Auto Save preferences. Missing preferences mean Auto Save is off.
- Image inputs upload to `{data_dir}/dashboard-assets/{asset_id}` through `POST /api/workflows/{id}/assets/image`.
- Audio inputs upload to `{data_dir}/dashboard-assets/{asset_id}` through `POST /api/workflows/{id}/assets/audio`, form field `audio`. Supported dashboard audio assets are streamed to temporary files first, validated as wav, mp3, flac, ogg, or m4a, capped at 100 GB per file, and moved atomically into place. Audio dashboard assets are local app data and are not stored inside portable `.noofy` packages.
- Video inputs upload to `{data_dir}/dashboard-assets/{asset_id}` through `POST /api/workflows/{id}/assets/video`, form field `video`. Supported dashboard video assets are streamed to temporary files first, validated as mp4, mov, webm, or mkv, capped at 100 GB per file, and moved atomically into place. Video dashboard assets are local app data and are not stored inside portable `.noofy` packages.
- Generic file inputs upload to `{data_dir}/dashboard-assets/{asset_id}` through `POST /api/workflows/{id}/assets/file`, form fields `input_id` and `file`. The saved workflow input binding supplies the accepted extension and MIME allow-list. These uploads are streamed with the same 100 GB cap and temporary-file cleanup as audio/video, but Noofy does not execute, import, unzip, deeply parse, or preview arbitrary uploaded files.
- ComfyUI `input/` is staging-only. The backend stages dashboard assets into the runner-visible input directory immediately before execution.
- Asset serving is behind the same local API token policy as other `/api/*` routes. Frontend image widgets fetch asset bytes through the API helper and render Blob URLs. Audio, video, and 3D widgets render backend media URLs directly so large files are not blob-fetched into memory. Generic file widgets fetch only metadata and use backend-owned URLs for open/download actions.
- Generated result media is also served through the backend API. Job results contain app-owned output URLs such as `/api/jobs/{job_id}/outputs/view?...`, while the selected `EngineAdapter` performs any engine-specific file retrieval.

`WorkflowUserState.dashboard_version` is compared with the active dashboard schema version. When the schema changes, stale values, layout overrides, and removed-control output preferences are pruned, new controls use creator defaults, and the cleaned state is saved back. Native workflow dashboard overrides are reset by deleting the override file, which restores the bundled dashboard schema on the next package load.

## Multimedia Gallery

Gallery treats generated `image`, `video`, `audio`, `3d`, and generic `file` outputs as first-class saved media. Auto Save is decided at run submission. The frontend sends the current output preference snapshot with `POST /api/workflows/{id}/run`; the backend validates it against the active dashboard schema and stores a sanitized run manifest with the job context. Later toggle changes affect future runs only. A completed declared output can also be saved manually while the job-bound adapter can still resolve its source.

Completed jobs save only final media whose `control_id -> output_id -> node_id` mapping matches a declared output widget. Uploaded dashboard inputs are never Gallery results. The background save coordinator streams each output through the backend-owned adapter path, stages it to a temporary file with disk checks, atomically finalizes it, and records per-control states such as `queued`, `saving`, `saved`, `failed`, `canceled`, `interrupted`, and `unavailable`. Concurrent Auto Save and manual Save requests reuse the same idempotent item.

Compatible saved `image`, `audio`, `video`, and `3d` Gallery items can be selected as dashboard media inputs. User state stores a metadata-only Gallery reference, never a media URL or filesystem path. At run submission the backend re-resolves the Gallery item, validates its kind and package-declared extension/MIME constraints, and stages the file into the active runner input workspace. Missing or incompatible items block the run with a user-facing validation error.

Output preferences are keyed by output control ID and must not assume every output is an image. Gallery metadata, sanitized manifests, and save state live in `{data_dir}/outputs/gallery/gallery.db`; full saved media lives in flat `media/` storage, and image thumbnails live in `thumbnails/`. Legacy image rows and files under `images/` migrate transactionally and remain readable. Only images are inspected with Pillow. Videos and 3D models use placeholder cards unless a backend-owned thumbnail is available, and generic files are never executed, imported, unpacked, deeply parsed, or previewed.

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
- `DELETE /api/workflows/{id}/dashboard`: remove a user-owned native dashboard override and fall back to the bundled dashboard.
- `POST /api/workflows/{id}/assets/image`: store a Noofy dashboard image asset.
- `POST /api/workflows/{id}/assets/audio`: store a Noofy dashboard audio asset.
- `POST /api/workflows/{id}/assets/video`: store a Noofy dashboard video asset.
- `POST /api/workflows/{id}/assets/file`: store a Noofy dashboard file asset for the declared `load_file` input.
- `POST /api/workflows/{id}/assets/3d`: stream a Noofy dashboard 3D asset into app-owned storage. GLTF JSON uploads are bounded to 16 MiB and accepted only when referenced resources are embedded.
- `POST /api/workflows/{id}/uploads/image`: upload or stage a workflow image input through the workflow-selected engine adapter.
- `GET /api/assets/{asset_id}`: serve a dashboard asset.
- `GET /api/jobs/{job_id}/outputs/view`: serve generated job output media through the job-bound engine adapter.
- `GET/PUT /api/workflows/{id}/user-state`: read/write values and layout overrides.
- `DELETE /api/workflows/{id}/user-state/values`: restore creator defaults.
- `DELETE /api/workflows/{id}/user-state/layout`: reset user layout overrides.
- `GET /api/gallery`, `GET /api/gallery/{item_id}`, `GET /api/gallery/{item_id}/content`, `GET /api/gallery/{item_id}/thumbnail`, `DELETE /api/gallery/{item_id}`, `PUT /api/gallery/{item_id}/favorite`: manage saved Gallery records and backend-owned media. Gallery listing supports picker-oriented `kind`, `search`, `accepted_extensions`, `accepted_mime_types`, `limit`, and `cursor` filters. `GET /api/gallery/{item_id}/image` remains a compatibility alias for older History links.
- `GET /api/jobs/{job_id}/gallery`, `POST /api/jobs/{job_id}/gallery/{control_id}`, `POST /api/jobs/{job_id}/gallery/{control_id}/cancel`: read per-output Gallery save state, manually save a declared completed output, or cancel an active background copy.

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
