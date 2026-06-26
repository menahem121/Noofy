import logging
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any, Protocol

from app.diagnostics.redaction import sanitize, sanitize_text
from app.engine.models import DiagnosticEvent, DiagnosticLogResponse, LogLevel

EVENT_LOGGER_NAME = "noofy.events"
INFO_EVENT_SOURCE_PREFIXES = (
    "app.",
    "runs.",
    "runtime.",
    "memory_governor",
    "model.",
    "model_sources.",
    "models.",
    "workflow.import",
    "workflow.models",
    "workflow.runtime",
    "workflows.import",
    "gallery.capture",
)
NOISY_INFO_MESSAGES = {
    "Gallery save progress",
}
NOISY_CONSOLE_EVENTS = {
    (
        "runtime.storage_gc",
        "Stale runtime artifacts detected for installed workflow",
    ),
}
CONSOLE_DETAIL_KEYS = (
    "runner_id",
    "queue_id",
    "status",
    "action",
    "reason",
    "reason_code",
    "memory_decision_id",
    "control_id",
    "item_count",
    "downloaded_count",
    "failed_count",
    "model_count",
    "max_parallel_downloads",
    "required_bytes",
    "error_type",
    "provider",
    "step",
    "source_host",
    "source_index",
    "source_count",
    "attempt",
    "max_attempts",
    "delay_seconds",
    "status_code",
    "error",
    "candidate_count",
    "reliable_candidate_count",
    "search_term_count",
    "candidate_repo_count",
    "inspected_repo_count",
    "metadata_error_repo_count",
    "size_bytes",
    "duration_seconds",
    "average_bytes_per_second",
)
WORKFLOW_MODEL_CONSOLE_DETAIL_KEYS = (
    "folder",
    "filename",
)


class DiagnosticsSink(Protocol):
    def add(
        self,
        level: LogLevel,
        message: str,
        source: str,
        *,
        job_id: str | None = None,
        workflow_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> DiagnosticEvent:
        """Record a structured diagnostic event."""


class DiagnosticsReader(Protocol):
    def list_events(
        self,
        *,
        job_id: str | None = None,
        level: LogLevel | None = None,
        limit: int = 200,
    ) -> DiagnosticLogResponse:
        """Return recent diagnostic events."""

    def latest_error(self) -> DiagnosticEvent | None:
        """Return the latest error event, if one exists."""


class DiagnosticsStore(DiagnosticsSink, DiagnosticsReader, Protocol):
    """Record and query structured diagnostic events."""


class LogStore:
    def __init__(self, max_events: int = 1000) -> None:
        self._events: deque[DiagnosticEvent] = deque(maxlen=max_events)
        self._next_id = 1
        # Diagnostics are written from parallel verification worker threads (and read by
        # the API thread), so id assignment and deque access must be serialized.
        self._lock = threading.Lock()

    def add(
        self,
        level: LogLevel,
        message: str,
        source: str,
        *,
        job_id: str | None = None,
        workflow_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> DiagnosticEvent:
        sanitized_message = sanitize_text(message)
        sanitized_details = sanitize(details or {})
        timestamp = datetime.now(UTC)
        with self._lock:
            event = DiagnosticEvent(
                id=self._next_id,
                timestamp=timestamp,
                level=level,
                message=sanitized_message,
                source=source,
                job_id=job_id,
                workflow_id=workflow_id,
                details=sanitized_details,
            )
            self._next_id += 1
            self._events.append(event)
        _emit_console_event(event)
        return event

    def list_events(
        self,
        *,
        job_id: str | None = None,
        level: LogLevel | None = None,
        limit: int = 200,
    ) -> DiagnosticLogResponse:
        with self._lock:
            events = list(self._events)
        if job_id is not None:
            events = [event for event in events if event.job_id == job_id]
        if level is not None:
            events = [event for event in events if event.level == level]
        return DiagnosticLogResponse(events=[_sanitized_event(event) for event in events[-limit:]])

    def latest_error(self) -> DiagnosticEvent | None:
        with self._lock:
            events = list(self._events)
        for event in reversed(events):
            if event.level == "error":
                return _sanitized_event(event)
        return None


def _sanitized_event(event: DiagnosticEvent) -> DiagnosticEvent:
    return event.model_copy(
        update={
            "message": sanitize_text(event.message),
            "details": sanitize(event.details),
        }
    )


def _emit_console_event(event: DiagnosticEvent) -> None:
    if not _should_emit_console_event(event):
        return
    logging.getLogger(EVENT_LOGGER_NAME).log(
        _python_log_level(event.level),
        _format_console_event(event),
    )


def _should_emit_console_event(event: DiagnosticEvent) -> bool:
    if (event.source, event.message) in NOISY_CONSOLE_EVENTS:
        return False
    level = str(event.level)
    if level in {"warning", "error"}:
        return True
    if level != "info":
        return False
    if event.message in NOISY_INFO_MESSAGES:
        return False
    return any(event.source.startswith(prefix) for prefix in INFO_EVENT_SOURCE_PREFIXES)


def _python_log_level(level: LogLevel) -> int:
    if level == "error":
        return logging.ERROR
    if level == "warning":
        return logging.WARNING
    if level == "debug":
        return logging.DEBUG
    return logging.INFO


def _format_console_event(event: DiagnosticEvent) -> str:
    fields: list[str] = []
    if event.workflow_id:
        fields.append(f"workflow={_console_value(event.workflow_id)}")
    if event.job_id:
        fields.append(f"job={_console_value(event.job_id)}")
    detail_keys = CONSOLE_DETAIL_KEYS
    if event.source == "workflow.models":
        detail_keys = (*WORKFLOW_MODEL_CONSOLE_DETAIL_KEYS, *CONSOLE_DETAIL_KEYS)
    for key in detail_keys:
        if key not in event.details:
            continue
        value = event.details[key]
        if value is None or isinstance(value, dict):
            continue
        fields.append(f"{key}={_console_value(value)}")
    suffix = f" {' '.join(fields)}" if fields else ""
    return f"{event.level} {event.source}: {event.message}{suffix}"


def _console_value(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        text = ",".join(str(item) for item in value)
    else:
        text = str(value)
    text = sanitize_text(" ".join(text.split()))
    if len(text) > 160:
        text = f"{text[:157]}..."
    if not text:
        return '""'
    if any(character.isspace() for character in text) or "=" in text:
        return repr(text)
    return text
