# Memory Governor Production Remaining Work

Date: 2026-06-02

Status: active roadmap after P0/P1 lifecycle stabilization.

This document is the source of truth for Memory Governor work that remains
after the completed P0/P1 stabilization. It reconciles the historical audit
against the current implementation; it is not a raw copy of earlier findings.

## Current Status

P0/P1 are completed in the current implementation. The Memory Governor now has
the lifecycle foundation expected for production work:

- backend-owned automatic workflow-run dispatch
- runner-start queue draining after relevant state changes
- UUID workflow queue records with queue ID to job ID aliases
- progress, result, cancel, logs, SSE, and output routing through queue aliases
- atomic runner reservations for submission, eviction, and startup transitions
- cancel, admission, reservation, cleanup, and adapter-submission race handling
- loop guards and backoff for repeated handoff/requeue paths
- finalize-once terminal handling for polling, SSE, terminal hints, watchers,
  retry, local learning, history, gallery scheduling, runner release, and queue
  drain notifications
- adaptive async `/free` release polling with timeout, pre-cleanup baseline,
  and timeline diagnostics
- core warm residency retained until an actual RAM/VRAM increase is observed
  from the pre-cleanup baseline
- observer-unavailable cleanup fails closed instead of claiming release
- job-specific peak learning, input-profile buckets, and observation dedupe
- prompt and seed edits treated as memory-neutral when identified as text/seed
  controls
- memory-changing profile inputs kept separate from prompt/seed-only reruns
- explicit cleanup and blocking states in backend payloads, including
  `memory_cleanup_failed`, `blocked_external_pressure`,
  `blocked_exceeds_capacity`, and `blocked_unattributed_pressure`
- first P2 estimate-enrichment slice: submitted width/height, graph/default
  batch size, frame count as effective batch multiplier, and inferred workflow
  type now feed the existing heuristic estimate and developer diagnostics
- second P2 estimate-safety slice: workflows with custom nodes now carry
  custom-node memory uncertainty in estimate diagnostics; non-local estimate
  confidence is lowered and heuristic VRAM estimates receive a conservative
  safety factor until local observations exist
- third P2 runtime-option slice: submitted precision and VRAM mode options are
  normalized, included in runtime estimate diagnostics, and applied to
  heuristic VRAM estimates
- first P3 frontend memory-state slice: the workflow run page and default
  canvas run view now render distinct user-facing copy for queueing, cleanup,
  retry, cleanup failure, external pressure, capacity failure, and unattributed
  pressure; generic backend "not enough memory" messages are replaced with the
  more precise `memory_status.state` copy where safe
- first real CUDA validation pass: the managed NVIDIA A10G harness now covers
  allocator telemetry, model-free warm prompt/seed reruns, profile-changing
  reruns, rapid queue handoff, queued cancellation, isolated runner eviction,
  observed VRAM release, and managed-runner shutdown

Ownership after P0/P1:

- `backend/app/runs/`: workflow queue records, aliases, dispatch, result and
  progress alias behavior, cancellation routing, watchers, and terminal
  finalize-once behavior.
- `backend/app/runtime/runners/`: runner descriptors, reservations, state
  transitions, startup/stop, runner-start queue lifecycle, and cancellation.
- `backend/app/runtime/memory/`: admission policy, cleanup, release polling,
  retry policy, learning, local observations, and memory diagnostics.
- `backend/app/engine/service.py`: composition glue and compatibility facade,
  not the durable owner for new lifecycle logic.

## Historical Audit Findings

