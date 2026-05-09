# Architecture Fix Plan

Date: 2026-05-10

Status: proposed implementation plan after independent EngineService and API route boundary investigations.

## Scope

This plan covers confirmed EngineService architecture problems, API route boundary follow-up work, engine adapter boundary cleanup, validation-tool runtime-surface cleanup, and large-module split guidance. It does not treat the Graphify report as authoritative; it uses the direct code investigation as the source of truth.

The main conclusion is that `EngineService` is an overgrown application facade, not a random god class. Some centralization is acceptable because the app backend owns workflow execution, runner selection, memory policy, diagnostics, and the engine adapter boundary. The fix should be incremental, preserving the current behavior around runner/job/memory coordination.

The API route layer is mostly healthy. Direct imports from runtime/workflow modules are not automatically boundary violations. The route layer should not push every concern into `EngineService`; several future fixes should introduce smaller application services instead.

The large modules flagged by Graphify are not rewrite candidates. The safest future work is extraction of narrow helpers with compatibility imports, not a rewrite.

Here’s the practical implementation plan I’d use.

**Guiding Rule**
Do not start by splitting `EngineService` or rewriting large modules. Start by moving composition and concrete factories out of the wrong places, then fix the real backend/frontend boundary leaks.

**Phase 1: Composition Cleanup**
Goal: reduce file-level coupling without changing behavior.

1. Move `create_default_engine_service()` out of `backend/app/engine/service.py`.
2. Move `comfyui_adapter_factory()` out of `backend/app/runtime/runner_coordinator.py`.
3. Keep temporary compatibility imports if needed.
4. Do not change `RunnerProcessCoordinator`; its injected factory shape is already right.

Protect with:
- `backend/tests/test_runner_coordinator.py`
- `backend/tests/test_api_runtime.py`
- `backend/tests/test_engine_service_install.py`

**Phase 2: API Composition Cleanup**
Goal: stop `routes.py` from being a composition root.

1. Move module-level service construction from `routes.py` into an app composition/bootstrap module.
2. Keep route handlers thin.
3. Keep user state and dashboard asset services outside `EngineService`.

Protect with:
- API route tests that monkeypatch `routes.engine_service`
- `backend/tests/test_user_state.py`
- `backend/tests/test_dashboard_assets.py`

**Phase 3: Real Boundary Fixes**
Goal: remove actual ComfyUI leakage.

1. Fix workflow image upload so it does not post directly to ComfyUI from `EngineService`.
2. Route upload through an adapter method or backend-owned input staging service.
3. Replace direct ComfyUI result `view_url` values with Noofy backend output references or media URLs.

Protect with new/updated tests:
- upload uses selected workflow runner
- output media URLs are Noofy-owned
- frontend rendering does not require direct ComfyUI `/view`

**Phase 4: API Schema Boundary**
Goal: separate public API DTOs from runtime implementation modules.

1. Move route-facing DTOs like `ComfyUIUpdateRequest` and `ComfyUIRebuildRequest` to `app.api.schemas` or another public contract module.
2. Keep runtime models internal where possible.
3. Use compatibility imports temporarily.

Protect with:
- `backend/tests/test_api_runtime.py`
- `backend/tests/test_comfyui_updates.py`
- `backend/tests/test_comfyui_launch_settings.py`

**Phase 5: Low-Risk Service Extractions**
Goal: reduce `EngineService` size only where behavior is delegated already.

1. Fix dashboard authoring/export injection or move them to explicit route-level services.
2. Extract memory observation/sampling later, but keep admission decisions near workflow orchestration for now.
3. Do not split `run_workflow()` or `start_workflow_runner()` yet.

Protect with:
- `backend/tests/test_runner_supervisor.py`
- `backend/tests/test_engine_service_install.py`
- `backend/tests/test_memory_governor.py`
- dashboard/export tests

**Phase 6: Runtime Surface Cleanup**
Goal: reduce confusion, not fix an urgent bug.

