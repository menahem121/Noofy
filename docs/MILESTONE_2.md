# Milestone 2

Milestone 2 delivers dashboard authoring and run-time consumption for imported workflows.

## Goal

A user imports any `.noofy` workflow archive, authors a dashboard by picking widget controls for the graph inputs, saves it, and then opens the workflow run page to generate output — without touching ComfyUI directly.

The bundled `text_to_image_v0` starter and any user-imported workflow must follow the same authoring and run path.

## Required Behavior

- Import a `.noofy` archive into the app-managed workflow store without modifying the original file.
- If the archive already contains a valid configured `dashboard.json`, route directly to the run page.
- If `dashboard.json` is missing, empty, `not_configured`, or invalid, open the Dashboard Builder.
- Expose the workflow graph's bindable node inputs through `GET /api/workflows/{id}/bindable-inputs` so the builder can suggest widget types without needing ComfyUI running.
- Expose unresolved runtime inputs (e.g. `LoadImage` creator-local references) through `GET /api/workflows/{id}/unresolved-inputs` so the builder can surface them as required bindings.
- Save a configured dashboard via `PUT /api/workflows/{id}/dashboard` which writes only `dashboard.json` inside the internal package copy. `package.json` and `comfyui_graph.json` are never modified by dashboard authoring.
- `dashboard.json` is the sole location for dashboard metadata: `inputs[]`, `outputs[]`, `sections[]`, and `status`. It is never embedded in the graph or in `package.json`.
- On successful save, transition the workflow status to `imported` (or `ready` if previously prepared) so the run page becomes accessible.
- The run page renders widgets from `dashboard.sections[].controls[]` using a type-keyed render registry. Defaults come from each control's referenced `WorkflowInput.default`.
- On Run, build `inputs: { [WorkflowInput.id]: value }` and submit through the existing `runWorkflow` path (`_apply_input_bindings` is unchanged).
- Image upload for `load_image` and `load_image_mask` widgets goes through `POST /api/workflows/{id}/uploads/image` (proxy to ComfyUI). The frontend never calls ComfyUI directly.
- The result image renders inside `result_image` controls once the job completes.
- "Share / Save as .noofy" packs the internal copy into a portable archive including the latest `dashboard.json`. The original imported file is never touched.
- Exported packages strip any original trust signatures and are treated as local/user-authored.

## Dashboard Storage Rules

- `dashboard.json` lives as a top-level file in every `.noofy` archive and in every workflow-store package directory.
- It is never embedded in `comfyui_graph.json` or `package.json`.
- Importing a `.noofy` archive creates an internal editable copy in the workflow store. The original file on disk is not modified.
- `PUT /dashboard` writes only the internal copy's `dashboard.json` (atomic rename). All other package files are byte-for-byte unchanged.

## First Workflow Controls

The dashboard builder must support configuring at minimum:

- prompt (textarea or string_field)
- seed (seed_widget)
- width and height (int_field or slider)
- image input (load_image)
- result image preview (result_image / display_image)

Run and Cancel buttons are implicit on the run page and are not dashboard control records.

## Out of Scope

- Marketplace or public workflow sharing.
- Re-signing user-authored dashboards as Noofy Verified.
- Multi-image outputs.
- Draft-vs-finalize state distinction.
- Custom widget types beyond the 11 declared (`slider`, `int_field`, `string_field`, `textarea`, `toggle`, `load_image`, `display_image`, `seed_widget`, `lora_loader`, `select`) plus `result_image`.
- In-place update of the original imported `.noofy` file.
- Raw `workflow_api.json` import (stretch).

## Acceptance Check

A developer can:

1. Import a `.noofy` archive — the original file is not modified.
2. Open the Dashboard Builder, see the workflow's graph nodes and bindable inputs without ComfyUI running.
3. Drop widgets on the grid and save — `dashboard.json` is written; `package.json` bytes are unchanged.
4. Open the run page — controls render from the saved schema.
5. Set inputs, upload an image if required, click Run, and see the result image in the `result_image` control.
6. Click "Share / Save as .noofy" and receive a portable archive with a separate `dashboard.json` and no trust signature from the original.

The bundled `text_to_image_v0` workflow follows the same schema: its dashboard lives in `dashboard.json` (not inline in `package.json`) with `status: configured`, and the run page renders it schema-driven.