| Finding | Status | Current owner/domain | Notes | Follow-up needed |
|---|---|---|---|---|
| Same workflow rerun double-counted the warm runner's resident VRAM. | fixed | `runtime/memory`, `runs/orchestrator` | Same-workflow warm reuse no longer subtracts the full peak again for compatible resident state. | Broaden warm reuse in P2 for different workflows sharing the same resident model set. |
| Prompt-only edits created a new memory bucket. | fixed | `engine/memory_observation`, `runs/orchestrator` | Text prompt controls are treated as memory-neutral when bindings identify them as prompt/text fields. | Add publisher/dashboard-declared memory-affecting metadata so unusual controls are classified explicitly. |
| Seed-only edits created a new memory bucket. | fixed | `engine/memory_observation`, workflow package bindings | Seed widgets are treated as memory-neutral. | Same metadata follow-up as prompt controls for packages with custom seed controls. |
| Memory-changing settings could inherit smaller-run evidence. | partially fixed | `engine/memory_observation`, `runtime/memory` | Non-neutral inputs/options are fingerprinted. P2 now semantically extracts width/height, batch, frame count, workflow type, precision, and VRAM mode into heuristic estimates. P2 still needs selected model/LoRA semantics and declared bindings. | Implement publisher-declared memory-affecting bindings and richer model/runtime-option extraction. |
| Low free VRAM from an idle Noofy runner caused immediate blocking. | fixed | `runtime/memory`, `runtime/runners` | Idle Noofy-owned memory is treated as reclaimable and cleanup waits for observed release before admission continues. | Improve attribution of idle/resident memory where platform signals allow it. |
| Active workflow followed by another run could block or be killed. | fixed | `runs/queue_service`, `runs/lifecycle_service`, `runtime/runners` | Active work queues by default; cleanup skips active runners. | Refine queueing so unrelated CPU-only/light work does not always wait behind GPU-heavy work. |
| `Not enough memory` was too early in the admission path. | partially fixed | `runtime/memory`, `runs/orchestrator`, frontend | Backend now attempts reuse, queue, cleanup, release polling, and retry before terminal memory failure. The workflow run page and canvas run view now render distinct copy from `memory_status.state`. | Continue P3 polish across any remaining surfaces and next-step copy. |
| Workflow queue required manual handoff and could strand records. | fixed | `runs/lifecycle_service` | Dispatch wakes automatically and processes eligible queue records before runner-start drains. Real CUDA validation also removed a self-wake loop caused by reservation rollback bookkeeping. | Continue to keep queue state observable in diagnostics. |
| Runner-start queue could strand records behind active work. | fixed | `runtime/runners/lifecycle_service`, `runs/lifecycle_service` | Runner-start draining is tied to run dispatch and runner state-change notifications. | No immediate follow-up beyond real-runner validation. |
| Queue record could be removed before successful adapter submission. | fixed | `runs/queue_service`, `runs/orchestrator` | Records stay active through handoff and submission; terminal aliases are retained in a bounded ledger. | No immediate follow-up. |
| Queue ID to job ID alias could break progress, result, output, logs, SSE, or cancel. | fixed | `runs/job_service`, `runs/result_service`, `runs/queue_service` | Public queue IDs resolve to canonical submitted jobs after submission and remain available while retained. | Consider persistence if queue/result aliases must survive backend restart. |
| Requeue could busy-loop. | fixed | `runs/queue_service`, `runs/lifecycle_service`, `runtime/runners` | Requeues use attempt counts, one attempt per dispatch epoch, max records per wake, backoff, and failure after repeated transient handoff failures. A real A10G pass found and fixed immediate self-wakes from queued handoff and reservation rollback bookkeeping. | No immediate follow-up. |
| Two rapid Run clicks could both submit to the same runner. | fixed | `runtime/runners/supervisor`, `runs/orchestrator` | Submission reservations are atomic and serialize handoff. Busy bound isolated runners remain selected instead of falling back to the core adapter. Real A10G validation confirms queued alias handoff. | Validate through the packaged desktop UI as a final product check. |
| Cleanup could unload a runner reserved by another request. | fixed | `runtime/runners/supervisor`, `runtime/memory/service` | Cleanup uses eviction reservations and rechecks active/reserved state. | No immediate follow-up. |
| Cancellation could lose the race during `adapter.run_workflow(...)`. | fixed | `runs/job_service`, `runs/orchestrator`, `runtime/runners/supervisor` | Cancellation requests during adapter submission wait for canonical job registration, then cancel the submitted job through the alias. | No immediate follow-up. |
| Terminal finalization could run twice. | fixed | `runs/result_service` | Per-job async locks and cached terminal outcomes guard sampling completion, learning, retry, history, gallery, runner release, and dispatch notifications. | No immediate follow-up. |
| Terminal polling could call the adapter again after a cached terminal outcome. | fixed | `runs/result_service`, `runs/job_service` | Cached terminal outcomes are returned through result/progress paths. | No immediate follow-up. |
| Core warm residency could be cleared before observed release. | fixed | `runtime/memory/service`, `runtime/runners/supervisor` | Core residency is cleared only after release polling observes an actual RAM/VRAM increase from the pre-cleanup baseline. Timeout/unavailable marks `release_failed`. | Validate with a real loaded CUDA model and delayed core `/free`. |
| `/free` HTTP success was treated as actual release. | fixed | `runtime/memory/memory_governor`, `runtime/memory/service` | `/free` is now only an acknowledgment; async polling distinguishes safe observed capacity from an actual memory increase and fails closed for unproven core release. | Real loaded-model `/free` timeline validation remains. |
| Cleanup failure was reported only as generic `blocked_by_memory`. | fixed | `runtime/memory/service`, API models, frontend | Compatibility `EngineJob.status` can remain `blocked_by_memory`, but `memory_status.state` reports `memory_cleanup_failed`; the workflow run page and canvas run view show cleanup failure separately from capacity failure. | Continue P3 polish outside the run page as needed. |
| Local learning reused stale descriptor peaks for sampled jobs. | fixed | `engine/memory_observation` | Sampled jobs learn selected job-window peaks or no peak evidence; descriptor fallback is not used when a sampled job lacks selected peak evidence. | No immediate follow-up. |
| Later larger runs might not raise runner/profile peak evidence. | fixed | `engine/memory_observation`, `runtime/runners/supervisor`, `LocalMemoryLearningStore` | Runner descriptor peaks and local summaries update as maxima and dedupe by non-null job ID. | P2 semantic profile extraction still needed so larger profiles are classified reliably. |
| Custom-node workflows could appear as ordinary low-risk heuristic runs. | partially fixed | `runtime/memory` | Custom-node count/types are now included in workflow estimates and developer diagnostics, non-local confidence is lowered, and heuristic estimates use a conservative safety factor. The implementation does not yet classify individual custom-node memory behavior or private caches. | Add publisher/dashboard declarations and richer custom-node/cache validation. |
| Observer-unavailable cleanup could claim success or clear residency. | fixed | `runtime/memory/service`, `runtime/memory/memory_governor` | Unavailable observation returns `memory_cleanup_failed`, preserves attribution, and marks release failure. | No immediate follow-up. |
| Runtime/memory exposed runs-owned queue internals. | fixed | `runtime/memory/service`, `runs/job_service`, `runs/lifecycle_service` | Queue handoff/progress/cancel ownership has moved back to `runs/`; memory owns admission and cleanup only. | Keep future queue work out of memory modules. |
| EngineService accumulated lifecycle ownership. | partially fixed | Composition root, `runs/`, `runtime/runners/`, `runtime/memory` | P0/P1 lifecycle behavior lives in domain services. `EngineService` still remains a broad application front door and compatibility facade. | Track long-term EngineService cleanup below. |
| Compatibility helpers for unreleased internals could outlive the migration. | partially fixed | `EngineService`, tests, API/dependency wiring | Some migration proxies remain for compatibility while callers move to domain services. | Retire proxies when call sites are migrated. |
| CPU-only/lightweight work may wait behind unrelated GPU-heavy work. | remaining | `runtime/memory`, `runtime/runners`, `runs/lifecycle_service` | Current default queues behind active Noofy work conservatively. | P2 active queueing policy refinement. |
| Process compatibility, model residency, and execution profile are not fully separated. | remaining | `runtime/runners`, `runtime/memory`, workflow packages | Current compatibility keys and input profiles are useful but not expressive enough for smarter cross-workflow warm reuse. | P2 signature decomposition. |
| Hardware-independent tests cannot prove real GPU allocator behavior. | partially fixed | validation/docs, platform observers, runner probe | A model-free NVIDIA A10G harness now validates real CUDA allocator telemetry, queue handoff, cancellation, isolated stop, observed release, and shutdown. Large-model and platform-specific confidence still needs hardware work. | See hardware validation section. |

