from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from app.artifacts import AssetOwnership
from app.diagnostics import LogStore
from app.workflows import model_availability as availability_module
from app.workflows.model_availability import (
    ModelAvailabilityService,
    ProviderAuthenticationRequired,
    ProviderModelResolver,
    ProviderRateLimited,
)
from app.workflows.model_identity_store import LocalModelIdentityStore
from app.workflows.package import RequiredModel, WorkflowMetadata, WorkflowPackage


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "providers"


def _fixture(name: str) -> object:
    return json.loads((FIXTURE_DIR / name).read_text())


def _package(models: list[RequiredModel]) -> WorkflowPackage:
    return WorkflowPackage(
        metadata=WorkflowMetadata(
            id="imported_workflow",
            name="Imported workflow",
            version="0.1.0",
        ),
        engine="comfyui",
        required_models=models,
        comfyui_graph={},
    )


def _service(
    *,
    noofy_root: Path,
    external_root: Path | None = None,
    provider_resolver: ProviderModelResolver | None = None,
    log_store: LogStore | None = None,
    local_model_identity_store: LocalModelIdentityStore | None = None,
) -> ModelAvailabilityService:
    roots = [noofy_root]
    if external_root is not None:
        roots.append(external_root)
    return ModelAvailabilityService(
        model_roots=roots,
        noofy_models_dir=noofy_root,
        log_store=log_store or LogStore(),
        provider_resolver=provider_resolver,
        local_model_identity_store=local_model_identity_store,
    )


def _http_error(url: str, status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("provider error", request=request, response=response)


def test_model_summary_detects_noofy_owned_sha256_model(tmp_path: Path) -> None:
    payload = b"model-bytes"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "demo.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    service = _service(noofy_root=noofy_root)

    summary = service.summarize(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="demo.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert summary.ready_to_run is True
    assert summary.models[0].status == "available"
    assert summary.models[0].asset_ownership is AssetOwnership.NOOFY_DOWNLOADED
    assert summary.models[0].matched_sha256 == sha


def test_model_summary_reuses_external_model_as_user_local(tmp_path: Path) -> None:
    payload = b"external-model"
    noofy_root = tmp_path / "Noofy Models"
    external_root = tmp_path / "ComfyUI" / "models"
    model_path = external_root / "loras" / "style.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    service = _service(noofy_root=noofy_root, external_root=external_root)

    summary = service.summarize(
        _package(
            [
                RequiredModel(
                    folder="loras",
                    filename="style.safetensors",
                    size_bytes=len(payload),
                    verification_level="filename_size",
                )
            ]
        )
    )

    assert summary.ready_to_run is True
    assert summary.models[0].status == "available"
    assert summary.models[0].asset_ownership is AssetOwnership.USER_LOCAL
    assert summary.models[0].source_path == str(model_path)


def test_fast_model_summary_skips_recursive_filename_search(tmp_path: Path) -> None:
    payload = b"nested-model"
    noofy_root = tmp_path / "Noofy Models"
    nested_model_path = noofy_root / "checkpoints" / "nested" / "demo.safetensors"
    nested_model_path.parent.mkdir(parents=True)
    nested_model_path.write_bytes(payload)
    service = _service(noofy_root=noofy_root)
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="demo.safetensors",
                size_bytes=len(payload),
                verification_level="filename_size",
                source_urls=["https://example.com/models/demo.safetensors"],
            )
        ]
    )

    fast_summary = service.summarize(package, deep_search=False, verify_hashes=False)
    full_summary = service.summarize(package)

    assert fast_summary.models[0].status == "missing"
    assert full_summary.models[0].status == "available"
    assert full_summary.models[0].source_path == str(nested_model_path)


def test_fast_model_summary_skips_sha256_file_hashing(tmp_path: Path) -> None:
    payload = b"model-bytes"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "demo.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    service = _service(noofy_root=noofy_root)
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="demo.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
            )
        ]
    )

    fast_summary = service.summarize(package, deep_search=False, verify_hashes=False)
    full_summary = service.summarize(package)

    assert fast_summary.models[0].status == "possible_match"
    assert fast_summary.models[0].matched_sha256 is None
    assert full_summary.models[0].status == "available"
    assert full_summary.models[0].matched_sha256 == sha


def test_model_summary_persists_and_reuses_cached_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"model-bytes"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "demo.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    service = _service(noofy_root=noofy_root, local_model_identity_store=store)
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="demo.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
            )
        ]
    )
    original_sha256_file = availability_module._sha256_file
    calls = 0

    def counting_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
        nonlocal calls
        calls += 1
        return original_sha256_file(path, chunk_size)

    monkeypatch.setattr(availability_module, "_sha256_file", counting_sha256)

    first = service.summarize(package)

    assert first.models[0].status == "available"
    assert first.models[0].matched_sha256 == sha
    assert calls == 1

    def fail_if_hashing(path: Path, chunk_size: int = 1 << 20) -> str:
        raise AssertionError("unchanged cached model should not be re-hashed")

    monkeypatch.setattr(availability_module, "_sha256_file", fail_if_hashing)

    second = service.summarize(package)

    assert second.models[0].status == "available"
    assert second.models[0].matched_sha256 == sha


