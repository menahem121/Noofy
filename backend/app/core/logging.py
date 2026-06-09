from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlsplit

from app.diagnostics import sanitize_text


SENSITIVE_QUERY_RE = re.compile(
    r"(?i)(?:[?&]|\s)(?:api[-_]?key|access[-_]?token|token|auth|authorization|secret|password)="
)


class SuppressResourceMonitorAccessLogFilter(logging.Filter):
    """Keep routine API traffic and unsafe query secrets out of uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            path = _normalized_path(str(args[2]))
            if _contains_sensitive_query_value(path):
                return False
            if _verbose_access_logs_enabled():
                _sanitize_record_args(record)
                return True
            status_code = _status_code_from_args(args)
            if _is_successful_api_request(path, status_code):
                return False
            if status_code is None and _is_suppressed_polling_path(path):
                return False
        try:
            request_line = str(args[1])
        except IndexError:
            request_line = record.getMessage()
        if _contains_sensitive_query_value(request_line):
            return False
        if _verbose_access_logs_enabled():
            _sanitize_record_args(record)
            return True
        status_code = _status_code_from_args(args)
        path = _path_from_request_line(request_line)
        if path is not None and _is_successful_api_request(path, status_code):
            return False
        _sanitize_record_args(record)
        return not _is_suppressed_polling_request_line(request_line)


def _contains_sensitive_query_value(text: str) -> bool:
    return bool(SENSITIVE_QUERY_RE.search(text))


def _verbose_access_logs_enabled() -> bool:
    return os.environ.get("NOOFY_ACCESS_LOGS", "").strip().casefold() == "all"


def _status_code_from_args(args: tuple[Any, ...]) -> int | None:
    for value in reversed(args):
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _is_successful_api_request(path: str, status_code: int | None) -> bool:
    return status_code is not None and status_code < 400 and _is_api_path(path)


def _is_api_path(path: str) -> bool:
    normalized = _normalized_path(path)
    return normalized == "/api" or normalized.startswith("/api/")


REQUEST_LINE_RE = re.compile(r"\s(?P<path>\S+)\sHTTP/\d+(?:\.\d+)?")


def _path_from_request_line(request_line: str) -> str | None:
    match = REQUEST_LINE_RE.search(f" {request_line}")
    if match is None:
        return None
    return _normalized_path(match.group("path"))


def _normalized_path(path: str) -> str:
    parsed = urlsplit(path)
    normalized = parsed.path or path
    if parsed.query:
        return f"{normalized}?{parsed.query}"
    return normalized


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
