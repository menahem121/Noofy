from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.diagnostics import LogStore
from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.engine.models import EngineJob, JobResult, ModelInfo, RequiredModelAvailability, RequiredModelSummary
from app.engine.service import EngineService
from app.runtime.runners.supervisor import CORE_RUNNER_FINGERPRINT, CORE_RUNNER_ID, RunnerDescriptor, RunnerKind, RunnerStatus, RunnerSupervisor
from app.workflows.exporter import WorkflowExporter
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.library import WorkflowLibraryStore, WorkflowMetadataUpdate
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class FakeAvailabilityService:
    def cleanup_interrupted_downloads(self) -> int:
        return 0

    def summarize(self, package) -> RequiredModelSummary:
        models = [
            RequiredModelAvailability(
                requirement_id=f"{model.folder}/{model.filename}",
                filename=model.filename,
                model_type=model.model_type,
                folder=model.folder,
                verification_level=ModelVerificationLevel.FILENAME_ONLY,
                size_bytes=model.size_bytes,
                status="missing" if model.filename.startswith("missing") else "available",
                status_label="Missing" if model.filename.startswith("missing") else "Available",
                asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
            )
            for model in package.required_models
        ]
        missing_count = sum(model.status == "missing" for model in models)
        return RequiredModelSummary(
            workflow_id=package.metadata.id,
            total_count=len(package.required_models),
            available_count=len(package.required_models) - missing_count,
            possible_match_count=0,
            missing_count=missing_count,
            needs_manual_download_count=0,
            ready_to_run=missing_count == 0,
            models=models,
        )


class RecordingAdapter:
    async def list_available_models(self) -> list[ModelInfo]:
        return []

    async def run_workflow(self, workflow_package, graph, inputs, options) -> EngineJob:
        return EngineJob(
            job_id="job-1",
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )

    async def get_result(self, job_id: str) -> JobResult:
        return JobResult(job_id=job_id, status="completed")


def _archive_bytes() -> bytes:
    package: dict[str, Any] = {
        "schema_version": "0.5.0",
        "engine": "comfyui",
        "metadata": {"id": "library_wf", "name": "Library Workflow", "version": "1.0.0"},
        "display_name": "Library Workflow",
        "publisher_id": "test",
        "package_id": "library_wf",
        "version": "1.0.0",
        "description": "Original description",
        "required_models": [],
        "custom_nodes": [],
    }
    graph = {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hi"}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
    }
    capsule = {
        "schema_version": "0.5.0",
        "capsule_id": "library_wf",
        "source_policy": "local",
        "custom_nodes": [],
        "dependency_lock": {"packages": []},
        "graph_hash": "aaa",
        "dependency_env_hash": "bbb",
        "runner_workspace_hash": "ccc",
    }
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [],
        "outputs": [{"id": "image", "label": "Image", "node_id": "2", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Main",
                "controls": [
                    {"id": "result", "type": "result_image", "label": "Result", "output_id": "image"}
                ],
            }
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("package.json", json.dumps(package))
        zf.writestr("comfyui_graph.json", json.dumps(graph))
        zf.writestr("capsule.lock.json", json.dumps(capsule))
        zf.writestr("export-report.json", "{}")
        zf.writestr("dashboard.json", json.dumps(dashboard))
    return buf.getvalue()


def _service(tmp_path: Path) -> tuple[EngineService, str, Path, bytes]:
    archive = _archive_bytes()
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    package = store.import_archive(archive, original_filename="library.noofy")
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url="http://127.0.0.1:8188",
            ws_url="ws://127.0.0.1:8188/ws",
            fingerprint=CORE_RUNNER_FINGERPRINT,
            status=RunnerStatus.IDLE_WARM,
        ),
        RecordingAdapter(),
    )
    service = EngineService(
        loader,
        WorkflowPackageValidator(),
        supervisor,
        StubRuntimeManager(),
        log_store,
        imported_package_store=store,
        workflow_exporter=WorkflowExporter(tmp_path / "packages", loader),
        model_availability_service=FakeAvailabilityService(),
        workflow_library_store=WorkflowLibraryStore(tmp_path / "library"),
    )
    return service, package.metadata.id, store.package_dir(package), archive


