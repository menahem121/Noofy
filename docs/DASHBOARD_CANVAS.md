# Milestone 2 — Production Canvas Dashboard

Status: in progress
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
| 3.11 | Restore Default Values resets widget values to creator defaults |
| 3.12 | User can reposition and resize existing widgets in "Edit Dashboard Layout" mode |
| 3.13 | "Edit Dashboard Layout" mode does not allow changing widget bindings or exposing new workflow variables |
| 3.14 | User layout overrides persist in Noofy-managed local app data |
| 3.15 | Reset Layout reverts user layout overrides to the creator layout |
| 3.16 | Widget placement uses a structured 32-column grid with named size presets |
| 3.17 | Canvas top-right action group shows: Run Workflow, Cancel Run, and a square workflow customization button. The workflow customization dropdown contains: Restore Default Values, Edit Dashboard Layout, Edit Widgets, Reset Layout, Export as JSON, Export as Noofy |

---

## 4. Non-Goals (Milestone 2)

- Multi-output-node dashboards (M2 supports single-output-node only; see §11)
- Free-pixel (non-grid) resize or drag of widgets
- Size-preset chip buttons visible on widgets in the run view
- Moving or resizing widgets outside of Edit Dashboard Layout mode
- Cross-device sync of user state
- The original imported `.noofy` archive is never silently mutated

---

## 5. UX and Product Behavior

### 5.1 Default canvas view

When `dashboard.status === "configured"` and controls have `layout` data:

- Run page renders a full-width responsive canvas grid (32 columns, 32 px row height, 14 px gap).
- Each widget is a card at `grid-column: x+1 / span w`, `grid-row: y+1 / span h`.
- Widgets feel like independent dashboard blocks, not graph nodes.
- Output image widgets show the result for their bound `output_id` inside their canvas cell.
- A canvas action group sits at the **top-right of the canvas**. Left to right: **Run Workflow** → **Cancel Run** → square workflow customization button (▼). Progress is shown inline in the canvas (e.g., a progress bar below the action group or overlaid on the canvas), not in a separate sticky footer.
- In normal viewing mode the grid is fully locked: no drag handles, no resize handles, no widget selection, no preset chips.

### 5.2 Workflow customization dropdown

The square workflow customization button at the top-right of the canvas opens a dropdown containing all workflow-scoped actions. It must use a customization/controls icon, not the same gear icon as the global app settings button.

| Menu item | Behavior |
|---|---|
| **Restore Default Values** | Resets all input widget values to creator defaults. No confirmation dialog. Does not affect layout. Calls `DELETE /workflows/{id}/user-state/values`. |
| **Edit Dashboard Layout** | Enters layout editing mode (see §17.3). Widget value inputs become read-only; drag and resize handles appear on widget cells. |
| **Edit Widgets** | Returns the user to the widget configuration/builder step for this workflow. |
| **Reset Layout** | Visible only when `GET /workflows/{id}/user-state` returns a non-empty `layout_overrides` map. Calls `DELETE /workflows/{id}/user-state/layout`. Reverts canvas to creator layout. |
| **Export as JSON** | Exports the workflow graph as a raw ComfyUI-compatible JSON file. |
| **Export as Noofy** | Exports the workflow as a `.noofy` package archive. |

Constraints:
- Layout editing mode does **not** allow changing widget bindings, adding hidden inputs, or exposing new ComfyUI parameters.
- When the user finishes repositioning or resizing a widget, the resulting grid position is saved immediately via `PUT /workflows/{id}/user-state`.
- Exiting Edit Dashboard Layout mode (via a **Done** button or menu item) returns to normal viewing mode.

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

Widgets have five named size presets. The creator/importer selects a preset in the builder layout step.