1. Move validation-only CLIs out of `app.runtime` later.
2. Keep `runner_process.py` and `runner_memory_probe.py` in runtime.
3. Decide whether validation tools should be packaged.

Protect with:
- `backend/tests/test_phase5e_real_smoke_command.py`
- `backend/tests/test_diagnostics.py`
- packaging/runtime verification

**Phase 7: Large Module Helper Extractions**
Goal: narrow helper moves only.

1. Extract shared workflow-store path helpers first.
2. Split `memory_governor.py` only by observers/parsers or local learning persistence.
3. Split `comfyui_updates.py` only by version-store, release/archive IO, or smoke validation.
4. Split `importer.py` by archive normalization, persistence, trust/source resolution, and capsule-lock generation.

Protect with:
- `backend/tests/test_memory_governor.py`
- `backend/tests/test_comfyui_updates.py`
- `backend/tests/test_noofy_importer.py`

## Architecture Rules To Preserve

- Frontend calls only the Noofy backend API.
- Backend owns the app engine contract and selected `EngineAdapter`.
- Product runtime uses app-managed ComfyUI and packaged runtime resources, with external ComfyUI only as development mode.
- Trusted backend never imports community custom-node modules or executes community setup code.
- Runtime and engine subsystems emit diagnostics through the shared diagnostics sink.
- Architecture docs stay compact and operationally useful.

## Confirmed Problems

### 1. Workflow Image Upload Bypasses The Adapter Boundary

`EngineService.upload_workflow_image()` directly posts to ComfyUI `/upload/image` through `runtime_manager.base_url`.

Why this is a problem:

- It bypasses `EngineAdapter`.
- It assumes the core ComfyUI runtime instead of the workflow-selected runner.
- It does not respect isolated runner routing.
- It exposes a ComfyUI-specific operation through the application facade without an app-owned adapter method.

Target outcome:

- Image upload is routed through the selected runner or through an app-owned backend asset/input-staging contract.
- `EngineService` no longer constructs ComfyUI upload URLs directly.

Likely work:

- Add an app-owned upload/input-staging operation to the engine adapter surface, or introduce a workflow input asset service that resolves runner staging behind the backend API.
- Implement the ComfyUI-specific upload in `ComfyUIEngineAdapter`.
- Route upload requests through the workflow-bound runner where applicable.
- Add tests for core runner and isolated runner upload routing.

Primary files:

- `backend/app/engine/service.py`
- `backend/app/engine/adapter.py`
- `backend/app/engine/comfyui_adapter.py`
- `backend/app/api/routes.py`
- related API and adapter tests

### 2. Composition Root Is Embedded In `service.py`

`create_default_engine_service()` wires most runtime, workflow, trust, model, runner, update, dashboard, and diagnostics dependencies inside `backend/app/engine/service.py`.

Why this is a problem:

- It makes `service.py` look more conceptually central than the runtime facade itself.
- It mixes service behavior with application bootstrapping.
- It increases merge risk when unrelated runtime wiring changes touch the same file as execution behavior.

Target outcome:

- `EngineService` contains service behavior only.
- Default dependency wiring lives in a dedicated factory/composition module.

Likely work:

- Move `create_default_engine_service()` and factory-only helper wiring to `backend/app/engine/factory.py` or `backend/app/bootstrap.py`.
- Keep the public import stable temporarily by re-exporting from `app.engine.service` if needed.
- Update `backend/app/api/routes.py` to import the factory from the new module.
- Run focused API/runtime tests.

Primary files:

- `backend/app/engine/service.py`
- `backend/app/engine/factory.py`
- `backend/app/api/routes.py`
- `backend/app/main.py`
- tests that monkeypatch `routes.engine_service`

### 3. Memory Observation Logic Is Too Deep Inside The Facade

`EngineService` owns job memory sampling, process/GPU attribution, local memory learning, memory retry bookkeeping, and memory-governor metrics.

Why this is a problem:

- The facade is doing detailed measurement and persistence work in addition to orchestration.
- The memory methods are cohesive enough to form their own collaborator.
- The code is heavily coupled to job result handling, so moving it carelessly could break retry and local learning behavior.

