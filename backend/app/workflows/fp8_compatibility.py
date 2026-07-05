"""FP8 model detection for Apple Silicon (MPS) compatibility.

PyTorch's MPS backend cannot store float8 tensors, so checkpoints whose
weights are stored as float8_e4m3fn/e5m2 crash at sampling time when ComfyUI
moves them to the GPU. Detection here is header-only: the safetensors header
lists every tensor's dtype and shape, so no tensor bytes are read and torch is
never imported.

FP4/NVFP4 checkpoints run fine on MPS (weights are packed uint8), but their
per-block scale tensors are float8-typed and can be large, so a tensor only
counts as incompatible fp8 weight storage when it is not quantization
plumbing (scale/marker tensors).
"""

from __future__ import annotations

import json
import math
import platform
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.engine.models import WorkflowValidationResult
from app.workflows.package import RequiredModel, WorkflowPackage

FP8_INCOMPATIBLE_MPS_ERROR_CODE = "fp8_incompatible_mps"
FP8_INCOMPATIBLE_MPS_ERROR_CATEGORY = "platform_compatibility"
FP8_INCOMPATIBLE_MPS_MESSAGE = (
    "This workflow uses an FP8 model that is not supported on Apple Silicon."
)
FP8_GRAPH_WEIGHT_DTYPE_VALUES = frozenset({"fp8_e4m3fn", "fp8_e4m3fn_fast", "fp8_e5m2"})

_FP8_HEADER_DTYPES = frozenset({"F8_E4M3", "F8_E5M2"})
# Tensors below this element count are treated as quantization plumbing
# (per-tensor scales, markers), not weight storage.
_FP8_WEIGHT_NUMEL_THRESHOLD = 4096
# Key suffixes used by ComfyUI quant formats for scale/config tensors. Their
# dtype never decides compatibility: NVFP4 block scales are float8-typed but
# the packed uint8 weights they belong to run on MPS.
_QUANT_PLUMBING_KEY_SUFFIXES = (
    ".weight_scale",
    ".weight_scale_2",
    ".input_scale",
    ".scale_weight",
    ".scale_input",
    ".block_scale",
    ".comfy_quant",
)
_SCALED_FP8_MARKER_KEY = "scaled_fp8"
_MAX_SAFETENSORS_HEADER_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Fp8Inspection:
    has_incompatible_fp8: bool
    fp8_dtypes: tuple[str, ...]
    quant_format: str  # "plain" | "scaled_fp8" | "quant_metadata"
    fp8_tensor_count: int


@dataclass(frozen=True)
class Fp8IncompatibleModel:
    folder: str
    filename: str
    path: Path
    inspection: Fp8Inspection

    def diagnostic_details(self) -> dict[str, Any]:
        return {
            "folder": self.folder,
            "filename": self.filename,
            "fp8_dtypes": list(self.inspection.fp8_dtypes),
            "quant_format": self.inspection.quant_format,
            "fp8_tensor_count": self.inspection.fp8_tensor_count,
        }


