# Memory Governor Current Status

Date: 2026-05-06

Status: Audited and updated after Runtime Isolation implementation.

This document is no longer a phase-by-phase implementation checklist. The old
plan was useful for building the v1 policy, but most of that detail now lives in
code and tests. Keep this file short: it should help future agents understand
what the Memory Governor actually does, what is intentionally conservative, and
where the remaining work is.

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

The Memory Governor is implemented in `backend/app/runtime/memory_governor.py`
and integrated by `backend/app/engine/service.py`.

Before submitting a workflow run through the engine service, Noofy:

1. Builds a workflow memory estimate from local evidence, creator observations,
   declared requirements, installed model size, or heuristics.
2. Reads a machine memory snapshot.
3. Compares the requested runner with relevant resident runners.
4. Chooses one of: start co-resident, evict idle runners then start, queue behind
   active work, or refuse execution when the available evidence is too strong to
   justify a run attempt.
5. Records a structured diagnostic and memory metric.

The same admission path now covers isolated runners and the core/default runner.
Idle isolated runners can be evicted before a run; idle core runners are not
treated as eviction candidates. Active core work is still considered for
queueing decisions, because the point is to make a good RAM/VRAM decision before
any workflow starts.

Input/options are hashed into an input-profile fingerprint, so local learning is
scoped to similar run settings instead of being blindly reused across materially
different runs.

After a workflow result, Noofy records a local observation for completed,
failed, canceled, and memory-error outcomes. Memory errors lower future
confidence. Repeated successful observations raise confidence only for the same
workflow, runner compatibility key, machine profile, backend, and input profile.
Local observations preserve whether the peak came from process-tree RSS,
per-process GPU memory, runner-side backend allocator telemetry, an
active-job-window system delta, or weak/unavailable attribution.

If a submitted job fails with a likely memory error, Noofy may stop idle
isolated runners, wait for bounded memory release, and retry the same workflow
once. It does not retry non-memory failures or repeat memory retries forever.

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

The core runner is not evicted as idle cleanup. Runtime Isolation, not the Memory
Governor, owns dependency-conflict boundaries.

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
- `cleanup` / `memory_release`: represented in the schema for cleanup/release
  diagnostics; release polling still records ordinary machine snapshots today
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

## Obsolete Plan Parts

The old phase checklist has been removed. It duplicated tests and created the
false impression that every listed future enhancement had to be implemented.

The detailed signal matrix, long UI copy table, and acceptance checklists are no
longer useful as the source of truth. The source of truth is now:

- `backend/app/runtime/memory_governor.py`
- `backend/app/engine/service.py`
- `backend/tests/test_memory_governor.py`
- runner/engine service tests covering queueing, eviction, retry, and API shape

The old "complete for Phase 5f" status was too broad. The more accurate status
is: complete enough for practical v1 workflow admission, with the explicit
platform accuracy limits documented here.

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
EngineService path.

Apple Silicon validation has been run on macOS 15.6 with an Apple M2 and 8 GB
unified memory. That pass confirmed Darwin RAM sampling through `sysctl` /
`vm_stat`, MPS-as-unified-memory admission with no dedicated VRAM requirement,
runner-side PyTorch MPS current/driver/recommended memory telemetry when the
APIs are available, process-tree RSS sampling during a managed runner workflow,
local observation persistence, and model-free `EmptyImage -> SaveImage`
execution through the EngineService path. The local validation artifact is
written under `.noofy-runtime/validation/memory-governor-mps-validation.json`.
This validates MPS signal availability and unified-memory behavior, not peak
behavior for large model loads.
- local evidence precedence and persistence
- input-profile-sensitive confidence lowering
- heavy/heavy denial and large-GPU high-confidence allowance
- memory-pressure eviction and active-runner queueing
- bounded memory-release success and timeout
- retry-after-cleanup success and blocked retry
- service-level runner start eviction, co-residence, and memory blocking
- service-level workflow run queueing, blocking, local learning, and retry
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
