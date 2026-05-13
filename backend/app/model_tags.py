from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.model_inventory_schemas import ModelTag, ModelTagCreateRequest
from app.workflows.store_paths import safe_store_segment

MODEL_TAGS_SCHEMA_VERSION = "1"


class ModelTagStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def list_tags(self) -> list[ModelTag]:
        with self._lock:
            return self._read_unlocked()[0]

    def tag_ids_for_model(self, model_key: str) -> list[str]:
        with self._lock:
            tags, assignments = self._read_unlocked()
        valid_ids = {tag.id for tag in tags}
        return [tag_id for tag_id in assignments.get(model_key, []) if tag_id in valid_ids]

    def create_tag(self, request: ModelTagCreateRequest) -> ModelTag:
        name = request.name.strip()[:40]
        if not name:
            raise ValueError("Tag name is required.")
        tag = ModelTag(id=f"tag_{uuid.uuid4().hex[:12]}", name=name, color=_clean_color(request.color))
        with self._lock:
            tags, assignments = self._read_unlocked()
            self._write_unlocked(tags + [tag], assignments)
        return tag

    def set_model_tags(self, model_key: str, tag_ids: list[str]) -> list[str]:
        with self._lock:
            tags, assignments = self._read_unlocked()
            valid_ids = {tag.id for tag in tags}
            cleaned: list[str] = []
            for tag_id in tag_ids:
                if tag_id in valid_ids and tag_id not in cleaned:
                    cleaned.append(tag_id)
            assignments[model_key] = cleaned
            self._write_unlocked(tags, assignments)
        return cleaned

    def clear_model_tags(self, model_key: str) -> None:
        with self._lock:
            tags, assignments = self._read_unlocked()
            if model_key in assignments:
                assignments.pop(model_key, None)
                self._write_unlocked(tags, assignments)

    def _read_unlocked(self) -> tuple[list[ModelTag], dict[str, list[str]]]:
        if not self.path.exists():
            return [], {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return [], {}
        if not isinstance(payload, dict):
            return [], {}
        raw_tags = payload.get("tags")
        raw_assignments = payload.get("assignments")
        tags: list[ModelTag] = []
        if isinstance(raw_tags, list):
            for raw_tag in raw_tags:
                try:
                    tags.append(ModelTag.model_validate(raw_tag))
                except Exception:
                    continue
        assignments: dict[str, list[str]] = {}
        if isinstance(raw_assignments, dict):
            for key, value in raw_assignments.items():
                if isinstance(key, str) and isinstance(value, list):
                    assignments[key] = [item for item in value if isinstance(item, str)]
        return tags, assignments

    def _write_unlocked(self, tags: list[ModelTag], assignments: dict[str, list[str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": MODEL_TAGS_SCHEMA_VERSION,
            "tags": [tag.model_dump(mode="json") for tag in tags],
            "assignments": assignments,
        }
        tmp = self.path.with_suffix(f".{safe_store_segment(str(datetime.now(UTC).timestamp()))}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def _clean_color(color: str) -> str:
    value = color.strip()
    if len(value) == 7 and value.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in value[1:]):
        return value
    return "#60a5fa"
