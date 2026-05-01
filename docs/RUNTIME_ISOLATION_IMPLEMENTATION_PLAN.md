# Runtime Isolation Implementation Plan

Date: 2026-04-30

Status: Accepted

This plan implements the accepted runtime isolation architecture in [RUNTIME_ISOLATION_ARCHITECTURE.md](RUNTIME_ISOLATION_ARCHITECTURE.md).

The implementation must stay incremental. Do not build full marketplace/community custom-node installation until the runtime-store, schema, runner-supervisor, install-state, and rollback foundations are in place.

## Phase 0: Decision And Boundaries

Goal: make the accepted runtime direction explicit across the project before changing runtime behavior.

Tasks:

- Treat Tauri and the Noofy backend as the trusted control plane.
- Treat ComfyUI runners as the data plane where custom node code executes.
- State in docs that the backend must not import community custom node code.
- Keep the current Tauri/backend handoff.
- Link the runtime isolation architecture from the main docs index.
- Add a product Python packaging decision record.
- Add a process-tree shutdown decision record for macOS and Windows.

Acceptance criteria:

- Runtime isolation architecture is documented as accepted.
- Implementation plan is documented separately.
- Main architecture docs point to the runtime isolation architecture.
- Product Python and process-tree cleanup are tracked as follow-up decisions.
- No custom-node install behavior is implemented in this phase.

## Phase 1: Runtime-Store Paths And Schemas

Goal: add the filesystem and data-contract foundation without installing community dependencies.

Tasks:

- Add path fields for:
  - `runtime-store/`
  - dependency envs
  - runner workspaces
  - transactions
  - workflow store
  - model store
  - wheel cache
  - custom node cache
- Add schema models for:
  - dependency-env manifest
  - runner-workspace manifest
  - immutable capsule lock
  - mutable install state
  - trust level
  - package identity
- Add package namespace/trust-precedence rules so user imports cannot silently replace Noofy Verified built-ins.
- Add tests for path resolution.
- Add tests for valid and invalid manifest parsing.
- Add tests that install state is separate from capsule lock.

Acceptance criteria:

- Existing Milestone 1 workflow behavior is unchanged.
- `NoofyPaths` or successor path resolution can expose all runtime-store directories.
- Schema tests cover success and likely failure cases.
- `capsule.lock.json` contains no mutable install status fields.
- `install-state.json` carries local mutable state.
- User workflow loading can no longer silently shadow verified built-ins in product rules.
- No backend code imports community custom node modules.

## Phase 2: RunnerSupervisor Abstraction

Goal: introduce runner selection without changing the observable Milestone 1 runner behavior.

Tasks:

- Add `RunnerSupervisor`.
- Add a runner descriptor object containing runner ID, base URL, WebSocket URL, fingerprint, and status.
- Make `RunnerSupervisor` initially return the existing core ComfyUI runner.
- Refactor `EngineService` to request a runner before validation/run operations.
- Add `job_id -> runner_id` registry shape.
- Route progress, cancellation, and result lookup through the job registry.
- Preserve existing API responses where possible.
- Add tests for runner lookup and job routing using the single existing runner.

Acceptance criteria:

- Existing `/api/runtime`, workflow validation, workflow run, progress, cancel, and result behavior still works.
- `EngineService` no longer assumes that all jobs always belong to one implicit global adapter.
- Job routing is explicit, even when it only points to the core runner.
- No process switching is implemented in this phase.

## Phase 3: Verified Core Workflow Install Path

Goal: make built-in/default-node workflows installable through the capsule model before community custom nodes exist.

Tasks:

- Add Noofy Verified capsule locks for built-in starter workflows.
- Add install-state records for verified workflows.
- Add content-hash model records for verified workflow models.
- Add user-facing install states:
  - preparing workflow
  - downloading required models
  - checking compatibility
  - ready
  - cannot prepare automatically
