# backend/app/diagnostics — Agent Map

Global backend diagnostics infrastructure. All backend subsystems share one `DiagnosticsStore` instance injected from `composition.py`. Subsystems receive a `DiagnosticsSink` (write-only); the orchestration layer holds `DiagnosticsStore` (read + write) to serve API endpoints.

## What this package owns

| File | Owns |
|------|------|
| `store.py` | `DiagnosticsSink` protocol (write), `DiagnosticsReader` protocol (read), `DiagnosticsStore` combined protocol, `LogStore` concrete in-memory implementation |

## What it must NOT own

- Route handlers (those belong in `api/routes/diagnostics.py`)
- Per-subsystem private stores — every subsystem must receive an injected sink
- Data model definitions for `DiagnosticEvent`, `DiagnosticLogResponse`, `LogLevel` — those live in `engine/models.py` for now (Phase 3/5 migration target)

## Import Rule

Import diagnostics protocols and stores from `app.diagnostics`. Do not add engine-level diagnostics re-export helpers for unreleased internal paths.

## Invariant

The diagnostics store is a single shared instance. Runtime startup, import, install, models, memory, sidecar, runners, storage GC, health, and workflow execution all write to the same store. Never create private per-subsystem `LogStore` instances in production code.

## Tests

```bash
backend/.venv/bin/python -m pytest backend/tests/test_diagnostics.py
```
