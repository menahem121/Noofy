"""End-to-end test for the engine service install API surface (Phase 3)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.diagnostics import LogStore
from app.engine.models import EngineJob, JobProgress
from app.engine.service import EngineService, _smoke_execution_fixture_for_capsule
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.install_state import InstallStateStore
from app.runtime.dependencies.isolation import (
    InstallState,
    InstallStatus,
    SmokeStageResult,
    SmokeStageStatus,
    SmokeTestReport,
    SmokeTestStatus,
)
from app.runtime.memory.memory_governor import MachineMemorySnapshot, MemoryBackend, MemoryPressureLevel
from app.runtime.models.model_store import ModelStore
from app.runtime.runners.runner_process import RunnerLaunchSpec, RunnerProcessHandle, RunnerProcessStatus
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    RunnerMemoryEstimateConfidence,
    RunnerMemoryEstimateSource,
    RunnerStatus,
    RunnerSupervisor,
)
from app.runtime.storage.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.storage.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore
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
    def __init__(self) -> None:
        self.release_memory_calls = 0

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        pass

    async def list_available_models(self):
        return []

    async def release_memory(self) -> None:
        self.release_memory_calls += 1


class RunCapableAdapter(StubAdapter):
    """StubAdapter that can also accept run submissions."""

    def __init__(self) -> None:
        super().__init__()
        self.run_calls: list[dict] = []

    async def run_workflow(self, workflow_package, graph, inputs, options) -> EngineJob:
        del graph, options
        self.run_calls.append(dict(inputs))
        return EngineJob(
            job_id=f"job-{len(self.run_calls)}",
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )

    async def get_progress(self, job_id: str, since_preview_sequence: int | None = None) -> JobProgress:
        del since_preview_sequence
        return JobProgress(job_id=job_id, status="running")

    async def cancel_job(self, job_id: str) -> JobProgress:
        return JobProgress(job_id=job_id, status="canceled")


class StubMemoryObserver:
    def __init__(self, snapshot: MachineMemorySnapshot | list[MachineMemorySnapshot]) -> None:
        self._snapshots = snapshot if isinstance(snapshot, list) else [snapshot]
        self._index = 0

    def snapshot(self) -> MachineMemorySnapshot:
        snapshot = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return snapshot


def _bundled_packages_dir() -> Path:
    return Path("app/workflows/packages")


def _build_service(
    tmp_path: Path,
    *,
    packages_dir: Path | None = None,
    user_packages_dir: Path | None = None,
    downloader=None,
    local_model_roots: list[Path] | None = None,
    runner_coordinator_factory=None,
    include_workspace_preparer: bool = True,
    memory_observer=None,
    comfyui_sidecar_service=None,
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
        local_model_roots=local_model_roots,
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

        async def workspace_smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
            return _passed_smoke_report(custom_nodes=bool(capsule_lock.custom_nodes))

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
        memory_observer=memory_observer,
        comfyui_sidecar_service=comfyui_sidecar_service,
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


def _runner_capsule_payload_with_model(payload: bytes, *, runner_char: str = "a") -> dict:
    capsule_payload = _runner_capsule_payload(runner_char=runner_char)
    capsule_payload["models"] = [
        {
            "id": "runner-model",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "source_urls": ["https://example.invalid/runner-model"],
            "comfyui_folder": "checkpoints",
            "filename": "runner-model.safetensors",
        }
    ]
    return capsule_payload


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
            runner_workspace_fingerprint=spec.runner_workspace_fingerprint,
            dependency_env_fingerprint=spec.dependency_env_fingerprint,
            runner_process_compatibility_key=spec.runner_process_compatibility_key,
            model_view_fingerprint=spec.model_view_fingerprint,
            runtime_profile_id=spec.runtime_profile_id,
            runtime_profile_variant_id=spec.runtime_profile_variant_id,
            memory_class=spec.memory_class,
            pid=4242,
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


class FailingStopAllRunnerCoordinator(RecordingRunnerCoordinator):
    async def stop_all_runners(self) -> list[RunnerProcessStatus]:
        self.stop_all_called = True
        raise RuntimeError("runner cleanup failed")


class RecordingSidecarService:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_get_install_state_reports_no_custom_node_workflow_as_ready(tmp_path: Path) -> None:
    """Core-runner workflows have nothing to prepare, so they never sit in a
    fake "pending" state that makes the UI announce preparation forever."""
    service = _build_service(tmp_path)

    payload = service.get_install_state("text_to_image_v0")

    assert payload["workflow_id"] == "text_to_image_v0"
    assert payload["status"] == InstallStatus.READY.value
    assert payload["user_facing_message"] == "Ready"
    assert payload["requires_preparation"] is False


def test_get_install_state_for_custom_node_workflow_starts_pending(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload()
    capsule_payload["custom_nodes"] = [
        {
            "package_id": "custom-node-a",
            "source": "https://example.invalid/custom-node-a.git",
            "trust_level": "quarantined_community",
            "node_types": ["CustomNodeA"],
        }
    ]
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    service = _build_service(tmp_path, packages_dir=packages_dir)

    payload = service.get_install_state("runner_workflow")

    assert payload["status"] == InstallStatus.PENDING.value
    assert payload["user_facing_message"] == "Not started"
    assert payload["requires_preparation"] is True


def test_workflow_status_keeps_retryable_prepare_failures_preparable(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload()
    capsule_payload["custom_nodes"] = [
        {
            "package_id": "custom-node-a",
            "source": "https://example.invalid/custom-node-a.git",
            "trust_level": "quarantined_community",
            "node_types": ["CustomNodeA"],
        }
    ]
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    service = _build_service(tmp_path, packages_dir=packages_dir)
    capsule_lock = service.capsule_loader.get_bundled_capsule_lock("runner_workflow")

    for status in (
        InstallStatus.FAILED,
        InstallStatus.CANNOT_PREPARE_AUTOMATICALLY,
        InstallStatus.BLOCKED_BY_POLICY,
        InstallStatus.UNSUPPORTED_RUNTIME_PROFILE,
    ):
        service.capsule_installer.install_state_store.update(
            capsule_lock.runtime.capsule_fingerprint,
            status=status,
            last_error="previous preparation failed",
        )

        payload = service.workflow_status("runner_workflow")

        assert payload["can_prepare"] is True
        action_kinds = {action["kind"] for action in payload["required_actions"]}
        assert "prepare_workflow" in action_kinds
        assert "review_preparation_issue" in action_kinds


def test_get_install_state_for_unknown_workflow_returns_unsupported(tmp_path: Path) -> None:
    service = _build_service(tmp_path)

    payload = service.get_install_state("does-not-exist")

    assert payload["status"] == InstallStatus.UNSUPPORTED.value
    assert payload["capsule_fingerprint"] is None


@pytest.mark.anyio
async def test_start_workflow_runner_waits_during_runtime_activation(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    assert service.runner_supervisor.begin_runtime_activation() == []

    result = await service.start_workflow_runner("text_to_image_v0")

    assert result["status"] == RunnerStatus.QUEUED_PENDING_SWITCH.value
    assert result["error"] == "ComfyUI runtime activation is in progress."
    service.runner_supervisor.end_runtime_activation()


@pytest.mark.anyio
async def test_prepare_workflow_waits_during_runtime_activation(
    tmp_path: Path,
) -> None:
    service = _build_service(tmp_path)
    assert service.runner_supervisor.begin_runtime_activation() == []

    result = await service.prepare_workflow("text_to_image_v0")

    assert result["status"] == RunnerStatus.QUEUED_PENDING_SWITCH.value
    assert result["error"] == "ComfyUI runtime activation is in progress."
    service.runner_supervisor.end_runtime_activation()


def test_install_payload_distinguishes_phase5i_statuses() -> None:
    service = EngineService.__new__(EngineService)
    statuses = [
        InstallStatus.READY,
        InstallStatus.PREPARED_NEEDS_INPUT_SETUP,
        InstallStatus.CANNOT_PREPARE_AUTOMATICALLY,
        InstallStatus.BLOCKED_BY_POLICY,
        InstallStatus.UNSUPPORTED_RUNTIME_PROFILE,
        InstallStatus.FAILED,
    ]

    payloads = [
        service._install_payload(
            "workflow",
            InstallState(
                schema_version="0.1.0",
                capsule_fingerprint=f"capsule-{status.value}",
                status=status,
                smoke_test_status=SmokeTestStatus.NOT_RUN,
            ),
        )
        for status in statuses
    ]

    assert [payload["status"] for payload in payloads] == [status.value for status in statuses]
    assert all(payload["user_facing_message"] for payload in payloads)
    assert all("developer_details_available" in payload for payload in payloads)


def test_smoke_execution_fixture_resolver_reads_workflow_package_fixture(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="d")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    package_path = packages_dir / "runner_workflow" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package["smoke_tests"] = {
        "workflow_execution": {
            "name": "tiny-fixture",
            "prompt": {"1": {"class_type": "NoOp", "inputs": {}}},
            "required_node_types": ["NoOp"],
            "expected_output_node_count": 1,
            "expected_output_node_ids": ["1"],
            "timeout_seconds": 4,
        }
    }
    package_path.write_text(json.dumps(package), encoding="utf-8")
    service = _build_service(tmp_path, packages_dir=packages_dir)
    capsule_lock = service.capsule_loader.get_bundled_capsule_lock("runner_workflow")

    fixture = _smoke_execution_fixture_for_capsule(
        capsule_lock,
        workflow_loader=service.workflow_loader,
    )

    assert fixture is not None
    assert fixture.name == "tiny-fixture"
    assert fixture.prompt == {"1": {"class_type": "NoOp", "inputs": {}}}
    assert fixture.required_node_types == ["NoOp"]
    assert fixture.expected_output_node_count == 1
    assert fixture.expected_output_node_ids == ["1"]
    assert fixture.timeout_seconds == 4


def test_smoke_execution_fixture_resolver_generates_default_for_core_package_without_fixture(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="e")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    package_path = packages_dir / "runner_workflow" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package.pop("smoke_tests", None)
    package_path.write_text(json.dumps(package), encoding="utf-8")
    service = _build_service(tmp_path, packages_dir=packages_dir)
    capsule_lock = service.capsule_loader.get_bundled_capsule_lock("runner_workflow")

    fixture = _smoke_execution_fixture_for_capsule(
        capsule_lock,
        workflow_loader=service.workflow_loader,
    )

    assert fixture is not None
    assert fixture.name == "default-core-empty-image"
    assert fixture.prompt["1"]["class_type"] == "EmptyImage"
    assert fixture.prompt["2"]["class_type"] == "SaveImage"
    assert fixture.required_node_types == ("EmptyImage", "SaveImage")
    assert fixture.expected_output_node_ids == ("2",)


def test_smoke_execution_fixture_resolver_does_not_generate_default_for_custom_nodes(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="f")
    capsule_payload["custom_nodes"] = [
        {
            "package_id": "custom-node-a",
            "source": "https://example.invalid/custom-node-a.git",
            "trust_level": "quarantined_community",
            "node_types": ["CustomNodeA"],
        }
    ]
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    package_path = packages_dir / "runner_workflow" / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    package.pop("smoke_tests", None)
    package_path.write_text(json.dumps(package), encoding="utf-8")
    service = _build_service(tmp_path, packages_dir=packages_dir)
    capsule_lock = service.capsule_loader.get_bundled_capsule_lock("runner_workflow")

    fixture = _smoke_execution_fixture_for_capsule(
        capsule_lock,
        workflow_loader=service.workflow_loader,
    )

    assert fixture is None


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
    assert coordinator.started_specs[0].runner_workspace_fingerprint == "sha256:" + ("2" * 64)
    assert coordinator.started_specs[0].dependency_env_fingerprint == "phase4-dep"
    assert coordinator.started_specs[0].runner_process_compatibility_key == "sha256:" + ("2" * 64)
    assert coordinator.started_specs[0].runtime_profile_id == "noofy-comfyui-v1-default"
    assert coordinator.started_specs[0].runtime_profile_variant_id == "darwin-arm64-mps-dev"
    assert coordinator.started_specs[0].memory_class is RunnerMemoryClass.GPU_HEAVY
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
    assert "--preview-method" in coordinator.started_specs[0].extra_args
    assert "--preview-size" in coordinator.started_specs[0].extra_args
    assert coordinator.started_specs[0].extra_args[
        coordinator.started_specs[0].extra_args.index("--preview-method") + 1
    ] == "auto"
    assert coordinator.started_specs[0].extra_args[
        coordinator.started_specs[0].extra_args.index("--preview-size") + 1
    ] == "512"
    assert "--disable-all-custom-nodes" in coordinator.started_specs[0].extra_args
    assert coordinator.started_specs[0].env["NOOFY_WORKFLOW_ID"] == "runner_workflow"
    assert result["runner"]["runner_process_compatibility_key"] == "sha256:" + ("2" * 64)
    package = service.workflow_loader.get_package("runner_workflow")
    assert service.runner_supervisor.acquire_runner(package).runner_id == result["runner"]["runner_id"]


@pytest.mark.anyio
async def test_start_workflow_runner_uses_effective_prepared_artifact_fingerprints(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="a")
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
    dependency_fingerprint = "sha256:" + ("b" * 64)
    runner_fingerprint = "sha256:" + ("c" * 64)
    dependency_manifest_path = Path(payload["dependency_env_path"]) / "manifest.json"
    dependency_manifest = json.loads(dependency_manifest_path.read_text(encoding="utf-8"))
    dependency_manifest["fingerprint"] = dependency_fingerprint
    dependency_manifest_path.write_text(json.dumps(dependency_manifest), encoding="utf-8")
    runner_manifest_path = Path(payload["runner_workspace_path"]) / "manifest.json"
    runner_manifest = json.loads(runner_manifest_path.read_text(encoding="utf-8"))
    runner_manifest["fingerprint"] = runner_fingerprint
    runner_manifest["dependency_env_fingerprint"] = dependency_fingerprint
    runner_manifest_path.write_text(json.dumps(runner_manifest), encoding="utf-8")
    capsule_lock = service.capsule_loader.get_bundled_capsule_lock("runner_workflow")
    service.capsule_installer.install_state_store.update(
        capsule_lock.runtime.capsule_fingerprint,
        dependency_env_fingerprint=dependency_fingerprint,
        runner_workspace_fingerprint=runner_fingerprint,
    )

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == RunnerStatus.READY.value
    assert coordinator is not None
    assert coordinator.started_specs[0].fingerprint == runner_fingerprint
    assert coordinator.started_specs[0].runner_workspace_fingerprint == runner_fingerprint
    assert coordinator.started_specs[0].dependency_env_fingerprint == dependency_fingerprint


@pytest.mark.anyio
async def test_start_workflow_runner_reuses_compatible_resident_runner(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
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

    first = await service.start_workflow_runner("runner_workflow")
    second = await service.start_workflow_runner("runner_workflow")

    assert coordinator is not None
    assert first["runner"]["runner_id"] == second["runner"]["runner_id"]
    assert len(coordinator.started_specs) == 1


@pytest.mark.anyio
async def test_start_workflow_runner_queues_pending_switch_when_incompatible_runner_is_busy(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
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
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="busy-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("9" * 64),
            status=RunnerStatus.RUNNING,
            runner_process_compatibility_key="sha256:" + ("9" * 64),
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-1",
            pid=9200,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert coordinator is not None
    assert result["status"] == RunnerStatus.QUEUED_PENDING_SWITCH.value
    assert result["queue_id"] is not None
    assert result["runner"]["runner_id"] == "busy-runner"
    assert result["pid"] == 9200
    assert coordinator.started_specs == []
    assert service.runner_supervisor.get_queued_runner_start(result["queue_id"]).workflow_id == "runner_workflow"


@pytest.mark.anyio
async def test_second_run_for_busy_isolated_runner_queues_behind_active_run(tmp_path: Path) -> None:
    """A second Run press while the workflow's own isolated runner is busy
    queues the run instead of failing with "runner is not ready: running"."""
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="c")
    capsule_payload["custom_nodes"] = [
        {
            "package_id": "custom-node-a",
            "source": "https://example.invalid/custom-node-a.git",
            "trust_level": "quarantined_community",
            "node_types": ["CustomNodeA"],
        }
    ]
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    shutil.copy(
        _bundled_packages_dir() / "text_to_image_v0" / "dashboard.json",
        packages_dir / "runner_workflow" / "dashboard.json",
    )
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
    started = await service.start_workflow_runner("runner_workflow")
    assert started["status"] == RunnerStatus.READY.value
    runner_id = started["runner"]["runner_id"]
    adapter = RunCapableAdapter()
    service.runner_supervisor.upsert_runner(
        service.runner_supervisor.get_runner(runner_id), adapter
    )
    service.runner_supervisor.mark_runner_job_started(
        runner_id, "job-active", workflow_id="runner_workflow"
    )

    queued = await service.run_workflow(
        "runner_workflow", inputs={"prompt": "a lake"}, options={}
    )

    assert isinstance(queued, EngineJob)
    assert queued.status == "queued_pending_memory"
    assert queued.memory_status is not None
    assert queued.memory_status["state"] == "queued_behind_active_run"
    assert "handoff" not in (queued.message or "")
    assert adapter.run_calls == []

    # The queued run is handed off automatically once the active run finishes.
    service.runner_supervisor.mark_runner_job_finished(runner_id, "job-active")
    await asyncio.sleep(0.05)
    assert len(adapter.run_calls) == 1


@pytest.mark.anyio
async def test_queued_pending_switch_handoff_starts_after_runner_finishes(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
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
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="busy-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("9" * 64),
            status=RunnerStatus.RUNNING,
            runner_process_compatibility_key="sha256:" + ("9" * 64),
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-1",
            pid=9200,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")
    queued = await service.start_workflow_runner("runner_workflow")

    service.runner_supervisor.mark_runner_job_finished("busy-runner", "job-1")
    handoff = await service.handoff_next_queued_runner_start(released_runner_id="busy-runner")

    assert coordinator is not None
    assert queued["status"] == RunnerStatus.QUEUED_PENDING_SWITCH.value
    assert handoff is not None
    assert handoff["started_from_queue_id"] == queued["queue_id"]
    assert handoff["status"] == RunnerStatus.READY.value
    assert coordinator.stopped_runner_ids == ["busy-runner"]
    assert len(coordinator.started_specs) == 1
    assert service.runner_supervisor.get_queued_runner_start(queued["queue_id"]) is None


@pytest.mark.anyio
async def test_queued_pending_switch_starts_automatically_after_runner_finishes(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
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
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="busy-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("9" * 64),
            status=RunnerStatus.RUNNING,
            runner_process_compatibility_key="sha256:" + ("9" * 64),
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-1",
            pid=9200,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")
    queued = await service.start_workflow_runner("runner_workflow")

    service.runner_supervisor.mark_runner_job_finished("busy-runner", "job-1")
    await asyncio.sleep(0.05)

    assert coordinator is not None
    assert queued["status"] == RunnerStatus.QUEUED_PENDING_SWITCH.value
    assert coordinator.stopped_runner_ids == ["busy-runner"]
    assert len(coordinator.started_specs) == 1
    assert service.runner_supervisor.get_queued_runner_start(queued["queue_id"]) is None


@pytest.mark.anyio
async def test_start_workflow_runner_evicts_idle_incompatible_runner_before_switch(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
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
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="idle-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("9" * 64),
            status=RunnerStatus.IDLE,
            runner_process_compatibility_key="sha256:" + ("9" * 64),
            memory_class=RunnerMemoryClass.GPU_HEAVY,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert coordinator is not None
    assert result["status"] == RunnerStatus.READY.value
    assert result["runner"]["runner_id"] != "idle-runner"
    assert coordinator.stopped_runner_ids == ["idle-runner"]
    assert len(coordinator.started_specs) == 1


@pytest.mark.anyio
async def test_start_workflow_runner_uses_memory_governor_to_evict_light_runner_under_pressure(
    tmp_path: Path,
) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
    capsule_payload["hardware_observations"] = {
        "observed_peak_vram_mb": 1200,
        "observed_peak_ram_mb": 800,
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
        memory_observer=StubMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    total_ram_mb=64_000,
                    free_ram_mb=50_000,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=8_000,
                    total_ram_mb=64_000,
                    free_ram_mb=50_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
    )
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="idle-light-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("8" * 64),
            status=RunnerStatus.IDLE_WARM,
            runner_process_compatibility_key="sha256:" + ("8" * 64),
            memory_class=RunnerMemoryClass.GPU_LIGHT,
            observed_idle_vram_mb=900,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert coordinator is not None
    assert result["status"] == RunnerStatus.READY.value
    assert result["memory_decision"]["action"] == "evict_then_start"
    assert coordinator.stopped_runner_ids == ["idle-light-runner"]
    assert len(coordinator.started_specs) == 1
    decision_events = [
        event
        for event in service.log_store.list_events().events
        if event.source == "memory_governor" and "action" in event.details
    ]
    assert decision_events[-1].details["action"] == "evict_then_start"
    assert decision_events[-1].details["reason_code"] == "memory_pressure_high"
    release_events = [
        event
        for event in service.log_store.list_events().events
        if event.source == "memory_governor" and event.message == "Memory release check completed"
    ]
    assert release_events[-1].details["status"] == "released"


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_when_memory_release_times_out_after_eviction(
    tmp_path: Path,
) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
    capsule_payload["hardware_observations"] = {
        "observed_peak_vram_mb": 1200,
        "observed_peak_ram_mb": 800,
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
        memory_observer=StubMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    total_ram_mb=64_000,
                    free_ram_mb=50_000,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=1_000,
                    total_ram_mb=64_000,
                    free_ram_mb=50_000,
                    memory_pressure=MemoryPressureLevel.MEDIUM,
                ),
            ]
        ),
    )
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="idle-light-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("8" * 64),
            status=RunnerStatus.IDLE_WARM,
            runner_process_compatibility_key="sha256:" + ("8" * 64),
            memory_class=RunnerMemoryClass.GPU_LIGHT,
            observed_idle_vram_mb=900,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert coordinator is not None
    assert result["status"] == RunnerStatus.MEMORY_CLEANUP_FAILED.value
    assert result["memory_release_check"]["status"] == "timeout"
    assert coordinator.stopped_runner_ids == ["idle-light-runner"]
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_uses_memory_governor_to_keep_light_runner_warm(
    tmp_path: Path,
) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
    capsule_payload["hardware_observations"] = {
        "observed_peak_vram_mb": 2000,
        "observed_peak_ram_mb": 900,
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
        memory_observer=StubMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=24_000,
                free_vram_mb=18_000,
                total_ram_mb=64_000,
                free_ram_mb=50_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="idle-light-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("8" * 64),
            status=RunnerStatus.IDLE_WARM,
            runner_process_compatibility_key="sha256:" + ("8" * 64),
            memory_class=RunnerMemoryClass.GPU_LIGHT,
            memory_estimate_confidence=RunnerMemoryEstimateConfidence.MEDIUM,
            memory_estimate_source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            observed_idle_vram_mb=900,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert coordinator is not None
    assert result["status"] == RunnerStatus.READY.value
    assert result["memory_decision"]["action"] == "start_co_resident"
    assert coordinator.stopped_runner_ids == []
    assert len(coordinator.started_specs) == 1
    memory_events = [
        event
        for event in service.log_store.list_events().events
        if event.source == "memory_governor"
    ]
    assert memory_events[-1].details["action"] == "start_co_resident"
    assert memory_events[-1].details["reason_code"] == "co_residence_margin_available"


@pytest.mark.anyio
async def test_start_workflow_runner_allows_uncertain_estimate_without_cleanup_candidates(
    tmp_path: Path,
) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
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
        memory_observer=StubMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=8_000,
                free_vram_mb=7_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == RunnerStatus.READY.value
    assert result["runner"] is not None
    assert result["memory_decision"]["action"] == "start_co_resident"
    assert result["memory_decision"]["reason_code"] == "gpu_estimate_uncertain_cautious_start"
    assert coordinator is not None
    assert len(coordinator.started_specs) == 1


@pytest.mark.anyio
async def test_queued_pending_memory_handoff_after_active_runner_finishes(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
    capsule_payload["hardware_observations"] = {
        "observed_peak_vram_mb": 1200,
        "observed_peak_ram_mb": 800,
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
        memory_observer=StubMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            )
        ),
    )
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="active-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("8" * 64),
            status=RunnerStatus.RUNNING,
            runner_process_compatibility_key="sha256:" + ("8" * 64),
            memory_class=RunnerMemoryClass.GPU_LIGHT,
            current_job_id="job-1",
            pid=9200,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")
    queued = await service.start_workflow_runner("runner_workflow")

    service.runner_supervisor.mark_runner_job_finished("active-runner", "job-1")
    service.memory_observer = StubMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=12_000,
            free_vram_mb=8_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )
    handoff = await service.handoff_next_queued_runner_start(released_runner_id="active-runner")

    assert coordinator is not None
    assert queued["status"] == RunnerStatus.QUEUED_PENDING_MEMORY.value
    assert queued["queue_id"] is not None
    assert coordinator.stopped_runner_ids == []
    assert handoff is not None
    assert handoff["started_from_queue_id"] == queued["queue_id"]
    assert handoff["status"] == RunnerStatus.READY.value
    assert coordinator.stopped_runner_ids == []
    assert len(coordinator.started_specs) == 1


@pytest.mark.anyio
async def test_cancel_queued_runner_start_prevents_handoff(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="2")
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
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="busy-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9200",
            ws_url="ws://127.0.0.1:9200/ws",
            fingerprint="sha256:" + ("9" * 64),
            status=RunnerStatus.RUNNING,
            runner_process_compatibility_key="sha256:" + ("9" * 64),
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-1",
            pid=9200,
        ),
        StubAdapter(),
    )
    await service.prepare_workflow("runner_workflow")
    queued = await service.start_workflow_runner("runner_workflow")

    canceled = service.cancel_queued_runner_start(queued["queue_id"])
    service.runner_supervisor.mark_runner_job_finished("busy-runner", "job-1")
    handoff = await service.handoff_next_queued_runner_start(released_runner_id="busy-runner")

    assert coordinator is not None
    assert canceled["status"] == "canceled"
    assert handoff is None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_custom_node_workflow_runner_allows_materialized_custom_nodes(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="c")
    capsule_payload["custom_nodes"] = [
        {
            "package_id": "custom-node-a",
            "source": "https://example.invalid/custom-node-a.git",
            "trust_level": "quarantined_community",
            "node_types": ["CustomNodeA"],
        }
    ]
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

    install_result = await service.prepare_workflow("runner_workflow")
    result = await service.start_workflow_runner("runner_workflow")

    assert install_result["status"] == InstallStatus.READY.value
    assert result["status"] == RunnerStatus.READY.value
    assert coordinator is not None
    assert "--disable-auto-launch" in coordinator.started_specs[0].extra_args
    assert "--preview-method" in coordinator.started_specs[0].extra_args
    assert "--preview-size" in coordinator.started_specs[0].extra_args
    assert "--disable-all-custom-nodes" not in coordinator.started_specs[0].extra_args


@pytest.mark.anyio
async def test_run_does_not_reuse_bound_runner_from_stale_runtime_fingerprint(
    tmp_path: Path,
) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="c")
    capsule_payload["custom_nodes"] = [
        {
            "package_id": "custom-node-a",
            "source": "https://example.invalid/custom-node-a.git",
            "trust_level": "quarantined_community",
            "node_types": ["CustomNodeA"],
        }
    ]
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
    service.runner_supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="stale-runner",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9191",
            fingerprint="stale",
            status=RunnerStatus.READY,
            runner_process_compatibility_key="stale",
        ),
        StubAdapter(),
    )
    service.runner_supervisor.bind_workflow_runner("runner_workflow", "stale-runner")

    unavailable = await service._ensure_workflow_runner_for_run(
        service.workflow_loader.get_package("runner_workflow")
    )

    assert unavailable is None
    assert coordinator is not None
    assert len(coordinator.started_specs) == 1
    assert (
        service.runner_supervisor.runner_for_workflow("runner_workflow").runner_id
        != "stale-runner"
    )


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
async def test_start_workflow_runner_blocks_active_runner_python_abi_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload(runner_char="8")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    monkeypatch.setattr(
        "app.runtime.runners.lifecycle_service.detect_python_major_minor",
        lambda python_executable: "3.14",
    )
    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        runner_coordinator_factory=coordinator_factory,
    )
    await service.prepare_workflow("runner_workflow")

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "Managed runner Python ABI mismatch" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_stale_dependency_manifest_python_abi(
    tmp_path: Path,
) -> None:
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
    payload = await service.prepare_workflow("runner_workflow")
    manifest_path = Path(payload["dependency_env_path"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["python_version"] = "3.14"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "dependency environment Python ABI mismatch" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_missing_model_view_file(tmp_path: Path) -> None:
    payload = b"runner-model"
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload_with_model(payload, runner_char="a")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(payload)
        return len(payload)

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        downloader=downloader,
        runner_coordinator_factory=coordinator_factory,
    )
    await service.prepare_workflow("runner_workflow")
    state = service.capsule_installer.install_state_store.get("runner_workflow-fp")
    assert state is not None
    materialized_path = Path(state.model_references[0].materialized_path or "")
    materialized_path.unlink()

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "model view file missing for runner-model" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_missing_model_blob(tmp_path: Path) -> None:
    payload = b"runner-model"
    packages_dir = tmp_path / "packages"
    capsule_payload = _runner_capsule_payload_with_model(payload, runner_char="b")
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    coordinator = None

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(payload)
        return len(payload)

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        downloader=downloader,
        runner_coordinator_factory=coordinator_factory,
    )
    await service.prepare_workflow("runner_workflow")
    state = service.capsule_installer.install_state_store.get("runner_workflow-fp")
    assert state is not None
    blob_path = Path(state.model_references[0].blob_path or "")
    blob_path.unlink()

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "model blob missing for runner-model" in result["error"]
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
async def test_shutdown_still_stops_sidecar_when_runner_cleanup_fails(
    tmp_path: Path,
) -> None:
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> FailingStopAllRunnerCoordinator:
        nonlocal coordinator
        coordinator = FailingStopAllRunnerCoordinator(supervisor)
        return coordinator

    sidecar = RecordingSidecarService()
    service = _build_service(
        tmp_path,
        runner_coordinator_factory=coordinator_factory,
        comfyui_sidecar_service=sidecar,
    )

    await service.shutdown()

    assert coordinator is not None
    assert coordinator.stop_all_called
    assert sidecar.shutdown_called
    assert any(
        event.message == "Backend shutdown step failed"
        and event.details.get("step") == "runner_process_coordinator"
        for event in service.log_store.list_events().events
    )


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
async def test_prepare_workflow_with_unresolved_runtime_input_does_not_mark_ready(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    workflow_dir = packages_dir / "runner_workflow"
    workflow_dir.mkdir(parents=True)
    package = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    package["metadata"]["id"] = "runner_workflow"
    package["metadata"]["name"] = "runner_workflow"
    package["required_models"] = []
    package["unresolved_runtime_inputs"] = [
        {
            "node_id": "10",
            "node_type": "LoadImage",
            "input_name": "image",
            "reason": "creator_local_image_not_bundled",
        }
    ]
    (workflow_dir / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (workflow_dir / CAPSULE_LOCK_FILENAME).write_text(
        json.dumps(_runner_capsule_payload(runner_char="0")),
        encoding="utf-8",
    )
    service = _build_service(tmp_path, packages_dir=packages_dir)

    result = await service.prepare_workflow("runner_workflow")

    assert result["status"] == InstallStatus.PREPARED_NEEDS_INPUT_SETUP.value
    assert result["smoke_test_status"] == SmokeTestStatus.NOT_RUN.value
    assert result["smoke_test_report"]["workflow_execution"]["status"] == SmokeStageStatus.BLOCKED.value
    assert "unresolved runtime inputs" in result["smoke_test_report"]["workflow_execution"]["message"]


@pytest.mark.anyio
async def test_prepare_workflow_blocks_filename_only_model_requirement(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    workflow_dir = packages_dir / "runner_workflow"
    workflow_dir.mkdir(parents=True)
    package = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    package["metadata"]["id"] = "runner_workflow"
    package["metadata"]["name"] = "runner_workflow"
    package["required_models"] = [
        {
            "folder": "checkpoints",
            "filename": "creator-local-model.safetensors",
            "verification_level": "filename_only",
        }
    ]
    (workflow_dir / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (workflow_dir / CAPSULE_LOCK_FILENAME).write_text(
        json.dumps(_runner_capsule_payload(runner_char="c")),
        encoding="utf-8",
    )
    service = _build_service(tmp_path, packages_dir=packages_dir)

    result = await service.prepare_workflow("runner_workflow")

    assert result["status"] == InstallStatus.CANNOT_PREPARE_AUTOMATICALLY.value
    assert result["user_facing_message"] == "Cannot prepare automatically"
    assert "filename-only model matches are not trusted" in result["last_error"]
    state = service.capsule_installer.install_state_store.get("runner_workflow-fp")
    assert state is not None
    assert state.model_references == []


@pytest.mark.anyio
async def test_prepare_workflow_reuses_filename_size_local_model_candidate(tmp_path: Path) -> None:
    payload = b"local-model"
    local_root = tmp_path / "user-models"
    local_path = local_root / "checkpoints" / "creator-local-model.safetensors"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(payload)
    packages_dir = tmp_path / "packages"
    workflow_dir = packages_dir / "runner_workflow"
    workflow_dir.mkdir(parents=True)
    package = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    package["metadata"]["id"] = "runner_workflow"
    package["metadata"]["name"] = "runner_workflow"
    package["required_models"] = [
        {
            "folder": "checkpoints",
            "filename": "creator-local-model.safetensors",
            "size_bytes": len(payload),
            "verification_level": "filename_size",
        }
    ]
    (workflow_dir / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (workflow_dir / CAPSULE_LOCK_FILENAME).write_text(
        json.dumps(_runner_capsule_payload(runner_char="d")),
        encoding="utf-8",
    )
    service = _build_service(tmp_path, packages_dir=packages_dir, local_model_roots=[local_root])

    result = await service.prepare_workflow("runner_workflow")

    assert result["status"] == InstallStatus.READY.value
    state = service.capsule_installer.install_state_store.get("runner_workflow-fp")
    assert state is not None
    ref = state.model_references[0]
    assert ref.verification_level is ModelVerificationLevel.FILENAME_SIZE
    assert ref.asset_ownership is AssetOwnership.USER_LOCAL
    assert ref.source_path == str(local_path)
    assert ref.blob_path is None
    assert ref.sha256 == f"sha256:{hashlib.sha256(payload).hexdigest()}"
    assert Path(ref.materialized_path or "").read_bytes() == payload


@pytest.mark.anyio
async def test_prepare_workflow_reuses_hash_verified_local_model_without_source_url(
    tmp_path: Path,
) -> None:
    payload = b"hash-verified-local-model"
    local_root = tmp_path / "user-models"
    local_path = local_root / "text_encoders" / "gemma.safetensors"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(payload)
    capsule_payload = _runner_capsule_payload(runner_char="b")
    capsule_payload["models"] = [
        {
            "id": "text_encoders/gemma.safetensors",
            "sha256": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload),
            "source_urls": [],
            "comfyui_folder": "text_encoders",
            "filename": "gemma.safetensors",
        }
    ]
    packages_dir = tmp_path / "packages"
    _write_workflow_with_capsule(packages_dir, "runner_workflow", capsule_payload)
    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        local_model_roots=[local_root],
    )

    result = await service.prepare_workflow("runner_workflow")

    assert result["status"] == InstallStatus.READY.value
    state = service.capsule_installer.install_state_store.get("runner_workflow-fp")
    assert state is not None
    ref = state.model_references[0]
    assert ref.verification_level is ModelVerificationLevel.SHA256_SIZE
    assert ref.asset_ownership is AssetOwnership.USER_LOCAL
    assert ref.source_path == str(local_path)
    assert ref.blob_path is None
    assert Path(ref.materialized_path or "").read_bytes() == payload


@pytest.mark.anyio
async def test_prepare_workflow_reports_missing_filename_size_local_candidate(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    workflow_dir = packages_dir / "runner_workflow"
    workflow_dir.mkdir(parents=True)
    package = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    package["metadata"]["id"] = "runner_workflow"
    package["metadata"]["name"] = "runner_workflow"
    package["required_models"] = [
        {
            "folder": "checkpoints",
            "filename": "creator-local-model.safetensors",
            "size_bytes": 123,
            "verification_level": "filename_size",
        }
    ]
    (workflow_dir / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (workflow_dir / CAPSULE_LOCK_FILENAME).write_text(
        json.dumps(_runner_capsule_payload(runner_char="f")),
        encoding="utf-8",
    )
    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        local_model_roots=[tmp_path / "user-models"],
    )

    result = await service.prepare_workflow("runner_workflow")

    assert result["status"] == InstallStatus.CANNOT_PREPARE_AUTOMATICALLY.value
    assert "No local model candidate found" in result["last_error"]


@pytest.mark.anyio
async def test_start_workflow_runner_blocks_missing_user_local_model_source(tmp_path: Path) -> None:
    payload = b"local-model"
    local_root = tmp_path / "user-models"
    local_path = local_root / "checkpoints" / "creator-local-model.safetensors"
    local_path.parent.mkdir(parents=True)
    local_path.write_bytes(payload)
    packages_dir = tmp_path / "packages"
    workflow_dir = packages_dir / "runner_workflow"
    workflow_dir.mkdir(parents=True)
    package = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    package["metadata"]["id"] = "runner_workflow"
    package["metadata"]["name"] = "runner_workflow"
    package["required_models"] = [
        {
            "folder": "checkpoints",
            "filename": "creator-local-model.safetensors",
            "size_bytes": len(payload),
            "verification_level": "filename_size",
        }
    ]
    (workflow_dir / "package.json").write_text(json.dumps(package), encoding="utf-8")
    (workflow_dir / CAPSULE_LOCK_FILENAME).write_text(
        json.dumps(_runner_capsule_payload(runner_char="e")),
        encoding="utf-8",
    )
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingRunnerCoordinator:
        nonlocal coordinator
        coordinator = RecordingRunnerCoordinator(supervisor)
        return coordinator

    service = _build_service(
        tmp_path,
        packages_dir=packages_dir,
        local_model_roots=[local_root],
        runner_coordinator_factory=coordinator_factory,
    )
    await service.prepare_workflow("runner_workflow")
    local_path.unlink()

    result = await service.start_workflow_runner("runner_workflow")

    assert result["status"] == "failed"
    assert "local model source missing for checkpoints/creator-local-model.safetensors" in result["error"]
    assert coordinator is not None
    assert coordinator.started_specs == []


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
async def test_prepare_user_capsule_uses_preparable_capsule_path(tmp_path: Path) -> None:
    user_packages_dir = tmp_path / "user_packages"
    workflow_dir = user_packages_dir / "user_capsule_workflow"
    workflow_dir.mkdir(parents=True)

    bundled_pkg = json.loads((_bundled_packages_dir() / "text_to_image_v0" / "package.json").read_text())
    bundled_pkg["metadata"]["id"] = "user_capsule_workflow"
    bundled_pkg["metadata"]["name"] = "User Capsule Workflow"
    bundled_pkg["required_models"] = []
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

    assert result["status"] == InstallStatus.READY.value
    assert result["capsule_fingerprint"] == "user_capsule_workflow-fp"

    shutil.rmtree(user_packages_dir, ignore_errors=True)
