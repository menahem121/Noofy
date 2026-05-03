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
- Add a process-tree shutdown decision record for Linux, Windows, and macOS.

Acceptance criteria:

- Runtime isolation architecture is documented as accepted.
- Implementation plan is documented separately.
- Main architecture docs point to the runtime isolation architecture.
- Product Python must be Noofy-managed. Process-tree cleanup is tracked as a follow-up decision and must be designed before deeper Phase 5 runner switching work.
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

## Phase 5: Runtime-Profiled Community Workflow Preparation

Goal: make imported `.noofy` workflow packages preparable and runnable when they include community custom nodes, without mutating the trusted core runtime, existing ready workflow environments, or another workflow's installed artifacts.

Phase 5 implements the strategy in [COMFYUI_RUNTIME_STRATEGY.md](COMFYUI_RUNTIME_STRATEGY.md). It assumes Phase 4.5 has already produced normalized imported package records. Phase 5 turns those records into isolated prepared runtimes only when Noofy can resolve the package under policy.

Phase 5 product scope:

- Use reusable compatibility-group runtimes, not one global mutable runtime and not one full runtime per workflow.
- Ship one pinned Noofy-owned ComfyUI runtime profile family in v1, with explicit platform/backend variants in schema.
- Keep `ComfyUI-official-repo/` as a local development and source-reference copy only. It must not be treated automatically as the product runtime source.
- Base the v1 product runtime profile on a clean reproducible ComfyUI source artifact, preferably the most recent stable upstream release at profile-generation time, materialized under Noofy's runtime store.
- Use ComfyUI README guidance for Python, PyTorch, CUDA, and backend support when selecting runtime profile variants.
- Do not use a floating "latest ComfyUI" as the runtime contract. Runtime profiles record exact source and dependency hashes.
- Do not silently fall back to a close-enough runtime profile.
- Support bundled custom node source from imported `.noofy` packages first.
- Defer registry lookup, non-bundled custom-node source resolution, and broad candidate lock generation until the locked/bundled path works end to end.
- Implement the v1 [Memory Governor](MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md): one GPU-heavy resident remains the safe fallback, but multiple warm runners are allowed when memory class, local learned observations, machine profile, and safety margins make co-residence low risk.
- Keep loaded runners and models warm while a compatible workflow is currently open in Noofy and the Memory Governor allows retention.
- Store creator/export memory observations in `.noofy` packages as advisory hints. Store learned local Memory Governor metrics only in local app data during normal use.
- Queue incompatible workflow runs while another job is running.
- Expose normal job cancellation, but do not add a dedicated "cancel and switch" action in v1.
- Do not implement an uncontrolled multi-runner warm pool outside Memory Governor admission, observation, eviction, retry, and diagnostics.
- Treat dependency environments as conflict isolation, not as a security sandbox.
- Product builds must use Noofy-managed Python.
- Process-tree cleanup must be designed before deeper runner switching work: process groups on macOS/Linux, and Job Objects or equivalent on Windows.
- Phase 5 development uses `exported-workflow-for-testing.noofy` as the main community-style `.noofy` package fixture. Phase 5e also keeps smaller purpose-built `.noofy` fixtures under `test_workflows/` so the staged smoke suite can validate core, custom-node, and multi-model cases quickly and repeatably.
- Keep unverified community workflow opt-in and trust UI aligned with Phase 6; Phase 5 may expose backend states before the full marketplace/trust UI exists.

Phase 5 status vocabulary:

- `imported`
- `needs_input_setup`
- `preparing`
- `resolving_runtime_profile`
- `resolving_models`
- `resolving_dependencies`
- `materializing_custom_nodes`
- `materializing_model_view`
- `checking_compatibility`
- `smoke_testing`
- `ready`
- `prepared_needs_input_setup`
- `cannot_prepare_automatically`
- `unsupported_runtime_profile`
- `blocked_by_policy`
- `failed`

Workflows with unresolved runtime inputs can have prepared runtime artifacts, but they must not be presented as `ready` to run. They remain `prepared_needs_input_setup` or `needs_input_setup` until the missing inputs and a workflow-level smoke test are resolved.

### Phase 5a: Runtime Profile Catalog, Schema Versioning, And Fingerprints

Goal: make the runtime profile and fingerprint boundaries explicit before installing or running community code.

Tasks:

- Add `COMFYUI_RUNTIME_STRATEGY.md` to the developer documentation index.
- Add a runtime profile catalog schema with:
  - `runtime_profile_id`
  - `runtime_profile_manifest_hash`
  - `runtime_profile_variant_id`
  - ComfyUI core version and source hash
  - ComfyUI frontend package name and version
  - Noofy-managed Python build ID
  - Torch version and wheel build tag
  - GPU backend profile
  - core dependency lock hash
  - allowlisted launch configuration defaults
  - supported OS/architecture/backend matrix
  - install policy version
  - profile signature or signed manifest reference
- Ship exactly one v1 runtime profile family in the catalog, with explicit variants for the supported development/product backends.
- Add product ComfyUI source acquisition/materialization under `runtime-store/core-engines/comfyui-core-<version>-<source-hash>/`.
- Product runtime sources may come from:
  - an upstream stable Git tag
  - a verified upstream source archive
  - a Noofy-vendored source snapshot with an explicit source manifest
- Keep `ComfyUI-official-repo/` available as a dev/reference source only. A runtime profile generated from it must be marked development-only.
- Generate the v1 product profile from the clean runtime-store source artifact and record:
  - source origin kind and reference
  - ComfyUI source hash
  - source manifest hash
  - source cleanliness and reproducibility status (`clean_reproducible` for product profiles)
  - README-derived Python/PyTorch/backend guidance used for the selected variant
- Reject product runtime profile generation from `ComfyUI-official-repo/`, dirty source trees, ignored runtime folders, or source artifacts with missing/ambiguous provenance.
- Ensure local runtime artifacts such as `models/`, `custom_nodes/`, `input/`, `output/`, and test-only files are excluded from product ComfyUI source identity.
- Add schema support for multiple profile families and variants even though v1 ships one family.
- Update capsule lock schemas so workflow capsules reference `runtime_profile_id`.
- Update install-state schemas so local resolution records:
  - selected `runtime_profile_variant_id`
  - `runtime_profile_manifest_hash`
  - runtime profile catalog version
  - dependency environment fingerprint
  - runner workspace fingerprint
  - runner process compatibility key when model-view startup behavior requires it
  - capsule fingerprint
- Define byte-stable canonical serialization for all Phase 5 fingerprints.
- Add a fingerprint schema version and require schema-version bumps when fingerprint inputs change.
- Include `runtime_profile_manifest_hash` and selected `runtime_profile_variant_id` in dependency-env and runner-workspace fingerprints.
- Keep machine-local paths, hardlink/symlink/copy materialization choices, and user-local source paths out of fingerprints.
- Add unsupported states for:
  - missing runtime profile
  - unsupported profile variant on the current OS/backend
  - profile manifest hash mismatch
  - unsupported fingerprint schema version