Widget type → default preset mapping:
| Preset | Grid columns (w) | Grid rows (h) | Use case |
|---|---|---|---|
| Compact | 6 | 4 | toggles, int_field, string_field, toggle, seed_widget |
| Standard | 8 | 6 | Most input fields, textarea, dropdowns, lora_loader |
| Wide | 10 | 4 | slider |
| Media | 10 | 10 | load_image , display_mask |
| Media-Large | 14 | 14 | result_image, large preview |

Rules:
- Sizes align to the 32-column grid. A row of four Compact widgets or two Wide widgets fills 32 columns exactly.
- Named presets define `min_w` and `min_h` for each widget type. A widget cannot be resized smaller than its minimum.
- The builder `defaultLayoutForWidget` function maps each widget type to a default preset.
- The creator may override the preset in the builder layout step (no freeform pixel resize in the builder either).
- In normal workflow viewing mode, users cannot move or resize widgets.
- In Edit Dashboard Layout mode, users can move and resize widgets using grid-snapped handles. The minimum size enforced during resize is the widget's `min_w` / `min_h`.
- **No size-preset chip/button UI is rendered on widget cards in the run view.** Presets exist as internal schema defaults and minimums only; they are not exposed as selectable UI elements.

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
| `noofyApi.ts` | Add `layout` field to `DashboardControlDef`; add new asset, user-state, and export API calls |
| `global.css` | Add `.dashboard-canvas`, `.canvas-action-group`, `.canvas-progress`, `.widget-canvas-cell`, `.widget-canvas-cell--editing` |

### Reused unchanged

- `ControlWidget` function (all 11 widget renderers) — reused inside canvas cells
- `extractImageUrls` helper — still used in classic mode and for output resolution
- `FallbackInputs` component — kept for no-dashboard workflows
- Two-panel layout in `WorkflowRunPage` — becomes the "classic" branch

### Canvas edit-mode behavior

In Edit Dashboard Layout mode:
- Widget cells show a drag handle. The user can drag to reposition.
- Widget cells show grid-snapped edge/corner resize handles. Resize snaps to grid cells; `min_w` / `min_h` are enforced.
- On move or resize: compute target grid cell; check collision using `layoutsOverlap` from `gridLayout.ts`; snap to nearest valid position; save override immediately via `PUT /workflows/{id}/user-state`.
- Widget value inputs are disabled (read-only) during edit mode.
- Canvas renders `effectiveLayout = userLayoutOverride ?? creatorLayout` per control.

In normal viewing mode:
- No drag handles, no resize handles, no selection outlines are rendered.
- No preset chips or size buttons are rendered.
- Widget value inputs are fully interactive.

---

## 11. Settings Work

> **Note:** The workflow-specific actions button described in §17 is separate from this app-global setting. The "Dashboard View" panel below controls the global canvas-vs-classic preference only.

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
| Canvas action group present | `WorkflowRunPage.test.tsx` | Run Workflow and Cancel Run buttons visible at top-right of canvas |
| Workflow customization button present | `WorkflowRunPage.test.tsx` | Square customization button visible at top-right, to the right of Cancel Run |
| Customization dropdown opens/closes | `WorkflowRunPage.test.tsx` | Clicking the button opens dropdown; clicking outside closes it |
| Dropdown contains all items | `WorkflowRunPage.test.tsx` | Dropdown shows: Restore Default Values, Edit Dashboard Layout, Edit Widgets, Reset Layout, Export as JSON, Export as Noofy |
| Reset Layout hidden when no overrides | `WorkflowRunPage.test.tsx` | Reset Layout item is absent/disabled when `layout_overrides` is empty |
| Normal mode: no layout affordances | `WorkflowRunPage.test.tsx` | No drag handles, resize handles, preset chips, or selection outlines rendered in normal mode |
| Edit Dashboard Layout mode disables inputs | `WorkflowRunPage.test.tsx` | Widget inputs are read-only in layout editing mode |
| Edit mode: resize handles visible | `WorkflowRunPage.test.tsx` | Grid-snapped resize handles appear on widget cells in edit mode |
| Edit mode: min size enforced | `WorkflowRunPage.test.tsx` | Widget cannot be resized below `min_w` / `min_h` |
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
| Very tall canvas (many widgets) | Canvas scrolls vertically; the canvas action group at the top-right remains accessible (sticky or fixed); progress remains visible without a separate footer |
| Narrow screen (< 768 px) | Canvas collapses to single-column auto-flow |
| Layout editing drop onto occupied cell | `findAvailableLayout` finds nearest free cell; widget snaps there |
| `noofy.dashboardLayout.*` key in localStorage | Renamed to `noofy.builderDraft.*` in Phase A; no other layout-related localStorage keys remain |

