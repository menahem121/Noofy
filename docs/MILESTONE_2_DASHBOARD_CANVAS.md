# Milestone 2 — Production Canvas Dashboard

Status: planning  
Relates to: [docs/MILESTONE_1.md](MILESTONE_1.md), [docs/NOOFY_IMPORT_DASHBOARD_WIDGET_FLOW.md](NOOFY_IMPORT_DASHBOARD_WIDGET_FLOW.md)

---

## 1. Goal

Replace the current flat-list normal workflow dashboard with a production canvas dashboard that renders the saved widget grid layout. Add user-specific value persistence, a settings toggle for the classic view, user layout editing, and a Noofy-managed asset store for image inputs.

---

## 2. Current Behavior

When a user opens a workflow (`WorkflowRunPage`):

- Input controls are rendered as a flat vertical list in a left panel.
- Output image appears in a fixed right preview panel regardless of where the creator placed it.
- The saved `layout.x / y / w / h` fields on each `DashboardControl` are **completely ignored**.
- Widget values reset to creator defaults on every page load.
- Image uploads go directly to ComfyUI's `input/` directory with no Noofy-managed durable record.
- There is no user preference for view mode, no layout editing, no restore-defaults action.

The Dashboard Builder already saves `layout` data into `dashboard.json` and the backend returns it through `GET /workflows/{id}/package`. The run view simply never reads it.

---

## 3. Product Requirements

| # | Requirement |
|---|---|
| 3.1 | Canvas renders widget positions from saved `layout.x/y/w/h` |
| 3.2 | Output image widgets resolve their result via `output_id → job result`, not a hardcoded `outputImages[0]` for all |
| 3.3 | Canvas works for configured, draft/invalid, and missing-runtime states |
| 3.4 | Frontend calls only the Noofy backend API |
| 3.5 | Dashboard does not look or feel like a node editor |
| 3.6 | View mode (canvas vs classic) is a user preference stored in `localStorage["noofy.prefs"]` |
| 3.7 | The current flat-list view is preserved as "Classic" mode |
| 3.8 | User-entered widget values persist across sessions per workflow |
| 3.9 | User values are stored in Noofy-managed local app data, separate from creator defaults |
| 3.10 | Image uploads are stored in a Noofy-managed asset store; ComfyUI `input/` is staging-only |
| 3.11 | A "Restore Default Values" button resets widget values to creator defaults |
| 3.12 | User can reposition existing widgets in "Edit Dashboard" mode |
| 3.13 | "Edit Dashboard" mode does not allow changing widget bindings or exposing new workflow variables |
| 3.14 | User layout overrides persist in Noofy-managed local app data |
| 3.15 | A "Reset Layout" button reverts user layout to creator layout |
| 3.16 | Widget placement uses a structured 12-column grid with named size presets |
| 3.17 | Canvas toolbar shows: Restore Default Values, Edit Dashboard / Edit Variables, Reset Layout |

---

## 4. Non-Goals (Milestone 2)

- Multi-output-node dashboards (M2 supports single-output-node only; see §11)
- Resize handles on widgets in the run view
- Named widget size changes by normal users (size is set by creator/importer in the builder)
- Cross-device sync of user state
- The original imported `.noofy` archive is never silently mutated

---

## 5. UX and Product Behavior

### 5.1 Default canvas view

When `dashboard.status === "configured"` and controls have `layout` data:

- Run page renders a full-width CSS Grid canvas (12 columns, `minmax(64px, auto)` row height, 14 px gap).
- Each widget is a card at `grid-column: x+1 / span w`, `grid-row: y+1 / span h`.
- Widgets feel like independent dashboard blocks, not graph nodes.
- Output image widgets show the result for their bound `output_id` inside their canvas cell.
- A sticky canvas toolbar sits above the grid with: **Restore Default Values**, **Edit Dashboard**, and **Reset Layout** (visible only when user layout overrides exist).
- A sticky canvas footer holds Run / Cancel / progress bar.

### 5.2 Canvas toolbar button behavior

**Restore Default Values**  
Resets all input widget values to creator defaults. No confirmation dialog in M2. Does not affect layout.