## Remaining P2 Work

P2 is about better estimates, richer compatibility signatures, and smarter
admission decisions on top of the now-stable lifecycle.

### Input-Value-Aware Memory Estimates

Initial slice completed:

- submitted width/height values are extracted from workflow/dashboard bindings
- graph and workflow input defaults are used when submitted values are absent
- batch size is extracted from submitted values, defaults, or graph inputs
- frame count is extracted and used as an effective batch multiplier
- workflow type is inferred from package metadata and graph node types
- precision and VRAM mode options are normalized and applied to heuristic VRAM
  estimates
- extracted features are recorded in Memory Governor developer diagnostics

Remaining semantic extraction work:

- image dimensions beyond simple width/height pairs, including generated image
  size and media-derived dimensions
- iteration count and latent count beyond batch size
- FPS-related frame windows and clip/image sequence length beyond simple frame
  count
- selected checkpoint/model, refiner, VAE, encoder, ControlNet, IPAdapter, and
  other model-bearing inputs
- selected LoRAs and LoRA strength sets when they affect model residency or
  execution memory
- quantization beyond simple precision aliases, attention implementation, tiled
  decode, CPU offload, and similar backend/runtime options
- input media dimensions and channel count when media is part of execution
  memory
- workflow type hints where available, such as text-to-image, image-to-image,
  upscaling, video, audio, LLM, or multimodal

