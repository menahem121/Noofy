# runtime/profiles — Agent Map

Runtime profile catalog: hardware/OS profile selection for community workflow isolation.

## What this package owns

| File | Owns |
|------|------|
| `profiles.py` | `RuntimeProfileCatalog`, profile loading and matching |

## Data files

- `profile_catalog.json` — shipped profile definitions

## What it must NOT own

- Memory governor (belongs in `memory/`)
- Dependency environment setup (belongs in `dependencies/`)