def test_model_summary_invalidates_cache_when_size_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_payload = b"old"
    updated_payload = b"new-model-bytes"
    initial_sha = hashlib.sha256(initial_payload).hexdigest()
    updated_sha = hashlib.sha256(updated_payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "demo.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(initial_payload)
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    service = _service(noofy_root=noofy_root, local_model_identity_store=store)
    original_sha256_file = availability_module._sha256_file
    calls = 0

    def counting_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
        nonlocal calls
        calls += 1
        return original_sha256_file(path, chunk_size)

    monkeypatch.setattr(availability_module, "_sha256_file", counting_sha256)

    service.summarize(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="demo.safetensors",
                    checksum=f"sha256:{initial_sha}",
                    size_bytes=len(initial_payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )
    model_path.write_bytes(updated_payload)

    summary = service.summarize(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="demo.safetensors",
                    checksum=f"sha256:{updated_sha}",
                    size_bytes=len(updated_payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert summary.models[0].status == "available"
    assert summary.models[0].matched_sha256 == updated_sha
    assert calls == 2


def test_model_summary_invalidates_cache_when_mtime_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"same-model-bytes"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "demo.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    service = _service(noofy_root=noofy_root, local_model_identity_store=store)
    original_sha256_file = availability_module._sha256_file
    calls = 0

    def counting_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
        nonlocal calls
        calls += 1
        return original_sha256_file(path, chunk_size)

    monkeypatch.setattr(availability_module, "_sha256_file", counting_sha256)
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="demo.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
            )
        ]
    )

    service.summarize(package)
    stat = model_path.stat()
    os.utime(model_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    summary = service.summarize(package)

    assert summary.models[0].status == "available"
    assert calls == 2


def test_model_summary_invalidates_cache_when_inode_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"same-model-bytes"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "demo.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    service = _service(noofy_root=noofy_root, local_model_identity_store=store)
    original_sha256_file = availability_module._sha256_file
    calls = 0

    def counting_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
        nonlocal calls
        calls += 1
        return original_sha256_file(path, chunk_size)

    monkeypatch.setattr(availability_module, "_sha256_file", counting_sha256)
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="demo.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
            )
        ]
    )

    service.summarize(package)
    previous_stat = model_path.stat()
    model_path.unlink()
    model_path.write_bytes(payload)
    os.utime(model_path, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
    if model_path.stat().st_ino == previous_stat.st_ino:
        pytest.skip("filesystem reused the inode")

    summary = service.summarize(package)

    assert summary.models[0].status == "available"
    assert calls == 2


def test_model_summary_reuses_cache_after_noofy_models_folder_moves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"model-bytes"
    sha = hashlib.sha256(payload).hexdigest()
    old_root = tmp_path / "Old Noofy Models"
    old_path = old_root / "checkpoints" / "demo.safetensors"
    old_path.parent.mkdir(parents=True)
    old_path.write_bytes(payload)
    old_stat = old_path.stat()
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    old_service = _service(noofy_root=old_root, local_model_identity_store=store)
    package = _package(
        [
            RequiredModel(
                folder="checkpoints",
                filename="demo.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
            )
        ]
    )
    old_service.summarize(package)
    new_root = tmp_path / "New Noofy Models"
    new_path = new_root / "checkpoints" / "demo.safetensors"
    new_path.parent.mkdir(parents=True)
    new_path.write_bytes(payload)
    os.utime(new_path, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
    new_service = _service(noofy_root=new_root, local_model_identity_store=store)

    def fail_if_hashing(path: Path, chunk_size: int = 1 << 20) -> str:
        raise AssertionError("moved model should reuse cached hash")

    monkeypatch.setattr(availability_module, "_sha256_file", fail_if_hashing)

    summary = new_service.summarize(package)

    assert summary.models[0].status == "available"
    assert summary.models[0].matched_sha256 == sha


def test_external_comfyui_models_are_cached_but_remain_user_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"external-model"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    external_root = tmp_path / "ComfyUI" / "models"
    model_path = external_root / "loras" / "style.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    service = _service(
        noofy_root=noofy_root,
        external_root=external_root,
        local_model_identity_store=store,
    )
    package = _package(
        [
            RequiredModel(
                folder="loras",
                filename="style.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
            )
        ]
    )

    first = service.summarize(package)
    assert first.models[0].status == "available"
    assert first.models[0].asset_ownership is AssetOwnership.USER_LOCAL

    def fail_if_hashing(path: Path, chunk_size: int = 1 << 20) -> str:
        raise AssertionError("external cached model should not be re-hashed")

    monkeypatch.setattr(availability_module, "_sha256_file", fail_if_hashing)
    second = service.summarize(package)

    assert second.models[0].status == "available"
    assert second.models[0].asset_ownership is AssetOwnership.USER_LOCAL


@pytest.mark.anyio
async def test_download_uses_part_file_then_atomic_final_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"downloaded-model"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    service = _service(noofy_root=noofy_root)
    seen_part_paths: list[Path] = []
    seen_state: dict[str, object] | None = None

    async def fake_stream(url: str, part_path: Path) -> None:
        assert part_path.name.endswith(".part")
        assert part_path.parent.parent == noofy_root / ".downloads"
        state_path = part_path.parent / "download-state.json"
        assert state_path.exists()
        nonlocal seen_state
        seen_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert seen_state["status"] == "downloading"
        assert seen_state["target_folder"] == "upscale_models"
        assert seen_state["target_filename"] == "upscale.safetensors"
        assert seen_state["expected_size"] == len(payload)
        assert seen_state["expected_sha256"] == sha
        seen_part_paths.append(part_path)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="upscale_models",
                    filename="upscale.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                    source_urls=["https://huggingface.co/example/upscale.safetensors"],
                )
            ]
        )
    )

    final_path = noofy_root / "upscale_models" / "upscale.safetensors"
    assert result.downloaded_count == 1
    assert final_path.read_bytes() == payload
    assert seen_part_paths
    assert seen_state is not None
    assert not seen_part_paths[0].exists()
    assert not seen_part_paths[0].parent.exists()
    assert not list((noofy_root / ".downloads").glob("**/*"))
    assert result.model_summary.models[0].status == "available"


