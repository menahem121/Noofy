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

The default harness remains model-free. It does not validate delayed core
`/free`, allocator reserved-memory behavior after `/free`, or private
custom-node caches.

## Latest A10G Model-Backed Pass

On 2026-06-02, the bundled `text_to_image_v0` workflow also passed on the same
NVIDIA A10G using the SD 1.5 checkpoint
`v1-5-pruned-emaonly-fp16.safetensors`.

Observed highlights:

- initial 512x512 text-to-image run completed through the managed backend path
- model selection diagnostics identified the checkpoint as model-residency
  evidence
- first-run heuristic estimate allowed a cautious `memory_warning` start
- observed execution peak VRAM rose to about 2.5 GB for the first run
- prompt-only rerun reused the same warm runner, kept the same memory-profile
  fingerprint, and reported `ready_reusing_runner`
- seed-only rerun reused the same warm runner, kept the same memory-profile
  fingerprint, and reported `ready_reusing_runner`
- 768x768 rerun changed the memory-profile fingerprint and execution-profile
  signature, reached about 4.0 GB observed execution peak VRAM, and completed
  successfully

The validation artifact was written to:

```text
.noofy-runtime/validation/sd15-large-model-validation.json
```

## Latest A10G External-Pressure Probe

On 2026-06-02, a separate non-Noofy Python process allocated about 19 GB of
CUDA memory before the bundled text-to-image workflow was requested.

Observed result:

- Noofy blocked the run instead of trying to reclaim memory it did not own
- the backend state was `blocked_unattributed_pressure`
- the reason was `insufficient_vram_margin`
- the global GPU snapshot showed about 3.3 GB free out of 23.0 GB total

This is safe for v1, but it is intentionally not counted as explicit
`blocked_external_pressure`: the admission snapshot did not carry external
process details, so the backend correctly avoided claiming proven external
ownership from global usage alone.

The validation artifact was written to:

```text
.noofy-runtime/validation/external-pressure-validation.json
```

Still pending after these A10G passes:

- delayed core `/free` from a loaded core-sidecar model
- PyTorch allocated/reserved memory after `/free`
- private custom-node cache behavior after `/free`
- orphan child-process cleanup beyond the validated managed runner PID absence
- packaged desktop UI rapid double-click against a real backend and runner
- Windows, Apple Silicon large-model, Linux PSI pressure, and multi-GPU
  platform validation
