from pathlib import Path

import pytest

from app.runtime.dependencies.isolation import (
    DependencyEnvManifest,
    InstallStatus,
    RunnerWorkspaceManifest,
    SmokeTestStatus,
)
from app.runtime.storage.workspace_store import (
    DependencyEnvManifestStore,
    ManifestStoreError,
    RunnerWorkspaceManifestStore,
)


def _dependency_manifest(
    fingerprint: str = "sha256:" + ("a" * 64),
    *,
    status: InstallStatus = InstallStatus.PREPARING,
) -> DependencyEnvManifest:
    return DependencyEnvManifest(
        schema_version="0.1.0",
        fingerprint=fingerprint,
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_catalog_version="0.1.0",
        fingerprint_schema_version="0.1.0",
        python_version="3.11",
        python_build_id="cpython-3.11-noofy-1",
        os="darwin",
        architecture="arm64",
        gpu_backend="mps",
        dependency_lock_hash="sha256:" + ("b" * 64),
        install_policy_version="1",
        status=status,
        smoke_test_status=SmokeTestStatus.NOT_RUN,
    )


def _runner_manifest(
    fingerprint: str = "sha256:" + ("c" * 64),
    *,
    status: InstallStatus = InstallStatus.PREPARING,
) -> RunnerWorkspaceManifest:
    return RunnerWorkspaceManifest(
        schema_version="0.1.0",
        fingerprint=fingerprint,
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_catalog_version="0.1.0",
        fingerprint_schema_version="0.1.0",
        dependency_env_fingerprint="sha256:" + ("a" * 64),
        comfyui_version="0.3.0",
        comfyui_source_hash="sha256:" + ("d" * 64),
        enabled_custom_node_hash="sha256:" + ("e" * 64),
        launch_config_hash="sha256:" + ("f" * 64),
        model_view_hash="sha256:" + ("1" * 64),
        status=status,
        smoke_test_status=SmokeTestStatus.NOT_RUN,
    )


def test_dependency_env_store_writes_manifest_under_fingerprint_dir(tmp_path: Path) -> None:
    store = DependencyEnvManifestStore(tmp_path)
    manifest = _dependency_manifest(status=InstallStatus.READY)

    path = store.write_new(manifest)

    assert path == tmp_path / f"dep-env-{manifest.fingerprint.removeprefix('sha256:')}" / "manifest.json"
    assert store.read(manifest.fingerprint) == manifest
    assert store.exists(manifest.fingerprint)


def test_store_rejects_duplicate_write_new(tmp_path: Path) -> None:
    store = DependencyEnvManifestStore(tmp_path)
    manifest = _dependency_manifest()

    store.write_new(manifest)

    with pytest.raises(ManifestStoreError):
        store.write_new(manifest)


def test_store_allows_staged_manifest_updates_until_ready(tmp_path: Path) -> None:
    store = DependencyEnvManifestStore(tmp_path)
    manifest = _dependency_manifest(status=InstallStatus.PREPARING)
    store.save_staged(manifest)

    updated = manifest.model_copy(update={"status": InstallStatus.CHECKING_COMPATIBILITY})
    store.save_staged(updated)

    assert store.read(manifest.fingerprint).status is InstallStatus.CHECKING_COMPATIBILITY


def test_store_allows_single_transition_from_staged_to_ready(tmp_path: Path) -> None:
    store = DependencyEnvManifestStore(tmp_path)
    manifest = _dependency_manifest(status=InstallStatus.PREPARING)
    store.save_staged(manifest)

    ready = manifest.model_copy(update={"status": InstallStatus.READY})
    store.save_staged(ready)

    assert store.read(manifest.fingerprint).status is InstallStatus.READY


def test_store_refuses_to_mutate_ready_manifest(tmp_path: Path) -> None:
    store = DependencyEnvManifestStore(tmp_path)
    ready = _dependency_manifest(status=InstallStatus.READY)
    store.save_staged(ready)

    with pytest.raises(ManifestStoreError):
        store.save_staged(ready.model_copy(update={"status": InstallStatus.FAILED}))

    assert store.read(ready.fingerprint).status is InstallStatus.READY


def test_runner_workspace_store_uses_runner_workspace_prefix(tmp_path: Path) -> None:
    store = RunnerWorkspaceManifestStore(tmp_path)
    manifest = _runner_manifest(status=InstallStatus.READY)

    path = store.write_new(manifest)

    assert path == tmp_path / f"runner-workspace-{manifest.fingerprint.removeprefix('sha256:')}" / "manifest.json"
    assert store.read(manifest.fingerprint) == manifest