@pytest.mark.anyio
async def test_downloaded_model_final_hash_is_cached_for_future_summaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"downloaded-model"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    store = LocalModelIdentityStore(tmp_path / "identity" / "cache.db")
    service = _service(noofy_root=noofy_root, local_model_identity_store=store)
    original_sha256_file = availability_module._sha256_file
    calls = 0

    async def fake_stream(url: str, part_path: Path) -> None:
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    def counting_sha256(path: Path, chunk_size: int = 1 << 20) -> str:
        nonlocal calls
        calls += 1
        return original_sha256_file(path, chunk_size)

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)
    monkeypatch.setattr(availability_module, "_sha256_file", counting_sha256)
    package = _package(
        [
            RequiredModel(
                folder="upscale_models",
                filename="upscale.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
                source_urls=["https://huggingface.co/example/upscale.safetensors"],
            )
        ]
    )

    result = await service.download_missing(package)

    assert result.downloaded_count == 1
    assert calls == 1

    def fail_if_hashing(path: Path, chunk_size: int = 1 << 20) -> str:
        raise AssertionError("downloaded cached model should not be re-hashed")

    monkeypatch.setattr(availability_module, "_sha256_file", fail_if_hashing)
    summary = service.summarize(package)

    assert summary.models[0].status == "available"
    assert summary.models[0].matched_sha256 == sha


@pytest.mark.anyio
async def test_download_reports_progress_with_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"progress-model"
    noofy_root = tmp_path / "Noofy Models"
    service = _service(noofy_root=noofy_root)
    events: list[dict[str, object]] = []

    async def fake_stream(
        url: str,
        part_path: Path,
        *,
        progress_callback=None,
        cancel_event=None,
    ) -> None:
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)
        if progress_callback is not None:
            progress_callback(len(payload), len(payload))

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="progress.safetensors",
                    size_bytes=len(payload),
                    verification_level="filename_size",
                    source_urls=["https://example.com/models/progress.safetensors"],
                )
            ]
        ),
        progress_callback=events.append,
    )

    assert result.downloaded_count == 1
    assert any(event["status"] == "downloading" and event["bytes_downloaded"] == len(payload) for event in events)
    assert any(event["status"] == "verifying" for event in events)
    assert any(event["status"] == "succeeded" for event in events)


@pytest.mark.anyio
async def test_failed_download_cleans_part_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"wrong-bytes"
    noofy_root = tmp_path / "Noofy Models"
    service = _service(noofy_root=noofy_root)
    seen_part_paths: list[Path] = []

    async def fake_stream(
        url: str,
        part_path: Path,
        *,
        progress_callback=None,
        cancel_event=None,
    ) -> None:
        seen_part_paths.append(part_path)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)
        if progress_callback is not None:
            progress_callback(len(payload), len(payload))

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    events: list[dict[str, object]] = []

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="broken.safetensors",
                    checksum="sha256:" + ("0" * 64),
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                    source_urls=["https://huggingface.co/example/broken.safetensors"],
                )
            ]
        ),
        progress_callback=events.append,
    )

    assert result.failed_count == 1
    assert not (noofy_root / "checkpoints" / "broken.safetensors").exists()
    assert seen_part_paths
    assert not seen_part_paths[0].exists()
    assert not seen_part_paths[0].parent.exists()
    assert result.model_summary.models[0].status == "verification_failed"
    assert result.model_summary.models[0].status_label == "Verification failed"
    assert any(event["status"] == "verification_failed" for event in events)
    assert "identity check" in (result.model_summary.models[0].message or "")


@pytest.mark.anyio
async def test_canceled_download_cleans_transaction_and_keeps_completed_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_payload = b"completed"
    canceled_payload = b"partial"
    noofy_root = tmp_path / "Noofy Models"
    service = _service(noofy_root=noofy_root)
    cancel_event = availability_module.asyncio.Event()
    calls = 0

    async def fake_stream(
        url: str,
        part_path: Path,
        *,
        progress_callback=None,
        cancel_event=None,
    ) -> None:
        nonlocal calls
        calls += 1
        part_path.parent.mkdir(parents=True, exist_ok=True)
        if calls == 1:
            part_path.write_bytes(completed_payload)
            return
        part_path.write_bytes(canceled_payload)
        if cancel_event is not None:
            cancel_event.set()
        raise availability_module.ModelDownloadCanceled("Download canceled.")

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="completed.safetensors",
                    size_bytes=len(completed_payload),
                    verification_level="filename_size",
                    source_urls=["https://example.com/models/completed.safetensors"],
                ),
                RequiredModel(
                    folder="checkpoints",
                    filename="canceled.safetensors",
                    size_bytes=len(canceled_payload),
                    verification_level="filename_size",
                    source_urls=["https://example.com/models/canceled.safetensors"],
                ),
            ]
        ),
        cancel_event=cancel_event,
    )

    assert result.status == "canceled"
    assert (noofy_root / "checkpoints" / "completed.safetensors").read_bytes() == completed_payload
    assert not (noofy_root / "checkpoints" / "canceled.safetensors").exists()
    assert not list((noofy_root / ".downloads").glob("*/**/*.part"))


def test_cleanup_interrupted_download_transactions_on_startup(tmp_path: Path) -> None:
    noofy_root = tmp_path / "Noofy Models"
    transaction_dir = noofy_root / ".downloads" / "interrupted-download"
    transaction_dir.mkdir(parents=True)
    (transaction_dir / "model.safetensors.part").write_bytes(b"partial")
    (transaction_dir / "download-state.json").write_text(
        json.dumps(
            {
                "download_id": "interrupted-download",
                "status": "downloading",
                "target_folder": "checkpoints",
                "target_filename": "model.safetensors",
                "started_at": "2026-05-10T00:00:00+00:00",
                "updated_at": "2026-05-10T00:00:01+00:00",
            }
        ),
        encoding="utf-8",
    )
    service = _service(noofy_root=noofy_root)

    assert service.cleanup_interrupted_downloads() == 1
    assert not transaction_dir.exists()