- Add transactional model download and rollback behavior.
- Add diagnostics for install state transitions.
- Add tests for verified install success.
- Add tests for model download or verification failure.

Acceptance criteria:

- Built-in workflows can be represented as Noofy Verified capsules.
- Missing models are tracked by model record/hash, not only by ComfyUI folder/name.
- Failed model preparation does not leave the workflow marked ready.
- Existing workflow execution still routes through the backend API and `EngineAdapter`.
- No custom-node installation is implemented in this phase.

## Phase 4: Layered Fingerprints And Runner Workspaces

Goal: introduce reusable dependency environments and runner workspaces.

Tasks:

- Compute dependency-env fingerprints from:
  - OS and architecture
  - managed Python build
  - Torch/GPU backend profile
  - dependency lock hash
  - native dependency constraints
  - install policy version
- Compute runner fingerprints from:
  - dependency-env fingerprint
  - ComfyUI source hash
  - enabled custom node manifest
  - launch configuration
  - runner model-view configuration
- Compute capsule fingerprints from:
  - workflow package hash
  - graph hash
  - dashboard schema hash
  - model requirement hashes
  - trust/signature metadata
  - runner fingerprint
- Create dependency envs under `runtime-store/envs/dep-env-<fingerprint>/`.
- Create runner workspaces under `runtime-store/runner-workspaces/runner-workspace-<fingerprint>/`.
- Start runner processes from a selected dependency env plus runner workspace.
- Switch runner endpoint per workflow.
- Add smoke tests for runner start, stop, endpoint routing, and adapter configuration.

Acceptance criteria:

- Compatible workflows can reuse a dependency env.
- Different runner workspaces can share a dependency env.
- Ready dependency envs and runner workspaces are immutable.
- Runner startup and shutdown are covered by tests.
- Product shutdown strategy can terminate backend-owned runner processes.

Current implementation notes:

- Runtime artifact manifests are staged during preparation and promoted to `ready` only after the runner smoke test succeeds.
- The runner smoke test starts from the prepared runner workspace, waits for health, and stops the process before install state becomes `ready`.
- Smoke-test failure leaves the workflow install state failed and does not promote staged dependency-env or runner-workspace manifests.
- A later successful prepare can reuse the staged workspace and promote it after smoke succeeds.
- Different runner workspaces can reuse the same ready dependency-env manifest.
- Install-state API payloads expose prepared runtime artifact paths for diagnostics and UI feedback.
- Runner smoke tests emit structured start, pass, and failure diagnostics.
- Workflow runner startup validates persisted dependency-env and runner-workspace manifests with the manifest schemas, requires `ready` status, and checks capsule fingerprint identity before launching.
- Workflow runner startup also requires install-state `smoke_test_status=passed`, so stale ready records cannot launch without a successful smoke result.
- The bundled verified starter capsule uses deterministic Phase 4 `sha256:` dependency, runner, and capsule fingerprints.

## Phase 4.5: Imported Package Foundation

Status: Complete

Goal: make exported `.noofy` workflow packages inspectable and storable as Noofy-owned data before attempting community custom-node installation.

This phase prepares the backend and API surface for community workflows without executing custom-node code, installing community dependencies, or changing the trusted core runtime. Phase 5 starts only after imported packages can be safely normalized and inspected.

Current test artifact:

- `exported-workflow-for-testing.noofy` is a real exported workflow package from the ComfyUI export node.
- It contains `package.json`, `comfyui_graph.json`, `dashboard.json`, `capsule.lock.json`, `export-report.json`, `assets/thumbnail.png`, and bundled `custom_nodes/`.
- It includes both standard ComfyUI nodes and custom nodes.
- It records two required models with their normal ComfyUI folders:
  - checkpoint model: `DreamShaperXL_Lightning.safetensors` in `checkpoints`
  - ControlNet model: `diffusion_pytorch_model_promax.safetensors` in `controlnet`
