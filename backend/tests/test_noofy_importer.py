from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.engine.service import EngineService
from app.runtime.supervisor import CORE_RUNNER_FINGERPRINT, CORE_RUNNER_ID, RunnerDescriptor, RunnerKind, RunnerSupervisor
from app.workflows.importer import (
    ImportedWorkflowPackageStore,
    NoofyArchiveImporter,
    NoofyImportError,
    _normalize_models,
)
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


def _archive_bytes() -> bytes:
    root = Path(__file__).resolve().parents[2]
    return (root / "exported-workflow-for-testing.noofy").read_bytes()


def test_noofy_importer_normalizes_real_export_without_importing_custom_nodes() -> None:
    custom_nodes_was_loaded = "custom_nodes" in sys.modules

    package = NoofyArchiveImporter(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    ).normalize()

    assert package.metadata.id == "unknown__eraserv4.5__0.1.0"
    assert package.metadata.name == "EraserV4.5"
    assert package.identity is not None
    assert package.identity.publisher_id == "unknown"
    assert package.identity.package_id == "eraserv4.5"
    assert package.identity.trust_level == "quarantined_community"
    assert len(package.custom_nodes) == 5
    assert {model.folder for model in package.required_models} == {"checkpoints", "controlnet"}
    assert {
        model.filename for model in package.required_models
    } == {
        "DreamShaperXL_Lightning.safetensors",
        "diffusion_pytorch_model_promax.safetensors",
    }
    assert {model.verification_level for model in package.required_models} == {"sha256_size"}
    assert all(model.identity_verified_by_exporter for model in package.required_models)
    assert all(model.bundled is False for model in package.required_models)
    assert {model.asset_ownership for model in package.required_models} == {"external_reference"}
    assert package.unresolved_runtime_inputs
    assert package.unresolved_runtime_inputs[0].node_type == "LoadImage"
    assert package.unresolved_runtime_inputs[0].reason == "creator_local_image_not_bundled"
    assert package.dashboard.sections[0].title == "Input setup needed"
    assert package.assets.thumbnail == "source-files/assets/thumbnail.png"
    assert package.observed_hardware["observed_peak_ram_mb"] == 5567
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.import_metadata.user_facing_message == "Needs input setup"
    assert package.import_metadata.source_archive_sha256.startswith("sha256:")
    assert ("custom_nodes" in sys.modules) is custom_nodes_was_loaded


def test_import_store_persists_normalized_package_and_original_source_files(tmp_path: Path) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    package = store.import_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )

    package_dir = tmp_path / "packages" / "unknown" / "eraserv4.5" / "0.1.0"
    assert (package_dir / "package.json").exists()
    assert (package_dir / "capsule.lock.json").exists()
    assert (package_dir / "source-archive.noofy").exists()
    assert (package_dir / "source-files" / "package.json").exists()
    assert (package_dir / "source-files" / "custom_nodes" / "comfyui-kjnodes" / "requirements.txt").exists()

    loader = WorkflowPackageLoader(Path("missing-bundled"), imported_packages_dir=tmp_path / "packages")
    loaded = loader.get_package(package.metadata.id)
    assert loaded.metadata.name == "EraserV4.5"
    assert loaded.custom_nodes[0].included is True
    assert loaded.required_models[0].checksum is not None
    assert loaded.import_metadata is not None
    assert loaded.import_metadata.status == "needs_input_setup"
    assert log_store.list_events().events[-1].message == "Imported workflow package"


def test_importer_normalizes_untrusted_model_identity_and_ownership_values() -> None:
    models = _normalize_models(
        {
            "models": [
                {
                    "comfyui_folder": "checkpoints",
                    "filename": "model.safetensors",
                    "size_bytes": 123,
                    "verification_level": "creator_claimed_verified",
                    "asset_ownership": "delete_anyway",
                }
            ]
        }
    )

    assert models[0].verification_level == "filename_size"
    assert models[0].asset_ownership == "external_reference"


def test_imported_package_cannot_shadow_bundled_workflow_by_id(tmp_path: Path) -> None:
    bundled_dir = tmp_path / "bundled" / "text_to_image_v0"
    imported_dir = tmp_path / "imported" / "unknown" / "text_to_image_v0" / "0.1.0"
    bundled_dir.mkdir(parents=True)
    imported_dir.mkdir(parents=True)

    package = NoofyArchiveImporter(_archive_bytes()).normalize()
    bundled_package = package.model_copy(
        update={
            "metadata": package.metadata.model_copy(
                update={"id": "text_to_image_v0", "name": "Bundled"}
            )
        }
    )
    imported_package = package.model_copy(
        update={
            "metadata": package.metadata.model_copy(
                update={"id": "text_to_image_v0", "name": "Imported Shadow"}
            )
        }
    )
    (bundled_dir / "package.json").write_text(bundled_package.model_dump_json(), encoding="utf-8")
    (imported_dir / "package.json").write_text(imported_package.model_dump_json(), encoding="utf-8")

    loaded = WorkflowPackageLoader(
        tmp_path / "bundled",
        imported_packages_dir=tmp_path / "imported",
    ).get_package("text_to_image_v0")

    assert loaded.metadata.name == "Bundled"


def test_noofy_importer_rejects_zip_path_traversal() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.txt", "bad")

    with pytest.raises(NoofyImportError, match="unsafe path"):
        NoofyArchiveImporter(payload.getvalue()).normalize()


def test_import_store_logs_failed_import_without_persisting_package(tmp_path: Path) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    with pytest.raises(NoofyImportError, match="unsafe path"):
        store.import_archive(_unsafe_zip_bytes(), original_filename="bad.noofy")

    assert not (tmp_path / "packages" / "_transactions").exists()
    latest = log_store.list_events().events[-1]
    assert latest.message == "Workflow import failed"
    assert latest.level == "warning"
    assert latest.details["original_filename"] == "bad.noofy"


def test_engine_service_imports_real_archive_and_exposes_normalized_package(tmp_path: Path) -> None:
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
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(
            Path("missing-bundled"),
            imported_packages_dir=tmp_path / "packages",
        ),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=log_store,
        imported_package_store=ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store),
    )

    result = service.import_workflow_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )
    package = service.get_workflow_package(result["workflow_id"])
    summaries = service.list_workflows()

    assert result["status"] == "needs_input_setup"
    assert package["metadata"]["name"] == "EraserV4.5"
    assert package["unresolved_runtime_inputs"][0]["reason"] == "creator_local_image_not_bundled"
    assert summaries[0]["status"] == "needs_input_setup"
    assert summaries[0]["status_label"] == "Needs input setup"
    assert summaries[0]["unresolved_input_count"] == 1


def _unsafe_zip_bytes() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.txt", "bad")
    return payload.getvalue()
