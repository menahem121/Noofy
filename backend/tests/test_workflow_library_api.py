from __future__ import annotations

import asyncio
import io
import json
import shutil
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.diagnostics import LogStore
from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.engine.models import EngineJob, JobResult, ModelInfo, RequiredModelAvailability, RequiredModelSummary
from app.engine.service import EngineService
from app.runtime.memory.hardware_warning import HardwareWarningReasonCode, evaluate_workflow_hardware_warning
from app.runtime.memory.memory_governor import (
    LocalMemoryLearningStore,
    LocalMemoryObservation,
    MachineMemorySnapshot,
    MemoryAttributionQuality,
    MemoryBackend,
    MemoryObservationOutcome,
    MemoryPressureLevel,
    MemorySignalQuality,
)
from app.runtime.runners.supervisor import CORE_RUNNER_FINGERPRINT, CORE_RUNNER_ID, RunnerDescriptor, RunnerKind, RunnerStatus, RunnerSupervisor
from app.workflows.exporter import WorkflowExporter
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.library import WorkflowLibraryStore, WorkflowMetadataUpdate, workflow_package_display_name
from app.workflows.library_service import WorkflowLibraryService
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import RequiredModel, WorkflowMetadata, WorkflowPackage, WorkflowPackageIdentity
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class StubMemoryObserver:
    def __init__(self, snapshot: MachineMemorySnapshot) -> None:
        self.snapshot_value = snapshot
        self.snapshot_count = 0

    def snapshot(self) -> MachineMemorySnapshot:
        self.snapshot_count += 1
        return self.snapshot_value


class CountingMemoryLearningStore(LocalMemoryLearningStore):
    def __init__(self, root_dir: Path) -> None:
        super().__init__(root_dir)
        self.list_summaries_count = 0

    def list_summaries(self):
        self.list_summaries_count += 1
        return super().list_summaries()


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


class VerifyingAvailabilityService:
    def cleanup_interrupted_downloads(self) -> int:
        return 0

    def summarize(self, package, *, deep_search=True, verify_hashes=True) -> RequiredModelSummary:
        status = "available" if verify_hashes else "possible_match"
        models = [
            RequiredModelAvailability(
                requirement_id=f"{model.folder}/{model.filename}",
                filename=model.filename,
                model_type=model.model_type,
                folder=model.folder,
                verification_level=ModelVerificationLevel.SHA256_SIZE,
                size_bytes=model.size_bytes,
                status=status,
                status_label="Available" if status == "available" else "Possible match",
                asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
                source_path=f"/models/{model.folder}/{model.filename}",
                message=None if status == "available" else "A local file with this name was found.",
            )
            for model in package.required_models
        ]
        available_count = sum(model.status == "available" for model in models)
        possible_count = sum(model.status == "possible_match" for model in models)
        return RequiredModelSummary(
            workflow_id=package.metadata.id,
            total_count=len(models),
            available_count=available_count,
            possible_match_count=possible_count,
            missing_count=0,
            needs_manual_download_count=0,
            ready_to_run=available_count == len(models),
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


def _workflow_library_service_for_package(tmp_path: Path, package_payload: dict[str, Any]) -> WorkflowLibraryService:
    packages_dir = tmp_path / "packages"
    package_dir = packages_dir / package_payload["metadata"]["id"]
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(json.dumps(package_payload), encoding="utf-8")
    loader = WorkflowPackageLoader(packages_dir)
    return WorkflowLibraryService(
        loader,
        FakeAvailabilityService(),
        LogStore(),
    )


def test_workflow_package_payload_filters_known_architecture_mismatches_without_mutating_schema(
    tmp_path: Path,
) -> None:
    package_payload = {
        "metadata": {"id": "sdxl_filter", "name": "SDXL Filter", "version": "1.0.0"},
        "engine": "comfyui",
        "required_models": [
            {
                "folder": "checkpoints",
                "filename": "SDXL_base.safetensors",
                "node_id": "4",
                "node_type": "CheckpointLoaderSimple",
                "input_name": "ckpt_name",
                "model_type": "checkpoint",
                "architecture_family": "sdxl",
                "architecture_family_confidence": "high",
                "architecture_family_source": "test",
            }
        ],
        "comfyui_graph": {
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "SDXL_base.safetensors"},
            }
        },
        "inputs": [
            {
                "id": "model",
                "label": "Model",
                "control": "select",
                "binding": {"node_id": "4", "input_name": "ckpt_name"},
                "default": "SDXL_base.safetensors",
                "validation": {
                    "options": [
                        "SDXL_base.safetensors",
                        "DreamshaperXL.safetensors",
                        "FLUX.dev.safetensors",
                        "unknown-style.safetensors",
                    ]
                },
            }
        ],
        "outputs": [],
        "dashboard": {"version": "0.1.0", "status": "configured", "sections": []},
    }
    service = _workflow_library_service_for_package(tmp_path, package_payload)

    response = service.workflow_package_payload("sdxl_filter")

    filtered_input = response["inputs"][0]
    assert filtered_input["validation"]["options"] == [
        "SDXL_base.safetensors",
        "DreamshaperXL.safetensors",
        "unknown-style.safetensors",
    ]
    assert filtered_input["validation"]["architecture_filter"]["hidden_options"] == [
        "FLUX.dev.safetensors"
    ]
    stored_package = service.workflow_loader.get_package("sdxl_filter")
    assert stored_package.inputs[0].validation["options"] == [
        "SDXL_base.safetensors",
        "DreamshaperXL.safetensors",
        "FLUX.dev.safetensors",
        "unknown-style.safetensors",
    ]


