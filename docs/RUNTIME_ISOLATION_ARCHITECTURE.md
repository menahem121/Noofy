# Runtime Isolation Architecture Review

Date: 2026-04-30

## Summary

The proposed direction is correct: Noofy must not install community workflow custom nodes and Python dependencies into one mutable global ComfyUI environment. The current codebase has a useful process foundation, but it is still built around a single ComfyUI runtime environment and a single active `ComfyUIEngineAdapter` endpoint. That is acceptable for Milestone 1 and built-in workflows, but it is not a safe foundation for community workflow installation.

The recommended product architecture is:

- Tauri/Rust is the root process owner for the desktop app and starts a trusted Noofy supervisor sidecar.
- Tauri and the Noofy backend are the trusted control plane: API contracts, install policy, metadata parsing, runner selection, diagnostics, and user-facing state.
- ComfyUI runner processes are the data plane: they execute workflow graphs and import custom node code.
- The trusted backend must never import community custom node code. Custom-node imports, smoke tests, and ComfyUI startup checks happen only inside isolated runner processes.
- Community workflows install into workflow capsules and fingerprinted runner environments, never into the trusted core runtime.
- Models, wheels, source archives, and custom node source checkouts are shared by content-addressed cache. Python site-packages, runner workspaces, enabled custom nodes, and runner model views are isolated by fingerprint.
- Capsule locks are immutable. Local install state, smoke-test results, timestamps, and failures live in separate state files.

Do not build the full capsule system in one pass. The next implementation should define the contracts, paths, manifests, immutable locks, install state, and runner-supervisor boundary before adding real custom-node installation.

## Current State Of The Codebase

### Tauri Shell

Current scaffold: `frontend/src-tauri/src/main.rs`.

The Rust shell currently:

- Generates a per-launch API token.
- Starts the Python backend with `python -m app --port 0`.
- Sets `NOOFY_API_TOKEN` for the backend.
- Reads `NOOFY_BACKEND_API_BASE_URL=...` from backend stdout.
- Injects `window.__NOOFY_RUNTIME_CONFIG__` into the webview.
- Exposes `noofy_runtime_config` as an IPC fallback before React renders.
- Kills the backend process on window close or app exit.

This is a good desktop process boundary for development. It is not product-grade yet because the shell still chooses a local Python executable from `NOOFY_BACKEND_PYTHON`, `backend/.venv`, Homebrew Python on macOS, or `python3`. Product builds must not depend on any system or Homebrew Python.

The current shell also kills the direct backend child process. Product builds need reliable process-tree cleanup so backend-owned runner grandchildren are terminated on app exit. On macOS/Linux this usually means process groups; on Windows it usually means Job Objects or equivalent child-process tracking.

### Backend Startup

Current entry point: `backend/app/__main__.py`.

The backend can bind to a free localhost port, print the API URL for Tauri, and run `uvicorn app.main:app`. `backend/app/main.py` sets up FastAPI, token middleware, CORS, and lifespan shutdown.

This is a good app API sidecar foundation. It is not yet a packaged product runtime.

### Managed ComfyUI Runtime

Current runtime code:

- `RuntimeEnvironment` creates/checks one app-owned ComfyUI virtual environment at `<runtime_dir>/comfyui-venv`.
- `RuntimeEnvironment.bootstrap()` creates the venv, installs PyTorch, then installs `ComfyUI-official-repo/requirements.txt`.
- `RuntimeManager` owns one ComfyUI process for either external or managed mode.
- Managed mode selects a free port, runs `main.py --listen <host> --port <port>`, polls `/system_stats`, captures stdout, restarts after crashes, and writes a PID file.
- `EngineService` owns one `RuntimeManager` and one `ComfyUIEngineAdapter`.
- `ComfyUIEngineAdapter` points at one ComfyUI endpoint at a time and can be reconfigured on restart.

This is good enough for a trusted built-in workflow and managed sidecar proof. It is not compatible with community workflow dependency isolation because it creates one mutable environment and one active runner.

The current `EngineService` also routes all job progress, cancellation, and results through one adapter. Once multiple runners exist, every job must be associated with the runner that owns it.

### Workflow Packages

Current schema: `backend/app/workflows/package.py`.

Current package contents include:

- metadata
- engine target
- required models by ComfyUI folder and filename
- ComfyUI graph
- exposed inputs and dashboard schema
- outputs

