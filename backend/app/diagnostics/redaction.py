from __future__ import annotations

import re
from typing import Any


class SecretRedactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register_secret(self, secret: str | None) -> None:
        if not secret:
            return
        value = str(secret)
        if len(value) < 4:
            return
        self._secrets.add(value)

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[redacted]" if _is_sensitive_key(str(key)) else self.sanitize(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.sanitize(item) for item in value)
        if isinstance(value, str):
            return self.sanitize_text(value)
        return value

    def sanitize_text(self, value: str) -> str:
        redacted = value
        for secret in sorted(self._secrets, key=len, reverse=True):
            redacted = redacted.replace(secret, "[redacted]")
        redacted = re.sub(
            r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
            r"\1[redacted]",
            redacted,
        )
        redacted = re.sub(
            r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|auth|authorization|secret|password)=)[^&#\s]+",
            r"\1[redacted]",
            redacted,
        )
        redacted = re.sub(
            r"(?i)\b(api[-_]?key|access[-_]?token|token|auth|authorization|secret|password)=\S+",
            r"\1=[redacted]",
            redacted,
        )
        redacted = re.sub(
            r'(?i)("(?:api[-_]?key|access[-_]?token|token|auth|authorization|secret|password)"\s*:\s*")[^"]+(")',
            r"\1[redacted]\2",
            redacted,
        )
        return redacted


global_redactor = SecretRedactor()


def register_secret(secret: str | None) -> None:
    global_redactor.register_secret(secret)


def sanitize(value: Any) -> Any:
    return global_redactor.sanitize(value)


def sanitize_text(value: str) -> str:
    return global_redactor.sanitize_text(value)


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", key.casefold())
    return any(
        needle in normalized
        for needle in (
            "apikey",
            "accesstoken",
            "authorization",
            "bearertoken",
            "secret",
            "password",
            "token",
            "signedurl",
        )
    )