Current fingerprinting protects against many accidental bucket collisions, but
it does not yet understand why a value changes memory. P2 should make estimates
more explainable and less dependent on broad fallback heuristics.

### Publisher And Dashboard Declarations

Add package/dashboard metadata that can declare which bindings materially affect
memory and how they should be interpreted. Noofy should not guess forever from
control names alone.

Useful declaration concepts:

- memory-neutral input
- memory-affecting scalar
- dimension pair
- batch/count/frame input
- model selector
- LoRA/model modifier selector
- precision/runtime-memory mode
- media input whose dimensions should be inspected
- custom estimator hint or conservative class override

Declarations should be advisory and validated. Local observations on the user's
machine remain stronger than publisher hints once enough evidence exists.

### Conservative Fallback For Custom Nodes

Community workflows and custom nodes may hide memory use behind arbitrary node
logic. P2 should use conservative fallback behavior when Noofy cannot classify
custom-node memory effects.

Initial slice completed:

- lower estimate confidence when unclassified custom nodes are present
- carry custom-node count and node types in `workflow_estimate`
- add `custom_node_memory_uncertainty` to developer diagnostics
- apply a small conservative VRAM heuristic factor for custom-node workflows
  until local observations provide better evidence

Remaining work:

- classify known custom-node memory behavior where package/runtime metadata can
  support it
- keep memory-affecting unknown controls in the profile fingerprint
- prefer cleanup, queueing, or warning over optimistic co-residence when
  unknown nodes combine with high memory pressure
- record diagnostics for memory-affecting bindings, not only node/package types
- test private custom-node caches and runner-side cleanup behavior on hardware

### Signature Decomposition

Separate these concepts instead of overloading a single compatibility/profile
key:

- process compatibility signature: whether a workflow can run in the same
  runner process and dependency environment
- model-residency signature: which models/checkpoints/LoRAs/runtime model
  state are already warm and reusable
- execution-profile signature: values that affect peak memory for a run, such
  as resolution, batch, frames, precision, and media dimensions

This decomposition should enable smarter decisions without making dependency
compatibility, warm model reuse, and peak memory evidence blur together.

### Smarter Warm Reuse Across Workflows

After signature decomposition, Noofy should reuse warm residency across
different workflows when they use the same resident model set and compatible
execution environment. The current same-workflow warm reuse fix prevents
double-counting, but P2 should generalize warm reuse safely.

Examples:

- two dashboards around the same checkpoint and VAE
- image-to-image and text-to-image flows that share the same resident base model
- workflows that differ only in prompt structure or non-memory-affecting nodes

### Refined Active-Job Queueing

Current behavior queues conservatively behind active Noofy work. P2 should
allow safe overlap when evidence supports it:

- CPU-only work should not necessarily wait behind unrelated GPU-heavy work.
- Lightweight GPU work may co-reside with active work only when runner,
  adapter, model, and memory evidence make that safe.
- Active queueing policy should avoid starving older queued GPU-heavy work.
- Diagnostics should explain why a run waited or co-resided.

### Better Idle And Resident Memory Attribution

Improve attribution when platform signals allow it:

- distinguish warm model memory from allocator cache where runner telemetry can
  report it
- connect process-tree RAM and per-process VRAM samples to runner descriptors
  more precisely
- record whether reclaimable memory is known from Noofy-owned runners, inferred
  from global deltas, or unattributed
- avoid labeling unattributed VRAM as external unless there is explicit
  non-Noofy process evidence

## Long-Term EngineService Cleanup

`EngineService` should eventually stop serving as a broad application front
door. This is long-term architecture cleanup, not a request to remove the engine
layer.

Important distinction:

- Do not remove the engine layer.
- Keep the engine layer focused on engine-specific responsibilities.
- Gradually migrate responsibilities to the domains that naturally own them.
- Reduce or retire `backend/app/engine/service.py` over time as callers move to
  the appropriate services.

Architectural intent:

- Engine-related code should remain narrowly focused on engine integration,
  adapter contracts, and adapter concerns.
- Workflow execution, run lifecycle management, runner management, memory
  management, model management, and other business responsibilities should
  ultimately live in the domains that best own them.
- New production lifecycle logic should generally be added to the appropriate
  owning domain rather than expanding `EngineService`.
- `EngineService` may remain temporarily as a compatibility layer during
  migration if needed.

Noofy has the full codebase context, so this roadmap intentionally does not
prescribe a specific target module layout. Track the intent and migrate
incrementally as natural domain seams become clear.

## Remaining P3 / UX Work

