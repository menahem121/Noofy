# Runtime Isolation Architecture

Date: 2026-04-30

Status: Accepted

## Context

Noofy is a local desktop AI workflow app for macOS and Windows. It hides ComfyUI complexity from beginners while preserving ComfyUI's workflow power behind an app-owned backend API.

Community ComfyUI workflows can include custom nodes. Custom nodes are Python code and commonly require additional Python dependencies. Installing those nodes and dependencies into one global mutable ComfyUI environment would make Noofy fragile:

- one workflow can upgrade or downgrade packages used by another workflow
- broken custom nodes can break the whole engine
- imported Python modules and native extensions cannot be reliably unloaded
- too many custom nodes can slow or destabilize startup
- failed installs can leave a half-mutated environment
- non-technical users cannot be expected to repair Python, pip, virtualenvs, or custom node folders

Noofy supports one-click workflow installation when the workflow can be resolved into an isolated runtime capsule without mutating the trusted core runtime or existing installed workflows.

For unverified community workflows, this means Noofy protects the application architecture from dependency conflicts and broken installs. It does not mean Noofy guarantees the imported code is secure or trustworthy.

Workflows that cannot be resolved into an isolated runtime capsule must fail gracefully without changing the trusted core runtime or any already installed workflow.

## Decision

Noofy must not use one global mutable ComfyUI/Python environment for community workflows.

The accepted architecture is:

- Tauri/Rust owns top-level desktop process lifecycle.
- The Python backend owns workflow/runtime resolution, install state, and runner orchestration.
- The trusted backend process must never import community custom node code.
- Custom node imports, compatibility checks, and smoke tests happen only inside isolated runner processes.
- The trusted core runtime stays immutable and protected from community installs.
- Community workflows are installed as workflow capsules.
- Users can opt in to unverified community workflows; Noofy may automatically prepare them only in isolated runtimes.
- Capsule locks are immutable.
- Local install state is separate and mutable.
- Runtime environments are reused by dependency fingerprint instead of duplicated blindly per workflow.
- Models, wheels, custom node source checkouts, ComfyUI source archives, and downloaded package archives use shared content-addressed caches.
- Product builds must not depend on system Python, Homebrew Python, user PATH, Conda, or developer virtualenvs.

## Consequences

This architecture increases implementation work before broad automated community workflow support ships. In exchange, it gives Noofy a stable foundation for workflow installs, rollback, diagnostics, and support.

Important consequences:

- The current single `RuntimeEnvironment`, single `RuntimeManager`, and single `ComfyUIEngineAdapter` endpoint are Milestone 1 scaffolding, not the long-term runtime model.
- Workflow execution must eventually route through a `RunnerSupervisor`.
- Job progress, cancellation, and result lookup must track `job_id -> runner_id`.
- User workflow packages must not silently override Noofy Verified built-ins by matching an ID.
- Workflow installation must be transactional.
- A failed install must not mutate a ready dependency env, runner workspace, capsule, model record, or the trusted core runtime.
- Product packaging must include or install a Noofy-managed Python/runtime strategy.

## Goals And Non-Goals For Community Workflows

Noofy must support community workflows from the internet as a first-class product direction.

Users should be able to import workflows made by other people without manually installing Python packages, copying custom node folders, editing ComfyUI paths, or troubleshooting dependency conflicts.

When a community workflow contains custom nodes, Noofy should attempt to resolve, download, install, and run those custom nodes automatically inside an isolated workflow runtime. This includes the common ComfyUI pattern where a custom node repository contains a `requirements.txt` file.

Automated installation does not mean Noofy can guarantee that every workflow is safe, trustworthy, or compatible.

The architecture distinguishes three concerns:

- Safety of Noofy itself: Noofy must not break, and one workflow must not corrupt another workflow or the trusted core runtime.
- Security of arbitrary community Python code: Noofy cannot fully guarantee that imported code is safe or trustworthy.
- User convenience: install should still be automated when technically possible.

### Goals