**Edit Dashboard / Edit Variables**  
Two-state toggle:
- Default label: **Edit Dashboard** — clicking enters layout editing mode.
- In layout editing mode: widgets become draggable; the label changes to **Edit Variables** — clicking exits layout editing mode back to normal value-editing mode.
- Layout editing mode does **not** allow changing widget bindings, adding hidden inputs, or exposing new ComfyUI parameters. Only repositioning existing widgets on the grid is possible.
- While in layout editing mode, widget value inputs are disabled (read-only) to prevent accidental changes.
- When the user drops a widget onto a new position, the layout override is saved immediately via `PUT /workflows/{id}/user-state`.

**Reset Layout**  
Appears only when `GET /workflows/{id}/user-state` returns a non-empty `layout_overrides` map. Calls `DELETE /workflows/{id}/user-state/layout`. Reverts canvas to creator layout.

### 5.3 Classic mode

Selected in Settings → Dashboard View → "Simple list". Renders the existing two-panel (inputs left, preview right) layout unchanged.

### 5.4 User values lifecycle

1. On first open: initialize from creator defaults.
2. On subsequent opens: load `GET /workflows/{id}/user-state`, merge with defaults (new inputs from creator get default value).
3. On every widget change: debounce-save via `PUT /workflows/{id}/user-state`.
4. Restore Default Values: call `DELETE /workflows/{id}/user-state/values` or send empty values; reset to defaults in UI.

### 5.5 Image input lifecycle

1. User picks a file in a `load_image` widget.
2. Frontend uploads to `POST /workflows/{id}/assets/image` → backend stores file in `{data_dir}/dashboard-assets/{asset_id}` → returns `{ asset_id, filename }`.
3. Dashboard state stores `asset_id` as the widget value.
4. Widget displays: "Loaded: {original filename}" as a hint label.
5. At run time: backend stages `dashboard-assets/{asset_id}` → ComfyUI `input/staging/` → passes the staged filename in the workflow graph. The ComfyUI `input/` directory is treated as ephemeral staging only.
6. No permanent duplicate: the asset lives in `dashboard-assets/`; ComfyUI `input/staging/` is a temporary symlink or copy that can be cleaned up after the job completes.

### 5.6 Workflow states the canvas must handle

| State | Canvas behavior |
|---|---|
| `configured`, runtime ready | Full canvas, Run enabled |
| `configured`, missing models | Full canvas, notice shown, Run disabled |
| `configured`, engine offline | Full canvas, notice shown, Run disabled |
| `not_configured` / `invalid` | Fall back to builder redirect or flat-list (as today) |
| No dashboard at all | `FallbackInputs` (minimal hardcoded controls) |

---

## 6. Widget Size System

Widgets have five named size presets. The creator/importer selects a preset in the builder layout step. Normal users cannot change widget sizes (only reposition).

| Preset | Grid columns (w) | Grid rows (h) | Use case |
|---|---|---|---|
| Compact | 3 | 2 | Sliders, toggles, simple number fields |
| Standard | 4 | 2 | Most input fields, seed widget, dropdowns |
| Wide | 6 | 3 | Textareas, prompt inputs |
| Media | 5 | 5 | Image input / output at moderate size |
| Media-Large | 7 | 7 | Primary output image, large preview |

Rules:
- Sizes align to the 12-column grid. A row of three Standard widgets fills 12 columns exactly.
- Two Compact widgets equal one Standard widget in width.
- Min widths / heights are enforced: min_w = w, min_h = h (no shrinking below preset).
- The builder `defaultLayoutForWidget` function maps each widget type to a default preset.
- The creator may override the preset in the builder layout step by choosing from the preset list (no freeform pixel resize).

Widget type → default preset mapping:

| Widget type | Default preset |
|---|---|
| slider, int_field, toggle | Compact |
| string_field, seed_widget, select | Standard |
| textarea | Wide |
| load_image, load_image_mask | Standard |
| display_image, result_image | Media-Large |
| lora_loader | Standard |

---

## 7. Output Widget Binding

In M2, `display_image` / `result_image` controls resolve their output as follows:

1. Each `DashboardControl` has `output_id: str | None`.
2. `output_id` maps to a `WorkflowOutput` in `packageData.outputs`, which has `node_id`.
3. After a job completes, the frontend matches `output.node_id` against the job result's output entries.
4. The matched image URL is shown inside that widget's canvas cell.

