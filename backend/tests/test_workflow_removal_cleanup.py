from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.paths import NoofyPaths, resolve_paths
from app.diagnostics import LogStore
from app.engine import service as engine_service_module
from app.engine.models import RequiredModelSummary
from app.engine.service import EngineService
from app.main import create_app
from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.runtime.dependencies.isolation import (
    InstallState,
    InstalledModelReference,
    InstallStatus,
    SmokeTestStatus,
)
from app.runtime.install_state import InstallStateStore
from app.runtime.runners.supervisor import RunnerDescriptor, RunnerKind, RunnerStatus, RunnerSupervisor
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.library import WorkflowLibraryStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.store_paths import imported_workflow_id
from app.workflows.validator import WorkflowPackageValidator


class _AvailabilityService:
    def cleanup_interrupted_downloads(self) -> int:
        return 0

    def summarize(self, package) -> RequiredModelSummary:
        return RequiredModelSummary(
            workflow_id=package.metadata.id,
            total_count=0,
            available_count=0,
            possible_match_count=0,
            missing_count=0,
            needs_manual_download_count=0,
            ready_to_run=True,
            models=[],
        )


class _Adapter:
    async def list_available_models(self):
        return []


class _RuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class _CapsuleInstaller:
    def __init__(self, install_state_store: InstallStateStore) -> None:
        self.install_state_store = install_state_store


class _FailingRemoveInstallStateStore:
    def __init__(self, delegate: InstallStateStore) -> None:
        self._delegate = delegate

    def remove(self, capsule_fingerprint: str) -> bool:
        raise OSError("read-only install state")

    def list_states(self) -> list[InstallState]:
        return self._delegate.list_states()


def test_remove_workflow_keeps_shared_runtime_artifacts_referenced_by_another_workflow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(engine_service_module, "settings", _settings(paths))
    dep_fp = _fp("d")
    runner_fp = _fp("r")
    model_view_fp = _fp("v")
    capsule_a = "capsule-a"
    capsule_b = "capsule-b"
    workflow_a = _write_imported_workflow(paths, "publisher", "shared-a", "0.1.0", capsule_a, dep_fp, runner_fp)
    _write_imported_workflow(paths, "publisher", "shared-b", "0.1.0", capsule_b, dep_fp, runner_fp)
    dep_path = _write_dependency_env(paths, dep_fp)
    runner_path = _write_runner_workspace(paths, runner_fp)
    blob = _write_model_blob(paths, "a" * 64, b"shared")
    view = _write_model_view(paths, model_view_fp)
    _old(dep_path)
    _old(runner_path)
    _old(view)
    state_store = InstallStateStore(paths.workflow_store_dir / "install-state")
    state_store.save(_state(capsule_a, dep_fp, runner_fp, blob, view))
    state_store.save(_state(capsule_b, dep_fp, runner_fp, blob, view))
    service = _service(paths, state_store)

    result = service.remove_workflow(workflow_a)

    assert result == {"workflow_id": workflow_a, "removed": True}
    assert state_store.get(capsule_a) is None
    assert state_store.get(capsule_b) is not None
    assert dep_path.exists()
    assert runner_path.exists()
    assert blob.exists()
    assert view.exists()


def test_remove_last_workflow_makes_custom_runtime_artifacts_collectable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(engine_service_module, "settings", _settings(paths))
    dep_fp = _fp("d")
    runner_fp = _fp("r")
    model_view_fp = _fp("v")
    capsule = "capsule-last"
    workflow_id = _write_imported_workflow(paths, "publisher", "last", "0.1.0", capsule, dep_fp, runner_fp)
    dep_path = _write_dependency_env(paths, dep_fp)
    runner_path = _write_runner_workspace(paths, runner_fp)
    blob = _write_model_blob(paths, "b" * 64, b"owned")
    view = _write_model_view(paths, model_view_fp)
    core_env = paths.core_envs_dir / "default-python"
    core_runtime = paths.runtime_dir / "comfyui-venv"
    core_env.mkdir(parents=True)
    core_runtime.mkdir(parents=True)
    for path in (dep_path, runner_path, view):
        _old(path)
    state_store = InstallStateStore(paths.workflow_store_dir / "install-state")
    state_store.save(_state(capsule, dep_fp, runner_fp, blob, view))
    service = _service(paths, state_store)

    service.remove_workflow(workflow_id)

    assert state_store.get(capsule) is None
    assert not dep_path.exists()
    assert not runner_path.exists()
    assert not blob.exists()
    assert not view.exists()
    assert core_env.exists()
    assert core_runtime.exists()


