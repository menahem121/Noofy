import json
from pathlib import Path

import pytest

from app.artifacts import ModelVerificationLevel
from app.diagnostics import LogStore
from app.engine.models import ModelInfo
from app.engine.service import EngineService
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerSupervisor,
)
from app.workflows.bindings import apply_input_bindings, package_for_input_bindings
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class StubEngineAdapter:
    def __init__(self, models: list[ModelInfo]) -> None:
        self.models = models

    async def list_available_models(self) -> list[ModelInfo]:
        return self.models

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        pass


def _supervisor_with(adapter: StubEngineAdapter) -> RunnerSupervisor:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url=StubRuntimeManager.base_url,
            ws_url=StubRuntimeManager.ws_url,
            fingerprint=CORE_RUNNER_FINGERPRINT,
        ),
        adapter,
    )
    return supervisor


def test_text_to_image_package_loads() -> None:
    packages_dir = Path("app/workflows/packages")
    loader = WorkflowPackageLoader(packages_dir)

    package = loader.get_package("text_to_image_v0")

    assert package.metadata.id == "text_to_image_v0"
    assert package.engine == "comfyui"
    assert package.dashboard.sections
    assert package.smoke_tests.workflow_execution is not None
    assert package.smoke_tests.workflow_execution.name == "default-core-empty-image"
    assert package.smoke_tests.workflow_execution.required_node_types == ["EmptyImage", "SaveImage"]
    assert package.required_models[0].size_bytes == 2132696762
    assert package.required_models[0].verification_level is ModelVerificationLevel.SHA256_SIZE


