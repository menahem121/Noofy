# Milestone 2 Completion Plan

## Goal
Finish the remaining work needed to close the MILESTONE_2_DASHBOARD_CANVAS.md plan. The core canvas, user-state, asset-store, and classic/canvas preference work already exists; this plan focuses on gaps, hardening, and acceptance coverage.

## Remaining Tasks

### Output Widgets
- [x] Add reliable backend discovery for workflow output nodes.
- [x] Update dashboard builder so imported workflows can add real `display_image` widgets from discovered outputs.
- [x] Ensure saved output widgets always write matching `dashboard.outputs[]` records.
- [x] Implement full per-node output display.

### Widget Rendering
- [x] Reduce canvas/classic widget drift by extracting a shared widget renderer or shared field-control registry.
- [x] Keep canvas-specific shell/layout separate, but reuse input behavior where practical.

### Assets
- [ ] Show uploaded asset original filename in image widgets.
- [ ] Replace deprecated `imghdr` validation with non-deprecated image verification.
- [ ] Add asset serve/auth tests.
- [ ] Confirm staged ComfyUI files are cleaned up for completed, failed, and canceled jobs.

### Builder Drafts
- [ ] Make draft persistence consistent across both builder steps.
- [ ] Ensure failed backend saves never navigate to the run page.

### Tests
- [ ] Add `WorkflowRunPage` tests for canvas grid positions.
- [ ] Add canvas output widget rendering test.
- [ ] Add classic mode branch test.
- [ ] Add toolbar button visibility tests.
- [ ] Add edit-mode disables-inputs test.
- [ ] Add `EngineSettingsPage` preference toggle test.

### Progress Notes
- Done: core canvas mode, classic mode, settings preference, user values, layout overrides, asset upload/store/staging, main save/regression fixes, output-node discovery, imported workflow output-widget creation, output widget save payload hardening, full per-node canvas output display, and shared classic/canvas input-control rendering.
- Still pending: multi-output limitation notice, asset metadata/validation polish, builder draft consistency, missing acceptance tests.
- Newly discovered issues: none yet after the latest fixes.

## Implementation Order
1. Output discovery and builder output-widget creation.
2. Output-widget save/render hardening and multi-output limitation notice.
3. Shared widget-rendering cleanup between classic and canvas.
4. Asset metadata display and image validation replacement.
5. Builder draft consistency cleanup.
6. Add missing frontend/backend tests.
7. Run full validation and update progress notes.

## Validation
Milestone 2 is complete only when:

- `make test` passes.
- `npm run build` passes.
- `git diff --check` passes.
- Canvas opens by default for configured dashboards.
- Classic mode still renders the two-panel view.
- Imported workflows can create and save output image widgets.
- Output image widgets render results in their canvas cells.
- User values and layout overrides persist and reset correctly.
- Image uploads are stored in Noofy assets, staged only at run time, and preview with filename.
