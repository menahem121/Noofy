"""Tests for the content-addressed model store and its rollback behavior."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

import app.runtime.model_store as model_store_module
from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.engine.diagnostics import LogStore
from app.runtime.isolation import ModelLock
from app.runtime.model_store import (
    LocalModelRequirement,
    ModelDownloadError,
    ModelSourcePolicyError,
    ModelStore,
    probe_symlink_capability,
)
from app.source_policy import SourcePolicy


def _model_store_paths(root: Path) -> dict[str, Path]:
    return {
        "blobs_dir": root / "blobs",
        "refs_dir": root / "refs",
        "materialized_dir": root / "materialized",
        "transactions_dir": root / "transactions",
    }


def _build_store(tmp_path: Path, downloader, *, local_model_roots: list[Path] | None = None) -> tuple[ModelStore, LogStore]:
    log_store = LogStore()
    store = ModelStore(
        **_model_store_paths(tmp_path),
        log_store=log_store,
        downloader=downloader,
        local_model_roots=local_model_roots,
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


def _source_policy(
    *,
    model_source_trust: str = "hashed",
    allowed_model_origins: list[str] | None = None,
) -> SourcePolicy:
    return SourcePolicy(
        trust_level="quarantined_community",
        source_policy="explicit_opt_in_and_isolated_capsule_required",
        package_source_type="noofy_archive_import",
        automatic_preparation_allowed=True,
        allowed_source_origins=["explicit-metadata"],
        allowed_model_origins=allowed_model_origins or ["hashed-download"],
        model_source_trust=model_source_trust,
        community_preparation_opt_in_required=True,
        community_preparation_opted_in=True,
    )


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
async def test_materialized_path_uses_supported_link_or_copy_strategy(tmp_path: Path) -> None:
    payload = b"link-target"
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    result = await store.materialize(lock)

    assert result.materialization_strategy in {"hardlink", "symlink", "copy"}
    assert result.materialized_path.read_bytes() == payload


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


@pytest.mark.anyio
async def test_materialize_model_view_writes_per_view_tree_and_references(tmp_path: Path) -> None:
    payload = b"view-model"
    lock = _model_lock(payload, model_id="view/model", folder="checkpoints", filename="model.safetensors")
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    view = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    materialized = view.view_path / "checkpoints" / "model.safetensors"
    assert view.view_path.name.startswith("model-view-")
    assert materialized.exists()
    assert materialized.read_bytes() == payload
    assert view.model_references[0].requirement_id == "view/model"
    assert view.model_references[0].materialized_path == str(materialized)
    assert view.model_references[0].materialization_strategy in {"hardlink", "symlink", "copy"}
    assert view.model_references[0].materialized_file_verified is True
    manifest = json.loads((view.view_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["view_fingerprint"] == view.view_fingerprint


@pytest.mark.anyio
async def test_materialize_model_view_blocks_download_when_policy_disallows_model_origin(tmp_path: Path) -> None:
    payload = b"policy-model"
    lock = _model_lock(payload)
    call_count = 0

    async def downloader(url: str, dest: Path) -> int:
        nonlocal call_count
        call_count += 1
        dest.write_bytes(payload)
        return len(payload)

    store, _ = _build_store(tmp_path, downloader)

    with pytest.raises(ModelSourcePolicyError, match="source policy"):
        await store.materialize_model_view(
            view_id="capsule-fp",
            model_locks=[lock],
            source_policy=_source_policy(allowed_model_origins=["registry-locked"]),
        )

    assert call_count == 0
    assert not (tmp_path / "blobs").exists()


@pytest.mark.anyio
async def test_materialize_model_view_blocks_local_reuse_when_policy_requires_hashed_models(tmp_path: Path) -> None:
    model_root = tmp_path / "local-models"
    local_model = model_root / "checkpoints" / "local.safetensors"
    local_model.parent.mkdir(parents=True)
    local_model.write_bytes(b"local-bytes")
    requirement = LocalModelRequirement(
        requirement_id="checkpoints/local.safetensors",
        comfyui_folder="checkpoints",
        filename="local.safetensors",
        size_bytes=len(b"local-bytes"),
    )
    store, _ = _build_store(tmp_path, _make_downloader({}), local_model_roots=[model_root])

    with pytest.raises(ModelSourcePolicyError, match="requires hash-verified"):
        await store.materialize_model_view(
            view_id="capsule-fp",
            model_locks=[],
            local_model_requirements=[requirement],
            source_policy=_source_policy(model_source_trust="hashed", allowed_model_origins=["hashed-download"]),
        )

    assert not (tmp_path / "materialized").exists()


@pytest.mark.anyio
async def test_materialize_model_view_can_stage_then_promote_atomically(tmp_path: Path) -> None:
    payload = b"staged-view-model"
    lock = _model_lock(payload, model_id="staged/model", folder="checkpoints", filename="model.safetensors")
    download_targets: list[Path] = []

    async def downloader(url: str, dest: Path) -> int:
        download_targets.append(dest)
        dest.write_bytes(payload)
        return len(payload)

    store, _ = _build_store(tmp_path, downloader)
    staged_views_dir = tmp_path / "transactions" / "install-123" / "model-views"
    staged_blobs_dir = tmp_path / "transactions" / "install-123" / "model-blobs"

    staged = await store.materialize_model_view(
        view_id="capsule-fp",
        model_locks=[lock],
        staged_views_dir=staged_views_dir,
        staged_blobs_dir=staged_blobs_dir,
    )

    assert staged.is_staged is True
    assert staged.view_path.is_relative_to(staged_views_dir)
    assert download_targets
    assert download_targets[0].parent.is_relative_to(staged_blobs_dir)
    assert not download_targets[0].parent.exists()
    assert not staged.final_view_path.exists()
    assert Path(staged.model_references[0].materialized_path).is_relative_to(staged.view_path)

    promoted = store.promote_model_view(staged)

    assert promoted.view_path == staged.final_view_path
    assert promoted.view_path.exists()
    assert not staged.view_path.exists()
    materialized = promoted.view_path / "checkpoints" / "model.safetensors"
    assert materialized.read_bytes() == payload
    assert promoted.model_references[0].materialized_path == str(materialized)
    manifest = json.loads((promoted.view_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_references"][0]["materialized_path"] == str(materialized)


@pytest.mark.anyio
async def test_materialize_model_view_reuses_existing_blob_without_download(tmp_path: Path) -> None:
    payload = b"existing-view-model"
    lock = _model_lock(payload)
    call_count = 0

    async def downloader(url: str, dest: Path) -> int:
        nonlocal call_count
        call_count += 1
        dest.write_bytes(payload)
        return len(payload)

    store, _ = _build_store(tmp_path, downloader)

    first = await store.materialize_model_view(view_id="capsule-a", model_locks=[lock])
    second = await store.materialize_model_view(view_id="capsule-b", model_locks=[lock])

    assert call_count == 1
    assert first.view_path != second.view_path
    assert second.model_references[0].blob_path == first.model_references[0].blob_path


@pytest.mark.anyio
async def test_materialize_model_view_reuses_filename_size_local_candidate(tmp_path: Path) -> None:
    payload = b"local-candidate"
    local_root = tmp_path / "user-models"
    local_path = local_root / "checkpoints" / "local.safetensors"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(payload)
    requirement = LocalModelRequirement(
        requirement_id="checkpoints/local.safetensors",
        comfyui_folder="checkpoints",
        filename="local.safetensors",
        size_bytes=len(payload),
    )
    store, log_store = _build_store(tmp_path, _make_downloader({}), local_model_roots=[local_root])

    view = await store.materialize_model_view(
        view_id="capsule-fp",
        model_locks=[],
        local_model_requirements=[requirement],
    )

    ref = view.model_references[0]
    materialized = view.view_path / "checkpoints" / "local.safetensors"
    assert materialized.read_bytes() == payload
    assert ref.verification_level is ModelVerificationLevel.FILENAME_SIZE
    assert ref.asset_ownership is AssetOwnership.USER_LOCAL
    assert ref.source_path == str(local_path)
    assert ref.blob_path is None
    assert ref.store_ref is None
    assert ref.sha256 == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert ref.materialized_path == str(materialized)
    assert ref.materialization_strategy in {"hardlink", "symlink", "copy"}
    assert any("Reusing local model candidate" in event.message for event in log_store.list_events().events)


@pytest.mark.anyio
async def test_materialize_model_view_rejects_local_candidate_size_mismatch(tmp_path: Path) -> None:
    local_root = tmp_path / "user-models"
    local_path = local_root / "checkpoints" / "local.safetensors"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(b"wrong-size")
    requirement = LocalModelRequirement(
        requirement_id="checkpoints/local.safetensors",
        comfyui_folder="checkpoints",
        filename="local.safetensors",
        size_bytes=999,
    )
    store, _ = _build_store(tmp_path, _make_downloader({}), local_model_roots=[local_root])

    with pytest.raises(ModelDownloadError, match="Local model candidate size mismatch"):
        await store.materialize_model_view(
            view_id="capsule-fp",
            model_locks=[],
            local_model_requirements=[requirement],
        )


@pytest.mark.anyio
async def test_materialize_model_view_rejects_same_name_different_blob_collision(tmp_path: Path) -> None:
    first = _model_lock(b"first", model_id="first", folder="checkpoints", filename="shared.safetensors")
    second = _model_lock(b"second", model_id="second", folder="checkpoints", filename="shared.safetensors")
    store, _ = _build_store(tmp_path, _make_downloader({}))

    with pytest.raises(ModelDownloadError, match="conflicting requirements"):
        await store.materialize_model_view(view_id="capsule-fp", model_locks=[first, second])


@pytest.mark.anyio
async def test_materialize_model_view_repairs_stale_view_file(tmp_path: Path) -> None:
    payload = b"fresh-view-model"
    lock = _model_lock(payload, folder="checkpoints", filename="stale.safetensors")
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))
    first = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])
    target = first.view_path / "checkpoints" / "stale.safetensors"
    target.unlink()
    target.write_bytes(b"stale")

    repaired = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    assert repaired.view_path == first.view_path
    assert target.read_bytes() == payload
    assert repaired.model_references[0].materialized_file_verified is True


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform == "win32", reason="symlink permissions vary on Windows")
async def test_materialize_model_view_repairs_stale_symlink(tmp_path: Path) -> None:
    payload = b"fresh-view-model"
    lock = _model_lock(payload, folder="checkpoints", filename="stale-link.safetensors")
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))
    first = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])
    target = first.view_path / "checkpoints" / "stale-link.safetensors"
    target.unlink()
    model_store_module.os.symlink(tmp_path / "missing-blob", target)

    repaired = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    assert repaired.view_path == first.view_path
    assert target.read_bytes() == payload
    assert repaired.model_references[0].materialized_file_verified is True


@pytest.mark.anyio
async def test_materialize_model_view_falls_back_to_symlink_when_hardlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"symlink-fallback"
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    def fail_link(source: Path, target: Path) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr(model_store_module.os, "link", fail_link)

    view = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    assert view.model_references[0].materialization_strategy in {"symlink", "copy"}


@pytest.mark.anyio
async def test_materialize_model_view_falls_back_to_copy_when_links_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"copy-fallback"
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    def fail_link(source: Path, target: Path) -> None:
        raise OSError("cross-device link")

    def fail_symlink(source: Path, target: Path) -> None:
        raise OSError("symlink denied")

    monkeypatch.setattr(model_store_module.os, "link", fail_link)
    monkeypatch.setattr(model_store_module.os, "symlink", fail_symlink)

    view = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    assert view.model_references[0].materialization_strategy == "copy"
    assert Path(view.model_references[0].materialized_path).read_bytes() == payload


@pytest.mark.anyio
async def test_materialize_model_view_skips_symlink_when_capability_probe_is_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"copy-after-probe"
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))
    store._symlink_capability = False
    symlink_called = False

    def fail_link(source: Path, target: Path) -> None:
        raise OSError("cross-device link")

    def track_symlink(source: Path, target: Path) -> None:
        nonlocal symlink_called
        symlink_called = True
        raise AssertionError("symlink should not be attempted")

    monkeypatch.setattr(model_store_module.os, "link", fail_link)
    monkeypatch.setattr(model_store_module.os, "symlink", track_symlink)

    view = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    assert view.model_references[0].materialization_strategy == "copy"
    assert symlink_called is False


def test_probe_symlink_capability_returns_false_when_symlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_symlink(source: Path, target: Path) -> None:
        raise OSError("symlink denied")

    monkeypatch.setattr(model_store_module.os, "symlink", fail_symlink)

    assert probe_symlink_capability(tmp_path) is False
    assert not list(tmp_path.iterdir())


@pytest.mark.anyio
async def test_materialize_model_view_copy_failure_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"copy-failure"
    lock = _model_lock(payload)
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))

    def fail_link(source: Path, target: Path) -> None:
        raise OSError("cross-device link")

    def fail_symlink(source: Path, target: Path) -> None:
        raise OSError("symlink denied")

    def fail_copy(source: Path, target: Path) -> None:
        Path(target).write_bytes(b"partial")
        raise OSError("copy failed")

    monkeypatch.setattr(model_store_module.os, "link", fail_link)
    monkeypatch.setattr(model_store_module.os, "symlink", fail_symlink)
    monkeypatch.setattr(model_store_module.shutil, "copy2", fail_copy)

    with pytest.raises(OSError, match="copy failed"):
        await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    assert not list((tmp_path / "materialized").rglob("*.tmp"))


@pytest.mark.anyio
async def test_materialize_model_view_rejects_windows_path_length_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"windows-path-limit"
    lock = _model_lock(payload)
    store, log_store = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))
    store.materialized_dir = tmp_path / ("x" * 240)
    monkeypatch.setattr(model_store_module.sys, "platform", "win32")

    with pytest.raises(ModelDownloadError, match="path is too long"):
        await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])

    assert any("Model materialization failed" in event.message for event in log_store.list_events().events)


@pytest.mark.anyio
async def test_sweep_orphan_materialized_links_removes_view_file_when_blob_missing(tmp_path: Path) -> None:
    payload = b"orphan-view-model"
    lock = _model_lock(payload, model_id="orphan/model", folder="checkpoints", filename="model.safetensors")
    store, _ = _build_store(tmp_path, _make_downloader({lock.source_urls[0]: payload}))
    view = await store.materialize_model_view(view_id="capsule-fp", model_locks=[lock])
    materialized = Path(view.model_references[0].materialized_path)
    blob = Path(view.model_references[0].blob_path or "")

    blob.unlink()
    removed = store.sweep_orphan_materialized_links()

    assert removed == 1
    assert not materialized.exists()
