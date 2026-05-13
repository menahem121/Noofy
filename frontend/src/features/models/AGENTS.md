# frontend/src/features/models — Agent Map

Model management pages and components: the model inventory list, model import panel, and download progress tracking.

## What this feature owns

| File | Owns |
|------|------|
| `ModelsPage.tsx` | Main models page — list of known models, import entry point |
| `ModelRows.tsx` | Model list row components |
| `ModelImportPanel.tsx` | Import model panel (URL/file/HuggingFace import UI) |
| `useModelInventory.ts` | Hook for fetching and refreshing the model inventory |
| `useModelDownloadJob.ts` | Hook for polling/subscribing to a model download job |
| `modelDownloads.ts` | Download job helpers and status mapping |
| `modelUi.ts` | Model display helpers (labels, icons, formatting) |
| `modelsContent.ts` | Static content strings for the models page |

## What it must NOT own

- API call logic — all backend calls go through `lib/api/noofyApi.ts`
- Workflow model requirement checks — those belong in `features/workflows/`
- Settings for model folders — that belongs in `features/settings/`

## Tests

```bash
cd frontend && npm test -- ModelsPage
```

Test: `ModelsPage.test.tsx` (colocated).
