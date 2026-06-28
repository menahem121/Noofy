# Future Full ComfyUI Runtime Parity Strategy

Status: active plan.

Last reviewed: 2026-06-28.

This document defines the future release strategy for making Noofy compatible with
ComfyUI's native local runtime capabilities without turning ComfyUI into the
public API contract of the app. The frontend still talks only to the Noofy
backend. The backend owns `EngineAdapter`, runtime profiles, managed sidecar
startup, dependency preparation, diagnostics, and fallback behavior.

This is a planning document. It does not mark any missing backend, package, or
installer as implemented.

## Source Baseline

Release planning must pin a ComfyUI version and a date before implementation
starts. ComfyUI support changes quickly, so every release candidate must refresh
this section.

Current external baseline reviewed on 2026-06-28:

- ComfyUI system requirements page says local ComfyUI supports Windows, Linux,
  and macOS Apple Silicon, and that manual installation supports all system and
  GPU types listed there.
- ComfyUI supported hardware list includes NVIDIA CUDA, AMD ROCm on Linux,
  experimental AMD Windows/Linux support for RDNA 3/3.5/4, Intel Arc with native
  `torch.xpu`, Apple Silicon Metal acceleration, Ascend NPU, Cambricon MLU,
  Iluvatar Corex, and CPU mode.
- ComfyUI portable Windows uses separate packages for NVIDIA, AMD, and Intel GPU
  hardware. This is evidence that one embedded Python/PyTorch payload may not
  be practical for all Windows GPU backends.
- ComfyUI manual installation currently lists CUDA 13.0 for NVIDIA and ROCm 7.2
  for AMD in its example commands.
- ComfyUI changelog latest visible release is v0.26.2 dated 2026-06-25.
- The current Noofy runtime profile catalog pins ComfyUI v0.26.0. A Noofy
  release must keep that pin and document parity against that version.

Primary references:

- https://docs.comfy.org/installation/system_requirements
- https://docs.comfy.org/installation/manual_install
- https://docs.comfy.org/installation/comfyui_portable_windows
- https://docs.comfy.org/changelog
- https://pytorch.org/get-started/locally/

## Definitions

OS/CPU target:
: The desktop app installer and packaged trusted runtime target, such as
  `windows-x64`, `linux-x64`, or `macos-arm64`.

Accelerator backend:
: A runtime capability selected after install, such as CUDA, ROCm, XPU, MPS, a
  vendor extension, or CPU fallback.

Runtime profile variant:
: Noofy's pinned contract for one OS, CPU architecture, accelerator backend,
  Python ABI, PyTorch or vendor runtime package set, launch defaults, and smoke
  test requirements.

Trusted packaged runtime:
: The Noofy-owned Python and `uv` used to launch the backend and prepare managed
  runtime environments. It must not contain ComfyUI, PyTorch, custom node, or
  community workflow dependencies.

Managed engine environment:
: The app-data environment that contains ComfyUI, PyTorch or equivalent vendor
  runtime packages, and core engine dependencies for one runtime profile
  variant.

## Release Principle

Noofy must not claim full ComfyUI parity until every selected parity cell has:

- an installer or app-shell target for the OS/CPU architecture,
- a runtime profile variant for the accelerator backend,
- deterministic PyTorch or vendor runtime package selection,
- automatic detection and manual override behavior,
- CPU fallback where upstream ComfyUI supports CPU operation,
- managed sidecar startup and health checks,
- a successful clean-machine smoke test,
- user-visible diagnostics and support status,
- documentation that matches the implemented behavior.

If Noofy releases before all upstream ComfyUI-native cells are implemented, the
release must be marketed and documented as a limited-platform release. It must
not use "full ComfyUI parity" language.

## Current Noofy Support

The current repo supports a narrower release surface than upstream ComfyUI:

| Area | Current state |
| --- | --- |
| Packaged OS/CPU targets | `macos-arm64`, `windows-x64`, `linux-x64` |
| Explicitly excluded target | macOS Intel |
| Runtime profiles | macOS ARM MPS/CPU, Windows x64 CUDA/CPU, Linux x64 CUDA/CPU |
| Hardware detection | Apple Silicon MPS, NVIDIA through `nvidia-smi`, CPU fallback |
| PyTorch install policy | CUDA wheel index for detected NVIDIA, default macOS wheels for Apple Silicon, CPU wheels otherwise |
| Missing from runtime selection | ROCm, Windows AMD ROCm path, Intel XPU, Ascend NPU, Cambricon MLU, Iluvatar Corex |
| DirectML | Not currently a Noofy runtime target; existing architecture notes say it was deliberately avoided because of old `torch-directml` constraints |
| Packaging verifier | Accepts only the three current OS/CPU targets |
| UI/docs risk | Existing docs correctly describe the narrower support in many places; any new release language must avoid overstating backend parity |

