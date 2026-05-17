from __future__ import annotations

import logging


class SuppressResourceMonitorAccessLogFilter(logging.Filter):
    """Keep high-frequency frontend polling out of uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            path = str(args[2])
            if _is_suppressed_polling_path(path):
                return False
        try:
            request_line = str(args[1])
        except IndexError:
            request_line = record.getMessage()
        return not _is_suppressed_polling_request_line(request_line)


def _is_suppressed_polling_path(path: str) -> bool:
    if path == "/api/resources" or path.startswith("/api/resources?"):
        return True
    if path.startswith("/api/workflows/import/") and path.endswith("/model-verification"):
        return True
    return path.startswith("/api/workflows/import/") and "/model-verification?" in path


def _is_suppressed_polling_request_line(request_line: str) -> bool:
    if " /api/resources " in request_line:
        return True
    return (
        " /api/workflows/import/" in request_line
        and "/model-verification " in request_line
    )