- Update the bundled verified starter capsule and tests to keep working with the new profile fields.

Acceptance criteria:

- Runtime profile catalog parsing has success and failure tests.
- Fingerprint calculation is stable across dictionary ordering and process restarts.
- Missing, mismatched, or unsupported runtime profiles fail before dependency or custom-node preparation starts.
- Product profile generation rejects dirty ComfyUI source trees; development profiles may record dirty state explicitly.
- Fingerprints include profile manifest hash and selected variant ID.
- Fingerprints do not include local absolute paths.
- Existing verified starter workflow behavior still works.

### Phase 5b: Dependency Lock Policy And Dependency Environment Preparation

Status: Complete

Goal: install normal Python dependencies into reusable immutable dependency environments using a deterministic lock, without executing arbitrary custom-node setup code.

Tasks:

- Use `uv` as the primary resolver, wheel cache manager, and dependency environment installer.
- Persist a Noofy-owned JSON dependency lock as the app contract. The lock records resolved wheel facts and `uv` resolver metadata; raw `uv.lock` is not the long-term public schema.
- Keep `pip` only as a compatibility fallback for existing managed-core bootstrap paths unless it can enforce the same wheels-only, hash-required community policy.
- Add a resolved dependency lock schema containing, for each wheel:
  - normalized package name
  - exact version
  - wheel filename
  - SHA-256 hash
  - source index URL or approved cache reference
  - platform tags and environment markers
  - direct/transitive dependency relationship
  - resolver name and version
  - runtime profile ID, variant ID, and manifest hash
  - install policy version
- Merge the runtime profile core dependency lock with custom-node dependency locks into one dependency-env lock.
- Compute dependency-env fingerprints only from resolved lock facts and runtime profile facts, not from raw dependency declarations.
- Support pre-resolved locks first. For bundled custom-node sources, allow local lock generation only from normal dependency marker files under the strict community policy.
- Inspect `requirements.txt`, `pyproject.toml`, and `setup.py` as data. Do not execute custom-node `setup.py`, editable installs, project build hooks, arbitrary install scripts, or custom setup commands.
- If `pyproject.toml` or `setup.py` dependency extraction requires executing project code, mark the workflow unsupported under the default community policy.
- Enforce default Quarantined Community dependency policy:
  - wheels only
  - hash required for every wheel
  - no sdists
  - no native source builds
  - no arbitrary install scripts
  - no downloads outside Noofy's approved resolver/materializer path
- Use the shared wheel cache, but verify wheel hashes before install.
- Create dependency environments only under staged transaction paths.
- Reuse a ready dependency environment when its manifest and fingerprint match exactly.
- Never mutate an existing ready dependency environment in place.
- Record dependency install diagnostics without exposing raw stack traces by default.

Acceptance criteria:

- Identical resolved locks reuse the same ready dependency environment.
- Conflicting dependency requirements produce `cannot_prepare_automatically` or `blocked_by_policy`, not a mutated existing env.
- Missing wheel, hash mismatch, sdist-only package, native build requirement, and setup-code execution attempts have failure tests.
- Failed dependency installation leaves no ready dependency-env manifest.
- The trusted backend never imports custom node modules during dependency resolution.

Current implementation notes:

- Noofy resolved dependency locks are persisted under `runtime-store/dependency-locks/<lock-hash>/dependency-lock.json`.
- The dependency lock resolver uses `uv pip compile --generate-hashes --no-build --only-binary :all:` and materializes hash-verified wheels into the shared wheel cache before installation.
- Dependency marker discovery reads `requirements.txt`, PEP 621 `pyproject.toml` dependencies, and static optional dependencies as data only.
- `setup.py`, dynamic `pyproject.toml` dependency fields, editable installs, local paths, URL requirements, alternate index flags, constraints, and recursive requirement files are blocked before resolver execution.
- Dependency environments are installed with `uv` from Noofy resolved locks only, using `--require-hashes`, `--only-binary :all:`, `--no-index`, and `--find-links` pointed at the shared wheel cache.
- Imported package source files are connected to runtime preparation through the package store, so bundled custom-node dependency markers can generate a local dependency lock without importing custom-node Python.
- Runtime preparation merges the stored core dependency lock with the generated custom-node dependency lock, stores the combined lock, recomputes the dependency-env fingerprint when the capsule lock hash is stale, and installs into a staged transaction path before promotion.
- Custom-node workflow packages can prepare dependency environments, but they remain `prepared_needs_input_setup` until Phase 5c materializes custom-node workspaces and compatibility smoke tests.

### Phase 5c: Core Node Manifest And Custom-Node Workspace Materialization

Status: Complete

Goal: materialize only the custom nodes required by a workflow into a staged runner workspace with deterministic manifests and no trusted-core mutation.

Tasks:

- Add a pinned core-node manifest for each runtime profile variant.
- Distinguish standard ComfyUI nodes from custom nodes by comparing graph node types to the pinned core-node manifest and normalized custom-node records.
- Treat unknown non-core node types as unsupported until they are resolved by bundled metadata or a later registry phase.
- Materialize bundled custom node sources from `.noofy` archives only into staged runner workspaces.
- Do not copy bundled custom node source into the trusted core runtime.
- Do not install custom node source into `site-packages` as editable packages under the default community policy.
- Build a deterministic custom node workspace manifest with:
  - custom node package ID
  - source kind (`bundled_archive`, later `registry`, etc.)
  - source ref or exported archive identity
  - source content hash
  - materialized relative path
  - deterministic import order index
  - dependency marker file hashes
  - package trust level
  - policy-relevant flags when known
- Reject materialization if bundled source contains:
  - absolute paths
  - `..` traversal
  - symlinks or junctions that escape the staged workspace
  - duplicate paths that collide on case-insensitive filesystems
  - file count or file size above policy limits
  - names that shadow protected Noofy or ComfyUI runtime paths
- Define and hash the allowlisted launch configuration surface:
  - preview method
  - VRAM mode
  - attention backend
  - precision policy
  - enabled custom node set
  - extra model paths mode
  - Noofy-controlled environment variables
- Reject unsupported launch options rather than folding them into the runner silently.

Acceptance criteria:

- Built-in nodes are recognized from the profile's pinned core-node manifest.
- Bundled custom nodes materialize only into staged runner workspaces.
- Custom node materialization is deterministic across filesystem ordering.
- Path traversal, symlink escape, case-insensitive collision, oversized source, unknown node, and protected path shadowing failures are covered by tests.
- Ready trusted core runtime files are unchanged after custom-node preparation.

Current implementation notes:

- Pinned core-node manifests are stored in `backend/app/runtime/core_node_manifest.json` for the v1 runtime profile variants.
- `CustomNodeWorkspaceMaterializer` reads workflow graphs and capsule custom-node records as data, recognizes built-in node types from the pinned core-node manifest, and rejects unknown non-core node types before materialization.
- Imported `.noofy` packages now persist an app-owned `capsule.lock.json` that is loadable by `CapsuleLockLoader`, while preserving the original exporter capsule separately as `exported-capsule.lock.json`.
- Imported package capsule generation chooses a concrete v1 runtime profile variant at import time as a Phase 5c bridge so runtime artifacts can be prepared. The import report records this `runtime_resolution`; later runner/model-view phases may replace it with an adapter-aware resolution pass before install.
- Bundled custom-node source is materialized only into staged runner workspaces under `custom_nodes/<folder>/`; trusted core runtime `custom_nodes/` is excluded from the runner source view and is not mutated.
- The custom-node workspace manifest records source kind, source ref, source content hash, materialized path, import order, dependency marker hashes, trust level, policy flags, node types, and a stable manifest hash.
- Runner workspace fingerprints now use the custom-node workspace manifest hash as the enabled custom-node set when custom-node source is available.
- The runner launch configuration hash is built from the allowlisted launch surface: preview method, VRAM mode, attention backend, precision policy, enabled custom-node set, extra model paths mode, and Noofy-controlled environment variables. Exported package launch options outside this app-owned surface are rejected during import instead of being silently folded into the runner.
- Materialization rejects path traversal, symlink escape, case-insensitive path collisions, oversized source, unknown node types, protected runtime path shadowing, and unsupported custom-node source kinds.
- `exported-workflow-for-testing.noofy` is covered by an integration test that imports the archive, loads the normalized capsule lock, and materializes all required bundled custom-node sources into a staged runner workspace.

### Phase 5d: Shared Model Store And Runner Model-View Materialization

Status: Complete for Phase 5e readiness

Goal: resolve required models through the shared model store and create runner-visible model views without duplicating or deleting user assets incorrectly.

Tasks:

- Resolve imported workflow model requirements before custom-node import checks or workflow smoke tests.
- Reuse existing model-store blobs by SHA-256 and byte size when exported identity is available.
- Treat filename plus byte size as an unverified local candidate only when exported hash identity is unavailable.
- Never treat filename-only matches as trusted model resolution.
- Compute and record local SHA-256 identity when Noofy reads a reused local candidate.
- Record model references in `install-state.json`, including:
  - model requirement ID
  - ComfyUI folder
  - expected filename
  - SHA-256 when known
  - byte size when known
  - verification level
  - asset ownership
  - model-store reference or user-local source path
  - materialized model-view path
  - materialization strategy (`hardlink`, `symlink`, `copy`)
  - materialized file verification result
- Materialize model views outside immutable runner workspaces, for example under `runtime-store/model-store/materialized/views/model-view-<fingerprint>/`.
- Use a per-capsule or per-compatible-view materialized model view by default. Do not overwrite a shared folder/name when two workflows require different blobs at the same ComfyUI folder/name.
- Do not implement graph rewriting to collision-free aliases in Phase 5. Use separate model views for folder/name collisions. Graph rewriting may be revisited later only through a tested `ComfyUIEngineAdapter` rewrite layer.
- Generate runner process configuration from the selected model view without mutating a ready runner workspace.
- Use the materialization fallback ladder:
  1. hardlink when source and destination are on the same volume and supported
  2. symlink when hardlink is unavailable and symlink permissions are available
  3. copy as a last resort
- Probe Windows symlink capability during preparation and fall back cleanly.
- Handle case-insensitive filename collisions, Windows path length limits, cross-volume hardlink failures, stale links, antivirus/file-lock copy failures, and missing target blobs.
- Preserve cleanup boundaries:
  - `noofy_downloaded` and `noofy_imported` app-owned copies may be garbage-collected when unreferenced
  - `user_local` originals must never be deleted
  - `external_reference` sources must never be deleted
- Emit structured diagnostics for model reuse, unverified candidate reuse, download needed, download failure, hash mismatch, size mismatch, materialization fallback, and materialization failure.

Acceptance criteria:

- Model reuse follows the verification hierarchy: SHA-256 plus size is trusted, filename plus size is unverified, filename-only is not trusted.
- Install state records resolved model references without mutating capsule locks.
- Materialized model views present the exact files expected by the runner before smoke tests.
- Name collision, missing blob, stale symlink, Windows symlink-denied, cross-volume hardlink, and copy failure cases have tests or platform-specific test fixtures.
- User-local model source files are never marked auto-deletable.

Current implementation notes:

- `ModelStore.materialize_model_view(...)` creates per-view model trees under `runtime-store/model-store/materialized/views/model-view-<fingerprint>/`.
- Model view fingerprints include the view ID plus each model's SHA-256, byte size, ComfyUI folder, and expected filename, so workflows with the same folder/name but different blobs do not overwrite a shared view.
- The materialization ladder now tries hardlink first, then symlink where allowed, then copy as the last fallback. The selected strategy is recorded in `install-state.json` model references.
- `CapsuleInstaller` resolves required model blobs and materializes the model view before runtime workspace preparation, then passes the selected model view into `RuntimeWorkspacePreparer`.
- Install state records resolved model references with requirement ID, ComfyUI folder, filename, SHA-256, byte size, verification level, asset ownership, store ref, blob path, materialized model-view path, materialization strategy, and file verification result.
- Runner workspaces link/copy the selected model view as their `models` directory without mutating ready runner workspaces.
- Covered: SHA-256/size blob reuse, filename+size local candidate reuse with locally computed SHA-256, per-view materialization, conflicting same folder/name with different blob rejection, install-state model-reference persistence, hardlink/symlink/copy strategy recording, stale model-view repair, missing blob/view/source validation at runner startup, copy-failure cleanup, cross-volume hardlink fallback, symlink-denied fallback, Windows path-length rejection, filename-only model requirement blocking, explicit model-reference cleanup policy, and a symlink capability probe used before Windows symlink attempts.
- No Phase 5d implementation work remains. Linux CUDA staged validation is complete on the Ubuntu A10G qualification host; Windows/macOS filesystem fallback validation remains part of broader platform readiness, not Phase 5e.

### Phase 5e: Runner Smoke Tests And Minimal Graph Execution

Status: Implementation complete; Ubuntu A10G staged real ComfyUI validation passed

Goal: promote runtime artifacts only after the staged environment and runner prove they can import, start, and execute real work.

Tasks:

- Split smoke status into:
  - dependency-env smoke
  - custom-node import smoke
  - runner health smoke
  - workflow execution smoke
- Run dependency import checks inside the staged dependency environment.
- Run custom-node import checks only inside a staged runner process started from the staged dependency env and staged runner workspace.
- Start the staged ComfyUI runner on a selected localhost port and wait for health.
- Verify `/object_info` or equivalent node metadata includes required core and custom node types after import.
- Run a minimal real graph execution test. It must execute real nodes, not only check registration.
- Prefer package-provided smoke fixtures when present. Otherwise generate a tiny graph only when Noofy can do so without changing workflow semantics or requiring missing user inputs.
- Execute real staged smoke graphs on the Ubuntu CUDA qualification host with the productized `make phase5e-real-smoke` suite. Keep fake/lightweight runner tests for fast default coverage where real ComfyUI execution is unavailable.
- Product readiness still requires real smoke execution before a workflow is marked `ready`. If real execution cannot be run, report `prepared_needs_input_setup`, `cannot_prepare_automatically`, or a developer-only skipped-smoke state rather than `ready`.
- If unresolved runtime inputs prevent workflow execution smoke, keep the workflow out of `ready` and report `prepared_needs_input_setup`.
- Use minimal resolution, minimal step count, and bounded timeouts for smoke graphs.
- Collect runner logs and smoke-test outputs into transaction diagnostics.
- Require all applicable smoke stages to pass before marking a workflow `ready`.
- Quarantine failed staging directories for a bounded retention window; do not promote them.