---

## 14. Risks and Open Decisions

| Risk | Severity | Mitigation |
|---|---|---|
| Asset staging copies large images on every run | Medium | Use symlink on macOS/Linux; copy on Windows; clean up after job |
| Fixed row heights clip tall widgets | Medium | Size widget shells from their configured row span and keep content internally constrained |
| `WorkflowRunPage.tsx` size grows unwieldy | Medium | Extract `CanvasDashboardView` to its own file from the start |
| Multi-output job result not per-node | Low-Medium | Document M2 single-output limitation clearly in the UI |
| Builder `defaultLayoutForWidget` and named presets diverge | Low | Extract preset table to shared `widgetSizes.ts` constant used by both builder and run view |
| Drag-reposition in run view and builder share similar code | Low | `gridLayout.ts` shared utility avoids duplication |

---

## 15. Implementation Phases

### Phase A — Audit and constants alignment ✅ DONE

- `DashboardControlDef` in `noofyApi.ts` updated to include `layout`, `output_id`, `show_download`, `min_w`, `min_h`.
- Builder draft localStorage key renamed from `noofy.dashboardLayout.*` to `noofy.builderDraft.*`.
- `layoutsOverlap`, `findAvailableLayout`, `fitLayout` extracted to `frontend/src/lib/gridLayout.ts`.
- Widget size preset table defined in `frontend/src/lib/widgetSizes.ts`.

**Files**: `noofyApi.ts`, `DashboardBuilderLayoutPage.tsx`, `gridLayout.ts` (new), `widgetSizes.ts` (new)

---

### Phase B — Canvas grid renderer ✅ DONE (UI corrected in Phase I)

- `CanvasDashboardView.tsx` created (~580 lines).
- Renders the shared responsive canvas grid (32 columns, 32 px rows). Each control at its `effectiveLayout` position (user override → creator layout → widget-type default).
- `display_image` / `result_image` controls resolve output via `output_id → WorkflowOutput.node_id → job result`.
- `AssetImageInput` fetches asset blob URLs with auth and revokes on unmount.
- Integrated into `WorkflowRunPage`: `hasDashboard && viewMode === "canvas"` → renders canvas; classic two-panel otherwise.
- CSS classes added to `global.css`.
- _Phase I will correct: old sticky toolbar/footer replaced by canvas action group; old action buttons moved to workflow customization dropdown._

**Files**: `CanvasDashboardView.tsx` (new), `WorkflowRunPage.tsx`, `global.css`

---

### Phase C — Widget size presets in builder ✅ DONE

- `DashboardBuilderLayoutPage.tsx` uses `defaultLayoutForWidgetType` from `widgetSizes.ts`.
- `SizePresetPicker` component renders 5 preset buttons; active preset detected by w+h match.
- Local duplicate helpers (`defaultLayoutForWidget`, `fitLayout`, `findAvailableLayout`, `layoutsOverlap`) removed in favour of shared modules.

**Files**: `DashboardBuilderLayoutPage.tsx`, `widgetSizes.ts`

---

### Phase D — Backend user state store ✅ DONE