## Target Matrix

This matrix separates OS/CPU packaging from accelerator backend support. A cell
is release-ready only when both packaging and backend profile work are complete.

Legend:

- Current: implemented in the current repo and still needs release hardware
  validation.
- Required: upstream ComfyUI-native path that Noofy must implement before any
  "full parity" release.
- Decision: needs a product/technical decision before it can become required.
- Research: upstream mentions a vendor backend, but Noofy needs vendor package,
  hardware, CI, and support feasibility evidence before committing.
- Excluded: not part of full-parity scope unless upstream ComfyUI changes its
  official support statement.

| OS/CPU target | Installer/runtime target | Backends to support | Status |
| --- | --- | --- | --- |
| `macos-arm64` | `.dmg` plus packaged trusted Python/uv | Metal/MPS, CPU | Current, required validation across M1/M2/M3/M4 class machines |
| `macos-x64` | none today | CPU only if upstream declares official support | Excluded today; ComfyUI system requirements emphasize Apple Silicon |
| `windows-x64` | `.exe` or `.msi` app-shell installer | CUDA, AMD ROCm if official for hardware class, Intel XPU, CPU | Current for CUDA/CPU; required gaps for AMD/Intel if full parity includes Windows portable/manual GPU paths |
| `windows-arm64` | separate app-shell installer if accepted | CPU, vendor-specific accelerator only if upstream and PyTorch/vendor packages support it | Decision |
| `linux-x64` | `.deb` plus other formats as needed | CUDA, ROCm, Intel XPU, CPU, vendor NPUs/MLUs where upstream lists official support | Current for CUDA/CPU; required gaps for ROCm/XPU and research gaps for vendor accelerators |
| `linux-arm64` | separate app-shell installer if accepted | CPU and vendor accelerators only when official wheels and hardware validation exist | Decision |

## Windows Packaging Position

Noofy should aim for one Windows app installer per CPU architecture, not one
installer per GPU backend.

For `windows-x64`, a single installer can contain the trusted backend runtime,
the desktop shell, and bootstrap logic. It should install or activate
backend-specific managed engine environments under app data after hardware
detection. This keeps the user experience simple while avoiding a huge and
fragile all-in-one PyTorch payload.

The public download page can label the x64 artifact as `Noofy-windows.exe` if
there is only one Windows CPU architecture. If Windows ARM64 becomes supported,
release artifacts must be architecture-specific, for example
`Noofy-windows-x64.exe` and `Noofy-windows-arm64.exe`, with website-side
auto-detection allowed.

Separate GPU-specific Windows installers are allowed only if a backend has a
hard technical constraint that prevents runtime preparation or side-by-side
managed environments. Any exception must be recorded in this document and in the
release notes.

## Runtime Backend Strategy

The backend should select runtime profiles through a deterministic resolver:

1. Detect OS, CPU architecture, driver/runtime signals, and visible accelerator
   devices.
2. Build an ordered list of compatible runtime profile variants.
3. Prefer the fastest supported backend with a validated runtime package set.
4. Honor manual override only when the selected variant is compatible with the
   current OS/CPU target.
5. Prepare the managed engine environment for that variant.
6. Run import, backend-availability, ComfyUI API, WebSocket, model-path, and
   starter-workflow smoke checks.
7. Activate the variant only after smoke checks pass.
8. If preparation fails, keep the failed environment quarantined, emit structured
   diagnostics, and offer the next compatible backend, including CPU fallback.

Noofy must never silently run a different accelerator than the one shown in the
UI. Fallback to CPU is allowed only with an explicit warning state and a clear
diagnostic reason.

## Backend-Specific Plans

### NVIDIA CUDA

Current status: partially implemented for Windows x64 and Linux x64.

Required work:

- Keep CUDA wheel index selection in runtime profiles instead of hardcoded logic.
- Support at least the current ComfyUI-recommended CUDA path and any legacy path
  needed for officially documented hardware.
- Verify driver compatibility before installing CUDA wheels.
- Smoke test on Windows x64 and Linux x64 NVIDIA hosts.
- Keep CPU fallback if CUDA wheel installation or `torch.cuda.is_available()`
  fails.

Validation signal:

- `torch.cuda.is_available()` is true.
- `torch.version.cuda` matches the selected profile policy.
- Managed ComfyUI starts and runs the starter workflow.
- Memory Governor receives CUDA/NVML or `nvidia-smi` telemetry when available.

### AMD ROCm

Current status: missing from Noofy runtime detection and runtime profiles.

Required work:

- Detect AMD GPU and driver/runtime eligibility separately on Linux and Windows.
- Add ROCm runtime profile variants for every officially supported OS/CPU cell.
- Use ComfyUI/PyTorch-recommended ROCm wheel indexes for the pinned release.
- Confirm whether Windows AMD support is official, experimental, or portable-only
  for the chosen ComfyUI version and RDNA class.
- Add clear UI status for unsupported AMD hardware generations.
- Add CPU fallback when ROCm packages cannot be installed or imported.

Validation signal:

- `torch.version.hip` exists for ROCm profiles.
- `torch.cuda.is_available()` or the PyTorch ROCm availability path reports the
  AMD device as usable.
- Managed ComfyUI starts and runs the starter workflow on Linux ROCm and any
  accepted Windows AMD profile.

### Intel XPU

Current status: missing from Noofy runtime detection and runtime profiles.

Required work:

- Detect Intel Arc/XPU devices and driver/runtime prerequisites on Windows and
  Linux.
- Add XPU runtime profile variants for supported OS/CPU targets.
- Use the official PyTorch XPU package strategy for the pinned ComfyUI release.
- Add startup checks for `torch.xpu` availability.
- Add CPU fallback when XPU is unavailable.

Validation signal:

- `hasattr(torch, "xpu")` is true.
- `torch.xpu.is_available()` is true.
- Managed ComfyUI starts and runs the starter workflow on Intel Arc hardware.

### Apple Silicon Metal/MPS

Current status: partially implemented for `macos-arm64`.

Required work:

- Keep `macos-arm64-mps` and `macos-arm64-cpu` profile variants pinned.
- Validate MPS on multiple Apple Silicon generations.
- Keep attention-backend policy profile-owned, because changing it can change
  same-seed output behavior.
- Validate CPU fallback with `--cpu`.

Validation signal:

- `torch.backends.mps.is_available()` is true for MPS profile.
- Managed ComfyUI starts and runs the starter workflow.
- Memory Governor records unified memory and MPS telemetry when available.

### CPU Fallback

Current status: partially implemented.

Required work:

- Ensure CPU runtime profile variants exist for every supported OS/CPU target.
- Treat CPU as a real validated runtime, not only an error fallback.
- Show beginner-friendly performance warnings.
- Ensure Memory Governor uses RAM pressure and does not require GPU telemetry.
- Validate starter workflow runtime within an accepted timeout or document CPU
  workflow limits.

Validation signal:

- Managed ComfyUI starts with CPU profile.
- Starter workflow completes on clean Windows, Linux, and macOS machines without
  accelerator hardware.

### DirectML

Current status: not a Noofy target.

ComfyUI's current system requirements reviewed for this plan do not list
DirectML as a primary supported hardware backend. Noofy should not make DirectML
a release requirement unless the pinned ComfyUI release and official install
docs define it as a supported native path.

If DirectML becomes required later, it must be added as a separate runtime
profile family or variant with its own PyTorch package constraints, ComfyUI
compatibility notes, smoke tests, and UI status. It must not replace ROCm or XPU
where those are the official upstream paths.

### Ascend NPU, Cambricon MLU, Iluvatar Corex

Current status: research gaps.

ComfyUI lists these vendor accelerators as supported through vendor PyTorch
extensions. Noofy cannot claim full manual-install parity while ignoring them,
but support should not be added blindly.

Required research before commitment:

- Confirm official ComfyUI support status for the pinned release.
- Confirm OS/CPU architecture availability.
- Confirm installable vendor packages, Python ABI support, and licensing.
- Confirm hardware access for validation.
- Confirm whether these profiles can run inside Noofy's managed runtime and
  isolated workflow runner architecture.
- Define diagnostics, memory telemetry, and fallback behavior.

If Noofy cannot validate these cells, release leadership must choose either to
block full-parity release or lower the public support claim.

## Runtime Profile Changes

The runtime profile system should become the source of truth for backend
support. Each profile variant must include:

- OS and CPU architecture.
- Accelerator backend.
- Python version and Python build ID.
- PyTorch or vendor runtime packages, exact versions, package index URLs, hashes
  where available, and environment variables.
