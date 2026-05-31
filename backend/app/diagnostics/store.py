import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any, Protocol

from app.diagnostics.redaction import sanitize, sanitize_text
from app.engine.models import DiagnosticEvent, DiagnosticLogResponse, LogLevel


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
