from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Awaitable, Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from app.diagnostics import DiagnosticsSink, sanitize_text
from app.engine.models import EngineOutputStream, JobResult
from app.workflows.library import workflow_package_display_name
from app.workflows.package import WorkflowPackage

GALLERY_SCHEMA_VERSION = 2
THUMBNAIL_SIZE = (512, 512)
MEDIA_KINDS = {"image", "video", "audio", "file"}
MEDIA_BUCKETS = ("images", "audio", "video", "videos", "gifs", "files", "text")
SaveState = Literal[
    "queued",
    "saving",
    "saved",
    "saved_with_errors",
    "failed",
    "canceled",
    "interrupted",
    "unavailable",
]


class OutputPreference(BaseModel):
    auto_save: bool = False


class GalleryOutputWidgetSnapshot(BaseModel):
    control_id: str
    output_id: str
    node_id: str
    widget_title: str
    media_kind: str = "image"


class GalleryInputSnapshot(BaseModel):
    input_id: str
    label: str
    control_type: str
    value: Any = None


class RunSubmissionSnapshot(BaseModel):
    workflow_id: str
    workflow_title: str
    dashboard_version: str
    values: dict[str, Any] = Field(default_factory=dict)
    output_preferences: dict[str, OutputPreference] = Field(default_factory=dict)
    output_widgets: list[GalleryOutputWidgetSnapshot] = Field(default_factory=list)
    inputs: list[GalleryInputSnapshot] = Field(default_factory=list)


class GalleryItem(BaseModel):
    id: str
    kind: str
    type: str
    created_at: str
    workflow_id: str
    workflow_title: str
    job_id: str
    control_id: str
    output_id: str
    node_id: str
    widget_title: str
    url: str
    content_url: str
    thumbnail_url: str | None = None
    content_rel_path: str = Field(exclude=True)
    thumbnail_rel_path: str | None = Field(default=None, exclude=True)
    filename: str
    mime_type: str | None = None
    extension: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None
    duration_seconds: float | None = None
    fps: float | None = None
    favorite: bool = False
    file_state: str = "available"
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = GALLERY_SCHEMA_VERSION


class GalleryResponse(BaseModel):
    items: list[GalleryItem]
    total: int


class GallerySaveRequest(BaseModel):
    job_id: str
    control_id: str
    status: SaveState
    message: str | None = None
    bytes_copied: int = 0
    total_bytes: int | None = None
    item_ids: list[str] = Field(default_factory=list)
    updated_at: str


class GalleryJobSaveStatus(BaseModel):
    job_id: str
    outputs: list[GallerySaveRequest] = Field(default_factory=list)


@dataclass(frozen=True)
class CapturedGalleryOutput:
    idempotency_key: str
    created_at: datetime
    workflow_id: str
    workflow_title: str
    job_id: str
    control_id: str
    output_id: str
    node_id: str
    widget_title: str
    kind: str
    staged_path: Path
    source_filename: str
    source_mime_type: str | None
    extension: str | None
    size_bytes: int
    width: int | None
    height: int | None
    duration_seconds: float | None
    fps: float | None
    generation_settings: dict[str, Any]


