"""Tests for the Phase 3 capsule install pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.capsule_installer import CapsuleInstaller, CapsuleInstallError
from app.runtime.install_state import InstallStateStore
from app.runtime.isolation import CapsuleLock, InstallStatus, SmokeTestStatus
from app.runtime.model_store import ModelStore


def _capsule_lock_data(
    *,
    fingerprint: str,
    models: list[dict],
    custom_nodes: list[dict] | None = None,
) -> dict:
    return {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "test_workflow",
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "phase3-core",
        },
        "runtime": {
            "dependency_env_fingerprint": "phase3-dep",
            "runner_fingerprint": "phase3-runner",
            "capsule_fingerprint": fingerprint,
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase3-deps",
            "runner_workspace_hash": "phase3-workspace",
        },
        "custom_nodes": custom_nodes or [],
        "dependencies": {"lock_file": "phase3", "install_policy": "core_only_no_community"},
        "models": models,
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }


def _model_record(content: bytes, *, model_id: str = "m1", folder: str = "checkpoints", filename: str = "m.safetensors") -> dict:
    return {
        "id": model_id,
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
        "source_urls": ["https://example.invalid/m"],
        "comfyui_folder": folder,
        "filename": filename,
    }


def _build_installer(
    tmp_path: Path,
    *,
    downloader,
) -> tuple[CapsuleInstaller, InstallStateStore, LogStore]:
    log_store = LogStore()
    state_store = InstallStateStore(tmp_path / "install-state")
    model_store = ModelStore(
        blobs_dir=tmp_path / "blobs",
        refs_dir=tmp_path / "refs",
        materialized_dir=tmp_path / "materialized",
        transactions_dir=tmp_path / "transactions",
        log_store=log_store,
        downloader=downloader,
    )
    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        log_store=log_store,
    )
    return installer, state_store, log_store


@pytest.mark.anyio
async def test_prepare_no_models_transitions_to_ready(tmp_path: Path) -> None:
    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("downloader must not be called when no models are required")

    installer, state_store, log_store = _build_installer(tmp_path, downloader=downloader)
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-empty", models=[]))

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.READY
    assert state.installed_at is not None
    assert state.last_error is None
    # Transitions are diagnosed in order.
    transition_messages = [
        event.message for event in log_store.list_events().events
        if event.source == "capsule.installer"
    ]
    assert "Capsule install: Preparing workflow" in transition_messages
    assert "Capsule install: Downloading required models" in transition_messages
    assert "Capsule install: Checking compatibility" in transition_messages
    assert "Capsule install: Ready" in transition_messages
    # Persisted to disk.
    persisted = state_store.get("fp-empty")
    assert persisted is not None and persisted.status is InstallStatus.READY


@pytest.mark.anyio
async def test_prepare_with_model_download_succeeds(tmp_path: Path) -> None:
    payload = b"model-bytes" * 32
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(fingerprint="fp-ok", models=[_model_record(payload)])
    )

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(payload)
        return len(payload)

    installer, state_store, _ = _build_installer(tmp_path, downloader=downloader)

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.READY
    assert state.smoke_test_status is SmokeTestStatus.NOT_RUN
    # Materialized link contains the bytes.
    materialized = tmp_path / "materialized" / "checkpoints" / "m.safetensors"
    assert materialized.exists()
    assert materialized.read_bytes() == payload


@pytest.mark.anyio
async def test_prepare_failure_marks_state_failed_and_does_not_become_ready(tmp_path: Path) -> None:
    payload = b"correct"
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(fingerprint="fp-bad", models=[_model_record(payload)])
    )

    async def bad_downloader(url: str, dest: Path) -> int:
        # Hand back the wrong bytes -> hash mismatch.
        dest.write_bytes(b"wrong")
        return len(b"wrong")

    installer, state_store, log_store = _build_installer(tmp_path, downloader=bad_downloader)

    with pytest.raises(CapsuleInstallError) as exc_info:
        await installer.prepare(capsule)

    state = exc_info.value.state
    assert state.status is InstallStatus.FAILED
    assert state.last_error
    persisted = state_store.get("fp-bad")
    assert persisted is not None
    assert persisted.status is InstallStatus.FAILED
    assert persisted.installed_at is None  # never marked ready
    # An error transition was diagnosed.
    error_events = [event for event in log_store.list_events().events if event.level == "error"]
    assert any("Capsule install: Cannot prepare automatically" in event.message for event in error_events)
    # No materialized link was written.
    assert not (tmp_path / "materialized" / "checkpoints" / "m.safetensors").exists()


@pytest.mark.anyio
async def test_recovery_from_failure_clears_last_error(tmp_path: Path) -> None:
    """A second prepare() after fixing the source must not leave stale error text."""
    payload = b"recovered-bytes"
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(fingerprint="fp-recover", models=[_model_record(payload)])
    )

    attempt = {"count": 0}

    async def flaky(url: str, dest: Path) -> int:
        attempt["count"] += 1
        if attempt["count"] == 1:
            dest.write_bytes(b"wrong")
            return len(b"wrong")
        dest.write_bytes(payload)
        return len(payload)

    installer, state_store, _ = _build_installer(tmp_path, downloader=flaky)

    with pytest.raises(CapsuleInstallError):
        await installer.prepare(capsule)
    assert state_store.get("fp-recover").last_error is not None

    second = await installer.prepare(capsule)

    assert second.status is InstallStatus.READY
    assert second.last_error is None


@pytest.mark.anyio
async def test_get_state_does_not_trigger_install(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-readonly", models=[]))

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("get_state must not download")

    installer, _, _ = _build_installer(tmp_path, downloader=downloader)

    state = installer.get_state(capsule)
    assert state.status is InstallStatus.PENDING


@pytest.mark.anyio
async def test_prepare_rejects_custom_node_capsule_in_verified_core_installer(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(
            fingerprint="fp-custom-node",
            models=[],
            custom_nodes=[
                {
                    "package_id": "community-node",
                    "source": "https://example.invalid/community-node.git",
                    "trust_level": "quarantined_community",
                    "node_types": ["CommunityNode"],
                }
            ],
        )
    )

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("custom-node capsules must be rejected before model download")

    installer, state_store, _ = _build_installer(tmp_path, downloader=downloader)

    with pytest.raises(CapsuleInstallError):
        await installer.prepare(capsule)

    state = state_store.get("fp-custom-node")
    assert state is not None
    assert state.status is InstallStatus.FAILED
    assert state.installed_at is None