def test_loader_enriches_weak_package_model_identity_from_capsule_lock(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "weak_identity"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": "weak_identity", "name": "Weak Identity", "version": "0.1.0"},
                "engine": "comfyui",
                "required_models": [
                    {
                        "folder": "checkpoints",
                        "filename": "demo.safetensors",
                        "source_url": "https://example.test/demo.safetensors",
                    }
                ],
                "comfyui_graph": {},
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "capsule.lock.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "comfyui_folder": "checkpoints",
                        "filename": "demo.safetensors",
                        "sha256": "a" * 64,
                        "size_bytes": 123,
                        "source_urls": ["https://example.test/demo.safetensors"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package("weak_identity")

    model = package.required_models[0]
    assert model.checksum == f"sha256:{'a' * 64}"
    assert model.size_bytes == 123
    assert model.verification_level is ModelVerificationLevel.SHA256_SIZE


def test_loader_filters_unresolved_inputs_resolved_by_dashboard_override(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "image_workflow"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "image_workflow",
                    "name": "Image Workflow",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {
                    "192": {
                        "class_type": "LoadImage",
                        "inputs": {
                            "image": "__noofy_runtime_image_input_required__",
                        },
                    }
                },
                "unresolved_runtime_inputs": [
                    {
                        "node_id": "192",
                        "node_type": "LoadImage",
                        "input_name": "image",
                        "current_value": "__noofy_runtime_image_input_required__",
                        "reason": "creator_local_image_not_bundled",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "dashboard.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "status": "configured",
                "inputs": [
                    {
                        "id": "ctrl-node-192-image",
                        "label": "Input image",
                        "control": "load_image",
                        "binding": {"node_id": "192", "input_name": "image"},
                    }
                ],
                "outputs": [],
                "sections": [
                    {
                        "id": "main",
                        "title": "Main",
                        "controls": [
                            {
                                "id": "ctrl-node-192-image",
                                "type": "load_image",
                                "label": "Input image",
                                "input_id": "ctrl-node-192-image",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package("image_workflow")

    assert package.unresolved_runtime_inputs == []


def test_loader_repairs_imported_custom_node_metadata_from_source_files(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "image_workflow"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "image_workflow",
                    "name": "Image Workflow",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {},
                "custom_nodes": [
                    {
                        "id": "custom-node",
                        "folder_name": "custom-node",
                        "source": "bundled_from_creator_machine",
                        "included": False,
                        "node_types": ["ColorMatchV2"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    source_files = package_dir / "source-files"
    source_files.mkdir()
    (source_files / "package.json").write_text(
        json.dumps(
            {
                "custom_nodes": [
                    {
                        "id": "comfyui-kjnodes",
                        "folder_name": "ComfyUI-KJNodes",
                        "source": "bundled_from_creator_machine",
                        "included": True,
                        "requirements_files": ["requirements.txt"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (source_files / "capsule.lock.json").write_text(
        json.dumps(
            {
                "custom_nodes": [
                    {
                        "package_id": "comfyui-kjnodes",
                        "source": "bundled_from_creator_machine",
                        "trust_level": "quarantined_community",
                        "node_types": ["ColorMatchV2"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package("image_workflow")

    assert package.custom_nodes[0].id == "comfyui-kjnodes"
    assert package.custom_nodes[0].folder_name == "ComfyUI-KJNodes"
    assert package.custom_nodes[0].included is True


def test_bundled_package_model_identity_is_not_weaker_than_capsule_lock() -> None:
    packages_dir = Path("app/workflows/packages")
    for capsule_file in packages_dir.glob("*/capsule.lock.json"):
        package_file = capsule_file.parent / "package.json"
        package_data = json.loads(package_file.read_text(encoding="utf-8"))
        capsule_data = json.loads(capsule_file.read_text(encoding="utf-8"))
        package_models = {
            (model.get("folder"), model.get("filename")): model
            for model in package_data.get("required_models", [])
            if isinstance(model, dict)
        }
        for locked in capsule_data.get("models", []):
            key = (locked.get("comfyui_folder"), locked.get("filename"))
            package_model = package_models.get(key)
            assert package_model is not None, f"{package_file} is missing locked model {key}"
            expected_checksum = f"sha256:{locked['sha256']}"
            assert package_model.get("checksum") == expected_checksum
            assert package_model.get("size_bytes") == locked.get("size_bytes")
            assert package_model.get("verification_level") == ModelVerificationLevel.SHA256_SIZE.value


def test_engine_service_workflow_summary_includes_phase6_trust_metadata() -> None:
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(StubEngineAdapter([])),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
    )

    summary = service.list_workflows()[0]

    assert summary["trust_level"] == "noofy_verified"
    assert summary["trust"]["label"] == "Noofy Verified"
    assert summary["trust"]["source_policy"] == "noofy_verified_sources_only"
    assert summary["trust"]["requires_explicit_opt_in"] is False


def test_workflow_package_can_declare_execution_smoke_fixture(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "fixture_workflow"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        """
        {
          "metadata": {
            "id": "fixture_workflow",
            "name": "Fixture workflow",
            "version": "0.1.0"
          },
          "engine": "comfyui",
          "comfyui_graph": {},
          "dashboard": {"version": "0.1.0", "sections": []},
          "smoke_tests": {
            "workflow_execution": {
              "name": "tiny-noop",
              "prompt": {
                "1": {"class_type": "NoOp", "inputs": {}}
              },
              "required_node_types": ["NoOp"],
              "expected_output_node_count": 1,
              "expected_output_node_ids": ["1"],
              "timeout_seconds": 5
            }
          }
        }
        """,
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package("fixture_workflow")

    assert package.smoke_tests.workflow_execution is not None
    assert package.smoke_tests.workflow_execution.name == "tiny-noop"
    assert package.smoke_tests.workflow_execution.required_node_types == ["NoOp"]
    assert package.smoke_tests.workflow_execution.expected_output_node_count == 1
    assert package.smoke_tests.workflow_execution.expected_output_node_ids == ["1"]
    assert package.smoke_tests.workflow_execution.timeout_seconds == 5


def test_validator_reports_missing_model() -> None:
    packages_dir = Path("app/workflows/packages")
    package = WorkflowPackageLoader(packages_dir).get_package("text_to_image_v0")

    validator = WorkflowPackageValidator()
    structure = validator.validate_structure(package)
    missing_models = validator.validate_models(package, available_models=set())
    result = validator.combine(package, structure, missing_models)

    assert not result.valid
    assert result.missing_models[0].filename == "v1-5-pruned-emaonly-fp16.safetensors"


def test_validator_rejects_audio_widget_bound_to_image_output() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "invalid_audio", "name": "Invalid Audio", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {"9": {"class_type": "SaveImage", "inputs": {}}},
            "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image", "kind": "image"}],
            "dashboard": {
                "version": "1",
                "status": "configured",
                "sections": [
                    {
                        "id": "main",
                        "title": "Main",
                        "controls": [
                            {"id": "audio", "type": "display_audio", "label": "Audio", "output_id": "image"},
                        ],
                    }
                ],
            },
        }
    )

    result = WorkflowPackageValidator().validate_structure(package)

    assert not result.valid
    assert "type 'display_audio' but output 'image' is 'image'" in result.errors[0]


def test_validator_rejects_video_widget_bound_to_image_output() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "invalid_video", "name": "Invalid Video", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {"9": {"class_type": "SaveImage", "inputs": {}}},
            "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image", "kind": "image"}],
            "dashboard": {
                "version": "1",
                "status": "configured",
                "sections": [
                    {
                        "id": "main",
                        "title": "Main",
                        "controls": [
                            {"id": "video", "type": "display_video", "label": "Video", "output_id": "image"},
                        ],
                    }
                ],
            },
        }
    )

    result = WorkflowPackageValidator().validate_structure(package)

    assert not result.valid
    assert "type 'display_video' but output 'image' is 'image'" in result.errors[0]


def test_validator_accepts_declared_non_image_output_kinds() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "media_outputs", "name": "Media Outputs", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {
                "1": {"class_type": "SaveAudio", "inputs": {}},
                "2": {"class_type": "SaveVideo", "inputs": {}},
                "3": {"class_type": "Save3D", "inputs": {}},
                "4": {"class_type": "SaveText", "inputs": {}},
                "5": {"class_type": "SaveFile", "inputs": {}},
            },
            "outputs": [
                {"id": "audio", "label": "Audio Output", "node_id": "1", "type": "audio", "kind": "audio"},
                {"id": "video", "label": "Video Output", "node_id": "2", "type": "video", "kind": "video"},
                {"id": "mesh", "label": "3D Output", "node_id": "3", "type": "3d", "kind": "3d"},
                {"id": "text", "label": "Text Output", "node_id": "4", "type": "text", "kind": "text"},
                {"id": "file", "label": "File Output", "node_id": "5", "type": "file", "kind": "file"},
            ],
            "dashboard": {"version": "1", "status": "not_configured", "sections": []},
        }
    )

    result = WorkflowPackageValidator().validate_structure(package)

    assert result.valid


def test_validator_rejects_image_widget_bound_to_non_image_output() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "invalid_image", "name": "Invalid Image", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {"9": {"class_type": "SaveAudio", "inputs": {}}},
            "outputs": [{"id": "audio", "label": "Audio", "node_id": "9", "type": "audio", "kind": "audio"}],
            "dashboard": {
                "version": "1",
                "status": "configured",
                "sections": [
                    {
                        "id": "main",
                        "title": "Main",
                        "controls": [
                            {"id": "image", "type": "display_image", "label": "Image", "output_id": "audio"},
                        ],
                    }
                ],
            },
        }
    )

    result = WorkflowPackageValidator().validate_structure(package)

    assert not result.valid
    assert "type 'display_image' but output 'audio' is 'audio'" in result.errors[0]


def test_validator_accepts_three_d_output_widget() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "three_d_widget", "name": "3D Widget", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {"1": {"class_type": "Load3D", "inputs": {"model_file": ""}}, "2": {"class_type": "SaveGLB", "inputs": {}}},
            "inputs": [{"id": "model", "label": "Model", "control": "load_3d", "binding": {"node_id": "1", "input_name": "model_file"}}],
            "outputs": [{"id": "model", "label": "Model", "node_id": "2", "type": "3d", "kind": "3d"}],
            "dashboard": {
                "version": "1",
                "status": "configured",
                "sections": [{"id": "main", "title": "Main", "controls": [
                    {"id": "model-input", "type": "load_3d", "label": "Model", "input_id": "model"},
                    {"id": "model-output", "type": "display_3d", "label": "Model", "output_id": "model"},
                ]}],
            },
        }
    )

    assert WorkflowPackageValidator().validate_structure(package).valid


def test_validator_accepts_text_output_widget() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "text_widget", "name": "Text Widget", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {"4": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}}},
            "outputs": [{"id": "text", "label": "Text", "node_id": "4", "type": "text", "kind": "text"}],
            "dashboard": {
                "version": "1",
                "status": "configured",
                "sections": [{"id": "main", "title": "Main", "controls": [
                    {"id": "text-output", "type": "display_text", "label": "Text", "output_id": "text"},
                ]}],
            },
        }
    )

    assert WorkflowPackageValidator().validate_structure(package).valid


def test_validator_accepts_file_widget_with_accept_rules() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "file_widget", "name": "File Widget", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {"1": {"class_type": "LoadFile", "inputs": {"file_path": ""}}, "2": {"class_type": "SaveFile", "inputs": {}}},
            "inputs": [
                {
                    "id": "source-file",
                    "label": "Source file",
                    "control": "load_file",
                    "binding": {"node_id": "1", "input_name": "file_path"},
                    "validation": {"accepted_extensions": [".json"]},
                }
            ],
            "outputs": [{"id": "file", "label": "File", "node_id": "2", "type": "file", "kind": "file"}],
            "dashboard": {
                "version": "1",
                "status": "configured",
                "sections": [
                    {
                        "id": "main",
                        "title": "Main",
                        "controls": [
                            {"id": "source-file", "type": "load_file", "label": "Source", "input_id": "source-file"},
                            {"id": "file", "type": "display_file", "label": "File", "output_id": "file"},
                        ],
                    }
                ],
            },
        }
    )

    result = WorkflowPackageValidator().validate_structure(package)

    assert result.valid


def test_validator_rejects_file_widget_without_accept_rules() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "file_widget", "name": "File Widget", "version": "1"},
            "engine": "comfyui",
            "comfyui_graph": {"1": {"class_type": "LoadFile", "inputs": {"file_path": ""}}},
            "inputs": [
                {
                    "id": "source-file",
                    "label": "Source file",
                    "control": "load_file",
                    "binding": {"node_id": "1", "input_name": "file_path"},
                }
            ],
            "dashboard": {
                "version": "1",
                "status": "configured",
                "sections": [{"id": "main", "title": "Main", "controls": [{"id": "source-file", "type": "load_file", "label": "Source", "input_id": "source-file"}]}],
            },
        }
    )

    result = WorkflowPackageValidator().validate_structure(package)

    assert not result.valid
    assert "has no accepted_extensions or accepted_mime_types" in result.errors[0]


def test_input_bindings_are_applied() -> None:
    package = WorkflowPackageLoader(Path("app/workflows/packages")).get_package("text_to_image_v0")
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(StubEngineAdapter([])),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
    )

    graph = service._apply_input_bindings(
        package,
        {
            "prompt": "test prompt",
            "seed": 123,
            "width": 768,
            "height": 640,
        },
    )

    assert graph["6"]["inputs"]["text"] == "test prompt"
    assert graph["3"]["inputs"]["seed"] == 123
    assert graph["5"]["inputs"]["width"] == 768
    assert graph["5"]["inputs"]["height"] == 640


def test_lora_none_bypasses_loader_node_and_required_model() -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "lora_wf", "name": "LoRA workflow", "version": "0.1.0"},
            "engine": "comfyui",
            "required_models": [
                {
                    "folder": "checkpoints",
                    "filename": "base.safetensors",
                    "node_id": "4",
                    "node_type": "CheckpointLoaderSimple",
                    "input_name": "ckpt_name",
                    "model_type": "checkpoint",
                },
                {
                    "folder": "loras",
                    "filename": "style.safetensors",
                    "node_id": "12",
                    "node_type": "LoraLoader",
                    "input_name": "lora_name",
                    "model_type": "lora",
                },
            ],
            "comfyui_graph": {
                "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "base.safetensors"}},
                "12": {
                    "class_type": "LoraLoader",
                    "inputs": {"model": ["4", 0], "clip": ["4", 1], "lora_name": "style.safetensors"},
                },
                "20": {"class_type": "KSampler", "inputs": {"model": ["12", 0]}},
                "21": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["12", 1], "text": "prompt"}},
            },
            "inputs": [
                {
                    "id": "style_lora",
                    "label": "Style LoRA",
                    "control": "lora_loader",
                    "binding": {"node_id": "12", "input_name": "lora_name"},
                    "default": "None",
                    "validation": {"options": ["style.safetensors"]},
                }
            ],
        }
    )

    runtime_package = package_for_input_bindings(package, {"style_lora": "None"})
    graph = apply_input_bindings(package, {"style_lora": "None"})

    assert [model.filename for model in runtime_package.required_models] == ["base.safetensors"]
    assert "12" not in graph
    assert graph["20"]["inputs"]["model"] == ["4", 0]
    assert graph["21"]["inputs"]["clip"] == ["4", 1]


@pytest.mark.anyio
async def test_engine_service_validates_models_from_adapter() -> None:
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(
            StubEngineAdapter(
                [
                    ModelInfo(
                        folder="checkpoints",
                        filename="v1-5-pruned-emaonly-fp16.safetensors",
                    )
                ]
            )
        ),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
    )

    result = await service.validate_workflow("text_to_image_v0")

    assert result.valid
    assert result.missing_models == []


@pytest.mark.anyio
async def test_engine_service_logs_validation_failure() -> None:
    log_store = LogStore()
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(StubEngineAdapter([])),
        runtime_manager=StubRuntimeManager(),
        log_store=log_store,
    )

    result = await service.validate_workflow("text_to_image_v0")
    logs = service.list_logs()

    assert not result.valid
    assert logs.events[-1].level == "warning"
    assert logs.events[-1].message == "Workflow validation failed"
    assert logs.events[-1].details["missing_models"][0]["filename"] == "v1-5-pruned-emaonly-fp16.safetensors"