- Allow users to import community-made ComfyUI workflows from the internet.
- Automatically detect required custom nodes when possible.
- Automatically download custom node repositories when their source can be resolved.
- Automatically install normal Python dependencies such as dependencies declared in `requirements.txt`.
- Install dependencies only inside isolated dependency environments, never inside the trusted core runtime.
- Run custom node imports and smoke tests only inside isolated runner processes.
- Prevent one workflow from breaking another workflow.
- Prevent community workflow installs from mutating or corrupting the trusted core runtime.
- Fail gracefully when a workflow cannot be prepared automatically.
- Hide Python, pip, virtualenv, and ComfyUI folder complexity from normal users.
- Provide technical details only behind developer or advanced details.
- Let users choose whether they want to allow unverified community workflows.

### Non-Goals

- No claim that arbitrary workflows from the internet are safe.
- No claim that virtual environments are a security sandbox.
- No guarantee that every community workflow can be installed automatically.
- No guarantee that every custom node repository is trustworthy.
- No guarantee that workflows with missing, broken, abandoned, or platform-specific dependencies will work.
- No automatic installation into the trusted core runtime.
- No silent execution of arbitrary install scripts such as `install.py` without an explicit future policy.
- No OS-level sandboxing guarantee until it is implemented and tested per platform.
- No responsibility for malicious or unsafe workflows imported from untrusted creators.
- No marketplace trust/signing system in the first foundation pass.
- No multi-runner warm pool in the first foundation pass.

### Unsupported

A workflow is unsupported when Noofy cannot prepare it automatically.

Examples:

- unknown custom nodes with no resolvable source
- missing repository or broken download source
- dependencies that cannot be installed on the user's OS/backend
- native builds that fail
- unsupported Python/Torch/GPU requirements
- arbitrary setup behavior that Noofy refuses to run
- missing or unverifiable model sources

Unsupported workflows must fail gracefully with a simple message such as:

> This workflow failed to open.

## Trusted Control Plane And Runner Data Plane

Noofy has two runtime layers.

Trusted control plane:

- Tauri shell
- Noofy Python backend
- app API contracts
- workflow package metadata parsing
- trust policy
- install resolver orchestration
- path resolution
- fingerprint calculation
- runner selection
- diagnostics and user-facing status

Runner data plane:

- ComfyUI runner processes
- dependency environments
- runner workspaces
- enabled custom nodes
- runner-visible model views
- custom node imports
- import checks and smoke tests
- workflow graph execution

The trusted backend may inspect workflow and custom-node files as data. It must not import custom node modules or execute custom node setup code. Community code runs only in data-plane runner processes that can be stopped, discarded, and recreated.

## Trusted Core Runtime

Noofy has a trusted core runtime used for:

- built-in workflows
- verified starter workflows
- core engine health checks
- workflows that use only ComfyUI default/base nodes

The trusted core runtime is content/version locked. Community workflow installs must never install dependencies, custom nodes, or generated files directly into it.

The core runtime may live in the user data directory if it is created or updated after installation. It must still be treated as immutable after preparation. Product builds must not write into the app bundle or Program Files for mutable runtime state.

## Workflow Capsules

A workflow capsule is the resolved install unit for a workflow. It combines the user-facing workflow package with immutable runtime resolution metadata and local install state.

Capsule contents:

- workflow package metadata
- ComfyUI graph
- dashboard schema
- required model records
- required custom node records
- dependency lock reference
- trust metadata
- hardware observation metadata
- layered fingerprints
- immutable capsule lock
- mutable local install state

Large models are not duplicated per capsule. Capsules reference models by content hash in the shared model store.

`package.json` describes what the user runs and sees. `capsule.lock.json` describes what Noofy resolved and trusts. `install-state.json` describes what exists on the current machine.

### Capsule Lock

`capsule.lock.json` is immutable after verification. It contains resolved, reproducible facts:

```json
{
  "schema_version": "0.1.0",
  "workflow": {
    "publisher_id": "noofy",
    "package_id": "starter_text_to_image",
    "version": "1.0.0",
    "package_hash": "sha256:..."
  },
  "engine": {
    "type": "comfyui",
    "comfyui_version": "0.0.0",
    "core_source_hash": "sha256:..."
  },
  "runtime": {
    "dependency_env_fingerprint": "sha256:...",
    "runner_fingerprint": "sha256:...",
    "capsule_fingerprint": "sha256:...",
    "os": "darwin",
    "architecture": "arm64",
    "python_version": "3.11",
    "gpu_backend": "apple_mps",
    "dependency_lock_hash": "sha256:...",
    "runner_workspace_hash": "sha256:..."
  },
  "custom_nodes": [],
  "dependencies": {
    "lock_file": "requirements.lock.json",
    "install_policy": "wheels_only_hash_required"
  },
  "models": [
    {
      "id": "model-id",
      "sha256": "...",
      "size_bytes": 0,
      "source_urls": ["https://..."],
      "comfyui_folder": "checkpoints",
      "filename": "model.safetensors"
    }
  ],
  "hardware_observations": {
    "observed_peak_vram_mb": null,
    "observed_peak_ram_mb": null,
    "tested_resolution": null,
    "tested_batch_size": null,
    "gpu_name": null,
    "os": null,
    "backend": null,
    "precision": null,
    "recommended_vram_mb": null,
    "recommended_ram_mb": null
  },
  "trust": {
    "level": "noofy_verified",
    "publisher": "Noofy",
    "signatures": []
  }
}
```

### Local Install State

`install-state.json` is mutable local state:

```json
{
  "schema_version": "0.1.0",
  "capsule_fingerprint": "sha256:...",
  "status": "ready",
  "installed_at": null,
  "last_used_at": null,
  "dependency_env_path": "runtime-store/envs/dep-env-...",
  "runner_workspace_path": "runtime-store/runner-workspaces/runner-workspace-...",
  "smoke_test_status": "passed",
  "last_error": null
}
```

The lock file is not updated for progress, timestamps, local paths, smoke-test failures, or retry state.

## Package Identity And Precedence

Workflow identity must include namespace/publisher, not only a short workflow ID.

Required identity fields:

- `publisher_id`
- `package_id`
- `version`
- `trust_level`
- `source`
- `signature`

User-imported packages must not silently replace Noofy Verified built-ins. If an imported package conflicts with a built-in display name or legacy ID, Noofy installs it under a distinct namespace or requires an explicit replacement action with clear trust downgrade language.

## Layered Fingerprints

Noofy uses layered fingerprints so compatible runtime pieces can be reused without collapsing isolation boundaries.

### Dependency Environment Fingerprint

The dependency environment fingerprint identifies a Python environment.

Inputs:

- OS and architecture
- Python major/minor version
- Noofy Python build ID
- Torch/GPU backend profile
- dependency lock hash
- native dependency constraints
- install policy version

It does not include workflow graph, dashboard metadata, model files, or enabled custom node source when the node source is mounted in the runner workspace.

### Runner Fingerprint

The runner fingerprint identifies a ComfyUI runner workspace.

Inputs:

- dependency environment fingerprint
- ComfyUI version/source hash
- enabled custom node package IDs and versions/commits
- custom node workspace manifest hash
- launch configuration that affects behavior
- model-view configuration when relevant

### Capsule Fingerprint

The capsule fingerprint identifies the resolved workflow capsule.

Inputs:

- workflow package hash
- ComfyUI graph hash
- dashboard schema hash
- model requirement hashes
- trust/signature metadata
- runner fingerprint

## Dependency Environments

A dependency environment contains:

- managed Python interpreter reference
- virtual environment
- installed wheels/site-packages
- dependency manifest
- smoke-test result for dependency imports

Dependency environments live under:

```text
runtime-store/envs/dep-env-<dependency-env-fingerprint>/
```

Ready dependency environments are immutable. New installs stage a new dependency environment and atomically mark it ready only after validation.

## Runner Workspaces

A runner workspace contains:

- ComfyUI launch directory or materialized source view
- enabled custom nodes
- runner model view
- temp files
- runner manifest
- smoke-test result

Runner workspaces live under:

```text
runtime-store/runner-workspaces/runner-workspace-<runner-fingerprint>/
```

Ready runner workspaces are immutable. They are created from staged transactions and marked ready after the runner process starts, imports required nodes, and passes smoke checks.

## Process Model

```text
Tauri app process
  -> Noofy backend/supervisor sidecar
      -> core ComfyUI runner process
      -> workflow runner process for runner fingerprint A
      -> workflow runner process for runner fingerprint B
```

Rules:

- Tauri starts exactly one Noofy backend/supervisor sidecar.
- The backend/supervisor starts and stops ComfyUI runner processes.
- Each runner process uses one dependency environment and one runner workspace.
- A runner can execute multiple workflows only when they share a compatible runner fingerprint.
- Switching to an incompatible workflow starts the correct runner and stops or idles the old runner according to policy.
- Noofy keeps at most one GPU-heavy runner active by default.
- The backend tracks `job_id -> runner_id`.
- Runner processes receive only runner-scoped secrets if needed.
- Runner processes must not receive the frontend/backend API token injected by Tauri.
- Product shutdown must terminate the full backend/runner process tree.

## Runner Supervisor

The Python backend owns runner orchestration through a `RunnerSupervisor`.

Responsibilities:

- resolve the capsule for a workflow
- verify install state
- select or start the correct runner
- expose runner endpoint details to engine adapters
- track `job_id -> runner_id`
- stop or idle incompatible runners
- collect runner status and diagnostics
- prevent custom node code from entering the trusted backend process

The current `RuntimeManager` should evolve into a lower-level runner process manager. `EngineService` should ask `RunnerSupervisor` for a runner instead of assuming one global adapter endpoint.

## Shared Stores

Shared stores avoid duplication while preserving isolation.

Runtime layout:

```text
Noofy/
  runtime-store/
    python/
      cpython-3.11-<build-id>/
    core-engines/
      comfyui-core-<version>-<source-hash>/
    envs/
      dep-env-<dependency-env-fingerprint>/
        venv/
        manifest.json
        smoke-test.json
    runner-workspaces/
      runner-workspace-<runner-fingerprint>/
        comfyui/
        custom_nodes/
        model-view/
        temp/
        manifest.json
        smoke-test.json
    runners/
      runner-<id>/
        pid
        port
        logs/
    transactions/
      install-<id>/
  workflow-store/
    packages/
      <publisher-id>/<package-id>/<version>/
        package.json
        comfyui_graph.json
        dashboard.json
        capsule.lock.json
        install-state.json
  custom-node-cache/
    <package-id>/<commit-or-version>/
  wheel-cache/
  model-store/
    blobs/sha256/<hash>
    refs/<model-id>.json
    materialized/
      <runner-or-shared-view>/
  outputs/
  logs/
  cache/
```

Shared:

- managed Python distributions
- ComfyUI source cache
- wheels
- custom node source checkouts
- model blobs
- downloaded package archives

Isolated:

- Python site-packages
- runner workspace files
- enabled custom nodes
- runner-visible model view
- runner process state

## Shared Model Store

Models are identified by content hash and metadata, not only folder/name.

Model record:

```json
{
  "id": "stable-diffusion-v1-5-pruned-emaonly-fp16",
  "sha256": "...",
  "size_bytes": 0,
  "source_urls": ["https://..."],
  "license": "unknown",
  "comfyui_folder": "checkpoints",
  "recommended_filename": "v1-5-pruned-emaonly-fp16.safetensors"
}
```

ComfyUI still expects folder/name paths. Noofy materializes a runner-visible model view from the hash store through hardlinks, symlinks, or shared ComfyUI model path configuration. Copying is a last resort because models are large. Windows symlink behavior must be tested for non-developer users.

## Install Resolver

The install resolver verifies and materializes workflow capsules.

For Noofy Verified, Registry Locked, Quarantined Community, or otherwise resolvable workflows:

1. Create an install transaction under `runtime-store/transactions/install-<id>`.
2. Parse `package.json` and immutable `capsule.lock.json`.
3. Verify signatures, hashes, schema versions, trust level, OS/backend compatibility, and policy version.
4. Verify every non-core node is resolved to a package/source in the lock.
5. Verify dependencies are declared or locked according to the policy for the workflow trust level.
6. Compute dependency environment, runner, and capsule fingerprints.
7. Reuse a ready dependency env or create a staged dependency env.
8. Reuse a ready runner workspace or create a staged runner workspace.
9. Download/cache custom node source at resolved refs.
10. Install normal Python dependencies, including dependencies declared in `requirements.txt`, into the staged dependency env according to policy.
11. Materialize required custom nodes into the staged runner workspace.
12. Download models into the shared model store and verify hashes.
13. Create or update the runner model view.
14. Run import/smoke tests in a runner process.
15. Atomically mark dependency env, runner workspace, and install state ready.
16. Delete the transaction directory.

For imported community workflows without a prebuilt lock:

1. Parse metadata and graph in the trusted backend.
2. Identify built-in node types from the pinned core-node manifest.
3. Resolve non-core nodes through explicit metadata, registry metadata, Noofy-maintained mappings, or other allowed source-resolution mechanisms.
4. Inspect custom node repositories as data to discover normal dependency declarations such as `requirements.txt`.
5. Produce a candidate lock when Noofy can resolve sources and dependency policy for an isolated runtime.
6. Continue through the capsule materialization path only after a policy-approved candidate lock exists.
7. Mark the workflow unsupported when Noofy cannot resolve required sources, dependency policy, or runtime compatibility.

Noofy Verified workflows should usually arrive pre-resolved through creator/exporter tooling or verified registry infrastructure. Unverified community workflows may be resolved on the user's machine when the user allows community imports and Noofy can keep the install isolated and transactional.

## Trust Levels

### Noofy Verified

- signed or approved by Noofy
- exact custom node versions pinned
- dependencies locked with hashes
- model hashes and sources verified
- smoke-tested on supported runtime profiles
- one-click install allowed

### Registry Locked

- community workflow
- all custom nodes resolved through known registry metadata
- package refs and dependencies pinned in a lock
- hash and policy checks pass for every install artifact
- no unsupported install behavior
- one-click install allowed only if product policy permits it for the current device/backend

### Quarantined Community

- unverified community workflow
- technically resolvable into an isolated runtime
- requires explicit user opt-in
- can install normal dependency declarations such as `requirements.txt` inside isolated dependency envs
- never affects core runtime or existing ready envs
- must not be described as safe

### Unsupported

- unknown nodes
- custom node source cannot be resolved
- unpinned repositories
- unsafe or arbitrary install scripts
- native builds required where no allowed wheel exists
- unsupported OS/GPU backend
- missing or unverified model sources
- automatic install failed or is not allowed by policy

User-facing unsupported states should use plain language:

- "This workflow is not compatible with one-click install."
- "Noofy could not verify the components required by this workflow."
- "This workflow needs components that are not supported on your device yet."

Technical terms such as `pip`, `venv`, `site-packages`, stack traces, and raw node import errors belong behind developer details.

## Transactional Rollback

Installs are transactional.

Rules:

- Write new install files under `runtime-store/transactions/install-<id>`.
- Never modify the trusted core runtime.
- Never modify an existing ready dependency env in place.
- Never modify an existing ready runner workspace in place.
- Build staged envs and runner workspaces under staging paths.
- Mark ready only after dependency import and ComfyUI runner smoke tests pass.
- Atomically move or register ready artifacts.
- Delete failed staging directories or move them to quarantine for diagnostics.
- Record structured diagnostics with safe summaries and technical details.

## Garbage Collection

Every dependency env, runner workspace, capsule, model, custom node source, and wheel cache entry must track:

- created_at
- last_used_at
- referenced_by
- size_bytes
- status
- trust_level

GC rules:

