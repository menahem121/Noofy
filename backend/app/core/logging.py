from __future__ import annotations

import logging


class SuppressResourceMonitorAccessLogFilter(logging.Filter):
    """Keep high-frequency resource monitor polling out of uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            path = str(args[2])
            if path == "/api/resources" or path.startswith("/api/resources?"):
                return False
        try:
            request_line = str(args[1])
        except IndexError:
            request_line = record.getMessage()
        return " /api/resources " not in request_line
