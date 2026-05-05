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


class WorkflowUserState(BaseModel):
    schema_version: str = "1"
    workflow_id: str
    dashboard_version: str = ""
    values: dict[str, Any] = Field(default_factory=dict)
    layout_overrides: dict[str, UserStateLayoutOverride] = Field(default_factory=dict)


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

    def save(self, state: WorkflowUserState) -> WorkflowUserState:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(state.workflow_id)
        _atomic_write_json(path, state.model_dump())
        return state

    def clear_values(self, workflow_id: str) -> WorkflowUserState:
        state = self.get(workflow_id)
        cleared = state.model_copy(update={"values": {}})
        return self.save(cleared)

    def clear_layout(self, workflow_id: str) -> WorkflowUserState:
        state = self.get(workflow_id)
        cleared = state.model_copy(update={"layout_overrides": {}})
        return self.save(cleared)


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
