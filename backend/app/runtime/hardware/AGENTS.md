# runtime/hardware — Agent Map

Hardware detection: GPU backend, VRAM, available devices.

## What this package owns

| File | Owns |
|------|------|
| `hardware.py` | GPU backend detection, VRAM query, device enumeration |

## What it must NOT own

- Memory governor logic (belongs in `memory/`)
- Runtime profile selection (belongs in `profiles/`)
