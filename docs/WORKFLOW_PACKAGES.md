# Workflow Packages

A workflow package is the app-owned format for turning a ComfyUI workflow into a simple end-user dashboard.

The ComfyUI graph should be treated as opaque execution data. The app should understand the package metadata, required models, exposed inputs, output mapping, and dashboard schema.

## Package Contents

A package should contain:

- metadata: id, name, version, description, author
- engine target: first target is ComfyUI
- ComfyUI API graph used for execution
- required models with folder/type, filename, source URL, and checksum when available
- exposed inputs mapped to ComfyUI node ids and input names
- output mapping for generated images or files
- dashboard schema for the end-user interface

## Dashboard Schema

The dashboard schema describes what the end user sees. It should support:

- sections and groups
- text inputs
- number inputs and sliders
- selects
- image upload controls
- toggles
- run and cancel actions
- default values and presets
- validation rules
- conditional visibility and enabled states

## Bindings

Each exposed control should bind to a workflow input without requiring the app to understand the full graph.

Example binding concept:

```text
control: prompt
target node id: 6
target input name: text
```

The backend applies input bindings before submitting the graph to the active `EngineAdapter`.

## Model Handling

The first version validates required models before running a workflow.

Validation depends on the active `EngineAdapter`. For ComfyUI, the adapter asks the running ComfyUI instance which models are available and compares that against the package's `required_models`.

If a model is missing, the backend should return a structured missing-model response. Automatic download can be added later using the same package metadata.

Do not validate workflow models by reading a hardcoded local ComfyUI models folder.

## Creator Mode Direction

Later, creators should be able to build a workflow in ComfyUI, export a workflow package through a custom ComfyUI node or extension, then design the end-user dashboard in the desktop app.
