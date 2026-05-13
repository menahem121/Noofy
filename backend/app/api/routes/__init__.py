"""Route package with a temporary lazy export for the assembled router.

Remove the module-level ``router`` export when internal callers import from
``app.api.router`` directly.
"""

from typing import Any

__all__ = ["router"]


def __getattr__(name: str) -> Any:
    if name == "router":
        from app.api.router import router

        return router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