**M2 scope limitation**: If the dashboard has multiple `display_image` widgets each with a distinct `output_id` but the backend job result bundles all images in a single output entry (current ComfyUI adapter behavior), only `outputImages[0]` can be resolved per widget in M2. This is documented in the UI as "Multiple output images — showing the first result." Full per-output-node resolution is Milestone 3.

---

## 8. Data Model and Persistence

### 8.1 Creator schema (unchanged)

`{data_dir}/workflow-store/packages/{publisher}/{package}/{version}/dashboard.json`  
Written only by `DashboardAuthoringService`. Never mutated during normal user operation.

### 8.2 Noofy dashboard asset store

New directory: `{data_dir}/dashboard-assets/{asset_id}`  
One file per uploaded image, named by content-addressed or UUID asset ID.  
Written by: `POST /workflows/{id}/assets/image`  
Served by: `GET /assets/{asset_id}`  
Never duplicated into ComfyUI `input/` permanently.

### 8.3 User state store

New directory: `{data_dir}/user-state/{workflow_id}.json`  

```json
{
  "schema_version": "1",
  "workflow_id": "...",
  "dashboard_version": "0.1.0",
  "values": {
    "input_id": "<value>"
  },
  "layout_overrides": {
    "control_id": { "x": 0, "y": 0, "w": 4, "h": 2 }
  }
}
```

Written by: `PUT /workflows/{id}/user-state`  
Read by: `GET /workflows/{id}/user-state`  
Partial resets: separate delete-values / delete-layout actions.

**Schema version tracking**: `dashboard_version` is copied from the active `DashboardSchema.version` when user state is written. On load, the frontend hook compares stored `dashboard_version` against the current schema version. If they differ, the hook prunes stale keys: widget IDs that no longer exist in the current schema are removed from `values` and `layout_overrides`; new widget IDs get creator default values. The pruned state is immediately saved back. This ensures user state does not accumulate dead keys after the creator updates the dashboard.

Image input values stored here are `asset_id` strings, not filenames or raw bytes.

### 8.4 App preferences (localStorage only)

Key: `localStorage["noofy.prefs"]`  
Shape: `{ "viewMode": "canvas" | "classic" }`  
Default: `"canvas"`  
Rationale: view mode is a UI-only preference with no backend relevance.

### 8.5 Builder draft fallback key

`DashboardBuilderLayoutPage` currently writes `localStorage["noofy.dashboardLayout.{workflowId}"]` as a save fallback. Phase A must rename this to `noofy.builderDraft.{workflowId}` to prevent confusion with any user-state keys.

---

## 9. Backend and API Work

### 9.1 New path properties (`backend/app/core/paths.py`)

```python
@property
def dashboard_assets_dir(self) -> Path:
    return self.data_dir / "dashboard-assets"

@property
def user_state_dir(self) -> Path:
    return self.data_dir / "user-state"
```

Add both to `ensure_directories()` and `_all_named()`.

### 9.2 New Pydantic model (`backend/app/workflows/package.py` or new `user_state.py`)

```python
class UserStateLayoutOverride(BaseModel):
    x: int
    y: int
    w: int
    h: int

class WorkflowUserState(BaseModel):
    schema_version: str = "1"
    workflow_id: str
    dashboard_version: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    layout_overrides: dict[str, UserStateLayoutOverride] = Field(default_factory=dict)
```

