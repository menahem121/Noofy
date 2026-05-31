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
- output mapping for generated images, audio, video, 3D assets, text, or files
- dashboard schema for the end-user interface

## Hardware And Memory Observations

A `.noofy` package may include creator/export-time hardware observations, such as peak RAM/VRAM, tested resolution, batch size, backend, GPU name, model set, and run duration.

These observations are advisory first-run hints. They describe the machine and settings used by the creator/exporter. They are not universal requirements and must not be treated as proof that the workflow will fit on another user's machine.

Noofy learned memory metrics from normal local use are stored in the app's local data, not written back into the `.noofy` package. Local learned metrics are machine-specific and can include successful runs, memory failures, retry outcomes, observed peaks, confidence changes, and warm-runner behavior on the user's device.

Normal app usage must not mutate imported `.noofy` packages with local memory history. This preserves portability, privacy, and package trust/signing semantics.

If a future creator/re-export flow includes local observations, they must be explicitly included as advisory metadata and clearly marked as observations from that exporting machine. A recipient's own local observations still take priority for Memory Governor decisions.

## Dashboard Schema

The dashboard schema describes what the end user sees. It should support:

- sections and groups
- text inputs
- number inputs and sliders
- selects
- image, audio, video, and generic file upload controls
- toggles
- run and cancel actions
- default values and presets
- validation rules
- conditional visibility and enabled states

### API Credential Controls

Official ComfyUI Partner/API-node workflows can declare an `api_credential`
control for a user-owned ComfyUI Account API Key. The dashboard/package may
store provider and injection metadata, but never the raw key or user-specific
status such as `configured` or `last_four`.

Supported v1 metadata:

```json
{
  "id": "comfy_account_key",
  "type": "api_credential",
  "label": "ComfyUI Account API Key",
  "provider": "comfy_org",
  "required": true,
  "secret_ref": "api-key:comfy_org",
  "injection_strategy": {
    "kind": "comfyui_extra_data",
    "field": "api_key_comfy_org"
  }
}
```

At run time, the backend derives a credential injection plan from the saved
dashboard schema, resolves `api-key:comfy_org` locally, registers the resolved
secret with shared redaction, and passes only that plan to the active engine
adapter. For ComfyUI this becomes `/prompt.extra_data.api_key_comfy_org`.
The frontend may submit credential references for state, but it must never
submit raw keys in a run payload and must not decide ComfyUI payload details.

Custom-node API key conventions are out of scope for this v1 path. Future
strategies such as `runner_env`, `config_file`, and `node_input` must be
modeled explicitly with separate warnings and tests; generic node input
injection is not a default. For example, BFL custom nodes that use
`BFL_API_KEY` or `bfl_api_key.txt` are not covered by
`comfyui_extra_data.api_key_comfy_org`.

## Bindings

Each exposed control should bind to a workflow input without requiring the app to understand the full graph.

Example binding concept:

```text
control: prompt
target node id: 6
target input name: text
```

The backend applies input bindings before submitting the graph to the active `EngineAdapter`.

Media outputs declare an app-owned `kind`: `image`, `audio`, `video`, `3d`, `text`,
or `file`. The compatibility `type` field mirrors that media kind, while engine
retrieval details remain separate. Package and dashboard validation accepts
these declared output kinds as first-class outputs, but output widgets remain
strict: image widgets bind only to `image`, audio widgets only to `audio`, video
widgets only to `video`, and generic file widgets only to `file` until dedicated
widgets exist for additional kinds.

Portable `.noofy` archives must not contain creator-local input media, generated
output media, generated filenames, output subfolders, temp/output paths, creator
machine paths, runtime file bucket identity, file bytes, or base64 media content.
Exporter-generated dashboards should persist only stable output records: stable
ID, generic or stable label, node ID, source node type when useful, `type`, and
`kind`. Uploaded dashboard media and generic files are user-local app data and
must not be embedded in portable `.noofy` archives.
Generic `load_file` inputs must declare `validation.accepted_extensions` and/or
`validation.accepted_mime_types`; those fields are allow-list checks for that
dashboard input, not a trust signal for backend parsing or execution.

When an exporter redacts a local file input, it may preserve setup metadata under
`unresolved_runtime_inputs`: node ID, node type, input name, expected kind,
required flag, and safe extension/MIME hints. It must not preserve private
filenames, absolute paths, temp paths, file bytes, base64 content, or generated
media references.

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

If a workflow has unresolved runtime inputs, Noofy must not treat runner health alone as sufficient for ready status. Imported community workflows may omit an execution fixture only when their runtime inputs are fully resolved and dependency import, custom-node registration, and runner health smoke all pass inside the isolated runner.

For core-only workflows that do not declare a fixture, Noofy may use a safe model-free fallback fixture such as `EmptyImage -> SaveImage` to prove the staged runner can execute work. This fallback is not used for custom-node packages because it would not exercise the custom node code.

For packages that declare custom nodes, the smoke fixture should exercise at least one declared custom node type when possible. Registration-only checks prove the staged runner imported the node package, while execution smoke remains the stronger signal before Noofy promotes isolated runtime artifacts.

## Duplicate Imports

Noofy must not silently replace an installed package with the same
publisher/package/version identity. A duplicate import is staged until the user
chooses to replace the existing workflow, import the archive as a separate
local copy, or cancel.

Replacing updates only the internal package copy and does not mutate the
original imported archive. Stale local state that is tied to the previous
package/dashboard, such as user values, layout overrides, output preferences,
and package-local install state, must not be silently reused after replacement.

Importing as copy changes the internal identity and user-facing name so it
cannot conflict with the existing workflow. Because the identity changed, the
copy must not pretend to keep the original verified/signature identity; trust
and source metadata must remain honest about the copied local package.

## Model Handling

The first version validates required models before running a workflow.

Validation depends on the active `EngineAdapter`. For ComfyUI, the adapter asks the running ComfyUI instance which models are available and compares that against the package's `required_models`.

For imported workflows, validation also uses Noofy's `ModelAvailabilityService` summary so identity-verified availability across the Noofy Models folder and the optional connected ComfyUI folder is respected. The structured availability is exposed at `GET /api/workflows/{id}/model-summary`.

Required models also record where they came from in the source graph (`node_id`, `node_type`, `input_name`) so the UI can show users which workflow step needs each file.

Missing models can be resolved through external model providers (Hugging Face and Civitai). The full identity, resolver, staged import preview, download transaction, and background job behavior are described in [MODEL_RESOLUTION_AND_DOWNLOADS.md](MODEL_RESOLUTION_AND_DOWNLOADS.md).

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

Do not validate workflow models by reading a hardcoded local ComfyUI models folder. The user-configurable Noofy Models folder (default `~/Documents/Noofy Models`) and the optional connected ComfyUI folder are the supported roots.

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
