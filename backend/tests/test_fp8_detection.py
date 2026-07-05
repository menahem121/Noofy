import os

from app.workflows import fp8_compatibility
from app.workflows.fp8_compatibility import (
    Fp8SafetensorsInspector,
    inspect_safetensors_fp8_header,
    read_safetensors_header,
)

from tests.fp8_test_utils import fp8_quant_metadata, write_safetensors


def _inspect(path):
    header = read_safetensors_header(path)
    assert header is not None
    return inspect_safetensors_fp8_header(header)


def test_flags_large_plain_fp8_e4m3_weights(tmp_path):
    path = write_safetensors(
        tmp_path / "model-fp8.safetensors",
        {"blocks.0.weight": ("F8_E4M3", [64, 128]), "blocks.0.bias": ("F16", [64])},
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is True
    assert inspection.fp8_dtypes == ("F8_E4M3",)
    assert inspection.quant_format == "plain"


def test_flags_large_plain_fp8_e5m2_weights(tmp_path):
    path = write_safetensors(
        tmp_path / "model-e5m2.safetensors",
        {"blocks.0.weight": ("F8_E5M2", [64, 128])},
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is True
    assert inspection.fp8_dtypes == ("F8_E5M2",)


def test_flags_old_scaled_fp8_format(tmp_path):
    path = write_safetensors(
        tmp_path / "scaled-fp8.safetensors",
        {
            "scaled_fp8": ("F8_E4M3", [2]),
            "blocks.0.weight": ("F8_E4M3", [64, 128]),
            "blocks.0.scale_weight": ("F32", []),
        },
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is True
    assert inspection.quant_format == "scaled_fp8"


def test_flags_new_quant_metadata_fp8_format(tmp_path):
    path = write_safetensors(
        tmp_path / "quant-fp8.safetensors",
        {
            "blocks.0.weight": ("F8_E4M3", [64, 128]),
            "blocks.0.weight_scale": ("F32", []),
        },
        metadata=fp8_quant_metadata({"blocks.0": "float8_e4m3fn"}),
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is True
    assert inspection.quant_format == "quant_metadata"


def test_bf16_only_file_is_not_flagged(tmp_path):
    path = write_safetensors(
        tmp_path / "model-bf16.safetensors",
        {"blocks.0.weight": ("BF16", [64, 128]), "blocks.0.bias": ("F32", [64])},
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is False
    assert inspection.fp8_tensor_count == 0


def test_nvfp4_file_with_large_fp8_scales_is_not_flagged(tmp_path):
    # NVFP4 checkpoints store packed uint8 weights whose per-block scale
    # tensors are float8-typed and large; they run fine on MPS.
    path = write_safetensors(
        tmp_path / "model-fp4.safetensors",
        {
            "blocks.0.weight": ("U8", [4096, 64]),
            "blocks.0.weight_scale": ("F8_E4M3", [4096, 8]),
            "blocks.0.weight_scale_2": ("F32", []),
        },
        metadata=fp8_quant_metadata({"blocks.0": "nvfp4"}),
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is False
    assert inspection.fp8_tensor_count == 1


def test_tiny_fp8_tensors_are_not_flagged(tmp_path):
    path = write_safetensors(
        tmp_path / "tiny-fp8.safetensors",
        {"some.marker": ("F8_E4M3", [2])},
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is False


def test_detection_reads_header_only(tmp_path):
    path = write_safetensors(
        tmp_path / "truncated-fp8.safetensors",
        {"blocks.0.weight": ("F8_E4M3", [64, 128])},
        truncate_data=True,
    )
    inspection = _inspect(path)
    assert inspection.has_incompatible_fp8 is True


def test_invalid_file_returns_none(tmp_path):
    path = tmp_path / "not-a-model.safetensors"
    path.write_bytes(b"garbage")
    assert read_safetensors_header(path) is None
    inspector = Fp8SafetensorsInspector()
    assert inspector.inspect(path) is None


def test_inspector_caches_by_stat_key(tmp_path, monkeypatch):
    path = write_safetensors(
        tmp_path / "cached-fp8.safetensors",
        {"blocks.0.weight": ("F8_E4M3", [64, 128])},
    )
    inspector = Fp8SafetensorsInspector()
    first = inspector.inspect(path)
    assert first is not None and first.has_incompatible_fp8

    def _fail(_path):
        raise AssertionError("header re-read despite unchanged stat key")

    monkeypatch.setattr(fp8_compatibility, "read_safetensors_header", _fail)
    second = inspector.inspect(path)
    assert second == first

    monkeypatch.undo()
    write_safetensors(
        path,
        {"blocks.0.weight": ("BF16", [64, 128]), "extra": ("F32", [8])},
    )
    os.utime(path, ns=(path.stat().st_atime_ns + 5, path.stat().st_mtime_ns + 5_000_000))
    refreshed = inspector.inspect(path)
    assert refreshed is not None
    assert refreshed.has_incompatible_fp8 is False
