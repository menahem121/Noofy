# backend/app/runs — Agent Map

Workflow execution as a user-facing app use case: job query, cancellation, output fetch, result handling, progress events, and (future) run orchestration.

## What this package owns

| File | Owns |
|------|------|
| `orchestrator.py` | `RunOrchestrator` — validate and submit workflow runs, register jobs, queue memory-blocked submissions |
| `job_service.py` | `RunJobService` — thin job query layer: progress, cancel, fetch_output, job_logs |
| `result_service.py` | `RunResultService` — result retrieval, progress SSE, gallery capture after completion, run-history recording |

## What it must NOT own

- Runner process lifecycle (belongs in `runtime/`)
- Memory governor logic (belongs in `runtime/memory_governor.py` until extracted)
- Workflow package loading or validation (belongs in `workflows/`)
- Durable gallery storage (belongs in `gallery/`; result service only coordinates capture after a run completes)

## Current extraction status (Phase 3)

- ✅ `RunJobService` — thin job query methods (get_progress, cancel_job, fetch_output, list_job_logs) extracted from `EngineService`. `EngineService` delegates; `deps.py` exposes `RunJobServiceDep`; run routes use it for progress, cancel, logs, and output viewing.
- ✅ `RunResultService` — result retrieval, progress SSE, gallery capture coordination, run-history recording. `deps.py` exposes `RunResultServiceDep`; run routes use it for result and event endpoints. Memory retry behavior remains injected from `EngineService` so the memory-governor state is not split across packages yet.
- ✅ `RunOrchestrator` — `validate_workflow` and `run_workflow` extracted from `EngineService`; workflow validate/run routes use it through `RunOrchestratorDep`.
- Pending: memory admission/retry-after-cleanup internals still live in `EngineService` and are injected into `RunOrchestrator`.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests/test_api_runtime.py
backend/.venv/bin/python -m pytest backend/tests/test_api_auth.py
backend/.venv/bin/python -m pytest backend/tests/test_runner_supervisor.py -k get_result
```
