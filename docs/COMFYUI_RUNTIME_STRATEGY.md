# ComfyUI Runtime Strategy

Date: 2026-05-01

Status: Reviewed / Implementation-plan source

This document refines the ComfyUI runtime strategy that Phase 5 of [RUNTIME_ISOLATION_IMPLEMENTATION_PLAN.md](RUNTIME_ISOLATION_IMPLEMENTATION_PLAN.md) will implement. It is consistent with the accepted [RUNTIME_ISOLATION_ARCHITECTURE.md](RUNTIME_ISOLATION_ARCHITECTURE.md) and tightens its underspecified parts.

This document is the strategy source for Phase 5 of [RUNTIME_ISOLATION_IMPLEMENTATION_PLAN.md](RUNTIME_ISOLATION_IMPLEMENTATION_PLAN.md). When this document and the implementation plan disagree, the implementation plan should be updated or the disagreement should be called out explicitly rather than resolved by inference.

## Context

Noofy is a local AI workflow app for Linux, Windows, and macOS. ComfyUI is the first execution engine, behind an app-owned `EngineAdapter` boundary. Community workflows are a first-class product direction. Some community workflows include custom nodes and Python dependencies; some workflows depend on ComfyUI behavior that may not match the latest upstream version.

The product needs an explicit runtime strategy that answers:

- How does Noofy decide when two workflows can share a runtime?
- How does Noofy choose which ComfyUI/runtime to start for a given workflow?
- How does Noofy switch runners without unloading large models unnecessarily?
- How does Noofy avoid disk, memory, and maintenance explosions when a user installs many community workflows?
- How does Noofy stay honest about what isolation does and does not guarantee?

The accepted runtime isolation architecture already commits to layered fingerprints, shared content-addressed stores, transactional installs, immutable capsule locks, mutable install state, a `RunnerSupervisor`, and a single trusted core runtime. This document does not change those decisions. It tightens the fingerprint surface, the runtime profile concept, the runner switching behavior, and the smoke-test, security, and garbage-collection rules so that Phase 5 can be implemented without underspecified edge cases.

## Decision Summary

Firm decisions:

- Noofy uses reusable compatibility-group runtimes. A workflow may require a specific runtime profile, but Noofy resolves that profile into reusable immutable runtime artifacts and shares them across compatible workflows.
- Compatibility is identified by layered fingerprints over fully resolved runtime facts, not over raw declarations such as `requirements.txt`.
- Noofy supports one pinned runtime profile family in v1, with explicit platform/backend variants as needed for Linux, Windows, macOS, CPU, MPS, CUDA, or other supported backends. The schema must support multiple profile families and variants so older or alternative profiles can be added later without migration.
- Noofy targets the most recent stable ComfyUI release when a Noofy runtime profile is generated. It does not use a floating "latest ComfyUI" at runtime; the product contract is a named, pinned runtime profile with exact source and dependency hashes.
- Noofy does not install a full isolated runtime per workflow. Workflows that share a fingerprint share the runtime artifact.
- Noofy v1 includes a Memory Governor. The safe fallback is one GPU-heavy runner resident, but multiple warm runners are allowed when memory class, local learned observations, machine profile, and safety margins make co-residence low risk.
- Noofy does not implement an uncontrolled multi-runner warm pool. All co-resident runners are admitted, monitored, evicted, retried, and explained through the Memory Governor policy in [MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md](MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md).
- Noofy v1 learns memory behavior locally over time. Creator `.noofy` hardware observations are first-run hints; repeated local successes and failures on the user's machine become the stronger evidence for warm retention, co-residence, eviction, retry, and user-facing explanations.
- Fingerprints are compatibility and reuse boundaries. They do not guarantee identical output. Practical readiness is established by smoke tests.
- Dependency isolation is not a security sandbox. Noofy protects its own architecture from dependency conflicts and broken installs. It does not claim arbitrary community Python code is safe.
- Product builds use Noofy-managed Python. Product builds must not depend on system Python, Homebrew Python, user PATH, Conda, or developer virtualenvs.