def test_remove_workflow_gc_keeps_active_runner_runtime_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(engine_service_module, "settings", _settings(paths))
    dep_fp = _fp("e")
    runner_fp = _fp("f")
    model_view_fp = _fp("9")
    capsule = "capsule-active-runner"
    workflow_id = _write_imported_workflow(paths, "publisher", "active", "0.1.0", capsule, dep_fp, runner_fp)
    dep_path = _write_dependency_env(paths, dep_fp)
    runner_path = _write_runner_workspace(paths, runner_fp)
    view = _write_model_view(paths, model_view_fp)
    for path in (dep_path, runner_path, view):
        _old(path)
    state_store = InstallStateStore(paths.workflow_store_dir / "install-state")
    state_store.save(_state(capsule, dep_fp, runner_fp))
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="runner-active",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9100",
            fingerprint=runner_fp,
            status=RunnerStatus.IDLE_WARM,
            dependency_env_fingerprint=dep_fp,
            runner_workspace_fingerprint=runner_fp,
            model_view_fingerprint=model_view_fp,
            open_workflow_lease_count=1,
            open_workflow_lease_ids=["lease-1"],
        ),
        _Adapter(),
    )
    service = _service(paths, state_store, supervisor=supervisor)

    service.remove_workflow(workflow_id)

    assert state_store.get(capsule) is None
    assert dep_path.exists()
    assert runner_path.exists()
    assert view.exists()


def test_user_local_models_are_not_deleted_when_workflow_install_state_is_removed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(engine_service_module, "settings", _settings(paths))
    source = tmp_path / "user-models" / "local.safetensors"
    source.parent.mkdir()
    source.write_bytes(b"user-owned")
    capsule = "capsule-user-local"
    workflow_id = _write_imported_workflow(paths, "publisher", "local-model", "0.1.0", capsule, _fp("1"), _fp("2"))
    state_store = InstallStateStore(paths.workflow_store_dir / "install-state")
    state_store.save(
        InstallState(
            schema_version="0.1.0",
            capsule_fingerprint=capsule,
            status=InstallStatus.READY,
            smoke_test_status=SmokeTestStatus.PASSED,
            model_references=[
                InstalledModelReference(
                    requirement_id="local",
                    comfyui_folder="checkpoints",
                    filename="local.safetensors",
                    verification_level=ModelVerificationLevel.FILENAME_SIZE,
                    asset_ownership=AssetOwnership.USER_LOCAL,
                    size_bytes=source.stat().st_size,
                    source_path=str(source),
                )
            ],
        )
    )
    service = _service(paths, state_store)

    service.remove_workflow(workflow_id)

    assert state_store.get(capsule) is None
    assert source.exists()


def test_remove_workflow_logs_and_continues_when_install_state_root_cannot_be_removed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setattr(engine_service_module, "settings", _settings(paths))
    dep_fp = _fp("7")
    runner_fp = _fp("8")
    capsule = "capsule-remove-error"
    package_id = "remove-state-error"
    workflow_id = _write_imported_workflow(paths, "publisher", package_id, "0.1.0", capsule, dep_fp, runner_fp)
    package_dir = paths.workflow_packages_store_dir / "publisher" / package_id / "0.1.0"
    dep_path = _write_dependency_env(paths, dep_fp)
    _old(dep_path)
    state_store = InstallStateStore(paths.workflow_store_dir / "install-state")
    state_store.save(_state(capsule, dep_fp, runner_fp))
    service = _service(paths, state_store)
    assert service.capsule_installer is not None
    service.capsule_installer.install_state_store = _FailingRemoveInstallStateStore(state_store)  # type: ignore[assignment]

    result = service.remove_workflow(workflow_id)

    assert result == {"workflow_id": workflow_id, "removed": True}
    assert not package_dir.exists()
    assert state_store.get(capsule) is not None
    assert dep_path.exists()
    events = service.log_store.list_events().events
    assert any(
        event.level == "warning"
        and event.message == "Workflow install-state GC root could not be removed"
        and event.workflow_id == workflow_id
        and event.details.get("capsule_fingerprint") == capsule
        for event in events
    )