Backend memory states are now distinct. P3 should make the UI and product copy
equally distinct.

Completed initial slice:

- workflow run page and default canvas run view render distinct messages for
  `waiting_for_active_workflow`, `freeing_previous_models`,
  `unloading_previous_workflow`, `retrying_after_memory_cleanup`,
  `memory_cleanup_failed`, `blocked_external_pressure`,
  `blocked_exceeds_capacity`, and `blocked_unattributed_pressure`
- generic backend "not enough memory" messages are replaced with state-specific
  copy for these precise states
- cleanup failure and true capacity failure are no longer collapsed in the run
  page or canvas run controls
- advanced users can expand developer details with the job ID, queue ID,
  compatibility status, memory status, and memory decision payload

Remaining polish needed:

- review any secondary frontend surfaces that display job/progress/result memory
  states and align them with the same copy map
- add more user-friendly next steps for queueing, freeing memory, retrying, and
  blocked external pressure
- show user-friendly next steps without overpromising that Noofy can reclaim
  memory it does not own
- expand developer-details diagnostics when backend payloads expose decision ID,
  estimate source, signal quality, cleanup timeline, runner ownership summary,
  and release-check reason

## Hardware And Real-World Validation Still Required

Current automated tests are intentionally hardware-independent. They verify
policy, queueing, cleanup state, aliases, finalization, learning, and mocked
platform signals, but they do not prove every real GPU allocator behavior.

Completed NVIDIA A10G model-free pass on 2026-06-02:

- managed CUDA PyTorch runner startup with PyTorch `2.12.0+cu130`
- real 256 MB PyTorch CUDA allocator probe
- NVML process attribution, process-tree RSS, runner allocator telemetry, and
  `before_submit`, `workflow_execution`, and `after_completion` sampling
- prompt-only and seed-only reruns reuse the same warm isolated runner and
  memory-profile fingerprint
- resolution and batch changes produce a separate memory-profile fingerprint
- rapid concurrent Run submissions queue and hand off automatically through
  the queue ID alias with bounded state-driven attempts
- queued cancellation does not submit the canceled record or terminate the
  active job
- isolated runner eviction confirms stopped status and an NVML free-VRAM
  increase from 22,331 MB to 22,589 MB
- managed runner PID is absent after backend shutdown

Still required:

- NVIDIA CUDA large-model warm run
- prompt-only and seed-only rerun with substantial real model VRAM residency
- core `/free` timeline with delayed release from a loaded model
- PyTorch allocated and reserved memory after `/free`
- external GPU pressure from another process
- custom-node private cache behavior after `/free`
- orphan child process cleanup after runner stop/eviction
- packaged desktop UI rapid double-click against a real backend and runner
- Windows GPU process attribution
- Apple Silicon large-model MPS pressure
- Linux PSI behavior under real memory pressure
- multi-GPU/device identity and non-GPU-0 routing

Validation notes should record:

- hardware, driver, OS, and backend
- model/workflow used
- before/during/after snapshots
- runner telemetry payloads when available
- Memory Governor decision IDs
- cleanup timeline and release outcome
- whether observed behavior matched the expected backend state

## Known Limitations To Keep Honest

- ComfyUI `/free` is asynchronous and best-effort.
- `/free` cannot guarantee private custom-node caches are released.
- Live tensor references may remain alive inside the runner or custom node code.
- Global GPU metrics cannot always prove ownership of unattributed VRAM.
- Some production confidence requires hardware validation, not only mocks.
- P0/P1 provides the lifecycle correctness foundation, but P2/P3 are still
  needed for production-quality optimization and UX.
- Queue records, aliases, and watcher state are in-process lifecycle state
  today; process-restart persistence is a separate durability question.

## Completion Criteria

Memory Governor can be considered production-complete when:

- [ ] P2 estimate enrichment is implemented and tested.
- [ ] Publisher/dashboard memory-affecting bindings are implemented and tested.
- [ ] Process compatibility, model-residency signatures, and execution-profile
      signatures are implemented.
- [ ] Warm reuse across compatible workflows sharing resident model sets is
      implemented safely.
- [ ] Active queueing policy is refined for CPU-only and lightweight work.
- [ ] Frontend memory states are polished and do not collapse distinct backend
      failures into generic copy.
- [ ] CUDA validation passes with real large models.
- [ ] Platform validation notes are added for Windows, Apple Silicon, and Linux.
- [ ] Multi-GPU and non-GPU-0 routing behavior is validated or explicitly scoped.
- [ ] `MEMORY_GOVERNOR.md` and this remaining-work file are updated together so
      they do not contradict each other.
