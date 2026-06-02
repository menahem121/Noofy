# runtime/memory — Agent Map

Memory governance: VRAM/RAM estimates, eviction decisions, machine snapshots, learning store.

## What this package owns

| File | Owns |
|------|------|
| `memory_governor.py` | `MemoryGovernorDecision`, memory governor logic, adaptive release polling, learning store |
| `service.py` | `MemoryGovernorService` — admission, cleanup coordination, release confirmation, retry policy, sampling coordination |
| `system_memory.py` | `MachineMemoryObserver`, hardware memory detection |
| `resource_monitor.py` | `SystemResourceObserver`, CPU/memory metrics |

## What it must NOT own

- Runner process lifecycle (belongs in `runners/`)
- Workflow-run queue ownership, aliases, watchers, or terminal finalization (belongs in `runs/`)