def test_delete_workflow_route_uses_engine_service_cleanup_facade(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    class FakeEngineService:
        gallery_capture_service = None
        removed_workflows: list[str]

        def __init__(self) -> None:
            self.removed_workflows = []

        def remove_workflow(self, workflow_id: str):
            self.removed_workflows.append(workflow_id)
            return {"workflow_id": workflow_id, "removed": True}

        async def shutdown(self):
            return None

    service = FakeEngineService()
    with TestClient(create_app(engine_service=service)) as client:
        response = client.delete("/api/workflows/imported-wf")

    assert response.status_code == 200
    assert response.json() == {"workflow_id": "imported-wf", "removed": True}
    assert service.removed_workflows == ["imported-wf"]


def _paths(tmp_path: Path) -> NoofyPaths:
    paths = resolve_paths(
        env={
            "NOOFY_DATA_DIR": str(tmp_path / "data"),
            "NOOFY_MODELS_DIR": str(tmp_path / "models"),
            "NOOFY_BUNDLED_WORKFLOWS_DIR": str(tmp_path / "bundled"),
            "COMFYUI_REPO_DIR": str(tmp_path / "comfyui"),
        }
    )
    paths.ensure_directories()
    paths.bundled_workflows_dir.mkdir(parents=True, exist_ok=True)
    return paths


def _settings(paths: NoofyPaths) -> SimpleNamespace:
    return SimpleNamespace(paths=paths, comfyui_models_dir=paths.models_dir)


def _service(
    paths: NoofyPaths,
    state_store: InstallStateStore,
    *,
    supervisor: RunnerSupervisor | None = None,
) -> EngineService:
    log_store = LogStore()
    imported_store = ImportedWorkflowPackageStore(paths.workflow_packages_store_dir, log_store=log_store)
    loader = WorkflowPackageLoader(
        paths.bundled_workflows_dir,
        imported_packages_dir=paths.workflow_packages_store_dir,
    )
    return EngineService(
        loader,
        WorkflowPackageValidator(),
        supervisor or RunnerSupervisor(),
        _RuntimeManager(),
        log_store,
        capsule_loader=CapsuleLockLoader(
            paths.bundled_workflows_dir,
            imported_packages_dir=paths.workflow_packages_store_dir,
        ),
        capsule_installer=_CapsuleInstaller(state_store),
        imported_package_store=imported_store,
        model_availability_service=_AvailabilityService(),
        workflow_library_store=WorkflowLibraryStore(paths.workflow_store_dir / "library"),
    )


def _write_imported_workflow(
    paths: NoofyPaths,
    publisher_id: str,
    package_id: str,
    version: str,
    capsule_fingerprint: str,
    dependency_env_fingerprint: str,
    runner_workspace_fingerprint: str,
) -> str:
    workflow_id = imported_workflow_id(publisher_id, package_id, version)
    package_dir = paths.workflow_packages_store_dir / publisher_id / package_id / version
    package_dir.mkdir(parents=True)
    package_payload = {
        "metadata": {
            "id": workflow_id,
            "name": package_id,
            "version": version,
        },
        "display_name": package_id,
        "identity": {
            "publisher_id": publisher_id,
            "package_id": package_id,
            "version": version,
            "trust_level": "quarantined_community",
            "source": "test",
        },
        "engine": "comfyui",
        "required_models": [],
        "comfyui_graph": {},
        "dashboard": {"version": "0.1.0", "status": "configured", "sections": []},
        "import_metadata": {"original_filename": f"{package_id}.noofy"},
    }
    capsule_payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": publisher_id,
            "package_id": package_id,
            "version": version,
            "trust_level": "quarantined_community",
            "source": "test",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "v-test",
            "core_source_hash": _fp("c"),
        },
        "runtime": {
            "runtime_profile_id": "community",
            "runtime_profile_variant_id": "cuda",
            "runtime_profile_manifest_hash": _fp("m"),
            "runtime_profile_catalog_version": "test",
            "fingerprint_schema_version": "test",
            "dependency_env_fingerprint": dependency_env_fingerprint,
            "runner_fingerprint": runner_workspace_fingerprint,
            "runner_process_compatibility_key": runner_workspace_fingerprint,
            "capsule_fingerprint": capsule_fingerprint,
            "os": "linux",
            "architecture": "x86_64",
            "python_version": "3.13",
            "python_build_id": "cpython-3.13",
            "gpu_backend": "cuda",
            "dependency_lock_hash": _fp("l"),
            "runner_workspace_hash": _fp("w"),
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "dependency-lock.json", "install_policy": "test"},
        "models": [],
        "trust": {"level": "quarantined_community"},
    }
    (package_dir / "package.json").write_text(json.dumps(package_payload), encoding="utf-8")
    (package_dir / "capsule.lock.json").write_text(json.dumps(capsule_payload), encoding="utf-8")
    return workflow_id


