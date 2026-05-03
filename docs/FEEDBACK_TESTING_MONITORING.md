# Feedback, Testing, and Monitoring

The project should be easy for users, developers, and agents to understand when something works, fails, or needs attention.

## Feedback Architecture

The backend exposes structured diagnostics through the app-owned API.

Current feedback surfaces:

- `GET /api/health`: backend status, ComfyUI reachability, workflow health, and latest error.
- `GET /api/logs`: recent global diagnostic events.
- `GET /api/jobs/{job_id}/logs`: diagnostics for a single workflow job.
- `GET /api/jobs/{job_id}/progress`: latest normalized progress.
- `GET /api/jobs/{job_id}/events`: frontend-ready progress/result stream.
- `GET /api/runners`: runner lifecycle state, including warm/queued/memory-related state.
- Workflow runner lease endpoints: frontend reports open/close intent; backend decides warm retention.

Diagnostics should be structured events, not ad hoc print output. Events should include source, level, message, job id when relevant, workflow id when relevant, and useful details.

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

Avoid logging secrets, full prompts by default, API keys, local private paths beyond what is needed for debugging, or large binary payloads.

## Automated Tests

Every meaningful backend behavior should have focused tests.

Current test areas:

- workflow package loading and validation
- model validation through the active `EngineAdapter`
- ComfyUI result parsing and view URL creation
- ComfyUI WebSocket progress/error/result parsing
- diagnostic log filtering and latest error tracking

When changing behavior, add or update tests that prove:

- the success path works
- the likely failure path is reported clearly
- diagnostics/logs are emitted when useful
- frontend-facing response shapes remain stable
- Memory Governor decisions remain deterministic for the same fake memory snapshots, local observation history, and runner states

## Monitoring Direction

For now, monitoring is in-memory and API-based. This is enough for development and early UI work.

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
