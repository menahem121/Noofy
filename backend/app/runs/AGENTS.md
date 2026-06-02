# backend/app/runs — Agent Map

Workflow execution as a user-facing app use case: queueing, aliases, job lifecycle, cancellation, output fetch, result handling, and progress events.

## What this package owns

| File | Owns |
|------|------|
| `queue_service.py` | `WorkflowRunQueueService` — UUID workflow-run records, queue ID aliases, cancellation state, bounded terminal retention |
| `lifecycle_service.py` | `RunLifecycleService` — backend-owned workflow queue draining, dispatch loop guards, submitted-job watchers |
| `orchestrator.py` | `RunOrchestrator` — validate and submit workflow runs through reservation-backed handoff |
| `media_staging.py` | Resolve persisted dashboard media references and stage files into the active runner input workspace at submission |
| `job_service.py` | `RunJobService` — thin job query layer: progress, cancel, fetch_output, job_logs |
| `result_service.py` | `RunResultService` — finalize-once result retrieval, progress SSE, gallery capture after completion, run-history recording |

## What it must NOT own

- Runner process lifecycle (belongs in `runtime/`)
- Memory admission, cleanup, release polling, or learning policy (belongs in `runtime/memory/`)
- Workflow package loading or validation (belongs in `workflows/`)
- Durable gallery storage (belongs in `gallery/`; result service only coordinates capture after a run completes)

## Current extraction status

- ✅ `RunJobService` — thin job query methods (get_progress, cancel_job, fetch_output, list_job_logs) extracted from `EngineService`. `EngineService` delegates; `deps.py` exposes `RunJobServiceDep`; run routes use it for progress, cancel, logs, and output viewing.
- ✅ `RunResultService` — result retrieval, progress SSE, gallery capture coordination, run-history recording. `deps.py` exposes `RunResultServiceDep`; run routes use it for result and event endpoints. Memory retry behavior is injected from `runtime/memory` so terminal finalization stays in `runs/` without moving admission policy.
- ✅ `RunOrchestrator` — `validate_workflow` and `run_workflow` extracted from `EngineService`; workflow validate/run routes use it through `RunOrchestratorDep`.
- ✅ `WorkflowRunQueueService` and `RunLifecycleService` own workflow queue records, aliases, loop guards, automatic dispatch wakes, and submitted-job watchers.
- ✅ `RunResultService` guards terminal side effects with per-job async locks and cached terminal outcomes.
- `EngineService` wires callbacks and keeps migration proxies only.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests/test_api_runtime.py
backend/.venv/bin/python -m pytest backend/tests/test_api_auth.py
backend/.venv/bin/python -m pytest backend/tests/test_runner_supervisor.py -k get_result
backend/.venv/bin/python -m pytest backend/tests/test_run_lifecycle_service.py
```