Acceptance criteria:

- Import-only checks are not sufficient for `ready`.
- Dependency import, custom-node import, runner health, and tiny execution success paths are tested with fake/lightweight runners where real ComfyUI execution is unavailable.
- Real ComfyUI smoke execution paths exist as opt-in local integration tests, skipped by default unless suitable hardware and ComfyUI dependencies are provided.
- Dependency import failure, custom node import failure, runner startup timeout, node registration missing, workflow execution failure, and unresolved input cases are tested.
- A workflow with unresolved `LoadImage` or `LoadImageMask` input is not presented as ready to run.
- Failed smoke tests do not mutate trusted core runtime, ready dependency environments, ready runner workspaces, or existing install states.
- Failed smoke tests write bounded-retention quarantine markers for staged dependency-env and runner-workspace artifacts without promoting them.

Current implementation notes:

- Install state now records a split `smoke_test_report` with dependency-env, custom-node import, runner-health, and workflow-execution stages while preserving the existing summary `smoke_test_status`.
- `CapsuleInstaller` moves staged runtime artifacts through `smoke_testing` and only promotes dependency envs / runner workspaces to `ready` when all required smoke stages pass.
- The bundled `text_to_image_v0` package declares a model-free `EmptyImage -> SaveImage` smoke fixture, so its smoke execution does not need to run the full model-heavy generation graph.
- Core-only packages without an explicit smoke fixture receive the same safe model-free fallback fixture. Custom-node packages do not receive this fallback because their smoke fixture must exercise at least one declared custom node type.
- Custom-node workflows can now become `ready` when isolated workspace preparation exists and every required smoke stage passes. Custom-node workflows without a workspace preparer still stop at `prepared_needs_input_setup`.
- Isolated runner launch args keep `--disable-all-custom-nodes` for default-node workflows, but omit it for materialized custom-node runner workspaces so staged custom nodes can actually import and execute inside the runner.
- The current `RunnerSmokeTester` runs dependency wheel import smoke from the staged dependency environment before runner startup, preferring explicit dependency-lock `import_names` over best-effort wheel metadata inference. It verifies `/object_info` node registration when custom node types or a smoke fixture require it, and can execute a package-declared lightweight prompt fixture from `smoke_tests.workflow_execution`. For custom-node capsules, a declared execution fixture must exercise at least one declared custom node type before the fixture can pass. Fixture execution can assert expected output node count and output node ids, and timeout failures report fixture name, timeout seconds, and prompt id when available. Without a workflow execution fixture it marks workflow execution as blocked. Health-only smoke is not sufficient for `ready`.
- Runner startup smoke failures now carry the split smoke report through to install state, so `runner_health=failed` is preserved instead of being flattened to an empty report.
- Workflows with unresolved runtime inputs force the workflow-execution stage to `blocked` and remain `prepared_needs_input_setup`.
- Failed smoke exceptions or failed smoke report stages write `quarantine.json` markers with `retain_until` metadata into staged runtime artifact directories. Phase 5g startup sweep now deletes expired quarantine artifacts.
- Fake/lightweight tests cover full-pass smoke reports, dependency import failure, runner health failure, missing execution smoke, object-info node registration failures, custom-node fixture exercise requirements, failed-staging quarantine markers, and unresolved runtime input blocking.
- The optional external real ComfyUI smoke validation can be run with `NOOFY_REAL_COMFYUI_SMOKE=1`, `NOOFY_REAL_COMFYUI_SMOKE_PROMPT=<small ComfyUI API prompt JSON>`, and optionally `NOOFY_REAL_COMFYUI_BASE_URL` / `NOOFY_REAL_COMFYUI_SMOKE_TIMEOUT`.
- The optional staged real ComfyUI smoke validation can be run with `NOOFY_REAL_STAGED_COMFYUI_SMOKE=1`, `NOOFY_REAL_COMFYUI_SOURCE_DIR=<ComfyUI checkout>`, `NOOFY_REAL_COMFYUI_PYTHON=<Python with ComfyUI deps>`, `NOOFY_REAL_COMFYUI_SMOKE_PROMPT=<small ComfyUI API prompt JSON>`, and optionally `NOOFY_REAL_COMFYUI_SMOKE_TIMEOUT` / `NOOFY_REAL_COMFYUI_SMOKE_MIN_OUTPUTS`. It materializes a Noofy staged runner workspace and executes through `RunnerSmokeTester`, so it is the preferred Phase 5e hardware-readiness check. Both real smoke paths are intentionally skipped in default test runs until suitable hardware is available.
- The productized staged validation suite can be run with `make phase5e-real-smoke`. Override `COMFYUI_SOURCE_DIR`, `COMFYUI_PYTHON`, `PHASE5E_SMOKE_WORK_DIR`, or `PHASE5E_SMOKE_SUMMARY` when validating a different server. The underlying command is `python -m app.runtime.phase5e_real_smoke`; it runs the model-free, SD1.5, custom-node, KJ custom-node, and ControlNet two-model scenarios and writes a JSON summary.

Ubuntu staged validation completed on 2026-05-03:

- Host: Ubuntu, NVIDIA A10G, driver 595.58.03, CUDA 13.2, ComfyUI venv with torch `2.11.0+cu130`.
- `EmptyImage -> PreviewImage` model-free staged smoke passed through `NOOFY_REAL_STAGED_COMFYUI_SMOKE`.
- `core_sd15_txt2img.noofy` passed with staged model view exposing `checkpoints/v1-5-pruned-emaonly-fp16.safetensors`; output node `9` completed.
- `custom_node_no_deps_success.noofy` passed with `ComfyUI_JPS-Nodes` materialized into the staged runner workspace; `Crop Image TargetSize (JPS)` registered and executed.
- `custom_node_with_pypi_dep_success.noofy` passed with `comfyui-kjnodes` materialized into the staged runner workspace; `ImageResizeKJv2` registered and executed.
- `exported-workflow-for-testing.noofy` / `controlnet_two_model_workflow` passed with staged model view exposing `checkpoints/DreamShaperXL_Lightning.safetensors` and `controlnet/diffusion_pytorch_model_promax.safetensors`; all required custom-node types registered and output node `144` completed.

Completion gate:

- None for Phase 5e on the current Ubuntu CUDA qualification host. Re-run `make phase5e-real-smoke` after changes to runtime staging, smoke execution, dependency handling, model-view materialization, custom-node materialization, or ComfyUI runner launch behavior.

