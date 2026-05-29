"""Tests for the Phase 3 capsule install pipeline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.runtime.capsule_installer import CapsuleInstaller, CapsuleInstallError
from app.runtime.dependencies.custom_nodes import CoreNodeManifest, CoreNodeManifestCatalog, CustomNodeWorkspaceMaterializer
from app.runtime.dependencies.dependency_env import DependencyEnvironmentInstallError, DependencyEnvironmentInstallRequest
from app.runtime.dependencies.dependency_lock import (
    DependencyPolicyErrorCode,
    ResolvedDependencyLock,
    ResolverMetadata,
    with_computed_lock_hash,
)
from app.runtime.install_state import InstallStateStore
from app.runtime.dependencies.isolation import (
    CapsuleLock,
    InstallStatus,
    SmokeStageResult,
    SmokeStageStatus,
    SmokeTestReport,
    SmokeTestStatus,
)
from app.runtime.models.model_store import ModelStore
from app.runtime.profiles import load_runtime_profile_catalog
from app.runtime.smoke_test import RunnerSmokeTestError
from app.runtime.storage.workspace_preparer import RUNTIME_QUARANTINE_FILENAME, RuntimeWorkspacePreparer
from app.runtime.storage.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore


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
    source_policy: dict | None = None,
) -> dict:
    data = {
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
    if source_policy is not None:
        data["source_policy"] = source_policy
    return data


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
    download_headers_resolver=None,
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
        download_headers_resolver=download_headers_resolver,
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


def _passed_smoke_report(*, custom_nodes: bool = False) -> SmokeTestReport:
    return SmokeTestReport(
        dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
        custom_node_import=SmokeStageResult(
            status=SmokeStageStatus.PASSED if custom_nodes else SmokeStageStatus.SKIPPED,
        ),
        runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
        workflow_execution=SmokeStageResult(status=SmokeStageStatus.PASSED),
    )


def _cached_node_materializer() -> CustomNodeWorkspaceMaterializer:
    return CustomNodeWorkspaceMaterializer(
        core_node_manifest_catalog=CoreNodeManifestCatalog(
            manifests=[
                CoreNodeManifest(
                    runtime_profile_id="noofy-comfyui-v1-default",
                    runtime_profile_variant_id="darwin-arm64-mps-dev",
                    runtime_profile_manifest_hash="sha256:" + ("9" * 64),
                    node_types=["KSampler", "LoadImage", "SaveImage"],
                )
            ]
        )
    )


def _write_source_cache_manifest(
    source_dir: Path,
    *,
    source_cache_ref: str = "abc123/source",
    source_ref: str = "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
    source_content_hash: str = "sha256:" + ("1" * 64),
) -> None:
    (source_dir.parent / "noofy-custom-node-source-cache-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "source_kind": "git_zip_archive",
                "source_url": "https://example.test/cached-node/archive/7b3f5d0.zip",
                "source_ref": source_ref,
                "source_content_hash": source_content_hash,
                "source_cache_ref": source_cache_ref,
            }
        ),
        encoding="utf-8",
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
    # Per-capsule model view contains the bytes.
    materialized = Path(state.model_references[0].materialized_path)
    assert materialized.exists()
    assert materialized.read_bytes() == payload
    assert "model-view-" in str(materialized)
    assert state.model_references[0].materialization_strategy in {"hardlink", "symlink", "copy"}
    assert state.model_references[0].materialized_file_verified is True


@pytest.mark.anyio
async def test_prepare_model_download_uses_provider_auth_headers(tmp_path: Path) -> None:
    payload = b"private-civitai-model"
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(
            fingerprint="fp-auth-model",
            models=[
                {
                    **_model_record(payload),
                    "source_urls": [
                        "https://civitai.com/api/download/models/2979642?fileId=2859181"
                    ],
                }
            ],
        )
    )
    seen_headers: list[dict[str, str] | None] = []

    async def downloader(
        url: str,
        dest: Path,
        *,
        headers: dict[str, str] | None = None,
    ) -> int:
        seen_headers.append(headers)
        dest.write_bytes(payload)
        return len(payload)

    installer, _, _ = _build_installer(
        tmp_path,
        downloader=downloader,
        download_headers_resolver=lambda url: {"Authorization": "Bearer civitai-token"}
        if "civitai.com" in url
        else {},
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.READY
    assert seen_headers == [{"Authorization": "Bearer civitai-token"}]


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
    source_dir = tmp_path / "ComfyUI-source"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=source_dir,
        log_store=log_store,
    )
    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.PREPARED_NEEDS_INPUT_SETUP
    assert state.dependency_env_path is not None
    assert state.runner_workspace_path is not None
    assert (Path(state.dependency_env_path) / "manifest.json").exists()
    assert (Path(state.runner_workspace_path) / "manifest.json").exists()
    dependency_policy = state.smoke_test_report.dependency_env.details["source_policy"]
    runner_policy = state.smoke_test_report.runner_health.details["source_policy"]
    assert dependency_policy["trust_level"] == "noofy_verified"
    assert dependency_policy["source_policy"] == "noofy_verified_sources_only"
    assert dependency_policy["policy_status"] == "active"
    assert runner_policy == dependency_policy


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
    source_dir = tmp_path / "ComfyUI-source"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=source_dir,
        log_store=log_store,
    )
    smoke_calls = []

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        smoke_calls.append((capsule_lock, prepared_workspace))
        assert capsule_lock == capsule
        assert (prepared_workspace.dependency_env_path / "manifest.json").exists()
        assert (prepared_workspace.runner_workspace_path / "manifest.json").exists()
        assert state_store.get(capsule.runtime.capsule_fingerprint).status is InstallStatus.SMOKE_TESTING
        return _passed_smoke_report()

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
    assert state.smoke_test_report.workflow_execution.status is SmokeStageStatus.PASSED
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
async def test_prepare_stages_model_view_in_install_transaction_then_promotes_after_smoke(tmp_path: Path) -> None:
    payload = b"transactional-model-view"
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(fingerprint="fp-transactional-model-view", models=[_model_record(payload)])
    )
    download_targets: list[Path] = []

    async def downloader(url: str, dest: Path) -> int:
        download_targets.append(dest)
        dest.write_bytes(payload)
        return len(payload)

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
    source_dir = tmp_path / "ComfyUI-source-transactional-model-view"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=source_dir,
        log_store=log_store,
    )
    staged_model_paths: list[Path] = []

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        models_path = prepared_workspace.runner_workspace_path / "models"
        assert models_path.exists()
        staged_model_paths.append(models_path.resolve())
        assert "transactions" in str(models_path.resolve())
        return _passed_smoke_report()

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.READY
    assert staged_model_paths
    assert download_targets
    assert "transactions" in str(download_targets[0])
    assert "model-blobs" in str(download_targets[0])
    materialized = Path(state.model_references[0].materialized_path)
    assert materialized.exists()
    assert "transactions" not in str(materialized)
    assert materialized.is_relative_to(tmp_path / "materialized" / "views")
    assert list((tmp_path / "transactions").glob("install-*")) == []
    assert Path(state.runner_workspace_path or "").exists()


@pytest.mark.anyio
async def test_prepare_writes_smoke_report_into_failed_install_transaction(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-smoke-report", models=[]))

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

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        return SmokeTestReport(
            dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
            custom_node_import=SmokeStageResult(status=SmokeStageStatus.SKIPPED),
            runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
            workflow_execution=SmokeStageResult(
                status=SmokeStageStatus.FAILED,
                message="fixture output was missing",
            ),
        )

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError, match="fixture output was missing"):
        await installer.prepare(capsule)

    transaction_dir = next((tmp_path / "transactions").glob("install-*"))
    smoke_report = json.loads((transaction_dir / "smoke-logs" / "smoke-report.json").read_text(encoding="utf-8"))
    assert smoke_report["workflow_id"] == "test_workflow"
    assert smoke_report["report"]["workflow_execution"]["status"] == "failed"


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

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
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
    assert not workspace_preparer.runner_workspace_store.manifest_path(capsule.runtime.runner_fingerprint).exists()
    quarantined_transaction = next((tmp_path / "transactions").glob("install-*"))
    dependency_quarantine = next(quarantined_transaction.glob("dependency-envs/*/" + RUNTIME_QUARANTINE_FILENAME))
    runner_quarantine = next(quarantined_transaction.glob("runner-workspaces/*/" + RUNTIME_QUARANTINE_FILENAME))
    transaction_quarantine = quarantined_transaction / RUNTIME_QUARANTINE_FILENAME
    assert dependency_quarantine.exists()
    assert runner_quarantine.exists()
    assert transaction_quarantine.exists()
    dependency_marker = json.loads(dependency_quarantine.read_text(encoding="utf-8"))
    runner_marker = json.loads(runner_quarantine.read_text(encoding="utf-8"))
    assert dependency_marker["artifact_kind"] == "dependency_env"
    assert runner_marker["artifact_kind"] == "runner_workspace"
    assert dependency_marker["reason"] == "runner never became healthy"
    assert runner_marker["reason"] == "runner never became healthy"
    assert dependency_marker["retain_until"]
    assert runner_marker["retain_until"]


@pytest.mark.anyio
async def test_prepare_failed_smoke_stage_quarantines_staged_workspace(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-smoke-stage-fail", models=[]))

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

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        return SmokeTestReport(
            dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
            custom_node_import=SmokeStageResult(status=SmokeStageStatus.SKIPPED),
            runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
            workflow_execution=SmokeStageResult(
                status=SmokeStageStatus.FAILED,
                message="fixture output was missing",
            ),
        )

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError, match="fixture output was missing"):
        await installer.prepare(capsule)

    quarantined_transaction = next((tmp_path / "transactions").glob("install-*"))
    dependency_quarantine = next(quarantined_transaction.glob("dependency-envs/*/" + RUNTIME_QUARANTINE_FILENAME))
    runner_quarantine = next(quarantined_transaction.glob("runner-workspaces/*/" + RUNTIME_QUARANTINE_FILENAME))
    assert json.loads(dependency_quarantine.read_text(encoding="utf-8"))["reason"] == "fixture output was missing"
    assert json.loads(runner_quarantine.read_text(encoding="utf-8"))["reason"] == "fixture output was missing"


@pytest.mark.anyio
async def test_prepare_runner_startup_failure_persists_smoke_report(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(_capsule_lock_data(fingerprint="fp-smoke-startup-fail", models=[]))

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
    startup_report = SmokeTestReport(
        dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
        custom_node_import=SmokeStageResult(status=SmokeStageStatus.SKIPPED),
        runner_health=SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message="startup timeout",
        ),
        workflow_execution=SmokeStageResult(status=SmokeStageStatus.NOT_RUN),
    )

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        raise RunnerSmokeTestError("Runner smoke test failed: startup timeout", report=startup_report)

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError, match="startup timeout") as exc_info:
        await installer.prepare(capsule)

    assert exc_info.value.state.smoke_test_status is SmokeTestStatus.FAILED
    assert exc_info.value.state.smoke_test_report.runner_health.status is SmokeStageStatus.FAILED
    assert exc_info.value.state.smoke_test_report.runner_health.message == "startup timeout"
    assert (
        exc_info.value.state.smoke_test_report.runner_health.details["source_policy"]["source_policy"]
        == "noofy_verified_sources_only"
    )


@pytest.mark.anyio
async def test_prepare_custom_node_capsule_marks_ready_after_all_smoke_stages_pass(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(
            fingerprint="fp-custom-node-ready",
            models=[],
            custom_nodes=[
                {
                    "package_id": "custom-node-a",
                    "source": "https://example.invalid/custom-node-a.git",
                    "trust_level": "quarantined_community",
                    "node_types": ["CustomNodeA"],
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

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        return _passed_smoke_report(custom_nodes=True)

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.READY
    assert state.smoke_test_status is SmokeTestStatus.PASSED
    runner_manifest = workspace_preparer.runner_workspace_store.read(capsule.runtime.runner_fingerprint)
    assert runner_manifest.status is InstallStatus.READY


@pytest.mark.anyio
async def test_prepare_marks_ready_when_runner_smoke_has_no_execution_fixture(tmp_path: Path) -> None:
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(
            fingerprint="fp-no-execution-fixture",
            models=[],
            custom_nodes=[
                {
                    "package_id": "custom-node-a",
                    "source": "https://example.invalid/custom-node-a.git",
                    "trust_level": "quarantined_community",
                    "node_types": ["CustomNodeA"],
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

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        return SmokeTestReport(
            dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
            custom_node_import=SmokeStageResult(status=SmokeStageStatus.PASSED),
            runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
            workflow_execution=SmokeStageResult(
                status=SmokeStageStatus.BLOCKED,
                message="No workflow execution smoke fixture is configured.",
            ),
        )

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.READY
    assert state.smoke_test_status is SmokeTestStatus.PASSED


@pytest.mark.anyio
async def test_prepare_imported_custom_node_capsule_uses_app_workflow_id_for_source_files(tmp_path: Path) -> None:
    source_files_dir = tmp_path / "imported-source-files"
    custom_node_dir = source_files_dir / "custom_nodes" / "custom-node-a"
    custom_node_dir.mkdir(parents=True)
    (custom_node_dir / "node.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    (source_files_dir / "comfyui_graph.json").write_text(
        json.dumps({"1": {"class_type": "CustomNodeA", "inputs": {}}}),
        encoding="utf-8",
    )
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(
            fingerprint="fp-imported-workflow-id",
            models=[],
            custom_nodes=[
                {
                    "package_id": "custom-node-a",
                    "source": "bundled_from_creator_machine",
                    "trust_level": "quarantined_community",
                    "node_types": ["CustomNodeA"],
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
    app_workflow_id = "unknown__test_workflow__0.1.0"
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        custom_node_materializer=_cached_node_materializer(),
        custom_node_source_files_dir_resolver=lambda workflow_id: (
            source_files_dir if workflow_id == app_workflow_id else None
        ),
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=log_store,
    )

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        return _passed_smoke_report(custom_nodes=True)

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    state = await installer.prepare(capsule, workflow_id=app_workflow_id)

    assert state.status is InstallStatus.READY
    assert state.runner_workspace_path is not None
    runner_workspace = Path(state.runner_workspace_path)
    assert (runner_workspace / "custom_nodes" / "custom-node-a" / "node.py").exists()


@pytest.mark.anyio
async def test_cached_non_bundled_custom_node_stays_staged_until_smoke_passes(tmp_path: Path) -> None:
    source_files_dir = tmp_path / "source-files"
    source_files_dir.mkdir()
    (source_files_dir / "comfyui_graph.json").write_text(
        json.dumps({"1": {"class_type": "CachedNode", "inputs": {}}}),
        encoding="utf-8",
    )
    cached_source_dir = tmp_path / "source-cache" / "abc123" / "source"
    cached_source_dir.mkdir(parents=True)
    (cached_source_dir / "node.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    _write_source_cache_manifest(cached_source_dir)
    capsule = CapsuleLock.model_validate(
        _capsule_lock_data(
            fingerprint="fp-cached-custom-node",
            models=[],
            custom_nodes=[
                {
                    "package_id": "cached-node",
                    "source": "registry_metadata:cached-node",
                    "source_ref": "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                    "source_content_hash": "sha256:" + ("1" * 64),
                    "source_cache_ref": "abc123/source",
                    "trust_level": "quarantined_community",
                    "node_types": ["CachedNode"],
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
        custom_node_materializer=_cached_node_materializer(),
        custom_node_source_files_dir=source_files_dir,
        custom_node_source_cache_dir=tmp_path / "source-cache",
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=log_store,
    )
    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        log_store=log_store,
    )

    state = await installer.prepare(capsule)

    assert state.status is InstallStatus.PREPARED_NEEDS_INPUT_SETUP
    assert state.smoke_test_status is SmokeTestStatus.NOT_RUN
    assert state.runner_workspace_path is not None
    runner_workspace = Path(state.runner_workspace_path)
    assert (runner_workspace / "custom_nodes" / "cached-node" / "node.py").exists()
    assert not workspace_preparer.runner_workspace_store.exists(capsule.runtime.runner_fingerprint)


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
        runtime_profile_catalog=load_runtime_profile_catalog(Path(__file__).parent.parent / "app/runtime/profile_catalog.json"),
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
async def test_prepare_source_policy_blocked_capsule_marks_blocked_before_model_download(tmp_path: Path) -> None:
    data = _capsule_lock_data(
        fingerprint="fp-source-policy-blocked",
        models=[
            {
                "id": "m1",
                "sha256": "sha256:" + ("1" * 64),
                "size_bytes": 10,
                "source_urls": ["https://example.invalid/model"],
                "comfyui_folder": "checkpoints",
                "filename": "model.safetensors",
            }
        ],
        source_policy={
            "trust_level": "unsupported",
            "source_policy": "blocked",
            "package_source_type": "noofy_archive_import",
            "automatic_preparation_allowed": False,
            "model_source_trust": "hashed",
            "policy_status": "blocked_by_policy",
        },
    )
    capsule = CapsuleLock.model_validate(data)

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError("source-policy blocked capsules must not download models")

    installer, state_store, _ = _build_installer(tmp_path, downloader=downloader)

    with pytest.raises(CapsuleInstallError, match="source policy") as exc:
        await installer.prepare(capsule)

    assert exc.value.state.status is InstallStatus.BLOCKED_BY_POLICY
    persisted = state_store.get("fp-source-policy-blocked")
    assert persisted is not None
    assert persisted.status is InstallStatus.BLOCKED_BY_POLICY
    assert list((tmp_path / "blobs").glob("*")) == []


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

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
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

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("runner never became healthy")
        return _passed_smoke_report()

    installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )

    with pytest.raises(CapsuleInstallError):
        await installer.prepare(capsule)
    assert not workspace_preparer.runner_workspace_store.manifest_path(capsule.runtime.runner_fingerprint).exists()
    assert len(list((tmp_path / "transactions").glob("install-*/quarantine.json"))) == 1

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