- `user_state_dir` and `dashboard_assets_dir` added to `NoofyPaths`, `ensure_directories()`, and `_all_named()`.
- `WorkflowUserState` + `UserStateLayoutOverride` Pydantic models in `app/workflows/user_state.py`.
- `UserStateService` with `get`, `save`, `clear_values`, `clear_layout` — atomic file writes.
- Routes added: `GET/PUT /workflows/{id}/user-state`, `DELETE /workflows/{id}/user-state/values`, `DELETE /workflows/{id}/user-state/layout`.
- `noofyApi.ts`: `fetchUserState`, `saveUserState`, `deleteUserStateValues`, `deleteUserStateLayout` added.
- Tests: `backend/tests/test_user_state.py` (7 tests, all passing).
- `useWorkflowUserState.ts` hook created.

**Files**: `paths.py`, `user_state.py` (new), `routes.py`, `noofyApi.ts`, `useWorkflowUserState.ts` (new)

---

### Phase E — Backend dashboard asset store ✅ DONE

- `POST /workflows/{id}/assets/image`: validates MIME, size ≤ 25 MB, `imghdr` check, UUID filename, atomic write.
- `GET /assets/{asset_id}`: path-traversal validated; serves with correct content type via `FileResponse`.
- `DashboardAssetService` in `app/workflows/assets.py`.
- `ComfyUIEngineAdapter` accepts `dashboard_assets_dir`; `_stage_assets` copies matching asset files to `ComfyUI/input/staging/` before run and cleans up on job completion.
- `uploadDashboardAsset` + `fetchAssetBlobUrl` added to `noofyApi.ts`.
- `dashboard_assets_dir` wired into adapter in `create_default_engine_service`.
- Tests: `backend/tests/test_dashboard_assets.py` (9 tests) + 4 staging tests in `test_comfyui_adapter.py` — all passing.

**Files**: `assets.py` (new), `routes.py`, `comfyui_adapter.py`, `service.py`, `noofyApi.ts`

---

### Phase F — User values persistence wired to backend ✅ DONE

- `useWorkflowUserState` hook in `WorkflowRunPage` replaces `useState`/`useEffect` approach.
- `setValue` debounce-saves via `PUT /workflows/{id}/user-state`.
- `handleImageUpload` calls `uploadDashboardAsset`; stores `asset_id` as value.
- `restoreDefaults` resets to creator defaults.

**Files**: `useWorkflowUserState.ts` (new), `WorkflowRunPage.tsx`

---

### Phase G — Edit Dashboard mode + user layout overrides ✅ DONE (UI corrected in Phase I)

- `CanvasDashboardView` implements drag-reposition in edit mode.
- Widget cells show drag handle and disable inputs in editing mode.
- Layout override saved via `setLayoutOverride` → debounced PUT.
- "Reset Layout" calls `resetLayout` → `DELETE /workflows/{id}/user-state/layout`.
- Layout overrides stored in `{data_dir}/user-state/{workflow_id}.json`.
- _Phase I will correct: add grid-snapped resize handles; move Edit/Reset actions into workflow customization dropdown; enforce normal-mode lock (no handles visible)._

**Files**: `CanvasDashboardView.tsx`, `WorkflowRunPage.tsx`, `useWorkflowUserState.ts`

---

### Phase H — Classic mode setting + tests + cleanup ✅ DONE

- `useAppPreferences.ts` created (localStorage-backed `viewMode: "canvas" | "classic"`).
- "Dashboard View" panel added to `EngineSettingsPage.tsx` with two radio options and CSS.
- `.settings-option-group` / `.settings-option` CSS classes added to `global.css`.
- `WorkflowRunPage` branches on `viewMode`.
- Backend tests: `test_user_state.py` (7 tests), `test_dashboard_assets.py` (9 tests), adapter staging tests (4 tests).

**Files**: `useAppPreferences.ts` (new), `EngineSettingsPage.tsx`, `WorkflowRunPage.tsx`, `global.css`

---

### Phase I — Corrected Canvas UI/UX

Corrects the canvas interaction model to match §17 and §3.17. Phases B and G shipped with the old sticky toolbar/footer model; this phase replaces it.

