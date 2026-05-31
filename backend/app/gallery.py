from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import sqlite3
import tempfile
import threading
import contextlib
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any, Callable

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from app.engine.models import JobResult
from app.workflows.package import WorkflowPackage
from app.workflows.library import workflow_package_display_name

GALLERY_SCHEMA_VERSION = 1
THUMBNAIL_SIZE = (512, 512)


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
    created_at: str
    workflow_id: str
    workflow_title: str
    job_id: str
    control_id: str
    output_id: str
    node_id: str
    widget_title: str
    image_url: str
    thumbnail_url: str | None = None
    image_rel_path: str
    thumbnail_rel_path: str | None = None
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    favorite: bool = False
    file_state: str = "available"
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    technical_metadata: dict[str, Any] = Field(default_factory=dict)
    schema_version: int = GALLERY_SCHEMA_VERSION


class GalleryResponse(BaseModel):
    images: list[GalleryItem]
    total: int


@dataclass(frozen=True)
class CapturedGalleryImage:
    idempotency_key: str
    created_at: datetime
    workflow_id: str
    workflow_title: str
    job_id: str
    control_id: str
    output_id: str
    node_id: str
    widget_title: str
    data: bytes
    source_filename: str
    source_mime_type: str | None
    generation_settings: dict[str, Any]
    technical_metadata: dict[str, Any]


class GalleryStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.images_dir = root_dir / "images"
        self.thumbnails_dir = root_dir / "thumbnails"
        self.db_path = root_dir / "gallery.db"
        self._lock = threading.RLock()
        self._ensure_ready()

    def _ensure_ready(self) -> None:
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        self._cleanup_temp_files()
        with closing(self._connect()) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            existing = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if existing is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (GALLERY_SCHEMA_VERSION,))
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gallery_items (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    workflow_id TEXT NOT NULL,
                    workflow_title TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    control_id TEXT NOT NULL,
                    output_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    widget_title TEXT NOT NULL,
                    image_rel_path TEXT NOT NULL,
                    thumbnail_rel_path TEXT,
                    mime_type TEXT,
                    width INTEGER,
                    height INTEGER,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    file_state TEXT NOT NULL DEFAULT 'available',
                    generation_settings_json TEXT NOT NULL,
                    technical_metadata_json TEXT,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_gallery_created_at ON gallery_items(created_at DESC)")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _cleanup_temp_files(self) -> None:
        for directory in (self.images_dir, self.thumbnails_dir):
            if not directory.exists():
                continue
            for path in directory.glob("*.tmp"):
                try:
                    path.unlink()
                except OSError:
                    pass

    def list_items(self) -> GalleryResponse:
        with self._lock, closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM gallery_items ORDER BY created_at DESC").fetchall()
            items = []
            for row in rows:
                try:
                    items.append(self._item_from_row(row))
                except Exception:
                    continue
            return GalleryResponse(images=items, total=len(items))

    def get_item(self, item_id: str) -> GalleryItem | None:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM gallery_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return None
            try:
                return self._item_from_row(row)
            except Exception:
                return None

    def image_path(self, item_id: str, *, thumbnail: bool = False) -> Path | None:
        item = self.get_item(item_id)
        if item is None:
            return None
        rel = item.thumbnail_rel_path if thumbnail else item.image_rel_path
        if not rel:
            return None
        return self._resolve_rel_path(rel)

    def set_favorite(self, item_id: str, favorite: bool) -> GalleryItem | None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute("UPDATE gallery_items SET favorite = ? WHERE id = ?", (1 if favorite else 0, item_id))
            conn.commit()
            if cursor.rowcount == 0:
                return None
        return self.get_item(item_id)

    def delete_item(self, item_id: str) -> bool:
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM gallery_items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                return False
            image_path = self._resolve_rel_path(row["image_rel_path"])
            thumb_path = self._resolve_rel_path(row["thumbnail_rel_path"]) if row["thumbnail_rel_path"] else None
            tombstones: list[tuple[Path, Path]] = []
            try:
                conn.execute("BEGIN IMMEDIATE")
                for path in (image_path, thumb_path):
                    if path is None or not path.exists():
                        continue
                    tombstone = path.with_name(f".{path.name}.{item_id}.delete.tmp")
                    tombstone.unlink(missing_ok=True)
                    path.replace(tombstone)
                    tombstones.append((path, tombstone))

                conn.execute("DELETE FROM gallery_items WHERE id = ?", (item_id,))
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
                try:
                    tombstone.unlink(missing_ok=True)
                except OSError as exc:
                    raise ValueError(f"Gallery file could not be removed: {tombstone.name}") from exc
            return True

    def get_item_by_idempotency_key(self, idempotency_key: str) -> GalleryItem | None:
        hashed_key = _hash_idempotency_key(idempotency_key)
        with self._lock, closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM gallery_items WHERE idempotency_key = ?",
                (hashed_key,),
            ).fetchone()
            if row is None:
                return None
            return self._item_from_row(row)

    def save_image(self, image: CapturedGalleryImage) -> GalleryItem:
        hashed_key = _hash_idempotency_key(image.idempotency_key)
        item_id = hashed_key[:24]
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM gallery_items WHERE idempotency_key = ?",
                (hashed_key,),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return self._item_from_row(existing)

            ext = _extension_for_image(image.source_filename, image.source_mime_type, image.data)
            slug = _slugify(image.workflow_title or image.widget_title or "noofy-output")
            local_stamp = image.created_at.astimezone().strftime("%Y-%m-%d_%H-%M-%S")
            base = f"{local_stamp}_{slug}_{item_id[:6]}"
            image_name = self._unique_filename(self.images_dir, base, ext)
            thumb_name = self._unique_filename(self.thumbnails_dir, base, ".webp")
            image_rel = f"images/{image_name}"
            thumb_rel = f"thumbnails/{thumb_name}"
            image_path = self.images_dir / image_name
            thumb_path = self.thumbnails_dir / thumb_name

            mime_type, width, height, thumb_ok = _inspect_and_thumbnail(image.data, thumb_path)
            if mime_type is None:
                mime_type = image.source_mime_type or mimetypes.guess_type(image_name)[0] or "application/octet-stream"
            generation_settings = {
                **image.generation_settings,
                "mime_type": mime_type,
                "width": width,
                "height": height,
            }

            _atomic_write_bytes(image_path, image.data)
            file_state = "available" if thumb_ok else "degraded"
            if not thumb_ok:
                thumb_rel = None

            try:
                conn.execute(
                    """
                    INSERT INTO gallery_items (
                        id, idempotency_key, created_at, workflow_id, workflow_title, job_id,
                        control_id, output_id, node_id, widget_title, image_rel_path,
                        thumbnail_rel_path, mime_type, width, height, favorite, file_state,
                        generation_settings_json, technical_metadata_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        hashed_key,
                        image.created_at.astimezone(UTC).isoformat(),
                        image.workflow_id,
                        image.workflow_title,
                        image.job_id,
                        image.control_id,
                        image.output_id,
                        image.node_id,
                        image.widget_title,
                        image_rel,
                        thumb_rel,
                        mime_type,
                        width,
                        height,
                        file_state,
                        json.dumps(generation_settings, sort_keys=True),
                        json.dumps(image.technical_metadata, sort_keys=True),
                        GALLERY_SCHEMA_VERSION,
                    ),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                for path in (image_path, thumb_path if thumb_ok else None):
                    if path is not None:
                        path.unlink(missing_ok=True)
                existing = conn.execute(
                    "SELECT * FROM gallery_items WHERE idempotency_key = ?",
                    (hashed_key,),
                ).fetchone()
                if existing is not None:
                    return self._item_from_row(existing)
                raise
            except Exception:
                conn.rollback()
                for path in (image_path, thumb_path if thumb_ok else None):
                    if path is not None:
                        path.unlink(missing_ok=True)
                raise
            conn.commit()
            row = conn.execute("SELECT * FROM gallery_items WHERE id = ?", (item_id,)).fetchone()
            return self._item_from_row(row)

    def _unique_filename(self, directory: Path, base: str, ext: str) -> str:
        candidate = f"{base}{ext}"
        if not (directory / candidate).exists():
            return candidate
        for counter in range(1, 1000):
            suffix = hashlib.sha1(f"{base}-{counter}".encode("utf-8")).hexdigest()[:5]
            candidate = f"{base}-{suffix}{ext}"
            if not (directory / candidate).exists():
                return candidate
        raise ValueError("Could not allocate a unique gallery filename.")

    def _resolve_rel_path(self, rel_path: str) -> Path:
        candidate = (self.root_dir / rel_path).resolve()
        root = self.root_dir.resolve()
        if root not in candidate.parents and candidate != root:
            raise ValueError("Invalid gallery file path.")
        return candidate

    def _item_from_row(self, row: sqlite3.Row) -> GalleryItem:
        image_path = self._resolve_rel_path(row["image_rel_path"])
        thumb_rel = row["thumbnail_rel_path"]
        thumb_path = self._resolve_rel_path(thumb_rel) if thumb_rel else None
        file_state = row["file_state"]
        if not image_path.exists():
            file_state = "missing"
        elif thumb_rel and (thumb_path is None or not thumb_path.exists()):
            file_state = "degraded"

        return GalleryItem(
            id=row["id"],
            created_at=row["created_at"],
            workflow_id=row["workflow_id"],
            workflow_title=row["workflow_title"],
            job_id=row["job_id"],
            control_id=row["control_id"],
            output_id=row["output_id"],
            node_id=row["node_id"],
            widget_title=row["widget_title"],
            image_url=f"/api/gallery/{row['id']}/image",
            thumbnail_url=f"/api/gallery/{row['id']}/thumbnail" if thumb_rel else None,
            image_rel_path=row["image_rel_path"],
            thumbnail_rel_path=thumb_rel,
            mime_type=row["mime_type"],
            width=row["width"],
            height=row["height"],
            favorite=bool(row["favorite"]),
            file_state=file_state,
            generation_settings=_loads_dict(row["generation_settings_json"]),
            technical_metadata=_loads_dict(row["technical_metadata_json"]),
            schema_version=row["schema_version"],
        )


class GalleryCaptureService:
    def __init__(self, store: GalleryStore) -> None:
        self.store = store

    async def save_completed_job_outputs(
        self,
        *,
        result: JobResult,
        snapshot: RunSubmissionSnapshot | None,
        fetch_output: Callable[[str, str, str, str], Any],
    ) -> list[GalleryItem]:
        if snapshot is None or result.status != "completed":
            return []

        enabled = {
            widget.control_id: widget
            for widget in snapshot.output_widgets
            if widget.media_kind == "image"
            and snapshot.output_preferences.get(widget.control_id, OutputPreference()).auto_save
        }
        if not enabled:
            return []

        saved: list[GalleryItem] = []
        widgets_by_node: dict[str, list[GalleryOutputWidgetSnapshot]] = {}
        for widget in enabled.values():
            widgets_by_node.setdefault(widget.node_id, []).append(widget)

        for output in result.outputs:
            if not isinstance(output, dict):
                continue
            node_id = output.get("node_id")
            if not isinstance(node_id, str) or node_id not in widgets_by_node:
                continue
            payload = output.get("output")
            if not isinstance(payload, dict):
                continue
            images = payload.get("images")
            if not isinstance(images, list):
                continue
            for widget in widgets_by_node[node_id]:
                for index, image in enumerate(images):
                    if not isinstance(image, dict):
                        continue
                    filename = _safe_basename(str(image.get("filename") or f"image-{index}.png"))
                    subfolder = str(image.get("subfolder") or "")
                    output_type = str(image.get("output_type") or image.get("type") or "output")
                    idempotency = _capture_idempotency_key(
                        job_id=result.job_id,
                        control_id=widget.control_id,
                        output_id=widget.output_id,
                        node_id=widget.node_id,
                        image_index=index,
                        filename=filename,
                        subfolder=subfolder,
                        output_type=output_type,
                    )
                    existing = self.store.get_item_by_idempotency_key(idempotency)
                    if existing is not None:
                        saved.append(existing)
                        continue
                    data, media_type = await fetch_output(result.job_id, filename, subfolder, output_type)
                    created_at = datetime.now(UTC)
                    settings = _generation_settings(
                        snapshot=snapshot,
                        widget=widget,
                        result=result,
                        created_at=created_at,
                        mime_type=media_type,
                    )
                    saved.append(
                        self.store.save_image(
                            CapturedGalleryImage(
                                idempotency_key=idempotency,
                                created_at=created_at,
                                workflow_id=snapshot.workflow_id,
                                workflow_title=snapshot.workflow_title,
                                job_id=result.job_id,
                                control_id=widget.control_id,
                                output_id=widget.output_id,
                                node_id=widget.node_id,
                                widget_title=widget.widget_title,
                                data=data,
                                source_filename=filename,
                                source_mime_type=media_type,
                                generation_settings=settings,
                                technical_metadata={
                                    "source_filename": _safe_basename(filename),
                                    "output_type": output_type,
                                    "image_index": index,
                                },
                            )
                        )
                    )
        return saved


def build_run_submission_snapshot(
    *,
    package: WorkflowPackage,
    inputs: dict[str, Any],
    output_preferences_snapshot: dict[str, OutputPreference] | None,
) -> RunSubmissionSnapshot:
    output_preferences_snapshot = output_preferences_snapshot or {}
    outputs_by_id = {output.id: output for output in package.outputs}
    output_widgets: list[GalleryOutputWidgetSnapshot] = []
    valid_output_control_ids: set[str] = set()
    for section in package.dashboard.sections:
        for control in section.controls:
            if control.type not in {"display_image", "display_audio", "result_image"} or not control.output_id:
                continue
            output = outputs_by_id.get(control.output_id)
            if output is None:
                continue
            valid_output_control_ids.add(control.id)
            output_widgets.append(
                GalleryOutputWidgetSnapshot(
                    control_id=control.id,
                    output_id=output.id,
                    node_id=output.node_id,
                    widget_title=control.label or output.label,
                    media_kind=output.kind or output.type,
                )
            )

    clean_preferences: dict[str, OutputPreference] = {}
    for control_id, preference in output_preferences_snapshot.items():
        if control_id in valid_output_control_ids:
            clean_preferences[control_id] = preference

    inputs_by_id = {input_def.id: input_def for input_def in package.inputs}
    control_by_input_id: dict[str, tuple[str, str]] = {}
    for section in package.dashboard.sections:
        for control in section.controls:
            if control.input_id:
                control_by_input_id[control.input_id] = (control.label, control.type)

    input_snapshots: list[GalleryInputSnapshot] = []
    for input_id, value in inputs.items():
        input_def = inputs_by_id.get(input_id)
        label, control_type = control_by_input_id.get(
            input_id,
            (input_def.label if input_def is not None else input_id, input_def.control if input_def is not None else "unknown"),
        )
        input_snapshots.append(
            GalleryInputSnapshot(
                input_id=input_id,
                label=label,
                control_type=control_type,
                value=_safe_setting_value(value),
            )
        )

    return RunSubmissionSnapshot(
        workflow_id=package.metadata.id,
        workflow_title=workflow_package_display_name(package),
        dashboard_version=package.dashboard.version,
        values={key: _safe_setting_value(value) for key, value in inputs.items()},
        output_preferences=clean_preferences,
        output_widgets=output_widgets,
        inputs=input_snapshots,
    )


def _generation_settings(
    *,
    snapshot: RunSubmissionSnapshot,
    widget: GalleryOutputWidgetSnapshot,
    result: JobResult,
    created_at: datetime,
    mime_type: str | None,
) -> dict[str, Any]:
    settings_by_label = {input_snapshot.label: input_snapshot.value for input_snapshot in snapshot.inputs}
    return {
        "schema_version": GALLERY_SCHEMA_VERSION,
        "workflow_id": snapshot.workflow_id,
        "workflow_title": snapshot.workflow_title,
        "job_id": result.job_id,
        "control_id": widget.control_id,
        "output_id": widget.output_id,
        "node_id": widget.node_id,
        "widget_title": widget.widget_title,
        "created_at": created_at.astimezone(UTC).isoformat(),
        "dashboard_version": snapshot.dashboard_version,
        "settings": settings_by_label,
        "submitted_values": snapshot.values,
        "mime_type": mime_type,
    }


def _safe_setting_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, str):
            return _safe_setting_string(value)
        return value
    if isinstance(value, list):
        return [_safe_setting_value(item) for item in value[:50]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _is_sensitive_key(key):
                safe[key] = "[redacted]"
            elif isinstance(key, str):
                safe[key] = _safe_setting_value(item)
        return safe
    return str(value)


def _safe_setting_string(value: str) -> Any:
    if _looks_like_local_path(value):
        return {"filename": _safe_basename(value), "redacted": "local_path"}
    return _redact_url_or_token_string(value)


def _redact_url_or_token_string(value: str) -> str:
    if _looks_like_url(value):
        try:
            parts = urlsplit(value)
            query = urlencode(
                [
                    (key, "[redacted]" if _is_sensitive_key(key) else val)
                    for key, val in parse_qsl(parts.query, keep_blank_values=True)
                ]
            )
            return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
        except ValueError:
            pass
    return re.sub(
        r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|auth|authorization|secret|password)=)[^&#\s]+",
        r"\1[redacted]",
        value,
    )


def _looks_like_url(value: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value))


def _looks_like_local_path(value: str) -> bool:
    stripped = value.strip()
    return (
        stripped.startswith(("file://", "/", "~/", "~\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", stripped))
    )


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


def _extension_for_image(filename: str, mime_type: str | None, data: bytes) -> str:
    mapping = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    if mime_type in mapping:
        return mapping[mime_type]
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    detected = _detect_mime_type(data)
    return mapping.get(detected or "", ".png")


def _inspect_and_thumbnail(data: bytes, thumb_path: Path) -> tuple[str | None, int | None, int | None, bool]:
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            mime_type = Image.MIME.get(image.format or "")
            thumbnail = image.copy()
            thumbnail.thumbnail(THUMBNAIL_SIZE)
            if thumbnail.mode not in {"RGB", "RGBA"}:
                thumbnail = thumbnail.convert("RGB")
            _atomic_save_pillow(thumb_path, thumbnail, "WEBP")
            return mime_type, width, height, True
    except (UnidentifiedImageError, OSError, ValueError):
        return _detect_mime_type(data), None, None, False


def _detect_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_save_pillow(target: Path, image: Image.Image, format_name: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(fd)
    try:
        image.save(tmp_path, format=format_name)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower())
    slug = re.sub(r"-+", "-", slug).strip("-")
    return (slug or "noofy-output")[:48].strip("-") or "noofy-output"


def _safe_basename(value: str) -> str:
    return Path(value.replace("\\", "/")).name


def _hash_idempotency_key(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def _capture_idempotency_key(
    *,
    job_id: str,
    control_id: str,
    output_id: str,
    node_id: str,
    image_index: int,
    filename: str,
    subfolder: str,
    output_type: str,
) -> str:
    return "|".join(
        [
            job_id,
            control_id,
            output_id,
            node_id,
            str(image_index),
            _safe_basename(filename),
            _safe_basename(subfolder) if subfolder else "",
            _safe_basename(output_type) if output_type else "output",
        ]
    )


def _loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