- ComfyUI core version, source hash, and frontend version.
- Launch defaults, including VRAM mode, attention backend, precision policy, and
  any backend-specific flags.
- Hardware detection requirements.
- Import and backend availability checks.
- Smoke workflow IDs and pass/fail criteria.
- Fallback policy.
- Support tier: stable, experimental, research, or blocked.

Custom node dependency resolution must continue to treat torch, torchvision,
torchaudio, and accelerator packages as core runtime-owned. Community workflows
must not replace or shadow the selected profile's core runtime packages.

## Packaged Runtime And Installer Changes

Noofy should keep one trusted packaged runtime artifact per OS/CPU target:

```text
noofy-runtime-macos-arm64.zip
noofy-runtime-windows-x64.zip
noofy-runtime-linux-x64.zip
```

Additional artifacts are added only after the OS/CPU target is accepted, for
example:

```text
noofy-runtime-windows-arm64.zip
noofy-runtime-linux-arm64.zip
```

GPU backend packages should be installed into app-data managed engine
environments, not bundled into the trusted runtime. This preserves the existing
trust boundary and allows side-by-side runtime profiles for CUDA, ROCm, XPU,
MPS, CPU, and vendor backends.

Required packaging work:

- Expand `frontend/scripts/packagedRuntime.mjs` only after a target-specific
  Python/uv artifact and verification path exist.
- Expand `frontend/scripts/downloadPackagedRuntime.mjs` target specs for each
  accepted OS/CPU target.
- Expand the Tauri target matrix only after packaged runtime verification and
  backend smoke tests can run for that target.
- Update the Tauri launcher target check to reject unsupported targets with
  clear user-facing errors.
- Keep release builds from depending on system Python, Homebrew, Conda, global
  `uv`, Node, or npm at runtime.

## Detection And Override Policy

Detection must produce structured diagnostics for every important branch:

- OS/CPU target accepted or rejected.
- Device probes attempted.
- Driver/runtime versions detected.
- Candidate profile variants considered.
- Selected variant and reason.
- Rejected variants and reason.
- Environment preparation result.
- Smoke test result.
- Fallback result.

Manual override rules:

- Users can choose only variants compatible with their OS/CPU target.
- Unsupported variants appear disabled with a reason.
- Experimental variants require explicit opt-in copy.
- A manual override is not persisted after a profile becomes invalid due to an
  app update, runtime update, or driver/runtime mismatch.
- CPU fallback remains available unless the workflow explicitly requires an
  accelerator-only custom node or model path.

## UI And Documentation Requirements

The UI must distinguish:

- Installed app target.
- Detected hardware.
- Selected runtime backend.
- Runtime profile support tier.
- Prepared/not prepared state.
- Fallback state.
- Missing driver or unsupported hardware state.
- Whether a workflow is blocked by runtime profile incompatibility.

Docs and release notes must use exact support language:

- "Supported and validated" only for cells with clean-machine hardware smoke
  evidence.
- "Experimental" only for cells where upstream marks the path experimental or
  Noofy lacks broad hardware coverage.
- "Not supported yet" for cells missing Noofy implementation.
- "Not an upstream native target" for requests such as DirectML unless the
  pinned ComfyUI docs say otherwise.

## Implementation Epics

1. Matrix source of truth
   - Add a machine-readable release matrix for OS/CPU targets, accelerator
     backends, support tiers, and validation requirements.
   - Generate docs, UI labels, and CI matrix entries from this data where
     practical.

2. Packaged OS/CPU target expansion
   - Decide whether Windows ARM64 and Linux ARM64 are in scope.
   - Add target-specific Python/uv runtime acquisition, verification, smoke
     execution, and Tauri build support for accepted targets.

3. Accelerator detection
   - Extend hardware detection beyond NVIDIA/MPS/CPU.
   - Add mocked unit tests for CUDA, ROCm, XPU, MPS, CPU, unsupported Mac Intel,
     and unsupported hardware generations.

4. Runtime profile variants
   - Add ROCm and XPU variants.
   - Research vendor NPU/MLU/Corex variants.
   - Move install package selection out of ad hoc hardware logic and into
     profile-owned policy.

5. Runtime preparation and fallback
   - Prepare side-by-side managed environments per profile variant.
   - Quarantine failed environments.
   - Add explicit fallback flows and user-visible diagnostics.

6. UI runtime selection
   - Add runtime backend display and manual override controls.
   - Add support-tier and fallback states.
   - Ensure workflow compatibility errors point to the active runtime profile.

