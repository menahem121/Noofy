from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from app.engine.memory_observation import memory_input_profile_fingerprint
from app.runtime.memory.input_features import (
    build_memory_signature_set,
    extract_model_selection_features,
)


def _input(
    input_id: str,
    *,
    node_id: str,
    input_name: str,
    default=None,
    control: str = "select",
):
    return SimpleNamespace(
        id=input_id,
        control=control,
        binding=SimpleNamespace(node_id=node_id, input_name=input_name),
        default=default,
    )


def _package():
    return SimpleNamespace(
        comfyui_graph={
            "1": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base-default.safetensors"},
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"refiner_ckpt_name": "refiner.safetensors"},
            },
            "3": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": "vae-default.safetensors"},
            },
            "4": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": "clip-l.safetensors",
                    "clip_name2": "t5xxl.safetensors",
                },
            },
            "5": {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": "pose-controlnet.safetensors"},
            },
            "6": {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": "ip-adapter-plus.safetensors"},
            },
            "7": {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": "style-default.safetensors",
                    "strength_model": 1.0,
                    "strength_clip": 0.4,
                },
            },
            "8": {
                "class_type": "LoraLoaderModelOnly",
                "inputs": {"lora_name": "None", "strength_model": 1.0},
            },
            "9": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "default prompt", "clip": ["4", 0]},
            },
            "10": {
                "class_type": "KSampler",
                "inputs": {"seed": 1, "model": ["1", 0]},
            },
            "11": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": "diffusion-model.safetensors"},
            },
        },
        inputs=[
            _input(
                "vae",
                node_id="3",
                input_name="vae_name",
                default="vae-default.safetensors",
            )
        ],
        dashboard=SimpleNamespace(
            inputs=[
                _input("base_model", node_id="1", input_name="ckpt_name"),
                _input(
                    "style_lora",
                    node_id="7",
                    input_name="lora_name",
                    default="style-default.safetensors",
                    control="lora_loader",
                ),
                _input(
                    "style_strength",
                    node_id="7",
                    input_name="strength_model",
                    default=1.0,
                    control="slider",
                ),
                _input(
                    "prompt",
                    node_id="9",
                    input_name="text",
                    default="default prompt",
                    control="textarea",
                ),
                _input(
                    "seed",
                    node_id="10",
                    input_name="seed",
                    default=1,
                    control="seed_widget",
                ),
            ],
            sections=[],
        ),
        required_models=[],
    )


def test_extract_model_selection_features_uses_workflow_dashboard_and_graph_bindings() -> None:
    features = extract_model_selection_features(
        _package(),
        {
            "base_model": "models/base-selected.safetensors",
            "vae": "vae-selected.safetensors",
            "style_lora": "loras/detail-style.safetensors",
            "style_strength": 0.75,
            "prompt": "a different prompt",
            "seed": 999,
        },
    )
    details = features.diagnostic_details()
    models = {
        (selection["node_id"], selection["input_name"]): selection
        for selection in details["selected_models"]
    }

    assert features.selected_model_count == 8
    assert features.selected_model_kinds == [
        "checkpoint",
        "controlnet",
        "encoder",
        "ipadapter",
        "model",
        "refiner",
        "vae",
    ]
    assert models[("1", "ckpt_name")]["selection"] == "base-selected.safetensors"
    assert models[("1", "ckpt_name")]["source"] == "input:base_model"
    assert models[("1", "ckpt_name")]["binding_source"] == "dashboard.inputs"
    assert models[("3", "vae_name")]["selection"] == "vae-selected.safetensors"
    assert models[("3", "vae_name")]["binding_source"] == "workflow.inputs"
    assert features.lora_count == 1
    assert features.lora_strength_total == 0.75
    assert details["selected_loras"] == [
        {
            "kind": "lora",
            "selection": "detail-style.safetensors",
            "node_id": "7",
            "node_type": "LoraLoader",
            "input_name": "lora_name",
            "source": "input:style_lora",
            "binding_source": "dashboard.inputs",
            "active": True,
            "effective_strength": 0.75,
            "strength_model": 0.75,
            "strength_clip": 0.4,
        }
    ]


def test_memory_profile_fingerprint_tracks_static_model_and_lora_graph_changes() -> None:
    package = _package()
    base = memory_input_profile_fingerprint(
        {"prompt": "a lake", "seed": 1},
        {},
        package=package,
    )

    neutral_graph = deepcopy(package.comfyui_graph)
    neutral_graph["9"]["inputs"]["text"] = "a forest"
    neutral_graph["10"]["inputs"]["seed"] = 999
    neutral = SimpleNamespace(**{**vars(package), "comfyui_graph": neutral_graph})

    model_graph = deepcopy(package.comfyui_graph)
    model_graph["6"]["inputs"]["ipadapter_file"] = "ip-adapter-faceid.safetensors"
    model_changed = SimpleNamespace(**{**vars(package), "comfyui_graph": model_graph})

    lora_graph = deepcopy(package.comfyui_graph)
    lora_graph["7"]["inputs"]["strength_clip"] = 0.9
    lora_changed = SimpleNamespace(**{**vars(package), "comfyui_graph": lora_graph})

    assert (
        memory_input_profile_fingerprint(
            {"prompt": "a forest", "seed": 999},
            {},
            package=neutral,
        )
        == base
    )
    assert memory_input_profile_fingerprint({}, {}, package=model_changed) != base
    assert memory_input_profile_fingerprint({}, {}, package=lora_changed) != base