- It records observed run metrics, including peak RAM, peak VRAM, backend, GPU name, run duration, batch size, and successful export status.
- It does not provide dashboard/input bindings yet. The `LoadImage` node still references the creator-side ComfyUI input image, which is intentionally not bundled.
- The current backend package schemas do not consume this archive shape directly. A `.noofy` importer/normalizer is required before runtime preparation.

Tasks:

- Add a root-level or documented project test command so backend tests run from the expected working directory and phase-specific test failures are not confused with invocation-path failures.
- Add backend-owned imported package storage under `workflow-store/packages/<publisher-id>/<package-id>/<version>/`, or a clearly named successor that preserves publisher, package, version, and trust identity.
- Update workflow package loading so imported packages can be discovered from the package store without silently shadowing Noofy Verified built-ins.
- Extend normalized package schemas for:
  - publisher/package/version identity
  - trust level and source metadata
  - exported package metadata
  - ComfyUI API graph
  - dashboard schema
  - required model records
  - custom node records
  - unresolved runtime inputs
  - thumbnail/assets metadata
  - export report and observed hardware metadata
- Add a `.noofy` archive importer that safely inspects package files as data:
  - validate zip paths before extraction
  - reject absolute paths and `..` traversal
  - enforce required top-level files
  - enforce reasonable file count and archive size limits
  - parse JSON through schema models
  - never import or execute bundled Python modules
- Normalize the archive into Noofy-owned package records and preserve the original exported files for diagnostics.
- Convert exported model records into Noofy model requirements while preserving:
  - model filename
  - model type
  - expected ComfyUI folder
  - SHA-256 hash and size when present
  - source URLs when present
  - identity verification level
  - asset ownership policy
- Add an import-time placeholder dashboard for workflows without configured controls.
- Detect `LoadImage` / `LoadImageMask` nodes that reference creator-local inputs and mark them as unresolved runtime image inputs until creator-mode/dashboard binding exists.
- Add backend API endpoints for importing a `.noofy` archive and inspecting the normalized package record.
- Wire the frontend file picker only to the Noofy backend import API, never to ComfyUI.
- Add user-facing states for imported workflow preparation:
  - imported
  - needs input setup
  - cannot prepare automatically
- Keep the existing Noofy Verified installer restricted to bundled verified capsules; do not broaden it to custom-node or unverified community capsules.

Acceptance criteria:

- Noofy can import the test `.noofy` archive without importing or executing custom node Python code in the trusted backend.
- Imported package metadata, graph, dashboard, capsule/export metadata, model records, custom-node records, assets, and observed hardware metadata are inspectable through backend-owned data structures.
- The imported workflow records both required models and their expected ComfyUI folders: `checkpoints` and `controlnet`.
- The importer detects creator-local `LoadImage` inputs and marks the workflow as needing input setup rather than pretending the original image is available.
- Imported packages are stored under app-owned workflow-store paths with publisher/package/version identity.
- Imported packages cannot silently replace Noofy Verified built-ins by reusing an ID.
- The frontend import control calls only the Noofy backend API.
- Unsupported or incomplete imports produce beginner-friendly states and structured diagnostics.
- No bundled custom node source is materialized into runner workspaces in this phase.
- No normal Python dependencies are installed in this phase.
- The trusted backend does not import custom node modules.
- Backend and frontend tests pass through the documented project test commands.

Current implementation notes:

- A root `make test` command runs backend tests from `backend/` and frontend tests from `frontend/`.
- `.noofy` imports are inspected as zip data with path traversal, absolute path, symlink, required-file, file-count, and size checks before persistence.
- Imported packages are normalized into app-owned `WorkflowPackage` records with identity, trust/source metadata, required model records, custom-node records, unresolved runtime inputs, assets, export metadata, observed hardware, and import status.
- Imported model records preserve the strongest exported identity available:
  - `sha256_size`
  - `filename_size`
  - `filename_only`
