# Memory Governor

Date: 2026-05-06

Status: current architecture/reference with remaining hardware-validation notes.

This document explains what the Memory Governor does now, what is intentionally
conservative, and where real hardware validation is still needed.

## Purpose

The Memory Governor is Noofy's resource-management and run-admission decision
system. Its main job is to help the user's workflow run successfully and
efficiently when they press Run.

It decides whether Noofy should:

- run while keeping existing runners and models warm
- reuse a compatible warm runner
- unload or evict idle runners before running
- queue behind active GPU work
- wait for memory release after cleanup
- retry once after likely memory cleanup
- warn the user that the machine is near its limit
- refuse execution only as a last resort

Snapshots and decisions carry `signal_quality`, `signal_sources`, and
`pressure_reasons` so diagnostics can distinguish backend APIs, OS pressure
signals, system samples, process samples, heuristics, and unavailable fallback
data.

Job memory samples and local observations also carry attribution metadata:
`runner_id`, `job_id`, `workflow_id`, runner process IDs, sample window,
`attribution_quality`, `attribution_sources`, and `attribution_reasons`. This
keeps strong per-runner evidence separate from weaker system-wide deltas.

The Memory Governor should not become an overly defensive blocker. If evidence
is uncertain, Noofy should prefer reversible preparation steps such as evicting
idle runners, freeing memory, queueing behind active work, or showing a clear
warning. Blocking is appropriate only when there is strong evidence that the run
cannot proceed safely, or after reasonable cleanup/retry steps have already
failed.

Runtime Isolation protects the trusted backend from dependency conflicts. The
Memory Governor works on top of that architecture to reduce memory-risky runner
and workflow decisions.

## Current Behavior

The Memory Governor is implemented in
`backend/app/runtime/memory/memory_governor.py` and integrated by
`backend/app/runtime/memory/service.py` and `backend/app/runs/orchestrator.py`.
Workflow queue records, queue-ID aliases, dispatch loop guards, and terminal
watchers live in `backend/app/runs/`. Runner reservations and runner-start
transitions live in `backend/app/runtime/runners/`. `EngineService` wires these
domains and retains migration proxies; it is not the lifecycle owner.

Before submitting a workflow run through the backend run path, Noofy:

1. Builds a workflow memory estimate from local evidence, creator observations,
   declared requirements, installed model size, or heuristics.
2. Reads a machine memory snapshot.
3. Compares the requested runner with relevant resident runners.
4. Chooses one of: start co-resident, evict idle runners then start, queue behind
   active work, or refuse execution when the available evidence is too strong to
   justify a run attempt.
5. Records a structured diagnostic and memory metric.

The same admission path now covers isolated runners and the core/default runner.
Idle isolated runners can be evicted before a run. The core process stays
alive, but its idle ComfyUI model and allocator cache can be released through
the adapter-owned `/free` operation. Active work is queued rather than
interrupted.

Submission reserves the selected runner atomically before memory admission and
before awaiting adapter submission. Cleanup uses separate eviction
reservations. A queued workflow keeps one UUID queue ID across requeue and
handoff attempts; after adapter submission that public queue ID resolves to the
canonical submitted job ID for progress, result, cancellation, logs, SSE, and
output reads.

Input/options are hashed into an input-profile fingerprint, so local learning is
scoped to similar run settings instead of being blindly reused across materially
different runs. Prompt text and seed controls are intentionally memory-neutral.
Resolution, batch size, model or LoRA choice, media inputs, video frame count,
precision, VRAM mode, and other non-text settings remain profile inputs.

Each decision also records a `memory_ownership` diagnostic summary: currently
free VRAM, same-workflow warm-runner memory, reclaimable idle Noofy runners,
active Noofy runners, known Noofy-runner VRAM, and VRAM that remains
unattributed or external. The last category is intentionally honest: incomplete
process attribution cannot prove which non-Noofy process owns every remaining
allocation.

After a workflow result, Noofy records a local observation for completed,
failed, canceled, and memory-error outcomes. Memory errors lower future
confidence. Repeated successful observations raise confidence only for the same
workflow, runner compatibility key, machine profile, backend, and input profile.
Local observations preserve whether the peak came from process-tree RSS,
per-process GPU memory, runner-side backend allocator telemetry, an
active-job-window system delta, or weak/unavailable attribution.

If a submitted job fails with a likely memory error, Noofy may stop idle
isolated runners, unload idle core model/cache memory, wait for bounded memory
release, and retry the same workflow once. It does not retry non-memory failures
or repeat memory retries forever.