def test_graph_model_selection_suppresses_stale_required_model_fallback() -> None:
    package = _package()
    graph = deepcopy(package.comfyui_graph)
    graph["1"]["inputs"]["ckpt_name"] = "selected-alternate.safetensors"
    with_required_model = SimpleNamespace(
        **{
            **vars(package),
            "comfyui_graph": graph,
            "required_models": [
                SimpleNamespace(
                    folder="checkpoints",
                    filename="base-default.safetensors",
                    node_id="1",
                    node_type="CheckpointLoaderSimple",
                    input_name="ckpt_name",
                    model_type="checkpoint",
                )
            ],
        }
    )

    features = extract_model_selection_features(with_required_model, {})
    checkpoints = [
        selection.selection
        for selection in features.selected_models
        if selection.kind == "checkpoint"
    ]

    assert checkpoints == ["selected-alternate.safetensors"]


def test_selected_model_count_deduplicates_repeated_loader_references() -> None:
    package = _package()
    graph = deepcopy(package.comfyui_graph)
    graph["12"] = {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "base-default.safetensors"},
    }
    repeated = SimpleNamespace(**{**vars(package), "comfyui_graph": graph})

    features = extract_model_selection_features(repeated, {})
    details = features.diagnostic_details()

    assert features.selected_model_count == 8
    assert details["selected_model_reference_count"] == 9


def test_memory_signatures_keep_prompt_and_seed_neutral() -> None:
    package = _package()
    base_features = extract_model_selection_features(
        package,
        {"prompt": "a lake", "seed": 1},
    )
    changed_features = extract_model_selection_features(
        package,
        {"prompt": "a forest", "seed": 999},
    )
    base = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=base_features,
        execution_profile={
            "resolution_width": 512,
            "resolution_height": 512,
            "effective_batch_size": 1,
            "workflow_type": "txt2img",
        },
    )
    changed = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=changed_features,
        execution_profile={
            "resolution_width": 512,
            "resolution_height": 512,
            "effective_batch_size": 1,
            "workflow_type": "txt2img",
        },
    )

    assert changed.signature_fields() == base.signature_fields()


def test_memory_signatures_separate_model_residency_from_process_compatibility() -> None:
    package = _package()
    base = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=extract_model_selection_features(package, {}),
        execution_profile={"resolution_width": 512, "resolution_height": 512},
    )
    changed = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=extract_model_selection_features(
            package,
            {"style_lora": "different-style.safetensors"},
        ),
        execution_profile={"resolution_width": 512, "resolution_height": 512},
    )

    assert changed.process_compatibility_signature == base.process_compatibility_signature
    assert changed.execution_profile_signature == base.execution_profile_signature
    assert changed.model_residency_signature != base.model_residency_signature


def test_memory_signatures_separate_execution_profile_from_model_residency() -> None:
    model_features = extract_model_selection_features(_package(), {})
    base = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=model_features,
        execution_profile={
            "resolution_width": 512,
            "resolution_height": 512,
            "effective_batch_size": 1,
            "workflow_type": "txt2img",
        },
    )
    large = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=model_features,
        execution_profile={
            "resolution_width": 1024,
            "resolution_height": 1024,
            "effective_batch_size": 1,
            "workflow_type": "txt2img",
        },
    )

    assert large.process_compatibility_signature == base.process_compatibility_signature
    assert large.model_residency_signature == base.model_residency_signature
    assert large.execution_profile_signature != base.execution_profile_signature


def test_model_residency_signature_matches_across_different_workflow_graphs() -> None:
    left = _package()
    right = SimpleNamespace(
        comfyui_graph={
            "checkpoint_node": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "base-default.safetensors"},
            },
            "vae_node": {
                "class_type": "VAELoader",
                "inputs": {"vae_name": "vae-default.safetensors"},
            },
            "encoder_node": {
                "class_type": "DualCLIPLoader",
                "inputs": {
                    "clip_name1": "clip-l.safetensors",
                    "clip_name2": "t5xxl.safetensors",
                },
            },
            "control": {
                "class_type": "ControlNetLoader",
                "inputs": {"control_net_name": "pose-controlnet.safetensors"},
            },
            "adapter": {
                "class_type": "IPAdapterModelLoader",
                "inputs": {"ipadapter_file": "ip-adapter-plus.safetensors"},
            },
            "lora": {
                "class_type": "LoraLoader",
                "inputs": {
                    "lora_name": "style-default.safetensors",
                    "strength_model": 1.0,
                    "strength_clip": 0.4,
                },
            },
            "unet": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": "diffusion-model.safetensors"},
            },
            "refiner": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"refiner_ckpt_name": "refiner.safetensors"},
            },
        },
        inputs=[],
        dashboard=SimpleNamespace(inputs=[], sections=[]),
        required_models=[],
    )

    left_signature = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=extract_model_selection_features(left, {}),
        execution_profile={},
    )
    right_signature = build_memory_signature_set(
        runner_process_compatibility_key="compat-a",
        model_selections=extract_model_selection_features(right, {}),
        execution_profile={},
    )

    assert right_signature.model_residency_signature == left_signature.model_residency_signature
