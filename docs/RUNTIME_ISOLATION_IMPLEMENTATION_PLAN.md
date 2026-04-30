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

## Phase 5: Community Custom Node Resolver

Goal: support community custom-node workflows when Noofy can resolve them into isolated runtime capsules without mutating the trusted core runtime or existing workflows.

Tasks:

- Add pinned core-node manifest for the supported ComfyUI version.
- Add Noofy node registry schema.
- Resolve non-core node types through:
  - explicit Noofy metadata
  - registry metadata
  - Noofy-maintained node-type mappings
  - allowed community source-resolution mechanisms
- Support the common custom node pattern where repositories declare dependencies in `requirements.txt`.
- Apply stricter policy to Noofy Verified and Registry Locked workflows.
- Allow Quarantined Community workflows only when the user explicitly opts in and Noofy can isolate the install.
- Download/cache custom node source at resolved refs.
- Install normal Python dependencies only into isolated dependency envs.
- Materialize required custom nodes into staged runner workspaces only.
- Run custom-node import checks only in staged runner processes.
- Reject unresolvable or unsafe packages with user-friendly unsupported status.
- Add diagnostics that keep technical errors behind developer details.

Acceptance criteria:

- Unknown custom nodes do not mutate the trusted core runtime or ready workflow environments.
- Noofy can automatically install custom nodes only when their source can be resolved.
- Normal Python dependencies such as `requirements.txt` are installed only inside isolated dependency envs.
- Arbitrary install scripts are not executed for one-click installs unless a future explicit policy allows them.
- The trusted backend does not import custom node modules.
- Failed custom-node install does not mutate core runtime, ready envs, or ready runner workspaces.
- Community custom-node workflows can become ready only after staged runner smoke tests pass.

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
- Add storage management UI for models, envs, runner workspaces, and caches.
- Add garbage-collection implementation:
  - reference tracking
  - last-used tracking
  - failed transaction retention
  - cache size limits
- Add marketplace/package source policy.
- Evaluate OS-level sandboxing feasibility for macOS and Windows.

Acceptance criteria:

- Trust level is visible in workflow install and detail surfaces.
- Unsupported workflows fail gracefully without technical setup language by default.
- Ready workflows protect referenced model blobs and runtime artifacts from automatic GC.
- Unreferenced dependency envs and runner workspaces can be removed safely.
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
