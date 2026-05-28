from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class UserStateLayoutOverride(BaseModel):
    x: int
    y: int
    w: int
    h: int


class UserStateActionBarPosition(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class UserStatePresentationOverrides(BaseModel):
    action_bar: UserStateActionBarPosition | None = None


class OutputPreference(BaseModel):
    auto_save: bool = False


class WorkflowUserState(BaseModel):
    schema_version: str = "1"
    workflow_id: str
    dashboard_version: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    layout_overrides: dict[str, UserStateLayoutOverride] = Field(default_factory=dict)
    presentation_overrides: UserStatePresentationOverrides = Field(
        default_factory=UserStatePresentationOverrides
    )
    output_preferences: dict[str, OutputPreference] = Field(default_factory=dict)


class UserStateService:
    def __init__(self, user_state_dir: Path) -> None:
        self._dir = user_state_dir

    def _path(self, workflow_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in workflow_id)
        return self._dir / f"{safe}.json"

    def get(self, workflow_id: str) -> WorkflowUserState:
        path = self._path(workflow_id)
        if not path.exists():
            return WorkflowUserState(workflow_id=workflow_id)
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return WorkflowUserState.model_validate(data)
        except Exception:
            return WorkflowUserState(workflow_id=workflow_id)

    def save(
        self,
        state: WorkflowUserState,
        *,
        credential_input_ids: set[str] | None = None,
    ) -> WorkflowUserState:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(state.workflow_id)
        safe_state = state.model_copy(
            update={"values": _safe_values(state.values, credential_input_ids or set())}
        )
        _atomic_write_json(path, safe_state.model_dump())
        return safe_state

    def clear_values(self, workflow_id: str) -> WorkflowUserState:
        state = self.get(workflow_id)
        cleared = state.model_copy(update={"values": {}})
        return self.save(cleared)

    def clear_layout(self, workflow_id: str) -> WorkflowUserState:
        state = self.get(workflow_id)
        cleared = state.model_copy(
            update={
                "layout_overrides": {},
                "presentation_overrides": UserStatePresentationOverrides(),
            }
        )
        return self.save(cleared)

    def delete(self, workflow_id: str) -> None:
        try:
            self._path(workflow_id).unlink()
        except FileNotFoundError:
            return


def _atomic_write_json(target: Path, data: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _safe_values(values: dict[str, Any], credential_input_ids: set[str]) -> dict[str, Any]:
    safe_values: dict[str, Any] = {}
    for key, value in values.items():
        if key in credential_input_ids or (
            isinstance(value, dict) and value.get("kind") == "api_key_ref"
        ):
            safe_credential = _safe_credential_value(value)
            if safe_credential is not None:
                safe_values[key] = safe_credential
            continue
        safe_values[key] = value
    return safe_values


def _safe_credential_value(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("kind") != "api_key_ref":
        return None
    safe: dict[str, Any] = {}
    for key in ("kind", "provider", "secret_ref", "configured", "last_four"):
        item = value.get(key)
        if item is not None and isinstance(item, (str, bool)):
            safe[key] = item
    return safe
