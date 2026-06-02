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
- adaptive async `/free` release polling with timeout and timeline diagnostics
- core warm residency retained until observed release is confirmed
- observer-unavailable cleanup fails closed instead of claiming release
- job-specific peak learning, input-profile buckets, and observation dedupe
- prompt and seed edits treated as memory-neutral when identified as text/seed
  controls
- memory-changing profile inputs kept separate from prompt/seed-only reruns
- explicit cleanup and blocking states in backend payloads, including
  `memory_cleanup_failed`, `blocked_external_pressure`,
  `blocked_exceeds_capacity`, and `blocked_unattributed_pressure`

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
| Memory-changing settings could inherit smaller-run evidence. | partially fixed | `engine/memory_observation`, `runtime/memory` | Non-neutral inputs/options are fingerprinted, so resolution/batch/model-like values can split buckets. P2 still needs semantic extraction and declared bindings. | Implement input-value-aware estimate enrichment and publisher-declared memory-affecting bindings. |
| Low free VRAM from an idle Noofy runner caused immediate blocking. | fixed | `runtime/memory`, `runtime/runners` | Idle Noofy-owned memory is treated as reclaimable and cleanup waits for observed release before admission continues. | Improve attribution of idle/resident memory where platform signals allow it. |
| Active workflow followed by another run could block or be killed. | fixed | `runs/queue_service`, `runs/lifecycle_service`, `runtime/runners` | Active work queues by default; cleanup skips active runners. | Refine queueing so unrelated CPU-only/light work does not always wait behind GPU-heavy work. |
| `Not enough memory` was too early in the admission path. | partially fixed | `runtime/memory`, `runs/orchestrator`, frontend | Backend now attempts reuse, queue, cleanup, release polling, and retry before terminal memory failure. Frontend still needs more distinct copy. | P3 UX polish for each backend memory state. |
| Workflow queue required manual handoff and could strand records. | fixed | `runs/lifecycle_service` | Dispatch wakes automatically and processes eligible queue records before runner-start drains. | Continue to keep queue state observable in diagnostics. |
| Runner-start queue could strand records behind active work. | fixed | `runtime/runners/lifecycle_service`, `runs/lifecycle_service` | Runner-start draining is tied to run dispatch and runner state-change notifications. | No immediate follow-up beyond real-runner validation. |
| Queue record could be removed before successful adapter submission. | fixed | `runs/queue_service`, `runs/orchestrator` | Records stay active through handoff and submission; terminal aliases are retained in a bounded ledger. | No immediate follow-up. |
| Queue ID to job ID alias could break progress, result, output, logs, SSE, or cancel. | fixed | `runs/job_service`, `runs/result_service`, `runs/queue_service` | Public queue IDs resolve to canonical submitted jobs after submission and remain available while retained. | Consider persistence if queue/result aliases must survive backend restart. |
| Requeue could busy-loop. | fixed | `runs/queue_service`, `runs/lifecycle_service` | Requeues use attempt counts, one attempt per dispatch epoch, max records per wake, backoff, and failure after repeated transient handoff failures. | No immediate follow-up. |
| Two rapid Run clicks could both submit to the same runner. | fixed | `runtime/runners/supervisor`, `runs/orchestrator` | Submission reservations are atomic and serialize handoff. | Validate against real UI/backend double-click timing. |
| Cleanup could unload a runner reserved by another request. | fixed | `runtime/runners/supervisor`, `runtime/memory/service` | Cleanup uses eviction reservations and rechecks active/reserved state. | No immediate follow-up. |
| Cancellation could lose the race during `adapter.run_workflow(...)`. | fixed | `runs/job_service`, `runs/orchestrator`, `runtime/runners/supervisor` | Cancellation requests during adapter submission wait for canonical job registration, then cancel the submitted job through the alias. | No immediate follow-up. |
| Terminal finalization could run twice. | fixed | `runs/result_service` | Per-job async locks and cached terminal outcomes guard sampling completion, learning, retry, history, gallery, runner release, and dispatch notifications. | No immediate follow-up. |
| Terminal polling could call the adapter again after a cached terminal outcome. | fixed | `runs/result_service`, `runs/job_service` | Cached terminal outcomes are returned through result/progress paths. | No immediate follow-up. |
| Core warm residency could be cleared before observed release. | fixed | `runtime/memory/service`, `runtime/runners/supervisor` | Core residency is cleared only after release polling confirms safe memory. Timeout/unavailable marks `release_failed`. | Validate with real CUDA `/free` and PyTorch allocator behavior. |
| `/free` HTTP success was treated as actual release. | fixed | `runtime/memory/memory_governor`, `runtime/memory/service` | `/free` is now only an acknowledgment; async polling confirms release or fails closed. | Real hardware `/free` timeline validation remains. |
| Cleanup failure was reported only as generic `blocked_by_memory`. | fixed | `runtime/memory/service`, API models | Compatibility `EngineJob.status` can remain `blocked_by_memory`, but `memory_status.state` reports `memory_cleanup_failed`. | P3 UI must avoid collapsing this into generic capacity failure. |
| Local learning reused stale descriptor peaks for sampled jobs. | fixed | `engine/memory_observation` | Sampled jobs learn selected job-window peaks or no peak evidence; descriptor fallback is not used when a sampled job lacks selected peak evidence. | No immediate follow-up. |
| Later larger runs might not raise runner/profile peak evidence. | fixed | `engine/memory_observation`, `runtime/runners/supervisor`, `LocalMemoryLearningStore` | Runner descriptor peaks and local summaries update as maxima and dedupe by non-null job ID. | P2 semantic profile extraction still needed so larger profiles are classified reliably. |
| Observer-unavailable cleanup could claim success or clear residency. | fixed | `runtime/memory/service`, `runtime/memory/memory_governor` | Unavailable observation returns `memory_cleanup_failed`, preserves attribution, and marks release failure. | No immediate follow-up. |
| Runtime/memory exposed runs-owned queue internals. | fixed | `runtime/memory/service`, `runs/job_service`, `runs/lifecycle_service` | Queue handoff/progress/cancel ownership has moved back to `runs/`; memory owns admission and cleanup only. | Keep future queue work out of memory modules. |
| EngineService accumulated lifecycle ownership. | partially fixed | Composition root, `runs/`, `runtime/runners/`, `runtime/memory` | P0/P1 lifecycle behavior lives in domain services. `EngineService` still remains a broad application front door and compatibility facade. | Track long-term EngineService cleanup below. |
| Compatibility helpers for unreleased internals could outlive the migration. | partially fixed | `EngineService`, tests, API/dependency wiring | Some migration proxies remain for compatibility while callers move to domain services. | Retire proxies when call sites are migrated. |
| CPU-only/lightweight work may wait behind unrelated GPU-heavy work. | remaining | `runtime/memory`, `runtime/runners`, `runs/lifecycle_service` | Current default queues behind active Noofy work conservatively. | P2 active queueing policy refinement. |
| Process compatibility, model residency, and execution profile are not fully separated. | remaining | `runtime/runners`, `runtime/memory`, workflow packages | Current compatibility keys and input profiles are useful but not expressive enough for smarter cross-workflow warm reuse. | P2 signature decomposition. |
| Hardware-independent tests cannot prove real GPU allocator behavior. | deferred | validation/docs, platform observers, runner probe | Automated tests cover logic and mocks; production confidence needs real hardware validation. | See hardware validation section. |

