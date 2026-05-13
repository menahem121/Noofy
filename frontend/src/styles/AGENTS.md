# frontend/src/styles — Agent Map

CSS split by ownership. `global.css` is an `@import` manifest — edit the domain file, not the manifest.

## Files

| File | Owns |
|------|------|
| `tokens.css` | `:root` CSS custom properties (design tokens) |
| `base.css` | `html`/`body` resets and base typography |
| `layout.css` | `.app-shell`, `.sidebar` |
| `components.css` | Sidebar nav, engine card, workflow card, settings grid/panel, engine-status-card, model-folder sections, shared UI components |
| `models.css` | Models page |
| `workflows.css` | Workflows page |
| `gallery.css` | Gallery page + gallery responsive |
| `history.css` | History page |
| `dashboard-builder.css` | Dashboard builder |
| `canvas.css` | Canvas dashboard view, layout canvas widgets, resize handles |
| `global.css` | `@import` manifest only — do not add selectors here |

## Rules

- Add new selectors to the domain file that owns the component, not `global.css`.
- Keep ComfyUI-derived visual treatments out of all files — these are Noofy-native styles.
- Avoid visual redesign during architecture migration.
