# Engine Contract

The app owns the engine contract. UI code should depend on this contract, not on ComfyUI routes or payloads.

## Core Operations

- `runWorkflow(workflowId, inputs, options)`: validate and start a workflow job.
- `getProgress(jobId)`: return current job progress and status.
- `streamProgress(jobId)`: stream frontend-ready progress/result events.
- `cancelJob(jobId)`: stop a running or queued job.
- `getResult(jobId)`: return final outputs, errors, and generated files.
- `fetchOutput(jobId, filename, subfolder, type)`: serve generated output media through the backend API.
- `listAvailableModels()`: report models available to the active engine.
- `listModelInventory()`: report frontend-ready model inventory through the app backend, combining Noofy-owned model files, optional external ComfyUI model files, engine-visible fallback models, workflow-required missing models, local tags, source ownership labels, and Noofy-only delete eligibility.
- `importModelFiles(sourcePaths, folder, overwrite?)`: copy user-selected local files into the configured Noofy Models folder through backend path validation. The frontend must not write model files directly and Noofy must not import into the external ComfyUI folder.
- `deleteModelFile(modelKey)`: delete a regular model file only when the backend resolves it inside the configured Noofy Models folder **and** Noofy recorded that file as imported or downloaded by Noofy. Arbitrary user-owned files placed inside Noofy Models, external ComfyUI folder files, engine-visible references, and missing workflow requirements are not deletable through this operation.
- `downloadRequiredModels(selections)`: download selected missing workflow requirements through backend provider resolution and verified download transactions.
- `getModelAvailabilitySummary(workflowId)`: return identity-verified required-model availability for an imported workflow, including local matches across the Noofy Models folder and the optional connected ComfyUI folder.
- `validateWorkflow(workflowId)`: validate package structure, bindings, and model availability. For imported workflows, validation uses the model availability summary so identity-verified local files take effect, not just raw engine `object_info`.
- `uploadWorkflowImage(workflowId, file)`: stage or upload a workflow image input through the selected runner.
- `listLogs(level?, limit?)`: return recent backend and engine diagnostic events.
- `listJobLogs(jobId, level?, limit?)`: return diagnostic events for a specific job.
- `listRunners()`: report runner lifecycle and memory-governor state through the backend API.
- `openWorkflowRunnerLease(workflowId)` / `closeWorkflowRunnerLease(workflowId, leaseId)`: report workflow view open/close intent so the backend can decide warm retention.

## Adapter Boundary

`EngineAdapter` is the backend abstraction for running workflows.

The first implementation is `ComfyUIEngineAdapter`, which translates app operations into ComfyUI HTTP and WebSocket calls.

The frontend must not know whether a workflow is running through ComfyUI, a future platform-native engine, or another adapter.

For v1, `ComfyUIEngineAdapter` should normally talk to an app-managed ComfyUI sidecar. Connecting to an externally launched ComfyUI instance is a development convenience only.

ComfyUI sidecar lifecycle, launch settings, bootstrap, update, rebuild, and repair operations are runtime management concerns owned by `ComfyUISidecarService`, not workflow execution operations on the engine contract.

Workflow image uploads and generated output reads are adapter operations. The route layer and run/result services select the workflow/job-bound runner first, then dispatch through that runner's adapter. `EngineService` may still delegate these operations during migration, but it is not the long-term owner. ComfyUI upload and `/view` calls are implementation details behind `ComfyUIEngineAdapter`.

The Models page is an app-management surface. The frontend calls Noofy backend
model endpoints only; it must not call ComfyUI `/models`, Hugging Face, or
Civitai directly. The backend owns source labels, ownership labels, delete
eligibility, path containment, tag persistence, provider credentials, and
verified download transactions.

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
- Read queue and job state through `/queue` and `/history`.
- Retrieve generated files through ComfyUI `/view` inside the adapter, while returning backend-owned media URLs such as `/api/jobs/{job_id}/outputs/view?...` to the frontend.
- Upload image inputs through ComfyUI `/upload/image` inside the adapter, selected by workflow runner.
- Inspect models and node information through `/models` and `/object_info`.

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

Runtime subsystems should emit diagnostics through an injected diagnostics sink. They should not create fallback private stores when no sink is supplied. The backend composition root owns one shared diagnostics store for the running app, and API-facing service methods read from that shared store for `listLogs`, `listJobLogs`, health `latest_error`, and troubleshooting payloads.

Emit-only components should not depend on storage, filtering, or API exposure details. Read/query behavior belongs in API-facing services or a diagnostics reader contract.

Model validation for `ComfyUIEngineAdapter` must query the running ComfyUI instance through its API. It must not depend on a local ComfyUI source `models` path, because ComfyUI may be external in dev mode or app-managed in product mode. Required-model availability for imported workflows is reported by Noofy's `ModelAvailabilityService`, which scans the configured Noofy Models folder and the optional connected ComfyUI folder; see [MODEL_RESOLUTION_AND_DOWNLOADS.md](MODEL_RESOLUTION_AND_DOWNLOADS.md).

## Runtime Ownership

The app owns ComfyUI runtime lifecycle for product builds:

- create/use an isolated Python environment
- start ComfyUI on a selected localhost port
- expose health and logs through the backend
- recover or report crashes cleanly
- stop ComfyUI when the app exits

The app also owns runner memory policy. The frontend must not choose ComfyUI endpoints, decide which runner to evict, or decide whether multiple runners can stay warm. Those decisions belong to the backend `RunnerSupervisor` and Memory Governor.

Memory Governor decisions should become more machine-specific over time. Creator-side `.noofy` observations are initial hints; local run observations gathered through the backend are stronger evidence for future confidence, warm retention, co-residence, eviction, retry, and user-facing explanations.