This is an execution/UI package, not an install capsule. It does not include:

- custom node package metadata
- dependency locks
- layered runtime fingerprints
- trust level
- model hashes as first-class identity
- install/smoke-test status
- hardware compatibility metadata
- rollback state

The current loader lets user workflow packages override bundled packages by ID. That is useful during development, but product builds need namespace and trust-precedence rules so an imported package cannot silently replace a verified built-in workflow.

### Model Handling

Current validation asks the active adapter for available models and compares `(folder, filename)`. The adapter first tries the ComfyUI `/models` API and falls back to the app-owned models directory. This follows the existing rule that model validation should use the active adapter, but it is not a content-addressed model store and cannot deduplicate or verify models by hash.

## Gaps Against The Proposed Architecture

| Area | Current state | Gap |
|---|---|---|
| Trusted core runtime | One managed ComfyUI venv | No immutable core runtime contract |
| Community workflow install | User packages can override bundled packages by ID | No resolver, trust policy, or install transaction |
| Custom nodes | Not modeled | Unknown nodes cannot be resolved safely |
| Python dependencies | Installed into one ComfyUI venv | No dependency lock or fingerprint isolation |
| Runner process model | One `RuntimeManager` and one adapter endpoint | No per-fingerprint runner selection |
| Job routing | All job operations go through one adapter | No `job_id -> runner_id` registry |
| Model storage | Folder/name validation | No shared hash store, verification, or dedupe |
| Product Python | Local venv/Homebrew/system fallback | No packaged Python or sidecar strategy |
| Process cleanup | Tauri kills the backend child | No product-grade process-tree cleanup for runner grandchildren |
| Rollback | Not present | Failed installs can only be avoided by not installing yet |
| Trust | Not present | No verified/community/unsupported distinction |
| Workflow precedence | User package ID overrides bundled package ID | No namespace or trust precedence for verified workflows |
| Security | Token/CORS/localhost only | No supply-chain or custom-code policy |
| GC | Not present | No cleanup policy for envs, caches, models |

## Review Of Proposed Claims

### Strongly Agree

- One global mutable ComfyUI environment will become fragile.
- Custom nodes are Python code and must be treated as executable code, not inert workflow data.
- Python dependencies and native extensions are not reliably unloadable from a running process.
- The isolation boundary must be a process, not dynamic venv switching inside one ComfyUI process.
- Dependency environments should be reused by dependency fingerprint rather than duplicated per workflow.
- Models should live in a shared store and be referenced by hash/source metadata.
- Unknown or unresolved workflows must fail safely without mutating core runtime or existing workflows.
- UI should hide technical setup terms by default and keep raw logs behind details.

### Needs Refinement

- "Trusted core runtime is immutable" should mean content/version locked and never mutated by community installs. The runtime may still live in user data if it is created after install. Product packaging should not write into the app bundle or Program Files because of code signing and permissions.
- Fingerprint layers should include only the inputs that affect that layer. Model hashes usually belong to the capsule's model requirements, not the Python dependency environment fingerprint, unless a model implies a different engine package or hardware backend.
- A virtual environment provides dependency isolation, not a malicious-code sandbox. The architecture should avoid presenting quarantined workflows as safe. They are only isolated from other dependency stacks.
- Blind install should be allowed only for workflows that resolve to a policy-approved lock. A workflow with arbitrary `install.py`, unpinned Git refs, sdists requiring native builds, or unknown model sources should not be one-click installed by default.
- A single "Noofy Supervisor" can be hybrid. It does not need to be all Rust. Rust should own top-level process lifecycle; Python should own workflow resolution and ComfyUI runner semantics because that logic is close to the engine and package formats.
- A single runtime fingerprint is too coarse. Split identity into dependency environment, runner, and capsule fingerprints so Noofy can reuse expensive Python environments without pretending every workflow with different metadata needs a new venv.
- The client should not solve arbitrary community dependency graphs during install if it can avoid it. Noofy Verified workflows should arrive with pre-resolved locks from trusted tooling; the desktop client verifies and materializes those locks.
- Runtime locks should be immutable. Local install status, smoke-test status, errors, and timestamps belong in separate local state files.
- Runner workspaces should be separate from Python envs. The env owns site-packages; the workspace owns enabled custom nodes, ComfyUI launch files, model view, logs, and temp files.
- Runner-specific secrets should be separate from the frontend/backend API token. Do not pass the Tauri frontend token to ComfyUI runner processes.