### 9.3 New API endpoints (`backend/app/api/routes.py`)

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/workflows/{id}/assets/image` | Upload image to asset store; return `{ asset_id, view_url }` |
| `GET` | `/assets/{asset_id}` | Serve asset file |
| `GET` | `/workflows/{id}/user-state` | Return `WorkflowUserState` (empty defaults if not found) |
| `PUT` | `/workflows/{id}/user-state` | Save full user state (values + layout overrides) |
| `DELETE` | `/workflows/{id}/user-state/values` | Reset values only |
| `DELETE` | `/workflows/{id}/user-state/layout` | Reset layout overrides only |

### 9.4 Run handler change

Before submitting the ComfyUI graph, the engine service must:
1. For each input binding whose value is an `asset_id`: resolve `dashboard-assets/{asset_id}` → stage to `ComfyUI/input/staging/{asset_id}{ext}`.
2. Substitute the staged filename in the graph payload.
3. After job completion: optionally clean up the staged file (or rely on periodic temp cleanup).

This logic lives in the `ComfyUIEngineAdapter` run path, not in frontend code.

### 9.5 `DashboardControl` minor addition

Add `show_download: bool = False` to `DashboardControl` in `package.py`. The builder's `toBackendPayload()` sends this field. The run view respects it on `display_image` widgets.

### 9.6 Asset upload security requirements

The `POST /workflows/{id}/assets/image` endpoint must enforce:

- **Max file size**: reject requests over a defined limit (suggested 25 MB for v1).
- **MIME type and extension allowlist**: accept only `image/jpeg`, `image/png`, `image/webp`, `image/gif`. Reject anything else, regardless of the filename extension provided.
- **Generated asset IDs only**: asset filenames on disk are UUID-based, not derived from original filenames. Original filename is stored only as display metadata.
- **No path traversal**: the original filename from the upload is never used to construct filesystem paths. Asset IDs are validated as UUIDs before any file system access.
- **Basic image validity check**: attempt to open the file as an image (e.g., using `PIL.Image.verify()`) and reject corrupted or non-image files even if the MIME type matches.

### 9.7 Asset serving and authentication

Dashboard assets are served under `/api/assets/{asset_id}`, behind the same local API token policy as all other Noofy API routes.

Because `<img src>` cannot send Authorization headers, the frontend must not use asset URLs directly in `<img src>` tags. Instead:

1. Fetch asset bytes via `fetch("/api/assets/{asset_id}", { headers: { Authorization: ... } })`.
2. Create a `Blob URL` from the response: `URL.createObjectURL(blob)`.
3. Use the Blob URL as `<img src>`. Release it on component unmount with `URL.revokeObjectURL`.

The `noofyApi.ts` helper `fetchAssetBlobUrl(assetId)` encapsulates this pattern and is called by the `load_image` and `display_image` widget renderers.

---

## 10. Frontend Work

### New files

| File | Purpose |
|---|---|
| `frontend/src/features/workflows/CanvasDashboardView.tsx` | Grid canvas component |
| `frontend/src/lib/useAppPreferences.ts` | `{ viewMode, setViewMode }` backed by localStorage |
| `frontend/src/lib/useWorkflowUserState.ts` | `{ values, setValue, restoreDefaults, layoutOverrides, setLayoutOverride, resetLayout }` — calls backend user-state API |
| `frontend/src/lib/gridLayout.ts` | Extracted grid helpers: `layoutsOverlap`, `findAvailableLayout`, `fitLayout` (moved from builder) |

### State save invariant

`useWorkflowUserState` holds a single in-memory `WorkflowUserState` object. Both `setValue` and `setLayoutOverride` update that object before scheduling a save. Every debounced `PUT /workflows/{id}/user-state` sends the full current state. This ensures a value save cannot erase layout overrides and a layout save cannot erase values.

### Modified files

| File | Change |
|---|---|
| `WorkflowRunPage.tsx` | Integrate canvas view; use `useWorkflowUserState`; branch on `viewMode` |
| `EngineSettingsPage.tsx` | Add "Dashboard View" panel with canvas/classic toggle |
| `DashboardBuilderLayoutPage.tsx` | Import grid helpers from `gridLayout.ts`; rename localStorage draft key |
| `noofyApi.ts` | Add `layout` field to `DashboardControlDef`; add new asset and user-state API calls |
| `global.css` | Add `.dashboard-canvas`, `.canvas-toolbar`, `.canvas-run-footer`, `.widget-canvas-cell`, `.widget-canvas-cell--editing` |

### Reused unchanged

- `ControlWidget` function (all 11 widget renderers) — reused inside canvas cells
- `extractImageUrls` helper — still used in classic mode and for output resolution
- `FallbackInputs` component — kept for no-dashboard workflows
- Two-panel layout in `WorkflowRunPage` — becomes the "classic" branch

### Canvas drag-reposition behavior

In "Edit Dashboard" mode:
- Widget cells get a drag handle affordance.
- Uses HTML5 drag-and-drop (same API already used in the builder).
- MIME type: `application/noofy-dashboard-widget` (same as builder).
- On drop: compute target grid position; check collision using `layoutsOverlap` from `gridLayout.ts`; if collision, find nearest available position via `findAvailableLayout`; save override immediately via `PUT /workflows/{id}/user-state`.
- Canvas renders `effectiveLayout = userLayoutOverride ?? creatorLayout` per control.

---

## 11. Settings Work

`EngineSettingsPage.tsx` gains a "Dashboard View" panel:

```
Dashboard View
──────────────────────────────
[ Arranged layout ▸ ]   [ Simple list ]