V1 scope is documented under [Phase 5 Scope Recommendation](#phase-5-scope-recommendation). Deferred work and unresolved questions are listed at the end of this document.

## Why Not One Global Runtime

A single mutable global ComfyUI/Python environment fails for community workflows:

- One workflow's install can upgrade or downgrade packages relied on by another workflow.
- A broken custom node or failed install can break the trusted core runtime.
- Imported Python modules and native extensions cannot be reliably unloaded or rolled back.
- Many custom nodes monkey-patch ComfyUI registries on import; combining many at once produces unpredictable behavior.
- Unverified community installs would be writing into the same environment Noofy uses for its own verified flows.
- Non-technical users cannot be expected to repair Python, pip, virtualenvs, or custom node folders.

A global "latest ComfyUI" also makes Noofy fragile to upstream changes, custom node compatibility drift, and Torch/CUDA/Metal stack churn.

## Why Not One Runtime Per Workflow

A full isolated ComfyUI runtime per workflow gives the strongest per-workflow compatibility but fails on every other axis:

- Disk usage explodes. Each runtime can be many gigabytes once Python, Torch, ComfyUI source, custom nodes, and caches are duplicated.
- Maintenance complexity scales linearly with workflow count.
- Cold-start latency becomes the common case because runtime reuse is impossible.
- Each runtime is an independent surface to keep updated, signed, and patched.
- The user has to understand or accept many parallel environments per workflow they install.

The accepted architecture rejects this approach. This document keeps that rejection.

## Runtime Profile

Compatibility must be defined over a named runtime profile, not just a ComfyUI version string.

A runtime profile is a pinned bundle that fully describes the runtime contract Noofy supports for a class of workflows. A profile may contain multiple platform/backend variants, because the exact Torch wheel, native dependency set, and launch constraints differ across Linux, Windows, macOS, CPU, MPS, CUDA, DirectML, and future backends. The profile and every selected variant are signed/owned by Noofy and treated as immutable artifacts in the trusted control plane.

### ComfyUI Source Roles

`ComfyUI-official-repo/` is a local development and reference copy. Agents and developers may inspect it, compare behavior against it, or use it for development-mode experiments. It is not automatically the product runtime source.

Product runtime profiles must be generated from a clean, reproducible ComfyUI source artifact under Noofy's runtime store, such as:

- an upstream stable release tag checked out into `runtime-store/core-engines/comfyui-core-<version>-<source-hash>/`
- a verified source archive unpacked into the same content-addressed layout
- a Noofy-vendored source snapshot with an explicit source manifest

Local ignored artifacts inside `ComfyUI-official-repo/`, including `models/`, `custom_nodes/`, `input/`, `output/`, test paths, and developer-only files, must never affect product runtime identity. If a development profile intentionally uses `ComfyUI-official-repo/`, it must be marked development-only and record that it came from the reference copy.

V1 product profiles should prefer the latest stable ComfyUI release available at profile-generation time, using ComfyUI README guidance for supported Python, PyTorch, CUDA, and backend options. The profile must record exact source identity. Product profile generation must reject dirty or non-reproducible source artifacts.

Required runtime profile fields:

- `runtime_profile_id` (stable name such as `noofy-comfyui-v1-default`)
- `runtime_profile_manifest_hash` (hash of the signed profile manifest)
- `runtime_profile_variant_id` (stable selected variant name such as `darwin-arm64-mps`)
- ComfyUI core version
- ComfyUI core source hash
- ComfyUI source origin (`upstream_git_tag`, `upstream_source_archive`, `noofy_vendored_snapshot`, or explicit `development_reference_copy`)
- source cleanliness and reproducibility status (`clean_reproducible` for product profiles; `development_only` for reference-copy profiles)
- ComfyUI frontend package name and version
- Python build ID (Noofy-managed CPython distribution)
- Torch version and wheel build tag (for example `torch==2.4.0+cu121`)
- GPU backend profile (`cuda`, `mps`, `cpu`, `directml`, etc.)
- Core dependency lock hash (resolved transitive dependencies for ComfyUI core, with hashes)
- Default launch configuration (allowlisted launch options, see [Custom Node Workspace Policy](#custom-node-workspace-policy))
- Supported OS/architecture matrix
- Install policy version
- Profile signature or signed manifest reference

V1 ships one supported profile family. The runtime profile catalog schema must support more than one profile family and more than one variant per family from day one so that legacy or alternative profiles can be added later without changing fingerprint shape.

A workflow's `capsule.lock.json` must reference a `runtime_profile_id`. Resolved install state must record the selected `runtime_profile_variant_id` and `runtime_profile_manifest_hash`. Workflows that target a profile not present in the catalog, or a variant not supported on the current machine, are surfaced as not preparable on this build with a beginner-friendly explanation. They must not silently fall back to a "close enough" profile.

## Layered Fingerprints

Fingerprints are reuse and compatibility boundaries. They are not a guarantee of identical output. They identify when an existing prepared artifact may be reused for a new workflow.

All fingerprints are SHA-256 over a canonical, sorted, byte-stable serialization of their inputs. The serialization rules, fingerprint schema version, and input enumeration are part of the schema; changing inputs is a schema-version bump.

### Dependency Environment Fingerprint

Identifies a Python environment that can be reused across runner workspaces.

Inputs:

- runtime profile ID
- runtime profile manifest hash
- selected runtime profile variant ID
- OS, architecture, libc/CRT identity
- Python build ID
- Torch wheel build tag
- GPU backend profile
- resolved transitive dependency lock hash (full lock with hashes; not raw `requirements.txt`)
- native dependency constraints
- install policy version

The dependency environment fingerprint must not include workflow graph, dashboard metadata, model files, or enabled custom node source when that source is mounted into the runner workspace rather than installed into site-packages.

### Runner Workspace Fingerprint

Identifies a ComfyUI runner workspace that can be reused across compatible workflows.

Inputs:

- dependency environment fingerprint
- runtime profile ID
- runtime profile manifest hash
- selected runtime profile variant ID
- ComfyUI core source hash
- ComfyUI frontend version
- enabled custom node workspace manifest hash (see [Custom Node Workspace Policy](#custom-node-workspace-policy))
- launch configuration hash (allowlisted surface only)
- model-view compatibility hash when required by the current ComfyUI profile (logical folder layout, aliasing strategy, and allowed extra-path mode; not local absolute paths, materialization strategy, user source paths, or blob contents)

The runner workspace fingerprint must be deterministic across machines for the same logical inputs. Machine-local facts such as app-data paths, hardlink versus copy behavior, symlink support, and user-local source file paths belong in install state and diagnostics, not in fingerprints.

### Capsule Fingerprint

Identifies a fully resolved workflow capsule.

Inputs:

- workflow package hash
- ComfyUI graph hash
- dashboard schema hash
- model requirement hashes (declared identities, not local resolution)
- trust/signature metadata
- runner workspace fingerprint

Two workflows with the same capsule fingerprint are treated as the same install unit. Two workflows with the same runner workspace fingerprint can share a runner. Two workflows with the same dependency environment fingerprint can share a Python environment even if their runner workspaces differ.

## Dependency Lock Policy

Dependency fingerprints must be computed over a fully resolved transitive lock, not over raw `requirements.txt`.

V1 uses `uv` as the primary resolver, wheel cache manager, and dependency environment installer. The persisted Noofy dependency lock is a Noofy-owned JSON schema that records the resolved wheel facts and `uv` resolver metadata; Noofy does not expose raw `uv.lock` as the long-term app contract. `pip` may remain a compatibility fallback for existing managed-core bootstrap paths, but community workflow installs must use a path that can enforce Noofy's wheels-only, hash-required policy.

The lock format must include, per dependency:

- package name
- exact version
- wheel filename
- hash (sha256)
- source index URL
- platform tags / environment markers
- transitive dependency closure
- resolver version
- install policy version

Lock generation must be deterministic enough to be reproducible from the stored lock, not from a later re-run against a moving package index. The resolver version, index URLs, index snapshot identity when available, platform context, and all wheel hashes must be recorded. Once a lock is accepted, future reinstall and fingerprint calculation uses the stored lock. If Noofy cannot produce or verify a complete lock under policy, the workflow is unsupported rather than installed from raw declarations.

Default install policy for unverified or community workflows:

- wheels only (`--only-binary=:all:` or equivalent)
- hash required for every dependency
- no sdists
- no native source builds
- no arbitrary install scripts (`install.py`, post-install hooks, custom setup commands)
- no downloads outside Noofy's approved resolver/materializer path
- no execution of custom-node `setup.py`, editable installs, or source-tree build hooks

`setup.py` and `pyproject.toml` inside a custom node source tree may be inspected as data only when a static parser can extract normal dependency declarations without executing project code. If dependency extraction requires executing build hooks or arbitrary Python, the workflow is unsupported under the default community policy.

Verified workflows may relax specific policies through an explicit, named policy override, but the default is the strict policy.

If the strict policy cannot be satisfied (no wheel available for the required platform, native build required, missing hash) the workflow must be marked unsupported with a beginner-friendly reason and structured developer diagnostics.

## Custom Node Workspace Policy

The runner workspace fingerprint depends on a deterministic custom node workspace manifest. Filesystem ordering must not be load-bearing.

The manifest must include, per enabled custom node:

- custom node package ID
- source ref (commit hash, version tag, or registry-pinned identity)
- source content hash
- materialized path inside the runner workspace
- import order index (Noofy-controlled, deterministic)
- optional metadata flags relevant to runtime behavior (for example: registers nodes globally, monkey-patches core symbols)

The manifest itself has a stable hash that contributes to the runner workspace fingerprint.

Custom node source is materialized as source directories in the runner workspace. It is not installed into `site-packages` as an editable package under the default community policy. Bundled archives must already have passed import-time path validation; materialization must still reject absolute paths, parent traversal, symlinks or junctions that escape the staged workspace, duplicate case-insensitive paths, and files that exceed policy limits.

Launch configuration must be allowlisted. Only the following launch surface affects the runner fingerprint:

- preview method
- VRAM mode
- attention backend
- precision policy
- enabled/disabled custom nodes set
- extra model paths mode
- environment variables explicitly controlled by Noofy

Unknown or unsupported launch options must be rejected at runner-start time. They must not silently become part of a runner.

The trusted backend must never import custom node Python modules. All custom node import checks happen inside the staged runner process, as already required by the accepted architecture.

## Model And Asset Handling

Model blobs stay outside the runtime fingerprint. Models are large data assets shared by content hash through the shared model store.

Model-view compatibility is part of the runner workspace fingerprint only when it affects runtime behavior for the selected profile. The fingerprint includes the logical model-view contract, not machine-local realization:

- model folder layout
- aliasing strategy and conflict policy
- relative `extra_model_paths.yaml` template or mode
- custom model folders required by specific custom nodes
- node-specific folder mappings
- materialized model view manifest schema version

The same model blob may be reused by multiple workflows. Noofy must materialize the blob under the name and path each workflow expects when needed, even if the same blob is materialized under different names for different workflows.

If two workflows require different blobs at the same ComfyUI folder/name, Noofy must not overwrite one with the other in a shared view. Phase 5 uses separate materialized model views for these collisions. Graph rewriting to app-assigned collision-free aliases is deferred until there is a concrete need and a tested `EngineAdapter` rewrite layer.

Model resolution rules follow the Phase 4.5 verification hierarchy:

- SHA-256 plus size: trusted reuse
- filename plus size: unverified local candidate; usable only when no exported hash identity exists
- filename only: never trusted

Resolved model references and verification level are recorded in `install-state.json`, not in `capsule.lock.json`.

### Model Materialization Fallback Ladder

The runner-visible model view is built by linking blobs from the shared model store. The selection ladder is:

1. Hardlink, when source and destination are on the same volume and the filesystem supports it.
2. Symlink, when hardlink is not possible. On Windows, symlink creation requires Developer Mode or admin and must be probed at install time.
3. Copy, as a last resort. Copy is required across volumes and on platforms or filesystems where neither hardlink nor symlink is reliably available.

The selected strategy must be:

- recorded in install state per model reference
- observable in diagnostics
- verified at runner start; the runner refuses to start if the materialized view does not present the expected files

A change in materialization strategy (for example: user moved data dir to another volume) is detected at runner start and triggers a re-materialization, not a silent failure.

Materialization must also handle platform edge cases: case-insensitive filename collisions, Windows path length limits, Windows junction/symlink permissions, antivirus or file-lock interference during large copies, cross-volume hardlink failures, and stale links whose target blob has been garbage-collected.

## Runner Switching, Warm Runners, And Memory Governor

`RunnerSupervisor` owns runner selection and lifecycle. The Memory Governor owns RAM/VRAM risk decisions. The frontend never controls runners directly; switching tabs does not stop or start anything.

V1 runner and memory policy:

- If the requested workflow shares the runner workspace fingerprint of the current runner, reuse the current runner.
- If a different runner is needed and the current runner is executing a job, do not kill it automatically. Queue the new job by default as `queued_pending_switch` or `queued_pending_memory`, depending on the blocker.
- The UI exposes a normal Cancel action for the currently running job. V1 does not implement a dedicated "cancel and switch" action.
- If a different runner is needed and existing runners are idle, the Memory Governor decides whether to start co-resident, evict first, wait for memory release, or block with a clear memory reason.
- The safe fallback is one resident GPU-heavy runner. Unknown runner memory class is treated as GPU-heavy and high risk until local observations prove otherwise.
- Multiple warm runners are allowed in v1 only through the Memory Governor. The decision must consider memory class, VRAM/RAM snapshots, safety margins, local run history, creator `.noofy` observations, recent memory errors, active/idle state, and runner compatibility fingerprints.
- The Memory Governor's confidence model is local-learning driven: repeated successful runs under similar settings can make a workflow more likely to stay warm or co-reside safely; memory failures make future decisions more conservative for that workflow, backend, machine profile, and similar settings.
- After a workflow finishes, its runner may remain `idle_warm` while at least one compatible workflow is currently open in Noofy and the Memory Governor allows retention. If the user leaves the computer and returns to the same still-open workflow, Noofy should reuse the loaded runner and models when memory policy allows it.
- When the last compatible workflow view closes, the runner may enter a short closed-view cooldown before eviction. The default closed-view cooldown is 90 seconds and is configurable.
- If memory pressure appears, or the next runner cannot allocate, evict idle-warm runners before reporting failure.
- When a runner is stopped for memory, the supervisor must wait and verify bounded RAM/VRAM release before starting the next runner. Process termination does not always free VRAM cleanly on every driver and OS combination.
- If a workflow fails due to likely memory pressure, Noofy may stop idle runners, wait for memory release, and retry once when the run is safe to retry. Repeated failures lower future confidence and should not loop.

Runner warm retention depends on workflow-open leases reported through the backend API. The frontend reports when workflow views open and close; the backend remains authoritative and may evict runners for memory pressure, process failure, shutdown, or explicit cancellation.

`RunnerSupervisor` exposes runner state through the backend API. The following states must be representable so the frontend can render understandable UX:

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

State transitions emit structured diagnostics so support, telemetry, and the UI can explain what happened.

The detailed v1 Memory Governor policy, including memory classes, signal reliability, local memory learning, co-residence matrix, safety margins, eviction order, recovery behavior, UI text, and implementation gates, lives in [MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md](MEMORY_GOVERNOR_IMPLEMENTATION_PLAN.md).

## Smoke Testing Policy

A staged dependency environment and runner workspace are promoted to ready only after smoke tests pass. Import-only checks are not sufficient.

Required smoke test stages, in order:

1. Dependency import check inside the staged dependency environment.
2. Custom node import check inside a staged runner process started from the staged environment and workspace.
3. ComfyUI runner start health check (process up, HTTP health endpoint reachable).
4. Minimal real workflow execution. Use a tiny graph or test fixture at minimal resolution and minimal step count when possible. The execution test must exercise real node execution, not just node registration.

Failure rules:

- Failed dependency or import check: the staged environment and workspace are quarantined, not promoted. The workflow install state becomes failed with a beginner-friendly message and developer diagnostics.
- Failed runner start or smoke execution: same as above. The trusted core runtime, ready dependency environments, and ready runner workspaces are not mutated.
- Quarantined staging directories are retained for diagnostics for a bounded retention window before garbage collection sweeps them.

A workflow may become ready only after all smoke test stages pass. A `smoke_test_status=passed` record in install state is required for the runner supervisor to launch the workflow runner.

## Security And Trust Boundaries

Dependency isolation is not a security sandbox.

Virtual environments and isolated runner processes prevent dependency conflicts and protect the trusted core runtime from mutation. They do not make arbitrary Python code safe. They do not prevent malicious code in a runner process from reading user files the runner can access, making network requests, consuming CPU/GPU, or exfiltrating local data.

Noofy's first security layer is supply-chain control. For unverified community workflows, Noofy must block by default:

- `install.py` and other arbitrary setup scripts
- sdists
- native source builds
- post-install hooks
- unpinned repositories
- unknown repositories not resolvable through Noofy or registry metadata
- downloads outside Noofy's approved resolver/materializer path
- runner access to the frontend/backend API token

Runner processes must receive only runner-scoped secrets if any. The frontend/backend API token described in [docs/ARCHITECTURE.md](ARCHITECTURE.md) must not be present in the runner process environment.

Noofy must be honest about what it does and does not protect:

- Noofy protects itself: one workflow cannot break another workflow or the trusted core runtime.
- Noofy does not guarantee that arbitrary community Python code is safe or trustworthy.
- Noofy does not currently provide OS-level sandboxing. macOS App Sandbox, hardened runtime constraints, Windows AppContainer, restricted child-process tokens, Linux namespaces/seccomp/cgroups, network restrictions, and per-runner filesystem allowlists are deferred and platform-specific.

Trust levels follow the four-level model defined in the runtime isolation architecture (Noofy Verified, Registry Locked, Quarantined Community, Unsupported). The default install policy in this document applies to Quarantined Community workflows. Verified levels may relax specific rules through explicit, named overrides.

## Disk, Cache, And Garbage Collection

Reference tracking and garbage collection must be explicit, not best-effort.

GC roots:

- installed ready workflows (capsule + install state)
- open workflow leases
- active runners
- idle-warm runners retained by an open workflow lease or closed-view cooldown
- pinned built-in runtime profile artifacts
- Noofy Verified bundled assets
- protected user-local models (`asset_ownership=user_local`)

Tracked metadata per dependency env, runner workspace, source checkout, wheel cache entry, model blob, and materialized model view:

- `created_at`
- `last_used_at`
- `referenced_by`
- `size_bytes`
- `status`
- `trust_level`

GC rules:

- Active and idle-warm runners and their environments are never deleted.
- Models referenced by an installed ready capsule are never silently deleted.
- User-local models are never deleted by Noofy under any policy.
- Dependency environments and runner workspaces no longer referenced by any installed workflow are eligible for deletion after a retention window.
- Failed transactions and quarantined staging directories are deleted after a retention window.
- Wheel cache and source cache use LRU caps with documented default sizes; caps are user-overridable.
- Large downloaded model blobs (`asset_ownership=noofy_downloaded`) above a configurable size threshold require user confirmation before deletion. Bulk reclamation surfaces a list with sizes.
- A startup sweep runs on backend boot to clean stale `runtime-store/transactions/install-*` directories, unpromoted staged environments and workspaces, and orphan symlinks whose target blobs are gone.

The reverse index from artifact fingerprint to referencing workflows must be authoritative. V1 uses a derived reference index computed from installed workflow records and `install-state.json` on startup and after install/remove operations. It does not maintain separate reference-count files, because split-brain reference counts are a common source of accidental deletion.

## Phase 5 Scope Recommendation

Phase 5 should be split. The split avoids landing the full registry/community-resolver surface alongside the foundational runtime preparation work.

### Phase 5a — Locked / Bundled Runtime Preparation

Scope:

- one pinned runtime profile family, declared in a runtime profile catalog with platform/backend variants
- runtime profile schema (supports multiple profile families and variants in the future)
- resolved dependency lock schema with hashes
- dependency environment fingerprint computed from the resolved lock
- deterministic custom node workspace manifest with import order
- custom node materialization from bundled `.noofy` archives only (no network resolution)
- shared model store integration with SHA-256 + size verification
- model-view materialization with hardlink → symlink → copy fallback ladder
- import smoke test (dependencies and custom nodes)
- minimal real graph execution smoke test when hardware and model requirements allow it; otherwise use fake/lightweight runner tests and keep real execution as an optional local gate
- transactional promote-on-success / quarantine-on-failure
- startup sweep for stale transactions and unpromoted staging directories
- baseline reference tracking and garbage collection
- `RunnerSupervisor` runner-switch policy with workflow-open warm retention, closed-view cooldown, and Memory Governor controlled co-resident warm runners
- Memory Governor schemas, estimates, local-learning observations, co-residence decisions, eviction, bounded memory-release checks, retry-after-cleanup behavior, user-facing memory states, and developer diagnostics
- expanded runner state surface in the API

Out of scope for 5a:

- registry lookup
- non-bundled custom node source resolution
- candidate lock generation from remote custom-node sources
- multi-runtime-profile UI surfaces
- uncontrolled multi-runner warm pool outside Memory Governor policy
- OS-level sandboxing

### Phase 5b — Community Runtime Resolution

Scope:

- Noofy node registry schema and lookup
- non-bundled custom node source resolution (explicit metadata, registry metadata, Noofy-maintained mappings, allowed community resolution mechanisms)
- candidate lock generation for Quarantined Community workflows on the user's machine, with deterministic resolver behavior
- broader trust policy enforcement at install time
- user-facing unsupported workflow explanations for unresolved or policy-blocked cases
- diagnostics for resolution failures behind developer details

Out of scope for 5b:

- multi-runtime-profile catalog expansion (still ships one profile family in v1)
- uncontrolled multi-runner warm pool outside Memory Governor policy
- OS-level sandboxing

## Deferred Work

The following are intentionally not part of v1. They are recorded so the schema and `RunnerSupervisor` design accommodate them later without rework.

- Multiple runtime profile families in the catalog (older ComfyUI versions, alternative behavior profiles, alternative Torch major versions). The schema supports this from day one; the catalog ships with one family.
- Uncontrolled multi-runner warm pool without Memory Governor admission, observation, eviction, and diagnostics.
- OS-level sandboxing per platform (macOS App Sandbox, Windows AppContainer, Linux namespaces/seccomp/cgroups, network restrictions, per-runner filesystem allowlists).
- Verified publishing and signing pipeline for Noofy Verified packages.
- Marketplace and registry hosting infrastructure.
- Native platform inference paths, including macOS Core ML/Metal/MLX and Linux CUDA-specific paths behind future `EngineAdapter` implementations.
- Allowed-with-explicit-policy native builds and sdists for Verified workflows.

## Open Questions

These questions should be resolved before or during implementation, but they do not block the strategy decision.

- Resolver pinning mechanism for community workflows. How is the index snapshot identity captured so that reinstalls on the same machine produce the same lock?
- Memory-pressure thresholds need platform-specific tuning. The implementation plan starts with conservative defaults and should record telemetry-friendly diagnostics.
- Cache-size defaults need tuning over time. The implementation plan starts with conservative wheel/source/archive caps.
- Windows symlink probing strategy at install time, including detection of Developer Mode and graceful copy fallback.
- Frontend UX for `queued_pending_switch` and `blocked_by_memory` states. The product copy belongs in the design system, not in this document.
- Runtime profile signing format. Tied to the broader package signing decision in [RUNTIME_ISOLATION_ARCHITECTURE.md](RUNTIME_ISOLATION_ARCHITECTURE.md) follow-up decisions.
- VRAM-release verification on stop, per backend (CUDA, MPS, CPU). Implementation will likely be heuristic.

## Implementation Plan Impact

The Phase 5 implementation plan should:

1. Pin v1 to one supported runtime profile family while making profile variants and manifest hashes first-class schema fields.
2. Split Phase 5 into bounded sub-phases for runtime schema, dependency locking, custom-node materialization, model-store/model-view materialization, smoke testing, runner lifecycle, transactional promotion, garbage collection, diagnostics/API state, integration tests, and deferred registry/non-bundled source resolution.
3. Move the workflow-open warm retention policy, runner-switch policy, and expanded `RunnerSupervisor` state surface into implementation tasks and acceptance criteria.
4. Add tasks for: runtime profile catalog schema, resolved dependency lock schema with hashes, deterministic custom node workspace manifest with import order, allowlisted launch configuration surface, and model materialization fallback ladder with install-state recording.
5. Add tasks for: explicit derived reference tracking, startup sweep for stale transactions and unpromoted staging directories, LRU cache caps with documented defaults, and confirm-before-delete for large model blobs.
6. Require smoke-test acceptance for dependency import, custom node import, runner start, and minimal real graph execution before a workflow is marked ready.
7. Enforce the default community security policy: wheels-only, hash-required, no sdists, no native builds, no arbitrary install scripts, no custom-node `setup.py` execution, and no runner access to the frontend/backend API token.
8. Defer registry lookup, non-bundled custom node source resolution, and broader candidate-lock generation until the locked/bundled runtime preparation path is working end to end.
9. Reference this document from the main developer docs so future contributors find the refined fingerprint and runner-switch rules without rediscovering them.
