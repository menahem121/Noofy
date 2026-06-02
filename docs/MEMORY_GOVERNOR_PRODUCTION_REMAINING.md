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
- fourth P2 model-selection slice: checkpoint/model, refiner, VAE, encoder,
  ControlNet, IPAdapter, and LoRA selections are extracted from workflow
  inputs, dashboard bindings, graph nodes, and required-model fallback
  metadata; LoRA count and available strengths now feed conservative heuristic
  adjustments and profile fingerprints
- fifth P2 signature-decomposition groundwork slice: process compatibility,
  model residency, and execution profile signatures are computed separately,
  exposed in Memory Governor diagnostics, and persisted in local learning
  observations/summaries; same-runner warm reuse now keys on the selected
  runner's model-residency signature while execution-shape changes remain in
  the execution-profile estimate lane and are checked as incremental same-runner
  pressure where runner residency evidence exists
- sixth P2 residency-pressure slice: cleanup planning now scores useful
  overlap before reclaiming idle residency, including same checkpoint, VAE,
  encoder, ControlNet/IPAdapter, LoRA set, open leases, queued demand, recent
  use, and reload-cost estimate; same-runner useful-overlap changes delegate
  intra-runner reuse to ComfyUI first, while V1 fallback cleanup capabilities
  remain runner-level `/free` and isolated runner eviction
- first P3 frontend memory-state slice: the workflow run page and default
  canvas run view now render distinct user-facing copy for queueing, cleanup,
  retry, cleanup failure, external pressure, capacity failure, and unattributed
  pressure; generic backend "not enough memory" messages are replaced with the
  more precise `memory_status.state` copy where safe
- first real CUDA validation pass: the managed NVIDIA A10G harness now covers
  allocator telemetry, model-free warm prompt/seed reruns, profile-changing
  reruns, rapid queue handoff, queued cancellation, isolated runner eviction,
  observed VRAM release, and managed-runner shutdown
- second real CUDA validation pass: bundled SD 1.5 text-to-image completed on
  the NVIDIA A10G with substantial model VRAM residency; prompt-only and
  seed-only reruns reused the warm runner and same profile bucket, while a
  768x768 rerun kept the same model-residency signature, changed the
  memory/execution profile, and completed
- external GPU pressure probe: a separate process consuming about 19 GB VRAM
  caused Noofy to block safely as `blocked_unattributed_pressure`; explicit
  `blocked_external_pressure` remains limited to snapshots with actual
  non-Noofy process evidence

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
| Same workflow rerun double-counted the warm runner's resident VRAM. | fixed | `runtime/memory`, `runtime/runners`, `runs/orchestrator` | Same-runner warm reuse now uses runner-local model-residency signatures when available, so prompt/seed edits and execution-shape changes can reuse already loaded models without treating them as a new model state. | Continue improving estimates; do not assume loaded model memory is reusable across different runner processes. |
| Prompt-only edits created a new memory bucket. | fixed | `engine/memory_observation`, `runs/orchestrator` | Text prompt controls are treated as memory-neutral when bindings identify them as prompt/text fields. | Add publisher/dashboard-declared memory-affecting metadata so unusual controls are classified explicitly. |
| Seed-only edits created a new memory bucket. | fixed | `engine/memory_observation`, workflow package bindings | Seed widgets are treated as memory-neutral. | Same metadata follow-up as prompt controls for packages with custom seed controls. |
| Memory-changing settings could inherit smaller-run evidence. | partially fixed | `engine/memory_observation`, `runtime/memory` | Non-neutral inputs/options are fingerprinted. P2 now semantically extracts width/height, batch, frame count, workflow type, precision, VRAM mode, model-bearing selections, and LoRA strengths/counts into heuristic estimates. Static graph model/LoRA selections also contribute to profile fingerprints. Compatible fallback learning is now filtered by model-residency signature, so model/LoRA changes do not inherit stale evidence from a different loaded model set. | Implement publisher-declared memory-affecting bindings and richer runtime-option/media extraction. |
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
| Process compatibility, model residency, and execution profile are not fully separated. | partially fixed | `runtime/memory`, `runtime/runners`, `engine/memory_observation`, workflow packages | P2 now computes separate diagnostic/learning signatures for process compatibility, model residency, and execution profile. Same-runner warm reuse uses the model-residency signature, while resolution/batch/frame/precision/VRAM mode changes stay in execution-profile diagnostics and estimates. Active queueing and cross-workflow learning remain conservative. | Continue using signatures for estimates and runner-local diagnostics; do not plan cross-runner loaded-model VRAM reuse without engine-internal support. |
| Hardware-independent tests cannot prove real GPU allocator behavior. | partially fixed | validation/docs, platform observers, runner probe | NVIDIA A10G validation now covers model-free lifecycle behavior, real CUDA allocator telemetry, queue handoff, cancellation, isolated stop/release, shutdown, and a bundled SD 1.5 large-model warm rerun. Platform-specific and core `/free` confidence still needs hardware work. | See hardware validation section. |

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
- checkpoint/model, refiner, VAE, encoder, ControlNet, IPAdapter, and LoRA
  selections are extracted from workflow inputs, dashboard bindings, graph
  nodes, and required-model fallback metadata
