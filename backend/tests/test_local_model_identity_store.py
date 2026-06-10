from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.diagnostics import LogStore
from app.workflows.model_identity_store import (
    LocalModelIdentityContext,
    LocalModelIdentityStore,
)


def _context(relative_path: str = "checkpoints/demo.safetensors") -> LocalModelIdentityContext:
    return LocalModelIdentityContext(
        root_type="noofy_models",
        root_identifier="/models-a",
        relative_path=relative_path,
    )


def test_store_creates_schema_and_reuses_valid_hash(tmp_path: Path) -> None:
    path = tmp_path / "models" / "checkpoints" / "demo.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")

    store.remember_hash(path, _context(), "a" * 64)

    assert (tmp_path / "identity" / "cache.db").exists()
    assert store.get_valid_hash(path, _context()) == "a" * 64


def test_store_invalidates_size_changes(tmp_path: Path) -> None:
    path = tmp_path / "models" / "checkpoints" / "demo.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    store.remember_hash(path, _context(), "a" * 64)

    path.write_bytes(b"larger-model")

    assert store.get_valid_hash(path, _context()) is None


def test_store_invalidates_modified_time_changes(tmp_path: Path) -> None:
    path = tmp_path / "models" / "checkpoints" / "demo.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    store.remember_hash(path, _context(), "a" * 64)
    stat = path.stat()

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert store.get_valid_hash(path, _context()) is None


def test_store_reuses_after_root_move_by_root_type_and_relative_path(tmp_path: Path) -> None:
    old_path = tmp_path / "old-models" / "checkpoints" / "demo.safetensors"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(b"model")
    old_stat = old_path.stat()
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    store.remember_hash(path=old_path, context=_context(), sha256="a" * 64)
    new_path = tmp_path / "new-models" / "checkpoints" / "demo.safetensors"
    new_path.parent.mkdir(parents=True)
    new_path.write_bytes(old_path.read_bytes())
    os.utime(new_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))

    moved_context = LocalModelIdentityContext(
        root_type="noofy_models",
        root_identifier="/models-b",
        relative_path="checkpoints/demo.safetensors",
    )

    assert store.get_valid_hash(new_path, moved_context) == "a" * 64


def test_store_upserts_safely_from_multiple_threads(tmp_path: Path) -> None:
    path = tmp_path / "models" / "checkpoints" / "demo.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(
            executor.map(
                lambda _: store.remember_hash(path, _context(), "b" * 64),
                range(12),
            )
        )

    assert store.get_valid_hash(path, _context()) == "b" * 64


def test_store_quarantines_corrupt_database_without_blocking_verification(tmp_path: Path) -> None:
    db_path = tmp_path / "identity" / "cache.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a sqlite database")
    log_store = LogStore()

    store = LocalModelIdentityStore(db_path, log_store=log_store)
    path = tmp_path / "models" / "checkpoints" / "demo.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    store.remember_hash(path, _context(), "c" * 64)

    assert store.get_valid_hash(path, _context()) == "c" * 64
    assert list(db_path.parent.glob("cache.db.corrupt.*"))
    assert log_store.list_events(level="warning").events


def test_cache_hit_events_are_logged_once_per_path(tmp_path: Path) -> None:
    """Repeated availability polls must not flood the bounded diagnostics
    store with one cache-hit event per model per poll."""
    path = tmp_path / "models" / "checkpoints" / "demo.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    log_store = LogStore()
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db", log_store=log_store)
    store.remember_hash(path, _context(), "a" * 64)

    for _ in range(5):
        assert store.get_valid_hash(path, _context()) == "a" * 64

    hit_events = [
        event
        for event in log_store.list_events(limit=100).events
        if event.message == "Local model hash cache hit"
    ]
    assert len(hit_events) == 1


def test_cache_hit_touch_writes_are_throttled(tmp_path: Path) -> None:
    """last_used_at is refreshed only after the refresh window, not per hit."""
    import sqlite3
    from datetime import UTC, datetime, timedelta

    path = tmp_path / "models" / "checkpoints" / "demo.safetensors"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"model")
    db_path = tmp_path / "identity" / "cache.db"
    store = LocalModelIdentityStore(db_path)
    store.remember_hash(path, _context(), "a" * 64)

    def read_last_used() -> str:
        with sqlite3.connect(db_path) as conn:
            return conn.execute(
                "SELECT last_used_at FROM local_model_identities"
            ).fetchone()[0]

    def write_last_used(value: str) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE local_model_identities SET last_used_at = ?", (value,))
            conn.commit()

    fresh = datetime.now(UTC).isoformat()
    write_last_used(fresh)
    assert store.get_valid_hash(path, _context()) == "a" * 64
    assert read_last_used() == fresh  # fresh timestamp: no write

    stale = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    write_last_used(stale)
    assert store.get_valid_hash(path, _context()) == "a" * 64
    assert read_last_used() != stale  # aged past the window: refreshed
