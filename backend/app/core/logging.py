from __future__ import annotations

import logging
import re

from app.diagnostics import sanitize_text


SENSITIVE_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|\s)(?:api[-_]?key|access[-_]?token|token|auth|authorization|secret|password)="
)


class SuppressResourceMonitorAccessLogFilter(logging.Filter):
    """Keep noisy polling and unsafe query secrets out of uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            path = str(args[2])
            if _contains_sensitive_query_value(path):
                return False
            if _is_suppressed_polling_path(path):
                return False
        try:
            request_line = str(args[1])
        except IndexError:
            request_line = record.getMessage()
        if _contains_sensitive_query_value(request_line):
            return False
        _sanitize_record_args(record)
        return not _is_suppressed_polling_request_line(request_line)


def _contains_sensitive_query_value(text: str) -> bool:
    return bool(SENSITIVE_QUERY_RE.search(text))


def _is_suppressed_polling_path(path: str) -> bool:
    if path == "/api/runtime" or path.startswith("/api/runtime?"):
        return True
    if path == "/api/resources" or path.startswith("/api/resources?"):
        return True
    if path.startswith("/api/workflows/import/") and path.endswith("/model-verification"):
        return True
    return path.startswith("/api/workflows/import/") and "/model-verification?" in path


def _is_suppressed_polling_request_line(request_line: str) -> bool:
    if " /api/runtime " in request_line:
        return True
    if " /api/resources " in request_line:
        return True
    return (
        " /api/workflows/import/" in request_line
        and "/model-verification " in request_line
    )


def _sanitize_record_args(record: logging.LogRecord) -> None:
    if isinstance(record.args, tuple):
        record.args = tuple(
            sanitize_text(str(arg)) if isinstance(arg, str) else arg for arg in record.args
        )
    elif isinstance(record.args, dict):
        record.args = {
            key: sanitize_text(str(value)) if isinstance(value, str) else value
            for key, value in record.args.items()
        }
