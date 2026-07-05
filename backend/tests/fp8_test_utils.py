"""Helpers for synthesizing minimal real safetensors files in fp8 tests."""

from __future__ import annotations

import json
import math
from pathlib import Path

_DTYPE_SIZES = {
    "F8_E4M3": 1,
    "F8_E5M2": 1,
    "U8": 1,
    "F16": 2,
    "BF16": 2,
    "F32": 4,
}


def write_safetensors(
    path: Path,
    tensors: dict[str, tuple[str, list[int]]],
    *,
    metadata: dict[str, str] | None = None,
    truncate_data: bool = False,
) -> Path:
    """Write a structurally valid safetensors file with zeroed tensor data.

    ``tensors`` maps key -> (header dtype string, shape). ``truncate_data``
    omits the data section entirely, proving readers that only need the
    header never touch tensor bytes.
    """
    header: dict[str, object] = {}
    offset = 0
    for key, (dtype, shape) in tensors.items():
        numel = math.prod(shape) if shape else 1
        size = numel * _DTYPE_SIZES[dtype]
        header[key] = {
            "dtype": dtype,
            "shape": list(shape),
            "data_offsets": [offset, offset + size],
        }
        offset += size
    if metadata is not None:
        header["__metadata__"] = metadata
    header_bytes = json.dumps(header).encode("utf-8")
    payload = len(header_bytes).to_bytes(8, "little") + header_bytes
    if not truncate_data:
        payload += bytes(offset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def fp8_quant_metadata(layers: dict[str, str]) -> dict[str, str]:
    return {
        "_quantization_metadata": json.dumps(
            {"layers": {name: {"format": fmt} for name, fmt in layers.items()}}
        )
    }