## Recommended Architecture

### Control Plane And Data Plane

Treat Noofy as two layers:

- Trusted control plane: Tauri and the Noofy backend/supervisor.
- Runner data plane: ComfyUI runner processes and their Python environments.

The control plane may parse workflow manifests, verify signatures, resolve registry metadata, compute fingerprints, manage local paths, and report status. It must not import or execute community custom node code. Community code is executed only in runner data-plane processes that can be started, stopped, discarded, and recreated without mutating the trusted backend or core runtime.

This is a product boundary, not just a code organization preference. It keeps install resolution, diagnostics, and user-facing state available even if a custom node crashes a runner.

### Process Model

```text
Tauri app process
  -> Noofy backend/supervisor sidecar
      -> core ComfyUI runner process, for built-in/default-node workflows
      -> workflow runner process for runner fingerprint A
      -> workflow runner process for runner fingerprint B
```

Rules:

- Tauri starts exactly one Noofy backend/supervisor sidecar.
- The backend/supervisor starts and stops ComfyUI runner processes.
- Each runner process uses one dependency environment and one runner workspace.
- A dependency environment belongs to one dependency-env fingerprint; a runner workspace belongs to one runner fingerprint.
- A runner may execute multiple workflows only if they share a compatible runner fingerprint.
- Switching to an incompatible workflow stops or idles the current runner and starts the correct one.
- Keep at most one GPU-heavy runner active by default. Warm runner reuse can be added later.
- The backend tracks `job_id -> runner_id` so progress, cancellation, and result calls route to the runner that owns the job.
- Runner processes receive only runner-scoped secrets if needed. They should not receive the frontend/backend API token injected by Tauri.
- Product shutdown must terminate the whole backend/runner process tree, not only the direct backend child process.

### Supervisor Location

Recommended: hybrid.

- Rust/Tauri owns desktop lifecycle, token generation, backend startup, packaged resource paths, app close cleanup, and OS integration.
- Python backend owns API routes, workflow package parsing, install resolver, layered fingerprint calculation, runner state, ComfyUI endpoint selection, and diagnostics.
- The existing `RuntimeManager` should evolve into a lower-level `RunnerProcessManager`.
- A new `RunnerSupervisor` or `RuntimeSupervisor` should sit above process managers and answer: "which runner should this workflow use?"

This avoids pushing workflow-resolution logic into Rust while still making the Rust shell the reliable root process.

### Engine Service Direction

The current `EngineService` assumes one active `RuntimeManager` and one active adapter. That should change before community workflows.

Recommended future flow:

```text
run_workflow(workflow_id)
  -> load workflow capsule/package
  -> verify install_status == ready
  -> ask RunnerSupervisor for a runner for capsule.runner_fingerprint
  -> get or create ComfyUIEngineAdapter bound to that runner endpoint
  -> validate models against that runner/model view
  -> submit graph
  -> record job_id -> runner_id
```

Then:

```text
get_progress(job_id) / cancel_job(job_id) / get_result(job_id)
  -> look up runner_id for job_id
  -> route to that runner's adapter/client
```

The `EngineAdapter` contract can remain app-owned, but the service should not rely on a singleton adapter endpoint for all workflows. During the first incremental refactor, Noofy can enforce one active runner and one active job if that keeps the implementation small. Even then, the job registry shape should exist so multi-runner support does not require API redesign.

## Recommended Runtime Layout

Use the platform data dir already defined in `NoofyPaths`:

- macOS: `~/Library/Application Support/Noofy`
- Windows: `%APPDATA%\Noofy`

Suggested layout:

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
      <workflow-id>/<version>/
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

- Python distributions
- ComfyUI source cache
- wheels
- custom node source checkouts
- model blobs
- downloaded package archives

Isolated:

- Python site-packages
- runner workspace files
- enabled custom nodes for each runner workspace
- runner-visible model view
- environment and runner manifests
- smoke-test result
- runner process state

Keep the Python dependency environment separate from the runner workspace:

- Dependency env: interpreter, site-packages, installed wheels, and dependency manifest.
- Runner workspace: ComfyUI launch directory, enabled custom nodes, runner model view, logs, temp files, and runner manifest.

