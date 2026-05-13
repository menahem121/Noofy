# Runtime Isolation Architecture

Date: 2026-04-30 (initial), updated 2026-05-04 to reflect the implemented state.

Status: Accepted and implemented for the locked/bundled, registry-resolved, and trust/policy scope. Operational distribution of production trust roots and a full in-app marketplace UI remain future product work.

## Why This Exists

Noofy is a local AI workflow app. Community ComfyUI workflows can ship custom nodes (Python) with their own dependencies. A single global mutable ComfyUI/Python environment would make Noofy fragile: one workflow can upgrade or downgrade packages used by another, broken nodes can break the whole engine, native extensions cannot be reliably unloaded, and non-technical users cannot be expected to repair Python, pip, virtualenvs, or custom-node folders.

The opposite extreme — one full runtime per workflow — also fails: disk usage explodes (Python + Torch + ComfyUI + caches duplicated each time), maintenance scales linearly with workflow count, and runtime reuse becomes impossible. Noofy instead uses **reusable compatibility-group runtimes**: workflows that resolve to the same fingerprint share immutable runtime artifacts.

Runtime isolation gives Noofy a stable foundation for installs, rollback, diagnostics, and support. It is dependency/runtime isolation, **not** a malicious-code sandbox. Noofy protects its own architecture from dependency conflicts and broken installs; it does not claim arbitrary community Python code is safe.

OS-level sandboxing is evaluated separately in [OS_SANDBOXING_FEASIBILITY.md](OS_SANDBOXING_FEASIBILITY.md).

## Trust Boundary: Control Plane vs Data Plane

```
Tauri shell  ──▶  Noofy backend (trusted control plane)
                      │
                      ├── RunnerSupervisor + JobRegistry
                      ├── CapsuleInstaller / RuntimeWorkspacePreparer
                      ├── Memory Governor
                      └── spawns ────▶  ComfyUI runner processes (data plane)
                                         ├── isolated dependency env
                                         ├── isolated runner workspace
                                         └── runner-visible model view
```

**Trusted control plane** (Tauri + Noofy backend):
- Owns the app API contract, install resolution, trust verification, source policy, fingerprint calculation, runner selection, diagnostics.
- Reads workflow archives, graphs, and custom-node files **as data only**.
- **Must never import community custom-node Python modules** or execute custom-node setup code.

**Runner data plane** (ComfyUI runner processes):
- Where every custom-node import, compatibility check, smoke test, and graph execution happens.
- Each runner uses one dependency environment + one runner workspace + one model view.
- Runner processes can be stopped, discarded, and recreated without touching trusted core.
- Runner processes do **not** receive `NOOFY_API_TOKEN`. Tauri's per-launch API token never leaves the control plane.
- On macOS/Linux runners launch in their own process group/session; on Windows in a new process group, so the supervisor can terminate the full process tree.

The frontend calls only the Noofy backend API. It never calls ComfyUI directly.

## Components And Where They Live

All runtime isolation code lives in [backend/app/runtime/](../backend/app/runtime/) unless noted.

