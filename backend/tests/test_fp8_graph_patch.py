from copy import deepcopy

from app.workflows.bindings import apply_input_bindings
from app.workflows.model_overrides import (
    WorkflowModelOverride,
    apply_model_overrides_to_graph,
)
from app.workflows.package import WorkflowPackage

FP8_NAME = "model-fp8.safetensors"
CONVERTED_NAME = "model-fp8-converted-for-mac.safetensors"


def _override(**updates):
    values = {
        "folder": "diffusion_models",
        "source_filename": FP8_NAME,
        "replacement_filename": CONVERTED_NAME,
        "origin": "converted",
    }
    values.update(updates)
    return WorkflowModelOverride(**values)


def _graph():
    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": FP8_NAME, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "checkpoint.safetensors"},
        },
        "3": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": "encoder.safetensors", "type": "flux2"},
        },
        "4": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": FP8_NAME},
        },
        "5": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {"noise": ["7", 0]},
        },
    }


def _package(required_models, graph):
    return WorkflowPackage(
        metadata={"id": "wf-1", "name": "Test", "version": "0.1.0"},
        engine="comfyui",
        required_models=required_models,
        comfyui_graph=graph,
    )


def test_patches_known_loader_input_by_folder_mapping():
    graph = _graph()
    package = _package(
        [{"folder": "diffusion_models", "filename": FP8_NAME}],
        graph,
    )
    report = apply_model_overrides_to_graph(
        graph, package, [_override()], force_default_weight_dtype=False
    )
    assert graph["1"]["inputs"]["unet_name"] == CONVERTED_NAME
    # Non-selector string values with the same content stay untouched.
    assert graph["4"]["inputs"]["filename_prefix"] == FP8_NAME
    assert [entry["node_id"] for entry in report.replaced] == ["1"]


def test_patches_via_required_model_binding_for_unknown_nodes():
    graph = {
        "9": {
            "class_type": "MyExoticNode",
            "inputs": {"some_input": FP8_NAME},
        },
    }
    package = _package(
        [
            {
                "folder": "diffusion_models",
                "filename": FP8_NAME,
                "node_id": "9",
                "input_name": "some_input",
            }
        ],
        graph,
    )
    apply_model_overrides_to_graph(
        graph, package, [_override()], force_default_weight_dtype=False
    )
    assert graph["9"]["inputs"]["some_input"] == CONVERTED_NAME


def test_unknown_loader_falls_back_only_when_unambiguous():
    graph = {
        "9": {
            "class_type": "MyCustomLoader",
            "inputs": {"model_name": FP8_NAME},
        },
    }
    package = _package([{"folder": "diffusion_models", "filename": FP8_NAME}], graph)
    apply_model_overrides_to_graph(
        graph, package, [_override()], force_default_weight_dtype=False
    )
    assert graph["9"]["inputs"]["model_name"] == CONVERTED_NAME

    # Two overrides sharing the filename across folders are ambiguous for an
    # unknown loader input; nothing is patched.
    ambiguous_graph = {
        "9": {"class_type": "MyCustomLoader", "inputs": {"model_name": FP8_NAME}},
    }
    report = apply_model_overrides_to_graph(
        ambiguous_graph,
        _package([{"folder": "diffusion_models", "filename": FP8_NAME}], ambiguous_graph),
        [_override(), _override(folder="text_encoders", replacement_filename="te.safetensors")],
        force_default_weight_dtype=False,
    )
    assert ambiguous_graph["9"]["inputs"]["model_name"] == FP8_NAME
    assert report.replaced == []


def test_known_loader_never_borrows_override_from_other_folder():
    # A text-encoder input whose value happens to equal a diffusion-model
    # override's filename must not be rerouted.
    graph = {
        "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": FP8_NAME}},
    }
    package = _package([{"folder": "diffusion_models", "filename": FP8_NAME}], graph)
    report = apply_model_overrides_to_graph(
        graph, package, [_override()], force_default_weight_dtype=False
    )
    assert graph["3"]["inputs"]["clip_name"] == FP8_NAME
    assert report.replaced == []


def test_weight_dtype_forced_to_default_on_mps_only():
    for value in ("fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"):
        graph = {
            "1": {
                "class_type": "UNETLoader",
                "inputs": {"unet_name": "anything.safetensors", "weight_dtype": value},
            },
        }
        package = _package([], graph)
        report = apply_model_overrides_to_graph(
            graph, package, [], force_default_weight_dtype=True
        )
        assert graph["1"]["inputs"]["weight_dtype"] == "default"
        assert report.weight_dtype_patched[0]["from"] == value

    off_mps = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": "anything.safetensors", "weight_dtype": "fp8_e4m3fn"},
        },
    }
    report = apply_model_overrides_to_graph(
        off_mps, _package([], off_mps), [], force_default_weight_dtype=False
    )
    assert off_mps["1"]["inputs"]["weight_dtype"] == "fp8_e4m3fn"
    assert not report.changed


def test_runtime_patch_never_mutates_package_graph():
    graph = _graph()
    package = _package([{"folder": "diffusion_models", "filename": FP8_NAME}], graph)
    original_graph_snapshot = deepcopy(package.comfyui_graph)

    runtime_graph = apply_input_bindings(package, {})
    report = apply_model_overrides_to_graph(
        runtime_graph, package, [_override()], force_default_weight_dtype=True
    )

    assert report.changed
    assert runtime_graph["1"]["inputs"]["unet_name"] == CONVERTED_NAME
    assert package.comfyui_graph == original_graph_snapshot
    assert package.comfyui_graph["1"]["inputs"]["unet_name"] == FP8_NAME
