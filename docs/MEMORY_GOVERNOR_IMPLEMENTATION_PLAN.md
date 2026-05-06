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

Before starting an isolated workflow runner, Noofy:

1. Builds a workflow memory estimate from local evidence, creator observations,
   declared requirements, installed model size, or heuristics.
2. Reads a machine memory snapshot.
3. Compares the requested runner with resident isolated runners.
4. Chooses one of: start co-resident, evict idle runners then start, queue behind
   active work, or refuse execution when the available evidence is too strong to
   justify a run attempt.
5. Records a structured diagnostic and memory metric.

For submitted isolated workflow runs, Noofy also gates execution when another
isolated runner is active or when memory evidence requires cleanup first.
Input/options are hashed into an input-profile fingerprint, so local learning is
scoped to similar run settings instead of being blindly reused across materially
different runs.

After a workflow result, Noofy records a local observation for completed,
failed, canceled, and memory-error outcomes. Memory errors lower future
confidence. Repeated successful observations raise confidence only for the same
workflow, runner compatibility key, machine profile, backend, and input profile.

If a submitted job fails with a likely memory error, Noofy may stop idle
isolated runners, wait for bounded memory release, and retry the same workflow
once. It does not retry non-memory failures or repeat memory retries forever.

## Core Runner Direction

Current v1 coverage is strongest for isolated workflow runners. The desired
architecture is broader: the core/default/trusted runner should also be covered
by the same memory-governance strategy, or routed through an equivalent
admission path before workflow execution.

The reason is not to protect Noofy from the core runner. The reason is to make
the correct RAM/VRAM and runner-residency decision before running any workflow.
Core-runner admission should follow the same product policy:

- prefer reuse when it is likely to be fast and safe
- prefer cleanup or idle-runner eviction when memory is uncertain
- queue behind active work rather than interrupt it
- warn when the machine is near its practical limit
- avoid refusing execution unless there is strong evidence the run cannot
  proceed or cleanup/retry has already failed

Noofy should not claim full app-wide memory governance until core-runner run
admission is implemented or explicitly scoped through an equivalent path.

## Platform Policy

CUDA uses `nvidia-smi` for VRAM when available, with system RAM included in the
snapshot when possible.

macOS Apple Silicon uses RAM as the unified CPU/GPU pressure pool. MPS admission
does not require dedicated VRAM fields.

CPU fallback uses RAM pressure. GPU-style estimates can be used as a RAM
pressure proxy when no dedicated RAM estimate exists. Capsules whose runtime
backend is explicitly `cpu` are classified as `cpu_only`.

DirectML remains incomplete. The current RAM fallback is acceptable as a
conservative fallback, but it should not be the final Windows GPU strategy.
Future work should investigate Windows-compatible GPU memory observation for
DirectML, AMD, Intel, and NVIDIA paths, while preserving graceful fallback when
reliable data is unavailable.

## Measurement Direction

Current runner descriptors and snapshots are useful but not enough for the
accuracy Noofy should eventually have. Noofy should improve peak RAM/VRAM
observation from Noofy's side without modifying vendored ComfyUI source.

Do not modify or fork code under `third_party/comfyui`.

Future measurement work should investigate and implement the best available
Noofy-side approach using:

- process observation
- backend-specific APIs
- adapter-level hooks
- wrapper logic around runner launch and job execution
- launch configuration
- logs
- supported ComfyUI APIs or events
- external system metrics

The target measurement windows are:

- runner startup
- model loading
- workflow execution
- retry after cleanup
- memory release after runner stop

These observations are best-effort evidence, not guaranteed exact true peaks on
every backend and platform. The goal is to make decisions less theoretical and
more grounded in local behavior, while staying honest about fragmentation,
allocator caches, delayed release, other applications, and backend-specific
visibility limits.

## Safety Margins

Current safety margins are conservative constants. Before adding more custom
margin logic, investigate whether the operating system, GPU backend, PyTorch,
DirectML, CUDA, MPS, or available platform APIs expose useful pressure signals,
allocation feedback, or memory-release signals.

Margins that are too large will handicap users by causing unnecessary unloading,
queueing, or refusal. Margins that are too small will cause avoidable memory
failures. Prefer real system/backend pressure signals where possible. If Noofy
still needs its own margins, they should be justified, conservative, and
preferably adaptive based on local observations rather than fixed forever.

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
is: complete enough for v1 isolated runner memory admission, with the limitations
and follow-up directions above.

## Test Coverage

Current tests cover:

- schema validation and decision serialization
- CUDA observer success/failure/partial data
- RAM fallback observer behavior
- MPS and CPU RAM-pressure admission
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

## V1 Completion Gate

Memory Governor v1 is complete enough for isolated workflow runners when:

- the default service wires an observer and local learning store
- isolated runner starts use Memory Governor admission
- isolated workflow runs can queue, clean up, warn, or refuse based on memory
  decisions
- local observations are persisted outside `.noofy` packages
- memory errors make future decisions more conservative
- safe retry-after-cleanup is bounded to one retry
- API/UI payloads explain waiting, cleanup, retry, blocked, and warm states
- tests remain hardware-independent by default

That gate is currently met for isolated workflow runners. The next meaningful
work is targeted hardening around core-runner admission, better Noofy-side peak
memory observation, DirectML/Windows GPU observation, adaptive safety margins,
and real hardware validation logs.
