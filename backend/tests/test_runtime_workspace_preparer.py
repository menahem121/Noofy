from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.isolation import CapsuleLock, InstallStatus, SmokeTestStatus
from app.runtime.profiles import RuntimeProfileErrorCode, RuntimeProfileResolutionError, load_runtime_profile_catalog
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore


def _capsule_lock(
    *,
    runner_fingerprint: str = "sha256:" + ("c" * 64),
) -> CapsuleLock:
    return CapsuleLock.model_validate(
        {
            "schema_version": "0.1.0",
            "workflow": {
                "publisher_id": "noofy",
                "package_id": "text_to_image_v0",
                "version": "0.1.0",
                "trust_level": "noofy_verified",
                "source": "bundled",
            },
            "engine": {
                "type": "comfyui",
                "comfyui_version": "milestone-1",
                "core_source_hash": "sha256:" + ("a" * 64),
            },
            "runtime": {
                "runtime_profile_id": "noofy-comfyui-v1-default",
                "runtime_profile_variant_id": "darwin-arm64-mps-dev",
                "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
                "runtime_profile_catalog_version": "0.1.0",
                "fingerprint_schema_version": "0.1.0",
                "dependency_env_fingerprint": "sha256:" + ("b" * 64),
                "runner_fingerprint": runner_fingerprint,
                "capsule_fingerprint": "sha256:" + ("d" * 64),
                "os": "darwin",
                "architecture": "arm64",
                "python_version": "3.11",
                "python_build_id": "cpython-3.11-noofy-dev",
                "gpu_backend": "mps",
                "dependency_lock_hash": "sha256:" + ("e" * 64),
                "runner_workspace_hash": "sha256:" + ("f" * 64),
            },
            "custom_nodes": [],
            "dependencies": {
                "lock_file": "core.lock",
                "install_policy": "core_only_no_community",
            },
            "models": [],
            "trust": {
                "level": "noofy_verified",
                "publisher": "Noofy",
            },
        }
    )


def _preparer(tmp_path: Path) -> RuntimeWorkspacePreparer:
    return RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=LogStore(),
    )


def _profile_checked_preparer(tmp_path: Path) -> RuntimeWorkspacePreparer:
    return RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        runtime_profile_catalog=load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json")),
        log_store=LogStore(),
    )


def _source_preparer(tmp_path: Path) -> RuntimeWorkspacePreparer:
    source_dir = tmp_path / "ComfyUI-source"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (source_dir / "folder_paths.py").write_text("# fake folder paths\n", encoding="utf-8")
    (source_dir / "comfy").mkdir()
    (source_dir / "comfy" / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()
    model_view_dir = tmp_path / "model-view"
    model_view_dir.mkdir()
    (model_view_dir / "checkpoints").mkdir()
    return RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=source_dir,
        model_view_dir=model_view_dir,
        log_store=LogStore(),
    )