- active LoRA count and available model/CLIP strengths receive modest
  conservative heuristic adjustments
- static graph model/LoRA selections contribute to input-profile fingerprints
  so residency-changing graph edits do not inherit an unrelated local-learning
  bucket
- extracted features are recorded in Memory Governor developer diagnostics

Remaining semantic extraction work:

- image dimensions beyond simple width/height pairs, including generated image
  size and media-derived dimensions
- iteration count and latent count beyond batch size
- FPS-related frame windows and clip/image sequence length beyond simple frame
  count
- richer model metadata where available, including selected file sizes,
  quantization, architecture families, and model-specific residency behavior
- custom-node model loaders and modifier stacks beyond the conservative
  filename/binding heuristics
- quantization beyond simple precision aliases, attention implementation, tiled
  decode, CPU offload, and similar backend/runtime options
- input media dimensions and channel count when media is part of execution
  memory
- workflow type hints where available, such as text-to-image, image-to-image,
  upscaling, video, audio, LLM, or multimodal

Current fingerprinting protects against many accidental bucket collisions and
now records common model-bearing semantics. P2 should keep making estimates
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
  state are loaded by a particular runner and may be warm-reused only by that
  same runner
- execution-profile signature: values that affect peak memory for a run, such
  as resolution, batch, frames, precision, and media dimensions

Initial slice completed:

- process compatibility, model residency, and execution profile signatures are
  computed as separate hashes
- signatures are exposed in `workflow_estimate` and `memory_signatures`
  developer diagnostics
- signatures are persisted into local memory observations and summaries
- prompt/seed changes keep the signatures stable when model and execution
  values are unchanged
- model/LoRA changes alter the model-residency signature without changing the
  process-compatibility signature
- resolution/batch/profile changes alter the execution-profile signature
  without changing model residency
- different workflows with the same model set can now report the same
  model-residency signature for diagnostics and learning

Remaining work:

- keep model-residency signatures runner-local for admission: same selected
  runner plus same loaded model set may reuse warm residency; different
  runners/processes/environments cannot reuse loaded model memory in V1
- use execution-profile signatures to compare local learning across workflows
  without conflating dependency compatibility and peak-memory shape
- add publisher/dashboard declarations so signature payloads do not rely only
  on heuristic binding inference
- validate signatures against more real large-model CUDA residency before using
  them for broader cross-workflow runner selection or more permissive
  co-residence decisions

This decomposition should enable smarter decisions without making dependency
compatibility, warm model reuse, and peak memory evidence blur together.

### Same-Runner Warm Residency Reuse

Noofy should reuse warm residency only inside the same selected runner process
when the resident model set is compatible. The model-residency signature can
show that two workflows refer to the same loaded model set, but it is not
evidence that VRAM can be shared across different ComfyUI runners, processes,
dependency environments, or devices.

Examples:

- prompt-only and seed-only reruns on the same runner
- resolution, batch, frame, precision, or VRAM-mode changes on the same runner
  when selected models/LoRAs are unchanged
- two workflows selected onto the same compatible runner and using the same
  checkpoint/VAE/encoder/ControlNet/IPAdapter/LoRA set

Out of scope for V1:

- treating a checkpoint already loaded in runner A as reusable by runner B
- assuming two isolated runners can share loaded model tensors
- planning cross-runner loaded-model VRAM reuse without modifying or extending
  engine internals to make that sharing explicit and safe

### Residency Pressure Cleanup

Implemented V1 behavior:

- cleanup planning keeps active, reserved, submitting, output-stream, evicting,
  and waiting-release runners out of cleanup candidates
- candidate diagnostics expose reuse value and useful overlap, including same
  checkpoint, VAE, encoder, ControlNet/IPAdapter, LoRA set, open lease, queued
  demand, recent use, reload-cost estimate, and reclaim estimate
- same-runner compatible execution with useful model overlap is preferred before
  runner-level cleanup, so ComfyUI can reuse cached checkpoint/encoder/VAE
  outputs and replace changed LoRA/model-modifier branches internally