"Arranged layout" shows widgets in the positions set by the workflow creator.
"Simple list" shows a plain vertical list. Use this if the canvas layout
does not fit your screen.
```

Reads/writes `localStorage["noofy.prefs"].viewMode`. No page reload required.

---

## 12. Testing Plan

| Test | File | Verifies |
|---|---|---|
| Canvas renders grid positions | `WorkflowRunPage.test.tsx` | Controls with layout render at correct CSS grid positions |
| Fallback for missing layout | `WorkflowRunPage.test.tsx` | Controls without layout render in auto-flow |
| Output image in correct cell | `WorkflowRunPage.test.tsx` | `display_image` widget shows result via `output_id` |
| Classic mode renders flat list | `WorkflowRunPage.test.tsx` | `viewMode=classic` → two-panel layout |
| Toolbar buttons present | `WorkflowRunPage.test.tsx` | Restore Defaults, Edit Dashboard, Reset Layout (conditional) visible |
| Edit Dashboard mode disables inputs | `WorkflowRunPage.test.tsx` | Widget inputs are read-only in layout editing mode |
| App preferences default | `useAppPreferences.test.ts` | Default is "canvas"; `setViewMode` updates localStorage |
| User state: load defaults | `useWorkflowUserState.test.ts` | First load uses creator defaults when no user state exists |
| User state: persist values | `useWorkflowUserState.test.ts` | `setValue` calls `PUT /user-state`; stored value returned on reload |
| User state: restore defaults | `useWorkflowUserState.test.ts` | `restoreDefaults` calls `DELETE /user-state/values`; values reset |
| User state: layout overrides | `useWorkflowUserState.test.ts` | `setLayoutOverride` saves override; `resetLayout` calls `DELETE /user-state/layout` |
| Asset upload route | `test_dashboard_assets.py` | `POST /assets/image` writes file to asset store; returns `asset_id` |
| Asset serve route | `test_dashboard_assets.py` | `GET /assets/{asset_id}` returns correct bytes |
| User state CRUD | `test_user_state.py` | GET/PUT/DELETE endpoints work; file written atomically |
| Asset staging at run time | `test_asset_staging.py` | Asset is copied/symlinked to ComfyUI input before graph execution |
| Grid helpers | `gridLayout.test.ts` | `layoutsOverlap`, `findAvailableLayout`, `fitLayout` cover overlap and no-space cases |
| Settings toggle | `EngineSettingsPage.test.tsx` | View mode toggle renders; clicking persists to localStorage |
| Schema version prune: stale values | `useWorkflowUserState.test.ts` | After dashboard_version change, orphaned input IDs are removed; new IDs get defaults |
| Schema version prune: stale overrides | `useWorkflowUserState.test.ts` | After dashboard_version change, orphaned layout_override keys are removed |
| Asset upload: oversized file rejected | `test_dashboard_assets.py` | File over size limit returns 413 |
| Asset upload: invalid MIME rejected | `test_dashboard_assets.py` | Non-image MIME returns 415 |
| Asset upload: path traversal rejected | `test_dashboard_assets.py` | Filename `../../etc/passwd` is never used in path construction |
| Asset serve: auth required | `test_dashboard_assets.py` | `GET /api/assets/{id}` without token returns 401 |

Run all: `make test`

---

## 13. Edge Cases

| Case | Handling |
|---|---|
| Control has no `layout` field | Falls back to `defaultLayoutForWidget(type)` auto-flow position |
| `dashboard.status !== "configured"` | Builder redirect or FallbackInputs as today |
| Asset file deleted from asset store | `GET /assets/{asset_id}` returns 404; widget shows "Image not found — please re-upload" |
| User state file corrupted | Backend returns 400; frontend falls back to creator defaults and logs warning |
| Dashboard with multiple `display_image` widgets (multi-output) | Each shows its bound output if resolvable; M2 limitation notice shown if job result bundles all images into one entry |
| Very tall canvas (many widgets) | Canvas scrolls vertically; toolbar and footer are `position: sticky` |
| Narrow screen (< 768 px) | Canvas collapses to single-column auto-flow |
| Layout editing drop onto occupied cell | `findAvailableLayout` finds nearest free cell; widget snaps there |
| `noofy.dashboardLayout.*` key in localStorage | Renamed to `noofy.builderDraft.*` in Phase A; no other layout-related localStorage keys remain |

---

## 14. Risks and Open Decisions

| Risk | Severity | Mitigation |
|---|---|---|
| Asset staging copies large images on every run | Medium | Use symlink on macOS/Linux; copy on Windows; clean up after job |
| `grid-auto-rows: 64px` clips tall widgets | Medium | Use `minmax(64px, auto)` |
| `WorkflowRunPage.tsx` size grows unwieldy | Medium | Extract `CanvasDashboardView` to its own file from the start |
| Multi-output job result not per-node | Low-Medium | Document M2 single-output limitation clearly in the UI |
| Builder `defaultLayoutForWidget` and named presets diverge | Low | Extract preset table to shared `widgetSizes.ts` constant used by both builder and run view |
| Drag-reposition in run view and builder share similar code | Low | `gridLayout.ts` shared utility avoids duplication |

---

## 15. Implementation Phases

### Phase A — Audit and constants alignment

- Read `DashboardBuilderLayoutPage.tsx`: confirm `defaultLayoutForWidget`, grid constants, draft localStorage key.
- Confirm `DashboardControlDef` in `noofyApi.ts` includes `layout`, `output_id`, `show_download` fields. Add any missing.
- Rename builder draft localStorage key from `noofy.dashboardLayout.*` to `noofy.builderDraft.*`.
- Extract `layoutsOverlap`, `findAvailableLayout`, `fitLayout` from builder into `frontend/src/lib/gridLayout.ts`.
- Define widget size preset table in `frontend/src/lib/widgetSizes.ts`.
- No UI changes in this phase.

**Files**: `noofyApi.ts`, `DashboardBuilderLayoutPage.tsx`, `gridLayout.ts` (new), `widgetSizes.ts` (new)

---

### Phase B — Canvas grid renderer

- Create `CanvasDashboardView.tsx`.
- Renders `.dashboard-canvas` CSS Grid. Each control at its `layout` position.
- `display_image` / `result_image` controls resolve output via `output_id → WorkflowOutput.node_id → job result`.
- Controls with no layout use `defaultLayoutForWidget` fallback.
- Sticky toolbar (placeholder buttons) and sticky footer with Run / Cancel / progress.
- Integrate into `WorkflowRunPage`: `hasDashboard && viewMode === "canvas"` → render canvas.
- Add CSS classes to `global.css`.

**Files**: `CanvasDashboardView.tsx` (new), `WorkflowRunPage.tsx`, `global.css`

---

### Phase C — Widget size presets in builder

- Update `DashboardBuilderLayoutPage.tsx` to offer the 5 named presets (Compact / Standard / Wide / Media / Media-Large) in the layout step, replacing the current freeform drag-resize.
- Builder uses `widgetSizes.ts` for preset definitions.
- Existing saved dashboards with custom `w/h` values continue to work (presets are just defaults).

**Files**: `DashboardBuilderLayoutPage.tsx`, `widgetSizes.ts`

---

### Phase D — Backend user state store

- Add `dashboard_assets_dir` and `user_state_dir` to `NoofyPaths`.
- Add `WorkflowUserState` Pydantic model.
- Add `UserStateService` with `get`, `save`, `clear_values`, `clear_layout` methods using atomic file writes.
- Add routes: `GET/PUT /workflows/{id}/user-state`, `DELETE /workflows/{id}/user-state/values`, `DELETE /workflows/{id}/user-state/layout`.
- Add `noofyApi.ts` calls for all new routes.
- Add tests: `backend/tests/test_user_state.py`.

**Files**: `paths.py`, `user_state.py` (new Pydantic model + service), `routes.py`, `noofyApi.ts`

---

### Phase E — Backend dashboard asset store

- Add `POST /workflows/{id}/assets/image` endpoint: receive file, write to `dashboard-assets/{uuid}{ext}`, return `{ asset_id, view_url }`.
- Add `GET /assets/{asset_id}` endpoint: serve file with correct content type.
- Add asset staging logic in `ComfyUIEngineAdapter`: before run, for each input value that is an `asset_id`, stage to `ComfyUI/input/staging/`; substitute staged path in graph; clean up staging file after job.
- Replace frontend `uploadWorkflowImage` with new `uploadDashboardAsset`.
- Add tests: `backend/tests/test_dashboard_assets.py`, `backend/tests/test_asset_staging.py`.

**Files**: `routes.py`, `engine/comfyui_adapter.py` (staging logic), `noofyApi.ts`

---

### Phase F — User values persistence wired to backend

- Create `useWorkflowUserState.ts` hook.
- `WorkflowRunPage` uses hook instead of `useState` + `useEffect` for input values.
- `setValue` debounce-saves via `PUT /workflows/{id}/user-state`.
- Image upload calls `uploadDashboardAsset`; stores returned `asset_id` as value.
- "Restore Default Values" button calls `DELETE /workflows/{id}/user-state/values`.
- Tests: `useWorkflowUserState.test.ts`.

**Files**: `useWorkflowUserState.ts` (new), `WorkflowRunPage.tsx`

---

### Phase G — Edit Dashboard mode + user layout overrides

- Canvas toolbar "Edit Dashboard" button enters layout editing mode.
- In editing mode: widget cells show drag handle; inputs are disabled.
- Drag-drop uses HTML5 API + `gridLayout.ts` helpers.
- On drop: compute new position, check collisions, save via `PUT /workflows/{id}/user-state`.
- "Edit Variables" button exits editing mode.
- "Reset Layout" button (visible when overrides exist) calls `DELETE /workflows/{id}/user-state/layout`.
- Tests: drag-reposition integration test in `WorkflowRunPage.test.tsx`.

**Files**: `CanvasDashboardView.tsx`, `WorkflowRunPage.tsx`, `useWorkflowUserState.ts`

---

### Phase H — Classic mode setting + tests + cleanup

- Create `useAppPreferences.ts`.
- Add "Dashboard View" panel to `EngineSettingsPage.tsx`.
- `WorkflowRunPage` branches on `viewMode`: `"canvas"` → `CanvasDashboardView`; `"classic"` → existing two-panel layout.
- Remove dead code if any.
- Complete all tests listed in §12.
- Update `AGENTS.md` documentation index.

**Files**: `useAppPreferences.ts` (new), `EngineSettingsPage.tsx`, `WorkflowRunPage.tsx`, `AGENTS.md`

---

## 16. Acceptance Criteria

### Canvas view
- [ ] Opening a configured workflow shows the canvas layout by default
- [ ] Widget positions match saved `x / y / w / h` from `dashboard.json`
- [ ] Output image appears inside its `display_image` widget cell after a successful run
- [ ] Run / Cancel / progress are always visible (sticky footer)
- [ ] Controls without `layout` fields render without crashing

### Toolbar
- [ ] "Restore Default Values" resets values to creator defaults
- [ ] "Edit Dashboard" enters layout editing mode; widget inputs become read-only
- [ ] "Edit Variables" exits layout editing mode
- [ ] Drag-drop in Edit Dashboard mode repositions the widget and saves override
- [ ] "Reset Layout" appears only when user layout overrides exist and reverts to creator layout

### Classic mode
- [ ] "Simple list" setting renders the two-panel flat-list view
- [ ] "Arranged layout" setting renders the canvas view
- [ ] Preference persists after navigating away and returning

### User values
- [ ] Values are restored from the previous session on page load
- [ ] Image widget shows "Loaded: {filename}" hint after upload
- [ ] "Restore Default Values" resets to creator defaults and clears stored values
- [ ] Uploaded image asset is stored in `dashboard-assets/`, not permanently in ComfyUI `input/`

### User layout
- [ ] Layout override persists in `user-state/{workflow_id}.json` after drag-reposition
- [ ] "Reset Layout" removes overrides and reverts canvas to creator positions

### Tests
- [ ] `make test` passes with all new tests
- [ ] No existing tests regressed
