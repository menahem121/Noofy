# frontend/src/lib/api — Agent Map

Domain-split Noofy backend API layer. All modules are pure request/type — no UI state, no hooks.

## Files

| File | Owns |
|------|------|
| `client.ts` | HTTP primitives: `getApiBaseUrl`, `getApiToken`, `apiHeaders`, `resolveBackendUrl`, `createJobEventsUrl`, `getJson`/`postJson`/`putJson`/`deleteJson`/`postBytes`, `Window.__NOOFY_RUNTIME_CONFIG__` type |
| `jobs.ts` | `JobStatus`, `EngineJob`, `JobProgress`, `JobResult`, `MemoryStatus`, `DiagnosticEvent`, `DiagnosticLogResponse`, `isEngineJob`, job API functions |
| `engine.ts` | `RuntimeStatus`, `ComfyUIVramMode`, `ComfyUILaunchSettings`, `ComfyUIVersionsResponse`, `ComfyUIUpdateStatus`, `MachineResourceSnapshot`, `BackendHealthReport`, engine/ComfyUI API functions |
| `settings.ts` | `ApiKeyProviderId`, `ApiKeySettingsResponse`, `ModelFolderSettings`, settings API functions |
| `workflows.ts` | Workflow types, dashboard authoring types, user state types, dashboard asset types + all workflow/import/authoring/user-state/asset API functions |
| `models.ts` | Model inventory types, `ModelDownloadJobStatus`, model API functions |
| `gallery.ts` | `GalleryImage`, `GalleryResponse`, gallery API functions |
| `noofyApi.ts` | Barrel re-export — `export * from` each domain module. All existing imports remain valid. |

## What it must NOT own

- ComfyUI API calls — forbidden from `frontend/src`
- UI state or React hooks — pure request/type layer only
- Business logic beyond request shaping

## Cross-module imports

`workflows.ts` imports `EngineJob` from `./jobs` (for `WorkflowRunResponse = EngineJob | WorkflowValidationResult`).
`models.ts` imports `ImportModelDownloadProgressItem` from `./workflows` (shared download progress item type).
All other modules import from `./client` only.

## Tests

```bash
cd frontend && npm test -- noofyApi
```