Target outcome:

- Keep memory admission decisions near workflow/runner orchestration for now.
- Move sampling, attribution, and local observation recording behind a `WorkflowMemoryObservationService` or similarly narrow collaborator.

Likely work:

- First extract pure helper functions or a small object without changing behavior.
- Preserve `run_workflow()` and `get_result()` semantics.
- Add characterization tests around:
  - sampling start/finish
  - peak attribution preference
  - local memory learning records
  - retry after memory cleanup
  - metrics increments

Primary files:

- `backend/app/engine/service.py`
- `backend/app/runtime/memory_governor.py`
- `backend/tests/test_runner_supervisor.py`
- `backend/tests/test_engine_service_install.py`
- `backend/tests/test_memory_governor.py`

### 4. Dashboard Authoring And Export Are Mutable Optional Attributes

`EngineService` receives most dependencies in the constructor, but `dashboard_authoring` and `workflow_exporter` are assigned after construction.

Why this is a problem:

- It creates a partially initialized service object.
- It makes route availability depend on mutable post-construction state.
- It obscures ownership: dashboard authoring/export behavior is mostly delegated and does not need to live inside the engine facade.

Target outcome:

- Dashboard authoring and workflow export are either constructor-injected dependencies or separate API-facing services.

Likely work:

- Prefer separate route-level services for dashboard authoring/export if they do not need engine orchestration.
- If route separation is too large, inject them through the `EngineService` constructor as explicit optional collaborators.
- Keep current route payloads unchanged.

Primary files:

- `backend/app/engine/service.py`
- `backend/app/api/routes.py`
- `backend/app/workflows/authoring.py`
- `backend/app/workflows/exporter.py`
- dashboard persistence/export tests

### 5. ComfyUI Adapter Factory Lives In Runtime Layer

`backend/app/runtime/runner_coordinator.py` defines `comfyui_adapter_factory()` and imports `ComfyUIEngineAdapter`.

Why this is a problem:

- `RunnerProcessCoordinator` is correctly generic over an injected `AdapterFactory`; the class itself does not choose ComfyUI.
- The concrete ComfyUI factory is engine-specific and belongs in engine composition, not runtime.
- The current coupling is real at module level: importing `app.runtime.runner_coordinator` also imports the concrete ComfyUI adapter.
- `app.runtime.__init__` re-exports `comfyui_adapter_factory`, which further suggests runtime owns a concrete engine choice.
- This is mild today because v1 only has ComfyUI runners and the coordinator behavior already uses the injected factory, but it will get in the way as soon as another adapter exists.

Target outcome:

- Runtime runner coordination remains adapter-agnostic.
- ComfyUI adapter construction happens in engine composition/factory code.
- `backend/app/runtime/runner_coordinator.py` imports only `EngineAdapter` and runner/runtime abstractions, not `ComfyUIEngineAdapter`.

Likely work:

- Move `comfyui_adapter_factory()` to the engine factory/composition module, for example `backend/app/engine/factory.py` or a future app composition module.
- Keep `RunnerProcessCoordinator` accepting only `AdapterFactory`; do not change its constructor contract.
- Keep `AdapterFactory = Callable[[RunnerDescriptor], EngineAdapter]` in runtime or move it to a neutral contract module if that becomes useful.
- Update `create_default_engine_service()` to import/use the concrete factory from the engine/composition layer.
- Remove `comfyui_adapter_factory` from `app.runtime.__init__` exports after compatibility concerns are handled.
- Move the concrete factory test out of `test_runner_coordinator.py` or update it to import from the new engine/composition module.

Primary files:

- `backend/app/runtime/runner_coordinator.py`
- `backend/app/runtime/__init__.py`
- `backend/app/engine/factory.py`
- `backend/app/engine/comfyui_adapter.py`
- `backend/app/engine/service.py`
- runner coordinator tests

### 6. Generated Output URLs Can Point Directly At ComfyUI

`ComfyUIEngineAdapter` builds result `view_url` values from `self.base_url` and ComfyUI `/view`. The frontend renders these `view_url` values directly.

