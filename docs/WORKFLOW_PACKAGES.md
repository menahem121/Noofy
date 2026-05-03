# Workflow Packages

A workflow package is the app-owned format for turning a ComfyUI workflow into a simple end-user dashboard.

The ComfyUI graph should be treated as opaque execution data. The app should understand the package metadata, required models, exposed inputs, output mapping, and dashboard schema.

## Package Contents

A package should contain:

- metadata: id, name, version, description, author
- engine target: first target is ComfyUI
- ComfyUI API graph used for execution
- required models with folder/type, filename, size, source URL, checksum when available, and identity verification level
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

## Smoke Tests

Workflow packages may declare a small execution smoke fixture under `smoke_tests.workflow_execution`.

This fixture is not the public workflow contract. It is a preparation-time check that lets Noofy prove a staged runner can execute real work before promoting isolated runtime artifacts to ready. The fixture should be minimal, bounded, and safe for low-resource machines.

Example:

```json
{
  "smoke_tests": {
    "workflow_execution": {
      "name": "tiny-noop",
      "prompt": {
        "1": {
          "class_type": "NoOp",
          "inputs": {}
        }
      },
      "required_node_types": ["NoOp"],
      "expected_output_node_count": 1,
      "expected_output_node_ids": ["1"],
      "timeout_seconds": 10
    }
  }
}
```

If a workflow has unresolved runtime inputs or no execution fixture, Noofy must not treat runner health alone as sufficient for ready status.

For core-only workflows that do not declare a fixture, Noofy may use a safe model-free fallback fixture such as `EmptyImage -> SaveImage` to prove the staged runner can execute work. This fallback is not used for custom-node packages because it would not exercise the custom node code.

For packages that declare custom nodes, the smoke fixture should exercise at least one declared custom node type when possible. Registration-only checks prove the staged runner imported the node package, but execution smoke is the stronger signal before Noofy promotes isolated runtime artifacts.

## Model Handling

The first version validates required models before running a workflow.

Validation depends on the active `EngineAdapter`. For ComfyUI, the adapter asks the running ComfyUI instance which models are available and compares that against the package's `required_models`.

If a model is missing, the backend should return a structured missing-model response. Automatic download can be added later using the same package metadata.

Model identity should use the strongest exported data available:

- `sha256_size`: hash and byte size both match, so the local file can be reused confidently.
- `filename_size`: filename and byte size match but no exported hash exists, so the local file is only an unverified candidate until Noofy computes and records its own local hash.
- `filename_only`: filename is the only known value and must not be treated as a trusted match by itself.

Noofy may reuse user-owned local files that pass validation, but cleanup and uninstall logic should only auto-delete assets that Noofy downloaded or created itself and that are not referenced by any other installed workflow.

Model and asset ownership should use explicit policy values:

- `noofy_downloaded`: Noofy downloaded the file and may garbage-collect it when unreferenced.
- `noofy_imported`: Noofy copied the file into app-owned storage and may garbage-collect that copy when unreferenced.
- `user_local`: Noofy may reuse the file, but must not delete the user's original.
- `external_reference`: Noofy may store or forget the reference, but must not delete the source.

Do not validate workflow models by reading a hardcoded local ComfyUI models folder.

## Workflow Removal And Storage Cleanup Direction

After imported workflows can be prepared and run, Noofy should add product-level storage cleanup that is separate from runtime isolation.

Workflow install and removal should use reference tracking:

- Installing a workflow creates references from the workflow install state to resolved model blobs, materialized model-view entries, downloaded assets, custom-node source archives, dependency envs, runner workspaces, and imported package files.
- Removing or uninstalling a workflow removes that workflow's references.
- Cleanup deletes only Noofy-owned files that have no remaining installed workflow references.
- Shared models and assets stay installed while at least one installed workflow references them.
- User-local files may be reused when valid, but Noofy must not delete the user's original file.
- External references may be forgotten by Noofy, but the external source must not be deleted.

Storage management should eventually expose:

- installed workflow package storage
- model blobs and materialized model views
- downloaded package archives
- custom-node source caches
- dependency envs
- runner workspaces
- wheel and source caches
- failed transactions after a retention window
- last-used timestamps and size information
- cache size limits and manual cleanup controls

## Creator Mode Direction

Later, creators should be able to build a workflow in ComfyUI, export a workflow package through a custom ComfyUI node or extension, then design the end-user dashboard in the desktop app.
