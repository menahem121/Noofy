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
from app.runtime.isolation import InstallStatus
from app.runtime.model_store import ModelStore
from app.runtime.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerSupervisor,
)
from app.workflows.capsule import CAPSULE_LOCK_FILENAME, CapsuleLockLoader
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


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
    model_store = ModelStore(
        blobs_dir=tmp_path / "blobs",
        refs_dir=tmp_path / "refs",
        materialized_dir=tmp_path / "materialized",
        transactions_dir=tmp_path / "transactions",
        log_store=log_store,
        downloader=downloader or default_downloader,
    )
    capsule_installer = CapsuleInstaller(
        install_state_store=state_store,
        model_store=model_store,
        log_store=log_store,
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
            "dependency_env_fingerprint": "phase3-dep",
            "runner_fingerprint": "phase3-runner",
            "capsule_fingerprint": "bundled_empty_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
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
    # The state is persisted: a follow-up GET sees Ready too.
    assert service.get_install_state("bundled_empty_workflow")["status"] == InstallStatus.READY.value


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
            "dependency_env_fingerprint": "phase3-dep",
            "runner_fingerprint": "phase3-runner",
            "capsule_fingerprint": "bundled_model_workflow-fp",
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
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
                    "dependency_env_fingerprint": "phase3-dep",
                    "runner_fingerprint": "phase3-runner",
                    "capsule_fingerprint": "user_capsule_workflow-fp",
                    "os": "any",
                    "architecture": "any",
                    "python_version": "3.11",
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