| Concern | Source |
|---|---|
| Path layout, env var overrides | [backend/app/core/paths.py](../backend/app/core/paths.py) |
| Trust roots, signature verification (Ed25519 + HMAC for tests) | [backend/app/trust.py](../backend/app/trust.py) |
| Durable source policy schema and enforcement | [backend/app/source_policy.py](../backend/app/source_policy.py) |
| Workflow package import orchestration | [backend/app/workflows/importer.py](../backend/app/workflows/importer.py), [backend/app/workflows/package.py](../backend/app/workflows/package.py) |
| Workflow import helpers | [archive_validation.py](../backend/app/workflows/archive_validation.py), [import_normalization.py](../backend/app/workflows/import_normalization.py), [package_persistence.py](../backend/app/workflows/package_persistence.py), [import_runtime_profile.py](../backend/app/workflows/import_runtime_profile.py), [import_policy.py](../backend/app/workflows/import_policy.py), [import_capsule_lock.py](../backend/app/workflows/import_capsule_lock.py), [store_paths.py](../backend/app/workflows/store_paths.py) |
| Capsule lock + install-state schemas | [backend/app/workflows/capsule.py](../backend/app/workflows/capsule.py), [backend/app/runtime/install_state.py](../backend/app/runtime/install_state.py) |
| Layered fingerprints (dependency-env, runner, capsule) | [backend/app/runtime/fingerprints.py](../backend/app/runtime/fingerprints.py) |
| Runtime profile catalog | [backend/app/runtime/profiles/profiles.py](../backend/app/runtime/profiles/profiles.py), [backend/app/runtime/profiles/profile_catalog.json](../backend/app/runtime/profiles/profile_catalog.json) |
| Pinned core-node manifest | [backend/app/runtime/core_node_manifest.json](../backend/app/runtime/core_node_manifest.json) |
| Dependency lock + `uv` resolver, wheel cache | [backend/app/runtime/dependencies/dependency_lock.py](../backend/app/runtime/dependencies/dependency_lock.py), [dependency_resolver.py](../backend/app/runtime/dependencies/dependency_resolver.py), [dependency_env.py](../backend/app/runtime/dependencies/dependency_env.py) |
| Custom-node node-registry / non-bundled source resolution | [backend/app/runtime/node_registry.py](../backend/app/runtime/node_registry.py), [backend/app/runtime/dependencies/custom_nodes.py](../backend/app/runtime/dependencies/custom_nodes.py) |
| Workspace materialization (custom nodes, model view) | [backend/app/runtime/storage/workspace_preparer.py](../backend/app/runtime/storage/workspace_preparer.py), [backend/app/runtime/storage/workspace_store.py](../backend/app/runtime/storage/workspace_store.py) |
| Shared model store + runner-visible model views | [backend/app/runtime/models/model_store.py](../backend/app/runtime/models/model_store.py) |
| Transactional install + promotion + quarantine + startup sweep | [backend/app/runtime/capsule_installer.py](../backend/app/runtime/capsule_installer.py), [install_transactions.py](../backend/app/runtime/install_transactions.py) |
| Runner process lifecycle, isolation, smoke tests | [backend/app/runtime/runners/runner_process.py](../backend/app/runtime/runners/runner_process.py), [isolation.py](../backend/app/runtime/dependencies/isolation.py), [smoke_test.py](../backend/app/runtime/smoke_test.py) |
| Runner selection, leases, idle-warm, switching | [backend/app/runtime/runners/supervisor.py](../backend/app/runtime/runners/supervisor.py), [runner_coordinator.py](../backend/app/runtime/runners/runner_coordinator.py) |
| Memory Governor (estimates, co-residence, eviction, retry) | [backend/app/runtime/memory/memory_governor.py](../backend/app/runtime/memory/memory_governor.py), [backend/app/runtime/memory/service.py](../backend/app/runtime/memory/service.py); strategy in [MEMORY_GOVERNOR.md](MEMORY_GOVERNOR.md) |
| Reference index + GC + retention windows | [backend/app/runtime/storage/storage_gc.py](../backend/app/runtime/storage/storage_gc.py), [model_gc.py](../backend/app/runtime/models/model_gc.py) |
| Engine adapter + job registry | [backend/app/engine/](../backend/app/engine/) |

Manual validation and hardware smoke harnesses are tools, not product runtime modules. They live under [backend/tools/validation/](../backend/tools/validation/) and are invoked through Makefile validation targets.

Run and workflow services request a runner from `RunnerSupervisor` for every workflow operation. There is no implicit global adapter endpoint; jobs are tracked through `job_id -> runner_id` in the engine job registry. `EngineService` remains only as a temporary migration facade where internal callers have not moved to the owning domain service yet.

## Workflow Capsules

A workflow capsule is the resolved install unit for a workflow. Capsule data is split across three files inside `workflow-store/packages/<publisher_id>/<package_id>/<version>/`:

- `package.json` — what the user runs and sees (graph, dashboard, identity).
- `capsule.lock.json` — **immutable** resolved facts: engine version, runtime profile ID, dependency-env / runner / capsule fingerprints, custom-node records, model records (by content hash), trust evidence, source policy.
- `install-state.json` — **mutable** local state for this machine: status, resolved blob/view paths, materialization strategy, smoke-test report, last error.

