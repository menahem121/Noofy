"""End-to-end test for the engine service install API surface (Phase 3)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.engine.service import EngineService
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.install_state import InstallStateStore
from app.runtime.isolation import InstallStatus, SmokeTestStatus
from app.runtime.model_store import ModelStore
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessHandle, RunnerProcessStatus
from app.runtime.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerStatus,
    RunnerSupervisor,
)
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore
from app.workflows.capsule import CAPSULE_LOCK_FILENAME, CapsuleLockLoader
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    mode = "external"
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"
    repo_dir = Path("/tmp/noofy-comfyui")
    python_executable = "/opt/noofy/python"
    managed_host = "127.0.0.1"
    environment = None


class StubAdapter:
    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        pass

    async def list_available_models(self):
        return []


def _bundled_packages_dir() -> Path:
    return Path("app/workflows/packages")


def _build_service(
    tmp_path: Path,
    *,
    packages_dir: Path | None = None,
    user_packages_dir: Path | None = None,
    downloader=None,
    runner_coordinator_factory=None,
    include_workspace_preparer: bool = True,
) -> EngineService:
    log_store = LogStore()
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url=StubRuntimeManager.base_url,
            ws_url=StubRuntimeManager.ws_url,
            fingerprint=CORE_RUNNER_FINGERPRINT,
        ),
        StubAdapter(),
    )

    async def default_downloader(url: str, dest: Path) -> int:
        raise AssertionError("downloader was not expected to be invoked")

    state_store = InstallStateStore(tmp_path / "install-state")
    materialized_dir = tmp_path / "materialized"
    model_store = ModelStore(
        blobs_dir=tmp_path / "blobs",
        refs_dir=tmp_path / "refs",
        materialized_dir=materialized_dir,
        transactions_dir=tmp_path / "transactions",
        log_store=log_store,
        downloader=downloader or default_downloader,
    )
    workspace_preparer = None
    workspace_smoke_test = None
    if include_workspace_preparer:
        comfyui_source_dir = tmp_path / "comfyui-source"
        comfyui_source_dir.mkdir()
        (comfyui_source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
        (comfyui_source_dir / "folder_paths.py").write_text("# fake\n", encoding="utf-8")
        workspace_preparer = RuntimeWorkspacePreparer(
            dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
            runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
            comfyui_source_dir=comfyui_source_dir,
            model_view_dir=materialized_dir,
            log_store=log_store,
        )

        async def workspace_smoke_test(capsule_lock, prepared_workspace) -> None:
            return None

    capsule_installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=workspace_smoke_test,
        log_store=log_store,
    )
    runner_process_coordinator = (
        runner_coordinator_factory(supervisor) if runner_coordinator_factory is not None else None
    )

    packages_dir = packages_dir or _bundled_packages_dir()
    return EngineService(
        workflow_loader=WorkflowPackageLoader(
            packages_dir,
            user_packages_dir=user_packages_dir,
        ),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=log_store,
        capsule_loader=CapsuleLockLoader(
            packages_dir,
            user_packages_dir=user_packages_dir,
        ),
        capsule_installer=capsule_installer,
        runner_process_coordinator=runner_process_coordinator,
    )


def _write_workflow_with_capsule(
    packages_dir: Path,
    workflow_id: str,
    capsule_payload: dict,
) -> None:
    workflow_dir = packages_dir / workflow_id
    workflow_dir.mkdir(parents=True)
    package = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    package["metadata"]["id"] = workflow_id
    package["metadata"]["name"] = workflow_id
    package["required_models"] = [
        {
            "folder": model["comfyui_folder"],
            "filename": model["filename"],
            "source_url": model["source_urls"][0] if model["source_urls"] else None,
            "checksum": f"sha256:{model['sha256']}",
        }
        for model in capsule_payload["models"]
    ]
    (workflow_dir / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (workflow_dir / CAPSULE_LOCK_FILENAME).write_text(json.dumps(capsule_payload), encoding="utf-8")


def _runner_capsule_payload(*, runner_char: str = "6") -> dict:
    return {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "runner_workflow",
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "phase4-core",
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps-dev",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "phase4-dep",
            "runner_fingerprint": "sha256:" + (runner_char * 64),
            "capsule_fingerprint": "runner_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase4-deps",
            "runner_workspace_hash": "phase4-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase4", "install_policy": "core_only_no_community"},
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }


class RecordingRunnerCoordinator:
    def __init__(self, runner_supervisor: RunnerSupervisor) -> None:
        self.runner_supervisor = runner_supervisor
        self.started_specs: list[RunnerLaunchSpec] = []
        self.stopped_runner_ids: list[str] = []
        self.stop_all_called = False

    async def start_runner(self, spec: RunnerLaunchSpec, *, workflow_id: str | None = None) -> RunnerProcessHandle:
        self.started_specs.append(spec)
        descriptor = RunnerDescriptor(
            runner_id=spec.runner_id,
            kind=spec.kind,
            base_url=f"http://{spec.host}:9100",
            ws_url=f"ws://{spec.host}:9100/ws",
            fingerprint=spec.fingerprint,
            status=RunnerStatus.READY,
        )
        self.runner_supervisor.upsert_runner(descriptor, StubAdapter())
        if workflow_id is not None:
            self.runner_supervisor.bind_workflow_runner(workflow_id, descriptor.runner_id)
        return RunnerProcessHandle(
            runner_id=descriptor.runner_id,
            descriptor=descriptor,
            pid=4242,
            command=(spec.python_executable, spec.entrypoint),
        )

    async def stop_runner(self, runner_id: str) -> RunnerProcessStatus:
        self.stopped_runner_ids.append(runner_id)
        self.runner_supervisor.update_runner_status(runner_id, RunnerStatus.STOPPED)
        descriptor = self.runner_supervisor.get_runner(runner_id)
        return RunnerProcessStatus(
            runner_id=runner_id,
            status=RunnerStatus.STOPPED,
            base_url=descriptor.base_url,
            ws_url=descriptor.ws_url or "",
            pid=None,
        )

    async def stop_all_runners(self) -> list[RunnerProcessStatus]:
        self.stop_all_called = True
        return []


def test_get_install_state_for_bundled_workflow_starts_pending(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    payload = service.get_install_state("text_to_image_v0")

    assert payload["workflow_id"] == "text_to_image_v0"
    assert payload["status"] == InstallStatus.PENDING.value
    assert payload["user_facing_message"] == "Not started"


def test_get_install_state_for_unknown_workflow_returns_unsupported(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    payload = service.get_install_state("does-not-exist")

    assert payload["status"] == InstallStatus.UNSUPPORTED.value
    assert payload["capsule_fingerprint"] is None


@pytest.mark.anyio
async def test_start_workflow_runner_requires_ready_install_state(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "runner_workflow",
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "phase4-core",
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps-dev",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "phase4-dep",
            "runner_fingerprint": "sha256:" + ("1" * 64),
            "capsule_fingerprint": "runner_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase4-deps",
            "runner_workspace_hash": "phase4-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase4", "install_policy": "core_only_no_community"},
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "install_not_ready"
    assert result["install_status"] == InstallStatus.PENDING.value
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_binds_ready_isolated_runner(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "runner_workflow",
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "phase4-core",
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps-dev",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "phase4-dep",
            "runner_fingerprint": "sha256:" + ("2" * 64),
            "capsule_fingerprint": "runner_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase4-deps",
            "runner_workspace_hash": "phase4-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase4", "install_policy": "core_only_no_community"},
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == RunnerStatus.READY.value
    assert result["pid"] == 4242
    assert result["runner"]["kind"] == RunnerKind.ISOLATED_COMFYUI.value
    assert coordinator is not None
    assert coordinator.started_specs[0].fingerprint == "sha256:" + ("2" * 64)
    assert coordinator.started_specs[0].python_executable == "/opt/noofy/python"
    assert coordinator.started_specs[0].working_dir.name.startswith("runner-workspace-")
    assert coordinator.started_specs[0].runner_workspace_path == coordinator.started_specs[0].working_dir
    assert (coordinator.started_specs[0].working_dir / "main.py").exists()
    assert coordinator.started_specs[0].dependency_env_path is not None
    assert coordinator.started_specs[0].dependency_env_path.name.startswith("dep-env-")
    assert coordinator.started_specs[0].extra_args[:2] == [
        "--base-directory",
        str(coordinator.started_specs[0].working_dir),
    ]
    assert "--disable-all-custom-nodes" in coordinator.started_specs[0].extra_args
    assert coordinator.started_specs[0].env["NOOFY_WORKFLOW_ID"] == "runner_workflow"
    package = service.workflow_loader.get_package("runner_workflow")
    assert service.runner_supervisor.acquire_runner(package).runner_id == result["runner"]["runner_id"]


@pytest.mark.anyio
async def test_start_workflow_runner_fails_when_ready_state_lacks_runtime_artifacts(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "runner_workflow",
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "phase4-core",
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps-dev",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "phase4-dep",
            "runner_fingerprint": "sha256:" + ("4" * 64),
            "capsule_fingerprint": "runner_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase4-deps",
            "runner_workspace_hash": "phase4-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase4", "install_policy": "core_only_no_community"},
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
        include_workspace_preparer=False,
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "Prepared runtime artifact paths are missing" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_staged_runtime_manifests(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="5")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )
    capsule_lock = service.capsule_loader.get_bundled_capsule_lock("runner_workflow")
    prepared = service.capsule_installer.workspace_preparer.prepare(capsule_lock)
    service.capsule_installer.install_state_store.update(
        capsule_lock.runtime.capsule_fingerprint,
        status=InstallStatus.READY,
        dependency_env_path=str(prepared.dependency_env_path),
        runner_workspace_path=str(prepared.runner_workspace_path),
        smoke_test_status=SmokeTestStatus.PASSED,
    )

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "Prepared runtime artifact is not ready" in result["error"]
    assert "runner workspace manifest status checking_compatibility" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_invalid_runtime_manifest(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="6")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )
    payload = await service.prepare_workflow("runner_workflow")
    manifest_path = Path(payload["runner_workspace_path"]) / "manifest.json"
    manifest_path.write_text("{not-json", encoding="utf-8")

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "Prepared runtime artifact manifest is invalid" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_manifest_fingerprint_mismatch(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="7")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )
    payload = await service.prepare_workflow("runner_workflow")
    manifest_path = Path(payload["runner_workspace_path"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fingerprint"] = "sha256:" + ("8" * 64)
    manifest["dependency_env_fingerprint"] = "different-dependency-env"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "runner workspace manifest fingerprint mismatch" in result["error"]
    assert "runner workspace dependency environment mismatch" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_requires_passed_smoke_status(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="9")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )
    await service.prepare_workflow("runner_workflow")
    capsule_lock = service.capsule_loader.get_bundled_capsule_lock("runner_workflow")
    service.capsule_installer.install_state_store.update(
        capsule_lock.runtime.capsule_fingerprint,
        smoke_test_status=SmokeTestStatus.NOT_RUN,
    )

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "Prepared runtime smoke test has not passed: not_run" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_stop_workflow_runner_stops_and_unbinds_isolated_runner(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "runner_workflow",
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "phase4-core",
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps-dev",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "phase4-dep",
            "runner_fingerprint": "sha256:" + ("3" * 64),
            "capsule_fingerprint": "runner_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase4-deps",
            "runner_workspace_hash": "phase4-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase4", "install_policy": "core_only_no_community"},
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )
    await service.prepare_workflow("runner_workflow")
    start_result = await service.start_workflow_runner("runner_workflow")

    stop_result = await service.stop_workflow_runner("runner_workflow")

    assert stop_result["status"] == RunnerStatus.STOPPED.value
    assert coordinator is not None
    assert coordinator.stopped_runner_ids == [start_result["runner"]["runner_id"]]
    assert service.runner_supervisor.runner_for_workflow("runner_workflow") is None
    package = service.workflow_loader.get_package("runner_workflow")
    assert service.runner_supervisor.acquire_runner(package).runner_id == CORE_RUNNER_ID


@pytest.mark.anyio
async def test_shutdown_stops_backend_owned_workflow_runners(tmp_path: Path) -> None:
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(tmp_path, runner_coordinator_factory=coordinator_factory)

    await service.shutdown()

    assert coordinator is not None
    assert coordinator.stop_all_called


@pytest.mark.anyio
async def test_prepare_workflow_drives_bundled_capsule_to_ready(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "bundled_empty_workflow",
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
            "capsule_fingerprint": "bundled_empty_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase3-deps",
            "runner_workspace_hash": "phase3-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase3", "install_policy": "core_only_no_community"},
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }
    _write_workflow_with_capsule(packages_dir, "bundled_empty_workflow", capsule_payload)
    service = _build_service(tmp_path, packages_dir=packages_dir)

    payload = await service.prepare_workflow("bundled_empty_workflow")

    assert payload["status"] == InstallStatus.READY.value
    assert payload["user_facing_message"] == "Ready"
    assert payload["installed_at"] is not None
    assert payload["dependency_env_path"] is not None
    assert payload["runner_workspace_path"] is not None
    # The state is persisted: a follow-up GET sees Ready too.
    followup = service.get_install_state("bundled_empty_workflow")
    assert followup["status"] == InstallStatus.READY.value
    assert followup["dependency_env_path"] == payload["dependency_env_path"]
    assert followup["runner_workspace_path"] == payload["runner_workspace_path"]


@pytest.mark.anyio
async def test_prepare_bundled_workflow_with_model_failure_returns_failed_payload(tmp_path: Path) -> None:
    payload = b"correct-bytes"

    async def bad_downloader(url: str, dest: Path) -> int:
        dest.write_bytes(b"WRONG")
        return len(b"WRONG")

    packages_dir = tmp_path / "packages"
    capsule_payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
                "package_id": "bundled_model_workflow",
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
            "capsule_fingerprint": "bundled_model_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase3-deps",
            "runner_workspace_hash": "phase3-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase3", "install_policy": "core_only_no_community"},
        "models": [
            {
                "id": "user-model",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "source_urls": ["https://example.invalid/m"],
                "comfyui_folder": "checkpoints",
                "filename": "user-model.safetensors",
            }
        ],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }
    _write_workflow_with_capsule(packages_dir, "bundled_model_workflow", capsule_payload)

    service = _build_service(tmp_path, packages_dir=packages_dir, downloader=bad_downloader)

    result = await service.prepare_workflow("bundled_model_workflow")

    assert result["status"] == InstallStatus.FAILED.value
    assert result["user_facing_message"] == "Cannot prepare automatically"
    assert result["last_error"]
    assert result["installed_at"] is None
    # No materialized model from a failed prepare.
    assert not (tmp_path / "materialized" / "checkpoints" / "user-model.safetensors").exists()


@pytest.mark.anyio
async def test_prepare_user_capsule_is_unsupported_in_phase3(tmp_path: Path) -> None:
    user_packages_dir = tmp_path / "user_packages"
    workflow_dir = user_packages_dir / "user_capsule_workflow"
    workflow_dir.mkdir(parents=True)

    bundled_pkg = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    bundled_pkg["metadata"]["id"] = "user_capsule_workflow"
    bundled_pkg["metadata"]["name"] = "User Capsule Workflow"
    (workflow_dir / "package.json").write_text(json.dumps(bundled_pkg), encoding="utf-8")
    (workflow_dir / CAPSULE_LOCK_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "workflow": {
                    "publisher_id": "noofy",
                    "package_id": "user_capsule_workflow",
                    "version": "0.1.0",
                    "trust_level": "noofy_verified",
                    "source": "user",
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
                    "capsule_fingerprint": "user_capsule_workflow-fp",
                    "os": "any",
                    "architecture": "any",
                    "python_version": "3.11",
                    "python_build_id": "cpython-3.11-noofy-dev",
                    "gpu_backend": "any",
                    "dependency_lock_hash": "phase3-deps",
                    "runner_workspace_hash": "phase3-workspace",
                },
                "custom_nodes": [],
                "dependencies": {"lock_file": "phase3", "install_policy": "core_only_no_community"},
                "models": [],
                "trust": {"level": "noofy_verified", "publisher": "Noofy"},
            }
        ),
        encoding="utf-8",
    )

    service = _build_service(tmp_path, user_packages_dir=user_packages_dir)

    result = await service.prepare_workflow("user_capsule_workflow")

    assert result["status"] == InstallStatus.UNSUPPORTED.value
    assert result["capsule_fingerprint"] is None

    shutil.rmtree(user_packages_dir, ignore_errors=True)
