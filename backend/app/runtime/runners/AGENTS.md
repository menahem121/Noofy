# runtime/runners — Agent Map

Runner processes: supervision, launch, coordination, and memory probing.

## What this package owns

| File | Owns |
|------|------|
| `supervisor.py` | `RunnerSupervisor` — runner registry, job routing, leases, tokenized submission/eviction/startup reservations, atomic runner transitions |
| `lifecycle_service.py` | `WorkflowRunnerLifecycleService` — workflow runner leases, durable queued runner-start cancellation/handoff, stop/start |
| `runner_process.py` | `RunnerProcess` — isolated runner process launch and lifecycle |
| `runner_coordinator.py` | `RunnerProcessCoordinator` — multi-runner stop/start coordination |
| `runner_memory_probe.py` | Memory telemetry reading from runner processes |

## What it must NOT own

- ComfyUI sidecar management (belongs in `comfyui/`)
- Memory governor decisions (belongs in `memory/`)
- Workflow execution orchestration (belongs in `runs/`)
- Workflow-run queueing or result finalization (belongs in `runs/`)
