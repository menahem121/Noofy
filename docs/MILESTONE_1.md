# Milestone 1

Milestone 1 proves the app architecture with one working text-to-image workflow.

## Goal

Run a packaged text-to-image workflow through the app backend, using ComfyUI as the first engine adapter, while keeping the frontend isolated from ComfyUI internals.

For development, this milestone may use an externally launched ComfyUI URL to unblock the first generation test. That is not the product architecture.

## Required Behavior

- Load one text-to-image workflow package.
- Validate package metadata, dashboard bindings, and output mapping.
- Check required model files before execution.
- Return a clear missing-model response if a required model is not present.
- Submit the ComfyUI graph through `ComfyUIEngineAdapter`.
- Stream progress to the app backend and frontend.
- Retrieve the final generated image result.
- Support canceling a running or queued job.

## First Workflow Controls

The first dashboard should include only the controls needed to prove the system:

- prompt
- seed
- width
- height
- run
- cancel
- output image preview

## Out of Scope

- Full creator mode UI.
- Marketplace or public workflow sharing.
- Automatic model download and installation.
- ComfyUI custom node or extension for exporting packages.
- Native macOS or Windows inference adapters.
- Production-ready managed ComfyUI sidecar packaging.

## Acceptance Check

A developer can start the backend, run the first text-to-image package, see progress, cancel a job, and retrieve the generated image without the frontend calling ComfyUI directly.

The follow-up milestone is managed ComfyUI sidecar startup, documented in [MANAGED_COMFYUI_SIDECAR.md](MANAGED_COMFYUI_SIDECAR.md).
