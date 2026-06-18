from __future__ import annotations

import pytest

from app.workflows.import_normalization import (
    ImportNormalizationError,
    detect_unresolved_runtime_inputs,
    filter_resolved_runtime_inputs,
    normalize_custom_nodes,
    normalize_models,
    normalize_unresolved_runtime_inputs,
    repair_misclassified_multimodal_text_inputs,
    reject_unsupported_exported_launch_options,
    required_models_from_comfyui_workflow,
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


def test_required_models_from_api_graph_known_model_selector_without_properties() -> None:
    models = required_models_from_comfyui_workflow(
        {},
        comfyui_graph={
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
        },
    )

    assert len(models) == 1
    model = models[0]
    assert model.folder == "background_removal"
    assert model.filename == "birefnet.safetensors"
    assert model.node_id == "88:85:82"
    assert model.node_type == "LoadBackgroundRemovalModel"
    assert model.input_name == "bg_removal_name"
    assert model.model_type == "background_removal"


@pytest.mark.parametrize(
    ("node_type", "input_name", "filename", "folder", "model_type"),
    [
        (
            "IPAdapterModelLoader",
            "ipadapter_file",
            "ip-adapter-plus.safetensors",
            "ipadapter",
            "ipadapter",
        ),
        (
            "ADE_LoadAnimateDiffModel",
            "model_name",
            "mm_sd_v15_v2.ckpt",
            "animatediff_models",
            "animatediff_model",
        ),
        (
            "ADE_AnimateDiffLoRALoader",
            "name",
            "v2_lora.safetensors",
            "animatediff_motion_lora",
            "animatediff_motion_lora",
        ),
        (
            "ACN_ControlNetLoaderAdvanced",
            "cnet",
            "control_v11p_sd15_canny.safetensors",
            "controlnet",
            "controlnet",
        ),
        (
            "ACN_DiffControlNetLoaderAdvanced",
            "cnet",
            "diff_controlnet.safetensors",
            "controlnet",
            "controlnet",
        ),
        (
            "ACN_ControlNet++LoaderSingle",
            "name",
            "controlnet_plus.safetensors",
            "controlnet",
            "controlnet",
        ),
        (
            "ACN_ControlNet++LoaderAdvanced",
            "name",
            "controlnet_plus_advanced.safetensors",
            "controlnet",
            "controlnet",
        ),
        ("SAMLoader", "model_name", "sam_vit_b_01ec64.pth", "sams", "sam"),
        (
            "ONNXDetectorProvider",
            "model_name",
            "face_detector.onnx",
            "onnx",
            "onnx_detector",
        ),
        ("CLIPLoaderGGUF", "clip_name", "clip_l.gguf", "clip", "clip"),
        (
            "UnetLoaderGGUF",
            "unet_name",
            "flux1-dev-Q4_K_S.gguf",
            "diffusion_models",
            "diffusion_model",
        ),
        (
            "UnetLoaderGGUFAdvanced",
            "unet_name",
            "wan2.2-t2v-Q5_K_M.gguf",
            "diffusion_models",
            "diffusion_model",
        ),
    ],
)
def test_required_models_from_api_graph_custom_node_model_selectors(
    node_type: str,
    input_name: str,
    filename: str,
    folder: str,
    model_type: str,
) -> None:
    models = required_models_from_comfyui_workflow(
        {},
        comfyui_graph={
            "10": {
                "class_type": node_type,
                "inputs": {input_name: filename},
            }
        },
    )

    assert len(models) == 1
    model = models[0]
    assert model.folder == folder
    assert model.filename == filename
    assert model.node_id == "10"
    assert model.node_type == node_type
    assert model.input_name == input_name
    assert model.model_type == model_type


@pytest.mark.parametrize(
    ("node_type", "inputs", "expected"),
    [
        (
            "DualCLIPLoaderGGUF",
            {"clip_name1": "clip_l.gguf", "clip_name2": "t5xxl_fp16.gguf"},
            [
                ("clip_name1", "clip_l.gguf"),
                ("clip_name2", "t5xxl_fp16.gguf"),
            ],
        ),
        (
            "TripleCLIPLoaderGGUF",
            {
                "clip_name1": "clip_l.gguf",
                "clip_name2": "clip_g.gguf",
                "clip_name3": "t5xxl_fp16.gguf",
            },
            [
                ("clip_name1", "clip_l.gguf"),
                ("clip_name2", "clip_g.gguf"),
                ("clip_name3", "t5xxl_fp16.gguf"),
            ],
        ),
        (
            "QuadrupleCLIPLoaderGGUF",
            {
                "clip_name1": "clip_l.gguf",
                "clip_name2": "clip_g.gguf",
                "clip_name3": "t5xxl_fp16.gguf",
                "clip_name4": "umt5_xxl.gguf",
            },
            [
                ("clip_name1", "clip_l.gguf"),
                ("clip_name2", "clip_g.gguf"),
                ("clip_name3", "t5xxl_fp16.gguf"),
                ("clip_name4", "umt5_xxl.gguf"),
            ],
        ),
    ],
)
def test_required_models_from_api_graph_gguf_multi_clip_loaders(
    node_type: str,
    inputs: dict[str, str],
    expected: list[tuple[str, str]],
) -> None:
    models = required_models_from_comfyui_workflow(
        {},
        comfyui_graph={"10": {"class_type": node_type, "inputs": inputs}},
    )

    assert [(model.input_name, model.filename) for model in models] == expected
    assert {model.folder for model in models} == {"clip"}
    assert {model.model_type for model in models} == {"clip"}


@pytest.mark.parametrize(
    "graph",
    [
        {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "clip_l.gguf"}}},
        {
            "1": {
                "class_type": "UnrelatedNode",
                "inputs": {
                    "label": "ip-adapter-plus.safetensors",
                    "title": "mm_sd_v15_v2.ckpt",
                    "name": "sam_vit_b_01ec64.pth",
                },
            }
        },
        {
            "1": {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": "https://example.test/model.safetensors"},
            }
        },
        {
            "1": {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": "nested/model.safetensors"},
            }
        },
        {
            "1": {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": "/abs/model.safetensors"},
            }
        },
        {
            "1": {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": "model.txt"},
            }
        },
    ],
)
def test_required_models_from_api_graph_rejects_false_positives(
    graph: dict[str, dict[str, object]],
) -> None:
    assert required_models_from_comfyui_workflow({}, comfyui_graph=graph) == []


