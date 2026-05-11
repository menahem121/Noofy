# Feedback, Testing, and Monitoring

The project should be easy for users, developers, and agents to understand when something works, fails, or needs attention.

## Feedback Architecture

The backend exposes structured diagnostics through the app-owned API.

Current feedback surfaces:

- Frontend runtime status provider: session-scoped last-known backend/engine state for the top bar and run gating. Background refreshes are non-blocking.
- `GET /api/health`: backend status, ComfyUI reachability, workflow health, and latest error.
- `GET /api/runtime`: lightweight backend/engine status used for frontend refreshes.
- `GET /api/logs`: recent global diagnostic events.
- `GET /api/jobs/{job_id}/logs`: diagnostics for a single workflow job.
- `GET /api/jobs/{job_id}/progress`: latest normalized progress.
- `GET /api/jobs/{job_id}/events`: frontend-ready progress/result stream.
- `GET /api/runners`: runner lifecycle state, including warm/queued/memory-related state.
- Workflow runner lease endpoints: frontend reports open/close intent; backend decides warm retention.
- `GET /api/workflows/{id}/model-summary`: identity-verified required-model availability for an installed workflow.
- `GET /api/workflows/import/{session}/download-models/{job_id}`: live progress for the staged-import model download job (current model, bytes, percent, speed, per-model status, refreshed summary).
- `GET /api/settings/model-folders` and `GET /api/settings/apis`: model folder and external provider key status (only `configured`/`last_four`, never full keys).

Diagnostics should be structured events, not ad hoc print output. Events should include source, level, message, job id when relevant, workflow id when relevant, and useful details.

Runtime, install, smoke-test, Memory Governor, model, update, runner, and workflow subsystems should emit diagnostics through an injected diagnostics sink. They should not construct their own private diagnostic stores. The app composition root owns the shared in-memory `LogStore` and passes it to subsystems so events remain visible through `/api/logs`, `/api/health`, job logs, and other API/UI troubleshooting surfaces.

Emit-only subsystems should depend on the small diagnostics sink contract instead of the concrete store. API-facing code that needs to read or filter events may use the concrete `LogStore` or a reader contract. This keeps runtime code decoupled from storage/exposure details while preserving one shared event stream for the UI.

Frontend availability feedback should be non-blocking after startup. Keep the last known "Ready" state visible while silent health refreshes run. If a background refresh fails once, store the error but do not downgrade the visible status. Downgrade only after repeated silent failures or a real user action failure, such as Run failing because the backend is unreachable.

## What To Log

Add diagnostic events for important state transitions:

- validation success or failure
- missing models
- workflow submission
- job queued, completed, failed, or canceled
- ComfyUI HTTP errors
- ComfyUI WebSocket disconnects or execution errors
- managed sidecar start, stop, crash, and recovery events
- Memory Governor estimates, signal confidence, local learning updates, co-residence admits/denies, runner evictions, memory-release waits, retry-after-cleanup attempts, and blocked-by-memory outcomes
- staged import lifecycle: preview created, session expired, commit, cancel
- model download job lifecycle: provider authentication required, rate limited, hash mismatch, disk-space failure, cleanup of interrupted transactions, completion
- model folder and API key setting changes (provider name only; never the key itself)

Avoid logging secrets, full prompts by default, API keys, local private paths beyond what is needed for debugging, or large binary payloads. Provider URLs that carry credentials (for example `?token=...`) must be redacted before reaching diagnostics or UI messages.

## Automated Tests

Every meaningful backend behavior should have focused tests.

Current test areas:

- workflow package loading and validation
- model validation through the active `EngineAdapter`
- model availability summary, provider resolver (mocked), download transactions, and startup cleanup
- staged `.noofy` import preview, session TTL, background download job, cancel/commit endpoints
- API key endpoints with mocked OS credential stores (full keys never appear in responses or fixtures)
- ComfyUI result parsing and backend-owned output media URLs
- ComfyUI WebSocket progress/error/result parsing
- diagnostic log filtering and latest error tracking

Default tests must use mocked/offline provider responses. No default test may call live Hugging Face or Civitai APIs.

When changing behavior, add or update tests that prove:

- the success path works
- the likely failure path is reported clearly
- frontend status refreshes do not hide cached workflows or disable ready workflows without a known blocker
- diagnostics/logs are emitted when useful
- frontend-facing response shapes remain stable
- Memory Governor decisions remain deterministic for the same fake memory snapshots, local observation history, and runner states
- runtime diagnostic emitters require an explicit injected sink rather than creating fallback stores

## Monitoring Direction

For now, monitoring is backed by the shared in-memory `LogStore` and exposed through backend APIs. This is enough for development and early UI work, but the storage implementation should remain behind the diagnostics sink/reader boundary so later persistence or streaming can be added without coupling runtime subsystems to the store.

Later product builds may add:

- persisted logs
- crash reports
- startup timing metrics
- model download/install progress
- local memory observation history and confidence changes over repeated runs
- Memory Governor decision traces
- user-visible troubleshooting bundles
- optional privacy-respecting telemetry if the project ever chooses to support it

The default product should remain local-first and privacy-focused.