@pytest.mark.anyio
async def test_download_refuses_final_path_outside_noofy_models_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"symlink-escape"
    noofy_root = tmp_path / "Noofy Models"
    outside_root = tmp_path / "outside"
    outside_root.mkdir()
    noofy_root.mkdir()
    (noofy_root / "checkpoints").symlink_to(outside_root, target_is_directory=True)
    service = _service(noofy_root=noofy_root)

    async def fake_stream(url: str, part_path: Path) -> None:
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="escape.safetensors",
                    size_bytes=len(payload),
                    verification_level="filename_size",
                    source_urls=["https://example.com/models/escape.safetensors"],
                )
            ]
        )
    )

    assert result.failed_count == 1
    assert "Noofy Models folder" in (result.model_summary.models[0].message or "")
    assert not (outside_root / "escape.safetensors").exists()
    assert not list((noofy_root / ".downloads").glob("*.part"))


@pytest.mark.anyio
async def test_download_with_external_root_still_writes_only_to_noofy_models(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"owned-download"
    noofy_root = tmp_path / "Noofy Models"
    external_root = tmp_path / "External ComfyUI" / "models"
    service = _service(noofy_root=noofy_root, external_root=external_root)

    async def fake_stream(url: str, part_path: Path) -> None:
        assert noofy_root in part_path.parents
        assert external_root not in part_path.parents
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="loras",
                    filename="owned.safetensors",
                    size_bytes=len(payload),
                    verification_level="filename_size",
                    source_urls=["https://example.com/models/owned.safetensors"],
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert (noofy_root / "loras" / "owned.safetensors").read_bytes() == payload
    assert not (external_root / "loras" / "owned.safetensors").exists()
    assert not list((noofy_root / ".downloads").glob("**/*.safetensors"))


@pytest.mark.anyio
async def test_download_refuses_noofy_models_folder_inside_bundled_comfyui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundled_root = availability_module.settings.comfyui_repo_dir / "models" / "Noofy Downloads"
    service = _service(noofy_root=bundled_root)
    streamed = False

    async def fake_stream(url: str, part_path: Path) -> None:
        nonlocal streamed
        streamed = True

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="blocked.safetensors",
                    size_bytes=123,
                    verification_level="filename_size",
                    source_urls=["https://example.com/models/blocked.safetensors"],
                )
            ]
        )
    )

    assert streamed is False
    assert result.failed_count == 1
    assert not (bundled_root / "checkpoints" / "blocked.safetensors").exists()


@pytest.mark.anyio
async def test_download_checks_disk_space_before_streaming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noofy_root = tmp_path / "Noofy Models"
    service = _service(noofy_root=noofy_root)
    streamed = False

    async def fake_stream(url: str, part_path: Path) -> None:
        nonlocal streamed
        streamed = True

    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)
    monkeypatch.setattr(
        availability_module.shutil,
        "disk_usage",
        lambda path: availability_module.shutil._ntuple_diskusage(10, 9, 1),
    )

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="too-large.safetensors",
                    size_bytes=1024,
                    verification_level="filename_size",
                    source_urls=["https://huggingface.co/example/too-large.safetensors"],
                )
            ]
        )
    )

    assert streamed is False
    assert result.failed_count == 1
    assert "Not enough free disk space" in (result.model_summary.models[0].message or "")


@pytest.mark.anyio
async def test_download_resolves_hugging_face_exact_filename_and_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"hf-model"
    noofy_root = tmp_path / "Noofy Models"
    fetched_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        assert method == "GET"
        fetched_urls.append(url)
        assert headers == {}
        if url == "https://huggingface.co/api/models":
            if params["search"] != "hf-model.safetensors":
                return []
            return [
                {
                    "modelId": "creator/repo",
                    "siblings": [
                        {
                            "rfilename": "models/hf-model.safetensors",
                        }
                    ],
                }
            ]
        assert url == "https://huggingface.co/api/models/creator/repo"
        assert params == {"blobs": "true"}
        return {
            "siblings": [
                {
                    "rfilename": "models/hf-model.safetensors",
                    "size": len(payload),
                }
            ],
        }

    async def fake_stream(url: str, part_path: Path) -> None:
        assert url == "https://huggingface.co/creator/repo/resolve/main/models/hf-model.safetensors"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="hf-model.safetensors",
                    size_bytes=len(payload),
                    verification_level="filename_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert "https://huggingface.co/api/models/creator/repo" in fetched_urls
    assert (noofy_root / "checkpoints" / "hf-model.safetensors").read_bytes() == payload


@pytest.mark.anyio
async def test_download_resolves_generic_diffusers_hugging_face_filename_from_model_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"controlnet-union-promax"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    searched_terms: list[str] = []
    streamed_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        assert method == "GET"
        assert headers == {}
        if "/by-hash/" in url:
            return {"files": []}
        if url == "https://huggingface.co/api/models":
            searched_terms.append(params["search"])
            if params["search"] == "controlnet":
                return [{"modelId": "xinsir/controlnet-union-sdxl-1.0"}]
            return []
        assert url == "https://huggingface.co/api/models/xinsir/controlnet-union-sdxl-1.0"
        assert params == {"blobs": "true"}
        return {
            "siblings": [
                {
                    "rfilename": "diffusion_pytorch_model_promax.safetensors",
                    "size": len(payload),
                    "lfs": {"sha256": sha, "size": len(payload)},
                }
            ],
        }

    async def fake_stream(url: str, part_path: Path) -> None:
        streamed_urls.append(url)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="controlnet",
                    filename="diffusion_pytorch_model_promax.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert "controlnet" in searched_terms
    assert streamed_urls == [
        "https://huggingface.co/xinsir/controlnet-union-sdxl-1.0/resolve/main/diffusion_pytorch_model_promax.safetensors"
    ]
    assert (
        noofy_root / "controlnet" / "diffusion_pytorch_model_promax.safetensors"
    ).read_bytes() == payload