7. CI and hardware validation
   - Add installer smoke tests per OS/CPU target.
   - Add managed ComfyUI smoke tests per backend profile.
   - Add hardware-lab validation notes for cells that cannot run in normal CI.

8. Documentation and release gates
   - Update `PACKAGED_RUNTIME.md`, `MANAGED_COMFYUI_SIDECAR.md`,
     `RUNTIME_ISOLATION_ARCHITECTURE.md`, and user-facing release notes after
     implementation.
   - Keep this strategy document as the status source until all release gates
     pass.

## Validation Matrix

Noofy is not full-parity release-ready until every required cell has current
evidence.

| Host | Required backend evidence |
| --- | --- |
| macOS Apple Silicon M1/M2 class | MPS profile, CPU fallback, app install/open/run/close |
| macOS Apple Silicon M3/M4 class | MPS profile, CPU fallback, app install/open/run/close |
| Windows x64 NVIDIA modern RTX | CUDA current profile, CPU fallback |
| Windows x64 NVIDIA older supported generation | CUDA legacy/current policy as appropriate, CPU fallback |
| Windows x64 AMD RDNA supported class | ROCm or upstream official AMD Windows profile, CPU fallback |
| Windows x64 Intel Arc | XPU profile, CPU fallback |
| Windows x64 without supported GPU | CPU profile |
| Linux x64 NVIDIA | CUDA profile, CPU fallback |
| Linux x64 AMD ROCm supported GPU | ROCm profile, CPU fallback |
| Linux x64 Intel Arc | XPU profile, CPU fallback |
| Linux x64 without supported GPU | CPU profile |
| Linux x64 Ascend/Cambricon/Iluvatar | Vendor profile, if full manual-install parity includes these cells |
| Linux ARM64 | Only if accepted as an OS/CPU target |
| Windows ARM64 | Only if accepted as an OS/CPU target |

Each validation run must record:

- Noofy version and commit.
- ComfyUI profile ID, source hash, and frontend version.
- OS version and CPU architecture.
- GPU model, driver/runtime versions, and selected profile variant.
- Package versions installed into the managed engine environment.
- Startup health result.
- Starter workflow result and output artifact.
- Memory Governor signal quality.
- Fallback behavior when the primary backend is intentionally disabled.
- Logs needed for support reproduction.

## Release Readiness Criteria

Noofy can be declared fully compatible with ComfyUI native local support only
when all of the following are true:

1. The pinned ComfyUI release and documentation baseline are recorded.
2. Every accepted OS/CPU target has a packaged trusted runtime artifact,
   manifest verification, app installer, and clean-machine install smoke test.
3. Every upstream-native accelerator backend for the pinned release has a
   runtime profile variant or a documented leadership decision lowering the
   public support claim.
4. Runtime detection selects the optimal compatible backend automatically.
5. Manual override is available for compatible variants and blocked with clear
   reasons for incompatible variants.
6. CPU fallback works on every supported OS/CPU target where upstream ComfyUI
   supports CPU mode.
7. Failed backend preparation never corrupts the trusted runtime or another
   managed engine environment.
8. The frontend talks only to Noofy backend APIs and never directly to ComfyUI.
9. Workflow model validation goes through the active `EngineAdapter`.
10. Community workflow custom nodes and Python dependencies remain isolated from
    the trusted backend runtime.
11. UI and docs exactly match the implemented support matrix.
12. Hardware smoke evidence exists for every required matrix cell.
13. Release notes list any experimental, research, or unsupported cells without
    ambiguity.

## Open Decisions

- Does "full parity" mean ComfyUI manual-install hardware parity, including
  Ascend NPU, Cambricon MLU, and Iluvatar Corex, or only Desktop/Portable parity?
- Is Windows AMD support release-required while ComfyUI labels it experimental
  for specific RDNA classes?
- Is Linux ARM64 in scope for the first full-parity release?
- Is Windows ARM64 in scope for the first full-parity release?
- Should Noofy support vendor accelerator cells without direct hardware access,
  or are they release blockers until hardware validation is available?
- Should DirectML remain excluded unless upstream ComfyUI reintroduces it as an
  official native path?
- What is the maximum acceptable first-run download size for backend-specific
  managed engine environments?
- Which starter workflow is lightweight enough to validate CPU fallback while
  still proving the architecture?

## Immediate Next Task

Create the machine-readable release matrix and wire it into docs/tests without
changing runtime behavior yet. That keeps the implementation sequence explicit
and prevents accidental support claims before hardware and packaging work are
complete.