- [ ] Remove old sticky canvas toolbar (Restore Default Values, Edit Dashboard/Edit Variables, Reset Layout standalone buttons).
- [ ] Remove old sticky run footer (Run / Cancel buttons in footer).
- [ ] Add canvas action group at the top-right of the canvas: **Run Workflow** → **Cancel Run** → square workflow customization button.
- [ ] Workflow customization button uses a customization/controls icon (not the global settings gear icon).
- [ ] Workflow customization dropdown contains: Restore Default Values, Edit Dashboard Layout, Edit Widgets, Reset Layout (conditional), Export as JSON, Export as Noofy.
- [ ] Progress indicator shown inline in the canvas area, not in a footer bar.
- [ ] Normal viewing mode: remove all drag handles, resize handles, selection outlines, and preset chips from widget cells.
- [ ] Edit Dashboard Layout mode: add grid-snapped edge/corner resize handles; enforce `min_w` / `min_h` during resize drag.
- [ ] Rename CSS classes: `.canvas-toolbar` → `.canvas-action-group`, remove `.canvas-run-footer`, add `.canvas-progress`.
- [ ] Visual parity: `CanvasDashboardView` canvas area uses the same background, grid tokens, and widget card styles as `DashboardBuilderLayoutPage`.
- [ ] Classic mode unchanged: two-panel layout in `WorkflowRunPage` is not modified.
- [ ] Update `WorkflowRunPage.test.tsx` to cover all new tests listed in §12.

**Files**: `CanvasDashboardView.tsx`, `WorkflowRunPage.tsx`, `WorkflowRunPage.test.tsx`, `global.css`

---

## 16. Acceptance Criteria

### Canvas view
- [x] Opening a configured workflow shows the canvas layout by default
- [x] Widget positions match saved `x / y / w / h` from `dashboard.json`
- [x] Output image appears inside its `display_image` widget cell after a successful run
- [x] Controls without `layout` fields render without crashing
- [ ] Run canvas is visually identical to the builder canvas (background, grid, widget cards)
- [ ] Run / Cancel buttons and workflow customization button are at the top-right of the canvas
- [ ] Progress is shown inline in the canvas area, not in a sticky footer
- [ ] Normal mode: no drag handles, resize handles, selection outlines, or preset chips visible

### Canvas action group and workflow customization dropdown
- [ ] Run Workflow and Cancel Run buttons visible at top-right of canvas
- [ ] Square workflow customization button visible to the right of Cancel Run
- [ ] Customization button opens dropdown with: Restore Default Values, Edit Dashboard Layout, Edit Widgets, Reset Layout, Export as JSON, Export as Noofy
- [ ] Reset Layout item hidden/disabled when no layout overrides exist
- [ ] "Restore Default Values" resets values to creator defaults
- [ ] "Edit Dashboard Layout" enters layout editing mode; widget inputs become read-only; drag and resize handles appear
- [ ] Resize in edit mode snaps to grid; `min_w`/`min_h` enforced
- [ ] Move/resize saves layout override immediately; "Reset Layout" reverts to creator positions

### Classic mode
- [x] "Simple list" setting renders the two-panel flat-list view
- [x] "Arranged layout" setting renders the canvas view
- [x] Preference persists after navigating away and returning

### User values
- [x] Values are restored from the previous session on page load
- [x] Image widget shows "Loaded" hint and preview after upload
- [x] "Restore Default Values" resets to creator defaults and clears stored values
- [x] Uploaded image asset is stored in `dashboard-assets/`, not permanently in ComfyUI `input/`

### User layout
- [x] Layout override persists in `user-state/{workflow_id}.json` after drag-reposition
- [x] "Reset Layout" removes overrides and reverts canvas to creator positions

### Tests
- [x] Backend tests: `test_user_state.py` (7), `test_dashboard_assets.py` (9), adapter staging (4) — all passing
- [x] Frontend tests: `gridLayout.test.ts` (13), `useAppPreferences.test.ts` (7), `useWorkflowUserState.test.ts` (9) — all passing
- [x] No existing tests regressed