@pytest.mark.anyio
async def test_hugging_face_accepts_sha_match_when_provider_filename_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"same-model-different-name"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    streamed_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if "/by-hash/" in url:
            return {"files": []}
        if url == "https://huggingface.co/api/models":
            if params["search"] == "expected-target.safetensors":
                return [{"modelId": "creator/repo"}]
            return []
        assert url == "https://huggingface.co/api/models/creator/repo"
        return {
            "siblings": [
                {
                    "rfilename": "provider-name.safetensors",
                    "size": len(payload),
                    "lfs": {"sha256": sha, "size": len(payload)},
                }
            ],
        }

    async def fake_stream(url: str, part_path: Path) -> None:
        streamed_urls.append(url)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="expected-target.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert streamed_urls == [
        "https://huggingface.co/creator/repo/resolve/main/provider-name.safetensors"
    ]
    assert (noofy_root / "checkpoints" / "expected-target.safetensors").read_bytes() == payload


@pytest.mark.anyio
async def test_explicit_hugging_face_blob_source_url_is_used_as_verified_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"explicit-source-url"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    streamed_urls: list[str] = []

    async def fake_stream(url: str, part_path: Path) -> None:
        streamed_urls.append(url)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    class FailingResolver(ProviderModelResolver):
        async def resolve(self, model: RequiredModel) -> list[str]:
            raise AssertionError("provider search should not run when a source URL exists")

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=FailingResolver(api_key_resolver=lambda provider: None),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="controlnet",
                    filename="target-name.safetensors",
                    source_urls=[
                        "https://huggingface.co/creator/repo/blob/main/provider-name.safetensors"
                    ],
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert streamed_urls == [
        "https://huggingface.co/creator/repo/resolve/main/provider-name.safetensors"
    ]
    assert (noofy_root / "controlnet" / "target-name.safetensors").read_bytes() == payload


@pytest.mark.anyio
async def test_download_falls_back_when_first_provider_source_requires_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"public-fallback"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    streamed_urls: list[str] = []

    class FallbackResolver(ProviderModelResolver):
        async def resolve(self, model: RequiredModel) -> list[str]:
            return [
                "https://civitai.com/api/download/models/auth-gated",
                "https://huggingface.co/creator/repo/resolve/main/model.safetensors",
            ]

    async def fake_stream(url: str, part_path: Path, **kwargs: object) -> None:
        streamed_urls.append(url)
        if "civitai.com" in url:
            raise _http_error(url, 401)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=FallbackResolver(api_key_resolver=lambda provider: None),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="controlnet",
                    filename="target-name.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert streamed_urls == [
        "https://civitai.com/api/download/models/auth-gated",
        "https://huggingface.co/creator/repo/resolve/main/model.safetensors",
    ]
    assert (noofy_root / "controlnet" / "target-name.safetensors").read_bytes() == payload


@pytest.mark.anyio
async def test_explicit_source_urls_take_priority_over_provider_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"explicit-source"
    noofy_root = tmp_path / "Noofy Models"
    streamed_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        raise AssertionError("provider search should not run for explicit source URLs")

    async def fake_stream(url: str, part_path: Path) -> None:
        streamed_urls.append(url)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="vae",
                    filename="explicit.safetensors",
                    size_bytes=len(payload),
                    verification_level="filename_size",
                    source_urls=["https://example.com/models/explicit.safetensors"],
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert streamed_urls == ["https://example.com/models/explicit.safetensors"]


def test_model_summary_redacts_secret_bearing_source_url(tmp_path: Path) -> None:
    noofy_root = tmp_path / "Noofy Models"
    service = _service(noofy_root=noofy_root)

    summary = service.summarize(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="redacted.safetensors",
                    size_bytes=100,
                    verification_level="filename_size",
                    source_urls=[
                        "https://example.com/redacted.safetensors?token=source_secret&api_key=also_secret"
                    ],
                )
            ]
        )
    )

    exposed = str(summary.model_dump(mode="json"))
    assert "source_secret" not in exposed
    assert "also_secret" not in exposed
    assert "token=[redacted]" in exposed
    assert "api_key=[redacted]" in exposed


@pytest.mark.anyio
async def test_provider_tokens_are_not_returned_or_logged_on_resolver_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noofy_root = tmp_path / "Noofy Models"
    log_store = LogStore()
    hf_token = "hf_super_secret_token"
    civitai_token = "civitai_super_secret_token"

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            assert headers == {"Authorization": f"Bearer {hf_token}"}
            return []
        assert headers == {"Authorization": f"Bearer {civitai_token}"}
        raise RuntimeError(
            f"provider failed with Authorization: Bearer {civitai_token} and token={civitai_token}"
        )

    service = _service(
        noofy_root=noofy_root,
        log_store=log_store,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: hf_token
            if provider == "hugging_face"
            else civitai_token,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(
        availability_module,
        "_stream_url",
        lambda url, part_path: pytest.fail("download should not start"),
    )

    payload = b"secret-bearing-failure"
    sha = hashlib.sha256(payload).hexdigest()
    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="loras",
                    filename="secret-bearing-failure.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    exposed_result = str(result.model_dump(mode="json"))
    exposed_logs = str(log_store.list_events().model_dump(mode="json"))
    assert result.failed_count == 1
    assert hf_token not in exposed_result
    assert civitai_token not in exposed_result
    assert hf_token not in exposed_logs
    assert civitai_token not in exposed_logs
    assert "Bearer [redacted]" in exposed_logs


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        ProviderAuthenticationRequired(
            "Auth failed for https://provider.test/model?token=auth_secret&api_key=also_secret"
        ),
        ProviderRateLimited(
            "Rate limited at https://provider.test/model?token=rate_secret&api_key=also_secret"
        ),
    ],
)
async def test_provider_auth_and_rate_limit_messages_redact_url_tokens(
    tmp_path: Path,
    failure: Exception,
) -> None:
    noofy_root = tmp_path / "Noofy Models"
    log_store = LogStore()

    class FailingResolver(ProviderModelResolver):
        async def resolve(self, model: RequiredModel) -> list[str]:
            raise failure

    service = _service(
        noofy_root=noofy_root,
        log_store=log_store,
        provider_resolver=FailingResolver(api_key_resolver=lambda provider: None),
    )

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="provider-failure.safetensors",
                    size_bytes=100,
                    verification_level="filename_size",
                )
            ]
        )
    )

    exposed_result = str(result.model_dump(mode="json"))
    exposed_logs = str(log_store.list_events().model_dump(mode="json"))
    assert result.failed_count == 1
    assert "auth_secret" not in exposed_result
    assert "rate_secret" not in exposed_result
    assert "also_secret" not in exposed_result
    assert "auth_secret" not in exposed_logs
    assert "rate_secret" not in exposed_logs
    assert "also_secret" not in exposed_logs
    assert "token=[redacted]" in exposed_result
    assert "api_key=[redacted]" in exposed_result