def read_safetensors_header(path: Path) -> dict[str, Any] | None:
    """Read the JSON header of a safetensors file without loading tensor data."""
    try:
        with path.open("rb") as file:
            header_length = int.from_bytes(file.read(8), "little")
            if header_length <= 0 or header_length > _MAX_SAFETENSORS_HEADER_BYTES:
                return None
            header = json.loads(file.read(header_length).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(header, dict):
        return None
    return header


def _is_quant_plumbing_key(key: str) -> bool:
    if key == _SCALED_FP8_MARKER_KEY or key.endswith("." + _SCALED_FP8_MARKER_KEY):
        return True
    return key.endswith(_QUANT_PLUMBING_KEY_SUFFIXES)


def inspect_safetensors_fp8_header(header: dict[str, Any]) -> Fp8Inspection:
    fp8_dtypes: set[str] = set()
    fp8_tensor_count = 0
    has_incompatible_fp8 = False
    scaled_marker_present = False
    metadata = header.get("__metadata__")
    quant_metadata_present = isinstance(metadata, dict) and "_quantization_metadata" in metadata

    for key, entry in header.items():
        if key == "__metadata__" or not isinstance(entry, dict):
            continue
        if key == _SCALED_FP8_MARKER_KEY or key.endswith("." + _SCALED_FP8_MARKER_KEY):
            scaled_marker_present = True
        dtype = entry.get("dtype")
        if dtype not in _FP8_HEADER_DTYPES:
            continue
        fp8_dtypes.add(str(dtype))
        fp8_tensor_count += 1
        if _is_quant_plumbing_key(key):
            continue
        shape = entry.get("shape")
        numel = math.prod(shape) if isinstance(shape, list) and shape else 1
        if numel >= _FP8_WEIGHT_NUMEL_THRESHOLD:
            has_incompatible_fp8 = True

    if scaled_marker_present:
        quant_format = "scaled_fp8"
    elif quant_metadata_present:
        quant_format = "quant_metadata"
    else:
        quant_format = "plain"
    return Fp8Inspection(
        has_incompatible_fp8=has_incompatible_fp8,
        fp8_dtypes=tuple(sorted(fp8_dtypes)),
        quant_format=quant_format,
        fp8_tensor_count=fp8_tensor_count,
    )


@dataclass
class _CachedInspection:
    stat_key: tuple[int, int]
    inspection: Fp8Inspection | None


class Fp8SafetensorsInspector:
    """Header-only fp8 inspection with a stat-key cache keyed by resolved path."""

    def __init__(self) -> None:
        self._cache: dict[Path, _CachedInspection] = {}

    def inspect(self, path: Path) -> Fp8Inspection | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        stat_key = (stat.st_size, stat.st_mtime_ns)
        resolved = path.resolve(strict=False)
        cached = self._cache.get(resolved)
        if cached is not None and cached.stat_key == stat_key:
            return cached.inspection
        header = read_safetensors_header(path)
        inspection = inspect_safetensors_fp8_header(header) if header is not None else None
        self._cache[resolved] = _CachedInspection(stat_key=stat_key, inspection=inspection)
        return inspection


def detect_darwin_accelerator() -> str | None:
    """Sync accelerator probe for the fp8 gate (Darwin branch of detect_hardware)."""
    if platform.system() != "Darwin":
        return None
    if (platform.machine() or "").lower() in {"arm64", "aarch64"}:
        return "apple_mps"
    return "unsupported_macos_intel"


def default_mps_execution_active(effective_vram_mode: Callable[[], str] | None = None) -> bool:
    """True when workflows execute on the MPS backend on this machine.

    CPU launch mode runs fp8 checkpoints (slowly) on the CPU, so it must not
    trigger the compatibility flow.
    """
    if detect_darwin_accelerator() != "apple_mps":
        return False
    if effective_vram_mode is not None and effective_vram_mode() == "cpu":
        return False
    return True


ResolveLocalModelPath = Callable[[RequiredModel], Path | None]
OverriddenModelKeys = Callable[[str], set[tuple[str, str]]]


@dataclass
class Fp8CompatibilityChecker:
    """Finds MPS-incompatible fp8 models required by a workflow.

    ``mps_execution_active`` gates every check; on non-MPS platforms this
    checker never flags anything.
    """

    resolve_local_model_path: ResolveLocalModelPath
    mps_execution_active: Callable[[], bool]
    overridden_model_keys: OverriddenModelKeys
    log_store: DiagnosticsSink | None = None
    inspector: Fp8SafetensorsInspector = field(default_factory=Fp8SafetensorsInspector)

    def find_incompatible_models(self, package: WorkflowPackage) -> list[Fp8IncompatibleModel]:
        if not self.mps_execution_active():
            return []
        overridden = self.overridden_model_keys(package.metadata.id)
        found: list[Fp8IncompatibleModel] = []
        for model in package.required_models:
            # .sft is the same safetensors container under a shorter name.
            if not model.filename.casefold().endswith((".safetensors", ".sft")):
                continue
            if (model.folder, model.filename) in overridden:
                continue
            path = self.resolve_local_model_path(model)
            if path is None:
                continue
            inspection = self.inspector.inspect(path)
            if inspection is None or not inspection.has_incompatible_fp8:
                continue
            found.append(
                Fp8IncompatibleModel(
                    folder=model.folder,
                    filename=model.filename,
                    path=path,
                    inspection=inspection,
                )
            )
        return found

    def preflight_validation(self, package: WorkflowPackage) -> WorkflowValidationResult | None:
        """Blocking validation result when the workflow needs incompatible fp8 models."""
        incompatible = self.find_incompatible_models(package)
        if not incompatible:
            return None
        details = {
            "accelerator": "apple_mps",
            "fp8_models": [model.diagnostic_details() for model in incompatible],
        }
        if self.log_store is not None:
            self.log_store.add(
                "warning",
                "FP8 model incompatible with MPS detected",
                "workflows.fp8_compat",
                workflow_id=package.metadata.id,
                details=details,
            )
        return WorkflowValidationResult(
            workflow_id=package.metadata.id,
            valid=False,
            errors=[FP8_INCOMPATIBLE_MPS_MESSAGE],
            error_category=FP8_INCOMPATIBLE_MPS_ERROR_CATEGORY,
            error_code=FP8_INCOMPATIBLE_MPS_ERROR_CODE,
            developer_details=details,
        )