def test_workflow_package_payload_filters_loras_from_upstream_base_model(tmp_path: Path) -> None:
    package_payload = {
        "metadata": {"id": "lora_filter", "name": "LoRA Filter", "version": "1.0.0"},
        "engine": "comfyui",
        "required_models": [
            {
                "folder": "checkpoints",
                "filename": "base-sdxl.safetensors",
                "node_id": "4",
                "node_type": "CheckpointLoaderSimple",
                "input_name": "ckpt_name",
                "model_type": "checkpoint",
                "architecture_family": "sdxl",
            },
            {
                "folder": "loras",
                "filename": "style-sdxl.safetensors",
                "node_id": "12",
                "node_type": "LoraLoader",
                "input_name": "lora_name",
                "model_type": "lora",
            },
        ],
        "comfyui_graph": {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base-sdxl.safetensors"}},
            "12": {
                "class_type": "LoraLoader",
                "inputs": {"model": ["4", 0], "clip": ["4", 1], "lora_name": "style-sdxl.safetensors"},
            },
        },
        "inputs": [
            {
                "id": "style",
                "label": "Style",
                "control": "lora_loader",
                "binding": {"node_id": "12", "input_name": "lora_name"},
                "default": "None",
                "validation": {"options": ["None", "style-sdxl.safetensors", "flux-style.safetensors"]},
            }
        ],
        "outputs": [],
        "dashboard": {"version": "0.1.0", "status": "configured", "sections": []},
    }
    service = _workflow_library_service_for_package(tmp_path, package_payload)

    response = service.workflow_package_payload("lora_filter")

    assert response["inputs"][0]["validation"]["options"] == ["None", "style-sdxl.safetensors"]
    assert response["inputs"][0]["validation"]["architecture_filter"]["target_family"] == "sdxl"
    assert response["inputs"][0]["validation"]["architecture_filter"]["hidden_options"] == [
        "flux-style.safetensors"
    ]


def test_workflow_package_payload_leaves_unknown_targets_and_unrelated_categories_unfiltered(
    tmp_path: Path,
) -> None:
    package_payload = {
        "metadata": {"id": "unknown_filter", "name": "Unknown Filter", "version": "1.0.0"},
        "engine": "comfyui",
        "required_models": [
            {
                "folder": "checkpoints",
                "filename": "mystery.safetensors",
                "node_id": "4",
                "node_type": "CheckpointLoaderSimple",
                "input_name": "ckpt_name",
                "model_type": "checkpoint",
            },
            {
                "folder": "vae",
                "filename": "vae-sdxl.safetensors",
                "node_id": "5",
                "node_type": "VAELoader",
                "input_name": "vae_name",
                "model_type": "vae",
                "architecture_family": "sdxl",
            },
        ],
        "comfyui_graph": {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "mystery.safetensors"}},
            "5": {"class_type": "VAELoader", "inputs": {"vae_name": "vae-sdxl.safetensors"}},
        },
        "inputs": [
            {
                "id": "model",
                "label": "Model",
                "control": "select",
                "binding": {"node_id": "4", "input_name": "ckpt_name"},
                "default": "mystery.safetensors",
                "validation": {"options": ["mystery.safetensors", "FLUX.dev.safetensors"]},
            },
            {
                "id": "vae",
                "label": "VAE",
                "control": "select",
                "binding": {"node_id": "5", "input_name": "vae_name"},
                "default": "vae-sdxl.safetensors",
                "validation": {"options": ["vae-sdxl.safetensors", "vae-flux.safetensors"]},
            },
        ],
        "outputs": [],
        "dashboard": {"version": "0.1.0", "status": "configured", "sections": []},
    }
    service = _workflow_library_service_for_package(tmp_path, package_payload)

    response = service.workflow_package_payload("unknown_filter")

    assert response["inputs"][0]["validation"] == {"options": ["mystery.safetensors", "FLUX.dev.safetensors"]}
    assert response["inputs"][1]["validation"] == {"options": ["vae-sdxl.safetensors", "vae-flux.safetensors"]}


