# Noofy ComfyUI Export Extension

The Noofy ComfyUI Export Extension adds an **Export to Noofy** action to ComfyUI. It turns the current ComfyUI workflow into a `.noofy` package that Noofy can import as a tested workflow package instead of a raw ComfyUI JSON file.

A `.noofy` file is a zip archive. It contains the execution-ready ComfyUI API graph, Noofy package metadata, runtime metadata, hardware observations from a real test run, model references, and bundled custom node folders when the extension can identify them.

The package is designed for Noofy’s runtime isolation model: Noofy treats the ComfyUI graph as engine-specific execution data, reads the metadata and lock files, and prepares an isolated runtime capsule for the workflow.

## What It Does

- Adds an **Export to Noofy** button to the ComfyUI interface.
- Converts the current canvas to ComfyUI API prompt format.
- Runs the workflow once before export.
- Fails the export if the test run fails.
- Uses the images already selected in `LoadImage` / `LoadImageMask` nodes for the export test.
- Does not bundle the source images loaded into `LoadImage` / `LoadImageMask` nodes.
- Forces detected `batch_size` inputs to `1` for the export test.
- Records ComfyUI, Python, platform, GPU backend, and PyTorch metadata.
- Samples RAM and VRAM usage while the test run is active.
- Detects model references from common ComfyUI loader nodes.
- Hashes model files when ComfyUI can resolve them locally.
- Detects custom-node packages used by the workflow.
- Bundles custom-node folders when their source folders can be located.
- Records dependency marker files such as `requirements.txt`, `pyproject.toml`, `setup.py`, and `install.py`.
- Writes and downloads a `.noofy` package.

## Installation

Copy or symlink this folder into a ComfyUI `custom_nodes/` directory:

```text
ComfyUI/
  custom_nodes/
    comfyui_export2noofy_node/
```

Restart ComfyUI after installing the extension. On startup, ComfyUI loads the extension backend route and serves the frontend JavaScript from the extension `web/` directory.

## Usage

1. Open ComfyUI.
2. Load or build the workflow you want to export.
3. Click **Export to Noofy** in the ComfyUI interface.
4. Wait for the export test run to finish.
5. Save the downloaded `.noofy` file.

Export is intentionally blocking. If the workflow takes 20 minutes to run, the export takes about 20 minutes. The extension only creates a `.noofy` package after ComfyUI reports a successful test run.

## Export Flow

When **Export to Noofy** is clicked, the frontend asks ComfyUI to convert the current graph to API prompt format. The extension backend then prepares an export graph, queues the graph in ComfyUI using the current `LoadImage` / `LoadImageMask` selections, and waits for the prompt history result.

During the run, the extension samples memory usage and keeps the workflow’s selected models and image inputs intact except for the export-test batch-size normalization described above. If execution succeeds, the extension collects metadata and writes the `.noofy` archive. If execution fails, the response contains an error and no package is created.

## Package Contents

A `.noofy` package has this structure:

```text
workflow.noofy
  package.json
  comfyui_graph.json
  dashboard.json
  capsule.lock.json
  export-report.json
  assets/
    thumbnail.png
  custom_nodes/
    <custom-node-folder>/
      .noofy-file-manifest.json
      ...
```

`custom_nodes/` is present when the workflow uses detected custom nodes and their source folders can be located.

## package.json

`package.json` is the user-facing Noofy package descriptor. It contains the package ID, display name, version, source, trust level, exporter identity, and target engine metadata.

Example shape:

```json
{
  "schema_version": "0.1.0",
  "publisher_id": "unknown",
  "package_id": "exported-workflow",
  "version": "0.1.0",
  "display_name": "Exported Workflow",
  "description": "",
  "source": "comfyui_noofy_export_extension",
  "trust_level": "public_unverified",
  "created_at": "2026-04-30T00:00:00Z",
  "exporter": {
    "name": "Noofy ComfyUI Export Extension",
    "version": "0.1.0"
  },
  "engine": {
    "type": "comfyui",
    "graph_format": "comfyui_api_prompt",
    "comfyui_version": "0.0.0",
    "version_lock": true
  }
}
```

All workflows exported by this extension use `public_unverified`. A successful export means the workflow ran on the creator’s machine. It does not mean the workflow is safe, officially verified by Noofy, or trusted by default.

## comfyui_graph.json

`comfyui_graph.json` contains the execution-ready ComfyUI API prompt graph used for the successful export test run.

Noofy treats this graph as engine-specific execution data. Noofy uses package metadata, model records, custom-node records, dashboard schema, and runtime locks as the app-owned contract around the graph.

## dashboard.json

`dashboard.json` is intentionally minimal:

```json
{
  "schema_version": "0.1.0",
  "status": "not_configured",
  "controls": [],
  "notes": "Dashboard layout is configured inside Noofy creator mode."
}
```

The extension does not add Noofy marker nodes to the ComfyUI canvas. Dashboard configuration belongs to Noofy creator-mode tooling, not to the exported ComfyUI graph.

## capsule.lock.json

`capsule.lock.json` records reproducible facts known at export time:

- ComfyUI version
- Python version
- platform
- GPU backend
- graph hash
- custom-node package records
- model records
- hardware observations
- trust metadata

It is not local install state. Noofy keeps machine-specific preparation state separately when importing and preparing the workflow.

## export-report.json

`export-report.json` summarizes the export event. It includes start and finish timestamps, duration, test-run status, graph adjustments used for the export test, detected node/model counts, runtime metadata, and warnings.

This file is useful for diagnostics and for explaining what the exporter observed on the creator’s machine.

## Assets

`assets/thumbnail.png` is generated from the first exported output image when available. If no output image can be resolved, a generic placeholder thumbnail is used.

The source images selected in `LoadImage` / `LoadImageMask` nodes are not copied into the `.noofy` archive. Imported workflows should receive image inputs through Noofy dashboard controls or user-supplied runtime inputs.

## Model Records

The extension records model references from common loader nodes such as checkpoints, LoRAs, VAEs, ControlNets, CLIP models, diffusion models, style models, and upscale models.

Each model record includes:

- node ID
- node type
- input name
- model type
- ComfyUI model folder
- filename
- SHA-256 hash when the local file can be resolved
- file size when available
- source URLs, empty by default

Models are not bundled into `.noofy` packages. They are usually too large, and Noofy’s import flow validates or resolves models separately.

## Custom Node Bundling

For each detected non-core node, the extension uses ComfyUI’s loaded-node metadata to identify the custom-node package that registered the node. When the package folder or file can be located, the extension bundles it under `custom_nodes/`.

The bundler excludes files and folders that are not useful or are unsafe to carry into an import package, including:

- `.git/`
- `__pycache__/`
- virtual environments
- build and cache folders
- output and temp folders
- model-weight file extensions such as `.safetensors`, `.ckpt`, `.pt`, `.pth`, `.onnx`, and `.gguf`

Each bundled package includes `.noofy-file-manifest.json`, which lists included files, file sizes, and hashes.

Bundled custom nodes remain untrusted community code. Noofy may inspect the files as data, but does not import them or run setup code in the trusted backend process.

## Dependency Markers

The extension records dependency marker files found inside bundled custom-node packages:

- `requirements.txt`
- `pyproject.toml`
- `setup.py`
- `install.py`

`requirements.txt`, `pyproject.toml`, and `setup.py` are recorded in the package metadata for Noofy’s isolated dependency resolver.

`install.py` is recorded with `has_install_py: true`. It is included as a file when bundled, but it is not executed by the exporter. Noofy import does not execute arbitrary install scripts silently.

## Hardware Observations

Hardware fields are observations from the export run, not guarantees.

The package records values such as:

- observed peak RAM
- observed peak VRAM when available
- tested resolution
- tested batch size
- GPU name
- backend

The package does not claim a minimum hardware requirement. Noofy can compare the creator’s observed run against the importing user’s device and present compatibility guidance.

## Noofy Import Semantics

Noofy treats `.noofy` packages as richer imports than raw ComfyUI JSON files.

Compared with raw JSON, a `.noofy` package includes:

- a successful creator-side test run
- runtime metadata
- hardware observations
- model records
- custom-node package records
- bundled custom-node sources when available
- a package descriptor
- an initial dashboard schema
- an export report

Noofy imports these files into its workflow package system and prepares runtime artifacts through the active engine adapter and isolated runner architecture. Community custom nodes from the package are materialized only inside isolated runner workspaces, never into the trusted core runtime.

## Raw ComfyUI JSON Compared With .noofy

Noofy can still import raw ComfyUI JSON files as degraded imports. Raw JSON imports do not include a guaranteed test run, hardware observations, bundled custom nodes, model hashes, package metadata, or dashboard configuration.

`.noofy` is the preferred format for workflows intended to be shared or imported reliably.

## Security Boundaries

The exporter packages files and metadata. It does not make community Python code safe.

Important boundaries:

- The exporter does not execute custom-node dependency installers.
- The exporter does not bundle model files.
- The exporter does not mark creator workflows as Noofy Verified.
- Noofy does not install bundled custom nodes into the trusted core runtime.
- Noofy imports and smoke-tests community workflow code only inside isolated runner processes.

These boundaries match Noofy’s runtime isolation architecture and keep the app backend separate from untrusted community custom-node execution.
