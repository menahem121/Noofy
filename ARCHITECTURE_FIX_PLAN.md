# Architecture Cleanup Notes

Date: 2026-05-10

Status: current architecture note. This file records the cleanup boundaries that matter for future work; it is not an implementation checklist.

## Current Boundaries

- The frontend calls only the Noofy backend API. Generated output media and workflow image uploads go through backend routes, not direct ComfyUI endpoints.
- The backend owns the `EngineAdapter` contract. ComfyUI-specific HTTP/WebSocket behavior lives behind `ComfyUIEngineAdapter`.
- Runtime runner coordination accepts an injected adapter factory. Concrete ComfyUI adapter construction belongs to engine/application composition.
- API route handlers are HTTP adapters over composed application services. User state and dashboard asset services remain outside `EngineService`.
- Validation and hardware smoke harnesses live outside the product runtime package under `backend/tools/validation/`.
- Large runtime/workflow modules should be split only along clear helper boundaries with compatibility imports where needed.

## EngineService Position

`EngineService` remains an application facade for workflow execution. It still owns cross-domain orchestration where keeping the coordination local reduces risk:

- workflow package loading and validation
- selected runner lookup and job-to-runner tracking
- adapter dispatch
- memory admission, queueing, eviction, and retry decisions
- diagnostics around workflow submission, completion, and failure

Detailed memory sampling and local observation recording are delegated to `app.engine.memory_observation.MemoryObservationCoordinator`. Dashboard authoring and workflow export are constructor-injected collaborators.

Do not split `run_workflow()`, `start_workflow_runner()`, `get_result()`, or memory retry orchestration just to reduce file size. Those methods should move only when a concrete policy collaborator naturally emerges, such as runner-start policy, workflow-run admission policy, or job-result finalization.

## Current Helper Modules

Workflow import/storage helpers:

- `app.workflows.store_paths`: safe workflow-store path segments and imported package IDs.
- `app.workflows.archive_validation`: `.noofy` archive member safety and required-file validation.
- `app.workflows.import_normalization`: archive JSON normalization into app package fields.
- `app.workflows.package_persistence`: imported package transaction writing.
- `app.workflows.import_runtime_profile`: import-time runtime profile selection and platform detection.
- `app.workflows.import_policy`: import status, trust/source policy status, and custom-node source validation.
- `app.workflows.import_capsule_lock`: imported package capsule-lock generation.

Runtime helpers:

- `app.runtime.system_memory`: cross-platform system RAM probing and parsing.
- `app.runtime.comfyui_update_releases`: upstream release lookup, archive download, and stable release sorting.
- `app.runtime.comfyui_update_archive`: ComfyUI release archive extraction and path safety.
- `app.runtime.comfyui_update_records`: local ComfyUI version metadata persistence.
- `app.runtime.comfyui_update_smoke`: update smoke route, prompt/WebSocket, and path-isolation checks.

## Future Decisions

- Decide whether dashboard assets should validate workflow existence and record workflow ownership.
- Remove compatibility wrappers once downstream callers and tests have migrated.
- Consider deeper `memory_governor.py` extraction only if platform observers, parsers, or local learning persistence become independently painful to maintain.
- Consider smaller API-facing application services only when they reduce route complexity without pushing unrelated concerns into `EngineService`.

## Guardrails

- Do not make the frontend aware of ComfyUI endpoints.
- Do not import community custom-node modules or execute community setup code in the trusted backend process.
- Do not move user state or dashboard assets into `EngineService` just to make routes depend on one object.
- Do not move validation CLIs back into `app.runtime`.
- Do not rewrite large cohesive modules for graph-size reasons alone.
