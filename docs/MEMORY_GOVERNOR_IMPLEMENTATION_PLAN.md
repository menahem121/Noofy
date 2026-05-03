# Memory Governor Implementation Plan

Date: 2026-05-03

Status: Accepted v1 direction / implementation source

This plan supersedes the older "one GPU-heavy runner only" v1 strategy as the complete policy. The single GPU-heavy rule remains the safe fallback, but v1 should already include an intelligent Memory Governor that can keep multiple runners warm when the evidence is strong enough.

The goal is product behavior that feels fast without becoming mysterious or fragile:

- avoid GPU crashes and memory errors
- avoid unnecessary model and runner reloads
- keep recent workflows quick to relaunch
- allow multiple warm runners on machines that can really support them
- learn each user's machine over time so repeated workflows become faster and more predictable
- fall back to conservative behavior when signals are weak
- recover cleanly after memory failures
- explain decisions in beginner-friendly language

## Core Product Rules

- Opening or switching workflow tabs must not start, stop, load, or unload heavy runners.
- Running a workflow is the moment where Noofy may start runners, load models, queue work, or evict warm runners.
- The frontend reports user intent, such as workflow view open/close and cancel. The backend remains authoritative for runner and memory decisions.
- No active run is killed automatically to make room for another workflow. A normal Cancel action remains available.
- Unknown memory cost is treated as high risk.
- Multiple warm GPU runners are allowed only through the Memory Governor, never through a simple "free VRAM exists" rule.
- Co-resident warm runners do not imply unrestricted parallel GPU-heavy execution. V1 may keep multiple runners ready while still serializing heavy GPU work.
- Runtime isolation remains unchanged. Co-resident runners are separate isolated processes with separate dependency environments and runner workspaces when their fingerprints differ.
- Creator `.noofy` memory observations are starting hints, not machine-specific truth.
- Local observations on the user's own device are more trustworthy than creator observations and should progressively replace them.
- Learned memory behavior improves confidence, but never becomes a perfect guarantee.

## Local Memory Learning Principle

Noofy should not make a memory decision once and freeze it forever. The Memory Governor learns the user's machine over time.

At first, Noofy starts with approximate confidence. It may use creator-side `.noofy` metrics, model size, workflow type, resolution, batch size, and conservative heuristics. This is enough to make a cautious first decision, but not enough to trust aggressive co-residence.

Each local run gives Noofy more evidence:

- a successful run under similar settings raises confidence for that workflow, runner, backend, and machine profile
- repeated successful runs make Noofy more comfortable keeping the runner warm, co-residing it with safe neighbors, or avoiding unnecessary eviction
- a memory failure lowers confidence and makes Noofy prefer eviction, cleanup, or blocking before repeating the same risky decision
- retry outcomes teach Noofy whether cleanup actually helps on this machine

Frequently used workflows should therefore feel faster and more reliable over time because Noofy learns their real memory behavior on this specific device. The learning is still probabilistic: local history improves confidence, but Noofy must still account for changed settings, other apps using the GPU, driver behavior, fragmentation, and temporary memory spikes.

## Memory Metric Storage Decision

Decision: learned local memory metrics are stored in Noofy's local app data, not written back into imported `.noofy` packages during normal use.

Package-level `.noofy` memory metrics are creator/export-time observations. They are useful as initial hints, but they describe the creator's machine and tested settings. They must not be treated as authoritative requirements for another user's machine.

Local learned metrics are machine-specific. They belong in mutable local state owned by the app, keyed by workflow/capsule identity, runner fingerprint, backend, machine profile, model set, and similar input settings. The Memory Governor should trust these local observations more than creator-side observations once local evidence exists.

Normal workflow runs must not mutate `.noofy` packages, capsule locks, or imported package records with learned local memory history. This keeps packages portable, preserves trust/signing semantics, avoids noisy package churn, and reduces privacy risk from leaking device details or usage patterns.

If Noofy later supports re-export after local use, it may offer an explicit export option to include anonymized local observations as advisory metadata. Such exported observations must be clearly marked as machine-specific hints and must remain weaker than the recipient's own local history.

Short rule:

- `.noofy` describes the workflow and what the creator/exporter observed.
- Local app data describes what this user's machine has learned.
- The Memory Governor trusts local app data first, but still treats it as evidence rather than a guarantee.

## 1. Compatibility Reality Between Workflows

Runner compatibility means two workflows can execute inside the same runner process without changing the dependency environment, runner workspace, custom-node set, launch configuration, or model-view compatibility contract.

Expected compatibility in practice:

| Workflow relationship | Expected runner sharing | Notes |
| --- | --- | --- |
| Noofy Verified workflows from the same pack | Frequent | Packs can be authored to share a runtime profile, dependency lock, custom-node workspace, and model-view contract. |
| Core-only workflows | Frequent | They usually depend only on the pinned ComfyUI profile and default nodes. |
| Community workflows from the same ecosystem | Medium | They may share popular custom nodes, but version and dependency drift still matters. |
| Random community workflows | Low to medium | Custom nodes, dependency pins, and model-view expectations often differ. |
| Workflows with conflicting custom nodes or dependencies | Not compatible | They must use different runner workspaces or dependency environments. |

When two workflows cannot share a runner, Noofy can still keep both runners warm if the Memory Governor decides their combined idle footprint and predicted next-run peak fit safely on the machine. The decision is about co-residence, not compatibility.

## 2. Runner Memory Classes

Every prepared workflow and resident runner has a memory class plus confidence metadata. The class is a decision input, not a guarantee.

| Class | Typical workflows | V1 co-residence policy |
| --- | --- | --- |
| `gpu_heavy` | SDXL/Flux/video, large ControlNet chains, high-resolution generation, large batch, unknown large checkpoints | One by default. Co-resident with another heavy only on large GPUs with high-confidence local observations and large margin. |
| `gpu_medium` | SD1.5 generation at moderate settings, img2img, many upscalers, moderate ControlNet | Can co-reside with light; can co-reside with heavy or medium only with reliable estimates and enough margin. |
| `gpu_light` | Lightweight post-processing, preview, metadata, small image transforms, tiny utility models | Usually allowed to co-reside if RAM/VRAM pressure is low. |
| `cpu_only` | CPU-side transforms, file preparation, metadata, non-GPU utility flows | Allowed with GPU runners unless system RAM pressure is high. |
| `unknown` | Missing estimates, first run of ambiguous workflow, community workflow with untrusted or incomplete metadata | Treated as `gpu_heavy` and high risk until local observations prove otherwise. |

Memory class fields should record:

- class value
- source: `declared`, `creator_observed`, `local_observed`, `heuristic`, or `unknown`
- confidence: `high`, `medium`, `low`
- estimated peak VRAM and RAM
- observed idle footprint when warm
- observed load/start peak
- observed execution peak for each relevant input profile
- last memory error timestamp, if any

## 3. Signals Used For Decisions

Reliable signals:

- VRAM total
- current VRAM free/used when the backend can query it
- current RAM free/used
- memory pressure state on platforms that expose it
- backend type: CUDA, MPS, DirectML, CPU
- runner state: running, idle, idle-warm, queued, stopping
- runner compatibility fingerprint
- local run history for the same workflow/input profile/backend
- repeated local success count under similar settings
- local memory errors and retry outcomes
- observed runner idle footprint and execution peak

Useful but approximate signals:

- creator/export `.noofy` hardware observations
- model file sizes and model family hints
- workflow type: generation, upscale, ControlNet, video, utility
- resolution and batch size
- sampler/step count when available
- custom-node package identity and known memory behavior
- current number of warm runners
- estimated duration

Uncertain signals:

- VRAM fragmentation
- temporary allocator spikes
- arbitrary community custom-node behavior
- other applications using GPU memory after Noofy takes a snapshot
- differences between creator hardware and user hardware
- PyTorch/ComfyUI backend-specific cache behavior
- driver or OS-specific delayed memory release

The Memory Governor must use a confidence score. It should never promote an uncertain estimate into a high-confidence decision just because the machine appears to have free memory.

## 4. V1 Co-Residence Policy

Noofy may keep multiple runners warm only if all of these are true:

- no active job would be interrupted
- all co-resident runners are idle or explicitly allowed to remain warm
- memory classes are compatible under the matrix below
- current RAM/VRAM has enough safety margin
- the next workflow has a high- or medium-confidence estimate
- local learning does not show recent memory failure for the same workflow, backend, machine profile, or similar input settings
- no recent memory instability exists on this machine/profile
- Noofy has a bounded way to evict an idle runner if pressure appears
- diagnostics can explain why co-residence was allowed

Co-residence matrix:

| Combination | V1 policy |
| --- | --- |
| `gpu_heavy + gpu_heavy` | Deny by default. Allow only on 24 GB+ CUDA-class GPUs or equivalent, with high-confidence local observations for both, no recent memory errors, and large margin. |
| `gpu_heavy + gpu_medium` | Allow only with reliable estimate and large margin. Prefer eviction on 12 GB or smaller GPUs. |
| `gpu_heavy + gpu_light` | Allow when current free memory remains above margin and no instability is present. |
| `gpu_medium + gpu_medium` | Allow on 16 GB+ or when both have strong local observations and safe margin. |
| `gpu_medium + gpu_light` | Usually allow if margin remains healthy. |
| `gpu_light + gpu_light` | Allow unless system pressure is high. |
| `cpu_only + GPU runner` | Allow unless RAM pressure is high. |
| `unknown + anything` | Treat unknown as heavy and high risk. Deny extra co-residence unless the other runner is CPU-only or trivially light and margins are large. |

V1 execution policy can remain stricter than warm policy. For example, Noofy may keep two heavy runners warm on a 24 GB GPU after strong observations, while still queueing simultaneous heavy jobs.

## 5. Safety Margins

Margins are deliberately conservative. They should be configurable in developer settings later, but product defaults should prioritize reliability.

CUDA / dedicated GPU suggested minimum free margin after predicted allocation:

| VRAM | Minimum free margin |
| --- | --- |
| 6-8 GB | max(30%, 2 GB) |
| 10-12 GB | max(25%, 2.5 GB) |
| 16 GB | max(20%, 3.5 GB) |
| 24 GB | max(18%, 4 GB) |
| 48 GB+ | max(12%, 6 GB) |

MPS / Apple Silicon unified memory:

- treat RAM and GPU memory as one shared pressure pool
- reserve at least 25% system memory or 8 GB, whichever is smaller but still leaves normal app responsiveness
- avoid co-resident heavy runners when swap or high memory pressure is observed

DirectML / Windows GPU backends:

- start with CUDA-like margins plus an extra caution factor until local observations prove stable
- downgrade confidence after driver reset, device lost, or allocation errors

CPU-only:

- use RAM pressure and swap pressure instead of VRAM
- allow co-residence more freely, but avoid pushing the system into swap

## 6. Memory Decision Flow

Before starting or reusing a runner, Noofy builds a decision record:

1. Identify requested workflow, input profile, runner fingerprint, and memory class.
2. Collect machine memory snapshot.
3. Collect resident runner snapshots.
4. Find compatible resident runner, if any.
5. Estimate requested load and execution peak.
6. Score confidence: repeated local success > single local success > creator observation > heuristic > unknown.
7. Decide:
   - reuse compatible runner
   - keep current idle runners and start another runner
   - evict one or more idle runners first
   - queue because another job is active
   - block because memory risk is too high
8. Emit a structured diagnostic with inputs, decision, and user-facing reason.

Confidence must be scoped to similar conditions. A workflow that was stable at 1024x1024 batch 1 is not automatically high-confidence at 2048x2048 batch 4. If inputs, model selection, backend, runtime profile, or machine profile change materially, Noofy should lower confidence and rebuild it from new evidence.

Decision outcomes:

- `reuse_runner`
- `start_co_resident`
- `evict_then_start`
- `queue_pending_switch`
- `queue_pending_memory`
- `wait_for_memory_release`
- `retry_after_memory_cleanup`
- `blocked_by_memory`

## 7. Eviction Policy

Noofy evicts warm runners when:

- an incompatible workflow needs memory
- estimated memory is insufficient
- actual memory pressure rises
- a runner is idle past lease/cooldown policy
- a workflow view closes and no likely reuse remains
- a memory error recently occurred
- local history shows this workflow or a similar input profile is memory-risky on this machine
- a higher-priority user action needs the GPU
- the app is shutting down

Eviction order:

1. Idle runners with no open workflow lease.
2. Least recently used idle runners.
3. Runners with low predicted reuse.
4. Runners with high idle footprint.
5. Runners incompatible with currently open workflows.
6. Runners associated with recent memory errors.
7. Runners whose local history says they are unlikely to be reused soon.
8. Never kill an active job without explicit user cancellation.

An eviction record should include:

- runner ID and fingerprint
- memory class
- idle time
- lease state
- estimated freed RAM/VRAM
- decision reason
- stop duration
- memory released or timeout result

## 8. Memory Release Checks

After stopping a runner for memory, Noofy should wait for bounded release:

- poll VRAM/RAM for a short window
- accept release when memory reaches the expected safe threshold
- time out with diagnostics rather than waiting indefinitely
- continue only when the next start has enough margin or the policy explicitly allows a cautious attempt

If memory does not release:

- report `waiting_for_memory_release` while polling
- then either retry with stronger cleanup or surface `blocked_by_memory`
- record driver/backend details for developer diagnostics

## 9. Recovery After Memory Error

If a workflow likely fails due to memory:

1. Classify the error as memory-related when possible: CUDA OOM, MPS allocation failure, DirectML device allocation failure, process killed by memory pressure, or model load allocation failure.
2. Stop idle runners.
3. Wait for memory release.
4. Retry once if safe.
5. If retry fails, show a clear user error.
6. Record local failure history.
7. Lower future confidence and prefer eviction before future attempts.

Safe to retry:

- normal generation or transformation with no irreversible side effects
- no external paid service
- no destructive overwrite
- no previous retry for this same run
- memory is now above minimum margin
- error signature is likely memory, not an arbitrary custom-node bug

Not safe to retry:

- destructive export or overwrite
- long-running job whose failure cause is unclear
- repeated memory failure
- process crash with uncertain cause
- workflow with side effects outside Noofy's output directory

## 10. Local Learning Policy

Noofy records local memory observations after each run. These local observations gradually replace creator `.noofy` metrics for decisions on the user's machine.

This is part of v1, not a future optimization. The Memory Governor's confidence model, co-residence decisions, eviction decisions, retry decisions, and user-facing explanations should all use local learning when evidence exists.

Evidence hierarchy:

1. Recent repeated local observations under similar settings on the same machine/backend.
2. A single local observation under similar settings.
3. Creator-side `.noofy` observations from export.
4. Heuristics from model size, workflow type, resolution, batch size, and backend.
5. Unknown.

Creator-side metrics are useful as a first-run estimate only. They describe the creator's machine and tested settings, not the user's machine. Once local observations exist, local observations are more trustworthy.

Persist per observation:

- workflow ID and capsule fingerprint
- runner fingerprint and dependency env fingerprint
- model references
- input profile: resolution, batch size, relevant options
- backend: CUDA, MPS, DirectML, CPU
- machine profile: GPU name, VRAM total, RAM total, OS, driver/backend version when available
- duration
- runner start/load time
- observed idle RAM/VRAM
- observed peak RAM/VRAM
- success/failure
- memory error signature
- eviction required
- retry required and retry result
- confidence before the run
- confidence after the run

These observations are stored in local app data, not in the `.noofy` archive or immutable capsule lock.

Use observations to:

- raise confidence for known-good workflow/input profiles
- raise confidence more when success is repeated under similar settings
- keep frequently used stable workflows warm more often
- downgrade memory class when a workflow proves light locally
- upgrade memory class after high peak or memory error
- avoid repeating failed co-residence decisions
- decide whether multiple runners can stay warm
- decide whether a runner should be evicted or retained based on real reuse and memory behavior
- decide whether retry-after-cleanup is likely to help
- display better user guidance

Memory failures are also learning events. If a workflow fails because of memory, Noofy should remember that for the workflow, machine profile, backend, runner fingerprint, model set, and similar input settings. Future decisions should become more conservative: evict first, avoid co-residence, require larger margins, or block earlier with a clearer explanation.

Learned metrics are evidence, not guarantees. Noofy must still lower confidence when inputs change, models change, another app consumes GPU memory, the backend/driver changes, or observed system pressure becomes unstable.

Product effect: the first run of a workflow may be cautious. After several successful local runs, Noofy can make the workflow feel faster by keeping it warm more confidently, avoiding unnecessary eviction, and allowing safe co-residence on machines that have proved they can handle it.

## 11. User-Facing UI Language

The UI should explain actions, not implementation details.

| Situation | Suggested text |
| --- | --- |
| Runner kept warm | "Ready to run again quickly" |
| Multiple runners warm | "Several workflows are ready to run again" |
| Workflow queued | "Waiting for the GPU" |
| Switching runner | "Preparing this workflow" |
| Evicting for memory | "Freeing memory before starting" |
| Waiting for release | "Waiting for memory to clear" |
| Retry after cleanup | "Noofy freed memory and is trying again" |
| Blocked by memory | "Not enough memory for this workflow" |
| Machine is near limit | "This workflow may be slower on this machine" |
| Not kept warm | "Noofy closed this workflow to avoid a memory problem" |
| Learned stable workflow | "Noofy knows this workflow runs well on this machine" |
| Learned memory risk | "Noofy is being careful because this workflow needed more memory before" |

Developer details can include VRAM, RAM, runner IDs, fingerprints, estimates, and raw errors. Default user text should avoid CUDA, allocator, Python, and stack trace terminology.

## 12. Backend And API State Surface

Runner and job state should support:

- `idle_warm`
- `running`
- `queued`
- `queued_pending_switch`
- `queued_pending_memory`
- `switching`
- `evicting_runner`
- `waiting_for_memory_release`
- `loading_model`
- `retrying_after_memory_cleanup`
- `blocked_by_memory`
- `memory_cleanup_failed`
- `evicted_for_memory`
- `evicted_after_cooldown`
- `co_resident`

