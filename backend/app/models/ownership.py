from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.workflows.store_paths import safe_store_segment

MODEL_OWNERSHIP_SCHEMA_VERSION = "1"
StoredModelOrigin = Literal["imported", "downloaded"]


class StoredModelOwnership(BaseModel):
    origin: StoredModelOrigin
    recorded_at: str


class ModelOwnershipStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def origin_for_model(self, model_key: str) -> StoredModelOrigin | None:
        with self._lock:
            record = self._read_unlocked().get(model_key)
            return record.origin if record is not None else None

    def mark_imported(self, model_key: str) -> None:
        self._set_origin(model_key, "imported")

    def mark_downloaded(self, model_key: str) -> None:
        self._set_origin(model_key, "downloaded")

    def forget_model(self, model_key: str) -> None:
        with self._lock:
            records = self._read_unlocked()
            if records.pop(model_key, None) is not None:
                self._write_unlocked(records)

    def _set_origin(self, model_key: str, origin: StoredModelOrigin) -> None:
        with self._lock:
            records = self._read_unlocked()
            records[model_key] = StoredModelOwnership(origin=origin, recorded_at=datetime.now(UTC).isoformat())
            self._write_unlocked(records)

    def _read_unlocked(self) -> dict[str, StoredModelOwnership]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(payload, dict):
            return {}
        raw_models = payload.get("models")
        if not isinstance(raw_models, dict):
            return {}
        records: dict[str, StoredModelOwnership] = {}
        for key, value in raw_models.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            try:
                records[key] = StoredModelOwnership.model_validate(value)
            except Exception:
                continue
        return records

    def _write_unlocked(self, records: dict[str, StoredModelOwnership]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": MODEL_OWNERSHIP_SCHEMA_VERSION,
            "models": {key: value.model_dump(mode="json") for key, value in records.items()},
        }
        tmp = self.path.with_suffix(f".{safe_store_segment(str(datetime.now(UTC).timestamp()))}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