- Imported model records normalize asset ownership to the explicit cleanup policy values:
  - `noofy_downloaded`
  - `noofy_imported`
  - `user_local`
  - `external_reference`
- `install-state.json` has a typed `model_references` foundation so Phase 5 can record resolved model blobs, materialized model-view paths, verification level, and ownership without mutating immutable capsule locks.
- Imported packages are stored under `workflow-store/packages/<publisher-id>/<package-id>/<version>/` with the original archive and extracted source files preserved for diagnostics.
- The test archive imports as a Quarantined Community workflow and is marked `needs_input_setup` because its `LoadImage` input points to a creator-local image that is not bundled.
- Workflow summaries expose imported package status, trust level, unresolved input count, custom-node count, and required model count so the UI can avoid presenting imported workflows as simply installed.
- The frontend file picker posts `.noofy` bytes only to the Noofy backend import API.
- The Noofy Verified installer remains restricted to bundled verified capsules and still rejects custom-node or unverified community capsules.
- Failed imports return a beginner-facing error through the API and emit structured diagnostics without executing archive code.
- No bundled custom node source is materialized into runner workspaces and no Python dependencies are installed in this phase.

## Phase 5: `.noofy` Import And Community Custom Node Preparation

Goal: make exported `.noofy` workflow packages usable by Noofy when they include community custom nodes, without mutating the trusted core runtime or existing workflows.

Phase 5 assumes Phase 4.5 has already produced normalized imported package records. This phase turns those records into isolated prepared runtimes when policy allows it.

Next implementation slice:

1. Resolve required model records through the shared model store before smoke tests.
2. Reuse required models by SHA-256 and size when available; treat filename and size as an unverified local candidate only.
3. Record resolved model references, materialized model-view paths, verification level, and asset ownership in install state.
4. Resolve custom-node requirements from normalized imported package records.
5. Materialize bundled custom node sources into staged runner workspaces only.
6. Install normal dependency declarations only into isolated dependency envs.
7. Run custom-node import checks only inside staged runner processes.
8. Run workflow smoke tests only inside staged runner processes.
9. Promote dependency envs and runner workspaces to ready only after smoke tests pass.
10. Reject unresolvable, platform-incompatible, or policy-blocked packages with user-friendly unsupported status.
11. Surface observed hardware metrics as compatibility guidance, not guaranteed minimum requirements.

Model preparation tasks:

- Resolve imported workflow model requirements from normalized package records before custom-node import checks or workflow smoke tests.
- Match existing shared model-store blobs by SHA-256 and byte size when exported identity is available.
- Treat filename and byte size matches as unverified local candidates only when exported hash identity is unavailable.
- Do not treat filename-only matches as trusted model resolution.
- Compute and record local SHA-256 identity for reused local candidates when Noofy reads the file.
- Record resolved model references in `install-state.json`, including:
  - model requirement id
  - ComfyUI folder
  - filename
  - SHA-256 when known
  - byte size when known
  - verification level
  - asset ownership
  - model-store reference or source path
  - materialized model-view path
- Materialize runner-visible model views from the shared model store before runner smoke tests.
- Preserve the cleanup boundary: Noofy-owned blobs or copies may be tracked for future garbage collection, but user-local source files must not be marked as auto-deletable.
- Emit structured diagnostics for model reuse, unverified local candidate reuse, download needed, download failure, hash mismatch, size mismatch, and materialization failure.

Custom-node preparation tasks:

- Add pinned core-node manifest for the supported ComfyUI version.
- Distinguish standard ComfyUI nodes from custom nodes by comparing graph node types to the pinned core-node manifest and the package's custom-node records.
- Materialize bundled custom node sources from `.noofy` archives only into staged runner workspaces.
- Do not copy bundled custom nodes into the trusted core runtime.
- Record custom-node file manifests and dependency marker files as resolver inputs.
- Support normal dependency marker files bundled with custom nodes:
  - `requirements.txt`
  - `pyproject.toml`
  - `setup.py`