def _state(
    capsule_fingerprint: str,
    dependency_env_fingerprint: str,
    runner_workspace_fingerprint: str,
    blob_path: Path | None = None,
    model_view_path: Path | None = None,
) -> InstallState:
    refs = []
    if blob_path is not None and model_view_path is not None:
        refs.append(
            InstalledModelReference(
                requirement_id="model",
                comfyui_folder="checkpoints",
                filename="model.safetensors",
                verification_level=ModelVerificationLevel.SHA256_SIZE,
                asset_ownership=AssetOwnership.NOOFY_DOWNLOADED,
                model_id="model",
                sha256="sha256:" + ("a" * 64),
                size_bytes=max(blob_path.stat().st_size, 1),
                blob_path=str(blob_path),
                materialized_path=str(model_view_path / "checkpoints" / "model.safetensors"),
            )
        )
    return InstallState(
        schema_version="0.1.0",
        capsule_fingerprint=capsule_fingerprint,
        status=InstallStatus.READY,
        smoke_test_status=SmokeTestStatus.PASSED,
        dependency_env_fingerprint=dependency_env_fingerprint,
        runner_workspace_fingerprint=runner_workspace_fingerprint,
        model_references=refs,
    )


def _write_dependency_env(paths: NoofyPaths, fingerprint: str) -> Path:
    path = paths.dependency_envs_dir / f"dep-env-{fingerprint.removeprefix('sha256:')}"
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(
        json.dumps({"fingerprint": fingerprint, "status": "ready"}),
        encoding="utf-8",
    )
    return path


def _write_runner_workspace(paths: NoofyPaths, fingerprint: str) -> Path:
    path = paths.runner_workspaces_dir / f"runner-workspace-{fingerprint.removeprefix('sha256:')}"
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(
        json.dumps({"fingerprint": fingerprint, "status": "ready"}),
        encoding="utf-8",
    )
    return path


def _write_model_blob(paths: NoofyPaths, sha: str, content: bytes) -> Path:
    path = paths.model_blobs_dir / sha / "blob"
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    return path


def _write_model_view(paths: NoofyPaths, fingerprint: str) -> Path:
    path = paths.model_materialized_dir / "views" / f"model-view-{fingerprint.removeprefix('sha256:')}"
    (path / "checkpoints").mkdir(parents=True)
    (path / "checkpoints" / "model.safetensors").write_bytes(b"view")
    return path


def _old(path: Path, *, days: int = 30) -> None:
    timestamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    if path.is_dir():
        for child in path.rglob("*"):
            os.utime(child, (timestamp, timestamp), follow_symlinks=False)


def _fp(char: str) -> str:
    return "sha256:" + (char * 64)
