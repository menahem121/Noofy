# Docs Index

Status categories:

- Architecture/reference: current contracts, boundaries, invariants, and behavior.
- Active plan: future or incomplete work that still needs implementation or verification.
- Validation note: targeted manual/hardware validation instructions.

## Architecture / Reference

- [ARCHITECTURE.md](ARCHITECTURE.md): app stack, process boundaries, local API security, app data paths, and major decisions.
- [ENGINE_CONTRACT.md](ENGINE_CONTRACT.md): backend-owned engine operations, adapter boundary, job lifecycle, diagnostics, and runtime ownership.
- [WORKFLOW_PACKAGES.md](WORKFLOW_PACKAGES.md): `.noofy` package contents, bindings, smoke fixtures, model handling, storage cleanup direction, and creator-mode direction.
- [DASHBOARD_ARCHITECTURE.md](DASHBOARD_ARCHITECTURE.md): import routing, dashboard authoring, canvas run dashboard, user state, dashboard assets, and related APIs.
- [WIDGETS.md](WIDGETS.md): dashboard widget types, default grid minimum sizes, and widget sizing notes.
- [RUNTIME_ISOLATION_ARCHITECTURE.md](RUNTIME_ISOLATION_ARCHITECTURE.md): community workflow capsules, isolated runner processes, dependency environments, trust boundaries, source policy, and GC.
- [MEMORY_GOVERNOR.md](MEMORY_GOVERNOR.md): current run-admission, memory observation, local learning, retry, and platform signal policy.
- [MANAGED_COMFYUI_SIDECAR.md](MANAGED_COMFYUI_SIDECAR.md): app-managed ComfyUI lifecycle, path isolation, runtime modes, crash restart, and status APIs.
- [PACKAGED_RUNTIME.md](PACKAGED_RUNTIME.md): packaged Python/uv runtime contract, manifest, release preparation, and CI gates.
- [COMFYUI_UPDATES.md](COMFYUI_UPDATES.md): user-managed upstream ComfyUI update, activation, automatic repair, and rebuild behavior.
- [FEEDBACK_TESTING_MONITORING.md](FEEDBACK_TESTING_MONITORING.md): structured diagnostics, test expectations, and monitoring direction.

## Trust And Safety Reference

- [NOOFY_VERIFIED_PUBLISHING.md](NOOFY_VERIFIED_PUBLISHING.md): operational definition and future completion gates for Noofy Verified publishing.
- [OS_SANDBOXING_FEASIBILITY.md](OS_SANDBOXING_FEASIBILITY.md): product-claim boundaries and future OS sandboxing feasibility.

## Active Plans / Future Work

- [MODEL_COMPATIBILITY_PLAN.md](MODEL_COMPATIBILITY_PLAN.md): not started. Backend model identity scanning, compatibility resolution, optional registry lookup, LoRA download/verification, and frontend compatible-LoRA picker.

## Validation Notes

- [MEMORY_GOVERNOR_LINUX_VALIDATION.md](MEMORY_GOVERNOR_LINUX_VALIDATION.md): production-like Ubuntu CUDA validation for Memory Governor runner telemetry and managed ComfyUI execution.

## Removed Completed Plans

Completed milestone and implementation-plan histories were removed after durable decisions were merged into reference docs. Use git history for old checklists or progress logs.