Why this is a problem:

- The frontend normally calls only the Noofy backend API, but rendered output media can make the browser load ComfyUI directly.
- There is no app-owned `/api/view` or output-asset proxy route in `backend/app/api/routes.py`.
- Direct ComfyUI media URLs expose internal runtime endpoints and do not carry Noofy API token behavior.
- Isolated runner outputs should be addressed through Noofy-owned job/output references, not through whichever ComfyUI runner happened to produce the file.

Target outcome:

- Backend job results return app-owned output references or app-owned media URLs.
- Frontend output rendering uses only Noofy backend API URLs.
- ComfyUI `/view` remains an adapter implementation detail.

Likely work:

- Add an output asset/media service that can serve generated files through Noofy API routes.
- Change `ComfyUIEngineAdapter` result enrichment to return Noofy-owned references or relative backend URLs instead of direct ComfyUI URLs.
- Preserve enough metadata for download, preview, and future gallery indexing without exposing raw ComfyUI internals as the public frontend contract.
- Add tests that assert result image URLs start with `/api/` or another configured Noofy backend API base, not `runtime_manager.base_url` or ComfyUI runner URLs.

Primary files:

- `backend/app/engine/comfyui_adapter.py`
- `backend/app/api/routes.py`
- `backend/app/engine/models.py`
- `frontend/src/features/workflows/WorkflowRunPage.tsx`
- `frontend/src/lib/api/noofyApi.ts`
- `backend/tests/test_comfyui_adapter.py`
- workflow run page tests

### 7. Public API Schemas Are Imported From Runtime Modules

`routes.py` imports `ComfyUIRebuildRequest`, `ComfyUIUpdateRequest`, and `ComfyUILaunchSettings` from `app.runtime.*`.

Why this is a problem:

- This is not a current behavioral violation because routes still delegate to `EngineService`.
- It does make runtime implementation modules part of the public API route surface.
- As more runtime internals appear, this pattern can blur the line between API DTOs and internal runtime implementation.

Target outcome:

- Public request/response schemas used by routes live in an API-facing or engine-facing schema module.
- Runtime modules can keep internal models, but route handlers should not need to import runtime implementation modules for request parsing.

Likely work:

- Move API request DTOs for ComfyUI update/rebuild/launch settings to `app.engine.models`, `app.api.schemas`, or another explicit public contract module.
- Keep compatibility imports temporarily if needed.
- Update route imports and focused API tests.

Primary files:

- `backend/app/api/routes.py`
- `backend/app/runtime/comfyui_updates.py`
- `backend/app/runtime/launch_settings.py`
- `backend/app/engine/models.py` or a new `backend/app/api/schemas.py`
- `backend/tests/test_api_runtime.py`
- `backend/tests/test_comfyui_launch_settings.py`
- `backend/tests/test_comfyui_updates.py`

### 8. Route Module Performs Module-Level Composition

`routes.py` creates `engine_service`, `_user_state_service`, and `_asset_service` at import time.

Why this is a problem:

- It makes API routing, application composition, and service lifetime management share one module.
- Tests monkeypatch module globals, which works but increases coupling.
- Future service splitting will be easier if the app composition root owns service creation and injects route dependencies.

Target outcome:

- Route handlers remain thin HTTP adapters.
- Service construction moves to a composition root or app state dependency provider.
- Tests can override dependencies without mutating module globals.

Likely work:

- Introduce an app composition module that constructs `EngineService`, user state, dashboard assets, and future smaller services.
- Make routes read services from app state or dependency providers.
- Keep existing route paths and response shapes stable.

Primary files:

- `backend/app/api/routes.py`
- `backend/app/main.py`
- future `backend/app/bootstrap.py` or `backend/app/composition.py`
- API tests that monkeypatch `routes.engine_service`, `_asset_service`, or `_user_state_service`

### 9. User State And Dashboard Assets Need Explicit Application Boundaries

