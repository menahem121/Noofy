from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.diagnostics import DiagnosticsSink
from app.workflows.store_paths import safe_store_segment

LOCAL_MODEL_IDENTITY_SCHEMA_VERSION = 1

ModelRootType = Literal["noofy_models", "external_comfyui_models"]


@dataclass(frozen=True)
class LocalModelIdentityContext:
    root_type: ModelRootType
    root_identifier: str
    relative_path: str


class LocalModelIdentityStore:
    def __init__(
        self,
        db_path: Path,
        *,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.db_path = db_path
        self.log_store = log_store
        self._lock = threading.RLock()
        self._ensure_ready()

    def get_valid_hash(
        self,
        path: Path,
        context: LocalModelIdentityContext,
    ) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        resolved_path = _resolved_path_key(path)
        now = _utc_now_iso()
        try:
            with self._lock, closing(self._connect()) as conn:
                row = conn.execute(
                    "SELECT * FROM local_model_identities WHERE resolved_path = ?",
                    (resolved_path,),
                ).fetchone()
                if row is not None:
                    if _strict_metadata_matches(row, stat):
                        self._touch(conn, int(row["id"]), now)
                        self._record_cache_event(
                            "debug",
                            "Local model hash cache hit",
                            context,
                            path,
                            details={"lookup": "resolved_path"},
                        )
                        return str(row["sha256"])
                    self._delete_row(conn, int(row["id"]))
                    self._record_cache_event(
                        "info",
                        "Stale local model hash cache record invalidated",
                        context,
                        path,
                        details={"reason": _stale_reason(row, stat)},
                    )

                fallback_rows = conn.execute(
                    """
                    SELECT * FROM local_model_identities
                    WHERE root_type = ?
                      AND relative_path = ?
                      AND size_bytes = ?
                      AND mtime_ns = ?
                    ORDER BY last_used_at DESC, scanned_at DESC
                    """,
                    (
                        context.root_type,
                        context.relative_path,
                        stat.st_size,
                        _stat_mtime_ns(stat),
                    ),
                ).fetchall()
                for fallback in fallback_rows:
                    sha256 = str(fallback["sha256"])
                    self._move_record_to_path(
                        conn,
                        int(fallback["id"]),
                        path=path,
                        context=context,
                        stat=stat,
                        now=now,
                    )
                    self._record_cache_event(
                        "debug",
                        "Local model hash cache hit",
                        context,
                        path,
                        details={"lookup": "root_relative_path"},
                    )
                    return sha256
        except sqlite3.DatabaseError as exc:
            self._record_store_failure("Local model hash cache lookup failed", exc)
        return None

    def remember_hash(
        self,
        path: Path,
        context: LocalModelIdentityContext,
        sha256: str,
    ) -> None:
        normalized_sha = _normalize_sha256(sha256)
        if normalized_sha is None:
            return
        try:
            stat = path.stat()
        except OSError:
            return
        resolved_path = _resolved_path_key(path)
        now = _utc_now_iso()
        try:
            with self._lock, closing(self._connect()) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    DELETE FROM local_model_identities
                    WHERE root_type = ?
                      AND relative_path = ?
                      AND resolved_path != ?
                    """,
                    (context.root_type, context.relative_path, resolved_path),
                )
                conn.execute(
                    """
                    INSERT INTO local_model_identities (
                        resolved_path, root_type, root_identifier, relative_path,
                        sha256, size_bytes, mtime_ns, device_id, inode,
                        scanned_at, last_used_at, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(resolved_path) DO UPDATE SET
                        root_type = excluded.root_type,
                        root_identifier = excluded.root_identifier,
                        relative_path = excluded.relative_path,
                        sha256 = excluded.sha256,
                        size_bytes = excluded.size_bytes,
                        mtime_ns = excluded.mtime_ns,
                        device_id = excluded.device_id,
                        inode = excluded.inode,
                        scanned_at = excluded.scanned_at,
                        last_used_at = excluded.last_used_at,
                        schema_version = excluded.schema_version
                    """,
                    (
                        resolved_path,
                        context.root_type,
                        context.root_identifier,
                        context.relative_path,
                        normalized_sha,
                        stat.st_size,
                        _stat_mtime_ns(stat),
                        _stat_device_id(stat),
                        _stat_inode(stat),
                        now,
                        now,
                        LOCAL_MODEL_IDENTITY_SCHEMA_VERSION,
                    ),
                )
                conn.commit()
        except sqlite3.DatabaseError as exc:
            self._record_store_failure("Local model hash cache store failed", exc)

    def _ensure_ready(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._create_schema()
        except sqlite3.DatabaseError as exc:
            corrupt_path = self._quarantine_database_files()
            if self.log_store is not None:
                self.log_store.add(
                    "warning",
                    "Local model hash cache database was corrupt and has been quarantined",
                    "workflow.models.cache",
                    details={"path": str(corrupt_path), "error": str(exc)},
                )
            self._create_schema()

    def _create_schema(self) -> None:
        with self._lock, closing(self._connect()) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            existing = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    (LOCAL_MODEL_IDENTITY_SCHEMA_VERSION,),
                )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS local_model_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    resolved_path TEXT NOT NULL UNIQUE,
                    root_type TEXT NOT NULL,
                    root_identifier TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    device_id INTEGER,
                    inode INTEGER,
                    scanned_at TEXT NOT NULL,
                    last_used_at TEXT NOT NULL,
                    schema_version INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_model_identity_root_relative
                ON local_model_identities(root_type, relative_path, size_bytes, mtime_ns)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_model_identity_sha256
                ON local_model_identities(sha256)
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

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

    def _touch(self, conn: sqlite3.Connection, row_id: int, now: str) -> None:
        conn.execute(
            "UPDATE local_model_identities SET last_used_at = ? WHERE id = ?",
            (now, row_id),
        )
        conn.commit()

    def _delete_row(self, conn: sqlite3.Connection, row_id: int) -> None:
        conn.execute("DELETE FROM local_model_identities WHERE id = ?", (row_id,))
        conn.commit()

    def _move_record_to_path(
        self,
        conn: sqlite3.Connection,
        row_id: int,
        *,
        path: Path,
        context: LocalModelIdentityContext,
        stat: object,
        now: str,
    ) -> None:
        resolved_path = _resolved_path_key(path)
        conn.execute("DELETE FROM local_model_identities WHERE resolved_path = ? AND id != ?", (resolved_path, row_id))
        conn.execute(
            """
            UPDATE local_model_identities
            SET resolved_path = ?,
                root_identifier = ?,
                size_bytes = ?,
                mtime_ns = ?,
                device_id = ?,
                inode = ?,
                last_used_at = ?
            WHERE id = ?
            """,
            (
                resolved_path,
                context.root_identifier,
                stat.st_size,  # type: ignore[attr-defined]
                _stat_mtime_ns(stat),
                _stat_device_id(stat),
                _stat_inode(stat),
                now,
                row_id,
            ),
        )
        conn.commit()

    def _record_cache_event(
        self,
        level: str,
        message: str,
        context: LocalModelIdentityContext,
        path: Path,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        if self.log_store is None:
            return
        self.log_store.add(
            level,  # type: ignore[arg-type]
            message,
            "workflow.models.cache",
            details={
                "root_type": context.root_type,
                "relative_path": context.relative_path,
                "resolved_path": str(path),
                **(details or {}),
            },
        )

    def _record_store_failure(self, message: str, exc: Exception) -> None:
        if self.log_store is None:
            return
        self.log_store.add(
            "warning",
            message,
            "workflow.models.cache",
            details={"path": str(self.db_path), "error": str(exc)},
        )


def _resolved_path_key(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _strict_metadata_matches(row: sqlite3.Row, stat: object) -> bool:
    if int(row["size_bytes"]) != stat.st_size:  # type: ignore[attr-defined]
        return False
    if int(row["mtime_ns"]) != _stat_mtime_ns(stat):
        return False
    row_device = row["device_id"]
    current_device = _stat_device_id(stat)
    if row_device is not None and current_device is not None and int(row_device) != current_device:
        return False
    row_inode = row["inode"]
    current_inode = _stat_inode(stat)
    if row_inode is not None and current_inode is not None and int(row_inode) != current_inode:
        return False
    return True


def _stale_reason(row: sqlite3.Row, stat: object) -> str:
    if int(row["size_bytes"]) != stat.st_size:  # type: ignore[attr-defined]
        return "size_changed"
    if int(row["mtime_ns"]) != _stat_mtime_ns(stat):
        return "modified_time_changed"
    row_device = row["device_id"]
    current_device = _stat_device_id(stat)
    if row_device is not None and current_device is not None and int(row_device) != current_device:
        return "device_changed"
    row_inode = row["inode"]
    current_inode = _stat_inode(stat)
    if row_inode is not None and current_inode is not None and int(row_inode) != current_inode:
        return "inode_changed"
    return "metadata_changed"


def _stat_mtime_ns(stat: object) -> int:
    return int(getattr(stat, "st_mtime_ns", int(getattr(stat, "st_mtime", 0) * 1_000_000_000)))


def _stat_device_id(stat: object) -> int | None:
    value = getattr(stat, "st_dev", None)
    return int(value) if isinstance(value, int) else None


def _stat_inode(stat: object) -> int | None:
    value = getattr(stat, "st_ino", None)
    return int(value) if isinstance(value, int) else None


def _normalize_sha256(value: str) -> str | None:
    normalized = value.removeprefix("sha256:").casefold()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        return None
    return normalized