def _hardware_warning_service(
    tmp_path: Path,
    *,
    observed_hardware: dict[str, Any] | None = None,
    required_model_size_bytes: int | None = None,
    memory_snapshot: MachineMemorySnapshot | None = None,
    memory_learning_store: LocalMemoryLearningStore | None = None,
) -> tuple[EngineService, str, StubMemoryObserver | None]:
    packages_dir = tmp_path / "hardware-packages"
    package_dir = packages_dir / "hardware_warning_wf"
    package_dir.mkdir(parents=True)
    required_models = []
    if required_model_size_bytes is not None:
        required_models.append(
            {
                "folder": "checkpoints",
                "filename": "heavy.safetensors",
                "model_type": "checkpoint",
                "size_bytes": required_model_size_bytes,
            }
        )
    package_payload: dict[str, Any] = {
        "metadata": {
            "id": "hardware_warning_wf",
            "name": "Hardware Warning",
            "version": "1.0.0",
            "description": "Tests hardware warnings.",
        },
        "engine": "comfyui",
        "required_models": required_models,
        "comfyui_graph": {"1": {"class_type": "SaveImage", "inputs": {}}},
        "inputs": [],
        "outputs": [],
        "custom_nodes": [],
        "unresolved_runtime_inputs": [],
    }
    if observed_hardware is not None:
        package_payload["observed_hardware"] = observed_hardware
    (package_dir / "package.json").write_text(json.dumps(package_payload), encoding="utf-8")
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
    observer = StubMemoryObserver(memory_snapshot) if memory_snapshot is not None else None
    service = EngineService(
        WorkflowPackageLoader(packages_dir),
        WorkflowPackageValidator(),
        RunnerSupervisor(),
        StubRuntimeManager(),
        LogStore(),
        model_availability_service=FakeAvailabilityService(),
        workflow_library_store=WorkflowLibraryStore(tmp_path / "library"),
        memory_observer=observer,
        memory_learning_store=memory_learning_store,
    )
    return service, "hardware_warning_wf", observer


def _cuda_snapshot(
    *,
    total_vram_mb: int = 12_000,
    free_vram_mb: int = 8_000,
    total_ram_mb: int = 64_000,
    free_ram_mb: int = 48_000,
    memory_pressure: MemoryPressureLevel = MemoryPressureLevel.LOW,
) -> MachineMemorySnapshot:
    return MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        machine_profile_id="machine-a",
        device_name="Test GPU",
        total_vram_mb=total_vram_mb,
        free_vram_mb=free_vram_mb,
        total_ram_mb=total_ram_mb,
        free_ram_mb=free_ram_mb,
        memory_pressure=memory_pressure,
        signal_quality=MemorySignalQuality.BACKEND_API,
        signal_sources=["test"],
    )


def test_workflow_list_returns_lightweight_table_fields(tmp_path: Path) -> None:
    service, workflow_id, _, _ = _service(tmp_path)

    row = next(item for item in service.list_workflows() if item["id"] == workflow_id)

    assert row["source_label"] == "Imported"
    assert row["main_model"]["name"] == "No model detected"
    assert row["category"] == "Txt2img"
    assert row["can_remove"] is True
    assert row["hardware_warning"] is None
    assert "models_used" not in row
    assert "overview" not in row