This split allows Noofy to reuse expensive Python environments while still creating different runner workspaces for different enabled custom-node sets or ComfyUI launch configurations.

### Model Store

Models should be identified by content hash and metadata, not only folder/name.

Recommended model record:

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

ComfyUI still expects folder/name paths. Noofy can materialize a runner model view from the hash store using hardlinks or symlinks where available. On Windows, symlink support can be inconsistent for non-developer users, so hardlinks or direct shared folder configuration may be more reliable. Copying should be a last resort because models are large.

## Product And Development Python Strategy

### Development

Keep development simple:

- Backend: `backend/.venv`
- Tauri fallback: `NOOFY_BACKEND_PYTHON` or local Python
- ComfyUI managed mode: current `RuntimeEnvironment` is acceptable for built-in workflow testing
- External ComfyUI mode remains useful for fast iteration

### Product

Product builds must not depend on system Python, Homebrew Python, user PATH, Conda, or a developer venv.

Recommended product strategy:

1. Ship a signed Noofy-managed CPython distribution and core runtime with the app for product v1 where practical. First-run runtime download can be evaluated later, but the starter experience should not depend on arbitrary dependency solving.
2. Ship or install a small environment manager. Prefer `uv` for venv creation, wheel caching, and lock-based installs. Keep `pip` as the compatibility fallback inside the managed Python.
3. Run the Noofy backend either:
   - as a Python package under the managed CPython runtime, or
   - as a PyInstaller/standalone backend executable while still shipping managed CPython for ComfyUI runners.
4. Create dependency environments from lockfiles under `runtime-store/envs/dep-env-<dependency-env-fingerprint>`.
5. For blind installs, install only pinned wheels/hashes from allowed sources. Do not run arbitrary install scripts by default.

Recommendation:

- Use bundled/managed CPython plus `uv` as the default direction.
- Use PyInstaller only as an optional packaging optimization for the backend, not as the whole runtime answer, because ComfyUI and custom nodes still need real Python environments.
- Avoid Conda as the default. It is large, slow to solve, hard to productize cleanly, and not needed for the current architecture.
- Avoid raw `venv + pip install -r requirements.txt` for community installs. It is acceptable for the current core prototype, but not for reproducible capsules.

## Workflow Capsule Format

Keep `WorkflowPackage` as the app/UI execution schema. Add a separate install-layer capsule/lock schema so execution metadata does not become overloaded.

The lock file must be immutable after verification. It describes the resolved workflow and runtime requirements. Local facts such as install progress, installed path, smoke-test status, errors, and timestamps belong in `install-state.json`, not in the lock.

Suggested `capsule.lock.json`:

```json
{
  "schema_version": "0.1.0",
  "workflow": {
    "id": "example_workflow",
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
  "custom_nodes": [
    {
      "package_id": "comfyui-example-node",
      "source": "https://github.com/example/comfyui-example-node",
      "commit": "abcdef",
      "trust_level": "registry_locked",
      "node_types": ["ExampleNode"]
    }
  ],
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
    "level": "registry_locked",
    "publisher": null,
    "signatures": []
  }
}
```

Suggested `install-state.json`:

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

Important distinction:

- `package.json` describes what the user runs and sees.
- `capsule.lock.json` describes what Noofy resolved and trusts. It is immutable after verification.
- `install-state.json` describes what exists on this machine. It is mutable local state.

### Package Identity And Precedence

Product workflow identity should include namespace/publisher, not only a short workflow ID. User-imported packages should not silently override Noofy Verified built-ins by matching an ID. If a user imports a package with the same display name or legacy ID, Noofy should either install it under a distinct namespace or require an explicit replacement action with trust downgrade language.

Suggested identity fields:

- `publisher_id`
- `package_id`
- `version`
- `trust_level`
- `source`
- `signature`

## Runtime Fingerprints

Use layered fingerprints instead of one coarse fingerprint.

### Dependency Environment Fingerprint

Identifies the expensive Python environment that can be reused by compatible runners.

Inputs:

- OS and architecture
- Python major/minor version and Noofy Python build ID
- Torch/GPU backend profile
- dependency lock hash
- native dependency constraints if supported
- install policy version

Does not include:

- workflow graph
- dashboard metadata
- enabled custom node set if the node code is mounted into the runner workspace rather than installed into site-packages
- models unless they change Python/runtime requirements