Memory risk should be exposed as structured metadata:

- `memory_risk_low`
- `memory_risk_medium`
- `memory_risk_high`
- `memory_estimate_confidence`
- `memory_estimate_source`
- `local_memory_evidence_count`
- `recent_local_memory_failure`
- `memory_decision_reason`
- `can_retry_after_cleanup`

API payloads should remain beginner-friendly by default, with developer details behind an explicit diagnostics view.

## 13. Implementation Phases

### MG1: Schemas And Decision Records

- Add memory class enum with `gpu_medium`.
- Add workflow memory estimate schema.
- Add local memory observation summary schema.
- Add runner memory snapshot schema.
- Add machine memory snapshot schema.
- Add Memory Governor decision schema.
- Persist decision records in diagnostics.

Acceptance:

- Unit tests cover schema validation, unknown-as-heavy behavior, local evidence summaries, and decision serialization.

### MG2: Hardware And Memory Observers

- Implement backend-specific observers for CUDA first.
- Add MPS, DirectML, CPU/RAM observer interfaces even if initial implementation is conservative.
- Capture total/free VRAM, total/free RAM, backend name, device name, and pressure indicators.

Acceptance:

- Fake observer tests cover normal, unavailable, and partial-data cases.

### MG3: Estimation Engine

- Build estimates from repeated local history, single local observations, `.noofy` creator metrics, model metadata, workflow type, resolution, batch size, and heuristics.
- Attach confidence level and reason list.
- Treat missing estimates as `unknown`/high risk.
- Lower confidence when local observations do not match the requested settings or machine profile.

Acceptance:

- Tests prove repeated local observations outrank single local observations, local observations outrank creator observations, creator observations outrank heuristics, unknown remains conservative, and materially changed settings lower confidence.

### MG4: Co-Residence And Eviction Policy

- Implement co-residence matrix.
- Implement safety margins per backend/device tier.
- Implement eviction scoring.
- Integrate decisions into runner start and workflow run requests.

Acceptance:

- Tests cover heavy/heavy deny, heavy/light allow with margin, unknown deny, large-GPU high-confidence allow, and memory-pressure eviction.

### MG5: Queue And Handoff

- Add `queued_pending_memory`.
- Persist in-memory queue records while backend is running.
- Handoff queued work after active job finishes or memory release completes.
- Keep normal cancellation behavior.

Acceptance:

- Tests cover queue behind active job, queue after memory cleanup, cancellation of queued work, and no active-job auto-kill.

### MG6: Memory Release And Retry

- Stop idle runners and wait for bounded RAM/VRAM release.
- Detect likely memory errors.
- Retry once when safe.
- Record failure history and downgrade future confidence.
- Record successful local runs and raise future confidence only for similar settings.

Acceptance:

- Tests cover release success, release timeout, retry success, retry blocked, repeated memory failure avoiding the same optimistic decision, and repeated success increasing confidence for future warm retention.

### MG7: UI And Diagnostics Contract

- Extend API payloads with user-facing memory status summaries.
- Add developer details for decision signals and raw errors.
- Add frontend states and copy for memory decisions.

Acceptance:

- API tests cover response shape and redaction.
- UI tests or stories cover memory waiting, retry, blocked, and warm co-residence states.

### MG8: Integration And Hardware Validation

- Use fake/lightweight runners for CI.
- Add optional real CUDA validation for memory release and warm co-residence.
- Add manual or automated platform checks for MPS, DirectML, and CPU-only behavior.

Acceptance:

- Full default test suite remains hardware-independent.
- Real hardware validation produces a decision log that can be reviewed without raw stack traces.

## 14. V1 Completion Gate

Memory Governor v1 is complete when:

- runner memory classes and estimates exist
- local memory observations are persisted
- repeated local successes increase confidence for similar future decisions
- local memory failures make future decisions more conservative for the workflow, machine, backend, and similar settings
- creator `.noofy` observations are used as initial hints only until local evidence exists
- decisions use reliability-ranked signals
- co-resident warm runners are allowed only through policy
- idle runner eviction happens before user-facing memory failure
- memory release is bounded and diagnosed
- one safe retry after cleanup exists for likely memory errors
- UI/API states explain waiting, cleanup, retry, blocked, and warm readiness
- tests cover success, uncertainty fallback, memory pressure, eviction, retry, and blocked failure

The safe fallback remains: if Memory Governor cannot make a confident decision, Noofy behaves as though only one GPU-heavy runner may remain resident.