def test_workflow_list_uses_package_discovery_category_and_tags(tmp_path: Path) -> None:
    service = _workflow_library_service_for_package(
        tmp_path,
        {
            "metadata": {
                "id": "export2noofy_discovery",
                "name": "Creator Video Tool",
                "version": "1.0.0",
                "description": "Turns a still image into motion.",
                "author": "Noofy Creator",
                "website": "https://example.test",
                "category": "img2vid",
                "tags": ["video", "starter"],
            },
            "engine": "comfyui",
            "required_models": [],
            "comfyui_graph": {"1": {"class_type": "SaveVideo", "inputs": {}}},
            "inputs": [],
            "outputs": [{"id": "video", "label": "Video", "type": "video", "kind": "video", "node_id": "1"}],
            "custom_nodes": [],
            "unresolved_runtime_inputs": [],
        },
    )

    row = service.list_workflows()[0]
    details = service.workflow_details("export2noofy_discovery")

    assert row["category"] == "img2vid"
    assert row["tags"] == ["video", "starter"]
    assert details["overview"]["description"] == "Turns a still image into motion."
    assert details["overview"]["author"] == "Noofy Creator"
    assert details["overview"]["website"] == "https://example.test"
    assert details["organization"]["category"] == "img2vid"
    assert details["organization"]["tags"] == ["video", "starter"]


def test_workflow_list_infers_media_workflow_type_categories_from_declared_interfaces(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    cases = [
        (
            "txt2audio",
            "txt2audio",
            [{"id": "prompt", "label": "Prompt", "control": "textarea"}],
            [{"id": "audio", "label": "Audio", "type": "audio", "kind": "audio"}],
            [],
        ),
        (
            "audio2audio",
            "audio2audio",
            [{"id": "audio_in", "label": "Audio", "control": "load_audio"}],
            [{"id": "audio", "label": "Audio", "type": "audio", "kind": "audio"}],
            [],
        ),
        (
            "txt2vid",
            "txt2vid",
            [{"id": "prompt", "label": "Prompt", "control": "textarea"}],
            [{"id": "video", "label": "Video", "type": "video", "kind": "video"}],
            [],
        ),
        (
            "img2vid",
            "img2vid",
            [{"id": "image_in", "label": "Image", "control": "load_image"}],
            [{"id": "video", "label": "Video", "type": "video", "kind": "video"}],
            [],
        ),
        (
            "vid2vid",
            "vid2vid",
            [{"id": "video_in", "label": "Video", "control": "load_video"}],
            [{"id": "video", "label": "Video", "type": "video", "kind": "video"}],
            [],
        ),
        (
            "imgTo3D",
            "imgTo3D",
            [{"id": "image_in", "label": "Image", "control": "load_image"}],
            [{"id": "model", "label": "Model", "type": "3d", "kind": "3d"}],
            [],
        ),
        (
            "txtTo3D",
            "txtTo3D",
            [{"id": "prompt", "label": "Prompt", "control": "textarea"}],
            [{"id": "model", "label": "Model", "type": "3d", "kind": "3d"}],
            [],
        ),
        (
            "txt2txt",
            "txt2txt",
            [{"id": "prompt", "label": "Prompt", "control": "textarea"}],
            [{"id": "text", "label": "Text", "type": "text", "kind": "text"}],
            [],
        ),
        (
            "img2text",
            "img2text",
            [{"id": "image_in", "label": "Image", "control": "load_image"}],
            [{"id": "caption", "label": "Caption", "type": "text", "kind": "text"}],
            [],
        ),
        (
            "audio2txt",
            "audio2txt",
            [{"id": "audio_in", "label": "Audio", "control": "load_audio"}],
            [{"id": "transcript", "label": "Transcript", "type": "text", "kind": "text"}],
            [],
        ),
        (
            "unresolved_image_to_video",
            "img2vid",
            [],
            [{"id": "video", "label": "Video", "type": "video", "kind": "video"}],
            [{"expected_kind": "image"}],
        ),
    ]
    for workflow_id, _, inputs, outputs, unresolved in cases:
        _write_category_fixture(packages_dir, workflow_id, inputs, outputs, unresolved=unresolved)

    service = WorkflowLibraryService(
        WorkflowPackageLoader(packages_dir),
        model_availability_service=FakeAvailabilityService(),
        log_store=LogStore(),
    )

    categories = {row["id"]: row["category"] for row in service.list_workflows()}
    for workflow_id, expected_category, _, _, _ in cases:
        assert categories[workflow_id] == expected_category


def test_workflow_list_preserves_task_category_inference_before_media_type_fallback(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    _write_category_fixture(
        packages_dir,
        "video_upscale",
        [{"id": "video_in", "label": "Video", "control": "load_video"}],
        [{"id": "video", "label": "Video", "type": "video", "kind": "video"}],
    )

    service = WorkflowLibraryService(
        WorkflowPackageLoader(packages_dir),
        model_availability_service=FakeAvailabilityService(),
        log_store=LogStore(),
    )

    row = service.list_workflows()[0]

    assert row["category"] == "Upscaling"


def _write_category_fixture(
    packages_dir: Path,
    workflow_id: str,
    inputs: list[dict[str, str]],
    outputs: list[dict[str, str]],
    unresolved: list[dict[str, str]] | None = None,
) -> None:
    package_dir = packages_dir / workflow_id
    package_dir.mkdir(parents=True)
    normalized_inputs = [
        {
            **input_def,
            "binding": {"node_id": "1", "input_name": input_def["id"]},
            "default": "",
            "validation": {},
        }
        for input_def in inputs
    ]
    normalized_outputs = [
        {
            **output_def,
            "node_id": "2",
        }
        for output_def in outputs
    ]
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": workflow_id, "name": workflow_id, "version": "1.0.0"},
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {
                    "1": {"class_type": "InputNode", "inputs": {}},
                    "2": {"class_type": "OutputNode", "inputs": {}},
                },
                "inputs": normalized_inputs,
                "outputs": normalized_outputs,
                "custom_nodes": [],
                "unresolved_runtime_inputs": [
                    {
                        "node_id": "3",
                        "node_type": "LoadImage",
                        "input_name": "image",
                        "reason": "creator_local_image_not_bundled",
                        "expected_kind": item["expected_kind"],
                    }
                    for item in (unresolved or [])
                ],
            }
        ),
        encoding="utf-8",
    )