### Runner Fingerprint

Identifies a ComfyUI runner workspace that can execute a compatible set of workflows.

Inputs:

- dependency environment fingerprint
- ComfyUI version/source hash
- enabled custom node package IDs and versions/commits
- custom node workspace manifest hash
- launch configuration that affects behavior
- model-view configuration when relevant

### Capsule Fingerprint

Identifies the resolved workflow package/capsule.

Inputs:

- workflow package hash
- ComfyUI graph hash
- dashboard schema hash
- model requirement hashes
- trust/signature metadata
- runner fingerprint

This split avoids two bad outcomes:

- Rebuilding large Python environments for workflows that only differ by graph or UI metadata.
- Reusing one runner workspace for workflows that need different enabled custom node code.

## Install Resolver Flow

Recommended resolver pipeline for Noofy Verified or otherwise pre-resolved workflows:

1. Create an install transaction directory under `runtime-store/transactions/install-<id>`.
2. Parse workflow package metadata and the immutable `capsule.lock.json`.
3. Verify signatures, hashes, schema versions, trust level, OS/backend compatibility, and policy version.
4. Verify that every non-core node is already resolved to a pinned package/source in the lock.
5. Verify dependencies are locked to allowed artifacts according to policy.
6. Compute dependency environment, runner, and capsule fingerprints.
7. Reuse an existing ready dependency env or create a staged dependency env.
8. Reuse an existing ready runner workspace or create a staged runner workspace.
9. Download/cache custom node source at pinned refs.
10. Install dependencies into the staged dependency env from the lock.
11. Materialize only the required custom nodes into the staged runner workspace.
12. Download required models into the shared model store and verify hashes.
13. Create or update the runner model view.
14. Run import/smoke tests in a runner process using the staged env/workspace.
15. Atomically mark env, runner workspace, and capsule install state ready.
16. Delete the transaction directory.

Recommended resolver pipeline for unresolved community workflows:

1. Parse the workflow package and graph in the trusted backend.
2. Query a trusted core ComfyUI runtime or static core-node manifest for built-in node types.
3. Determine non-core node types.
4. Resolve non-core nodes through layered metadata:
   - explicit workflow metadata
   - ComfyUI Registry/package IDs when available
   - Noofy-maintained node-type-to-package registry
   - signed/verified package manifests
5. If any node is unresolved, mark install unsupported and stop without mutating existing envs.
6. Resolve package source, pinned version/commit, dependency manifests, and trust level.
7. Produce a proposed lock only if policy allows every artifact.
8. Continue through the pre-resolved workflow path only after a policy-approved lock exists.

The preferred product path is to do most dependency resolution before the package reaches the user's machine, through Noofy creator/exporter tooling or verified registry infrastructure. The desktop client should primarily verify, materialize, and smoke-test locks. This reduces install-time solver failures for beginners.

Failure behavior:

- Never mutate the trusted core runtime.
- Never mutate an existing ready env in place.
- Never mutate an existing ready runner workspace in place.
- Failed staged envs stay in the transaction/quarantine area or are deleted.
- User-facing state says the workflow cannot be prepared automatically.
- Technical details go to diagnostics.

## Custom Node Resolution

Noofy should not rely only on raw workflow JSON. Raw workflow graphs identify node class names but usually do not prove which repository or package should provide them.

Resolution strategy:

1. Maintain a core-node manifest for the pinned ComfyUI version.
2. Prefer explicit custom-node metadata exported by a Noofy creator/exporter.
3. Use ComfyUI Registry metadata when package IDs and versions are available.
4. Maintain a Noofy registry mapping `node_type -> package_id -> source -> pinned refs -> dependency policy`.
5. Require pinned refs for blind install.
6. Treat unknown nodes as unsupported, not as "try pip and hope".

Noofy should not run arbitrary custom node install scripts for blind installs. If a custom node needs special setup, it needs a Noofy package manifest that declares the setup in a policy-controlled way.

The trusted backend may inspect files as data, but it must not import custom node modules or execute their setup code. Import checks happen inside a staged runner process so a crash or malicious import does not compromise the supervisor process.

## Trust Model

Recommended trust levels:

### Noofy Verified

- Signed or approved by Noofy.
- Exact custom node versions pinned.
- Dependencies locked with hashes.
- Model hashes and sources verified.
- Smoke-tested on supported runtime profiles.
- Blind install allowed.

