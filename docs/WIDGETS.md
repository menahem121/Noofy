# Dashboard Widgets

Status: current architecture/reference.

Dashboard widgets are the app-owned controls shown on the Noofy workflow dashboard. The frontend renders them from `dashboard.json`, and workflow execution still goes through the backend API and the active `EngineAdapter`.

Widget layout uses the shared 32-column dashboard grid. The minimum sizes below are stored as grid cells and are also the default size for newly placed widgets. In code, these map to `w`, `h`, `minW`, and `minH` from `frontend/src/lib/widgetSizes.ts`.

Widget groups are visual containers around existing controls. A group owns one dashboard block layout and an ordered list of child control IDs; child controls keep their own bindings, defaults, validation, values, and widget configuration.

## Existing Widgets

| Widget type | Minimum grid columns (`w`) | Minimum grid rows (`h`) | Use case |
|---|---:|---:|---|
| `toggle` | 6 | 4 | On/off controls. |
| `int_field` | 6 | 4 | Compact integer or numeric text entry. |
| `string_field` | 6 | 4 | Compact single-line text entry. |
| `seed_widget` | 6 | 4 | Seed or variation ID controls. |
| `note` | 6 | 4 | Read-only creator notes, instructions, explanations, or warnings. |
| `textarea` | 8 | 6 | Multi-line prompt or text entry. |
| `select` | 8 | 6 | Dropdown selection controls. |
| `lora_loader` | 8 | 6 | LoRA model selection and related options. |
| `slider` | 10 | 4 | Numeric tuning controls that benefit from visual adjustment. |
| `load_image` | 10 | 10 | Image upload/input controls. |
| `load_image_mask` | 10 | 10 | Image upload controls with mask drawing enabled. |
| `load_audio` | 10 | 4 | Audio upload/input controls. |
| `load_video` | 14 | 12 | Video upload/input controls. |
| `display_mask` | 10 | 10 | Mask display or preview widgets. |
| `display_audio` | 12 | 6 | Audio output playback widgets. |
| `display_video` | 16 | 14 | Video output playback widgets. |
| `display_image` | 14 | 14 | Large image output preview widgets. |
| `result_image` | 14 | 14 | Result image widgets bound to workflow outputs. |

## Notes

- `select` is the dashboard schema type used for dropdowns.
- `note` stores multi-line creator guidance directly in the dashboard control and does not require an executable workflow binding.
- Creators can add dashboard-only notes directly in Dashboard Builder. Detected ComfyUI `Note` nodes are preselected as notes during analysis.
- Use `select` for ComfyUI node inputs that expose a fixed list of choices, such as `KSampler.sampler_name` and `KSampler.scheduler`.
- When ComfyUI `/object_info` is available through the backend, Noofy reads the valid choices from the node definition and copies them into the dashboard input validation as `options`.
- If automatic option extraction is unavailable or incomplete for a node, the Dashboard Builder still lets the creator/importer choose the Dropdown widget and enter choices manually, one per line.
- `display_image` is the frontend builder/run widget type for image outputs.
- `display_audio` is the frontend builder/run widget type for audio outputs.
- `display_video` is the frontend builder/run widget type for video outputs.
- `result_image` is also accepted by the package schema for output image controls.
- `load_image_mask` follows the same minimum as `load_image` because it needs room for upload and mask affordances.
- Audio-specific media widgets are limited to `load_audio` and `display_audio`; duration, speaker, voice, lyric, and similar simple parameters should use generic controls such as `int_field`, `slider`, `select`, `textarea`, or `toggle`.
- Video-specific media widgets are limited to `load_video` and `display_video`; FPS, frame count, duration, resolution, strength, and similar simple parameters should use generic controls such as `int_field`, `slider`, `select`, or `toggle`.
