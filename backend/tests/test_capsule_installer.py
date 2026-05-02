"""Tests for the Phase 3 capsule install pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.capsule_installer import CapsuleInstaller, CapsuleInstallError
from app.runtime.dependency_env import DependencyEnvironmentInstallError, DependencyEnvironmentInstallRequest
from app.runtime.dependency_lock import (
    DependencyPolicyErrorCode,
    ResolvedDependencyLock,
    ResolverMetadata,
    with_computed_lock_hash,
)
from app.runtime.install_state import InstallStateStore
from app.runtime.isolation import CapsuleLock, InstallStatus, SmokeTestStatus
from app.runtime.model_store import ModelStore
from app.runtime.profiles import load_runtime_profile_catalog
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore


class _FailingDependencyEnvInstaller:
    def install(self, request: DependencyEnvironmentInstallRequest) -> None:
        raise DependencyEnvironmentInstallError(
            DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
            "dependency source is not approved",
        )


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
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps-dev",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "phase3-dep",
            "runner_fingerprint": "phase3-runner",
            "capsule_fingerprint": fingerprint,
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
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


def _resolved_lock_for_capsule(capsule: CapsuleLock) -> ResolvedDependencyLock:
    return with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id=capsule.runtime.runtime_profile_id,
            runtime_profile_variant_id=capsule.runtime.runtime_profile_variant_id,
            runtime_profile_manifest_hash=capsule.runtime.runtime_profile_manifest_hash,
            install_policy_version=capsule.dependencies.install_policy,
            resolver=ResolverMetadata(name="uv", version="0.9.0"),
            wheels=[],
        )
    )


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
async def test_prepare_records_runtime_workspace_paths_when_preparer_is_configured(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-workspace", models=[]))

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("no models should be downloaded")

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
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=log_store,
    )
    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.READY
    assert state.dependency_env_path is not None
    assert state.runner_workspace_path is not None
    assert (Path(state.dependency_env_path) / "manifest.json").exists()
    assert (Path(state.runner_workspace_path) / "manifest.json").exists()


@pytest.mark.anyio
async def test_prepare_runs_workspace_smoke_test_before_ready(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-smoke", models=[]))

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("no models should be downloaded")

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
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=log_store,
    )
    smoke_calls = []

    async def smoke_test(capsule_lock, prepared_workspace) -> None:
        smoke_calls.append((capsule_lock, prepared_workspace))
        assert capsule_lock == capsule
        assert (prepared_workspace.dependency_env_path / "manifest.json").exists()
        assert (prepared_workspace.runner_workspace_path / "manifest.json").exists()
        assert state_store.get(capsule.runtime.capsule_fingerprint).status is InstallStatus.CHECKING_COMPATIBILITY

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert len(smoke_calls) == 1
    assert state.status is InstallStatus.READY
    assert state.smoke_test_status is SmokeTestStatus.PASSED
    persisted = state_store.get("fp-smoke")
    assert persisted is not None
    assert persisted.status is InstallStatus.READY
    assert persisted.smoke_test_status is SmokeTestStatus.PASSED
    assert persisted.runtime_profile_variant_id == capsule.runtime.runtime_profile_variant_id
    assert persisted.runtime_profile_manifest_hash == capsule.runtime.runtime_profile_manifest_hash
    assert persisted.runtime_profile_catalog_version == capsule.runtime.runtime_profile_catalog_version
    assert persisted.dependency_env_fingerprint == capsule.runtime.dependency_env_fingerprint
    assert persisted.runner_workspace_fingerprint == capsule.runtime.runner_fingerprint
    runner_manifest = workspace_preparer.runner_workspace_store.read(capsule.runtime.runner_fingerprint)
    assert runner_manifest.status is InstallStatus.READY
    assert runner_manifest.smoke_test_status is SmokeTestStatus.PASSED


@pytest.mark.anyio
async def test_prepare_smoke_failure_marks_state_failed_and_not_ready(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-smoke-fail", models=[]))

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("no models should be downloaded")

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
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=log_store,
    )

    async def smoke_test(capsule_lock, prepared_workspace) -> None:
        raise RuntimeError("runner never became healthy")

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError, match="runner never became healthy") as exc_info:
        await installer.prepare(capsule)

    state = exc_info.value.state
    assert state.status is InstallStatus.FAILED
    assert state.smoke_test_status is SmokeTestStatus.FAILED
    assert state.installed_at is None
    persisted = state_store.get("fp-smoke-fail")
    assert persisted is not None
    assert persisted.status is InstallStatus.FAILED
    assert persisted.smoke_test_status is SmokeTestStatus.FAILED
    runner_manifest = workspace_preparer.runner_workspace_store.read(capsule.runtime.runner_fingerprint)
    assert runner_manifest.status is InstallStatus.CHECKING_COMPATIBILITY
    assert runner_manifest.smoke_test_status is SmokeTestStatus.NOT_RUN


@pytest.mark.anyio
async def test_prepare_runtime_profile_failure_marks_unsupported_runtime_profile(tmp_path: Path) -> None:
    data = _capsule_lock_data(fingerprint="fp-runtime-profile", models=[])
    data["runtime"]["runtime_profile_id"] = "missing-profile"
    capsule = CapsuleLock.model_validate(data)

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("no models should be downloaded")

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
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        runtime_profile_catalog=load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json")),
        log_store=log_store,
    )
    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError) as exc:
        await installer.prepare(capsule)

    assert exc.value.state.status is InstallStatus.UNSUPPORTED_RUNTIME_PROFILE
    persisted = state_store.get("fp-runtime-profile")
    assert persisted is not None
    assert persisted.status is InstallStatus.UNSUPPORTED_RUNTIME_PROFILE
    assert list((tmp_path / "envs").glob("*")) == []
    assert list((tmp_path / "runner-workspaces").glob("*")) == []


@pytest.mark.anyio
async def test_prepare_dependency_policy_failure_marks_blocked_by_policy(tmp_path: Path) -> None:
    data = _capsule_lock_data(fingerprint="fp-dependency-policy", models=[])
    capsule = CapsuleLock.model_validate(data)
    lock = _resolved_lock_for_capsule(capsule)
    data["runtime"]["dependency_lock_hash"] = lock.lock_hash
    capsule = CapsuleLock.model_validate(data)

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("no models should be downloaded")

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
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=_FailingDependencyEnvInstaller(),
        dependency_locks={lock.lock_hash: lock},
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=log_store,
    )
    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError, match="dependency source is not approved") as exc:
        await installer.prepare(capsule)

    assert exc.value.state.status is InstallStatus.BLOCKED_BY_POLICY
    persisted = state_store.get("fp-dependency-policy")
    assert persisted is not None
    assert persisted.status is InstallStatus.BLOCKED_BY_POLICY
    assert list((tmp_path / "envs").glob("*")) == []


@pytest.mark.anyio
async def test_prepare_custom_node_capsule_prepares_dependencies_without_marking_ready(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(
            fingerprint="fp-custom-node",
            models=[],
            custom_nodes=[
                {
                    "package_id": "custom-node-a",
                    "source": "https://example.invalid/custom-node-a.git",
                    "trust_level": "quarantined_community",
                }
            ],
        )
    )

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("no models should be downloaded")

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
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=log_store,
    )

    async def smoke_test(capsule_lock, prepared_workspace) -> None:
        raise AssertionError("custom-node capsules are not smoke-tested until custom-node materialization exists")

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.PREPARED_NEEDS_INPUT_SETUP
    assert state.dependency_env_path is not None
    assert state.runner_workspace_path is not None
    assert state.smoke_test_status is SmokeTestStatus.NOT_RUN


@pytest.mark.anyio
async def test_prepare_can_retry_after_smoke_failure_and_promote_staged_workspace(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-smoke-retry", models=[]))

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("no models should be downloaded")

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
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=log_store,
    )
    attempts = {"count": 0}

    async def smoke_test(capsule_lock, prepared_workspace) -> None:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("runner never became healthy")

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError):
        await installer.prepare(capsule)
    first_runner_manifest = workspace_preparer.runner_workspace_store.read(capsule.runtime.runner_fingerprint)
    assert first_runner_manifest.status is InstallStatus.CHECKING_COMPATIBILITY

    second = await installer.prepare(capsule)

    assert attempts["count"] == 2
    assert second.status is InstallStatus.READY
    assert second.smoke_test_status is SmokeTestStatus.PASSED
    runner_manifest = workspace_preparer.runner_workspace_store.read(capsule.runtime.runner_fingerprint)
    assert runner_manifest.status is InstallStatus.READY
    assert runner_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert Path(second.runner_workspace_path) == workspace_preparer.runner_workspace_store.artifact_dir(
        capsule.runtime.runner_fingerprint
    )


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
async def test_prepare_custom_node_capsule_without_workspace_stops_before_ready(tmp_path: Path) -> None:
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

    result = await installer.prepare(capsule)

    state = state_store.get("fp-custom-node")
    assert state is not None
    assert result.status is InstallStatus.PREPARED_NEEDS_INPUT_SETUP
    assert state.status is InstallStatus.PREPARED_NEEDS_INPUT_SETUP
    assert state.installed_at is not None