`/free` HTTP success is only an acknowledgment. Noofy polls RAM/VRAM
asynchronously with adaptive intervals until release is observed or the
configured timeout expires. The defaults are:

- `NOOFY_MEMORY_RELEASE_TIMEOUT_SECONDS=8`
- `NOOFY_MEMORY_RELEASE_INITIAL_POLL_INTERVAL_SECONDS=0.1`
- `NOOFY_MEMORY_RELEASE_MAX_POLL_INTERVAL_SECONDS=1.0`

Core warm residency is cleared only after confirmed release. If observation is
unavailable or release times out, Noofy preserves attribution, marks the runner
`release_failed`, and reports `memory_cleanup_failed`.

Compatibility responses may retain `EngineJob.status = "blocked_by_memory"`,
while `memory_status.state` exposes the precise backend condition:
`waiting_for_active_workflow`, `freeing_previous_models`,
`unloading_previous_workflow`, `retrying_after_memory_cleanup`,
`memory_cleanup_failed`, `blocked_external_pressure`,
`blocked_exceeds_capacity`, or `blocked_unattributed_pressure`. Noofy reports
external pressure only when observation includes explicit non-Noofy-process
evidence; unexplained residual usage remains unattributed.

## Library Advisory Warnings

Workflow summaries and details may include a lightweight `hardware_warning`
advisory for the current machine. The warning evaluator reads existing package
metadata, required model sizes, local memory-learning summaries, and a
best-effort memory snapshot. It does not start runners, contact ComfyUI, or run
full workflow validation while rendering library cards.

These yellow or red warnings are separate from run-time admission. They never
disable opening, editing, importing, exporting, customizing, or attempting to
run a workflow. The Memory Governor still applies its normal admission policy
when the user actually presses Run.

Local observations stay in local app data and are not written into `.noofy`
packages, package metadata, capsule locks, or exports. Creator/export-time
observations remain advisory hints and never override evidence learned on the
current machine. Card warnings use recent local memory failures so stale errors
do not keep a workflow flagged indefinitely.

## Core Runner Policy

Core/default/trusted runner admission is Memory-Governor-aware. This is not to
protect Noofy from the core runner. It is to make the correct RAM/VRAM and
runner-residency decision before running any workflow.

Core-runner admission follows the same product policy:

- prefer reuse when it is likely to be fast and safe
- prefer cleanup or idle-runner eviction when memory is uncertain
- queue behind active work rather than interrupt it
- warn when the machine is near its practical limit
- avoid refusing execution unless there is strong evidence the run cannot
  proceed or cleanup/retry has already failed

The core process is not evicted as idle cleanup. When its warm models are
reclaimable, the ComfyUI adapter requests model unload and allocator-cache
release while leaving the trusted process alive. Runtime Isolation, not the
Memory Governor, owns dependency-conflict boundaries.

## Platform Policy

CUDA/NVIDIA first uses direct NVML observation when available, then falls back to
`nvidia-smi`. System RAM is included when the host exposes it. These paths report
backend signal quality and source metadata. When NVML per-process memory is
available, Noofy maps GPU memory PIDs back to the active runner root/child
process tree and records that as stronger attribution than global GPU deltas.

macOS Apple Silicon uses RAM as the unified CPU/GPU pressure pool. MPS admission
does not require dedicated VRAM fields. The Noofy-owned runner memory probe can
record PyTorch MPS allocator/driver/recommended memory telemetry when those APIs
are available in the runner process.

macOS Intel is not a supported managed ComfyUI runtime target for Noofy.
Generic Darwin RAM and process-tree observers may still produce diagnostics on
Intel Macs, but those signals do not imply workflow/runtime support.

CPU fallback uses RAM pressure. GPU-style estimates can be used as a RAM
pressure proxy when no dedicated RAM estimate exists. Capsules whose runtime
backend is explicitly `cpu` are classified as `cpu_only`.

Linux RAM observation is augmented with PSI memory pressure when
`/proc/pressure/memory` is available. PSI is a system stall/thrashing signal, not
workflow-specific VRAM attribution.

Windows first tries NVML and `nvidia-smi` for NVIDIA, then a DirectML-class
observer using Windows GPU Adapter Memory counters and Win32 video controller
metadata, then RAM fallback. Global adapter counters are still marked as
`system_sample`. For stronger runner attribution, the Windows observer also reads
GPU Process Memory counters and maps matching PIDs back to the active runner
process tree. That process-counter path is stronger than a system delta, but it
is still OS-reported process usage, not an exact workflow graph allocation.

