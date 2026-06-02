from __future__ import annotations

import pytest

from app.workflows.import_normalization import (
    ImportNormalizationError,
    detect_unresolved_runtime_inputs,
    filter_resolved_runtime_inputs,
    normalize_custom_nodes,
    normalize_models,
    normalize_unresolved_runtime_inputs,
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
                    "architecture_family": "sdxl",
                    "architecture_family_confidence": "high",
                    "architecture_family_source": "exporter",
                }
            ]
        }
    )

    assert models[0].checksum == "sha256:" + "a" * 64
    assert models[0].identity_verified_by_exporter is True
    assert models[0].architecture_family == "sdxl"
    assert models[0].architecture_family_confidence == "high"
    assert models[0].architecture_family_source == "exporter"


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
    assert unresolved[0].current_value == "__noofy_runtime_image_input_required__"
    assert unresolved[0].expected_kind == "image"


def test_detect_unresolved_runtime_inputs_finds_local_media_and_file_values() -> None:
    unresolved = detect_unresolved_runtime_inputs(
        {
            "1": {"class_type": "LoadAudio", "inputs": {"audio": "/Users/local/song.flac"}},
            "2": {"class_type": "VHS_LoadVideo", "inputs": {"video": "/Users/local/movie.mp4"}},
            "3": {"class_type": "Load3D", "inputs": {"model_file": "/Users/local/scan.glb"}},
            "4": {"class_type": "LoadFile", "inputs": {"file_path": "/Users/local/data.json"}},
            "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0]}},
        }
    )

    assert [(item.node_id, item.expected_kind, item.extension_hint) for item in unresolved] == [
        ("1", "audio", ".flac"),
        ("2", "video", ".mp4"),
        ("3", "3d", ".glb"),
        ("4", "text", ".json"),
    ]
    assert [item.current_value for item in unresolved] == [
        "__noofy_runtime_audio_input_required__",
        "__noofy_runtime_video_input_required__",
        "__noofy_runtime_three_d_input_required__",
        "__noofy_runtime_text_input_required__",
    ]


def test_detect_unresolved_runtime_inputs_ignores_generic_model_loaders() -> None:
    unresolved = detect_unresolved_runtime_inputs(
        {
            "1": {"class_type": "ModelLoader", "inputs": {"model": "/Users/local/checkpoint.safetensors"}},
        }
    )

    assert unresolved == []


def test_normalize_unresolved_runtime_inputs_redacts_package_values() -> None:
    unresolved = normalize_unresolved_runtime_inputs(
        [
            {
                "node_id": "4",
                "node_type": "LoadAudio",
                "input_name": "audio",
                "current_value": "/Users/local/private-song.flac",
                "reason": "creator_local_audio_not_bundled /Users/local/private-song.flac",
                "expected_kind": "audio",
                "required": True,
                "extension_hint": ".flac",
                "mime_type_hint": "audio/flac",
            }
        ]
    )

    assert len(unresolved) == 1
    assert unresolved[0].current_value == "__noofy_runtime_audio_input_required__"
    assert unresolved[0].reason == "creator_local_audio_not_bundled"
    assert unresolved[0].expected_kind == "audio"
    assert unresolved[0].extension_hint == ".flac"
    assert unresolved[0].mime_type_hint == "audio/flac"


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
