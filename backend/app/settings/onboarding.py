from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.diagnostics import DiagnosticsSink

ONBOARDING_SETTINGS_SCHEMA_VERSION = "1"


class OnboardingState(BaseModel):
    schema_version: str = ONBOARDING_SETTINGS_SCHEMA_VERSION
    completed: bool = False
    completed_at: str | None = None


class OnboardingUpdateResult(BaseModel):
    status: Literal["completed", "already_completed"]
    onboarding: OnboardingState


class OnboardingSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> OnboardingState:
        if not self.path.exists():
            return OnboardingState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return OnboardingState()
        if not isinstance(raw, dict):
            return OnboardingState()
        try:
            parsed = OnboardingState.model_validate(raw)
        except Exception:
            return OnboardingState()
        if parsed.schema_version != ONBOARDING_SETTINGS_SCHEMA_VERSION:
            return OnboardingState()
        return parsed

    def write(self, state: OnboardingState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(
            json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)


class OnboardingSettingsService:
    def __init__(
        self,
        *,
        store: OnboardingSettingsStore,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.store = store
        self.log_store = log_store

    def state(self) -> OnboardingState:
        return self.store.read()

    def mark_complete(self) -> OnboardingUpdateResult:
        current = self.store.read()
        if current.completed:
            return OnboardingUpdateResult(status="already_completed", onboarding=current)

        completed = OnboardingState(
            completed=True,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.store.write(completed)
        if self.log_store is not None:
            self.log_store.add(
                "info",
                "First-launch onboarding marked complete",
                "settings.onboarding",
                details={"completed_at": completed.completed_at},
            )
        return OnboardingUpdateResult(status="completed", onboarding=completed)