- Do not execute arbitrary `install.py` or custom setup scripts for one-click installs.
- Install normal Python dependencies only into isolated dependency envs.
- Run custom-node import checks only in staged runner processes.
- Run workflow smoke tests only in staged runner processes.
- Promote dependency envs and runner workspaces to ready only after smoke tests pass.
- Reject unresolvable, platform-incompatible, or policy-blocked packages with user-friendly unsupported status.
- Add diagnostics that keep technical errors behind developer details.

Registry/source-resolution tasks:

- Add Noofy node registry schema.
- Resolve non-bundled or future package custom node sources through:
  - explicit Noofy metadata
  - registry metadata
  - Noofy-maintained node-type mappings
  - allowed community source-resolution mechanisms
- Download/cache custom node source at resolved refs.
- Apply stricter policy to Noofy Verified and Registry Locked workflows.
- Allow Quarantined Community workflows only when the user explicitly opts in and Noofy can isolate the install.

Acceptance criteria:

- Noofy can import the test `.noofy` archive without importing or executing custom node Python code in the trusted backend.
- Imported package metadata, graph, dashboard, capsule/export metadata, model records, and custom-node records are inspectable through backend-owned data structures.
- The imported workflow records both required models and their expected ComfyUI folders: `checkpoints` and `controlnet`.
- The importer detects creator-local `LoadImage` inputs and marks the workflow as needing input setup rather than pretending the original image is available.
- Imported workflow preparation resolves required models before smoke tests or marks the workflow as not automatically preparable with a beginner-friendly reason.
- Model reuse follows the verification hierarchy: SHA-256 plus size is trusted, filename plus size is unverified, and filename-only is not trusted.
- Install state records resolved model references without mutating immutable capsule locks.
- Unknown custom nodes do not mutate the trusted core runtime or ready workflow environments.
- Bundled custom nodes are materialized only into staged runner workspaces.
- Normal Python dependencies such as `requirements.txt` are installed only inside isolated dependency envs.
- Arbitrary install scripts are not executed for one-click installs unless a future explicit policy allows them.
- The trusted backend does not import custom node modules.
- Failed custom-node install or smoke test does not mutate core runtime, ready envs, or ready runner workspaces.
- Community custom-node workflows can become ready only after staged runner smoke tests pass.
- Observed hardware metrics from `export-report.json` and `capsule.lock.json` are surfaced as compatibility guidance, not as guaranteed minimum requirements.

## Phase 6: Trust, Signing, Marketplace Readiness

Goal: prepare the runtime model for community distribution without weakening isolation.

Tasks:

- Add package signatures or signed registry metadata.
- Define Noofy Verified publishing process.
- Add trust-level UI:
  - Noofy Verified
  - Registry Locked
  - Quarantined Community
  - Unsupported
- Add explicit opt-in policy for unverified/community workflows.
- Add marketplace/package source policy.
- Evaluate OS-level sandboxing feasibility for macOS and Windows.

Acceptance criteria:

- Trust level is visible in workflow install and detail surfaces.
- Unsupported workflows fail gracefully without technical setup language by default.
- Unverified workflows require explicit user opt-in and can still be prepared automatically when Noofy can resolve them into isolated runtime capsules.

## Cross-Phase Requirements

These requirements apply to every phase:

- The frontend calls only the Noofy backend API.
- The frontend never calls ComfyUI directly.
- The backend owns `EngineAdapter` contracts.
- Community custom node code is never imported by the trusted backend process.
- Meaningful runtime behavior changes include success-path and failure-path tests.
- User-facing states avoid terms like `pip`, `venv`, `site-packages`, and stack traces by default.
- Diagnostics include technical details behind developer details.
- Existing Milestone 1 functionality remains working unless a phase explicitly replaces it.