### Phase 5f: RunnerSupervisor Switching, Idle-Warm Policy, And Memory Governor

Goal: make runtime switching predictable and fast by using a v1 Memory Governor that can keep multiple runners warm when safe, evict intelligently when needed, retry after memory cleanup when safe, and fall back to one GPU-heavy resident runner when signals are weak.

Tasks:

- Implement the v1 Memory Governor described in [MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md](MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md).
- Design process-tree cleanup before implementing deeper runner switching:
  - use process groups or equivalent process-tree termination on macOS/Linux
  - use Windows Job Objects or equivalent containment on Windows
  - verify child custom-node processes do not survive runner stop or app shutdown
- Extend runner descriptors with:
  - runner ID
  - runner workspace fingerprint
  - dependency environment fingerprint
  - runner process compatibility key
  - model-view fingerprint when required
  - runtime profile ID and variant ID
  - memory class (`gpu_heavy`, `gpu_medium`, `gpu_light`, `cpu_only`, `unknown`)
  - memory estimate confidence and source
  - observed idle RAM/VRAM footprint
  - observed load/execution RAM/VRAM peaks
  - recent memory error state
  - base URL and WebSocket URL
  - process ID
  - state
  - current job ID
  - last used timestamp
  - open workflow lease count or lease IDs
  - closed-view cooldown expiry when no compatible workflow view remains open
- Add runner states:
  - `missing_runtime`
  - `preparing`
  - `starting`
  - `idle`
  - `running`
  - `queued`
  - `queued_pending_switch`
  - `queued_pending_memory`
  - `idle_warm`
  - `stopping`
  - `switching`
  - `evicting_runner`
  - `waiting_for_memory_release`
  - `loading_model`
  - `retrying_after_memory_cleanup`
  - `failed`
  - `blocked_by_memory`
  - `memory_cleanup_failed`
  - `evicted_for_memory`
  - `co_resident`
- Make workflow run requests ask `RunnerSupervisor` for the correct runner. The frontend must not choose runner endpoints.
- Add backend APIs for workflow-open leases so the frontend can report when a workflow view opens and closes. The backend remains authoritative and may evict runners for memory pressure, process failure, shutdown, or explicit cancellation.
- Reuse the current runner when the requested workflow is compatible with the current runner process key.
- If an incompatible runner is requested while the current runner is running a job, queue the new job as `queued_pending_switch` by default.
- Add a normal Cancel action for the currently running job. Do not add a dedicated "cancel current and switch" action in v1.
- Keep one resident GPU-heavy runner as the safe fallback, not the whole policy. Treat unknown memory class as GPU-heavy/high-risk until local observations prove otherwise.
- Add memory observations for CUDA first, with conservative interfaces for MPS, DirectML, CPU-only, and unavailable metrics.
- Build memory estimates from repeated local observations, single local observations, `.noofy` creator observations, model metadata, workflow type, resolution, batch size, and conservative heuristics.
- Persist local run observations in local app data, not in `.noofy` packages or immutable capsule locks: workflow ID, runner fingerprint, models, resolution, batch size, backend, machine profile, duration, idle/load/execution RAM/VRAM peaks, success/failure, memory error, eviction, and retry.
- Feed local learning into Memory Governor confidence: repeated local successes under similar settings raise confidence; local memory failures lower confidence and trigger more conservative future decisions for that workflow, backend, machine profile, and similar settings.
- Treat creator `.noofy` metrics as initial hints only until local evidence exists. Learned local metrics improve confidence but never become absolute guarantees.
- Add a co-residence policy that allows multiple warm runners only when memory class combinations, estimates, local history, current memory snapshots, safety margins, and recent error state make the decision low-risk.
- Keep a compatible runner `idle_warm` while at least one compatible workflow view is open and no incompatible GPU-heavy runner or memory-pressure condition requires eviction.
- When the last compatible workflow view closes, start a closed-view cooldown. Default closed-view cooldown is 90 seconds and is configurable.
- Evict idle-warm runners before starting incompatible or memory-risky workflows when the Memory Governor cannot keep co-residence within margin.
- Allow co-resident runners only through the Memory Governor. `gpu_light` and `cpu_only` can co-reside more often; `gpu_medium` requires reliable estimates and margin; `gpu_heavy + gpu_heavy` requires large machines, high-confidence local observations, and large safety margin.
- Before starting a new memory-risky runner, stop selected evicted runner process trees and wait for bounded VRAM/RAM release checks. The check may be heuristic but must time out and produce diagnostics.
- Detect likely memory errors, stop idle runners, wait for memory release, retry once when safe, and record the outcome so Noofy avoids repeating the same optimistic decision.
- Record observed startup time, stop time, crash count, restart count, idle-warm evictions, co-residence admits/denies, memory estimates, safety margins, retry attempts, and blocked-by-memory events.

Acceptance criteria:

- Switching tabs does not start or stop runners.
- Running an incompatible workflow while another job is active queues by default.
- Cancel stops the currently running job through the job registry. There is no dedicated cancel-and-switch action in v1.
- The Memory Governor admits multiple warm runners only when memory classes, estimates, local learned observations, current memory snapshots, safety margins, and recent memory-error state allow it.
- If Memory Governor confidence is low, Noofy falls back to one resident GPU-heavy runner.
- Repeated successful local runs under similar settings raise confidence for warm retention and co-residence. Local memory failures lower confidence and prevent repeating risky decisions automatically.
- Idle-warm reuse while a workflow remains open, co-resident warm runners, co-residence denial, closed-view cooldown expiry, memory-pressure eviction, process crash, process-tree cleanup, memory-release timeout, memory-error retry, and blocked-by-memory cases are tested.
- Job progress, cancellation, and result lookup continue to route through `job_id -> runner_id`.

Current implementation notes:

- `RunnerDescriptor` now carries runner-workspace and dependency-env fingerprints, runner-process compatibility key, optional model-view fingerprint, runtime profile identity, memory class, memory estimate confidence/source, observed idle/load/execution RAM/VRAM peaks, recent memory error counters, PID, current job, last-used timestamp, workflow lease IDs, and closed-view cooldown expiry.
- `RunnerStatus` includes the Phase 5f lifecycle states needed for switching and memory policy: `missing_runtime`, `preparing`, `idle`, `running`, `queued`, `queued_pending_switch`, `queued_pending_memory`, `idle_warm`, `switching`, `evicting_runner`, `waiting_for_memory_release`, `loading_model`, `retrying_after_memory_cleanup`, `failed`, `blocked_by_memory`, `memory_cleanup_failed`, `evicted_for_memory`, `evicted_after_cooldown`, and `co_resident`.
- `RunnerSupervisor.runner_selection_for(...)` returns a structured decision to reuse a compatible resident runner, switch from an idle incompatible GPU-heavy runner, queue behind a busy incompatible GPU-heavy runner, or start a co-resident CPU/GPU-light runner. Until the full Memory Governor can prove margin, `unknown` and `gpu_medium` are treated conservatively as GPU-heavy by the fallback policy.
- Workflow-open leases keep compatible runners `idle_warm` while views are open, and closing the final lease starts the default 90-second closed-view cooldown.
- Backend endpoints now expose workflow view leases through `POST /api/workflows/{workflow_id}/runner/leases` and `DELETE /api/workflows/{workflow_id}/runner/leases/{lease_id}`. These endpoints report lease state without exposing runner selection to the frontend.
- Isolated runner launch specs propagate compatibility fingerprints, runtime profile IDs, memory class, and process PID into the registered runner descriptor, so process supervision and engine routing share one descriptor shape.
- Bound workflows now reuse `ready`, `idle`, or `idle_warm` runners through the backend supervisor. Job progress, cancellation, and result lookup still route through `job_id -> runner_id`.
- Runner processes now launch in a dedicated process group/session on macOS/Linux and a new process group on Windows. Runner stop uses a bounded process-tree termination path, with fallback direct parent termination for injected/custom process factories.
- Workflow runner start now applies `RunnerSupervisor.runner_selection_for(...)`: compatible resident runners are reused, busy incompatible GPU-heavy runners return `queued_pending_switch`, and idle incompatible isolated GPU-heavy runners are stopped before the requested runner starts.
- Memory Governor MG1 schema records now exist for machine memory snapshots, runner memory snapshots, workflow memory estimates, local memory evidence summaries, decision actions, risk levels, retry flags, user messages, developer details, and diagnostic serialization.
- The Memory Governor record layer treats `unknown` and `gpu_medium` conservatively as GPU-heavy until later policy stages can prove safe co-residence. It also ranks local observations above creator-side `.noofy` observations for future estimate selection.
- Memory Governor MG2 observer interfaces now expose structured CUDA, RAM-only, and unavailable snapshots. The CUDA observer uses `nvidia-smi` when present, tolerates unavailable or partial data, and computes a simple memory-pressure level.
- Memory Governor MG3 now has a first-pass estimate builder and an app-local learning store. The estimator prefers repeated local evidence, lowers confidence after memory failures or changed settings, falls back through creator observations and declared requirements, then uses low-confidence model/resolution heuristics before returning `unknown`. The learning store records local observations in app data only and summarizes successes, memory failures, peaks, evictions, and retries.
- Memory Governor MG4 now has a deterministic admission policy for co-residence, safety margins, memory-pressure handling, queue-vs-evict decisions, and idle-runner eviction ordering. Runner-start integration is active when a Memory Governor observer is configured; the startup payload includes the structured memory decision for UI explanation.
- Memory Governor MG5 now has in-memory queued runner-start records for `queued_pending_switch` and `queued_pending_memory`, cancellation, and a backend handoff method that can start the next queued workflow after the blocking runner releases. The API exposes queued runner-start cancellation through `DELETE /api/workflows/runner/queue/{queue_id}`.
- Memory Governor MG6 now has bounded memory-release polling after idle-runner eviction, memory-release timeout handling, likely memory-error detection, automatic one-shot retry execution for submitted workflow jobs, workflow-run admission before submission, queued workflow-run handoff, and local observation recording from completed/failed job results when a learning store is configured.
- Memory Governor MG7 now exposes a compact `memory_status` object for UI copy alongside the detailed `memory_decision` developer payload. Aggregate backend counters are available through `GET /api/memory-governor/metrics` for admission, queueing, eviction, retry, blocked-by-memory, and learned observation outcomes.
- The workflow run page consumes `memory_status` without calling ComfyUI directly. It shows waiting, blocked-memory, and retry-ready messages while avoiding progress polling for memory queue IDs.
- Focused tests cover reuse, queue-pending-switch, idle incompatible GPU-heavy eviction decisions, CPU-only co-residency decisions, `gpu_medium` conservative fallback, unknown-as-GPU-heavy behavior, Memory Governor descriptor fields, Memory Governor MG1 schemas and diagnostic records, MG2 observer normal/unavailable/partial snapshots, MG3 estimate precedence, local-learning store, and confidence behavior, MG4 co-residence/eviction policy cases, Memory Governor runner-start evict/co-reside/block outcomes, queued switch/memory handoff, queued workflow-run handoff, queued-work cancellation, no active-job auto-kill, workflow-run blocked-by-memory, memory-status payloads, MG6 release success/timeout, automatic retry-after-cleanup execution, retry block after one attempt, memory-error learning, successful-run learning, core-runner non-eviction, workflow lease cooldowns, job start/finish markers, launch metadata propagation, process-tree containment flags, process-tree termination hooks, install-service launch spec metadata, API memory metrics, frontend memory waiting/blocked states, and runner-start reuse/queue/switch behavior.

Phase 5f backend completion:

- The Memory Governor contract, supervisor integration, queueing, eviction, retry, local learning, user-facing API state, diagnostics counters, and initial frontend memory-state consumption are implemented.
- Remaining work belongs to real-hardware validation polish and later product refinement, not the Phase 5f foundation. The next runtime implementation phase can start from this contract.

### Phase 5g: Transactional Install Promotion, Rollback, Quarantine, And Startup Sweep

Goal: make preparation atomic and recoverable across failures, restarts, crashes, and concurrent installs.

Tasks:

- Create every preparation under `runtime-store/transactions/install-<id>/`.
- Write staged dependency envs, staged runner workspaces, staged model views, smoke logs, and candidate manifests under the transaction first.
- Use per-fingerprint locks so concurrent installs do not race to promote the same dependency env or runner workspace.
- Re-check for an already-ready artifact after acquiring the lock; reuse it when manifests match.
- Promote dependency envs and runner workspaces only after required smoke tests pass.
- Promote by atomic move or atomic manifest registration. Do not partially mark artifacts ready.
- Update `install-state.json` last, after ready artifacts exist and have been verified.
- Do not mutate `capsule.lock.json` during local install progress.
- On failure, mark install state failed or unsupported with a beginner-friendly reason and developer diagnostics.
- Quarantine failed transactions and staged artifacts for diagnostics for a bounded retention window.
- Add startup sweep on backend boot to:
  - find stale install transactions
  - identify unpromoted staged envs/workspaces/model views
  - terminate orphaned runner processes owned by Noofy
  - remove stale PID files
  - remove orphan materialized links whose target blobs are gone
  - preserve recent quarantined failures until their retention window expires
- Ensure product shutdown terminates the full backend/runner process tree.

Acceptance criteria:

- Killing the backend during dependency install, custom-node materialization, model-view materialization, smoke test, and promotion leaves no artifact falsely marked ready.
- Startup sweep is idempotent.
- Concurrent preparation of two workflows with the same dependency lock reuses or promotes exactly one dependency environment.
- Failed preparation does not mutate trusted core runtime or any ready artifact.
- Install state is updated atomically and remains readable after interrupted writes.

Current implementation notes:

- Install preparation now opens `runtime-store/transactions/install-<id>/` transactions with typed transaction metadata, staged dependency-env, staged runner-workspace, staged model-view, staged model-blob download, and smoke-log directories.
- Dependency environments and runner workspaces are written under the install transaction first. They are promoted only after required smoke stages pass, with per-fingerprint lock files and a ready-artifact re-check before promotion.
- Model downloads and model views can be staged under the install transaction, used by smoke through the staged runner workspace, and then promoted to canonical blob/view paths before install state records final model references.
- Failed preparation and smoke failure quarantine the install transaction and staged artifacts with a bounded retention marker. Ready dependency-env and runner-workspace manifests are not written on failure.
- Backend startup runs an idempotent install-transaction sweep that quarantines stale install transactions, removes stale temp and lock files, removes legacy unscoped transaction directories, expires old quarantines, removes orphan materialized model links whose recorded blobs are gone, and cleans stale managed ComfyUI PID files.
- Isolated workflow runner processes now write backend-owned runner PID files while running. Backend startup removes stale runner PID files and attempts to terminate orphan workflow-runner processes from a previous backend crash.
- Smoke reports are persisted under the install transaction's `smoke-logs/` directory before transaction promotion or quarantine, giving failed smoke attempts bounded diagnostic artifacts without writing ready manifests.
- Install state writes remain atomic through temp-file replacement and are updated with final ready artifact paths only after promotion.
- Stale interrupted `install-state/*.json.tmp` writes are ignored by normal reads and removed during backend startup cleanup.

Phase 5g backend completion:

- The transactional preparation path, promotion order, rollback/quarantine behavior, startup sweep, stale runner PID cleanup, install-state temp cleanup, importer fixture relocation, and focused regression coverage are implemented.
- Remaining validation is operational crash testing on long-running real installs and real-hardware smoke runs; no known backend implementation task is left for Phase 5g.

### Phase 5h: Reference Tracking And Garbage Collection

Goal: prevent runtime-store growth without deleting assets still needed by installed workflows or active runners.

Tasks:

- Implement a derived reference index from installed workflow package records and `install-state.json`.
- Do not maintain separate reference-count files in v1.
- Track metadata for dependency envs, runner workspaces, custom-node source cache entries, wheel cache entries, model blobs, materialized model views, transactions, and package archives:
  - created timestamp
  - last used timestamp
  - referenced workflows
  - size bytes
  - status
  - trust level
- Define GC roots:
  - installed ready workflows
  - workflows in `prepared_needs_input_setup`
  - open workflow leases
  - active runners
  - idle-warm runners retained by an open workflow lease or closed-view cooldown
  - pinned runtime profile artifacts
  - Noofy Verified bundled assets
  - protected user-local models
- Never delete active or idle-warm runner artifacts.
- Never delete user-local source files.
- Never silently delete model blobs referenced by installed workflows.
- Delete failed transactions and quarantined staging directories only after the retention window.
- Default retention windows:
  - failed transactions and quarantined staging: 7 days
  - unreferenced dependency envs and runner workspaces: 14 days
  - orphan materialized model views: 7 days
- Add configurable LRU caps:
  - wheel cache default: 5 GB
  - custom-node source cache default: 2 GB
  - downloaded package archive cache default: 2 GB
- Require user confirmation before deleting Noofy-owned model blobs larger than 1 GB during manual cleanup.
- Expose storage diagnostics for developer details and future UI.

Acceptance criteria:

- Derived reference index correctly keeps artifacts referenced by multiple workflows.
- Removing one workflow does not delete shared artifacts still referenced by another workflow.
- GC skips active and idle-warm runners.
- GC never deletes `user_local` originals.
- Quarantine retention, LRU cap, orphan materialized view, and large-model confirm behavior are tested.

Current implementation notes:

- `RuntimeStorageGarbageCollector` now builds a derived reference index from `install-state.json` records, package/capsule records, and live runner descriptors. It does not write or maintain reference-count files.
- The index records dependency envs, runner workspaces, model blobs, materialized model views, install transactions, wheel-cache entries, custom-node source-cache entries, and package archives with timestamps, size, status, referenced workflows, trust where derivable, and developer diagnostics.
- GC roots include `ready` and `prepared_needs_input_setup` install states, active/queued/loading runners, idle-warm runners, open workflow leases, closed-view cooldown runners, configured pinned artifacts, and protected user-local model sources.
- Cleanup deletes unreferenced dependency envs and runner workspaces after the 14-day retention window, orphan materialized model views after 7 days, and expired quarantined transactions after their `retain_until` timestamp.
- Configurable LRU caps are implemented for wheel cache, custom-node source cache, and imported package archive cache. Referenced cache entries are kept when applying caps.
- Noofy-owned unreferenced model blobs can be deleted during manual cleanup, but blobs larger than the configured threshold defaulting to 1 GB require explicit confirmation. `user_local` source files are surfaced as protected diagnostics and are never deleted.
- `RuntimeStorageReferenceIndex.to_diagnostics()` exposes structured storage diagnostics for future API/UI work.
- Focused tests cover shared references, one-workflow removal with shared artifacts, active/idle-warm runner protection, user-local source protection, quarantine retention, LRU cap behavior, orphan model-view cleanup, large-model confirmation, and cache/archive metadata indexing.

Phase 5h backend completion:

- Derived reference indexing, GC policy, retention windows, configurable cache caps, protected-root behavior, diagnostics, and regression tests are implemented. Remaining work belongs to Phase 5i API exposure and future UI controls for manual cleanup.

### Phase 5i: Diagnostics, API States, And Frontend-Readable Status

Goal: make preparation, runtime switching, and failure modes understandable to the UI and useful to developers without exposing raw technical noise by default.

Tasks:

- Add backend API endpoints or extend existing endpoints so the frontend can:
  - start workflow preparation
  - inspect install/preparation state
  - inspect required action states such as missing model or input setup
  - inspect runner lifecycle state
  - report workflow view open/close leases for warm runner retention
  - cancel preparation when cancellation is safe
  - cancel the currently running job
- Ensure the frontend continues to call only the Noofy backend API.
- Add structured diagnostic events for:
  - runtime profile resolution success/failure
  - dependency lock resolution success/failure
  - dependency env reuse/build/failure
  - custom-node source classification/materialization/failure
  - model resolution/materialization/failure
  - smoke test start/pass/failure by stage
  - install promotion/rollback/quarantine
  - runner queue/switch/start/stop/idle-warm/eviction
  - Memory Governor estimates, co-residence admits/denies, memory pressure, memory cleanup, retry, and blocked-by-memory cases
  - garbage collection decisions
- Use beginner-friendly status summaries by default. Keep `pip`, `venv`, `site-packages`, stack traces, raw Python exceptions, and raw node import errors behind developer details.
- Ensure runner processes do not receive the frontend/backend API token.
- Redact secrets, local API tokens, signed URLs, and user-private paths from default diagnostics.
- Surface observed hardware metrics as compatibility guidance, not guaranteed minimum requirements.

Acceptance criteria:

- API payloads distinguish `ready`, `prepared_needs_input_setup`, `cannot_prepare_automatically`, `blocked_by_policy`, `unsupported_runtime_profile`, and `failed`.
- Diagnostic events are structured and include correlation IDs for workflow ID, install transaction ID, runner ID, and job ID where relevant.
- Technical failure details are available behind developer details.
- User-facing status text avoids Python setup terminology by default.
- Tests verify that runner environment variables do not include the frontend/backend API token.

Current implementation notes:

- `GET /api/workflows/{workflow_id}/status` now returns a consolidated frontend-readable payload with workflow summary, install/preparation state, required actions, runner lifecycle state, cancel capabilities, and compatibility guidance that labels observed hardware as advisory.
- `GET /api/workflows/{workflow_id}/install-state/developer-details` exposes raw preparation details such as `last_error`, smoke-stage report, and runtime artifact paths behind a developer details endpoint. Default install/status payloads keep beginner-friendly status fields and only report whether developer details are available.
- Existing preparation, runner start/stop, runner lease, queued runner cancellation, job cancellation, install-state, and validation endpoints cover the main frontend workflow-control surface. `DELETE /api/workflows/{workflow_id}/prepare` reports that no active cancellation is available for the current synchronous preparation path.
- `GET /api/diagnostics` exposes structured diagnostic events with extracted correlation IDs for workflow, job, runner, install transaction, queue, and memory decisions. Developer details are hidden by default and available only with `developer_details=true`.
- Default diagnostics redact token/secret/authorization/signed-url fields, sensitive signed URL strings, and home-directory local paths. Technical setup details remain in developer details rather than beginner-facing status payloads.
- `GET /api/storage/diagnostics` exposes the Phase 5h storage reference index for developer details and future UI cleanup views.
- Runner process launch now always passes an explicit environment with `NOOFY_API_TOKEN` removed, even when the runner launch spec does not define custom environment variables.
- Focused tests cover workflow status payloads, preparation cancellation reporting, status distinction for the required install states, redacted diagnostics, storage diagnostics, diagnostic correlation IDs/developer details, and runner API-token environment stripping.

Phase 5i backend completion:

- Frontend-readable workflow status, required action state, runner lifecycle state, cancellation surfaces, redacted diagnostics, storage diagnostics, developer-detail payloads, compatibility guidance, and runner token isolation are implemented. Remaining work belongs to Phase 5j integration/acceptance coverage and any later frontend presentation polish.

### Phase 5j: Integration Tests And Phase Acceptance Gate

Goal: prove the locked/bundled community workflow path works end to end and fails safely.

Tasks:

- Use `exported-workflow-for-testing.noofy` as the only real `.noofy` archive fixture for now.
- Do not add new real `.noofy` archive fixtures until this fixture is fully covered or product needs require another archive.
- For failure paths, use unit fixtures, temporary normalized records, or controlled mutations derived from `exported-workflow-for-testing.noofy`, rather than maintaining additional `.noofy` files.
- Cover:
  - import and inspection of `exported-workflow-for-testing.noofy`
  - bundled custom node source discovery and materialization planning
  - unresolved `LoadImage` input status
  - dependency lock success and policy failure with derived records
  - model hash match, filename-size unverified match, filename-only non-match, and hash mismatch with derived records
  - model-view collision requiring separate views with derived records
  - runner smoke success and failure through fake/lightweight runner adapters
  - runner switching between compatible and incompatible workflows through fake/lightweight runner adapters
  - interrupted transaction and startup sweep
  - GC with shared references
- Add unit tests for schemas, fingerprint canonicalization, policy decisions, path validation, dependency lock parsing, manifest parsing, and install-state parsing.
- Add integration tests using fake or lightweight runner adapters where real ComfyUI startup is too expensive for normal CI.
- Keep real ComfyUI smoke paths available for local/optional CI validation, with `make phase5e-real-smoke` as the productized full staged validation command and pytest real-smoke hooks skipped by default in ordinary CI.
- Add Linux, Windows, and macOS filesystem behavior tests or documented manual test scripts for hardlink, symlink, copy fallback, long paths, and case-insensitive collisions.
- Keep `make test` as the documented root test command and ensure backend/frontend tests still run from the expected directories.

Phase 5 locked/bundled acceptance criteria:

- Noofy can prepare the supported portions of `exported-workflow-for-testing.noofy` into isolated staged/runtime artifacts without importing custom node Python in the trusted backend.
- Dependency envs, runner workspaces, model views, and install state are reusable where fingerprints and manifests match.
- Failed dependency install, custom-node materialization, model materialization, runner start, or smoke execution does not mutate trusted core runtime or ready artifacts.
- Workflows become `ready` only after required smoke stages pass. Test archives must not be marked `ready` without a real smoke run or an explicitly controlled fake-runner test.
- Workflows with unresolved runtime inputs remain not-ready with beginner-friendly required-action status.
- Runner switching honors queueing, normal cancellation, workflow-open warm retention, closed-view cooldown, and Memory Governor co-residence/eviction policy with one-GPU-heavy fallback when confidence is low.
- Reference tracking and GC do not delete assets still referenced by installed workflows or active runners.
- Backend and frontend tests pass through the documented project test commands.

### Phase 5k: Community Registry And Non-Bundled Source Resolution

Goal: expand beyond bundled custom node source only after locked/bundled preparation is stable.

This sub-phase is intentionally last. Do not begin it until Phases 5a through 5j are implemented and tested.

Tasks:

- Add Noofy node registry schema.
- Resolve non-bundled custom node sources through:
  - explicit Noofy metadata
  - registry metadata
  - Noofy-maintained node-type mappings
  - allowed community source-resolution mechanisms
- Require pinned source refs and source content hashes before materialization.
- Download/cache custom node source at resolved refs.
- Generate candidate locks for Quarantined Community workflows only when:
  - the user has explicitly allowed unverified community workflow preparation
  - all custom-node sources are resolved
  - dependencies can be locked under policy
  - all downloads happen through Noofy's approved resolver/materializer path
- Apply stricter policy to Noofy Verified and Registry Locked workflows.
- Mark packages unsupported when source resolution, dependency locking, platform compatibility, or trust policy cannot be satisfied.
- Add diagnostics that explain registry and resolution failures behind developer details.

Acceptance criteria:

- Non-bundled custom-node source resolution never mutates the trusted core runtime.
- Unpinned repositories, unknown sources, blocked install behavior, missing hashes, and policy-blocked dependencies fail before runner execution.
- Quarantined Community workflows require explicit opt-in before automatic preparation.
- Registry resolution failures produce beginner-friendly unsupported states and structured developer diagnostics.

## Phase 6: Trust, Signing, Marketplace Readiness

Goal: prepare the runtime model for community distribution without weakening isolation.

This phase does not implement the full in-app marketplace. It adds the trust, signing, source-policy, and UI foundations required before marketplace workflows can be distributed safely.

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
- Evaluate OS-level sandboxing feasibility for Linux, Windows, and macOS.

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