def test_hardware_warning_is_yellow_for_temporary_low_free_memory(tmp_path: Path) -> None:
    service, workflow_id, observer = _hardware_warning_service(
        tmp_path,
        memory_snapshot=_cuda_snapshot(total_vram_mb=12_000, free_vram_mb=1_500),
    )

    row = next(item for item in service.list_workflows() if item["id"] == workflow_id)
    warning = row["hardware_warning"]

    assert observer is not None
    assert observer.snapshot_count == 1
    assert warning["severity"] == "medium"
    assert warning["confidence"] == "low"
    assert HardwareWarningReasonCode.TEMPORARY_LOW_FREE_MEMORY.value in warning["reason_codes"]


def test_workflow_list_reads_hardware_warning_signals_once(tmp_path: Path) -> None:
    learning = CountingMemoryLearningStore(tmp_path / "learning")
    service, workflow_id, observer = _hardware_warning_service(
        tmp_path,
        memory_snapshot=_cuda_snapshot(),
        memory_learning_store=learning,
    )
    packages_dir = tmp_path / "hardware-packages"
    shutil.copytree(packages_dir / workflow_id, packages_dir / "hardware_warning_wf_2")
    second_package_path = packages_dir / "hardware_warning_wf_2" / "package.json"
    second_package = json.loads(second_package_path.read_text(encoding="utf-8"))
    second_package["metadata"]["id"] = "hardware_warning_wf_2"
    second_package["metadata"]["name"] = "Hardware Warning Two"
    second_package_path.write_text(json.dumps(second_package), encoding="utf-8")

    rows = service.list_workflows()

    assert len(rows) == 2
    assert observer is not None
    assert observer.snapshot_count == 1
    assert learning.list_summaries_count == 1


