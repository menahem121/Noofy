# frontend/src — Agent Map

Vite + React + TypeScript desktop frontend for the Noofy app. Runs inside a Tauri webview.

## Core rule

**Frontend must call `/api/*` on the Noofy backend only. It must never call ComfyUI endpoints directly.**

Boundary check: `npm run check:no-comfyui-calls` (see `scripts/check-no-comfyui-calls.mjs`).

## Structure

```
src/
  App.tsx              Root component, router setup
  main.tsx             Vite entry point

  features/            One folder per user-facing feature
    app/               App layout and runtime status provider
    home/              Home page and workflow library
    workflows/         Workflow library page, run page, canvas view, dashboard
    models/            Models page, import panel, download tracking
    settings/          Engine settings page
    gallery/           Gallery page
    history/           History page (placeholder)
    dashboard-builder/ Dashboard schema builder
    dashboard-canvas/  Canvas run view presentation

  lib/
    api/noofyApi.ts    All backend API types and request functions — Phase 6 split target
    gridLayout.ts      Grid layout helpers
    widgetSizes.ts     Widget size constants
    useAppPreferences.ts  App preference hooks
    useWorkflowUserState.ts  Per-workflow user state hook
    folderDialogs.ts   OS folder-picker dialogs (Tauri)
    openExternalUrl.ts Open external URLs in browser (Tauri)

  styles/
    global.css         All CSS — Phase 7 split target

  test/
    setup.ts           Vitest global setup
```

## What must NOT happen

- Do not call ComfyUI API endpoints (e.g. `localhost:8188`, `/comfyui/`, websocket to ComfyUI) from frontend source.
- Do not bypass the backend API to access runtime internals.
- Do not add product logic to `App.tsx` or `main.tsx` — keep those as thin shells.

## API boundary

All backend calls go through `lib/api/noofyApi.ts`. It resolves the backend URL from runtime config and attaches the auth token.

**Phase 6** will split this file into domain modules under `api/`:
```
api/
  client.ts       Base HTTP client (auth, URL resolution, upload)
  runtime.ts      Runtime/health/diagnostics
  workflows.ts    Workflow library, import, run, authoring, export
  jobs.ts         Job progress, logs, events, result, cancel
  models.ts       Model inventory, downloads, imports, tags
  settings.ts     API key settings, model folder settings
  gallery.ts      Mixed-media Gallery CRUD and render-time media URLs
  assets.ts       Dashboard asset management
  types/          Shared API type definitions
```

## Tests

Tests are colocated with their source files (`*.test.ts`, `*.test.tsx`). Run:

```bash
cd frontend && npm test
```

## Search hygiene

Ignore when searching: `dist/`, `node_modules/`, `src-tauri/target/`.