### Registry Locked

- Community workflow.
- All custom nodes resolved through known registry metadata.
- Package refs and dependencies are pinned in a lock.
- Hash and policy checks pass for every install artifact.
- No unsupported install behavior.
- One-click install is allowed only if product policy permits this trust level on the current device/backend.

### Quarantined Community

- Technically isolatable, but not fully trusted.
- Requires explicit user opt-in to community/unverified workflows.
- Must never affect core runtime or existing ready envs.
- Should show clear trust language without implying safety.

### Unsupported

- Unknown nodes.
- Unpinned repositories.
- Unsafe or arbitrary install scripts.
- Native builds required where no allowed wheel exists.
- Unsupported OS/GPU backend.
- Missing or unverified model sources.
- Automatic install not allowed.

Suggested user-facing language:

- "This workflow is not compatible with one-click install."
- "Noofy could not verify the components required by this workflow."
- "This workflow needs components that are not supported on your device yet."

Avoid default UI language such as `pip`, `venv`, `site-packages`, stack trace, or raw node import errors.

## Security Considerations

Be precise: venv isolation is dependency isolation, not a security sandbox.

It helps prevent:

- dependency upgrades breaking other workflows
- broken custom nodes breaking the trusted core runtime
- package conflicts between workflows
- global environment pollution

It does not prevent malicious Python code from:

- reading files the process can access
- making network requests
- consuming CPU/GPU
- deleting or modifying user-accessible files
- exfiltrating prompts or local data if network is available

Product security should focus first on supply-chain control:

- signed/verified package metadata
- pinned commits and hashes
- hash-locked wheels and model downloads
- no arbitrary install scripts for blind install
- deny sdists/native builds by default for one-click installs
- clear trust badges and opt-in gates for unverified workflows
- separate frontend/backend API tokens from any runner-scoped secrets
- no community code imports in the trusted backend process

Future sandboxing to evaluate:

- macOS App Sandbox and hardened runtime constraints
- Windows AppContainer or restricted child-process tokens
- network restrictions for runner processes
- per-runner filesystem allowlists
- quarantined unverified workflow mode

These are non-trivial with Python, GPU libraries, and ComfyUI. Do not market them until proven.

## Hardware Compatibility Metadata

Do not call observed usage a guaranteed minimum requirement.

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

UI language should be probabilistic:

- "This workflow was tested with 8 GB VRAM."
- "Your device has 6 GB VRAM."
- "It may run slowly or fail."

Runtime checks should account for resolution, batch size, model, precision, backend, driver, OS, memory fragmentation, and other running apps.

## Failed Install Rollback

Install should be transactional:

- Write all files into `runtime-store/transactions/install-<id>`.
- Never modify an existing ready env in place.
- Never modify an existing ready runner workspace in place.
- Build new envs and runner workspaces under staging paths.
- Verify dependency import and ComfyUI startup before marking ready.
- Atomically move the env, runner workspace, and install-state metadata into final location.
- On failure, delete staging or move it to quarantine for diagnostics.
- Record structured diagnostics with a safe summary and technical details.

## Garbage Collection

Add metadata to every dependency env, runner workspace, capsule, model, custom node source, and wheel cache entry:

- created_at
- last_used_at
- referenced_by
- size_bytes
- status
- trust level

GC rules:

- Never delete active runner envs or runner workspaces.
- Never delete models referenced by installed ready capsules unless the user explicitly removes them.
- Delete failed transaction directories after a retention window.
- Remove dependency envs and runner workspaces whose fingerprints are no longer referenced by any installed workflow.
- Wheel/custom-node caches can be LRU with a size cap.
- Model blobs should be reference-counted or protected by installed workflow manifests.

## Major Risks

- Maintaining a reliable node-type-to-package registry is ongoing product work.
- Many custom nodes have incomplete metadata, unpinned dependencies, or arbitrary setup scripts.
- Python package resolution for Torch/CUDA/Metal stacks is difficult and changes over time.
- Native wheels may be unavailable for some OS/Python/GPU combinations.
- Windows filesystem linking behavior can complicate model deduplication.
- macOS and Windows app signing/notarization can be affected by bundled interpreters and downloaded executable code.
- Untrusted Python code remains dangerous even in a separate venv.
- Multiple warm ComfyUI runners can exhaust VRAM quickly.
- Install times and model downloads may be long; UX must expose progress clearly.
- Reliable process-tree cleanup is OS-specific and must be tested on macOS and Windows.
- If registry locks are generated too late on the user's machine, installs may fail in ways that feel technical and unpredictable.
- GPLv3 distribution obligations for ComfyUI need legal/product review when bundling.

