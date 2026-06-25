# backend/app/runtime — Agent Map

Runtime mechanics: everything needed to prepare isolated execution environments and run workflow jobs on the machine. This package is intentionally isolated from product-level orchestration.

## Subpackages (Phase 4 complete)

| Package | Owns |
|---------|------|
| `comfyui/` | Managed ComfyUI sidecar, update/rebuild pipeline, launch settings |
| `runners/` | Runner process supervision, launch, coordination, memory probing |
| `dependencies/` | Custom nodes, dependency lock/resolver, accelerator/core-package policy, isolation data models |
| `memory/` | Memory governor, machine memory observer, resource monitor |
| `storage/` | Storage GC, workspace store, workspace preparer |
| `profiles/` | Runtime profile catalog, `profile_catalog.json` |
| `hardware/` | GPU backend detection, VRAM/RAM query |
| `models/` | Runner-visible model materialization and GC |

## Root files (no clear subdomain)

| File | Owns |
|------|------|
| `manager.py` | `RuntimeManager` — coordinates sidecar + runners |
| `capsule_installer.py` | Install community workflow capsules into isolated workspaces |
| `install_transactions.py` | Atomic install transaction orchestration |
| `install_state.py` | `InstallState` data model |
| `environment.py` | `CommandRunner`, `RuntimeEnvironment` — env variable assembly |
| `smoke_test.py` | Post-install workflow smoke test driver |
| `fingerprints.py` | Workspace/environment fingerprint computation |
| `node_registry.py` | Node type registry used for capsule preparation |
| `uv_executable.py` | Resolves the bundled `uv` binary path |

## Import Rule

Import runtime code from the owning subpackage (`runtime/comfyui`, `runtime/runners`, `runtime/dependencies`, `runtime/memory`, `runtime/storage`, `runtime/models`, `runtime/profiles`, or `runtime/hardware`). Do not add root-level runtime re-export helpers for unreleased internal paths.

## What it must NOT own

- Product-level workflow orchestration (belongs in `runs/`)
- App model inventory or downloads (belongs in `app/models/`)
- ComfyUI source code (lives in `third_party/comfyui/`)
- Community custom-node imports executed in the trusted process — community code is always isolated
- Workflow package parsing or dashboard authoring (belongs in `workflows/`)

## Trust boundary — critical

The trusted backend process must never import or execute community custom-node Python code. `capsule_installer.py` installs capsules into isolated workspaces; custom-node imports, compatibility checks, and smoke tests run only inside isolated runner processes.

See [docs/RUNTIME_ISOLATION_ARCHITECTURE.md](../../../docs/RUNTIME_ISOLATION_ARCHITECTURE.md).

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests/test_runtime_manager.py
backend/.venv/bin/python -m pytest backend/tests/test_runner_coordinator.py
backend/.venv/bin/python -m pytest backend/tests/test_runner_process.py
backend/.venv/bin/python -m pytest backend/tests/test_runtime_isolation.py
backend/.venv/bin/python -m pytest backend/tests/test_capsule_installer.py
backend/.venv/bin/python -m pytest backend/tests/test_memory_governor.py
```
