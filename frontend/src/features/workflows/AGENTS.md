# frontend/src/features/workflows — Agent Map

Workflow-related pages and components: the library list, the per-workflow run page with dashboard input controls, and the canvas view.

## What this feature owns

| File | Owns |
|------|------|
| `WorkflowsPage.tsx` | Workflow library list page — browse installed workflows |
| `WorkflowRunPage.tsx` | Per-workflow run page — shows dashboard inputs, triggers runs |
| `CanvasDashboardView.tsx` | Canvas-mode dashboard view (full-screen run UI) |
| `DashboardInputControl.tsx` | Individual dashboard input widget renderer |
| `dashboardEditing.ts` | Dashboard editing helpers (input binding, schema mutation) |

## What it must NOT own

- API call logic — all backend calls go through `lib/api/noofyApi.ts`
- Job progress polling infrastructure — that hook lives in `lib/`
- Model inventory UI — that belongs in `features/models/`
- Dashboard builder/schema authoring pages — those live in `features/dashboard-builder/`
- Canvas presentation logic — `features/dashboard-canvas/` owns the pure presentation layer

## Migration target (Phase 6)

Large pages should be split into page shell + hooks + small presentational components:
- `WorkflowRunPage.tsx` is a candidate for extracting a `useWorkflowRun` hook and smaller input components.

## Tests

```bash
cd frontend && npm test -- WorkflowsPage WorkflowRunPage DashboardInputControl
```

Tests are colocated: `WorkflowsPage.test.tsx`, `WorkflowRunPage.test.tsx`, `DashboardInputControl.test.tsx`.