- never delete active runner envs or runner workspaces
- never delete models referenced by installed ready capsules unless the user explicitly removes them
- delete failed transactions after a retention window
- remove dependency envs and runner workspaces no longer referenced by installed workflows
- apply LRU size caps to wheel and source caches
- protect model blobs by manifest references or reference counts

## Product And Development Python Strategy

Development:

- `backend/.venv` is acceptable.
- `NOOFY_BACKEND_PYTHON` is acceptable.
- external ComfyUI mode remains useful for fast iteration.

Product:

- product builds must not depend on system Python, Homebrew Python, user PATH, Conda, or developer virtualenvs
- product v1 should ship a signed Noofy-managed CPython distribution and core runtime where practical
- `uv` is the preferred environment manager for venv creation, wheel caching, and lock-based installs
- `pip` is a compatibility fallback inside the managed Python
- PyInstaller can package the backend, but it does not solve ComfyUI/custom-node runner environments
- Conda is not the default product strategy

## Security Boundaries

Virtual environments provide dependency isolation, not malicious-code sandboxing.

They help prevent:

- package conflicts between workflows
- dependency upgrades breaking other workflows
- broken custom nodes breaking the trusted core runtime
- global environment pollution

They do not prevent malicious Python code from:

- reading files the runner process can access
- making network requests
- consuming CPU/GPU
- deleting or modifying user-accessible files
- exfiltrating local data if network access is available

Noofy's first security layer is supply-chain control:

- signed/verified package metadata
- pinned commits and hashes
- hash-locked wheels and models
- isolated dependency environments for all community workflow dependencies
- isolated runner processes for all custom node imports and smoke tests
- no arbitrary install scripts for one-click installs unless a future explicit policy allows them
- deny sdists/native builds by default for unverified one-click installs
- no community code imports in the trusted backend process
- separate runner-scoped secrets from frontend/backend API tokens

Future sandboxing work must be evaluated per platform:

- macOS App Sandbox and hardened runtime constraints
- Windows AppContainer or restricted child-process tokens
- network restrictions for runner processes
- per-runner filesystem allowlists
- quarantined unverified workflow mode

## Hardware Compatibility Metadata

Hardware metadata records observations, not guarantees.

Store:

- observed peak VRAM
- observed peak RAM
- tested resolution
- tested batch size
- GPU name
- OS
- backend
- precision
- recommended VRAM with margin
- recommended RAM with margin

UI language must be probabilistic:

- "This workflow was tested with 8 GB VRAM."
- "Your device has 6 GB VRAM."
- "It may run slowly or fail."

Runtime checks must account for resolution, batch size, model, precision, backend, driver, OS, memory fragmentation, and other running apps.

## Risks

- Maintaining a reliable node-type-to-package registry is ongoing product work.
- Many custom nodes have incomplete metadata, unpinned dependencies, or arbitrary setup scripts.
- Python package resolution for Torch/CUDA/Metal stacks is difficult and changes over time.
- Native wheels may be unavailable for some OS/Python/GPU combinations.
- Windows filesystem linking behavior can complicate model deduplication.
- macOS and Windows app signing/notarization can be affected by bundled interpreters and downloaded executable code.
- Untrusted Python code remains dangerous even in a separate virtualenv.
- Multiple warm ComfyUI runners can exhaust VRAM quickly.
- Install times and model downloads may be long.
- Reliable process-tree cleanup is OS-specific and must be tested on macOS and Windows.
- Registry locks generated on the user's machine can fail in technical and unpredictable ways.
- GPLv3 distribution obligations for ComfyUI need legal/product approval when bundling.

## Follow-Up Decisions Needed

- exact product Python distribution strategy for macOS and Windows
- whether backend packaging uses managed Python directly or a standalone executable
- process-tree cleanup implementation for macOS and Windows
- package signature format and verification process
- Noofy Verified package publishing process
- registry metadata format and hosting
- model source trust and license policy
- storage quota and garbage-collection policy
- unverified/community workflow opt-in policy
- OS-level sandboxing feasibility and scope