## Answers To Review Questions

### How is the backend currently launched by Tauri?

Tauri starts a child process running `python -m app --port 0`, passes `NOOFY_API_TOKEN`, reads the printed API URL, and injects runtime config into the frontend. It kills that backend process on app close. Product builds need stronger process-tree cleanup so backend-owned runner processes are also terminated.

### Does product code currently depend on system Python?

Yes. Development currently falls back to `backend/.venv`, Homebrew Python, or `python3`. That is acceptable for development and not acceptable for product.

### Where should a supervisor layer live?

Use a hybrid supervisor. Tauri/Rust owns top-level process lifecycle. Python backend owns workflow/runtime resolution and ComfyUI runner orchestration.

### Should the supervisor be Rust, Python, or hybrid?

Hybrid. Rust should stay small and reliable. Python should own workflow-specific engine logic. Long term, Rust can gain stronger child-process cleanup and packaged resource discovery, but not custom-node resolution.

### What docs need updates?

At minimum:

- `docs/ARCHITECTURE.md`: add runner/capsule/runtime-store architecture.
- `docs/WORKFLOW_PACKAGES.md`: distinguish workflow package from workflow capsule.
- `docs/MANAGED_COMFYUI_SIDECAR.md`: narrow current sidecar to trusted core/default-node workflows and describe future runner environments.
- `docs/ENGINE_CONTRACT.md`: describe runner selection before adapter execution.
- `docs/FEEDBACK_TESTING_MONITORING.md`: add install resolver diagnostics and rollback states.

This review document can be the source for those updates.

### What current assumptions conflict with this architecture?

- One `RuntimeEnvironment.venv_dir` for ComfyUI.
- One `RuntimeManager` and one ComfyUI process.
- One singleton `ComfyUIEngineAdapter` endpoint.
- No `job_id -> runner_id` routing layer.
- Workflow validation only checks structure and model folder/name.
- User workflow packages can override bundled packages by ID without trust metadata.
- Model identity is folder/name, not content hash.
- Tauri launches a development Python instead of a packaged Noofy runtime.
- Tauri currently kills the direct backend child process, not a full backend/runner process tree.

### What is the safest incremental path?

1. Keep Milestone 1 on trusted built-in/default-node workflows.
2. Document the capsule/runtime-store architecture.
3. Add path and manifest types without installing community dependencies.
4. Add content-hash model store for verified built-in workflows.
5. Add a `RunnerSupervisor` abstraction while it still returns the existing core runner.
6. Add immutable capsule lock schema and separate local install-state schema.
7. Add job-to-runner routing while it still routes to the existing core runner.
8. Implement resolver/materializer for Noofy Verified packages only.
9. Add layered dependency-env, runner, and capsule fingerprints.
10. Add process switching.
11. Add registry-locked custom nodes after transaction/rollback is proven.

### What should be implemented now vs documented?

Implement now:

- Documentation and contracts.
- Runtime/capsule path constants.
- Manifest schemas with tests.
- Immutable lock and mutable install-state models.
- RunnerSupervisor interface returning the existing core runner.
- Job registry shape, even if it only tracks the current core runner.
- Package namespace/trust-precedence rules for workflow loading.
- Product-grade process-tree shutdown design in Tauri.

Document only for now:

- Full custom-node registry.
- Marketplace trust/signing.
- Unverified workflow opt-in.
- OS-level sandboxing.
- Multi-runner warm pool.

### How should runtime paths work on macOS and Windows?

Writable runtime state belongs under `NoofyPaths.data_dir`, not inside the app bundle or Program Files. Packaged immutable resources live with the app. Prepared envs, caches, models, logs, workflow capsules, and outputs live under the platform app-data directory.

### What package/environment tool should be used?

Prefer signed Noofy-managed CPython plus `uv` for product runtime/env creation and lock-based installs. For v1, ship the managed Python/core runtime where practical so starter workflows do not depend on first-run solving. Use pip only as a compatibility fallback. Avoid Conda as the default. PyInstaller can package the backend, but it does not solve ComfyUI/custom-node runner environments.

