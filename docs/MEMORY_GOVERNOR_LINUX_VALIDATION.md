# Memory Governor Linux Hardware Validation

Use this note for production-like Ubuntu CUDA validation. It keeps the trusted
backend environment separate from the managed ComfyUI runner environment.

## Prepare

Install backend development tooling, including `uv`, in the backend venv:

```bash
cd backend
.venv/bin/python -m pip install uv
```

Run the validation from the repo root:

```bash
make memory-governor-linux-validation
```

By default this uses:

- app data: `.noofy-runtime/data`
- managed ComfyUI venv: `.noofy-runtime/data/runtime/comfyui-venv`
- validation output: `.noofy-runtime/validation/memory-governor-linux-validation.json`

The command uses Noofy's existing managed runtime bootstrap path. On NVIDIA
Linux, `RuntimeEnvironment.bootstrap()` detects `nvidia-smi`, creates the
managed venv, installs CUDA PyTorch into that venv, then installs
`third_party/comfyui/requirements.txt`. It does not install PyTorch into the
trusted backend venv.

On modern NVIDIA drivers that report CUDA 13.0 or newer, the bootstrap policy
should select the `cu130` PyTorch wheel index to match the
`linux-x64-cuda130` runtime profile.

## What It Validates

The validation command:

- writes a tiny local Noofy Verified validation workflow into the app data
  workflow directory
- bootstraps the managed ComfyUI runtime if needed
- verifies CUDA PyTorch from the managed runner Python
- runs `runner_memory_probe.py` around a small CUDA allocation and reads the
  resulting allocator telemetry
- prepares an isolated runner workspace through `CapsuleInstaller`
- starts the managed runner through `RunnerProcessSupervisor`
- runs a real model-free ComfyUI workflow through the backend run path
- reruns after prompt-only and seed-only edits and confirms warm runner reuse
  without creating a new memory-profile bucket
- reruns after resolution and batch changes and confirms a separate profile
  bucket with extracted estimate features
- performs rapid concurrent Run submissions and confirms bounded automatic
  queue handoff through the public queue ID alias
- cancels a queued second run while the active run completes normally
- evicts the idle isolated runner, polls for release, and records the observed
  NVML VRAM increase
- checks that the validated managed runner PID is gone after backend shutdown
- records Memory Governor diagnostics, local learning, process-tree RSS, NVML
  per-process VRAM attribution, and sample windows

Useful fields in the JSON output:

- `managed_torch`: managed Python path, Torch version, CUDA availability
- `allocator_probe.sample`: PyTorch CUDA allocator signal and attribution quality
- `memory_sampling_finish.sample_windows_observed`: lifecycle windows observed
- `memory_sampling_finish.attribution_sources`: process/NVML/allocator sources
- `local_memory_summaries`: local learning stored for the validation workflow
- `p0_p1_lifecycle`: warm reruns, profile split, rapid queue handoff, queued
  cancellation, and isolated release-polling results
- `post_shutdown_cuda_processes`: managed-runner shutdown check

Model loading is not meaningfully validated by the default workflow because it
uses only `EmptyImage -> SaveImage` and no model files.

## Latest A10G Pass

On 2026-06-02, the checked-in harness passed on an NVIDIA A10G with 23,028 MB
VRAM, NVIDIA driver `595.71.05`, and managed PyTorch `2.12.0+cu130`.

Observed highlights:

- standalone allocator probe recorded a real 256 MB CUDA allocation
- prompt-only and seed-only reruns reused the same warm isolated runner and
  memory-profile fingerprint
- resolution and batch changes produced a different profile fingerprint
- rapid double-click handoff submitted automatically under the queue alias with
  three bounded, state-driven handoff attempts and no transient submission
  failure
- queued cancellation completed without submitting the canceled record
- isolated runner eviction increased free VRAM from 22,331 MB to 22,589 MB and
  recorded `release_requested -> observed_memory_drop`
- the managed runner PID was absent after shutdown

This does not replace the pending large-model CUDA pass. A real checkpoint or
other substantial model is still needed to validate large warm residency,
delayed core `/free`, allocator reserved-memory behavior after `/free`, and
private custom-node caches.