@pytest.mark.anyio
async def test_hugging_face_uses_api_key_and_is_tried_before_civitai_without_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"hf-fixture"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        calls.append((method, url, params, headers))
        assert method == "GET"
        assert headers == {"Authorization": "Bearer hf-token"}
        if url == "https://huggingface.co/api/models":
            if params["search"] != "hf-fixture.safetensors":
                return []
            return _fixture("huggingface_models_response.json")
        assert url == "https://huggingface.co/api/models/creator/noofy-hf-fixture"
        assert params == {"blobs": "true"}
        return _fixture("huggingface_models_response.json")[0]

    async def fake_stream(url: str, part_path: Path) -> None:
        assert url == "https://huggingface.co/creator/noofy-hf-fixture/resolve/main/models/hf-fixture.safetensors"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: "hf-token" if provider == "hugging_face" else "civitai-token",
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="hf-fixture.safetensors",
                    size_bytes=len(payload),
                    verification_level="filename_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert [call[1] for call in calls if "civitai" in call[1]] == []
    assert "https://huggingface.co/api/models/creator/noofy-hf-fixture" in [call[1] for call in calls]


@pytest.mark.anyio
async def test_hugging_face_inspects_repo_file_metadata_when_repo_name_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"hf repo inspected model"
    sha = hashlib.sha256(payload).hexdigest()
    filename = "v1-5-pruned-emaonly-fp16.safetensors"
    noofy_root = tmp_path / "Noofy Models"
    inspected_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if "/by-hash/" in url:
            return {"files": []}
        if url == "https://huggingface.co/api/models":
            if params["search"] == "stable-diffusion-v1-5":
                return [{"modelId": "Comfy-Org/stable-diffusion-v1-5-archive"}]
            return []
        inspected_urls.append(url)
        assert url == "https://huggingface.co/api/models/Comfy-Org/stable-diffusion-v1-5-archive"
        assert params == {"blobs": "true"}
        return {
            "siblings": [
                {
                    "rfilename": filename,
                    "size": len(payload),
                    "lfs": {"sha256": sha, "size": len(payload)},
                }
            ]
        }

    async def fake_stream(url: str, part_path: Path) -> None:
        assert url == f"https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/{filename}"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename=filename,
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert inspected_urls == ["https://huggingface.co/api/models/Comfy-Org/stable-diffusion-v1-5-archive"]
    assert (noofy_root / "checkpoints" / filename).read_bytes() == payload


@pytest.mark.anyio
async def test_hugging_face_provider_sha_mismatch_is_rejected_even_when_size_matches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"expected-model"
    expected_sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    streamed = False

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            if params["search"] == "mismatch.safetensors":
                return [{"modelId": "creator/mismatch-repo"}]
            return []
        if "huggingface.co" in url:
            return {
                "siblings": [
                    {
                        "rfilename": "mismatch.safetensors",
                        "size": len(payload),
                        "lfs": {"sha256": "0" * 64, "size": len(payload)},
                    }
                ]
            }
        if "/by-hash/" in url:
            return {"files": []}
        return {"items": []}

    async def fake_stream(url: str, part_path: Path) -> None:
        nonlocal streamed
        streamed = True

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="mismatch.safetensors",
                    checksum=f"sha256:{expected_sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert streamed is False
    assert result.failed_count == 1
    assert result.model_summary.models[0].status == "needs_manual_download"


@pytest.mark.anyio
async def test_hugging_face_size_only_candidate_is_not_trusted_when_hash_is_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"size-only-hf"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            if params["search"] == "size-only.safetensors":
                return [{"modelId": "creator/size-only-repo"}]
            return []
        if "huggingface.co" in url:
            return {
                "siblings": [
                    {"rfilename": "size-only.safetensors", "size": len(payload)}
                ]
            }
        if "/by-hash/" in url:
            return {"files": []}
        return {"items": []}

    async def fake_stream(url: str, part_path: Path) -> None:
        raise AssertionError("size-only provider candidates should not be downloaded when a hash is required")

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="size-only.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.failed_count == 1
    assert result.model_summary.models[0].status == "needs_manual_download"
    assert not (noofy_root / "checkpoints" / "size-only.safetensors").exists()


@pytest.mark.anyio
async def test_hugging_face_candidates_are_downloaded_in_reliability_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"ranked-hf"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    streamed_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            if params["search"] == "ranked.safetensors":
                return [
                    {"modelId": "creator/size-only"},
                    {"modelId": "creator/sha-and-size"},
                ]
            return []
        if url.endswith("/creator/size-only"):
            return {
                "siblings": [
                    {"rfilename": "ranked.safetensors", "size": len(payload)}
                ]
            }
        if url.endswith("/creator/sha-and-size"):
            return {
                "siblings": [
                    {
                        "rfilename": "ranked.safetensors",
                        "size": len(payload),
                        "lfs": {"sha256": sha, "size": len(payload)},
                    }
                ]
            }
        return {"files": []} if "/by-hash/" in url else {"items": []}

    async def fake_stream(url: str, part_path: Path) -> None:
        streamed_urls.append(url)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="ranked.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert streamed_urls == [
        "https://huggingface.co/creator/sha-and-size/resolve/main/ranked.safetensors"
    ]