def test_prepare_creates_staged_dependency_and_runner_manifests(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()

    prepared = preparer.prepare(capsule)

    assert prepared.dependency_env_manifest.status is InstallStatus.CHECKING_COMPATIBILITY
    assert prepared.runner_workspace_manifest.status is InstallStatus.CHECKING_COMPATIBILITY
    assert prepared.dependency_env_path.name == f"dep-env-{capsule.runtime.dependency_env_fingerprint.removeprefix('sha256:')}"
    assert prepared.runner_workspace_path.name == f"runner-workspace-{capsule.runtime.runner_fingerprint.removeprefix('sha256:')}"
    assert (prepared.dependency_env_path / "manifest.json").exists()
    assert (prepared.runner_workspace_path / "manifest.json").exists()
    assert preparer.dependency_env_store.read(prepared.dependency_env_manifest.fingerprint).status is InstallStatus.CHECKING_COMPATIBILITY
    assert preparer.runner_workspace_store.read(prepared.runner_workspace_manifest.fingerprint).status is InstallStatus.CHECKING_COMPATIBILITY


def test_prepare_reuses_existing_ready_manifests(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()
    first = preparer.mark_ready(
        preparer.prepare(capsule),
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    second = preparer.prepare(capsule)

    assert second.dependency_env_path == first.dependency_env_path
    assert second.runner_workspace_path == first.runner_workspace_path
    assert second.dependency_env_manifest.status is InstallStatus.READY
    assert second.runner_workspace_manifest.status is InstallStatus.READY
    assert second.runner_workspace_manifest.smoke_test_status is SmokeTestStatus.PASSED


def test_mark_ready_promotes_staged_manifests_after_smoke_passes(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()
    staged_dependency = preparer._dependency_env_manifest(capsule).model_copy(
        update={"status": InstallStatus.PREPARING}
    )
    staged_runner = preparer._runner_workspace_manifest(capsule).model_copy(
        update={"status": InstallStatus.PREPARING}
    )
    preparer.dependency_env_store.save_staged(staged_dependency)
    preparer.runner_workspace_store.save_staged(staged_runner)

    prepared = preparer.prepare(capsule)
    ready = preparer.mark_ready(
        prepared,
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    assert ready.dependency_env_manifest.status is InstallStatus.READY
    assert ready.runner_workspace_manifest.status is InstallStatus.READY
    assert ready.runner_workspace_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert preparer.dependency_env_store.read(prepared.dependency_env_manifest.fingerprint).status is InstallStatus.READY
    assert preparer.runner_workspace_store.read(prepared.runner_workspace_manifest.fingerprint).status is InstallStatus.READY
    assert preparer.runner_workspace_store.read(prepared.runner_workspace_manifest.fingerprint).smoke_test_status is SmokeTestStatus.PASSED


def test_different_runner_workspaces_can_share_ready_dependency_env(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    first_capsule = _capsule_lock(runner_fingerprint="sha256:" + ("c" * 64))
    second_capsule = _capsule_lock(runner_fingerprint="sha256:" + ("d" * 64))
    first = preparer.mark_ready(
        preparer.prepare(first_capsule),
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=first_capsule.workflow.package_id,
    )

    second = preparer.prepare(second_capsule)

    assert second.dependency_env_path == first.dependency_env_path
    assert second.dependency_env_manifest.status is InstallStatus.READY
    assert second.dependency_env_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert second.runner_workspace_path != first.runner_workspace_path
    assert second.runner_workspace_manifest.status is InstallStatus.CHECKING_COMPATIBILITY


def test_mark_ready_returns_existing_ready_manifest_without_mutating_it(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()
    ready = preparer.mark_ready(
        preparer.prepare(capsule),
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    second = preparer.mark_ready(
        ready,
        smoke_test_status=SmokeTestStatus.NOT_RUN,
        workflow_id=capsule.workflow.package_id,
    )

    assert second.dependency_env_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert second.runner_workspace_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert preparer.dependency_env_store.read(capsule.runtime.dependency_env_fingerprint).smoke_test_status is SmokeTestStatus.PASSED
    assert preparer.runner_workspace_store.read(capsule.runtime.runner_fingerprint).smoke_test_status is SmokeTestStatus.PASSED


def test_prepare_materializes_runnable_runner_workspace_source_view(tmp_path: Path) -> None:
    preparer = _source_preparer(tmp_path)
    capsule = _capsule_lock()

    prepared = preparer.prepare(capsule)

    assert (prepared.runner_workspace_path / "main.py").exists()
    assert (prepared.runner_workspace_path / "folder_paths.py").exists()
    assert (prepared.runner_workspace_path / "comfy").exists()
    assert (prepared.runner_workspace_path / "custom_nodes").is_dir()
    assert list((prepared.runner_workspace_path / "custom_nodes").iterdir()) == []
    assert (prepared.runner_workspace_path / "models" / "checkpoints").exists()
    assert (prepared.runner_workspace_path / "input").is_dir()
    assert (prepared.runner_workspace_path / "output").is_dir()
    assert (prepared.runner_workspace_path / "temp").is_dir()
    assert (prepared.runner_workspace_path / "user").is_dir()


def test_prepare_refuses_to_reuse_ready_workspace_with_missing_source_view(tmp_path: Path) -> None:
    preparer = _source_preparer(tmp_path)
    capsule = _capsule_lock()
    manifest = preparer._runner_workspace_manifest(capsule)
    preparer.dependency_env_store.write_new(preparer._dependency_env_manifest(capsule))
    preparer.runner_workspace_store.write_new(manifest)

    with pytest.raises(RuntimeError, match="Ready runner workspace is missing materialized entries"):
        preparer.prepare(capsule)


def test_prepare_resolves_runtime_profile_before_staging_artifacts(tmp_path: Path) -> None:
    preparer = _profile_checked_preparer(tmp_path)
    capsule = _capsule_with_catalog_profile()

    prepared = preparer.prepare(capsule)

    assert prepared.dependency_env_manifest.runtime_profile_id == "noofy-comfyui-v1-default"
    assert prepared.runner_workspace_manifest.runtime_profile_variant_id == "darwin-arm64-mps"


def test_prepare_fails_missing_runtime_profile_before_staging_artifacts(tmp_path: Path) -> None:
    preparer = _profile_checked_preparer(tmp_path)
    data = _capsule_with_catalog_profile().model_dump(mode="json")
    data["runtime"]["runtime_profile_id"] = "missing-profile"
    capsule = CapsuleLock.model_validate(data)

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        preparer.prepare(capsule)

    assert exc.value.code is RuntimeProfileErrorCode.MISSING_RUNTIME_PROFILE
    assert list((tmp_path / "envs").glob("*")) == []
    assert list((tmp_path / "runner-workspaces").glob("*")) == []


def test_prepare_fails_profile_hash_mismatch_before_staging_artifacts(tmp_path: Path) -> None:
    preparer = _profile_checked_preparer(tmp_path)
    data = _capsule_with_catalog_profile().model_dump(mode="json")
    data["runtime"]["runtime_profile_manifest_hash"] = "sha256:" + ("8" * 64)
    capsule = CapsuleLock.model_validate(data)

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        preparer.prepare(capsule)

    assert exc.value.code is RuntimeProfileErrorCode.PROFILE_MANIFEST_HASH_MISMATCH
    assert list((tmp_path / "envs").glob("*")) == []
    assert list((tmp_path / "runner-workspaces").glob("*")) == []


def _capsule_with_catalog_profile() -> CapsuleLock:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    profile = catalog.profiles[0]
    variant = profile.variants[0]
    data = _capsule_lock().model_dump(mode="json")
    data["runtime"].update(
        {
            "runtime_profile_id": profile.runtime_profile_id,
            "runtime_profile_variant_id": variant.runtime_profile_variant_id,
            "runtime_profile_manifest_hash": profile.runtime_profile_manifest_hash,
            "runtime_profile_catalog_version": catalog.schema_version,
            "os": variant.os,
            "architecture": variant.architecture,
            "python_version": variant.python_version,
            "python_build_id": variant.python_build_id,
            "gpu_backend": variant.gpu_backend_profile,
            "dependency_lock_hash": variant.core_dependency_lock_hash,
        }
    )
    return CapsuleLock.model_validate(data)
