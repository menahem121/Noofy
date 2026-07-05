"""Dequantize an fp8 safetensors checkpoint into a Mac-compatible 16-bit file.

Executed with the managed ComfyUI venv's python (which has torch); the Noofy
backend env does not. The script is self-contained: it must never import
``comfy.*`` (import side effects, CLI arg parsing) or ``app.*``.

The transform is quality-lossless: every fp8 value is exactly representable
in bf16/fp16, and scaled-fp8 weights are dequantized as ``w * scale`` in
fp32 — the same math ComfyUI applies at compute time on hardware without fp8
support. Scale/marker/quant-metadata entries are dropped so the output is a
plain 16-bit checkpoint loadable by every stock loader node.

The safetensors container is written manually (8-byte little-endian header
length, JSON header, concatenated tensor data) so tensors stream one at a
time: peak RAM stays near the largest single tensor instead of the whole
converted model.

Protocol: JSON lines on stdout.
  {"phase": "converting", "done": <i>, "total": <n>}
  {"phase": "complete", "output_size": <bytes>, "target_dtype": "bf16",
   "fp8_tensors_converted": <k>}
  {"phase": "error", "code": "<slug>", "message": "..."} then exit code 1.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import shutil
import sys
from pathlib import Path

_FP8_DTYPES = ("F8_E4M3", "F8_E5M2")
_SCALED_FP8_MARKER_KEY = "scaled_fp8"
_DROPPED_KEY_SUFFIXES = (
    ".weight_scale",
    ".weight_scale_2",
    ".input_scale",
    ".scale_weight",
    ".scale_input",
    ".comfy_quant",
)
_QUANT_METADATA_KEY = "_quantization_metadata"
_MAX_HEADER_BYTES = 256 * 1024 * 1024
# Convert huge tensors in slices to bound peak memory.
_CONVERT_CHUNK_ELEMENTS = 32 * 1024 * 1024
_DISK_SPACE_HEADROOM = 1.1


def _emit(payload: dict) -> None:
    print(json.dumps(payload), flush=True)


def _fail(code: str, message: str):
    _emit({"phase": "error", "code": code, "message": message})
    sys.exit(1)


def _resolve_target_dtype(requested: str) -> str:
    if requested in ("bf16", "fp16"):
        return requested
    if platform.system() == "Darwin":
        release = platform.mac_ver()[0] or ""
        try:
            major = int(release.split(".")[0])
        except ValueError:
            major = 0
        # ComfyUI only enables bf16 on MPS for macOS 14+.
        return "bf16" if major >= 14 else "fp16"
    return "bf16"


def _read_header(path: Path) -> tuple[dict, int]:
    with path.open("rb") as file:
        header_length = int.from_bytes(file.read(8), "little")
        if header_length <= 0 or header_length > _MAX_HEADER_BYTES:
            _fail("invalid_safetensors", "The model file has an invalid safetensors header.")
        header = json.loads(file.read(header_length).decode("utf-8"))
    if not isinstance(header, dict):
        _fail("invalid_safetensors", "The model file has an invalid safetensors header.")
    return header, 8 + header_length


class ConversionPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fp8_weight_layers(header: dict) -> set:
    """Layer prefixes whose `.weight` tensor is stored as fp8 (and will be
    dequantized)."""
    layers = set()
    for key, entry in header.items():
        if key == "__metadata__" or not isinstance(entry, dict):
            continue
        if entry.get("dtype") in _FP8_DTYPES and key.endswith(".weight"):
            layers.add(key[: -len(".weight")])
    return layers


def _is_plumbing_suffixed(key: str) -> bool:
    return key.endswith(_DROPPED_KEY_SUFFIXES)


def _is_dropped_key(key: str, fp8_layers: set) -> bool:
    # The old-format marker only exists in files whose quant layers are all
    # fp8, so it never survives a conversion.
    if key == _SCALED_FP8_MARKER_KEY or key.endswith("." + _SCALED_FP8_MARKER_KEY):
        return True
    # Scale/quant plumbing is dropped only for layers being dequantized; a
    # mixed file's packed (e.g. NVFP4) layers keep their scales untouched.
    for suffix in _DROPPED_KEY_SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)] in fp8_layers
    return False


def _filtered_metadata(metadata, fp8_layers: set):
    """Preserve metadata, keeping quant descriptions only for layers that were
    not converted."""
    if not isinstance(metadata, dict):
        return None
    kept = dict(metadata)
    raw_quant = kept.pop(_QUANT_METADATA_KEY, None)
    if raw_quant is not None:
        try:
            quant = json.loads(raw_quant)
            remaining = {
                name: conf
                for name, conf in quant.get("layers", {}).items()
                if name not in fp8_layers
            }
        except (ValueError, TypeError, AttributeError):
            remaining = {}
        if remaining:
            quant["layers"] = remaining
            kept[_QUANT_METADATA_KEY] = json.dumps(quant)
    return kept or None


def _scale_key_for(key: str, header: dict) -> str | None:
    if not key.endswith(".weight"):
        return None
    layer = key[: -len(".weight")]
    for suffix in ("weight_scale", "scale_weight"):
        candidate = f"{layer}.{suffix}"
        if candidate in header:
            return candidate
    return None


def build_conversion_plan(header: dict, target_header_dtype: str) -> dict:
    """Pure planning pass (no torch): output header, per-tensor steps, sizes.

    Kept import-safe for the backend test environment.
    """
    metadata = header.get("__metadata__")
    tensor_keys = [key for key in header if key != "__metadata__"]
    fp8_layers = _fp8_weight_layers(header)

    output_header: dict = {}
    kept_metadata = _filtered_metadata(metadata, fp8_layers)
    if kept_metadata:
        output_header["__metadata__"] = kept_metadata
    plan: list = []
    offset = 0
    fp8_planned = 0
    for key in tensor_keys:
        entry = header[key]
        if not isinstance(entry, dict) or "data_offsets" not in entry:
            raise ConversionPlanError(
                "invalid_safetensors", f"Malformed header entry for tensor: {key}"
            )
        if _is_dropped_key(key, fp8_layers):
            continue
        dtype = entry.get("dtype")
        shape = entry.get("shape") or []
        input_size = entry["data_offsets"][1] - entry["data_offsets"][0]
        # fp8-typed plumbing kept for non-converted layers (NVFP4 block
        # scales) must pass through byte-for-byte, never be dequantized.
        convertible = dtype in _FP8_DTYPES and not _is_plumbing_suffixed(key)
        if convertible:
            fp8_planned += 1
            output_size = input_size * 2
            output_dtype = target_header_dtype
            scale_key = _scale_key_for(key, header)
        else:
            output_size = input_size
            output_dtype = dtype
            scale_key = None
        output_header[key] = {
            "dtype": output_dtype,
            "shape": shape,
            "data_offsets": [offset, offset + output_size],
        }
        plan.append(
            {
                "key": key,
                "entry": entry,
                "convert": convertible,
                "input_dtype": dtype,
                "scale_key": scale_key,
            }
        )
        offset += output_size

    if fp8_planned == 0:
        raise ConversionPlanError(
            "no_fp8_tensors", "The model file does not contain fp8 weight tensors."
        )
    return {
        "output_header": output_header,
        "plan": plan,
        "fp8_tensor_count": fp8_planned,
        "data_size": offset,
    }


def _torch_dtypes(torch, name: str):
    return {
        "F8_E4M3": torch.float8_e4m3fn,
        "F8_E5M2": torch.float8_e5m2,
        "F64": torch.float64,
        "F32": torch.float32,
        "F16": torch.float16,
        "BF16": torch.bfloat16,
    }[name]


def _read_tensor_bytes(file, data_start: int, offsets: list[int]) -> bytes:
    file.seek(data_start + offsets[0])
    return file.read(offsets[1] - offsets[0])


def _iter_tensor_byte_chunks(file, data_start: int, offsets: list[int], chunk_bytes: int):
    """Yield a tensor's raw data in bounded chunks so huge tensors never sit
    fully in memory."""
    position = data_start + offsets[0]
    remaining = offsets[1] - offsets[0]
    while remaining > 0:
        file.seek(position)
        chunk = file.read(min(chunk_bytes, remaining))
        if not chunk:
            raise ValueError("Unexpected end of tensor data")
        position += len(chunk)
        remaining -= len(chunk)
        yield chunk


def _read_scalar_scale(torch, file, data_start: int, entry: dict) -> float:
    raw = _read_tensor_bytes(file, data_start, entry["data_offsets"])
    dtype = entry.get("dtype")
    if dtype not in ("F64", "F32", "F16", "BF16"):
        raise ValueError(f"Unsupported scale dtype: {dtype}")
    tensor = torch.frombuffer(bytearray(raw), dtype=_torch_dtypes(torch, dtype))
    # Comfy scaled-fp8 checkpoints use a per-tensor scalar scale. Refusing
    # anything else is deliberate: silently taking the first element of a
    # per-channel/block scale layout would corrupt the converted weights.
    if tensor.numel() != 1:
        raise ValueError(
            f"Unsupported scale layout ({tensor.numel()} elements); "
            "only per-tensor scalar scales can be converted."
        )
    return float(tensor.flatten()[0].item())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--target-dtype", default="auto", choices=("auto", "bf16", "fp16"))
    parser.add_argument("--progress-every", type=int, default=5)
    args = parser.parse_args()

    try:
        # Only present in the managed ComfyUI venv, never in the backend env.
        import torch  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - depends on venv health
        _fail("torch_unavailable", f"PyTorch is not available in the runtime environment: {exc}")

    input_path = Path(args.input)
    output_path = Path(args.output)
    if not input_path.is_file():
        _fail("source_missing", f"Model file not found: {input_path}")

    target_name = _resolve_target_dtype(args.target_dtype)
    target_dtype = torch.bfloat16 if target_name == "bf16" else torch.float16
    target_header_dtype = "BF16" if target_name == "bf16" else "F16"

    header, data_start = _read_header(input_path)

    # Pass 1: plan the output header. Sizes are deterministic (fp8 doubles to
    # 16-bit; dropped keys disappear; everything else passes through).
    try:
        planned = build_conversion_plan(header, target_header_dtype)
    except ConversionPlanError as exc:
        _fail(exc.code, str(exc))
    output_header = planned["output_header"]
    plan = planned["plan"]

    header_bytes = json.dumps(output_header, separators=(",", ":")).encode("utf-8")
    total_output_size = 8 + len(header_bytes) + planned["data_size"]
    free_bytes = shutil.disk_usage(output_path.parent).free
    if free_bytes < total_output_size * _DISK_SPACE_HEADROOM:
        _fail(
            "not_enough_disk_space",
            f"Converting needs about {total_output_size / 1e9:.1f} GB of free disk space.",
        )

    total = len(plan)
    converted = 0
    with input_path.open("rb") as source, output_path.open("wb") as target:
        target.write(len(header_bytes).to_bytes(8, "little"))
        target.write(header_bytes)
        for index, step in enumerate(plan, start=1):
            entry = step["entry"]
            offsets = entry["data_offsets"]
            if not step["convert"]:
                # Pass-through tensors stream in bounded chunks too — a large
                # fp32 tensor must never be read whole.
                for raw_chunk in _iter_tensor_byte_chunks(
                    source, data_start, offsets, _CONVERT_CHUNK_ELEMENTS
                ):
                    target.write(raw_chunk)
            else:
                scale = 1.0
                if step["scale_key"] is not None:
                    scale = _read_scalar_scale(torch, source, data_start, header[step["scale_key"]])
                fp8_dtype = _torch_dtypes(torch, step["input_dtype"])
                input_size = offsets[1] - offsets[0]
                expected = math.prod(entry.get("shape") or []) if entry.get("shape") else input_size
                if expected and expected != input_size:
                    _fail("invalid_safetensors", f"Tensor size mismatch for: {step['key']}")
                # fp8 is one byte per element, so byte chunks are element chunks.
                for raw_chunk in _iter_tensor_byte_chunks(
                    source, data_start, offsets, _CONVERT_CHUNK_ELEMENTS
                ):
                    chunk = torch.frombuffer(bytearray(raw_chunk), dtype=fp8_dtype).to(torch.float32)
                    if scale != 1.0:
                        chunk = chunk * scale
                    out = chunk.to(target_dtype).contiguous()
                    target.write(out.view(torch.uint8).numpy().tobytes())
                converted += 1
            if index % max(args.progress_every, 1) == 0 or index == total:
                _emit({"phase": "converting", "done": index, "total": total})

    _emit(
        {
            "phase": "complete",
            "output_size": total_output_size,
            "target_dtype": target_name,
            "fp8_tensors_converted": converted,
        }
    )


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - single reporting funnel
        _emit({"phase": "error", "code": "conversion_failed", "message": str(exc)})
        sys.exit(1)