The Noofy-owned runner memory probe is also the route for runner-process-context
DXGI budget/current-usage telemetry. Querying DXGI only from the trusted backend
process is not enough if the runner owns the DirectML allocations. The probe
keeps the DXGI telemetry contract in runner-side code without modifying ComfyUI;
real Windows hardware validation must confirm whether the current best-effort
ctypes path is sufficient or whether Noofy should replace it with a tiny native
helper library.

## Attribution Model

Noofy currently treats attribution in descending strength:

- `process_exact`: backend per-process GPU memory, currently NVML process memory
  and Windows GPU Process Memory counters matched to the runner root or child PID
- `process_tree`: runner root plus child-process RSS
- `backend_allocator`: Noofy-owned runner-side telemetry from PyTorch CUDA/MPS
  allocator APIs, and runner-process-context DXGI budget/current-usage data when
  available
- `active_window_delta` / `system_delta`: whole-machine RAM/VRAM movement while a
  job is active
- `unavailable`: attribution could not be observed

Process-tree RAM is best-effort. It includes the runner root PID and child PIDs
when process inspection succeeds, and it fails closed into unavailable
attribution when the process has exited, permissions block inspection, or the
platform command is unavailable.

System-wide GPU/RAM deltas are useful diagnostics, but they must not be treated
as exact workflow usage. Other applications, allocator caches, delayed release,
and parallel runner activity can affect them.

## Measurement Behavior

Current runner descriptors, snapshots, signal metadata, and local observations
are the v1 evidence model. Noofy samples machine memory around submitted jobs,
samples runner process-tree RSS, maps backend process GPU counters where
available, reads runner-side allocator telemetry JSONL when present, and uses the
best observed memory pressure to fill missing runner peak observations. That
makes later decisions less theoretical without pretending weak measurements are
exact per-workflow attribution.

Do not modify or fork code under `third_party/comfyui`.

Noofy-side observation should continue to use:

- process observation and per-runner attribution where available
- backend-specific APIs
- adapter-level hooks
- wrapper logic around runner launch and job execution
- launch configuration
- logs
- supported ComfyUI APIs or events
- external system metrics

The implemented measurement windows are:

- `runner_startup`: emitted by the Noofy-owned runner memory probe while the
  runner starts
- `before_submit`: sampled immediately around workflow submission
- `workflow_execution`: sampled while a normal workflow job is active
- `retry_after_cleanup`: sampled while a memory-cleanup retry is active
- `after_completion`: sampled immediately after the job result is observed
- `cleanup` / `memory_release`: release polling records ordinary machine
  snapshots plus a structured timeline for request, pending allocation,
  partial release, observed drop, timeout, and observer-unavailable states
- `model_loading` / `unknown`: reserved for when reliable engine-side model-load
  boundaries are available

These observations are best-effort evidence, not guaranteed exact true peaks on
every backend and platform. The goal is to make decisions less theoretical and
more grounded in local behavior, while staying honest about fragmentation,
allocator caches, delayed release, other applications, and backend-specific
visibility limits.

PyTorch CUDA/MPS allocator attribution is collected through the Noofy-owned
runner memory probe when the runner process has PyTorch and the relevant backend
APIs available. The probe samples CUDA allocated/reserved current and peak bytes,
CUDA OOM/retry counters when exposed, and MPS current/driver/recommended memory
values when exposed. It runs as wrapper logic around the ComfyUI entrypoint and
does not patch vendored ComfyUI.

## Safety Margins

Fixed margins are fallback safety floors, not the main decision engine. Pressure
signals from OS/backend APIs, PSI, allocator telemetry, and local observations
should carry more weight when their signal quality is strong.

Margins that are too large will handicap users by causing unnecessary unloading,
queueing, or refusal. Margins that are too small will cause avoidable memory
failures. Do not add an abstract adaptive-margin formula without evidence. If
Noofy still needs its own margins, keep them small, justified, and secondary to
real pressure/budget/backend signals and local empirical learning.

## Source Of Truth

This document intentionally stays at the architecture and operating-policy
level. The source of truth for exact behavior is:

- `backend/app/runtime/memory/memory_governor.py`
- `backend/app/runtime/memory/system_memory.py`
- `backend/app/runtime/memory/service.py`
- `backend/app/engine/memory_observation.py`
- `backend/app/runs/orchestrator.py`
- `backend/app/runs/result_service.py`
- `backend/tests/test_memory_governor.py`
- runner/run-service tests covering queueing, eviction, retry, and API shape

Current status: complete enough for practical v1 workflow admission, with the
explicit platform accuracy limits documented here.

## Test Coverage

Current tests cover:

- schema validation and decision serialization
- signal quality/source/reason metadata
- attribution quality/source/reason metadata
- process-tree RAM attribution and missing-process fallback
- NVML per-process GPU memory mapping to runner PIDs
- Windows per-process GPU counter mapping to runner PIDs
- runner-side PyTorch CUDA/MPS allocator telemetry payload parsing
- runner launch wrapping through the Noofy memory probe
- direct NVML and `nvidia-smi` success/failure/partial data
- Linux PSI pressure parsing and unavailable fallback behavior
- RAM and Windows GPU-counter fallback observer behavior
- MPS and CPU RAM-pressure admission

For production-like Ubuntu CUDA hardware validation, use
[`MEMORY_GOVERNOR_LINUX_VALIDATION.md`](MEMORY_GOVERNOR_LINUX_VALIDATION.md).
That path prepares the managed ComfyUI runtime under app data, verifies runner
side PyTorch CUDA telemetry, and runs a model-free managed workflow through the
backend run path.

Apple Silicon validation has been run on macOS 15.6 with an Apple M2 and 8 GB
unified memory. That pass confirmed Darwin RAM sampling through `sysctl` /
`vm_stat`, MPS-as-unified-memory admission with no dedicated VRAM requirement,
runner-side PyTorch MPS current/driver/recommended memory telemetry when the
APIs are available, process-tree RSS sampling during a managed runner workflow,
local observation persistence, and model-free `EmptyImage -> SaveImage`
execution through the backend run path. The local validation artifact is
written under `.noofy-runtime/validation/memory-governor-mps-validation.json`.
This validates MPS signal availability and unified-memory behavior, not peak
behavior for large model loads.
- local evidence precedence and persistence
- input-profile-sensitive confidence lowering
- prompt/seed-neutral warm reuse and memory-changing profile invalidation
- heavy/heavy denial and large-GPU high-confidence allowance
- memory-pressure eviction and active-runner queueing
- bounded memory-release success and timeout
- asynchronous release pending, partial, unavailable, and confirmed-drop timelines
- UUID workflow queue aliases, same-ID requeue loop guards, automatic dispatch,
  custom-node queued startup handoff, and concurrent terminal finalize-once
- retry-after-cleanup success and blocked retry
- service-level runner start eviction, co-residence, and memory blocking
- service-level workflow run queueing, isolated-runner eviction, core-cache
  release, cleanup timeout, external-pressure blocking, local learning, and retry
- API payloads for memory status and metrics
- frontend display of memory waiting and blocked states

Default repo command remains:

```bash
make test
```

## Current Completion

Memory Governor v1 is complete enough for practical local workflow admission
when:

- the default service wires an observer and local learning store
- snapshots and decisions carry signal quality/source/reason metadata
- job samples and local observations carry attribution quality/source/reason
  metadata
- runner process-tree RAM attribution is captured when process inspection works
- NVIDIA process GPU attribution is captured when NVML exposes matching process
  memory
- NVIDIA uses NVML first, with `nvidia-smi` fallback
- Linux RAM pressure includes PSI when available
- Windows reports global GPU-counter signals honestly, maps GPU Process Memory
  counters to runner PIDs when present, and uses the runner memory probe as the
  DXGI process-context telemetry route
- PyTorch CUDA/MPS allocator telemetry is read from Noofy-owned runner probe
  JSONL files when present
- job observations record `before_submit`, `workflow_execution`,
  `retry_after_cleanup`, and `after_completion` windows, with startup telemetry
  available from the runner probe
- isolated and core workflow runs use Memory Governor admission
- same-workflow warm runs reuse exact memory profiles without double-counting
  resident model memory
- idle isolated memory is evicted and idle core model/cache memory is unloaded
  before a last-resort block
- workflow runs can queue, clean up, warn, or refuse based on memory
  decisions
- local observations are persisted outside `.noofy` packages
- memory errors make future decisions more conservative
- safe retry-after-cleanup is bounded to one retry
- API/UI payloads explain waiting, cleanup, retry, blocked, and warm states
- tests remain hardware-independent by default

That gate is met in hardware-independent code and tests, with important accuracy
limits. Windows DirectML still needs real-hardware validation to decide whether
the current runner-side DXGI ctypes path is sufficient or should become a tiny
native Noofy helper. CUDA/MPS allocator telemetry depends on which PyTorch APIs
exist in the runner environment. Apple Silicon MPS has local validation for
MPS telemetry availability and shared RAM pressure on one M2 macOS machine, but
large model-load behavior still needs broader hardware coverage. Remaining
validation should happen on real NVIDIA CUDA, Windows
DirectML/AMD/Intel/NVIDIA, Linux PSI-enabled machines, and additional Apple
Silicon memory sizes.
