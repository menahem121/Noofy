"""
Global backend diagnostics infrastructure.

All subsystems (runtime, install, memory, models, sidecar, runners, storage GC,
workflow execution, health) share one diagnostics store injected from composition.
Subsystems receive a DiagnosticsSink; only the orchestration layer holds
DiagnosticsStore to read events back out.

Public API:
  DiagnosticsSink    — write-only protocol injected into subsystems
  DiagnosticsReader  — read-only protocol for query endpoints
  DiagnosticsStore   — combined sink + reader (held by composition root)
  LogStore           — concrete in-memory implementation
"""

from app.diagnostics.redaction import register_secret, sanitize, sanitize_text
from app.diagnostics.store import DiagnosticsReader, DiagnosticsSink, DiagnosticsStore, LogStore

__all__ = [
    "DiagnosticsReader",
    "DiagnosticsSink",
    "DiagnosticsStore",
    "LogStore",
    "register_secret",
    "sanitize",
    "sanitize_text",
]