Identity always carries `publisher_id`, `package_id`, `version`, `trust_level`, and source metadata. User-imported packages cannot silently shadow Noofy Verified built-ins by reusing an ID; conflicts are namespaced or require an explicit replacement action.

Models are not duplicated per capsule. Capsules reference models by SHA-256 content hash in the shared model store; install state records the per-machine resolution (blob path, materialized model-view path, materialization strategy, asset ownership).

## Runtime Profile

A **runtime profile** is the named, pinned ComfyUI runtime contract Noofy supports for a class of workflows. Every capsule references a `runtime_profile_id`; install state records the selected `runtime_profile_variant_id` and `runtime_profile_manifest_hash`. Workflows targeting an unknown profile, or a variant unsupported on the current OS/backend, fail closed — Noofy never silently falls back to a "close enough" runtime.

A profile pins, at minimum:

- ComfyUI core version + source hash + frontend version
- Noofy-managed Python build ID
- Torch version + wheel build tag, and GPU backend (`cuda`, `mps`, `cpu`, `directml`, …)
- Core dependency lock hash (resolved transitive, with hashes)
- Allowlisted launch-config surface (preview method, VRAM mode, attention backend, precision, enabled-nodes set, extra-paths mode, Noofy-controlled env vars)
- Supported OS/architecture/backend matrix and install policy version

Multiple profile families and variants are first-class in the schema, but **v1 ships exactly one profile family** with explicit platform/backend variants. Product profile generation requires a clean reproducible ComfyUI source artifact materialized under `runtime-store/core-engines/...`; generation directly from `third_party/comfyui/` is rejected for product use and only allowed as a development/package input. Definitions live in [profile_catalog.json](../backend/app/runtime/profiles/profile_catalog.json) and [profiles.py](../backend/app/runtime/profiles/profiles.py).

## Layered Fingerprints

Compatible artifacts are reused across workflows by fingerprint, instead of duplicated blindly:

- **Dependency-env fingerprint**: OS+arch, managed Python build, Torch/GPU backend profile, dependency lock hash, native dependency constraints, install policy version, runtime profile manifest hash + variant ID.
- **Runner fingerprint**: dependency-env fingerprint, ComfyUI source hash, custom-node workspace manifest hash, allowlisted launch-config hash, model-view configuration when relevant.
- **Capsule fingerprint**: workflow package hash, graph hash, dashboard schema hash, model requirement hashes, trust/signature metadata, runner fingerprint.

Fingerprints are byte-stable canonical JSON. They never include machine-local absolute paths or hardlink/symlink/copy materialization choices.

## Runtime Store Layout

Everything community-installable lives under the user data directory, never inside the app bundle or `Program Files`:

```
<data_dir>/
  runtime-store/
    python/cpython-<ver>-<build>/         # managed Python (product strategy)
    core-engines/comfyui-core-<ver>-<src-hash>/   # immutable ComfyUI source artifact
    envs/dep-env-<dep-env-fp>/            # immutable when ready
    runner-workspaces/runner-workspace-<runner-fp>/   # immutable when ready
    runners/runner-<id>/                  # PID, port, logs of a live runner process
    transactions/install-<id>/            # all preparation work goes here first
    dependency-locks/<lock-hash>/         # Noofy-owned resolved locks
    model-store/
      blobs/sha256/<hash>
      refs/<model-id>.json
      materialized/views/model-view-<fp>/   # per-view, no global folder/name overwrite
  workflow-store/packages/<publisher>/<id>/<ver>/
  custom-node-cache/<package>/<commit-or-version>/
  wheel-cache/
  trust/trusted-keys.json                 # public trust roots; can be overridden by NOOFY_TRUST_KEYS_FILE
  outputs/  logs/  cache/
```

The trusted core runtime under `runtime-store/core-engines/...` is **content/version locked**. Community installs never write into it.

## Install Path

Preparation is transactional. Every install writes under `runtime-store/transactions/install-<id>/` first and only promotes ready artifacts after smoke succeeds.

For verified or registry-resolved packages the resolver:

1. Opens an install transaction.
2. Verifies trust evidence (signature/registry metadata) and source policy before any download.
3. Verifies every non-core node resolves to a pinned package + content hash.
4. Resolves dependencies under the active policy and installs them with `uv` into a staged dependency env.
5. Materializes bundled or cached custom-node sources into a staged runner workspace.
6. Materializes a per-view model tree from the shared model store (hardlink → symlink → copy fallback).
7. Runs the split smoke suite (dependency import, custom-node import, runner health, workflow execution).
8. Atomically promotes dependency env, runner workspace, and install state.
9. Quarantines failed staging directories with a bounded retention window; never mutates ready artifacts.

Backend startup runs an idempotent sweep that quarantines stale transactions, kills orphan runner processes from a prior crash, removes stale PID/temp files, and expires old quarantines.

## Smoke Stages (gating `ready`)

Install state records a split `smoke_test_report`. A workflow becomes `ready` only when every applicable stage passes:

1. **dependency-env** — wheels import inside the staged dependency env. Uses dependency-lock `import_names` when available.
2. **custom-node import** — staged runner registers required custom node types via `/object_info`.
3. **runner health** — staged runner process starts on a localhost port and reports healthy.
4. **workflow execution** — a real graph runs end-to-end. Custom-node packages must exercise at least one declared custom-node type. Workflows with unresolved runtime inputs (e.g. creator-local `LoadImage`) cannot reach `ready`; they stop at `prepared_needs_input_setup`.

Real-hardware staged smoke is run with the Makefile validation target on the Linux validation host. Unit tests use lightweight/fake runner adapters.

## Trust Model

Imported archives are parsed as data first; trust evidence is verified before any download or runtime preparation. An archive cannot make itself trusted by writing `trust_level` into `package.json`.

| Level | Requirement | Auto-prepare |
|---|---|---|
| **Noofy Verified** | Ed25519 package signature against a configured Noofy trust root | Yes |
| **Registry Locked** | Signed registry metadata against a configured registry trust root, matching the active registry snapshot | Yes, if policy permits the device/backend |
| **Quarantined Community** | Resolvable into an isolated capsule under policy | Only with explicit user opt-in |
| **Unsupported** | Trust evidence missing/invalid, sources unresolvable, dependencies cannot be locked under policy, hardware unsupported | No |

Verification details:

- Production verifier is **Ed25519** with key IDs, multiple active keys for rotation, purpose scoping, revocation, and not-before / expiry windows.
- HMAC-SHA256 verifier is retained for **local development and tests only** and must be explicitly enabled by the keyring.
- The signed payload is canonical JSON over package metadata + content hashes for graph, dashboard, capsule lock, and export report.
- Signed registry metadata signs the registry ID, snapshot hash, and package-payload hash.
- Trust roots load from `<data_dir>/trust/trusted-keys.json` (override: `NOOFY_TRUST_KEYS_FILE`). A missing or malformed keyring fails closed: imports claiming Noofy Verified or Registry Locked drop to Unsupported.
- `GET /api/trust/policy` exposes public trust policy (levels, source policies, key IDs, algorithms, purposes). It must not expose any secret material.

The publishing process for Noofy Verified is documented in [NOOFY_VERIFIED_PUBLISHING.md](NOOFY_VERIFIED_PUBLISHING.md).

## Source Policy

Source policy is a **durable record**, not just a UI label. It is stored on normalized package records, generated capsule locks, import reports, dependency locks, and workflow status payloads, and enforced at every gate where Noofy might fetch or accept content.

Each policy snapshot carries: trust level, policy version, allowed registry/source/model origins, registry snapshot identity, package source type, model source trust, and whether automatic preparation is allowed.

Enforcement points:

- **Import**: rejects mismatched registry snapshots, blocks community sources without explicit user opt-in.
- **Custom-node source download**: explicit-metadata sources cannot bypass a policy that requires verified or registry origins.
- **Model materialization**: hash-locked downloads must match an allowed model origin; filename+size local reuse is gated by policy.
- **Dependency env install / reuse**: dependency locks carry the source-policy snapshot; stale or policy-less locks for a policy-bound workflow fail as policy mismatches.
- **Smoke diagnostics**: blocked/failed stages record a redacted source-policy snapshot for developer details, without leaking secrets or local paths.