`routes.py` directly uses `UserStateService` and `DashboardAssetService`. This is preferable to pushing them into `EngineService`, but the boundaries should be made explicit.

Why this is a problem:

- These are application services, not engine execution behavior.
- Dashboard asset routes include `workflow_id` in the path but do not currently validate workflow existence or persist workflow ownership metadata.
- User state parsing currently happens inside the route handler.

Target outcome:

- User state and dashboard assets remain outside `EngineService`.
- They are composed as explicit route/application services with clear ownership.
- Dashboard assets can be associated with a workflow when needed.

Likely work:

- Keep `UserStateService` and `DashboardAssetService` outside the engine facade.
- Move route-level body parsing for user state into a small application service or API schema layer.
- Decide whether dashboard assets must validate `workflow_id` and record workflow ownership.
- Preserve current path traversal, MIME validation, and auth behavior.

Primary files:

- `backend/app/api/routes.py`
- `backend/app/workflows/user_state.py`
- `backend/app/workflows/assets.py`
- `backend/tests/test_user_state.py`
- `backend/tests/test_dashboard_assets.py`
- frontend user-state and dashboard-asset API tests

### 10. Validation Harnesses Live Under The Product Runtime Package

`backend/app/runtime/memory_governor_hardware_validation.py` and `backend/app/runtime/phase5e_real_smoke.py` are validation CLI harnesses, not product runtime modules. They are not imported by production code paths, but they live under `app.runtime`.

Why this is a problem:

- Their location makes them look like part of the trusted product runtime surface.
- `phase5e_real_smoke.py` imports many runtime/workflow internals and creates its own `LogStore`, which is acceptable for a harness but should not become a product pattern.
- `backend/pyproject.toml` includes `app*`, and the Tauri config bundles `../../backend/app`, so these files are likely included in the packaged runtime tree even though production code does not call them.
- Future agents may confuse validation-only entry points with supported runtime APIs.

Target outcome:

- Real product runtime modules stay under `backend/app/runtime`.
- Manual validation and hardware smoke harnesses live outside the product app package, or at least under an explicitly named validation-only namespace.
- Packaging makes it clear whether validation tools are intentionally shipped.

Likely work:

- Prefer moving validation CLIs to `backend/scripts/validation/` or `backend/tools/validation/`.
- Update Makefile targets to invoke the new paths.
- Update docs that mention `make phase5e-real-smoke` and `make memory-governor-linux-validation` only if command behavior changes.
- Update tests that import `app.runtime.phase5e_real_smoke`.
- Update diagnostics allowlists that currently permit `runtime/phase5e_real_smoke.py`.
- Do not move `runner_process.py`; it is product runtime code.
- Do not move `runner_memory_probe.py` as part of this cleanup unless runner telemetry is redesigned, because `RunnerProcessSupervisor` uses it for product runner memory telemetry.

Primary files:

- `backend/app/runtime/memory_governor_hardware_validation.py`
- `backend/app/runtime/phase5e_real_smoke.py`
- `backend/app/runtime/runner_process.py`
- `backend/app/runtime/runner_memory_probe.py`
- `Makefile`
- `backend/pyproject.toml`
- `frontend/src-tauri/tauri.conf.json`
- `backend/tests/test_phase5e_real_smoke_command.py`
- `backend/tests/test_diagnostics.py`

### 11. Large Modules Need Narrow Future Extractions, Not Rewrites

`backend/app/runtime/memory_governor.py`, `backend/app/runtime/comfyui_updates.py`, and `backend/app/workflows/importer.py` are large, but the direct investigation did not confirm that they are badly structured in the way a graph-size report might imply.

Why this is a problem:

- Large cohesive files can still slow future edits and make dependency boundaries harder to see.
- Future agents may treat line count as proof that these files should be rewritten or aggressively split.
- Some private helpers have already become cross-module dependencies, especially workflow-store path helpers.

Target outcome:

- Preserve current behavior and test characterization.
- Extract narrow helper modules only when the boundary is clear.
- Keep compatibility imports temporarily so existing callers and tests do not all move at once.