@pytest.mark.anyio
async def test_hugging_face_size_only_candidate_with_required_hash_does_not_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_payload = b"expected"
    wrong_payload = b"wrong-bytes"
    expected_sha = hashlib.sha256(expected_payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            if params["search"] == "final-mismatch.safetensors":
                return [{"modelId": "creator/final-mismatch-repo"}]
            return []
        if "huggingface.co" in url:
            return {
                "siblings": [
                    {"rfilename": "final-mismatch.safetensors", "size": len(wrong_payload)}
                ]
            }
        if "/by-hash/" in url:
            return {"files": []}
        return {"items": []}

    async def fake_stream(url: str, part_path: Path) -> None:
        raise AssertionError("size-only provider candidates should not be downloaded when a hash is required")

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="final-mismatch.safetensors",
                    checksum=f"sha256:{expected_sha}",
                    size_bytes=len(wrong_payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.failed_count == 1
    assert result.model_summary.models[0].status == "needs_manual_download"
    assert not (noofy_root / "checkpoints" / "final-mismatch.safetensors").exists()
    assert not list((noofy_root / ".downloads").glob("*/**/*.part"))


@pytest.mark.anyio
async def test_hugging_face_multiple_equally_reliable_candidates_try_next_after_verification_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"verified"
    wrong_payload = b"mismatch"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    log_store = LogStore()
    streamed_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            if params["search"] == "ambiguous.safetensors":
                return [
                    {"modelId": "creator/ambiguous-a"},
                    {"modelId": "creator/ambiguous-b"},
                ]
            return []
        if "huggingface.co" in url:
            return {
                "siblings": [
                    {
                        "rfilename": "ambiguous.safetensors",
                        "size": len(payload),
                        "lfs": {"sha256": sha, "size": len(payload)},
                    }
                ]
            }
        if "/by-hash/" in url:
            return {"files": []}
        return {"items": []}

    async def fake_stream(url: str, part_path: Path) -> None:
        streamed_urls.append(url)
        part_path.parent.mkdir(parents=True, exist_ok=True)
        if url.endswith("ambiguous-a/resolve/main/ambiguous.safetensors"):
            part_path.write_bytes(wrong_payload)
        else:
            part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        log_store=log_store,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
            log_store=log_store,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="ambiguous.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    logs = str(log_store.list_events().model_dump(mode="json"))
    assert result.downloaded_count == 1
    assert result.model_summary.models[0].status == "available"
    assert streamed_urls == [
        "https://huggingface.co/creator/ambiguous-a/resolve/main/ambiguous.safetensors",
        "https://huggingface.co/creator/ambiguous-b/resolve/main/ambiguous.safetensors",
    ]
    assert (noofy_root / "checkpoints" / "ambiguous.safetensors").read_bytes() == payload
    assert "ambiguous reliable matches" not in logs
    assert not list((noofy_root / ".downloads").glob("*/**/*.part"))


@pytest.mark.anyio
async def test_hugging_face_repo_inspection_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noofy_root = tmp_path / "Noofy Models"
    inspected_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            return [
                {"modelId": f"creator/repo-{params['search']}-{index}"}
                for index in range(20)
            ]
        if "huggingface.co" in url:
            inspected_urls.append(url)
            return {"siblings": []}
        if "/by-hash/" in url:
            return {"files": []}
        return {"items": []}

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(
        availability_module,
        "_stream_url",
        lambda url, part_path: pytest.fail("download should not start"),
    )

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="bounded-v1-5-model.safetensors",
                    checksum="sha256:" + ("1" * 64),
                    size_bytes=123,
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.failed_count == 1
    assert len(inspected_urls) == availability_module.HUGGING_FACE_REPO_INSPECTION_LIMIT


@pytest.mark.anyio
async def test_download_tries_civitai_when_hugging_face_has_no_reliable_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"civitai-model"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    fetched_urls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        assert method == "GET"
        fetched_urls.append(url)
        if "huggingface" in url:
            return [{"modelId": "creator/repo", "siblings": [{"rfilename": "almost.safetensors", "size": len(payload)}]}]
        if "/by-hash/" in url:
            return {"files": []}
        return {
            "items": [
                {
                    "modelVersions": [
                        {
                            "files": [
                                {
                                    "name": "civitai-model.safetensors",
                                    "downloadUrl": "https://civitai.com/api/download/models/123",
                                    "hashes": {"SHA256": sha},
                                }
                            ]
                        }
                    ]
                }
            ]
        }

    async def fake_stream(url: str, part_path: Path) -> None:
        assert url == "https://civitai.com/api/download/models/123"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="loras",
                    filename="civitai-model.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert f"https://civitai.com/api/v1/model-versions/by-hash/{sha}" in fetched_urls
    assert "https://civitai.com/api/v1/models" in fetched_urls
    assert fetched_urls.index(f"https://civitai.com/api/v1/model-versions/by-hash/{sha}") < fetched_urls.index("https://civitai.com/api/v1/models")
    assert (noofy_root / "loras" / "civitai-model.safetensors").read_bytes() == payload


@pytest.mark.anyio
async def test_civitai_by_hash_is_attempted_before_query_and_uses_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"civitai-hash-fixture"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        calls.append((method, url, params, headers))
        if url == "https://huggingface.co/api/models":
            return []
        assert url == f"https://civitai.com/api/v1/model-versions/by-hash/{sha}"
        assert method == "GET"
        assert params == {}
        assert headers == {"Authorization": "Bearer civitai-token"}
        return _fixture("civitai_by_hash_response.json")

    async def fake_stream(url: str, part_path: Path) -> None:
        assert url == "https://civitai.com/api/download/models/111"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: "civitai-token" if provider == "civitai" else None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="loras",
                    filename="civitai-hash-fixture.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    by_hash_calls = [
        call for call in calls if call[1] == f"https://civitai.com/api/v1/model-versions/by-hash/{sha}"
    ]
    assert len(by_hash_calls) == 1
    assert by_hash_calls[0][3] == {"Authorization": "Bearer civitai-token"}
    assert "https://civitai.com/api/v1/models" not in [call[1] for call in calls]


@pytest.mark.anyio
async def test_civitai_by_hash_public_request_works_without_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"civitai-hash-fixture"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    civitai_headers: list[dict[str, str]] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            return []
        civitai_headers.append(headers)
        return _fixture("civitai_by_hash_response.json")

    async def fake_stream(url: str, part_path: Path) -> None:
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="loras",
                    filename="civitai-hash-fixture.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert civitai_headers == [{}]


@pytest.mark.anyio
async def test_civitai_by_hash_falls_back_to_query_when_unreliable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"civitai-query-fixture"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    calls: list[str] = []

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        calls.append(url)
        if url == "https://huggingface.co/api/models":
            return []
        if "/by-hash/" in url:
            return {
                "files": [
                    {
                        "name": "similar-query-fixture.safetensors",
                        "downloadUrl": "https://civitai.com/api/download/models/wrong",
                        "size": len(payload),
                    }
                ]
            }
        assert url == "https://civitai.com/api/v1/models"
        assert params == {
            "query": "civitai-query-fixture.safetensors",
            "limit": str(availability_module.PROVIDER_SEARCH_LIMIT),
        }
        return _fixture("civitai_query_response.json")

    async def fake_stream(url: str, part_path: Path) -> None:
        assert url == "https://civitai.com/api/download/models/444"
        part_path.parent.mkdir(parents=True, exist_ok=True)
        part_path.write_bytes(payload)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="civitai-query-fixture.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.downloaded_count == 1
    assert f"https://civitai.com/api/v1/model-versions/by-hash/{sha}" in calls
    assert "https://civitai.com/api/v1/models" in calls
    assert calls.index(f"https://civitai.com/api/v1/model-versions/by-hash/{sha}") < calls.index("https://civitai.com/api/v1/models")


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_hugging_face_auth_errors_return_clear_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    noofy_root = tmp_path / "Noofy Models"
    streamed = False

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        raise _http_error(url, status_code)

    async def fake_stream(url: str, part_path: Path) -> None:
        nonlocal streamed
        streamed = True

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="private-hf.safetensors",
                    size_bytes=100,
                    verification_level="filename_size",
                )
            ]
        )
    )

    assert streamed is False
    assert result.failed_count == 1
    assert "Hugging Face API key" in (result.model_summary.models[0].message or "")


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_civitai_auth_errors_return_clear_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    payload = b"private-civitai"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if url == "https://huggingface.co/api/models":
            return []
        raise _http_error(url, status_code)

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(
        availability_module,
        "_stream_url",
        lambda url, part_path: pytest.fail("download should not start"),
    )

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="loras",
                    filename="private-civitai.safetensors",
                    checksum=f"sha256:{sha}",
                    size_bytes=len(payload),
                    verification_level="sha256_size",
                )
            ]
        )
    )

    assert result.failed_count == 1
    assert "Civitai API key" in (result.model_summary.models[0].message or "")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider_url", "expected_message", "use_hash"),
    [
        ("https://huggingface.co/api/models", "Hugging Face rate limit", False),
        ("https://civitai.com/api/v1/model-versions/by-hash", "Civitai rate limit", True),
    ],
)
async def test_provider_rate_limits_do_not_crash_import_flow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_url: str,
    expected_message: str,
    use_hash: bool,
) -> None:
    payload = b"rate-limited"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if provider_url == "https://huggingface.co/api/models":
            raise _http_error(url, 429)
        if url == "https://huggingface.co/api/models":
            return []
        if "/by-hash/" in url:
            raise _http_error(url, 429)
        return {"items": []}

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(
        availability_module,
        "_stream_url",
        lambda url, part_path: pytest.fail("download should not start"),
    )

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="rate-limited.safetensors",
                    checksum=f"sha256:{sha}" if use_hash else None,
                    size_bytes=len(payload),
                    verification_level="sha256_size" if use_hash else "filename_size",
                )
            ]
        )
    )

    assert result.failed_count == 1
    assert expected_message in (result.model_summary.models[0].message or "")