## Remaining P2 Work

P2 is about better estimates, richer compatibility signatures, and smarter
admission decisions on top of the now-stable lifecycle.

### Input-Value-Aware Memory Estimates

Implement semantic extraction of memory-affecting values from workflow packages,
dashboard controls, and run options:

- image dimensions and generated image size
- batch size, iteration count, and latent count
- video frame count, FPS-related frame windows, and clip/image sequence length
- selected checkpoint/model, refiner, VAE, encoder, ControlNet, IPAdapter, and
  other model-bearing inputs
- selected LoRAs and LoRA strength sets when they affect model residency or
  execution memory
- precision, quantization, VRAM mode, attention implementation, tiled decode,
  CPU offload, and similar backend/runtime options
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
logic. P2 should add conservative fallback behavior when Noofy cannot classify
custom-node memory effects:

- lower estimate confidence when unclassified custom nodes are present
- keep memory-affecting unknown controls in the profile fingerprint
- prefer cleanup, queueing, or warning over optimistic co-residence when
  unknown nodes combine with high memory pressure
- record diagnostics that explain which nodes or bindings caused conservative
  treatment

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

Polish needed:

- distinct UI messages for `waiting_for_active_workflow`
- distinct UI messages for `freeing_previous_models`
- distinct UI messages for `unloading_previous_workflow`
- distinct UI messages for `retrying_after_memory_cleanup`
- distinct UI messages for `memory_cleanup_failed`
- distinct UI messages for `blocked_external_pressure`
- distinct UI messages for `blocked_exceeds_capacity`
- distinct UI messages for `blocked_unattributed_pressure`
- avoid collapsing cleanup failure and true capacity failure into generic
  "Not enough memory"
- explain when Noofy is queueing, freeing memory, retrying, or blocked by
  external pressure
- show user-friendly next steps without overpromising that Noofy can reclaim
  memory it does not own
- expose developer-details diagnostics for advanced troubleshooting, including
  decision ID, estimate source, signal quality, cleanup timeline, runner
  ownership summary, and release-check reason

## Hardware And Real-World Validation Still Required

Current automated tests are intentionally hardware-independent. They verify
policy, queueing, cleanup state, aliases, finalization, learning, and mocked
platform signals, but they do not prove every real GPU allocator behavior.

Required validation items:

- NVIDIA CUDA large-model warm run
- prompt-only and seed-only rerun with real VRAM residency
- `/free` timeline with delayed release
- PyTorch allocated and reserved memory after `/free`
- external GPU pressure from another process
- custom-node private cache behavior after `/free`
- orphan child process cleanup after runner stop/eviction
- rapid double-click on a real backend and runner
- queued handoff on a real runner
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
