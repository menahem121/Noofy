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
- runs a real model-free ComfyUI workflow through `EngineService`
- records Memory Governor diagnostics, local learning, process-tree RSS, NVML
  per-process VRAM attribution, and sample windows

Useful fields in the JSON output:

- `managed_torch`: managed Python path, Torch version, CUDA availability
- `allocator_probe.sample`: PyTorch CUDA allocator signal and attribution quality
- `memory_sampling_finish.sample_windows_observed`: lifecycle windows observed
- `memory_sampling_finish.attribution_sources`: process/NVML/allocator sources
- `local_memory_summaries`: local learning stored for the validation workflow

Model loading is not meaningfully validated by the default workflow because it
uses only `EmptyImage -> SaveImage` and no model files.
