# runtime/memory — Agent Map

Memory governance: VRAM/RAM estimates, eviction decisions, machine snapshots, learning store.

## What this package owns

| File | Owns |
|------|------|
| `memory_governor.py` | `MemoryGovernorDecision`, memory governor logic, learning store |
| `system_memory.py` | `MachineMemoryObserver`, hardware memory detection |
| `resource_monitor.py` | `SystemResourceObserver`, CPU/memory metrics |

## What it must NOT own

- Runner process lifecycle (belongs in `runners/`)
- Workflow job retry orchestration (belongs in `engine/service.py` pending extraction)
