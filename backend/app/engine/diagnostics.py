from collections import deque
from datetime import UTC, datetime
from typing import Any, Protocol

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


class LogStore:
    def __init__(self, max_events: int = 1000) -> None:
        self._events: deque[DiagnosticEvent] = deque(maxlen=max_events)
        self._next_id = 1

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
        event = DiagnosticEvent(
            id=self._next_id,
            timestamp=datetime.now(UTC),
            level=level,
            message=message,
            source=source,
            job_id=job_id,
            workflow_id=workflow_id,
            details=details or {},
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
        events = list(self._events)
        if job_id is not None:
            events = [event for event in events if event.job_id == job_id]
        if level is not None:
            events = [event for event in events if event.level == level]
        return DiagnosticLogResponse(events=events[-limit:])

    def latest_error(self) -> DiagnosticEvent | None:
        for event in reversed(self._events):
            if event.level == "error":
                return event
        return None