def _native_run_service(tmp_path: Path) -> tuple[EngineService, str]:
    packages_dir = tmp_path / "native-packages"
    package_dir = packages_dir / "native_run"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "native_run",
                    "name": "Native Run",
                    "version": "1.0.0",
                    "description": "Runs locally",
                },
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {"1": {"class_type": "SaveImage", "inputs": {}}},
                "inputs": [],
                "outputs": [],
                "custom_nodes": [],
                "unresolved_runtime_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "dashboard.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "status": "configured",
                "inputs": [],
                "outputs": [{"id": "image", "label": "Image", "node_id": "1", "type": "image"}],
                "sections": [
                    {
                        "id": "main",
                        "title": "Main",
                        "controls": [
                            {"id": "result", "type": "result_image", "label": "Result", "output_id": "image"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loader = WorkflowPackageLoader(packages_dir)
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url="http://127.0.0.1:8188",
            ws_url="ws://127.0.0.1:8188/ws",
            fingerprint=CORE_RUNNER_FINGERPRINT,
            status=RunnerStatus.IDLE_WARM,
        ),
        RecordingAdapter(),
    )
    service = EngineService(
        loader,
        WorkflowPackageValidator(),
        supervisor,
        StubRuntimeManager(),
        LogStore(),
        model_availability_service=FakeAvailabilityService(),
        workflow_library_store=WorkflowLibraryStore(tmp_path / "library"),
    )
    return service, "native_run"


def test_workflow_list_returns_lightweight_table_fields(tmp_path: Path) -> None:
    service, workflow_id, _, _ = _service(tmp_path)

    row = next(item for item in service.list_workflows() if item["id"] == workflow_id)

    assert row["source_label"] == "Imported"
    assert row["main_model"]["name"] == "No model detected"
    assert row["category"] == "Txt2img"
    assert row["can_remove"] is True
    assert "models_used" not in row
    assert "overview" not in row


def test_workflow_list_includes_missing_model_count_without_details_payload(tmp_path: Path) -> None:
    packages_dir = tmp_path / "native-packages"
    package_dir = packages_dir / "missing_model_wf"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": "missing_model_wf", "name": "Missing Model", "version": "1.0.0"},
                "engine": "comfyui",
                "required_models": [
                    {"folder": "checkpoints", "filename": "missing.safetensors", "model_type": "checkpoint"}
                ],
                    "comfyui_graph": {"1": {"class_type": "SaveImage", "inputs": {}}},
                "inputs": [],
                "outputs": [],
                "custom_nodes": [],
                "unresolved_runtime_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "dashboard.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "status": "configured",
                "inputs": [],
                "outputs": [{"id": "image", "label": "Image", "node_id": "1", "type": "image"}],
                "sections": [
                    {
                        "id": "main",
                        "title": "Main",
                        "controls": [
                            {"id": "result", "type": "result_image", "label": "Result", "output_id": "image"}
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = EngineService(
        WorkflowPackageLoader(packages_dir),
        WorkflowPackageValidator(),
        RunnerSupervisor(),
        StubRuntimeManager(),
        LogStore(),
        model_availability_service=FakeAvailabilityService(),
        workflow_library_store=WorkflowLibraryStore(tmp_path / "library"),
    )

    row = service.list_workflows()[0]

    assert row["missing_model_count"] == 1
    assert row["needs_setup"] is False
    assert "models_used" not in row


def test_workflow_details_loads_drawer_data_separately(tmp_path: Path) -> None:
    service, workflow_id, _, _ = _service(tmp_path)

    details = service.workflow_details(workflow_id)

    assert details["overview"]["description"] == "Original description"
    assert details["models_used"] == []
    assert details["advanced"]["engine"] == "comfyui"


def test_metadata_edits_update_internal_copy_but_not_original_archive_or_history_export(tmp_path: Path) -> None:
    service, workflow_id, package_dir, archive = _service(tmp_path)

    service.update_workflow_metadata(
        workflow_id,
        WorkflowMetadataUpdate(
            description="Updated description",
            author="Noofy User",
            website="https://example.test",
            category="Inpainting",
            tags=["portrait", "cleanup"],
            icon="image",
        ),
    )
    service.workflow_library_store.record_run_result(
        workflow_id=workflow_id,
        job_id="job-local",
        status="completed",
        started_at=datetime.now(UTC),
    )

    assert (package_dir / "source-archive.noofy").read_bytes() == archive
    package_data = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    assert package_data["metadata"]["description"] == "Updated description"
    assert package_data["metadata"]["category"] == "Inpainting"

    exported, _ = service.export_workflow_archive(workflow_id)
    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        exported_package = json.loads(zf.read("package.json"))
        names = set(zf.namelist())
    assert exported_package["metadata"]["description"] == "Updated description"
    assert "run-history.json" not in names


def test_comfyui_json_export_uses_stored_source_graph(tmp_path: Path) -> None:
    service, workflow_id, package_dir, _ = _service(tmp_path)
    source_graph = {
        "10": {
            "class_type": "KSampler",
            "inputs": {"seed": 1234},
        }
    }
    source_graph_file = package_dir / "source-files" / "comfyui_graph.json"
    source_graph_file.write_text(json.dumps(source_graph), encoding="utf-8")

    graph_bytes, filename = service.export_workflow_comfyui_graph(workflow_id)

    assert filename.endswith(".comfyui.json")
    assert json.loads(graph_bytes) == source_graph


def test_native_workflow_cannot_be_removed(tmp_path: Path) -> None:
    log_store = LogStore()
    service = EngineService(
        WorkflowPackageLoader(Path(__file__).resolve().parents[1] / "app/workflows/packages"),
        WorkflowPackageValidator(),
        RunnerSupervisor(),
        StubRuntimeManager(),
        log_store,
        model_availability_service=FakeAvailabilityService(),
        workflow_library_store=WorkflowLibraryStore(tmp_path / "library"),
    )

    native = service.list_workflows()[0]
    assert native["can_remove"] is False
    assert native["can_export_noofy"] is True
    with pytest.raises(ValueError):
        service.remove_workflow(str(native["id"]))


def test_run_history_is_recorded_in_local_library_store(tmp_path: Path) -> None:
    service, workflow_id = _native_run_service(tmp_path)

    async def run() -> None:
        job = await service.run_workflow(workflow_id, {}, {})
        await service.get_result(job.job_id)

    asyncio.run(run())

    history = service.workflow_details(workflow_id)["run_history"]
    assert history["last_run_status"] == "completed"
    assert history["run_count"] == 1
