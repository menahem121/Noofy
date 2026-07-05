"""Torch-free tests for the convert script's planning pass.

The dequantization itself needs torch (managed venv only), but the pass-1
plan — which keys convert, drop, or pass through, and what metadata survives —
is pure and must be provable in the backend test environment.
"""

import json

import pytest

from app.workflows.fp8_convert_script import ConversionPlanError, build_conversion_plan


def _entry(dtype, shape, offset_ref):
    sizes = {"F8_E4M3": 1, "F8_E5M2": 1, "U8": 1, "F16": 2, "BF16": 2, "F32": 4}
    numel = 1
    for dim in shape:
        numel *= dim
    size = numel * sizes[dtype]
    start = offset_ref[0]
    offset_ref[0] += size
    return {"dtype": dtype, "shape": list(shape), "data_offsets": [start, start + size]}


def _header(tensors, metadata=None):
    offset_ref = [0]
    header = {key: _entry(dtype, shape, offset_ref) for key, (dtype, shape) in tensors.items()}
    if metadata is not None:
        header["__metadata__"] = metadata
    return header


def test_plan_converts_fp8_and_doubles_size():
    header = _header(
        {
            "blocks.0.weight": ("F8_E4M3", [64, 128]),
            "blocks.0.bias": ("F16", [64]),
        }
    )
    planned = build_conversion_plan(header, "BF16")
    out = planned["output_header"]
    assert out["blocks.0.weight"]["dtype"] == "BF16"
    assert out["blocks.0.weight"]["data_offsets"] == [0, 64 * 128 * 2]
    assert out["blocks.0.bias"]["dtype"] == "F16"
    assert planned["fp8_tensor_count"] == 1


def test_plan_drops_scales_and_marker_for_converted_layers_only():
    header = _header(
        {
            "scaled_fp8": ("F8_E4M3", [2]),
            "blocks.0.weight": ("F8_E4M3", [64, 128]),
            "blocks.0.scale_weight": ("F32", []),
            "blocks.0.scale_input": ("F32", []),
        }
    )
    planned = build_conversion_plan(header, "BF16")
    out = planned["output_header"]
    assert set(out) == {"blocks.0.weight"}
    step = planned["plan"][0]
    assert step["scale_key"] == "blocks.0.scale_weight"


def test_plan_preserves_packed_quant_layers_in_mixed_files():
    # One file holding an fp8 layer plus an NVFP4-style packed layer: the
    # packed layer's scales and quant metadata must survive untouched.
    header = _header(
        {
            "unet.weight": ("F8_E4M3", [64, 128]),
            "unet.weight_scale": ("F32", []),
            "te.weight": ("U8", [4096, 64]),
            "te.weight_scale": ("F8_E4M3", [4096, 8]),
            "te.weight_scale_2": ("F32", []),
        },
        metadata={
            "_quantization_metadata": json.dumps(
                {"layers": {"unet": {"format": "float8_e4m3fn"}, "te": {"format": "nvfp4"}}}
            ),
            "modelspec.title": "mixed",
        },
    )
    planned = build_conversion_plan(header, "BF16")
    out = planned["output_header"]

    assert out["unet.weight"]["dtype"] == "BF16"
    assert "unet.weight_scale" not in out
    # The packed layer keeps its weights, scales, and quant description.
    assert out["te.weight"]["dtype"] == "U8"
    assert out["te.weight_scale"]["dtype"] == "F8_E4M3"
    assert "te.weight_scale_2" in out
    metadata = out["__metadata__"]
    assert metadata["modelspec.title"] == "mixed"
    quant = json.loads(metadata["_quantization_metadata"])
    assert set(quant["layers"]) == {"te"}
    assert quant["layers"]["te"]["format"] == "nvfp4"


def test_plan_strips_quant_metadata_when_all_layers_converted():
    header = _header(
        {
            "unet.weight": ("F8_E4M3", [64, 128]),
            "unet.weight_scale": ("F32", []),
        },
        metadata={
            "_quantization_metadata": json.dumps(
                {"layers": {"unet": {"format": "float8_e4m3fn"}}}
            )
        },
    )
    planned = build_conversion_plan(header, "BF16")
    assert "__metadata__" not in planned["output_header"]


def test_plan_requires_fp8_tensors():
    header = _header({"blocks.0.weight": ("BF16", [64, 128])})
    with pytest.raises(ConversionPlanError) as excinfo:
        build_conversion_plan(header, "BF16")
    assert excinfo.value.code == "no_fp8_tensors"


def test_plan_output_offsets_are_contiguous():
    header = _header(
        {
            "a.weight": ("F8_E4M3", [64, 128]),
            "b.weight": ("F32", [16]),
            "c.weight": ("F8_E5M2", [4096]),
        }
    )
    planned = build_conversion_plan(header, "F16")
    cursor = 0
    for key, entry in planned["output_header"].items():
        assert entry["data_offsets"][0] == cursor
        cursor = entry["data_offsets"][1]
    assert cursor == planned["data_size"]
