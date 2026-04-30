"""Tests for the content-addressed model store and its rollback behavior."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

import app.runtime.model_store as model_store_module
from app.engine.diagnostics import LogStore
from app.runtime.isolation import ModelLock
from app.runtime.model_store import ModelDownloadError, ModelStore


def _model_store_paths(root: Path) -> dict[str, Path]:
    return {
        "blobs_dir": root / "blobs",
        "refs_dir": root / "refs",
        "materialized_dir": root / "materialized",
        "transactions_dir": root / "transactions",
    }


def _build_store(tmp_path: Path, downloader) -> tuple[ModelStore, LogStore]:
    log_store = LogStore()
    store = ModelStore(
        **_model_store_paths(tmp_path),
        log_store=log_store,
        downloader=downloader,
    )
    return store, log_store


def _model_lock(content: bytes, *, model_id: str = "model-1", folder: str = "checkpoints", filename: str = "model.safetensors") -> ModelLock:
    return ModelLock(
        id=model_id,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        source_urls=["https://example.invalid/model.safetensors"],
        comfyui_folder=folder,
        filename=filename,
    )


def _make_downloader(content_by_url: dict[str, bytes]):
    async def downloader(url: str, dest: Path) -> int:
        if url not in content_by_url:
            raise RuntimeError(f"unexpected url: {url}")
        data = content_by_url[url]
        dest.write_bytes(data)
        return len(data)

    return downloader


@pytest.mark.anyio
async def test_materialize_writes_blob_ref_and_link(tmp_path: Path) -> None:
    payload = b"\x00\x01\x02hello world\xff" * 8
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    result = await store.materialize(lock)

    assert result.blob_path.exists()
    assert result.blob_path.read_bytes() == payload
    assert result.size_bytes == len(payload)
    assert not result.reused_existing_blob
    # Materialized link resolves back to the blob bytes.
    assert result.materialized_path.exists()
    assert result.materialized_path.read_bytes() == payload
    # Ref json references the blob.
    ref_path = tmp_path / "refs" / "model-1.json"
    assert ref_path.exists()


@pytest.mark.anyio
async def test_materialize_is_idempotent_and_skips_redownload(tmp_path: Path) -> None:
    payload = b"abc-payload"
    lock = _model_lock(payload)
    call_count = 0

    async def counting_downloader(url: str, dest: Path) -> int:
        nonlocal call_count
        call_count += 1
        dest.write_bytes(payload)
        return len(payload)

    store, _ = _build_store(tmp_path, counting_downloader)

    first = await store.materialize(lock)
    second = await store.materialize(lock)

    assert call_count == 1
    assert second.reused_existing_blob is True
    assert second.blob_path == first.blob_path


@pytest.mark.anyio
async def test_hash_mismatch_rolls_back_transaction(tmp_path: Path) -> None:
    expected = b"correct-content"
    wrong = b"WRONG-content"
    lock = _model_lock(expected)
    # Server returns the wrong bytes -> hash mismatch must abort.
    store, log_store = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: wrong}))

    with pytest.raises(ModelDownloadError):
        await store.materialize(lock)

    # No blob, ref, or materialized link should exist.
    assert not (tmp_path / "blobs").exists() or not any((tmp_path / "blobs").rglob("*"))
    assert not (tmp_path / "refs").exists() or list((tmp_path / "refs").iterdir()) == []
    assert not (tmp_path / "materialized").exists() or not any((tmp_path / "materialized").rglob("*.safetensors"))
    # Transaction directory is cleaned up.
    txn_dir = tmp_path / "transactions"
    if txn_dir.exists():
        assert list(txn_dir.iterdir()) == []
    # An error diagnostic was emitted.
    assert any("Model download failed" in event.message for event in log_store.list_events().events)


@pytest.mark.anyio
async def test_size_mismatch_rolls_back(tmp_path: Path) -> None:
    payload = b"hello"
    lock = ModelLock(
        id="model-size",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=999,  # intentionally wrong
        source_urls=["https://example.invalid/m.safetensors"],
        comfyui_folder="checkpoints",
        filename="m.safetensors",
    )
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    with pytest.raises(ModelDownloadError):
        await store.materialize(lock)

    assert not store.has_blob(lock.sha256)


@pytest.mark.anyio
async def test_falls_back_to_next_url_on_failure(tmp_path: Path) -> None:
    payload = b"fallback-payload"
    bad_url = "https://example.invalid/primary"
    good_url = "https://example.invalid/mirror"

    async def downloader(url: str, dest: Path) -> int:
        if url == bad_url:
            raise RuntimeError("primary unreachable")
        dest.write_bytes(payload)
        return len(payload)

    lock = ModelLock(
        id="model-fb",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        source_urls=[bad_url, good_url],
        comfyui_folder="checkpoints",
        filename="m.safetensors",
    )
    store, log_store = _build_store(tmp_path, downloader)

    result = await store.materialize(lock)

    assert result.size_bytes == len(payload)
    # First URL emitted a warning, second URL succeeded.
    warnings = [event for event in log_store.list_events().events if event.level == "warning"]
    assert any("Model source URL failed" in event.message for event in warnings)


@pytest.mark.anyio
async def test_no_source_urls_raises_without_attempted_download(tmp_path: Path) -> None:
    payload = b"unused"
    lock = ModelLock(
        id="model-empty",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        source_urls=[],
        comfyui_folder="checkpoints",
        filename="m.safetensors",
    )
    store, _ = _build_store(tmp_path, _make_downloader({}))

    with pytest.raises(ModelDownloadError):
        await store.materialize(lock)


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks differ on Windows")
async def test_materialized_path_is_a_symlink_to_blob(tmp_path: Path) -> None:
    payload = b"link-target"
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    result = await store.materialize(lock)

    assert result.materialized_path.is_symlink()
    assert result.materialized_path.resolve() == result.blob_path.resolve()


@pytest.mark.anyio
async def test_rejects_materialized_path_traversal_before_download(tmp_path: Path) -> None:
    payload = b"path-traversal"
    lock = ModelLock.model_construct(
        id="path-traversal-model",
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        source_urls=["https://example.invalid/model.safetensors"],
        comfyui_folder="checkpoints",
        filename="../escape.safetensors",
    )
    call_count = 0

    async def downloader(url: str, dest: Path) -> int:
        nonlocal call_count
        call_count += 1
        dest.write_bytes(payload)
        return len(payload)

    store, _ = _build_store(tmp_path, downloader)

    with pytest.raises(ModelDownloadError):
        await store.materialize(lock)

    assert call_count == 0
    assert not store.has_blob(lock.sha256)


@pytest.mark.anyio
async def test_failed_materialized_replace_preserves_existing_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"stable-target"
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))
    first = await store.materialize(lock)

    def fail_replace(source: Path, target: Path) -> None:
        if str(source).endswith(".tmp"):
            raise RuntimeError("replace failed")
        os_replace(source, target)

    os_replace = model_store_module.os.replace
    monkeypatch.setattr(model_store_module.os, "replace", fail_replace)

    with pytest.raises(RuntimeError):
        await store.materialize(lock)

    assert first.materialized_path.read_bytes() == payload
    assert not list(first.materialized_path.parent.glob("*.tmp"))