@pytest.mark.parametrize(
    ("value", "folder", "filename"),
    [
        ("bbox/person_yolov8m.pt", "ultralytics_bbox", "person_yolov8m.pt"),
        ("segm/person_yolov8m-seg.pt", "ultralytics_segm", "person_yolov8m-seg.pt"),
    ],
)
def test_required_models_from_api_graph_ultralytics_detector_paths(
    value: str,
    folder: str,
    filename: str,
) -> None:
    models = required_models_from_comfyui_workflow(
        {},
        comfyui_graph={
            "10": {
                "class_type": "UltralyticsDetectorProvider",
                "inputs": {"model_name": value},
            }
        },
    )

    assert len(models) == 1
    model = models[0]
    assert model.folder == folder
    assert model.filename == filename
    assert model.node_id == "10"
    assert model.node_type == "UltralyticsDetectorProvider"
    assert model.input_name == "model_name"
    assert model.model_type == folder


@pytest.mark.parametrize(
    "value",
    [
        "foo/bar/baz.pt",
        "../bad.pt",
        "/abs/model.pt",
        "https://example.test/model.pt",
    ],
)
def test_required_models_from_api_graph_ultralytics_rejects_unsafe_paths(
    value: str,
) -> None:
    models = required_models_from_comfyui_workflow(
        {},
        comfyui_graph={
            "10": {
                "class_type": "UltralyticsDetectorProvider",
                "inputs": {"model_name": value},
            }
        },
    )

    assert models == []


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


@pytest.mark.parametrize(
    ("node_type", "input_name"),
    [
        ("TextEncodeQwenImageEdit", "image"),
        ("TextEncodeWithAudio", "audio"),
        ("TextEncodeWithVideo", "video"),
        ("TextEncode3D", "model_file"),
    ],
)
def test_detect_unresolved_runtime_inputs_ignores_multimodal_encoder_media_sockets(
    node_type: str,
    input_name: str,
) -> None:
    unresolved = detect_unresolved_runtime_inputs(
        {
            "22:4": {
                "class_type": node_type,
                "inputs": {
                    input_name: "__noofy_runtime_text_input_required__",
                    "prompt": "turn the dog red",
                },
            }
        }
    )

    assert unresolved == []


@pytest.mark.parametrize("source_node_id", ["22:14", "22.14"])
def test_detect_unresolved_runtime_inputs_ignores_subgraph_links(
    source_node_id: str,
) -> None:
    unresolved = detect_unresolved_runtime_inputs(
        {
            "22:4": {
                "class_type": "TextEncodeQwenImageEdit",
                "inputs": {
                    "image": [source_node_id, 0],
                    "prompt": "turn the dog red",
                },
            }
        }
    )

    assert unresolved == []


def test_repair_misclassified_multimodal_text_inputs_removes_legacy_sentinel() -> None:
    graph = {
        "22:4": {
            "class_type": "TextEncodeQwenImageEdit",
            "inputs": {
                "image": "__noofy_runtime_text_input_required__",
                "prompt": "turn the dog red",
            },
        }
    }
    declared = normalize_unresolved_runtime_inputs(
        [
            {
                "node_id": "22:4",
                "node_type": "TextEncodeQwenImageEdit",
                "input_name": "image",
                "expected_kind": "text",
                "required": True,
            }
        ]
    )

    repaired_graph, repaired_inputs = repair_misclassified_multimodal_text_inputs(
        graph,
        declared,
    )

    assert repaired_graph["22:4"]["inputs"] == {"prompt": "turn the dog red"}
    assert repaired_inputs == []
    assert "image" in graph["22:4"]["inputs"]


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
