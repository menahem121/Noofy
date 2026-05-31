from __future__ import annotations

import base64
import json
import re
import sqlite3
import threading
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.diagnostics import DiagnosticsSink
from app.gallery import GalleryItem, RunSubmissionSnapshot
from app.workflows.library import (
    WorkflowLibraryStore,
    WorkflowRunHistoryRecord,
    workflow_package_display_name,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import safe_store_segment

HistoryEventType = Literal[
    "run",
    "run_blocked",
    "workflow_imported",
    "workflow_removed",
    "import_failed",
]
HistoryEventStatus = Literal[
    "completed",
    "failed",
    "canceled",
    "blocked",
    "installed",
    "removed",
]

HISTORY_SCHEMA_VERSION = 2
DEFAULT_HISTORY_RETENTION = 5_000


class HistoryEventListItem(BaseModel):
    id: str
    type: HistoryEventType
    status: HistoryEventStatus
    title: str
    workflow_id: str | None = None
    workflow_name: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None
    output_url: str | None = None
    gallery_item_id: str | None = None
    gallery_item_ids: list[str] = Field(default_factory=list)
    source: str | None = None
    trust_level: str | None = None
    error_summary: str | None = None
    can_open_workflow: bool = False


class HistoryEventDetail(HistoryEventListItem):
    prompt: str | None = None
    used_settings: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class HistoryListResponse(BaseModel):
    events: list[HistoryEventListItem]
    total: int
    next_cursor: str | None = None
    has_more: bool = False


class HistoryQuery(BaseModel):
    limit: int = 50
    cursor: str | None = None
    type: HistoryEventType | None = None
    status: HistoryEventStatus | None = None
    workflow_id: str | None = None
    q: str | None = None
    created_after: str | None = None
    created_before: str | None = None
    sort: Literal["newest", "oldest"] = "newest"


@dataclass(frozen=True)
class ActivityEventCreate:
    type: HistoryEventType
    status: HistoryEventStatus
    title: str
    workflow_id: str | None
    workflow_name: str
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    thumbnail_url: str | None = None
    output_url: str | None = None
    gallery_item_id: str | None = None
    gallery_item_ids: list[str] | None = None
    source: str | None = None
    trust_level: str | None = None
    error_summary: str | None = None
    can_open_workflow: bool = False
    prompt: str | None = None
    used_settings: dict[str, Any] | None = None
    source_event_id: str | None = None


class ActivityLogStore:
    def __init__(
        self,
        db_path: Path,
        *,
        max_events: int = DEFAULT_HISTORY_RETENTION,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.db_path = db_path
        self.max_events = max_events
        self.log_store = log_store
        self._lock = threading.RLock()
        self._ensure_ready()

    def record_event(self, event: ActivityEventCreate) -> HistoryEventDetail:
        event_id = event.source_event_id or f"hist_{uuid.uuid4().hex}"
        payload = _event_create_payload(event_id, event)
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR IGNORE INTO activity_events (
                    id, type, status, title, workflow_id, workflow_name,
                    created_at, started_at, completed_at, duration_seconds,
                    thumbnail_url, output_url, gallery_item_id, gallery_item_ids_json, source,
                    trust_level, error_summary, can_open_workflow, prompt,
                    used_settings_json, search_text, schema_version
                ) VALUES (
                    :id, :type, :status, :title, :workflow_id, :workflow_name,
                    :created_at, :started_at, :completed_at, :duration_seconds,
                    :thumbnail_url, :output_url, :gallery_item_id, :gallery_item_ids_json, :source,
                    :trust_level, :error_summary, :can_open_workflow, :prompt,
                    :used_settings_json, :search_text, :schema_version
                )
                """,
                payload,
            )
            self._apply_retention(conn)
            conn.commit()
        existing = self.get_event(event_id)
        return existing or _detail_from_payload(payload)

    def list_events(self, query: HistoryQuery) -> HistoryListResponse:
        limit = max(1, min(query.limit, 100))
        where, params = _where_clause(query)
        cursor_clause, cursor_params = _cursor_clause(query)
        order = "ASC" if query.sort == "oldest" else "DESC"
        with self._lock, closing(self._connect()) as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM activity_events {where}", params).fetchone()[0])
            rows = conn.execute(
                f"""
                SELECT * FROM activity_events
                {_combine_where(where, cursor_clause)}
                ORDER BY created_at {order}, id {order}
                LIMIT ?
                """,
                [*params, *cursor_params, limit + 1],
            ).fetchall()
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = _encode_cursor(page_rows[-1], query.sort) if page_rows and has_more else None
        return HistoryListResponse(
            events=[_list_item_from_row(row) for row in page_rows],
            total=total,
            next_cursor=next_cursor,
            has_more=has_more,
        )

    def get_event(self, event_id: str) -> HistoryEventDetail | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM activity_events WHERE id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            return None
        return _detail_from_row(row)

    def attach_gallery_items(
        self,
        *,
        event_id: str,
        gallery_item_ids: list[str],
        thumbnail_url: str | None,
        output_url: str | None,
        gallery_item_id: str | None,
    ) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE activity_events
                SET thumbnail_url = ?, output_url = ?, gallery_item_id = ?, gallery_item_ids_json = ?
                WHERE id = ?
                """,
                (thumbnail_url, output_url, gallery_item_id, json.dumps(gallery_item_ids), event_id),
            )
            conn.commit()

    def clear(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("DELETE FROM activity_events")
            conn.commit()

    def _ensure_ready(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._create_schema()
        except sqlite3.DatabaseError as exc:
            corrupt_path = self._quarantine_database_files()
            if self.log_store is not None:
                self.log_store.add(
                    "error",
                    "History database was corrupt and has been quarantined",
                    "history.store",
                    details={"path": str(corrupt_path), "error": str(exc)},
                )
            self._create_schema()

    def _quarantine_database_files(self) -> Path:
        suffix = safe_store_segment(datetime.now(UTC).isoformat())
        corrupt_path = self.db_path.with_name(f"{self.db_path.name}.corrupt.{suffix}")
        for path in (
            self.db_path,
            self.db_path.with_name(f"{self.db_path.name}-wal"),
            self.db_path.with_name(f"{self.db_path.name}-shm"),
        ):
            if not path.exists():
                continue
            target = path.with_name(f"{path.name}.corrupt.{suffix}")
            path.replace(target)
            if path == self.db_path:
                corrupt_path = target
        return corrupt_path

    def _create_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            existing = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if existing is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (HISTORY_SCHEMA_VERSION,))
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS activity_events (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    workflow_id TEXT,
                    workflow_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_seconds REAL,
                    thumbnail_url TEXT,
                    output_url TEXT,
                    gallery_item_id TEXT,
                    gallery_item_ids_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT,
                    trust_level TEXT,
                    error_summary TEXT,
                    can_open_workflow INTEGER NOT NULL DEFAULT 0,
                    prompt TEXT,
                    used_settings_json TEXT NOT NULL DEFAULT '{}',
                    search_text TEXT NOT NULL DEFAULT '',
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_created_at ON activity_events(created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_type ON activity_events(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_status ON activity_events(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_workflow ON activity_events(workflow_id)")
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(activity_events)").fetchall()}
            if "gallery_item_ids_json" not in columns:
                conn.execute("ALTER TABLE activity_events ADD COLUMN gallery_item_ids_json TEXT NOT NULL DEFAULT '[]'")
                conn.execute(
                    "UPDATE activity_events SET gallery_item_ids_json = '[\"' || gallery_item_id || '\"]' "
                    "WHERE gallery_item_id IS NOT NULL"
                )
            conn.execute("UPDATE schema_version SET version = ?", (HISTORY_SCHEMA_VERSION,))
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _apply_retention(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            DELETE FROM activity_events
            WHERE id NOT IN (
                SELECT id FROM activity_events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            """,
            (self.max_events,),
        )


class HistoryService:
    def __init__(
        self,
        *,
        store: ActivityLogStore,
        workflow_library_store: WorkflowLibraryStore | None = None,
        workflow_loader: WorkflowPackageLoader | None = None,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.store = store
        self.workflow_library_store = workflow_library_store
        self.workflow_loader = workflow_loader
        self.log_store = log_store

    def list_events(self, query: HistoryQuery) -> HistoryListResponse:
        self.import_existing_run_history()
        return self.store.list_events(query)

    def get_event(self, event_id: str) -> HistoryEventDetail | None:
        self.import_existing_run_history()
        return self.store.get_event(event_id)

    def record_workflow_imported(self, workflow: dict[str, object]) -> None:
        workflow_id = _string_or_none(workflow.get("id"))
        self._safe_record(
            ActivityEventCreate(
                type="workflow_imported",
                status="installed",
                title="Workflow imported",
                workflow_id=workflow_id,
                workflow_name=_string_or_none(workflow.get("name")) or "Imported workflow",
                created_at=datetime.now(UTC),
                source=_string_or_none(workflow.get("source_label")),
                trust_level=_string_or_none(workflow.get("trust_level")),
                can_open_workflow=workflow_id is not None,
                source_event_id=f"workflow_imported:{workflow_id}" if workflow_id else None,
            )
        )

    def record_import_failed(self, *, filename: str | None, error: str) -> None:
        label = filename or "Workflow import"
        self._safe_record(
            ActivityEventCreate(
                type="import_failed",
                status="failed",
                title="Workflow import failed",
                workflow_id=None,
                workflow_name=label,
                created_at=datetime.now(UTC),
                source=filename,
                error_summary=_redact_text(error),
                can_open_workflow=False,
            )
        )

    def record_workflow_removed(self, workflow: dict[str, object]) -> None:
        workflow_id = _string_or_none(workflow.get("id"))
        self._safe_record(
            ActivityEventCreate(
                type="workflow_removed",
                status="removed",
                title="Workflow removed",
                workflow_id=workflow_id,
                workflow_name=_string_or_none(workflow.get("name")) or "Removed workflow",
                created_at=datetime.now(UTC),
                source=_string_or_none(workflow.get("source_label")),
                trust_level=_string_or_none(workflow.get("trust_level")),
                can_open_workflow=False,
            )
        )

    def record_run_blocked(
        self,
        *,
        workflow_id: str,
        workflow_name: str,
        reason: str,
    ) -> None:
        self._safe_record(
            ActivityEventCreate(
                type="run_blocked",
                status="blocked",
                title="Workflow run blocked",
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                created_at=datetime.now(UTC),
                error_summary=_redact_text(reason),
                can_open_workflow=True,
            )
        )

    def record_run_finished(
        self,
        *,
        job_id: str,
        workflow_id: str,
        workflow_name: str,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        error: str | None,
        snapshot: RunSubmissionSnapshot | None,
        gallery_items: list[GalleryItem],
    ) -> None:
        normalized_status: HistoryEventStatus
        if status == "canceled":
            normalized_status = "canceled"
        elif status == "failed":
            normalized_status = "failed"
        else:
            normalized_status = "completed"
        primary_gallery_item = gallery_items[0] if gallery_items else None
        used_settings = _settings_from_snapshot(snapshot)
        self._safe_record(
            ActivityEventCreate(
                type="run",
                status=normalized_status,
                title=_run_title(normalized_status),
                workflow_id=workflow_id,
                workflow_name=workflow_name,
                created_at=completed_at,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=max((completed_at - started_at).total_seconds(), 0),
                thumbnail_url=primary_gallery_item.thumbnail_url if primary_gallery_item else None,
                output_url=primary_gallery_item.content_url if primary_gallery_item else None,
                gallery_item_id=primary_gallery_item.id if primary_gallery_item else None,
                gallery_item_ids=[item.id for item in gallery_items],
                error_summary=_redact_text(error) if error else None,
                can_open_workflow=True,
                prompt=_prompt_from_settings(used_settings),
                used_settings=used_settings,
                source_event_id=f"run:{job_id}",
            )
        )

    def attach_gallery_items(self, job_id: str, gallery_items: list[GalleryItem]) -> None:
        primary = gallery_items[0] if gallery_items else None
        try:
            self.store.attach_gallery_items(
                event_id=f"run:{job_id}",
                gallery_item_ids=[item.id for item in gallery_items],
                thumbnail_url=primary.thumbnail_url if primary else None,
                output_url=primary.content_url if primary else None,
                gallery_item_id=primary.id if primary else None,
            )
            if self.log_store is not None:
                self.log_store.add(
                    "info",
                    "History Gallery attachments updated",
                    "history.service",
                    job_id=job_id,
                    details={"item_count": len(gallery_items), "primary_item_id": primary.id if primary else None},
                )
        except Exception as exc:
            if self.log_store is not None:
                self.log_store.add(
                    "error",
                    "History Gallery attachments could not be updated",
                    "history.service",
                    job_id=job_id,
                    details={"error": _redact_text(str(exc))},
                )

    def import_existing_run_history(self) -> None:
        if self.workflow_library_store is None:
            return
        for record in self.workflow_library_store.list_run_history_records():
            workflow_name = self._workflow_name(record.workflow_id)
            self._safe_record(
                ActivityEventCreate(
                    type="run",
                    status=_history_status_from_run(record.status),
                    title=_run_title(_history_status_from_run(record.status)),
                    workflow_id=record.workflow_id,
                    workflow_name=workflow_name,
                    created_at=_parse_datetime(record.finished_at),
                    started_at=_parse_datetime(record.started_at),
                    completed_at=_parse_datetime(record.finished_at),
                    duration_seconds=record.duration_seconds,
                    error_summary=_redact_text(record.error) if record.error else None,
                    can_open_workflow=self._workflow_exists(record.workflow_id),
                    source_event_id=f"run:{record.job_id}",
                )
            )

    def _safe_record(self, event: ActivityEventCreate) -> None:
        try:
            self.store.record_event(event)
        except Exception as exc:
            if self.log_store is not None:
                self.log_store.add(
                    "error",
                    "History event could not be recorded",
                    "history.service",
                    workflow_id=event.workflow_id,
                    details={"event_type": event.type, "event_status": event.status, "error": _redact_text(str(exc))},
                )

    def _workflow_name(self, workflow_id: str) -> str:
        if self.workflow_loader is None:
            return workflow_id
        try:
            package = self.workflow_loader.get_package(workflow_id)
            metadata = (
                self.workflow_library_store.metadata(workflow_id)
                if self.workflow_library_store is not None
                else None
            )
            return workflow_package_display_name(package, metadata)
        except Exception:
            return workflow_id

    def _workflow_exists(self, workflow_id: str) -> bool:
        if self.workflow_loader is None:
            return False
        try:
            self.workflow_loader.get_package(workflow_id)
            return True
        except Exception:
            return False


def workflow_display_name(package: WorkflowPackage) -> str:
    return workflow_package_display_name(package)


def _event_create_payload(event_id: str, event: ActivityEventCreate) -> dict[str, object | None]:
    used_settings = _clean_settings(event.used_settings or {})
    prompt = _redact_text(event.prompt) if event.prompt else None
    search_text = " ".join(
        str(part)
        for part in (
            event.title,
            event.workflow_name,
            event.status,
            event.type,
            event.error_summary or "",
            prompt or "",
            " ".join(str(value) for value in used_settings.values()),
        )
        if part
    ).casefold()
    return {
        "id": event_id,
        "type": event.type,
        "status": event.status,
        "title": _redact_text(event.title) or event.title,
        "workflow_id": event.workflow_id,
        "workflow_name": _redact_text(event.workflow_name) or event.workflow_name,
        "created_at": _iso(event.created_at),
        "started_at": _iso(event.started_at),
        "completed_at": _iso(event.completed_at),
        "duration_seconds": event.duration_seconds,
        "thumbnail_url": event.thumbnail_url,
        "output_url": event.output_url,
        "gallery_item_id": event.gallery_item_id,
        "gallery_item_ids_json": json.dumps(event.gallery_item_ids or []),
        "source": _redact_text(event.source) if event.source else None,
        "trust_level": event.trust_level,
        "error_summary": _redact_text(event.error_summary) if event.error_summary else None,
        "can_open_workflow": 1 if event.can_open_workflow else 0,
        "prompt": prompt,
        "used_settings_json": json.dumps(used_settings, sort_keys=True),
        "search_text": search_text,
        "schema_version": HISTORY_SCHEMA_VERSION,
    }


def _where_clause(query: HistoryQuery) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if query.type:
        clauses.append("type = ?")
        params.append(query.type)
    if query.status:
        clauses.append("status = ?")
        params.append(query.status)
    if query.workflow_id:
        clauses.append("workflow_id = ?")
        params.append(query.workflow_id)
    if query.created_after:
        clauses.append("created_at >= ?")
        params.append(query.created_after)
    if query.created_before:
        clauses.append("created_at <= ?")
        params.append(query.created_before)
    if query.q and query.q.strip():
        clauses.append("search_text LIKE ?")
        params.append(f"%{query.q.strip().casefold()}%")
    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)


def _combine_where(where: str, cursor_clause: str) -> str:
    if not cursor_clause:
        return where
    if not where:
        return f"WHERE {cursor_clause}"
    return f"{where} AND {cursor_clause}"


def _cursor_clause(query: HistoryQuery) -> tuple[str, list[object]]:
    cursor = _decode_cursor(query.cursor)
    if cursor is None:
        return "", []
    created_at, event_id, sort = cursor
    if sort != query.sort:
        return "", []
    if query.sort == "oldest":
        return "(created_at > ? OR (created_at = ? AND id > ?))", [created_at, created_at, event_id]
    return "(created_at < ? OR (created_at = ? AND id < ?))", [created_at, created_at, event_id]


def _list_item_from_row(row: sqlite3.Row) -> HistoryEventListItem:
    return HistoryEventListItem(
        id=row["id"],
        type=row["type"],
        status=row["status"],
        title=row["title"],
        workflow_id=row["workflow_id"],
        workflow_name=row["workflow_name"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_seconds=row["duration_seconds"],
        thumbnail_url=row["thumbnail_url"],
        output_url=row["output_url"],
        gallery_item_id=row["gallery_item_id"],
        gallery_item_ids=_loads_string_list(row["gallery_item_ids_json"]),
        source=row["source"],
        trust_level=row["trust_level"],
        error_summary=row["error_summary"],
        can_open_workflow=bool(row["can_open_workflow"]),
    )


def _detail_from_row(row: sqlite3.Row) -> HistoryEventDetail:
    base = _list_item_from_row(row).model_dump()
    try:
        settings = json.loads(row["used_settings_json"] or "{}")
    except Exception:
        settings = {}
    return HistoryEventDetail(
        **base,
        prompt=row["prompt"],
        used_settings=_clean_settings(settings if isinstance(settings, dict) else {}),
    )


def _detail_from_payload(payload: dict[str, object | None]) -> HistoryEventDetail:
    return HistoryEventDetail(
        id=str(payload["id"]),
        type=payload["type"],
        status=payload["status"],
        title=str(payload["title"]),
        workflow_id=payload["workflow_id"] if isinstance(payload["workflow_id"], str) else None,
        workflow_name=str(payload["workflow_name"]),
        created_at=str(payload["created_at"]),
        started_at=payload["started_at"] if isinstance(payload["started_at"], str) else None,
        completed_at=payload["completed_at"] if isinstance(payload["completed_at"], str) else None,
        duration_seconds=payload["duration_seconds"] if isinstance(payload["duration_seconds"], (float, int)) else None,
        thumbnail_url=payload["thumbnail_url"] if isinstance(payload["thumbnail_url"], str) else None,
        output_url=payload["output_url"] if isinstance(payload["output_url"], str) else None,
        gallery_item_id=payload["gallery_item_id"] if isinstance(payload["gallery_item_id"], str) else None,
        gallery_item_ids=_loads_string_list(payload["gallery_item_ids_json"]),
        source=payload["source"] if isinstance(payload["source"], str) else None,
        trust_level=payload["trust_level"] if isinstance(payload["trust_level"], str) else None,
        error_summary=payload["error_summary"] if isinstance(payload["error_summary"], str) else None,
        can_open_workflow=bool(payload["can_open_workflow"]),
        prompt=payload["prompt"] if isinstance(payload["prompt"], str) else None,
        used_settings=_clean_settings(json.loads(str(payload["used_settings_json"] or "{}"))),
    )


def _decode_cursor(cursor: str | None) -> tuple[str, str, str] | None:
    if not cursor:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        created_at = payload.get("created_at")
        event_id = payload.get("id")
        sort = payload.get("sort")
        if isinstance(created_at, str) and isinstance(event_id, str) and sort in {"newest", "oldest"}:
            return created_at, event_id, sort
        return None
    except Exception:
        return None


def _encode_cursor(row: sqlite3.Row, sort: str) -> str:
    payload = json.dumps({"created_at": row["created_at"], "id": row["id"], "sort": sort}, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except Exception:
        return datetime.now(UTC)


def _history_status_from_run(status: str) -> HistoryEventStatus:
    if status == "failed":
        return "failed"
    if status == "canceled":
        return "canceled"
    if status == "blocked_by_memory":
        return "blocked"
    return "completed"


def _run_title(status: HistoryEventStatus) -> str:
    if status == "failed":
        return "Workflow run failed"
    if status == "canceled":
        return "Workflow run canceled"
    if status == "blocked":
        return "Workflow run blocked"
    return "Workflow run completed"


def _settings_from_snapshot(snapshot: RunSubmissionSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    return {
        input_snapshot.label: input_snapshot.value
        for input_snapshot in snapshot.inputs
        if input_snapshot.label
    }


def _prompt_from_settings(settings: dict[str, Any]) -> str | None:
    for key, value in settings.items():
        if key.casefold() == "prompt" and isinstance(value, str):
            return value
    return None


def _clean_settings(settings: dict[str, Any]) -> dict[str, str | int | float | bool | None]:
    cleaned: dict[str, str | int | float | bool | None] = {}
    for key, value in settings.items():
        safe_key = _redact_text(str(key)) or str(key)
        if isinstance(value, str):
            cleaned[safe_key] = _redact_text(value)
        elif isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
            cleaned[safe_key] = value
        else:
            cleaned[safe_key] = _redact_text(json.dumps(value, sort_keys=True, default=str))
    return cleaned


def _redact_text(value: str | None, *, limit: int = 300) -> str | None:
    if value is None:
        return None
    text = value.strip()
    text = re.sub(r"(?i)(api[_-]?key|token|secret|password)=\S+", r"\1=[redacted]", text)
    text = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]", text)
    text = re.sub(r"/(?:Users|home|var|tmp|Volumes)/[^\s,;:]+", "[local-path-redacted]", text)
    text = re.sub(r"[A-Za-z]:\\[^\s,;:]+", "[local-path-redacted]", text)
    if len(text) > limit:
        text = f"{text[:limit].rstrip()}..."
    return text


def _string_or_none(value: object | None) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _loads_string_list(value: object | None) -> list[str]:
    try:
        loaded = json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError:
        return []
    return [item for item in loaded if isinstance(item, str)] if isinstance(loaded, list) else []
