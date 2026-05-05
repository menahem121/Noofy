# Engine Contract

The app owns the engine contract. UI code should depend on this contract, not on ComfyUI routes or payloads.

## Core Operations

- `runWorkflow(workflowId, inputs, options)`: validate and start a workflow job.
- `getProgress(jobId)`: return current job progress and status.
- `streamProgress(jobId)`: stream frontend-ready progress/result events.
- `cancelJob(jobId)`: stop a running or queued job.
- `getResult(jobId)`: return final outputs, errors, and generated files.
- `listAvailableModels()`: report models available to the active engine.
- `validateWorkflow(workflowId)`: validate package structure, bindings, and model availability.
- `listLogs(level?, limit?)`: return recent backend and engine diagnostic events.
- `listJobLogs(jobId, level?, limit?)`: return diagnostic events for a specific job.
- `listRunners()`: report runner lifecycle and memory-governor state through the backend API.
- `openWorkflowRunnerLease(workflowId)` / `closeWorkflowRunnerLease(workflowId, leaseId)`: report workflow view open/close intent so the backend can decide warm retention.

## Adapter Boundary

`EngineAdapter` is the backend abstraction for running workflows.

The first implementation is `ComfyUIEngineAdapter`, which translates app operations into ComfyUI HTTP and WebSocket calls.

The frontend must not know whether a workflow is running through ComfyUI, a future platform-native engine, or another adapter.

For v1, `ComfyUIEngineAdapter` should normally talk to an app-managed ComfyUI sidecar. Connecting to an externally launched ComfyUI instance is a development convenience only.

## Job Lifecycle

```text
Load workflow package
  -> validate package and dashboard bindings
  -> ask RunnerSupervisor / Memory Governor for the runner decision
  -> check required models against the selected runner's EngineAdapter
  -> queue, reuse, start, evict, or wait for memory according to backend policy
  -> submit graph to selected EngineAdapter
  -> stream progress
  -> record diagnostics and errors
  -> collect outputs
  -> return result to frontend
```

## ComfyUI v1 Mapping

For the first adapter:

- Submit workflow graph through ComfyUI `/prompt`.
- Track progress through ComfyUI `/ws`.
- Normalize WebSocket progress into app progress fields: status, current node, value, max, and message.

## Diagnostics

The backend should record app-readable diagnostic events for:

- workflow validation success/failure
- workflow submission and queueing
- missing models
- ComfyUI HTTP and WebSocket failures
- job completion, cancellation, and execution errors
- managed sidecar lifecycle events
- runner reuse, queueing, switching, eviction, memory cleanup, retry, and blocked-by-memory decisions

Diagnostics are for both the desktop UI and future agents. Prefer structured events over ad hoc print output.
- Read queue and job state through `/queue` and `/history`.
- Retrieve generated files through `/view`.
- Inspect models and node information through `/models` and `/object_info`.

Model validation for `ComfyUIEngineAdapter` must query the running ComfyUI instance through its API. It must not depend on a local ComfyUI source `models` path, because ComfyUI may be external in dev mode or app-managed in product mode.

## Runtime Ownership

The app owns ComfyUI runtime lifecycle for product builds:

- create/use an isolated Python environment
- start ComfyUI on a selected localhost port
- expose health and logs through the backend
- recover or report crashes cleanly
- stop ComfyUI when the app exits

The app also owns runner memory policy. The frontend must not choose ComfyUI endpoints, decide which runner to evict, or decide whether multiple runners can stay warm. Those decisions belong to the backend `RunnerSupervisor` and Memory Governor.

Memory Governor decisions should become more machine-specific over time. Creator-side `.noofy` observations are initial hints; local run observations gathered through the backend are stronger evidence for future confidence, warm retention, co-residence, eviction, retry, and user-facing explanations.
