from __future__ import annotations

import pytest

from app.workflows.import_normalization import (
    ImportNormalizationError,
    detect_unresolved_runtime_inputs,
    filter_resolved_runtime_inputs,
    normalize_custom_nodes,
    normalize_models,
    reject_unsupported_exported_launch_options,
)
from app.workflows.package import WorkflowInput


def test_normalize_models_preserves_identity_evidence() -> None:
    models = normalize_models(
        {
            "models": [
                {
                    "comfyui_folder": "checkpoints",
                    "filename": "demo.safetensors",
                    "source_urls": ["https://example.test/demo.safetensors"],
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                }
            ]
        }
    )

    assert models[0].checksum == "sha256:" + "a" * 64
    assert models[0].identity_verified_by_exporter is True


def test_normalize_models_accepts_single_source_url_string() -> None:
    url = "https://example.test/demo.safetensors"

    models = normalize_models(
        {
            "models": [
                {
                    "comfyui_folder": "checkpoints",
                    "filename": "demo.safetensors",
                    "source_urls": url,
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                }
            ]
        }
    )

    assert models[0].source_urls == [url]
    assert models[0].source_url == url


def test_detect_unresolved_runtime_inputs_finds_local_load_image_values() -> None:
    unresolved = detect_unresolved_runtime_inputs(
        {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": "/Users/local/image.png"},
            },
            "2": {"class_type": "KSampler", "inputs": {}},
        }
    )

    assert len(unresolved) == 1
    assert unresolved[0].reason == "creator_local_image_not_bundled"


def test_normalize_custom_nodes_merges_capsule_and_package_metadata() -> None:
    custom_nodes = normalize_custom_nodes(
        {
            "custom_nodes": [
                {
                    "package_id": "comfyui-kjnodes",
                    "source": "bundled_from_creator_machine",
                    "node_types": ["ColorMatchV2"],
                }
            ]
        },
        {
            "custom_nodes": [
                {
                    "id": "comfyui-kjnodes",
                    "folder_name": "ComfyUI-KJNodes",
                    "included": True,
                    "requirements_files": ["requirements.txt"],
                    "has_install_py": False,
                }
            ]
        },
    )

    assert custom_nodes[0].id == "comfyui-kjnodes"
    assert custom_nodes[0].folder_name == "ComfyUI-KJNodes"
    assert custom_nodes[0].included is True
    assert custom_nodes[0].node_types == ["ColorMatchV2"]
    assert custom_nodes[0].requirements_files == ["requirements.txt"]


def test_filter_resolved_runtime_inputs_removes_dashboard_bound_load_image() -> None:
    unresolved = detect_unresolved_runtime_inputs(
        {
            "192": {
                "class_type": "LoadImage",
                "inputs": {"image": "__noofy_runtime_image_input_required__"},
            },
            "193": {
                "class_type": "LoadImage",
                "inputs": {"image": "__noofy_runtime_image_input_required__"},
            },
        }
    )
    workflow_inputs = [
        WorkflowInput.model_validate(
            {
                "id": "ctrl-node-192-image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "192", "input_name": "image"},
            }
        )
    ]

    remaining = filter_resolved_runtime_inputs(unresolved, workflow_inputs)

    assert [runtime_input.node_id for runtime_input in remaining] == ["193"]


def test_reject_unsupported_exported_launch_options_reports_nested_keys() -> None:
    with pytest.raises(ImportNormalizationError, match="runtime.launch_options"):
        reject_unsupported_exported_launch_options(
            {"runtime": {"launch_options": {"listen": "0.0.0.0"}}}
        )
