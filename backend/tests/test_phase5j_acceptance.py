from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.custom_nodes import (
    CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME,
    CustomNodeWorkspaceMaterializer,
)
from app.runtime.install_state import InstallStateStore
from app.runtime.isolation import (
    CapsuleLock,
    InstallStatus,
    ModelLock,
    SmokeStageResult,
    SmokeStageStatus,
    SmokeTestReport,
    SmokeTestStatus,
)
from app.runtime.profiles import load_runtime_profile_catalog
from app.runtime.model_store import ModelStore
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)
from app.workflows.importer import (
    ImportedWorkflowPackageStore,
    imported_package_capsule_lock,
)

TEST_WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "test_workflows"
BACKEND_DIR = Path(__file__).resolve().parents[1]


def _exported_archive_bytes() -> bytes:
    return (TEST_WORKFLOWS_DIR / "exported-workflow-for-testing.noofy").read_bytes()


def _fake_comfyui_source(root: Path) -> Path:
    source_dir = root / "trusted-comfyui"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (source_dir / "folder_paths.py").write_text(
        "# fake folder paths\n", encoding="utf-8"
    )
    (source_dir / "comfy").mkdir()
    (source_dir / "comfy" / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()
    (source_dir / "input").mkdir()
    return source_dir


def _derived_capsule_with_tiny_models(
    capsule: CapsuleLock, model_bytes: dict[str, bytes]
) -> CapsuleLock:
    models = []
    for original in capsule.models:
        payload = model_bytes[original.filename]
        models.append(
            ModelLock(
                id=original.id,
                sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
                size_bytes=len(payload),
                source_urls=[f"memory://{original.filename}"],
                comfyui_folder=original.comfyui_folder,
                filename=original.filename,
            )
        )
    data = capsule.model_dump(mode="json")
    data["models"] = [model.model_dump(mode="json") for model in models]
    data["runtime"]["capsule_fingerprint"] = "phase5j-derived-exported-capsule"
    return CapsuleLock.model_validate(data)


def _installer_for_exported_archive(
    tmp_path: Path,
    *,
    model_bytes: dict[str, bytes],
) -> tuple[CapsuleInstaller, CapsuleLock, Path, LogStore]:
    log_store = LogStore()
    package_store = ImportedWorkflowPackageStore(
        tmp_path / "workflow-store" / "packages", log_store=log_store
    )
    package = package_store.import_archive(
        _exported_archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
        allow_unverified_community_preparation=True,
    )
    package_dir = package_store.package_dir(package)
    capsule = _derived_capsule_with_tiny_models(
        imported_package_capsule_lock(package), model_bytes
    )

    async def downloader(url: str, dest: Path) -> int:
        filename = url.removeprefix("memory://")
        payload = model_bytes[filename]
        dest.write_bytes(payload)
        return len(payload)

    model_store = ModelStore(
        blobs_dir=tmp_path / "model-store" / "blobs" / "sha256",
        refs_dir=tmp_path / "model-store" / "refs",
        materialized_dir=tmp_path / "model-store" / "materialized",
        transactions_dir=tmp_path / "model-store" / "transactions",
        downloader=downloader,
        log_store=log_store,
    )
    workspace_preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(
            tmp_path / "runtime-store" / "dependency-envs"
        ),
        runner_workspace_store=RunnerWorkspaceManifestStore(
            tmp_path / "runtime-store" / "runner-workspaces"
        ),
        comfyui_source_dir=_fake_comfyui_source(tmp_path),
        runtime_profile_catalog=load_runtime_profile_catalog(
            BACKEND_DIR / "app/runtime/profile_catalog.json"
        ),
        custom_node_materializer=CustomNodeWorkspaceMaterializer(),
        custom_node_source_files_dir=package_dir / "source-files",
        dependency_transactions_dir=tmp_path / "runtime-store" / "transactions",
        log_store=log_store,
    )
    installer = CapsuleInstaller(
        install_state_store=InstallStateStore(
            tmp_path / "runtime-store" / "install-state"
        ),
        model_store=model_store,
        workspace_preparer=workspace_preparer,
        workspace_smoke_test=_passing_smoke_report,
        log_store=log_store,
    )
    return installer, capsule, package_dir, log_store


async def _passing_smoke_report(
    capsule_lock: CapsuleLock, prepared_workspace
) -> SmokeTestReport:
    assert (prepared_workspace.dependency_env_path / "manifest.json").exists()
    assert (prepared_workspace.runner_workspace_path / "manifest.json").exists()
    return SmokeTestReport(
        dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
        custom_node_import=SmokeStageResult(
            status=(
                SmokeStageStatus.PASSED
                if capsule_lock.custom_nodes
                else SmokeStageStatus.SKIPPED
            )
        ),
        runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
        workflow_execution=SmokeStageResult(status=SmokeStageStatus.PASSED),
    )


