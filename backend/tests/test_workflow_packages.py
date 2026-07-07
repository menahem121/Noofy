import hashlib
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


def test_loader_repairs_missing_model_source_url_from_source_workflow(tmp_path: Path) -> None:
    source_url = (
        "https://huggingface.co/example/repo/resolve/main/"
        "checkpoints/demo.safetensors"
    )
    package_dir = tmp_path / "packages" / "missing_source_url"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "missing_source_url",
                    "name": "Missing Source URL",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [
                    {
                        "folder": "checkpoints",
                        "filename": "demo.safetensors",
                        "checksum": "sha256:" + ("a" * 64),
                        "size_bytes": 123,
                    }
                ],
                "comfyui_graph": {},
            }
        ),
        encoding="utf-8",
    )
    source_files = package_dir / "source-files"
    source_files.mkdir()
    (source_files / "comfyui_workflow.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": 1,
                        "type": "CheckpointLoaderSimple",
                        "properties": {
                            "models": [
                                {
                                    "directory": "checkpoints",
                                    "name": "demo.safetensors",
                                    "url": source_url,
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package("missing_source_url")

    model = package.required_models[0]
    assert model.source_url == source_url
    assert model.source_urls == [source_url]


def test_loader_adds_missing_required_model_from_source_workflow(tmp_path: Path) -> None:
    source_url = (
        "https://huggingface.co/example/repo/resolve/main/"
        "background_removal/birefnet.safetensors"
    )
    package_dir = tmp_path / "packages" / "missing_background_model"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "missing_background_model",
                    "name": "Missing Background Model",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {},
            }
        ),
        encoding="utf-8",
    )
    source_files = package_dir / "source-files"
    source_files.mkdir()
    (source_files / "comfyui_workflow.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": 82,
                        "type": "LoadBackgroundRemovalModel",
                        "properties": {
                            "models": [
                                {
                                    "directory": "background_removal",
                                    "name": "birefnet.safetensors",
                                    "url": source_url,
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (source_files / "comfyui_graph.json").write_text(
        json.dumps(
            {
                "88:85:82": {
                    "class_type": "LoadBackgroundRemovalModel",
                    "inputs": {"bg_removal_name": "birefnet.safetensors"},
                }
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package(
        "missing_background_model"
    )

    model = package.required_models[0]
    assert model.folder == "background_removal"
    assert model.filename == "birefnet.safetensors"
    assert model.node_id == "88:85:82"
    assert model.input_name == "bg_removal_name"
    assert model.source_urls == [source_url]


def test_loader_adds_missing_required_model_from_source_graph_selector(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "packages" / "missing_background_model_graph_only"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "missing_background_model_graph_only",
                    "name": "Missing Background Model Graph Only",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {},
            }
        ),
        encoding="utf-8",
    )
    source_files = package_dir / "source-files"
    source_files.mkdir()
    (source_files / "comfyui_graph.json").write_text(
        json.dumps(
            {
                "88:85:82": {
                    "class_type": "LoadBackgroundRemovalModel",
                    "inputs": {
                        "bg_removal_name": "birefnet.safetensors",
                        "label": "birefnet.safetensors",
                    },
                },
                "90": {
                    "class_type": "CLIPTextEncode",
                    "inputs": {"text": "birefnet.safetensors"},
                },
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package(
        "missing_background_model_graph_only"
    )

    model = package.required_models[0]
    assert model.folder == "background_removal"
    assert model.filename == "birefnet.safetensors"
    assert model.node_id == "88:85:82"
    assert model.node_type == "LoadBackgroundRemovalModel"
    assert model.input_name == "bg_removal_name"
    assert model.source_urls == []


def test_loader_prunes_stale_required_model_from_adjacent_workflow(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "packages" / "stale_property_model"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "stale_property_model",
                    "name": "Stale Property Model",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [
                    {
                        "folder": "diffusion_models",
                        "filename": "active.safetensors",
                        "checksum": "sha256:" + ("a" * 64),
                        "size_bytes": 123,
                    },
                    {
                        "folder": "diffusion_models",
                        "filename": "stale.safetensors",
                        "checksum": "sha256:" + ("b" * 64),
                        "size_bytes": 456,
                        "source_url": "https://example.test/stale.safetensors",
                    },
                ],
                "comfyui_graph": {},
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "comfyui_workflow.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": 70,
                        "type": "UNETLoader",
                        "widgets_values": ["active.safetensors", "default"],
                        "properties": {
                            "models": [
                                {
                                    "directory": "diffusion_models",
                                    "name": "stale.safetensors",
                                    "url": "https://example.test/stale.safetensors",
                                }
                            ]
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "comfyui_graph.json").write_text(
        json.dumps(
            {
                "75:70": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": "active.safetensors"},
                }
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package(
        "stale_property_model"
    )

    assert [(model.folder, model.filename) for model in package.required_models] == [
        ("diffusion_models", "active.safetensors")
    ]


def test_loader_preserves_model_stale_on_one_node_but_active_on_another(
    tmp_path: Path,
) -> None:
    package_dir = tmp_path / "packages" / "shared_active_model"
    package_dir.mkdir(parents=True)
    shared_checksum = "sha256:" + ("b" * 64)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "shared_active_model",
                    "name": "Shared Active Model",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [
                    {
                        "folder": "diffusion_models",
                        "filename": "current.safetensors",
                        "checksum": "sha256:" + ("a" * 64),
                        "size_bytes": 123,
                    },
                    {
                        "folder": "diffusion_models",
                        "filename": "shared.safetensors",
                        "checksum": shared_checksum,
                        "size_bytes": 456,
                    },
                ],
                "comfyui_graph": {},
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "comfyui_workflow.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": 70,
                        "type": "UNETLoader",
                        "widgets_values": ["current.safetensors", "default"],
                        "properties": {
                            "models": [
                                {
                                    "directory": "diffusion_models",
                                    "name": "shared.safetensors",
                                    "url": "https://example.test/shared.safetensors",
                                }
                            ]
                        },
                    },
                    {
                        "id": 71,
                        "type": "UNETLoader",
                        "widgets_values": ["shared.safetensors", "default"],
                        "properties": {
                            "models": [
                                {
                                    "directory": "diffusion_models",
                                    "name": "shared.safetensors",
                                    "url": "https://example.test/shared.safetensors",
                                }
                            ]
                        },
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "comfyui_graph.json").write_text(
        json.dumps(
            {
                "75:70": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": "current.safetensors"},
                },
                "75:71": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": "shared.safetensors"},
                },
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package(
        "shared_active_model"
    )

    by_filename = {model.filename: model for model in package.required_models}
    assert set(by_filename) == {"current.safetensors", "shared.safetensors"}
    assert by_filename["shared.safetensors"].checksum == shared_checksum
    assert by_filename["shared.safetensors"].size_bytes == 456


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


def test_loader_repairs_legacy_multimodal_text_input_requirement(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "multimodal_workflow"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": "multimodal_workflow",
                    "name": "Multimodal Workflow",
                    "version": "0.1.0",
                },
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {
                    "22:4": {
                        "class_type": "TextEncodeQwenImageEdit",
                        "inputs": {
                            "image": "__noofy_runtime_text_input_required__",
                            "prompt": "turn the dog red",
                        },
                    }
                },
                "unresolved_runtime_inputs": [
                    {
                        "node_id": "22:4",
                        "node_type": "TextEncodeQwenImageEdit",
                        "input_name": "image",
                        "current_value": "__noofy_runtime_text_input_required__",
                        "reason": "creator_local_text_not_bundled",
                        "expected_kind": "text",
                        "required": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package(
        "multimodal_workflow"
    )

    assert package.comfyui_graph["22:4"]["inputs"] == {
        "prompt": "turn the dog red"
    }
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
            locked_sha256 = locked["sha256"]
            expected_checksum = locked_sha256 if locked_sha256.startswith("sha256:") else f"sha256:{locked_sha256}"
            assert package_model.get("checksum") == expected_checksum
            assert package_model.get("size_bytes") == locked.get("size_bytes")
            assert package_model.get("verification_level") == ModelVerificationLevel.SHA256_SIZE.value


def test_bundled_custom_node_sources_are_materializable_or_cacheable() -> None:
    packages_dir = Path("app/workflows/packages")
    for capsule_file in packages_dir.glob("*/capsule.lock.json"):
        workflow_dir = capsule_file.parent
        package_file = workflow_dir / "package.json"
        package_data = json.loads(package_file.read_text(encoding="utf-8"))
        capsule_data = json.loads(capsule_file.read_text(encoding="utf-8"))
        package_nodes = {
            node.get("id"): node
            for node in package_data.get("custom_nodes", [])
            if isinstance(node, dict)
        }

        for package_node in package_nodes.values():
            assert "source_cache_ref" not in package_node, (
                f"{package_file} must not persist runtime-local custom-node cache refs"
            )

        for capsule_node in capsule_data.get("custom_nodes", []):
            assert "source_cache_ref" not in capsule_node, (
                f"{capsule_file} must not persist runtime-local custom-node cache refs"
            )
            package_id = capsule_node.get("package_id")
            package_node = package_nodes.get(package_id) or {}
            source = capsule_node.get("source")
            if isinstance(source, str) and source.startswith("https://"):
                assert capsule_node.get("source_ref"), (
                    f"{capsule_file} has HTTPS custom-node source without source_ref"
                )
                content_hash = capsule_node.get("source_content_hash")
                assert isinstance(content_hash, str) and content_hash.startswith("sha256:"), (
                    f"{capsule_file} has HTTPS custom-node source without source_content_hash"
                )
                if source.startswith("https://codeload.github.com/"):
                    assert capsule_node.get("source_archive_subdir"), (
                        f"{capsule_file} has GitHub archive source without source_archive_subdir"
                    )
                continue

            assert _bundled_custom_node_source_exists(
                workflow_dir,
                {**package_node, **capsule_node},
            ), f"{capsule_file} is missing bundled custom-node source for {package_id}"


def test_bundled_custom_node_file_manifests_are_complete() -> None:
    packages_dir = Path("app/workflows/packages")
    for manifest_file in packages_dir.glob("*/custom_nodes/*/.noofy-file-manifest.json"):
        custom_node_dir = manifest_file.parent
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        for entry in manifest.get("files", []):
            relative_path = entry["path"]
            bundled_file = custom_node_dir / relative_path
            assert bundled_file.is_file(), (
                f"{manifest_file} lists missing bundled custom-node file {relative_path}"
            )
            content = bundled_file.read_bytes()
            assert len(content) == entry["size_bytes"], (
                f"{manifest_file} has size drift for bundled custom-node file {relative_path}"
            )
            assert hashlib.sha256(content).hexdigest() == entry["sha256"], (
                f"{manifest_file} has checksum drift for bundled custom-node file {relative_path}"
            )


def test_bundled_upscale_workflows_declare_model_source_urls() -> None:
    packages_dir = Path("app/workflows/packages")
    expected = {
        "unknown__upscalex2__0.1.0": {
            "filename": "2xNomosUni_span_multijpg_ldl.safetensors",
            "url": "https://huggingface.co/Phips/2xNomosUni_span_multijpg_ldl/resolve/main/2xNomosUni_span_multijpg_ldl.safetensors",
        },
        "unknown__upscalex4__0.1.0": {
            "filename": "4xNomos8k_atd_jpg.safetensors",
            "url": "https://huggingface.co/dong625/StableDiffusionModel/resolve/main/4xNomos8k_atd_jpg.safetensors",
        },
    }

    for workflow_id, model in expected.items():
        workflow_dir = packages_dir / workflow_id
        package_data = json.loads((workflow_dir / "package.json").read_text(encoding="utf-8"))
        capsule_data = json.loads((workflow_dir / "capsule.lock.json").read_text(encoding="utf-8"))
        package_model = package_data["required_models"][0]
        capsule_model = capsule_data["models"][0]

        assert package_model["folder"] == "upscale_models"
        assert package_model["filename"] == model["filename"]
        assert package_model["source_url"] == model["url"]
        assert package_model["source_urls"] == [model["url"]]
        assert capsule_model["comfyui_folder"] == "upscale_models"
        assert capsule_model["filename"] == model["filename"]
        assert capsule_model["source_urls"] == [model["url"]]
        package_node = package_data["custom_nodes"][0]
        capsule_node = capsule_data["custom_nodes"][0]
        assert package_node["source"].startswith("https://codeload.github.com/")
        assert package_node["source_ref"] == "7f43f2ce910a27971bdbbf3fb5a52081457f32e2"
        assert package_node["source_content_hash"].startswith("sha256:")
        assert package_node["source_archive_subdir"]
        assert package_node["node_types"] == ["ColorMatchV2"]
        assert "source_cache_ref" not in package_node
        assert capsule_node["source"] == package_node["source"]
        assert capsule_node["source_ref"] == package_node["source_ref"]
        assert capsule_node["source_content_hash"] == package_node["source_content_hash"]
        assert capsule_node["source_archive_subdir"] == package_node["source_archive_subdir"]
        assert "ColorMatchV2" in {
            node.get("class_type")
            for node in json.loads((workflow_dir / "comfyui_graph.json").read_text(encoding="utf-8")).values()
        }


def _bundled_custom_node_source_exists(workflow_dir: Path, node: dict[str, object]) -> bool:
    source = node.get("source")
    candidates: list[str] = []
    if isinstance(source, str) and source.startswith("bundled_archive:"):
        candidates.append(source.split(":", 1)[1])
    for key in ("folder_name", "id", "package_id"):
        value = node.get(key)
        if isinstance(value, str) and value:
            candidates.append(value)
    for root in (
        workflow_dir / "source-files" / "custom_nodes",
        workflow_dir / "custom_nodes",
    ):
        for candidate in candidates:
            if (root / candidate).is_dir():
                return True
    return False


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