---

## 17. Canvas Interaction Model (source of truth)

### 17.1 Visual parity between builder and run canvas

The run canvas (Interactive Grid Layout view) must be visually indistinguishable from the Dashboard Layout Builder canvas:

- Same canvas background, grid feel, widget card shape, border, shadow, spacing, and typography.
- Same 32-column grid, same 32 px row height, same gap.
- Widget positions come directly from saved `layout.x/y/w/h` in `dashboard.json`.

Do not redesign colors, button shapes, widget shapes, or any other element of the Noofy design system. Do not make the dashboard resemble a node editor.

### 17.2 Normal viewing mode — layout locked

The grid is fully locked. The user can only interact with widget values:

- No drag handles, resize handles, selection outlines, or preset chips are rendered.
- Widget value inputs (sliders, text fields, image pickers, etc.) remain fully interactive.

### 17.3 Edit Dashboard Layout mode

Entered via the **Edit Dashboard Layout** item in the workflow customization dropdown.

- Widgets can be repositioned using a drag handle.
- Widgets can be resized using grid-snapped edge/corner handles. No free-pixel dragging.
- Each widget's `min_w` and `min_h` are enforced; a widget cannot shrink below its minimum.
- Widget value inputs are disabled (read-only) during edit mode.
- On release of any drag (move or resize), the new grid position is saved immediately via `PUT /workflows/{id}/user-state`.
- A **Done** button exits back to normal viewing mode.

### 17.4 Canvas action group (top-right)

The canvas top-right holds three controls, left to right:

```
[Run Workflow]  [Cancel Run]  [customization-icon ▼]
```

- **Run Workflow** / **Cancel Run** — trigger and cancel the current job.
- **Customization button** — a square button using a controls/sliders icon (not the global app settings gear). Opens a dropdown with all workflow-scoped actions.

The app global settings button must not gain any workflow-specific items.

### 17.5 Workflow customization dropdown items

| Item | Condition | Action |
|---|---|---|
| Restore Default Values | always | Resets all input widget values to creator defaults; calls `DELETE /workflows/{id}/user-state/values` |
| Edit Dashboard Layout | always | Enters layout edit mode (§17.3) |
| Edit Widgets | always | Returns user to the widget configuration step |
| Reset Layout | only when `layout_overrides` non-empty | Calls `DELETE /workflows/{id}/user-state/layout`; reverts canvas to creator positions |
| Export as JSON | always | Exports workflow graph as ComfyUI-compatible JSON |
| Export as Noofy | always | Exports workflow as `.noofy` package archive |

---

## 18. What Changed from Earlier Versions of This Document

| Old assumption | Status | Replacement |
|---|---|---|
| Sticky canvas toolbar with standalone Restore / Edit Dashboard / Reset Layout buttons | **Removed** | All three are now items inside the workflow customization dropdown (§17.5) |
| Sticky run footer holding Run / Cancel / progress bar | **Removed** | Run Workflow and Cancel Run are in the canvas action group (§17.4); progress is inline in the canvas area |
| Resize handles in run view are a non-goal | **Replaced** | Grid-snapped edge/handle resize is allowed in Edit Dashboard Layout mode; free-pixel resize remains a non-goal |
| Size-preset chip buttons on widget cards | **Removed** | No preset chips in the run view; presets are internal schema minimums only |
| Run view and builder canvas may look different | **Replaced** | Run canvas must be visually identical to the builder canvas |
| Normal mode: full layout lock was not enforced | **Replaced** | Normal mode renders no layout affordances at all |
| Workflow actions accessible from the app global settings button | **Replaced** | A per-canvas customization button at top-right holds all workflow-scoped actions; global settings is unaffected |
| `Edit Dashboard / Edit Variables` two-state toggle | **Removed** | Edit Dashboard Layout is a dropdown menu item; Done exits edit mode |