@pytest.mark.anyio
async def test_phase5j_exported_archive_prepares_isolated_artifacts_without_backend_custom_node_import(
    tmp_path: Path,
) -> None:
    custom_nodes_was_loaded = "custom_nodes" in sys.modules
    model_bytes = {
        "DreamShaperXL_Lightning.safetensors": b"tiny-checkpoint",
        "diffusion_pytorch_model_promax.safetensors": b"tiny-controlnet",
    }
    installer, capsule, package_dir, _ = _installer_for_exported_archive(
        tmp_path, model_bytes=model_bytes
    )
    trusted_core_node = tmp_path / "trusted-comfyui" / "custom_nodes" / "trusted.py"
    trusted_core_node.write_text("x = 1\n", encoding="utf-8")

    state = await installer.prepare(capsule, workflow_execution_smoke_allowed=False)

    assert state.status is InstallStatus.PREPARED_NEEDS_INPUT_SETUP
    assert state.smoke_test_status is SmokeTestStatus.NOT_RUN
    assert state.smoke_test_report.workflow_execution.status is SmokeStageStatus.BLOCKED
    assert "unresolved runtime inputs" in (
        state.smoke_test_report.workflow_execution.message or ""
    )
    assert len(state.model_references) == 2
    assert all(
        Path(reference.materialized_path or "").exists()
        for reference in state.model_references
    )

    runner_workspace = Path(state.runner_workspace_path or "")
    materialized_custom_nodes = {
        path.name for path in (runner_workspace / "custom_nodes").iterdir()
    }
    assert {
        "ComfyUI_JPS-Nodes",
        "comfyui-image-blender",
        "comfyui-inpaint-nodes",
        "comfyui-kjnodes",
        "comfyui_controlnet_aux",
    } <= materialized_custom_nodes
    manifest = json.loads(
        (runner_workspace / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    assert len(manifest["entries"]) == 5
    assert (
        package_dir
        / "source-files"
        / "custom_nodes"
        / "comfyui-kjnodes"
        / "requirements.txt"
    ).exists()
    assert trusted_core_node.exists()
    assert ("custom_nodes" in sys.modules) is custom_nodes_was_loaded


@pytest.mark.anyio
async def test_phase5j_exported_archive_reuses_ready_runtime_artifacts_after_controlled_smoke(
    tmp_path: Path,
) -> None:
    model_bytes = {
        "DreamShaperXL_Lightning.safetensors": b"ready-checkpoint",
        "diffusion_pytorch_model_promax.safetensors": b"ready-controlnet",
    }
    installer, capsule, _, _ = _installer_for_exported_archive(
        tmp_path, model_bytes=model_bytes
    )

    first = await installer.prepare(capsule, workflow_execution_smoke_allowed=True)
    second = await installer.prepare(capsule, workflow_execution_smoke_allowed=True)

    assert first.status is InstallStatus.READY
    assert first.smoke_test_status is SmokeTestStatus.PASSED
    assert second.status is InstallStatus.READY
    assert second.dependency_env_path == first.dependency_env_path
    assert second.runner_workspace_path == first.runner_workspace_path
    assert Path(second.runner_workspace_path or "").is_relative_to(
        tmp_path / "runtime-store" / "runner-workspaces"
    )
    assert not list((tmp_path / "runtime-store" / "transactions").glob("install-*"))


@pytest.mark.anyio
async def test_phase5j_model_view_collision_uses_separate_views_for_derived_records(
    tmp_path: Path,
) -> None:
    payloads = {
        "memory://workflow-a": b"workflow-a-model",
        "memory://workflow-b": b"workflow-b-model",
    }

    async def downloader(url: str, dest: Path) -> int:
        payload = payloads[url]
        dest.write_bytes(payload)
        return len(payload)

    store = ModelStore(
        blobs_dir=tmp_path / "model-store" / "blobs" / "sha256",
        refs_dir=tmp_path / "model-store" / "refs",
        materialized_dir=tmp_path / "model-store" / "materialized",
        transactions_dir=tmp_path / "model-store" / "transactions",
        downloader=downloader,
        log_store=LogStore(),
    )
    first = ModelLock(
        id="workflow-a/shared",
        sha256=f"sha256:{hashlib.sha256(payloads['memory://workflow-a']).hexdigest()}",
        size_bytes=len(payloads["memory://workflow-a"]),
        source_urls=["memory://workflow-a"],
        comfyui_folder="checkpoints",
        filename="shared.safetensors",
    )
    second = ModelLock(
        id="workflow-b/shared",
        sha256=f"sha256:{hashlib.sha256(payloads['memory://workflow-b']).hexdigest()}",
        size_bytes=len(payloads["memory://workflow-b"]),
        source_urls=["memory://workflow-b"],
        comfyui_folder="checkpoints",
        filename="shared.safetensors",
    )

    first_view = await store.materialize_model_view(
        view_id="workflow-a-capsule", model_locks=[first]
    )
    second_view = await store.materialize_model_view(
        view_id="workflow-b-capsule", model_locks=[second]
    )

    assert first_view.view_path != second_view.view_path
    assert (
        first_view.view_path / "checkpoints" / "shared.safetensors"
    ).read_bytes() == payloads["memory://workflow-a"]
    assert (
        second_view.view_path / "checkpoints" / "shared.safetensors"
    ).read_bytes() == payloads["memory://workflow-b"]