@pytest.mark.anyio
async def test_provider_search_does_not_download_similar_filename_only_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noofy_root = tmp_path / "Noofy Models"
    streamed = False

    async def fake_fetch_json(
        method: str,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
    ) -> object:
        if "huggingface" in url:
            return [
                {
                    "modelId": "creator/repo",
                    "siblings": [
                        {
                            "rfilename": "target-model-v2.safetensors",
                            "size": 123,
                        }
                    ],
                }
            ]
        return {"items": []}

    async def fake_stream(url: str, part_path: Path) -> None:
        nonlocal streamed
        streamed = True

    service = _service(
        noofy_root=noofy_root,
        provider_resolver=ProviderModelResolver(
            api_key_resolver=lambda provider: None,
            fetch_json=fake_fetch_json,
        ),
    )
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)

    result = await service.download_missing(
        _package(
            [
                RequiredModel(
                    folder="checkpoints",
                    filename="target-model.safetensors",
                    size_bytes=123,
                    verification_level="filename_size",
                )
            ]
        )
    )

    assert streamed is False
    assert result.downloaded_count == 0
    assert result.failed_count == 1
    assert result.status == "completed_with_errors"
    assert result.user_facing_message == "Some downloads failed."
    assert result.model_summary.models[0].status == "needs_manual_download"
    assert result.model_summary.models[0].status_label == "Needs manual download"
    assert "reliable automatic download source" in (result.model_summary.models[0].message or "")
    assert not (noofy_root / "checkpoints" / "target-model.safetensors").exists()
