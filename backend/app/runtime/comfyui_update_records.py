from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.core.paths import NoofyPaths

ACTIVE_COMFYUI_FILENAME = "active-comfyui.json"
LOCAL_VALIDATION_FILENAME = "local-validation.json"
UPDATE_METADATA_SCHEMA_VERSION = "0.1.0"


class LocalComfyUIVersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    available_upstream: bool = False
    installed: bool = False
    active: bool = False
    locally_verified: bool = False
    failed_validation: bool = False
    failed_reason: str | None = None
    source_hash: str | None = None
    commit_sha: str | None = None
    source_path: str | None = None
    env_path: str | None = None
    archive_url: str | None = None
    installed_at: str | None = None
    activated_at: str | None = None
    validated_at: str | None = None
    repair_status: str | None = None
    repair_attempt_count: int = 0
    last_repair_attempt_at: str | None = None
    last_repair_error: str | None = None
    repair_blocked_until: str | None = None
    incompatible: bool = False
    incompatible_reason: str | None = None
    last_successfully_started_at: str | None = None


class ComfyUIVersionRecordStore:
    def __init__(self, paths: NoofyPaths) -> None:
        self.paths = paths

    def active_record(self) -> LocalComfyUIVersionRecord | None:
        return read_active_record(self.paths)

    def previous_active_record(self) -> LocalComfyUIVersionRecord | None:
        return read_previous_active_record(self.paths)

    def read_records(self) -> dict[str, LocalComfyUIVersionRecord]:
        path = self.paths.core_engines_dir / LOCAL_VALIDATION_FILENAME
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        records = payload.get("records", {})
        if not isinstance(records, dict):
            return {}
        return {
            tag: LocalComfyUIVersionRecord.model_validate(record)
            for tag, record in records.items()
        }

    def write_records(self, records: dict[str, LocalComfyUIVersionRecord]) -> None:
        write_json(
            self.paths.core_engines_dir / LOCAL_VALIDATION_FILENAME,
            {
                "schema_version": UPDATE_METADATA_SCHEMA_VERSION,
                "updated_at": now_iso(),
                "records": {
                    tag: record.model_dump(mode="json")
                    for tag, record in sorted(records.items())
                },
            },
        )

    def upsert_record(self, record: LocalComfyUIVersionRecord) -> None:
        records = self.read_records()
        records[record.tag] = record
        self.write_records(records)

    def write_active_record(self, record: LocalComfyUIVersionRecord) -> None:
        active_payload = read_active_payload(self.paths) or {}
        previous = self.active_record()
        existing_previous = active_payload.get("previous_active")
        previous_payload = (
            existing_previous if isinstance(existing_previous, dict) else None
        )
        if previous is not None and previous.tag != record.tag:
            previous_payload = previous.model_dump(mode="json")
        write_json(
            self.paths.core_engines_dir / ACTIVE_COMFYUI_FILENAME,
            {
                "schema_version": UPDATE_METADATA_SCHEMA_VERSION,
                "active": record.model_dump(mode="json"),
                "previous_active": previous_payload,
            },
        )

    def mark_active(self, active_tag: str) -> None:
        records = self.read_records()
        for tag, record in list(records.items()):
            records[tag] = record.model_copy(update={"active": tag == active_tag})
        self.write_records(records)


def read_active_record(paths: NoofyPaths) -> LocalComfyUIVersionRecord | None:
    payload = read_active_payload(paths)
    if payload is None:
        return None
    active = payload.get("active")
    if not isinstance(active, dict):
        return None
    return LocalComfyUIVersionRecord.model_validate(active)


def read_previous_active_record(paths: NoofyPaths) -> LocalComfyUIVersionRecord | None:
    payload = read_active_payload(paths)
    if payload is None:
        return None
    previous = payload.get("previous_active")
    if not isinstance(previous, dict):
        return None
    return LocalComfyUIVersionRecord.model_validate(previous)


def read_active_payload(paths: NoofyPaths) -> dict[str, object] | None:
    path = paths.core_engines_dir / ACTIVE_COMFYUI_FILENAME
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        return None
    return payload


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