class GalleryStore:
    def __init__(self, root_dir: Path, log_store: DiagnosticsSink | None = None) -> None:
        self.root_dir = root_dir
        self.media_dir = root_dir / "media"
        self.images_dir = root_dir / "images"
        self.thumbnails_dir = root_dir / "thumbnails"
        self.db_path = root_dir / "gallery.db"
        self.log_store = log_store
        self._lock = threading.RLock()
        self._ensure_ready()

    def _ensure_ready(self) -> None:
        for directory in (self.media_dir, self.images_dir, self.thumbnails_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self._cleanup_temp_files()
        with self._lock, closing(self._connect()) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            existing = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            has_items = self._table_exists(conn, "gallery_items")
            if existing is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (GALLERY_SCHEMA_VERSION,))
                version = GALLERY_SCHEMA_VERSION
            else:
                version = int(existing["version"])
            item_columns = self._table_columns(conn, "gallery_items") if has_items else set()
            if has_items and (version < 2 or "content_rel_path" not in item_columns):
                self._migrate_v1_to_v2(conn)
            self._create_v2_schema(conn)
            conn.execute("UPDATE schema_version SET version = ?", (GALLERY_SCHEMA_VERSION,))
            conn.execute(
                "UPDATE gallery_save_requests SET status = 'interrupted', message = ? "
                "WHERE status IN ('queued', 'saving')",
                ("Gallery save was interrupted. Retry if the output is still available.",),
            )
            conn.commit()

    def _create_v2_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gallery_items (
                id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL,
                workflow_id TEXT NOT NULL,
                workflow_title TEXT NOT NULL,
                job_id TEXT NOT NULL,
                control_id TEXT NOT NULL,
                output_id TEXT NOT NULL,
                node_id TEXT NOT NULL,
                widget_title TEXT NOT NULL,
                content_rel_path TEXT NOT NULL,
                thumbnail_rel_path TEXT,
                filename TEXT NOT NULL,
                mime_type TEXT,
                extension TEXT,
                size_bytes INTEGER,
                width INTEGER,
                height INTEGER,
                duration_seconds REAL,
                fps REAL,
                favorite INTEGER NOT NULL DEFAULT 0,
                file_state TEXT NOT NULL DEFAULT 'available',
                generation_settings_json TEXT NOT NULL DEFAULT '{}',
                schema_version INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gallery_created_at ON gallery_items(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gallery_job_id ON gallery_items(job_id)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gallery_run_manifests (
                job_id TEXT PRIMARY KEY,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gallery_save_requests (
                job_id TEXT NOT NULL,
                control_id TEXT NOT NULL,
                status TEXT NOT NULL,
                message TEXT,
                bytes_copied INTEGER NOT NULL DEFAULT 0,
                total_bytes INTEGER,
                item_ids_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(job_id, control_id)
            )
            """
        )

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ALTER TABLE gallery_items RENAME TO gallery_items_v1")
        self._create_v2_schema(conn)
        rows = conn.execute("SELECT * FROM gallery_items_v1").fetchall()
        for row in rows:
            rel_path = row["image_rel_path"]
            path = self._resolve_rel_path(rel_path)
            filename = path.name
            conn.execute(
                """
                INSERT INTO gallery_items (
                    id, idempotency_key, kind, created_at, workflow_id, workflow_title,
                    job_id, control_id, output_id, node_id, widget_title, content_rel_path,
                    thumbnail_rel_path, filename, mime_type, extension, size_bytes, width,
                    height, favorite, file_state, generation_settings_json, schema_version
                ) VALUES (?, ?, 'image', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"], row["idempotency_key"], row["created_at"], row["workflow_id"],
                    row["workflow_title"], row["job_id"], row["control_id"], row["output_id"],
                    row["node_id"], row["widget_title"], rel_path, row["thumbnail_rel_path"],
                    filename, row["mime_type"], Path(filename).suffix.lower() or None,
                    path.stat().st_size if path.exists() else None, row["width"], row["height"],
                    row["favorite"], row["file_state"], row["generation_settings_json"],
                    GALLERY_SCHEMA_VERSION,
                ),
            )
        conn.execute("DROP TABLE gallery_items_v1")
        conn.execute("UPDATE schema_version SET version = ?", (GALLERY_SCHEMA_VERSION,))
        conn.commit()
        self._log("info", "Migrated Gallery storage for mixed media", details={"item_count": len(rows)})

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone() is not None

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    def _cleanup_temp_files(self) -> None:
        removed = 0
        for directory in (self.media_dir, self.images_dir, self.thumbnails_dir):
            for path in directory.glob("*.tmp"):
                with contextlib.suppress(OSError):
                    path.unlink()
                    removed += 1
        if removed:
            self._log("info", "Cleaned interrupted Gallery temporary files", details={"file_count": removed})

    def create_staging_path(self) -> Path:
        fd, path = tempfile.mkstemp(dir=self.media_dir, suffix=".gallery-save.tmp")
        os.close(fd)
        return Path(path)

    def list_items(self) -> GalleryResponse:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM gallery_items ORDER BY created_at DESC").fetchall()
        items = [item for row in rows if (item := self._item_from_row_or_none(row)) is not None]
        return GalleryResponse(items=items, total=len(items))

    def items_for_job(self, job_id: str) -> list[GalleryItem]:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM gallery_items WHERE job_id = ? ORDER BY created_at, id", (job_id,)
            ).fetchall()
        return [item for row in rows if (item := self._item_from_row_or_none(row)) is not None]

    def get_item(self, item_id: str) -> GalleryItem | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM gallery_items WHERE id = ?", (item_id,)).fetchone()
        return self._item_from_row_or_none(row) if row is not None else None

    def content_path(self, item_id: str, *, thumbnail: bool = False) -> Path | None:
        item = self.get_item(item_id)
        if item is None:
            return None
        rel_path = item.thumbnail_rel_path if thumbnail else item.content_rel_path
        return self._resolve_rel_path(rel_path) if rel_path else None

    def set_favorite(self, item_id: str, favorite: bool) -> GalleryItem | None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                "UPDATE gallery_items SET favorite = ? WHERE id = ?", (1 if favorite else 0, item_id)
            )
            conn.commit()
        return self.get_item(item_id) if cursor.rowcount else None

    def delete_item(self, item_id: str) -> GalleryItem | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM gallery_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            item = self._item_from_row(row)
            paths = [self._resolve_rel_path(row["content_rel_path"])]
            if row["thumbnail_rel_path"]:
                paths.append(self._resolve_rel_path(row["thumbnail_rel_path"]))
            tombstones: list[tuple[Path, Path]] = []
            try:
                conn.execute("BEGIN IMMEDIATE")
                for path in paths:
                    if not path.exists():
                        continue
                    tombstone = path.with_name(f".{path.name}.{item_id}.delete.tmp")
                    path.replace(tombstone)
                    tombstones.append((path, tombstone))
                conn.execute("DELETE FROM gallery_items WHERE id = ?", (item_id,))
                self._detach_item_from_requests(conn, item_id)
                conn.commit()
            except Exception as exc:
                with contextlib.suppress(Exception):
                    conn.rollback()
                for original, tombstone in reversed(tombstones):
                    if tombstone.exists() and not original.exists():
                        with contextlib.suppress(OSError):
                            tombstone.replace(original)
                raise ValueError("Gallery item could not be deleted cleanly.") from exc
        for _, tombstone in tombstones:
            with contextlib.suppress(OSError):
                tombstone.unlink()
        self._log("info", "Deleted Gallery item", details={"item_id": item_id, "kind": item.kind})
        return item

    def get_item_by_idempotency_key(self, idempotency_key: str) -> GalleryItem | None:
        hashed_key = _hash_idempotency_key(idempotency_key)
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM gallery_items WHERE idempotency_key = ?", (hashed_key,)
            ).fetchone()
        return self._item_from_row_or_none(row) if row is not None else None

    def save_staged_output(self, output: CapturedGalleryOutput) -> GalleryItem:
        hashed_key = _hash_idempotency_key(output.idempotency_key)
        item_id = hashed_key[:24]
        existing = self.get_item_by_idempotency_key(output.idempotency_key)
        if existing is not None:
            output.staged_path.unlink(missing_ok=True)
            self._log("info", "Reused existing Gallery item", details={"item_id": existing.id, "job_id": output.job_id})
            return existing

        extension = _safe_extension(output.extension or Path(output.source_filename).suffix)
        if not extension:
            extension = _safe_extension(mimetypes.guess_extension(output.source_mime_type or "") or "") or ".bin"
        mime_type = output.source_mime_type or mimetypes.guess_type(output.source_filename)[0] or "application/octet-stream"
        width, height = output.width, output.height
        thumb_tmp: Path | None = None
        if output.kind == "image":
            thumb_tmp = self.create_thumbnail_staging_path()
            inspected_mime, inspected_width, inspected_height, ok = _inspect_image_file(output.staged_path, thumb_tmp)
            mime_type = inspected_mime or mime_type
            width = inspected_width or width
            height = inspected_height or height
            if not ok:
                thumb_tmp.unlink(missing_ok=True)
                thumb_tmp = None

        slug = _slugify(output.workflow_title or output.widget_title or "noofy-output")
        stamp = output.created_at.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        base = f"{stamp}_{slug}_{item_id[:6]}"
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM gallery_items WHERE idempotency_key = ?", (hashed_key,)
            ).fetchone()
            if row is not None:
                conn.commit()
                output.staged_path.unlink(missing_ok=True)
                if thumb_tmp:
                    thumb_tmp.unlink(missing_ok=True)
                return self._item_from_row(row)
            content_path: Path | None = None
            thumb_path: Path | None = None
            try:
                media_name = self._unique_filename(self.media_dir, base, extension)
                content_rel = f"media/{media_name}"
                content_path = self.media_dir / media_name
                thumb_rel: str | None = None
                if thumb_tmp:
                    thumb_name = self._unique_filename(self.thumbnails_dir, base, ".webp")
                    thumb_rel = f"thumbnails/{thumb_name}"
                    thumb_path = self.thumbnails_dir / thumb_name
                os.replace(output.staged_path, content_path)
                if thumb_tmp and thumb_path:
                    os.replace(thumb_tmp, thumb_path)
                conn.execute(
                    """
                    INSERT INTO gallery_items (
                        id, idempotency_key, kind, created_at, workflow_id, workflow_title,
                        job_id, control_id, output_id, node_id, widget_title, content_rel_path,
                        thumbnail_rel_path, filename, mime_type, extension, size_bytes, width,
                        height, duration_seconds, fps, favorite, file_state,
                        generation_settings_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'available', ?, ?)
                    """,
                    (
                        item_id, hashed_key, output.kind, output.created_at.astimezone(UTC).isoformat(),
                        output.workflow_id, output.workflow_title, output.job_id, output.control_id,
                        output.output_id, output.node_id, output.widget_title, content_rel, thumb_rel,
                        _safe_basename(output.source_filename) or media_name, mime_type, extension,
                        output.size_bytes, width, height, output.duration_seconds, output.fps,
                        json.dumps(output.generation_settings, sort_keys=True), GALLERY_SCHEMA_VERSION,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                output.staged_path.unlink(missing_ok=True)
                if content_path:
                    content_path.unlink(missing_ok=True)
                if thumb_tmp:
                    thumb_tmp.unlink(missing_ok=True)
                if thumb_path:
                    thumb_path.unlink(missing_ok=True)
                raise
        item = self.get_item(item_id)
        if item is None:
            raise ValueError("Gallery item was not persisted.")
        return item

    def create_thumbnail_staging_path(self) -> Path:
        fd, path = tempfile.mkstemp(dir=self.thumbnails_dir, suffix=".tmp")
        os.close(fd)
        return Path(path)

    def save_run_manifest(self, result: JobResult, snapshot: RunSubmissionSnapshot) -> None:
        payload = snapshot.model_dump(mode="json")
        with self._lock, closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO gallery_run_manifests(job_id, snapshot_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET snapshot_json = excluded.snapshot_json
                """,
                (result.job_id, json.dumps(payload, sort_keys=True), datetime.now(UTC).isoformat()),
            )
            conn.commit()

    def run_manifest(self, job_id: str) -> RunSubmissionSnapshot | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT snapshot_json FROM gallery_run_manifests WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return RunSubmissionSnapshot.model_validate_json(row["snapshot_json"])
        except Exception:
            return None

    def put_save_request(
        self,
        job_id: str,
        control_id: str,
        status: SaveState,
        *,
        message: str | None = None,
        bytes_copied: int = 0,
        total_bytes: int | None = None,
        item_ids: list[str] | None = None,
    ) -> GallerySaveRequest:
        updated_at = datetime.now(UTC).isoformat()
        with self._lock, closing(self._connect()) as conn:
            existing = conn.execute(
                "SELECT item_ids_json FROM gallery_save_requests WHERE job_id = ? AND control_id = ?",
                (job_id, control_id),
            ).fetchone()
            saved_ids = item_ids
            if saved_ids is None:
                saved_ids = _loads_list(existing["item_ids_json"]) if existing else []
            conn.execute(
                """
                INSERT INTO gallery_save_requests(
                    job_id, control_id, status, message, bytes_copied, total_bytes,
                    item_ids_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id, control_id) DO UPDATE SET
                    status = excluded.status, message = excluded.message,
                    bytes_copied = excluded.bytes_copied, total_bytes = excluded.total_bytes,
                    item_ids_json = excluded.item_ids_json, updated_at = excluded.updated_at
                """,
                (
                    job_id, control_id, status, message, bytes_copied, total_bytes,
                    json.dumps(saved_ids), updated_at,
                ),
            )
            conn.commit()
        return self.save_request(job_id, control_id)  # type: ignore[return-value]

    def save_request(self, job_id: str, control_id: str) -> GallerySaveRequest | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM gallery_save_requests WHERE job_id = ? AND control_id = ?",
                (job_id, control_id),
            ).fetchone()
        return self._request_from_row(row) if row is not None else None

    def job_save_status(self, job_id: str) -> GalleryJobSaveStatus:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM gallery_save_requests WHERE job_id = ? ORDER BY control_id", (job_id,)
            ).fetchall()
        return GalleryJobSaveStatus(job_id=job_id, outputs=[self._request_from_row(row) for row in rows])

    def _detach_item_from_requests(self, conn: sqlite3.Connection, item_id: str) -> None:
        rows = conn.execute(
            "SELECT job_id, control_id, status, item_ids_json FROM gallery_save_requests"
        ).fetchall()
        for row in rows:
            previous_ids = _loads_list(row["item_ids_json"])
            item_ids = [value for value in previous_ids if value != item_id]
            if len(item_ids) == len(previous_ids):
                continue
            status = row["status"]
            message = None
            if status in {"saved", "saved_with_errors"}:
                status = "interrupted" if not item_ids else "saved_with_errors"
                message = (
                    "Saved Gallery item was deleted. Save again to restore it."
                    if not item_ids
                    else "Some saved Gallery items were deleted. Save again to restore them."
                )
            conn.execute(
                """
                UPDATE gallery_save_requests
                SET status = ?, message = COALESCE(?, message), item_ids_json = ?, updated_at = ?
                WHERE job_id = ? AND control_id = ?
                """,
                (
                    status, message, json.dumps(item_ids), datetime.now(UTC).isoformat(),
                    row["job_id"], row["control_id"],
                ),
            )

    def _request_from_row(self, row: sqlite3.Row) -> GallerySaveRequest:
        return GallerySaveRequest(
            job_id=row["job_id"], control_id=row["control_id"], status=row["status"],
            message=row["message"], bytes_copied=row["bytes_copied"],
            total_bytes=row["total_bytes"], item_ids=_loads_list(row["item_ids_json"]),
            updated_at=row["updated_at"],
        )

    def _item_from_row_or_none(self, row: sqlite3.Row) -> GalleryItem | None:
        try:
            return self._item_from_row(row)
        except Exception:
            return None

    def _item_from_row(self, row: sqlite3.Row) -> GalleryItem:
        content_path = self._resolve_rel_path(row["content_rel_path"])
        thumb_rel = row["thumbnail_rel_path"]
        thumb_path = self._resolve_rel_path(thumb_rel) if thumb_rel else None
        file_state = row["file_state"]
        if not content_path.exists():
            file_state = "missing"
        elif thumb_rel and (thumb_path is None or not thumb_path.exists()):
            file_state = "degraded"
        content_url = f"/api/gallery/{row['id']}/content"
        return GalleryItem(
            id=row["id"], kind=row["kind"], type=row["kind"], created_at=row["created_at"],
            workflow_id=row["workflow_id"], workflow_title=row["workflow_title"], job_id=row["job_id"],
            control_id=row["control_id"], output_id=row["output_id"], node_id=row["node_id"],
            widget_title=row["widget_title"], url=content_url, content_url=content_url,
            thumbnail_url=f"/api/gallery/{row['id']}/thumbnail" if thumb_rel else None,
            content_rel_path=row["content_rel_path"], thumbnail_rel_path=thumb_rel,
            filename=row["filename"], mime_type=row["mime_type"], extension=row["extension"],
            size_bytes=row["size_bytes"], width=row["width"], height=row["height"],
            duration_seconds=row["duration_seconds"], fps=row["fps"], favorite=bool(row["favorite"]),
            file_state=file_state, generation_settings=_loads_dict(row["generation_settings_json"]),
            schema_version=row["schema_version"],
        )

    def _unique_filename(self, directory: Path, base: str, extension: str) -> str:
        candidate = f"{base}{extension}"
        if not (directory / candidate).exists():
            return candidate
        for counter in range(1, 1000):
            suffix = hashlib.sha1(f"{base}-{counter}".encode()).hexdigest()[:5]
            candidate = f"{base}-{suffix}{extension}"
            if not (directory / candidate).exists():
                return candidate
        raise ValueError("Could not allocate a unique Gallery filename.")

    def _resolve_rel_path(self, rel_path: str) -> Path:
        candidate = (self.root_dir / rel_path).resolve()
        root = self.root_dir.resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError("Invalid Gallery file path.")
        return candidate

    def _log(self, level: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        if self.log_store is not None:
            self.log_store.add(level, message, "gallery.store", details=details)  # type: ignore[arg-type]


class GalleryCaptureService:
    def __init__(
        self,
        store: GalleryStore,
        *,
        log_store: DiagnosticsSink | None = None,
        resolve_result: Callable[[str], Awaitable[JobResult]] | None = None,
        stream_output: Callable[[str, str, str, str, str | None], Awaitable[EngineOutputStream]] | None = None,
        on_items_changed: Callable[[str, list[GalleryItem]], None] | None = None,
    ) -> None:
        self.store = store
        self.log_store = log_store
        self.resolve_result = resolve_result
        self.stream_output = stream_output
        self.on_items_changed = on_items_changed
        self._tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._cancel_events: dict[tuple[str, str], asyncio.Event] = {}

    def configure(
        self,
        *,
        resolve_result: Callable[[str], Awaitable[JobResult]],
        stream_output: Callable[[str, str, str, str, str | None], Awaitable[EngineOutputStream]],
        on_items_changed: Callable[[str, list[GalleryItem]], None] | None = None,
    ) -> None:
        self.resolve_result = resolve_result
        self.stream_output = stream_output
        self.on_items_changed = on_items_changed

    def register_completed_run(self, result: JobResult, snapshot: RunSubmissionSnapshot | None) -> None:
        if result.status == "completed" and snapshot is not None:
            self.store.save_run_manifest(result, snapshot)

    def schedule_auto_saves(self, result: JobResult, snapshot: RunSubmissionSnapshot | None) -> None:
        if result.status != "completed" or snapshot is None:
            return
        for widget in snapshot.output_widgets:
            if snapshot.output_preferences.get(widget.control_id, OutputPreference()).auto_save:
                self.schedule_output_save(result.job_id, widget.control_id, result=result)

    def schedule_output_save(
        self, job_id: str, control_id: str, *, result: JobResult | None = None
    ) -> GallerySaveRequest:
        manifest = self.store.run_manifest(job_id)
        if manifest is None or not any(widget.control_id == control_id for widget in manifest.output_widgets):
            raise KeyError("Gallery output is not available for this job.")
        key = (job_id, control_id)
        existing_task = self._tasks.get(key)
        if existing_task is not None and not existing_task.done():
            return self.store.save_request(job_id, control_id) or self.store.put_save_request(job_id, control_id, "queued")
        existing_request = self.store.save_request(job_id, control_id)
        if existing_request is not None and existing_request.status == "saved":
            self._log("info", "Gallery save reused completed request", job_id, details={"control_id": control_id})
            return existing_request
        request = self.store.put_save_request(
            job_id, control_id, "queued", message="Waiting to save this output to Gallery."
        )
        cancel_event = asyncio.Event()
        self._cancel_events[key] = cancel_event
        task = asyncio.create_task(self._run_save(job_id, control_id, result, cancel_event))
        self._tasks[key] = task

        def _forget_task(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(key) is completed:
                self._tasks.pop(key, None)

        task.add_done_callback(_forget_task)
        self._log("info", "Gallery save scheduled", job_id, details={"control_id": control_id})
        return request

    def cancel_output_save(self, job_id: str, control_id: str) -> GallerySaveRequest:
        key = (job_id, control_id)
        event = self._cancel_events.get(key)
        task = self._tasks.get(key)
        if event is None or task is None or task.done():
            existing = self.store.save_request(job_id, control_id)
            if existing is not None:
                return existing
            raise KeyError("Gallery save is not active for this output.")
        if event is not None:
            event.set()
        if task is not None and not task.done():
            task.cancel()
        request = self.store.put_save_request(
            job_id, control_id, "canceled", message="Gallery save canceled. Partial files were removed."
        )
        self._log("info", "Gallery save canceled", job_id, details={"control_id": control_id})
        return request

    def job_status(self, job_id: str) -> GalleryJobSaveStatus:
        return self.store.job_save_status(job_id)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.items())
        for key, task in tasks:
            if not task.done():
                task.cancel()
                self.store.put_save_request(
                    *key, "interrupted", message="Gallery save was interrupted. Retry if the output is still available."
                )
                self._log("warning", "Gallery save interrupted during shutdown", key[0], details={"control_id": key[1]})
        if tasks:
            await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)

    async def _run_save(
        self, job_id: str, control_id: str, result: JobResult | None, cancel_event: asyncio.Event
    ) -> None:
        saved_ids: list[str] = []
        try:
            self.store.put_save_request(job_id, control_id, "saving", message="Saving output to Gallery.")
            manifest = self.store.run_manifest(job_id)
            if manifest is None:
                raise GalleryUnavailable("The saved run details are no longer available.")
            widget = next((item for item in manifest.output_widgets if item.control_id == control_id), None)
            if widget is None:
                raise GalleryUnavailable("This output is no longer declared by the workflow.")
            if result is None:
                if self.resolve_result is None:
                    raise GalleryUnavailable("The generated output is no longer available.")
                result = await self.resolve_result(job_id)
            if result.status != "completed":
                raise GalleryUnavailable("The generated output is no longer available.")
            prefer_declared_kind = sum(
                output_widget.node_id == widget.node_id for output_widget in manifest.output_widgets
            ) == 1
            matched = _matching_output_items(result, widget, prefer_declared_kind=prefer_declared_kind)
            if not matched:
                raise GalleryUnavailable("The generated output is no longer available.")
            for index, (bucket, item) in enumerate(matched):
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                saved = await self._copy_output(
                    result, manifest, widget, bucket, index, item, cancel_event,
                    prefer_declared_kind=prefer_declared_kind,
                )
                saved_ids.append(saved.id)
            message = "Saved to Gallery." if saved_ids else "No Gallery items were saved."
            self.store.put_save_request(job_id, control_id, "saved", message=message, item_ids=saved_ids)
            self._notify_items_changed(job_id)
            self._log("info", "Gallery save completed", job_id, details={"control_id": control_id, "item_count": len(saved_ids)})
        except GalleryUnavailable as exc:
            status: SaveState = "saved_with_errors" if saved_ids else "unavailable"
            self.store.put_save_request(job_id, control_id, status, message=str(exc), item_ids=saved_ids)
            self._notify_items_changed(job_id)
            self._log("warning", "Gallery source output unavailable", job_id, details={"control_id": control_id, "error": _redact_url_or_token_string(str(exc))})
        except asyncio.CancelledError:
            status: SaveState = "canceled" if cancel_event.is_set() else "interrupted"
            message = "Gallery save canceled. Partial files were removed." if status == "canceled" else "Gallery save was interrupted. Retry if the output is still available."
            self.store.put_save_request(job_id, control_id, status, message=message, item_ids=saved_ids)
            self._notify_items_changed(job_id)
        except Exception as exc:
            status = "saved_with_errors" if saved_ids else "failed"
            self.store.put_save_request(
                job_id, control_id, status, message="Some Gallery items could not be saved. Retry this output." if saved_ids else "Gallery save failed. Retry this output.", item_ids=saved_ids
            )
            self._notify_items_changed(job_id)
            self._log("error", "Gallery save failed", job_id, details={"control_id": control_id, "error": _redact_url_or_token_string(str(exc))})
        finally:
            self._cancel_events.pop((job_id, control_id), None)

    async def _copy_output(
        self,
        result: JobResult,
        manifest: RunSubmissionSnapshot,
        widget: GalleryOutputWidgetSnapshot,
        bucket: str,
        index: int,
        item: dict[str, Any],
        cancel_event: asyncio.Event,
        *,
        prefer_declared_kind: bool,
    ) -> GalleryItem:
        filename = _safe_basename(str(item.get("filename") or f"{widget.media_kind}-{index}.bin"))
        subfolder = str(item.get("subfolder") or "")
        output_type = _engine_output_type(item)
        idempotency_key = _capture_idempotency_key(
            job_id=result.job_id, control_id=widget.control_id, output_id=widget.output_id,
            node_id=widget.node_id, item_index=index, filename=filename, subfolder=subfolder,
            output_type=output_type,
        )
        existing = self.store.get_item_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing
        if self.stream_output is None:
            raise GalleryUnavailable("The generated output is no longer available.")
        try:
            streamed = await self.stream_output(result.job_id, filename, subfolder, output_type, None)
        except (KeyError, ValueError) as exc:
            raise GalleryUnavailable("The generated output is no longer available.") from exc
        staged_path: Path | None = None
        try:
            copied = 0
            total = _optional_int(streamed.headers.get("content-length")) or _optional_int(item.get("size"))
            if total:
                _ensure_disk_space(self.store.media_dir, total)
            staged_path = self.store.create_staging_path()
            with staged_path.open("wb") as handle:
                async for chunk in streamed.body:
                    if cancel_event.is_set():
                        raise asyncio.CancelledError
                    next_copied = copied + len(chunk)
                    should_check_disk = copied == 0 or next_copied // (64 * 1024 * 1024) > copied // (
                        64 * 1024 * 1024
                    )
                    if should_check_disk:
                        _ensure_disk_space(self.store.media_dir, max(len(chunk), (total or next_copied) - copied))
                    handle.write(chunk)
                    copied = next_copied
                    if should_check_disk:
                        self._log(
                            "info", "Gallery save progress", result.job_id,
                            details={"control_id": widget.control_id, "bytes_copied": copied, "total_bytes": total},
                        )
                    self.store.put_save_request(
                        result.job_id, widget.control_id, "saving", message="Saving output to Gallery.",
                        bytes_copied=copied, total_bytes=total,
                    )
            settings = _generation_settings(manifest, widget, result, datetime.now(UTC), streamed.media_type)
            return self.store.save_staged_output(
                CapturedGalleryOutput(
                    idempotency_key=idempotency_key, created_at=datetime.now(UTC),
                    workflow_id=manifest.workflow_id, workflow_title=manifest.workflow_title,
                    job_id=result.job_id, control_id=widget.control_id, output_id=widget.output_id,
                    node_id=widget.node_id, widget_title=widget.widget_title,
                    kind=widget.media_kind if prefer_declared_kind else _classify_media_kind(item, bucket),
                    staged_path=staged_path,
                    source_filename=filename, source_mime_type=streamed.media_type,
                    extension=_optional_str(item.get("extension")) or Path(filename).suffix,
                    size_bytes=copied, width=_optional_int(item.get("width")), height=_optional_int(item.get("height")),
                    duration_seconds=_optional_float(item.get("duration_seconds")), fps=_optional_float(item.get("fps")),
                    generation_settings=settings,
                )
            )
        finally:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
            await _close_async_iterator(streamed.body)

    def _notify_items_changed(self, job_id: str) -> None:
        if self.on_items_changed is not None:
            self.on_items_changed(job_id, self.store.items_for_job(job_id))

    def _log(self, level: str, message: str, job_id: str, *, details: dict[str, Any]) -> None:
        if self.log_store is not None:
            self.log_store.add(level, message, "gallery.capture", job_id=job_id, details=details)  # type: ignore[arg-type]


class GalleryUnavailable(ValueError):
    pass


def build_run_submission_snapshot(
    *,
    package: WorkflowPackage,
    inputs: dict[str, Any],
    output_preferences_snapshot: dict[str, OutputPreference] | None,
) -> RunSubmissionSnapshot:
    outputs_by_id = {output.id: output for output in package.outputs}
    output_widgets: list[GalleryOutputWidgetSnapshot] = []
    valid_ids: set[str] = set()
    for section in package.dashboard.sections:
        for control in section.controls:
            if control.type not in {"display_image", "display_audio", "display_video", "display_file", "result_image"} or not control.output_id:
                continue
            output = outputs_by_id.get(control.output_id)
            if output is None:
                continue
            valid_ids.add(control.id)
            output_widgets.append(
                GalleryOutputWidgetSnapshot(
                    control_id=control.id, output_id=output.id, node_id=output.node_id,
                    widget_title=control.label or output.label, media_kind=output.kind or output.type,
                )
            )
    clean_preferences = {
        control_id: preference
        for control_id, preference in (output_preferences_snapshot or {}).items()
        if control_id in valid_ids
    }
    inputs_by_id = {item.id: item for item in package.inputs}
    controls_by_input = {
        control.input_id: (control.label, control.type)
        for section in package.dashboard.sections for control in section.controls if control.input_id
    }
    snapshots = []
    sanitized_values: dict[str, Any] = {}
    for input_id, value in inputs.items():
        input_def = inputs_by_id.get(input_id)
        label, control_type = controls_by_input.get(
            input_id, (input_def.label if input_def else input_id, input_def.control if input_def else "unknown")
        )
        safe_value = _safe_input_setting_value(value, control_type)
        sanitized_values[input_id] = safe_value
        snapshots.append(GalleryInputSnapshot(input_id=input_id, label=label, control_type=control_type, value=safe_value))
    return RunSubmissionSnapshot(
        workflow_id=package.metadata.id, workflow_title=workflow_package_display_name(package),
        dashboard_version=package.dashboard.version,
        values=sanitized_values,
        output_preferences=clean_preferences, output_widgets=output_widgets, inputs=snapshots,
    )


def _matching_output_items(
    result: JobResult,
    widget: GalleryOutputWidgetSnapshot,
    *,
    prefer_declared_kind: bool = False,
) -> list[tuple[str, dict[str, Any]]]:
    matched: list[tuple[str, dict[str, Any]]] = []
    for output in result.outputs:
        if str(output.get("node_id") or "") != widget.node_id:
            continue
        payload = output.get("output")
        if not isinstance(payload, dict):
            continue
        for bucket in MEDIA_BUCKETS:
            items = payload.get(bucket)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and (
                    prefer_declared_kind or _classify_media_kind(item, bucket) == widget.media_kind
                ):
                    matched.append((bucket, item))
    return matched


def _classify_media_kind(item: dict[str, Any], bucket: str) -> str:
    explicit = item.get("kind") or item.get("type")
    if explicit in MEDIA_KINDS:
        return str(explicit)
    mime_type = str(item.get("mime_type") or item.get("content_type") or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type and mime_type != "application/octet-stream":
        return "file"
    suffix = Path(str(item.get("filename") or "")).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image"
    if suffix in {".wav", ".mp3", ".flac", ".ogg", ".m4a"}:
        return "audio"
    if suffix in {".mp4", ".mov", ".webm", ".mkv"}:
        return "video"
    if suffix:
        return "file"
    bucket_kind = {"images": "image", "gifs": "image", "audio": "audio", "video": "video", "videos": "video", "files": "file", "text": "file"}.get(bucket)
    return bucket_kind or "file"


def _generation_settings(
    snapshot: RunSubmissionSnapshot,
    widget: GalleryOutputWidgetSnapshot,
    result: JobResult,
    created_at: datetime,
    mime_type: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": GALLERY_SCHEMA_VERSION, "workflow_id": snapshot.workflow_id,
        "workflow_title": snapshot.workflow_title, "job_id": result.job_id,
        "control_id": widget.control_id, "output_id": widget.output_id, "node_id": widget.node_id,
        "widget_title": widget.widget_title, "created_at": created_at.astimezone(UTC).isoformat(),
        "dashboard_version": snapshot.dashboard_version,
        "settings": {item.label: item.value for item in snapshot.inputs},
        "submitted_values": snapshot.values, "mime_type": mime_type,
    }


def _inspect_image_file(path: Path, thumbnail_path: Path) -> tuple[str | None, int | None, int | None, bool]:
    try:
        with Image.open(path) as image:
            width, height = image.size
            mime_type = Image.MIME.get(image.format or "")
            thumbnail = image.copy()
            thumbnail.thumbnail(THUMBNAIL_SIZE)
            if thumbnail.mode not in {"RGB", "RGBA"}:
                thumbnail = thumbnail.convert("RGB")
            thumbnail.save(thumbnail_path, format="WEBP")
            return mime_type, width, height, True
    except (UnidentifiedImageError, OSError, ValueError):
        return None, None, None, False


def _safe_setting_value(value: Any) -> Any:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        if _looks_like_local_path(value):
            return {"filename": _safe_basename(value), "redacted": "local_path"}
        return sanitize_text(_redact_url_or_token_string(value))[:10_000]
    if isinstance(value, list):
        return [_safe_setting_value(item) for item in value[:50]]
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _is_sensitive_key(key) else _safe_setting_value(item)
            for key, item in list(value.items())[:50] if isinstance(key, str)
        }
    return str(value)[:300]


def _safe_input_setting_value(value: Any, control_type: str) -> Any:
    return "[redacted]" if control_type == "api_credential" else _safe_setting_value(value)


def _redact_url_or_token_string(value: str) -> str:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        try:
            parts = urlsplit(value)
            query = urlencode([(key, "[redacted]" if _is_sensitive_key(key) else val) for key, val in parse_qsl(parts.query, keep_blank_values=True)])
            return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
        except ValueError:
            pass
    return re.sub(r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|auth|authorization|secret|password)=)[^&#\s]+", r"\1[redacted]", value)


def _looks_like_local_path(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith(("file://", "/", "~/", "~\\")) or bool(re.match(r"^[A-Za-z]:[\\/]", stripped))


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", key.casefold())
    return any(value in normalized for value in ("apikey", "accesstoken", "authorization", "bearertoken", "credential", "secret", "password", "token", "signedurl"))


def _engine_output_type(item: dict[str, Any]) -> str:
    output_type = item.get("output_type")
    if isinstance(output_type, str) and output_type:
        return output_type
    legacy = item.get("type")
    return legacy if isinstance(legacy, str) and legacy and legacy not in MEDIA_KINDS else "output"


def _safe_extension(value: str) -> str | None:
    extension = value.lower().strip()
    if extension and not extension.startswith("."):
        extension = f".{extension}"
    return extension if re.fullmatch(r"\.[a-z0-9][a-z0-9._+-]{0,15}", extension) else None


def _safe_basename(value: str) -> str:
    return Path(value.replace("\\", "/")).name


def _slugify(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", value.lower())).strip("-")[:48] or "noofy-output"


def _hash_idempotency_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _capture_idempotency_key(
    *, job_id: str, control_id: str, output_id: str, node_id: str, item_index: int,
    filename: str, subfolder: str, output_type: str,
) -> str:
    return "|".join((job_id, control_id, output_id, node_id, str(item_index), _safe_basename(filename), _safe_basename(subfolder), _safe_basename(output_type) or "output"))


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _loads_dict(value: str | None) -> dict[str, Any]:
    try:
        loaded = json.loads(value or "{}")
        return loaded if isinstance(loaded, dict) else {}
    except json.JSONDecodeError:
        return {}


def _loads_list(value: str | None) -> list[str]:
    try:
        loaded = json.loads(value or "[]")
        return [item for item in loaded if isinstance(item, str)] if isinstance(loaded, list) else []
    except json.JSONDecodeError:
        return []


def _ensure_disk_space(directory: Path, required_bytes: int) -> None:
    if required_bytes <= 0:
        return
    if shutil.disk_usage(directory).free < required_bytes:
        raise OSError("Not enough disk space to save this Gallery output.")


async def _close_async_iterator(body: Any) -> None:
    if hasattr(body, "aclose"):
        with contextlib.suppress(Exception):
            await body.aclose()
