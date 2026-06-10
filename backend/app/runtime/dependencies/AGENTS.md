# runtime/dependencies — Agent Map

Isolated dependency environments for community workflows: custom nodes, lock files, resolver.

## What this package owns

| File | Owns |
|------|------|
| `accelerator_policy.py` | Which accelerator/core packages custom nodes may not install in the stable runtime |
| `custom_nodes.py` | Custom node registry, node compatibility checks (`core_node_manifest.json`) |
| `dependency_env.py` | Dependency environment setup and activation |
| `dependency_lock.py` | `ResolvedDependencyLock` data model and hashing |
| `dependency_lock_store.py` | Persisting resolved dependency locks |
| `dependency_resolver.py` | Resolving custom node package requirements |
| `isolation.py` | `CapsuleLock` and isolation data models |

## Data files

- `core_node_manifest.json` — shipped manifest of built-in ComfyUI nodes

## What it must NOT own

- Runner process launch (belongs in `runners/`)
- Capsule installation orchestration (belongs in `runtime/capsule_installer.py`)
