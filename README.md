# Local AI Workflow App

A desktop app for running local AI workflows on Linux, Windows, and macOS.

The goal is to make powerful AI tools easy for beginners: users choose a ready-made workflow, press a simple button, and the app runs the AI process in the background.

## Core Idea

This project will provide a clean desktop interface for local AI tasks such as:

- Text-to-image generation
- Background removal
- Image erasing / inpainting
- Image editing workflows
- Future local AI utilities built on reusable workflows

## Planned Architecture

- Frontend: TypeScript + React
- Desktop shell: Tauri / Rust
- Backend: Python
- AI engine: ComfyUI-based workflow engine
- Communication: local HTTP + WebSocket API

The desktop app will start and manage the local Python backend, then communicate with it through a local API.

For product v1, ComfyUI should run as an app-managed hidden sidecar with its own isolated Python environment. Users should not need to manually launch ComfyUI or install its Python dependencies.

## Project Direction

Version 1 will focus on a reliable cross-platform app for Linux, Windows, and macOS using a Python/ComfyUI backend. Linux CUDA workstations and servers are a first-class validation target for the ComfyUI backend.

Noofy should support community workflows from the internet as a first-class product direction. Users should be able to import workflows made by other people without manually installing Python packages, copying custom node folders, editing ComfyUI paths, or troubleshooting dependency conflicts.

Community workflows must be prepared through isolated workflow capsules and runner environments. Noofy should automatically resolve, download, install, and smoke-test custom nodes and normal Python dependencies when technically possible, including common custom-node repositories that declare dependencies in `requirements.txt`. Those installs must never mutate the trusted core runtime or another installed workflow.

Unverified community workflows are not guaranteed to be safe, trustworthy, or compatible. Noofy protects the app architecture from dependency conflicts and broken installs; it does not claim arbitrary Python code from the internet is secure.

In later platform-focused phases, the AI inference layer can add native acceleration paths where appropriate, such as Apple-native acceleration through Core ML, Metal, or MLX, Windows-native inference paths, or Linux CUDA-specific optimizations. These should improve performance and integration while keeping the general workflow system flexible.

## Future Workflow Creator Mode

A later phase may add a creator-focused workflow packaging system.

Workflow creators will be able to build a workflow in ComfyUI, then export it through a custom ComfyUI node or extension made for this project. That exported workflow package will include the ComfyUI graph, required model information, metadata, and the controls that should appear in the desktop app.

Inside the desktop app, the creator will be able to turn selected workflow inputs into a simple modular dashboard. For example, a creator could expose only the prompt field, strength slider, image upload, style selector, or run button while hiding the full node graph from the end user.

The end user would then open the workflow as a clean, intuitive interface designed by the creator. The app would detect missing models, show what needs to be downloaded, ask for user approval, and then download the required models from verified sources automatically.

## Developer Docs

- [Agent entry point](AGENTS.md)
- [Design system](DESIGN_SYSTEM.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Engine contract](docs/ENGINE_CONTRACT.md)
- [Workflow packages](docs/WORKFLOW_PACKAGES.md)
- [Runtime isolation architecture](docs/RUNTIME_ISOLATION_ARCHITECTURE.md)
- [Runtime isolation implementation plan](docs/RUNTIME_ISOLATION_IMPLEMENTATION_PLAN.md)
- [Noofy Verified publishing process](docs/NOOFY_VERIFIED_PUBLISHING.md)
- [OS sandboxing feasibility](docs/OS_SANDBOXING_FEASIBILITY.md)
- [ComfyUI runtime strategy](docs/COMFYUI_RUNTIME_STRATEGY.md)
- [Memory Governor implementation plan](docs/MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md)
- [Milestone 1](docs/MILESTONE_1.md)
- [Managed ComfyUI sidecar](docs/MANAGED_COMFYUI_SIDECAR.md)
- [Import, dashboard widgets, and user dashboard flow](docs/NOOFY_IMPORT_DASHBOARD_WIDGET_FLOW.md)

## Main Goal

Make local AI workflows feel simple, private, and approachable without requiring users to understand ComfyUI, Python, model folders, or complex node graphs.