## Memory Governor

Multiple runners may stay warm only when the Memory Governor judges co-residence safe. The full strategy lives in [MEMORY_GOVERNOR.md](MEMORY_GOVERNOR.md). Architectural points relevant here:

- One resident GPU-heavy runner is the **safe fallback** when confidence is low. `unknown` and `gpu_medium` are treated conservatively as GPU-heavy until local evidence proves otherwise.
- Compatible runners stay `idle_warm` while at least one workflow view holds a lease; closing the last lease starts a default 90-second cooldown before eviction.
- Local memory observations are stored in mutable local app data — never written back into `.noofy` packages or immutable capsule locks.
- Memory state surfaces to the UI through a compact `memory_status` field; structured `memory_decision` payloads sit behind developer details. Aggregate counters are exposed at `GET /api/memory-governor/metrics`.

## Reference Tracking And GC

The GC reference index is **derived** from `install-state.json`, package/capsule records, and live runner descriptors — there are no separate refcount files. GC roots include `ready` and `prepared_needs_input_setup` installs, active/queued/idle-warm runners, open workflow leases, pinned runtime profile artifacts, and protected user-local model sources.

Default retention windows: failed transactions / quarantine 7 days; unreferenced dependency envs and runner workspaces 14 days; orphan model views 7 days. Configurable LRU caps cover wheel cache, custom-node source cache, and imported package archive cache. `user_local` model sources are never deleted; deleting Noofy-owned model blobs over 1 GB requires explicit confirmation.

## Security Boundaries

Dependency isolation prevents:

- package conflicts between workflows
- one workflow's upgrade breaking another
- broken custom nodes corrupting the trusted core runtime
- failed installs leaving the global environment half-mutated

It does **not** prevent malicious Python code from reading runner-accessible files, making network requests, consuming CPU/GPU, or exfiltrating local data if network access is available.

Noofy's first security layer is supply-chain control: signed/verified package metadata, pinned commits and hashes, hash-locked wheels and models, wheels-only-with-hashes for community installs, no arbitrary install scripts, and no community code imports in the trusted backend process. Any future OS-level sandboxing must be evaluated per platform per [OS_SANDBOXING_FEASIBILITY.md](OS_SANDBOXING_FEASIBILITY.md), and Noofy product copy must not imply a sandbox that does not exist.

## Hardware Compatibility Metadata

Creator `.noofy` hardware observations (peak VRAM/RAM, tested resolution, batch size, GPU, backend, precision) are advisory first-run hints. Local Memory Governor observations on the user's machine override creator hints once enough evidence exists. UI language stays probabilistic (e.g. "may run slowly or fail"); exact behavior depends on resolution, batch size, model, precision, backend, driver, OS, and other running apps.

## Product Python Strategy

- Development may use `backend/.venv` or `NOOFY_BACKEND_PYTHON`; external ComfyUI mode is dev-only.
- Product builds must not depend on system Python, Homebrew Python, user PATH, Conda, or developer virtualenvs.
- Product v1 ships a bundled Noofy-managed CPython and `uv` in the Tauri resource root, verified by [PACKAGED_RUNTIME.md](PACKAGED_RUNTIME.md). Runtime-store Python environments for ComfyUI and community workflows are prepared from this trusted bootstrap runtime into app-data paths, keeping trusted backend dependencies separate from managed ComfyUI/PyTorch and custom-node dependencies.
- `uv` is the primary resolver and dependency-env installer; `pip` is a compatibility fallback inside managed Python environments.

## Open Risks And Follow-Ups

- Operational publication of production Ed25519 trust-root files and key rotation/revocation distribution.
- Registry metadata format, hosting, and revocation distribution at marketplace scale.
- Windows symlink permissions, case-insensitive collisions, and antivirus interactions for model-view materialization.
- Linux/macOS/Windows process-tree cleanup remains OS-specific and must keep being validated.
- GPLv3 distribution obligations for ComfyUI when bundling the core runtime.
- OS-level sandbox implementation is a future hardening project, not a completion blocker for dependency/runtime isolation.