### How should custom node metadata be resolved?

Layered resolution: explicit Noofy metadata, ComfyUI Registry metadata, Noofy-maintained node registry, then unsupported. Unknown nodes must not trigger blind source installs. The trusted backend may inspect metadata and files as data, but custom node imports must happen only inside staged runner processes.

### How should failed installs be rolled back?

Use install transactions plus staged dependency envs and runner workspaces. Existing ready envs and runner workspaces are immutable. Mark ready only after import/smoke tests pass in a staged runner. Delete or quarantine failed staging directories.

### How should model storage avoid duplication?

Store model blobs by SHA-256 and materialize runner-visible model views through hardlinks, symlinks, or shared ComfyUI model path configuration. Track references from capsules.

### How should multiple workflow environments be garbage-collected?

GC by reference metadata and last-used time. Delete unreferenced dependency envs, runner workspaces, and failed transactions. Keep referenced models. Apply LRU size caps to wheel/source caches.

### What are the major risks?

Supply-chain trust, native dependency resolution, GPU backend compatibility, package registry maintenance, app signing constraints, long installs, and the fact that venvs are not malicious-code sandboxes.

## Step-By-Step Implementation Plan

### Phase 0: Decision And Boundaries

- Accept that community workflows never mutate core runtime.
- Treat Tauri and the Noofy backend as the trusted control plane.
- Treat ComfyUI runners as the data plane where custom node code executes.
- State that the backend must not import community custom node code.
- Keep current Tauri/backend handoff.
- Add this architecture to the docs index.
- Add a product Python packaging decision record that favors shipped Noofy-managed CPython/core runtime for v1.
- Add a process-tree shutdown decision record for macOS and Windows.

### Phase 1: Runtime Store Foundation

- Add path fields for runtime store, dependency envs, runner workspaces, transactions, model store, wheel cache, custom node cache, and workflow store.
- Add Pydantic models for dependency-env manifests, runner manifests, immutable capsule lock files, mutable install state, and trust level.
- Add package namespace/trust-precedence rules so user imports cannot silently replace verified built-ins.
- Add tests for path resolution and manifest validation.
- Do not install custom nodes yet.

### Phase 2: Runner Supervisor Abstraction

- Introduce `RunnerSupervisor`.
- It initially returns the existing core ComfyUI runner.
- Refactor `EngineService` to request a runner for a workflow instead of using one singleton adapter endpoint directly.
- Add a `job_id -> runner_id` registry shape and route progress/result/cancel through it, even while only one runner exists.
- Preserve all current Milestone 1 behavior.

### Phase 3: Verified Core Workflow Install Path

- Add model store records with hashes.
- Support verified built-in workflow capsules with no custom nodes.
- Add install status and user-friendly progress states.
- Add rollback for model download failures.

### Phase 4: Layered Fingerprints And Runner Workspaces

- Compute dependency-env, runner, and capsule fingerprints from lock metadata.
- Create dependency envs under `runtime-store/envs/dep-env-<fingerprint>`.
- Create runner workspaces under `runtime-store/runner-workspaces/runner-workspace-<fingerprint>`.
- Start runner processes from selected dependency env plus runner workspace.
- Switch runner endpoint per workflow.
- Add smoke tests for runner start/stop and adapter routing.

### Phase 5: Registry-Locked Custom Node Resolver

- Add core node manifest.
- Add Noofy node registry schema.
- Add registry-locked custom-node install for pinned, wheel-only dependencies.
- Reject unknown or unsafe packages with user-friendly unsupported status.

### Phase 6: Trust And Marketplace Readiness

- Add package signatures or signed registry metadata.
- Add verified/community trust UI.
- Add unverified workflow opt-in policy.
- Add GC and storage management UI.

## Final Recommendation

Proceed with the proposed isolation direction, with the refinements above. Do not build marketplace/community installs yet. First, reshape the runtime architecture so the current trusted core runner is just one runner managed by a future `RunnerSupervisor`; add immutable capsule locks, separate install state, layered fingerprints, job-to-runner routing, runtime-store paths, model hash storage, and transactional install scaffolding. Only after those foundations exist should Noofy support registry-locked custom-node workflows.
