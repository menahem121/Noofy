from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.workflows.store_paths import safe_store_segment


class WorkflowLibraryMetadata(BaseModel):
    description: str | None = None
    author: str | None = None
    website: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    icon: str | None = None
    updated_at: str | None = None


class WorkflowMetadataUpdate(BaseModel):
    description: str | None = None
    author: str | None = None
    website: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    icon: str | None = None


class WorkflowRunHistoryRecord(BaseModel):
    job_id: str
    workflow_id: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float | None = None
    error: str | None = None


class WorkflowRunHistorySummary(BaseModel):
    last_run_status: str | None = None
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_duration_seconds: float | None = None
    average_duration_seconds: float | None = None
    last_error: str | None = None
    run_count: int = 0


class WorkflowOpenHistoryRecord(BaseModel):
    workflow_id: str
    last_opened_at: str


class _WorkflowRunHistoryFile(BaseModel):
    records: list[WorkflowRunHistoryRecord] = Field(default_factory=list)


class WorkflowLibraryStore:
    """Local app-data store for workflow organization metadata and run history.

    Run history is intentionally separate from package.json and exported
    archives because it is local, private device state.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.metadata_dir = root_dir / "metadata"
        self.run_history_dir = root_dir / "run-history"
        self.open_history_dir = root_dir / "open-history"

    def metadata(self, workflow_id: str) -> WorkflowLibraryMetadata:
        path = self._metadata_path(workflow_id)
        if not path.exists():
            return WorkflowLibraryMetadata()
        try:
            return WorkflowLibraryMetadata.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return WorkflowLibraryMetadata()

    def update_metadata(
        self,
        workflow_id: str,
        update: WorkflowMetadataUpdate,
    ) -> WorkflowLibraryMetadata:
        current = self.metadata(workflow_id).model_dump(mode="json", exclude_none=True)
        patch = update.model_dump(mode="json", exclude_unset=True)
        cleaned: dict[str, object | None] = {}
        for key, value in patch.items():
            if key == "tags" and isinstance(value, list):
                cleaned[key] = _clean_tags(value)
            elif isinstance(value, str):
                cleaned[key] = value.strip()
            else:
                cleaned[key] = value
        current.update(cleaned)
        current["updated_at"] = datetime.now(UTC).isoformat()
        metadata = WorkflowLibraryMetadata.model_validate(current)
        self._write_json(self._metadata_path(workflow_id), metadata.model_dump(mode="json", exclude_none=True))
        return metadata

    def run_history_summary(self, workflow_id: str) -> WorkflowRunHistorySummary:
        records = self._run_history(workflow_id).records
        if not records:
            return WorkflowRunHistorySummary()
        latest = records[-1]
        durations = [record.duration_seconds for record in records if record.duration_seconds is not None]
        failed = [record for record in reversed(records) if record.error]
        return WorkflowRunHistorySummary(
            last_run_status=latest.status,
            last_started_at=latest.started_at,
            last_finished_at=latest.finished_at,
            last_duration_seconds=latest.duration_seconds,
            average_duration_seconds=sum(durations) / len(durations) if durations else None,
            last_error=failed[0].error if failed else None,
            run_count=len(records),
        )

    def list_run_history_records(self) -> list[WorkflowRunHistoryRecord]:
        records: list[WorkflowRunHistoryRecord] = []
        if not self.run_history_dir.exists():
            return records
        for path in sorted(self.run_history_dir.glob("*.json")):
            try:
                history = _WorkflowRunHistoryFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            records.extend(history.records)
        return sorted(records, key=lambda record: record.finished_at)

    def record_workflow_opened(
        self,
        workflow_id: str,
        *,
        opened_at: datetime | None = None,
    ) -> WorkflowOpenHistoryRecord:
        opened = opened_at or datetime.now(UTC)
        record = WorkflowOpenHistoryRecord(
            workflow_id=workflow_id,
            last_opened_at=opened.isoformat(),
        )
        self._write_json(self._open_history_path(workflow_id), record.model_dump(mode="json"))
        return record

    def workflow_last_opened(self, workflow_id: str) -> str | None:
        path = self._open_history_path(workflow_id)
        if not path.exists():
            return None
        try:
            record = WorkflowOpenHistoryRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None
        return record.last_opened_at

    def record_run_result(
        self,
        *,
        workflow_id: str,
        job_id: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> WorkflowRunHistorySummary:
        finished = finished_at or datetime.now(UTC)
        duration = max((finished - started_at).total_seconds(), 0)
        history = self._run_history(workflow_id)
        history.records.append(
            WorkflowRunHistoryRecord(
                job_id=job_id,
                workflow_id=workflow_id,
                status=status,
                started_at=started_at.isoformat(),
                finished_at=finished.isoformat(),
                duration_seconds=duration,
                error=error,
            )
        )
        history.records = history.records[-100:]
        self._write_json(self._run_history_path(workflow_id), history.model_dump(mode="json"))
        return self.run_history_summary(workflow_id)

    def remove_workflow(self, workflow_id: str) -> None:
        for path in (
            self._metadata_path(workflow_id),
            self._run_history_path(workflow_id),
            self._open_history_path(workflow_id),
        ):
            path.unlink(missing_ok=True)

    def remove_all(self) -> None:
        shutil.rmtree(self.root_dir, ignore_errors=True)

    def _metadata_path(self, workflow_id: str) -> Path:
        return self.metadata_dir / f"{safe_store_segment(workflow_id)}.json"

    def _run_history_path(self, workflow_id: str) -> Path:
        return self.run_history_dir / f"{safe_store_segment(workflow_id)}.json"

    def _open_history_path(self, workflow_id: str) -> Path:
        return self.open_history_dir / f"{safe_store_segment(workflow_id)}.json"

    def _run_history(self, workflow_id: str) -> _WorkflowRunHistoryFile:
        path = self._run_history_path(workflow_id)
        if not path.exists():
            return _WorkflowRunHistoryFile()
        try:
            return _WorkflowRunHistoryFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return _WorkflowRunHistoryFile()

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{safe_store_segment(str(datetime.now(UTC).timestamp()))}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def _clean_tags(tags: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        value = tag.strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value[:40])
    return cleaned
