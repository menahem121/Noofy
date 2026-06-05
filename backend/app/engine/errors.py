from __future__ import annotations

from app.engine.models import RunUserFixableError


class EngineUserFixableValidationError(ValueError):
    def __init__(self, user_error: RunUserFixableError) -> None:
        super().__init__(user_error.message)
        self.user_error = user_error