def test_hardware_warning_is_red_for_matching_local_memory_failure(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            outcome=MemoryObservationOutcome.MEMORY_ERROR,
            peak_vram_mb=7_000,
            attribution_quality=MemoryAttributionQuality.PROCESS_TREE,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        memory_snapshot=_cuda_snapshot(total_vram_mb=12_000, free_vram_mb=9_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning["severity"] == "high"
    assert warning["confidence"] == "high"
    assert warning["evidence"]["local_memory_error_runs"] == 1
    assert warning["evidence"]["local_input_profile_match"] == "matching"
    assert warning["reason_codes"] == [HardwareWarningReasonCode.LOCAL_MEMORY_ERROR.value]


def test_hardware_warning_marks_trusted_peak_above_machine_capacity(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            outcome=MemoryObservationOutcome.MEMORY_ERROR,
            peak_vram_mb=14_000,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        memory_snapshot=_cuda_snapshot(total_vram_mb=12_000, free_vram_mb=11_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning["severity"] == "high"
    assert warning["exceeds_machine_capacity"] is True
    assert warning["estimate"]["estimated_peak_vram_mb"] == 14_000
    assert warning["machine_signal"]["total_vram_mb"] == 12_000
    assert warning["developer_details"]["exceeds_machine_capacity"] is True


def test_later_matching_local_success_clears_memory_error_hardware_warning(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            outcome=MemoryObservationOutcome.MEMORY_ERROR,
            peak_vram_mb=7_800,
            observed_at=(datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        )
    )
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=7_600,
            observed_at=datetime.now(UTC).isoformat(),
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=5_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning is None


def test_matching_local_success_near_capacity_does_not_show_hardware_warning(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=7_400,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        observed_hardware={"observed_peak_vram_mb": 12_000},
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=8_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning is None


def test_matching_local_success_with_low_free_memory_does_not_show_hardware_warning(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=7_400,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        observed_hardware={"observed_peak_vram_mb": 12_000},
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=5_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning is None


def test_matching_local_success_suppresses_creator_only_hardware_warning(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-a",
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=1_200,
            peak_ram_mb=900,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        observed_hardware={"observed_peak_vram_mb": 12_000},
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=7_000),
        memory_learning_store=learning,
    )

    package = service.workflow_loader.get_package(workflow_id)
    warning = evaluate_workflow_hardware_warning(
        package,
        memory_learning_store=learning,
        machine_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=7_000),
        input_profile_fingerprint="settings-a",
    )

    assert warning is None


def test_library_card_local_success_suppresses_creator_warning_without_active_settings(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-a",
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=1_200,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        observed_hardware={"observed_peak_vram_mb": 12_000},
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=7_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning is None


def test_explicit_mismatched_local_success_does_not_suppress_creator_warning(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-a",
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=1_200,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        observed_hardware={"observed_peak_vram_mb": 12_000},
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=7_000),
        memory_learning_store=learning,
    )

    package = service.workflow_loader.get_package(workflow_id)
    warning = evaluate_workflow_hardware_warning(
        package,
        memory_learning_store=learning,
        machine_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=7_000),
        input_profile_fingerprint="settings-b",
    )

    assert warning is not None
    assert warning.severity == "medium"
    assert warning.evidence.local_input_profile_match == "mismatched"
    assert HardwareWarningReasonCode.LOCAL_SUCCESS_SETTINGS_MISMATCH in warning.reason_codes
    assert HardwareWarningReasonCode.CREATOR_OBSERVED_MEMORY_HINT in warning.reason_codes
    assert set(warning.reason_codes).issubset(set(HardwareWarningReasonCode))


def test_profiled_local_memory_failure_still_produces_advisory_warning_for_card(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-a",
            outcome=MemoryObservationOutcome.MEMORY_ERROR,
            peak_vram_mb=7_000,
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        memory_snapshot=_cuda_snapshot(total_vram_mb=12_000, free_vram_mb=9_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning["severity"] == "high"
    assert warning["confidence"] == "high"
    assert warning["evidence"]["local_memory_error_runs"] == 1
    assert warning["evidence"]["local_input_profile_match"] == "matching"
    assert warning["reason_codes"] == [HardwareWarningReasonCode.LOCAL_MEMORY_ERROR.value]


def test_stale_local_memory_failure_does_not_warn(tmp_path: Path) -> None:
    learning = LocalMemoryLearningStore(tmp_path / "learning")
    learning.record(
        LocalMemoryObservation(
            workflow_id="hardware_warning_wf",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            outcome=MemoryObservationOutcome.MEMORY_ERROR,
            peak_vram_mb=7_000,
            observed_at=(datetime.now(UTC) - timedelta(days=60)).isoformat(),
        )
    )
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        memory_snapshot=_cuda_snapshot(total_vram_mb=12_000, free_vram_mb=9_000),
        memory_learning_store=learning,
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning is None


def test_model_size_capacity_risk_without_memory_error_stays_medium(tmp_path: Path) -> None:
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        required_model_size_bytes=8 * 1024 * 1024 * 1024,
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=7_000),
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning["severity"] == "medium"
    assert warning["exceeds_machine_capacity"] is False
    assert warning["confidence"] == "low"
    assert HardwareWarningReasonCode.MODEL_SIZE_HEURISTIC.value in warning["reason_codes"]
    assert HardwareWarningReasonCode.ESTIMATED_VRAM_CAPACITY_RISK.value in warning["reason_codes"]


def test_near_capacity_model_size_heuristic_stays_yellow(tmp_path: Path) -> None:
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        required_model_size_bytes=5_500 * 1024 * 1024,
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=8_000),
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]

    assert warning["severity"] == "medium"
    assert warning["confidence"] == "low"
    assert warning["estimate"]["source"] == "heuristic"
    assert HardwareWarningReasonCode.ESTIMATED_VRAM_CAPACITY_RISK.value in warning["reason_codes"]


def test_hardware_warning_counts_shared_model_file_once() -> None:
    def _node(node_id: str) -> RequiredModel:
        return RequiredModel(
            folder="checkpoints",
            filename="heavy.safetensors",
            node_id=node_id,
            input_name="ckpt_name",
            size_bytes=5_500 * 1024 * 1024,
            verification_level=ModelVerificationLevel.FILENAME_SIZE,
        )

    warning = evaluate_workflow_hardware_warning(
        WorkflowPackage(
            metadata=WorkflowMetadata(id="shared-model", name="Shared Model", version="0.1.0"),
            engine="comfyui",
            required_models=[_node("1"), _node("2")],
            comfyui_graph={},
        ),
        machine_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=8_000),
    )

    assert warning is not None
    assert warning.evidence.required_model_size_mb == 5_500


def test_hardware_warning_developer_details_are_diagnostic_safe(tmp_path: Path) -> None:
    service, workflow_id, _ = _hardware_warning_service(
        tmp_path,
        observed_hardware={"observed_peak_vram_mb": 12_000},
        memory_snapshot=_cuda_snapshot(total_vram_mb=8_000, free_vram_mb=7_000),
    )

    warning = next(item for item in service.list_workflows() if item["id"] == workflow_id)["hardware_warning"]
    details_text = json.dumps(warning["developer_details"], sort_keys=True)

    assert warning["severity"] == "medium"
    assert "machine-a" not in details_text
    assert "Test GPU" not in details_text
    assert "machine_profile_id" not in details_text


def test_workflow_open_history_updates_last_opened_without_run_history(tmp_path: Path) -> None:
    service, workflow_id, _, _ = _service(tmp_path)

    before = next(item for item in service.list_workflows() if item["id"] == workflow_id)
    opened = service.workflow_library_service.record_workflow_opened(workflow_id)
    after = next(item for item in service.list_workflows() if item["id"] == workflow_id)

    assert before["last_opened"] is None
    assert opened["workflow_id"] == workflow_id
    assert isinstance(opened["last_opened"], str)
    assert opened["workflow"]["last_opened"] == opened["last_opened"]
    assert after["last_opened"] == opened["last_opened"]
    assert service.workflow_details(workflow_id)["run_history"]["run_count"] == 0


def test_record_workflow_opened_rejects_unknown_workflow(tmp_path: Path) -> None:
    service, _, _, _ = _service(tmp_path)

    with pytest.raises(KeyError):
        service.workflow_library_service.record_workflow_opened("missing_workflow")


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


def test_workflow_model_verification_job_accepts_possible_local_match(tmp_path: Path) -> None:
    packages_dir = tmp_path / "native-packages"
    package_dir = packages_dir / "possible_model_wf"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": "possible_model_wf", "name": "Possible Model", "version": "1.0.0"},
                "engine": "comfyui",
                "required_models": [
                    {
                        "folder": "checkpoints",
                        "filename": "v1-5-pruned-emaonly-fp16.safetensors",
                        "model_type": "checkpoint",
                        "checksum": "sha256:abc",
                        "size_bytes": 1,
                    }
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
    service = EngineService(
        WorkflowPackageLoader(packages_dir),
        WorkflowPackageValidator(),
        RunnerSupervisor(),
        StubRuntimeManager(),
        LogStore(),
        model_availability_service=VerifyingAvailabilityService(),
        workflow_library_store=WorkflowLibraryStore(tmp_path / "library"),
    )

    started = service.workflow_library_service.start_model_verification("possible_model_wf")
    finished = service.workflow_library_service.model_verification_status(
        "possible_model_wf",
        started.job_id,
    )

    assert finished.status == "completed"
    assert finished.model_summary is not None
    assert finished.model_summary.ready_to_run is True
    assert finished.models[0].status == "available"


def test_workflow_details_loads_drawer_data_separately(tmp_path: Path) -> None:
    service, workflow_id, _, _ = _service(tmp_path)

    details = service.workflow_details(workflow_id)

    assert details["name"] == "Library Workflow"
    assert details["display_name"] == "Library Workflow"
    assert details["overview"]["display_name"] == "Library Workflow"
    assert details["overview"]["description"] == "Original description"
    assert details["models_used"] == []
    assert details["advanced"]["engine"] == "comfyui"


def test_metadata_edits_update_internal_copy_but_not_original_archive_or_history_export(tmp_path: Path) -> None:
    service, workflow_id, package_dir, archive = _service(tmp_path)

    response = service.update_workflow_metadata(
        workflow_id,
        WorkflowMetadataUpdate(
            display_name="Edited Cleanup Workflow",
            description="Updated description",
            author="Noofy User",
            website="https://example.test",
            category="Inpainting",
            tags=["portrait", "cleanup"],
            icon="image",
        ),
    )
    assert response["workflow"]["name"] == "Edited Cleanup Workflow"
    assert response["workflow"]["display_name"] == "Edited Cleanup Workflow"

    service.workflow_library_store.record_run_result(
        workflow_id=workflow_id,
        job_id="job-local",
        status="completed",
        started_at=datetime.now(UTC),
    )

    assert (package_dir / "source-archive.noofy").read_bytes() == archive
    package_data = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    assert package_data["display_name"] == "Edited Cleanup Workflow"
    assert package_data["metadata"]["name"] == "Edited Cleanup Workflow"
    assert package_data["metadata"]["display_name"] == "Edited Cleanup Workflow"
    assert package_data["metadata"]["description"] == "Updated description"
    assert package_data["metadata"]["author"] == "Noofy User"
    assert package_data["metadata"]["website"] == "https://example.test"
    assert package_data["metadata"]["category"] == "Inpainting"
    assert package_data["metadata"]["tags"] == ["portrait", "cleanup"]
    assert package_data["metadata"]["icon"] == "image"

    payload = service.workflow_library_service.workflow_package_payload(workflow_id)
    assert payload["metadata"]["author"] == "Noofy User"
    assert payload["metadata"]["website"] == "https://example.test"
    assert payload["metadata"]["category"] == "Inpainting"
    assert payload["metadata"]["tags"] == ["portrait", "cleanup"]
    assert payload["metadata"]["icon"] == "image"

    exported, _ = service.export_workflow_archive(workflow_id)
    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        exported_package = json.loads(zf.read("package.json"))
        names = set(zf.namelist())
        exported_json_text = "\n".join(
            zf.read(name).decode("utf-8")
            for name in names
            if name.endswith(".json")
        )
    assert exported_package["display_name"] == "Edited Cleanup Workflow"
    assert exported_package["metadata"]["display_name"] == "Edited Cleanup Workflow"
    assert exported_package["metadata"]["name"] == "Edited Cleanup Workflow"
    assert exported_package["metadata"]["description"] == "Updated description"
    assert "hardware_warning" not in exported_package
    assert "hardware_warning" not in exported_json_text
    assert "local_memory_error_runs" not in exported_json_text
    assert "run-history.json" not in names


def test_empty_workflow_display_name_edit_is_rejected(tmp_path: Path) -> None:
    service, workflow_id, _, _ = _service(tmp_path)

    with pytest.raises(ValueError, match="Workflow name cannot be empty"):
        service.update_workflow_metadata(
            workflow_id,
            WorkflowMetadataUpdate(display_name="   "),
        )


def test_display_name_helper_preserves_explicit_display_names_with_underscores() -> None:
    package = WorkflowPackage(
        display_name="My_Custom Workflow",
        metadata=WorkflowMetadata(
            id="unknown__technical_package__1.0.0",
            name="technical_package",
            version="1.0.0",
        ),
        identity=WorkflowPackageIdentity(
            publisher_id="unknown",
            package_id="technical_package",
            version="1.0.0",
            trust_level="quarantined_community",
            source="local",
        ),
        engine="comfyui",
        required_models=[],
        comfyui_graph={},
    )

    assert workflow_package_display_name(package) == "My_Custom Workflow"


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