- fully unreferenced or low-reuse idle residency is reclaimed before
  open-view, queued-needed, or useful-overlap residency when it can satisfy the
  pressure
- obsolete LoRAs/model modifiers are cleanup-pressure signals when no active,
  queued, or open workflow still needs them
- current ComfyUI cleanup capability is runner-level `/free`; isolated runners
  can be stopped/evicted through the runner coordinator

Future adapter capability work:

- precise per-LoRA/per-model cleanup is not a guaranteed ComfyUI feature
- enable `per_lora_unload` or `per_model_unload` only for adapters/runtimes
  that expose a stable public unload-by-reference capability without modifying
  ComfyUI internals
- clear precise residency metadata only after observed release confirmation
- if precise cleanup remains unsupported, obsolete LoRA/model-modifier changes
  must remain scoring/diagnostic signals rather than automatic whole-runner
  eviction reasons

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

Completed NVIDIA A10G model-backed pass on 2026-06-02:

- bundled `text_to_image_v0` ran with
  `v1-5-pruned-emaonly-fp16.safetensors`
- model-residency diagnostics identified the selected checkpoint
- initial 512x512 generation completed with about 2.5 GB observed execution
  peak VRAM
- prompt-only and seed-only reruns reused the same warm runner and the same
  memory-profile fingerprint
- 768x768 rerun changed the memory-profile fingerprint and execution-profile
  signature, completed successfully, and reached about 4.0 GB observed
  execution peak VRAM
- validation artifact:
  `.noofy-runtime/validation/sd15-large-model-validation.json`

Completed NVIDIA A10G external-pressure probe on 2026-06-02:

- a separate non-Noofy Python process allocated about 19 GB VRAM
- Noofy blocked the bundled text-to-image workflow instead of attempting to
  reclaim that memory
- backend state was `blocked_unattributed_pressure` with reason
  `insufficient_vram_margin`
- this is safe and honest for v1, but explicit `blocked_external_pressure`
  still requires admission snapshots to carry non-Noofy process evidence
- validation artifact:
  `.noofy-runtime/validation/external-pressure-validation.json`

Still required:

- core `/free` timeline with delayed release from a loaded model
- PyTorch allocated and reserved memory after `/free`
- explicit external-process attribution in admission snapshots where platform
  process evidence is available
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
- Global `/free` clears broad ComfyUI model/cache state. Noofy should not use it
  just because a same-runner LoRA/model-modifier changed while useful
  base-model residency still overlaps the requested workflow.
- Precise per-LoRA/per-model cleanup is a future adapter capability, not a
  guaranteed ComfyUI feature. In V1, Noofy only claims runner-level `/free` and
  isolated runner eviction unless an adapter proves a safer mode without
  ComfyUI internals.
- Live tensor references may remain alive inside the runner or custom node code.
- Global GPU metrics cannot always prove ownership of unattributed VRAM.
- Some production confidence requires hardware validation, not only mocks.
- Loaded model memory is runner/process-local in V1. Matching
  `model_residency_signature` values across different runners are useful for
  diagnostics and scheduling, but they do not prove that VRAM can be shared or
  reused across processes.
- P0/P1 provides the lifecycle correctness foundation, but P2/P3 are still
  needed for production-quality optimization and UX.
- Queue records, aliases, and watcher state are in-process lifecycle state
  today; process-restart persistence is a separate durability question.

## Completion Criteria

Memory Governor can be considered production-complete when:

- [ ] P2 estimate enrichment is implemented and tested.
- [ ] Publisher/dashboard memory-affecting bindings are implemented and tested.
- [x] Process compatibility, model-residency signatures, and execution-profile
      signatures are implemented.
- [x] Same-runner warm reuse uses model-residency signatures safely, while
      execution-profile changes update estimates without creating a new loaded
      model state.
- [x] Idle residency cleanup uses signature payloads and reuse-value
      diagnostics to prefer low-reuse cleanup before useful warm residency,
      without assuming cross-runner loaded-model VRAM reuse.
- [ ] Future adapters with stable public precise unload support can expose
      per-LoRA/per-model cleanup behind observed release confirmation.
- [ ] Active queueing policy is refined for CPU-only and lightweight work.
- [ ] Frontend memory states are polished and do not collapse distinct backend
      failures into generic copy.
- [x] CUDA validation passes with real large models.
- [ ] Platform validation notes are added for Windows, Apple Silicon, and Linux.
- [ ] Multi-GPU and non-GPU-0 routing behavior is validated or explicitly scoped.
- [ ] `MEMORY_GOVERNOR.md` and this remaining-work file are updated together so
      they do not contradict each other.
