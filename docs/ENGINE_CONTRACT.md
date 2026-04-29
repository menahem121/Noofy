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

## Adapter Boundary

`EngineAdapter` is the backend abstraction for running workflows.

The first implementation is `ComfyUIEngineAdapter`, which translates app operations into ComfyUI HTTP and WebSocket calls.

The frontend must not know whether a workflow is running through ComfyUI, a future macOS-native engine, or another adapter.

For v1, `ComfyUIEngineAdapter` should normally talk to an app-managed ComfyUI sidecar. Connecting to an externally launched ComfyUI instance is a development convenience only.

## Job Lifecycle

```text
Load workflow package
  -> validate package and dashboard bindings
  -> ask active EngineAdapter for available models
  -> check required models against active engine
  -> submit graph to active EngineAdapter
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

Diagnostics are for both the desktop UI and future agents. Prefer structured events over ad hoc print output.
- Read queue and job state through `/queue` and `/history`.
- Retrieve generated files through `/view`.
- Inspect models and node information through `/models` and `/object_info`.

Model validation for `ComfyUIEngineAdapter` must query the running ComfyUI instance through its API. It must not depend on a local `ComfyUI-official-repo/models` path, because ComfyUI may be external in dev mode or app-managed in product mode.

## Runtime Ownership

The app owns ComfyUI runtime lifecycle for product builds:

- create/use an isolated Python environment
- start ComfyUI on a selected localhost port
- expose health and logs through the backend
- recover or report crashes cleanly
- stop ComfyUI when the app exits
