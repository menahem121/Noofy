# Dashboard Widgets

Status: current architecture/reference.

Dashboard widgets are the app-owned controls shown on the Noofy workflow dashboard. The frontend renders them from `dashboard.json`, and workflow execution still goes through the backend API and the active `EngineAdapter`.

Widget layout uses the shared 32-column dashboard grid. The minimum sizes below are stored as grid cells and are also the default size for newly placed widgets. In code, these map to `w`, `h`, `minW`, and `minH` from `frontend/src/lib/widgetSizes.ts`.

## Existing Widgets

| Widget type | Minimum grid columns (`w`) | Minimum grid rows (`h`) | Use case |
|---|---:|---:|---|
| `toggle` | 6 | 4 | On/off controls. |
| `int_field` | 6 | 4 | Compact integer or numeric text entry. |
| `string_field` | 6 | 4 | Compact single-line text entry. |
| `seed_widget` | 6 | 4 | Seed or variation ID controls. |
| `textarea` | 8 | 6 | Multi-line prompt or text entry. |
| `select` | 8 | 6 | Dropdown selection controls. |
| `lora_loader` | 8 | 6 | LoRA model selection and related options. |
| `slider` | 10 | 4 | Numeric tuning controls that benefit from visual adjustment. |
| `load_image` | 10 | 10 | Image upload/input controls. |
| `load_image_mask` | 10 | 10 | Image upload controls with mask drawing enabled. |
| `display_mask` | 10 | 10 | Mask display or preview widgets. |
| `display_image` | 14 | 14 | Large image output preview widgets. |
| `result_image` | 14 | 14 | Result image widgets bound to workflow outputs. |

## Notes

- `select` is the dashboard schema type used for dropdowns.
- `display_image` is the frontend builder/run widget type for image outputs.
- `result_image` is also accepted by the package schema for output image controls.
- `load_image_mask` follows the same minimum as `load_image` because it needs room for upload and mask affordances.