File-specific guidance:

- `memory_governor.py` is mostly cohesive around memory evidence, platform signals, local learning, and admission policy. Keep the pure policy layer together. Future safe splits are platform observers/parsers and `LocalMemoryLearningStore`.
- `comfyui_updates.py` is mostly cohesive around managed ComfyUI update, rebuild, repair, activation, fallback, and local validation records. Future safe splits are version-record storage, release/archive IO, smoke validation, and public API DTOs.
- `importer.py` is the clearest future split candidate because it combines archive safety, package normalization, transactional package storage, trust/source resolution, capsule-lock generation, and runtime profile selection. Split by subdomain, not by line count.

Likely work:

- Do not start with broad file rewrites.
- Create shared workflow storage/path helpers for `_safe_store_segment` before splitting importer behavior. Similar logic currently appears in importer, authoring/export lookup paths, capsule path code, and engine service helper code.
- If splitting `memory_governor.py`, move observers/parsers first because policy tests can keep admission behavior stable.
- If splitting `comfyui_updates.py`, move record persistence or archive/release IO first because update/repair orchestration is tightly coupled and well tested.
- If splitting `importer.py`, move archive normalization and package persistence separately, then move capsule-lock/runtime-profile selection behind an explicit collaborator.

Primary files:

- `backend/app/runtime/memory_governor.py`
- `backend/app/runtime/comfyui_updates.py`
- `backend/app/workflows/importer.py`
- `backend/app/workflows/authoring.py`
- `backend/app/workflows/exporter.py`
- `backend/app/workflows/capsule.py`
- `backend/app/engine/service.py`
- `backend/tests/test_memory_governor.py`
- `backend/tests/test_comfyui_updates.py`
- `backend/tests/test_noofy_importer.py`

## False Alarms / Acceptable Centralization

- `EngineService` coordinating validation, runner selection, adapter calls, memory admission, and diagnostics is acceptable for the current architecture.
- `routes.py` is mostly a thin HTTP layer over the backend service. It should not be treated as the main architecture problem.
- Direct route imports of request DTOs and small workflow services are not automatically architecture violations.
- User state and dashboard assets should not be moved into `EngineService` just to make `routes.py` depend on one large object.
- Frontend ComfyUI settings/update controls call Noofy backend routes such as `/api/engine/comfyui/*`; that naming is not itself evidence of direct ComfyUI access.
- `start_workflow_runner()` and `run_workflow()` are central because they coordinate multiple domains. Splitting them before lower-risk extractions would increase risk.
- The `RunnerProcessCoordinator` dependency on an adapter factory is acceptable; only the concrete ComfyUI factory location should change.
- `runner_process.py` belongs in `app.runtime`; it is the product process-supervision layer beneath runner coordination.
- `memory_governor_hardware_validation.py` and `phase5e_real_smoke.py` are not production import violations today; the concern is package/runtime-surface clarity.
- `memory_governor.py` and `comfyui_updates.py` are large but mostly cohesive domain modules with strong tests.
- `importer.py` is mixed enough to deserve future splitting, but it is not currently a rewrite candidate.

## Dangerous Areas To Avoid Moving First

Do not start with these methods unless strong characterization tests are already in place:

- `run_workflow()`
- `get_result()`
- `_maybe_retry_after_memory_cleanup()`
- `start_workflow_runner()`
- `handoff_next_queued_runner_start()`
- `handoff_queued_workflow_run()`
- `_adapter_for_job()`

These methods coordinate runner routing, adapter calls, memory policy, queueing, diagnostics, and retry behavior.

## Recommended Split Order

### Phase 1: Low-Risk Structure

1. Move the default service factory out of `service.py`.
2. Move the concrete ComfyUI runner adapter factory out of `runtime/runner_coordinator.py`.
3. Move module-level service creation out of `routes.py` into an app composition root or dependency provider.
4. Keep compatibility imports temporarily if needed.

Validation:

- API runtime tests.
- runner coordinator tests.
- runner supervisor adapter registration/routing tests.
- import/startup smoke tests that instantiate `create_default_engine_service()`.
- API tests that currently monkeypatch `routes.engine_service`, `_asset_service`, or `_user_state_service`.

### Phase 2: Adapter Boundary Fix

1. Replace direct ComfyUI upload behavior with an app-owned operation.
2. Implement ComfyUI upload behavior behind `ComfyUIEngineAdapter` or a selected-runner staging service.
3. Replace direct ComfyUI result `view_url` values with app-owned output references or Noofy backend media URLs.
4. Add tests proving isolated-runner routing is respected and frontend-rendered output media does not point directly at ComfyUI.

Validation:

- API upload tests.
- ComfyUI adapter tests.
- runner-bound workflow upload tests.
- workflow result/output media tests.
- workflow run page tests.

### Phase 3: API Schema Boundary

1. Move public route request DTOs out of runtime implementation modules.
2. Keep route response shapes stable.
3. Keep compatibility imports temporarily if the move is otherwise noisy.

Validation:

- API runtime tests.
- ComfyUI launch settings/update tests.
- update/rebuild tests.

### Phase 4: Delegated Workflow Utilities

1. Move dashboard authoring/export out of mutable post-construction fields.
2. Keep user state and dashboard assets as separate application services, not EngineService methods.
3. Decide whether dashboard assets should validate and record workflow ownership.
4. Keep route response shapes stable.

Validation:

- dashboard authoring tests.
- dashboard persistence tests.
- workflow export tests.
- API dashboard/export tests.
- user state tests.
- dashboard asset tests.

### Phase 5: Validation Tool Runtime-Surface Cleanup

1. Move validation-only CLI harnesses out of `app.runtime` into `backend/scripts/validation/` or `backend/tools/validation/`.
2. Keep product runner supervision and memory telemetry helpers in `app.runtime`.
3. Decide whether validation tools should be included in packaged builds. If not, update Tauri/backend packaging accordingly.
4. Preserve Makefile command behavior where possible.

Validation:

- `backend/tests/test_phase5e_real_smoke_command.py`.
- `backend/tests/test_diagnostics.py`.
- Makefile validation targets still resolve.
- Runtime packaging verification still passes.

### Phase 6: Memory Observation Extraction

1. Extract memory sampling and observation recording into a collaborator.
2. Keep memory admission decisions in `EngineService` until the collaborator is stable.
3. Preserve current metrics and diagnostic event names.

Validation:

- `test_runner_supervisor.py` memory observation and retry cases.
- `test_engine_service_install.py` memory-governor runner-start cases.
- `test_memory_governor.py`.

### Phase 7: Large Module Helper Extractions

1. Extract shared workflow store path helpers used by importer, authoring, exporter, capsule code, and engine service.
2. Split `memory_governor.py` only along low-risk helper boundaries such as platform observers/parsers or local learning persistence.
3. Split `comfyui_updates.py` only along low-risk helper boundaries such as version-record storage, release/archive IO, or smoke validation.
4. Split `importer.py` by subdomain: archive normalization, package persistence, trust/source resolution, and capsule-lock/runtime-profile selection.
5. Keep compatibility imports until callers and tests have been migrated.

Validation:

- `backend/tests/test_memory_governor.py`.
- `backend/tests/test_comfyui_updates.py`.
- `backend/tests/test_noofy_importer.py`.
- dashboard authoring/export tests that locate mutable package directories.
- EngineService import/install tests that rely on imported package store paths.

### Phase 8: Reassess Core Orchestration

After the earlier phases, re-evaluate whether `run_workflow()` and `start_workflow_runner()` still need splitting. If they do, split by policy collaborator rather than by arbitrary method groups:

- runner start policy
- workflow run admission policy
- job result finalization policy

Do not split them only to reduce line count.

## Recommended Future Service Boundaries

These are target boundaries, not a request to move everything immediately:

- `EngineRuntimeService`: ComfyUI status/start/stop/bootstrap/update/launch settings.
- `WorkflowExecutionService`: validate/run/job progress/cancel/result/events and selected adapter routing.
- `WorkflowInstallService`: import, install state, preparation, runner lifecycle, queued runner starts.
- `DashboardAuthoringService`: bindable inputs, unresolved inputs, dashboard validate/save/export.
- `WorkflowUserStateApplicationService`: user values/layout persistence and API validation.
- `DashboardAssetApplicationService`: dashboard asset upload/serve/metadata, optionally workflow ownership.
- `OutputAssetService`: generated output references and media proxying through Noofy backend API.
- `AdapterFactory` ownership: runtime should accept an injected factory; engine/composition should own concrete factory functions such as ComfyUI adapter construction.

## Tests To Protect Before Any Split

Minimum focused test set:

- `backend/tests/test_runner_supervisor.py`
- `backend/tests/test_runner_coordinator.py`
- `backend/tests/test_engine_service_install.py`
- `backend/tests/test_workflow_packages.py`
- `backend/tests/test_noofy_importer.py`
- `backend/tests/test_api_workflow_install.py`
- `backend/tests/test_api_runtime.py`
- `backend/tests/test_api_paths.py`
- `backend/tests/test_api_auth.py`
- `backend/tests/test_api_workflow_import.py`
- `backend/tests/test_comfyui_launch_settings.py`
- `backend/tests/test_comfyui_updates.py`
- `backend/tests/test_dashboard_assets.py`
- `backend/tests/test_user_state.py`
- frontend API and workflow run page tests

Add missing coverage before changing upload routing:

- upload uses selected workflow runner, not always core runtime
- upload returns stable app-owned response shape
- upload failure is reported without leaking ComfyUI internals unnecessarily

Add missing coverage before changing output media routing:

- job result output media URLs are app-owned Noofy backend URLs or opaque output references
- frontend output rendering never requires direct ComfyUI `/view` URLs
- output media routes enforce the same local API token behavior as the rest of `/api`

Add or preserve coverage before moving the ComfyUI adapter factory:

- `RunnerProcessCoordinator` registers a started runner with whatever injected adapter factory returns
- coordinator refresh/stop/stop_all behavior remains independent of adapter implementation
- `RunnerSupervisor.update_runner_endpoint()` still updates descriptors and calls adapter `configure_endpoint()`
- job routing still uses the adapter registered for the selected runner
- the moved ComfyUI factory still constructs adapters with descriptor `base_url` and `ws_url`

Add or preserve coverage before moving validation harnesses:

- `phase5e_real_smoke` command helpers still normalize fixture image paths and runner args correctly
- diagnostics tests still prevent product modules from creating private `LogStore` fallbacks
- Makefile validation targets still point to working entry points
- packaged runtime verification intentionally includes or excludes validation tools

Add or preserve coverage before splitting large modules:

- memory observer/parsing behavior remains covered by `test_memory_governor.py`
- memory admission, retry, release, and local learning behavior remains unchanged
- ComfyUI update/rebuild/repair/fallback behavior remains covered by `test_comfyui_updates.py`
- importer archive safety, trust verification, source resolution, package persistence, and runtime profile selection remain covered by `test_noofy_importer.py`
- shared workflow store path helpers produce identical package directories across importer, authoring, exporter, capsule code, and EngineService

## Non-Goals

- Do not replace `EngineService` with many route-specific services in one change.
- Do not push user state, dashboard assets, or simple API DTO handling into `EngineService`.
- Do not move community custom-node execution into the trusted backend.
- Do not make the frontend aware of ComfyUI endpoints.
- Do not make runtime modules import concrete engine adapters unless the module is explicitly engine-specific.
- Do not split `run_workflow()` or `start_workflow_runner()` just to satisfy a graph metric.
- Do not rewrite large runtime files as part of this EngineService cleanup.
- Do not split large modules only because they are large; extract narrow helpers with compatibility imports when the boundary is clear.
- Do not move `runner_process.py` out of `app.runtime`; it is product runtime code.
- Do not treat validation CLI location as an urgent production safety issue unless packaging policy requires excluding them from shipped builds.
