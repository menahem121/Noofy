from __future__ import annotations

import copy
import hashlib
import json
import logging
import mimetypes
import os
import platform
import re
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, urlsplit, urlunsplit


EXPORTER_NAME = "Noofy ComfyUI Export Extension"
EXPORTER_VERSION = "0.1.1"
SCHEMA_VERSION = "0.1.0"
TRUST_LEVEL = "public_unverified"
TEST_INPUT_MODE = "workflow_current_load_image_inputs"
TEST_BATCH_SIZE = 1
LOCAL_IMAGE_NODE_TYPES = {"LoadImage", "LoadImageMask"}
REDACTED_IMAGE_INPUT_VALUE = "__noofy_runtime_image_input_required__"
MEDIA_KINDS = frozenset({"image", "audio", "video", "3d", "text", "file"})
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".json", ".csv", ".tsv", ".srt", ".vtt", ".yaml", ".yml"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"})
THREE_D_EXTENSIONS = frozenset({".glb", ".gltf", ".obj", ".fbx", ".stl", ".usdz", ".ply", ".dae", ".spz", ".splat", ".ksplat"})
FILE_INPUT_NAMES = frozenset(
    {
        "audio",
        "audio_path",
        "file",
        "file_path",
        "filepath",
        "filename",
        "image",
        "json",
        "mask",
        "model_file",
        "path",
        "recording",
        "srt",
        "subtitle",
        "subtitles",
        "video",
        "video_path",
        "zip",
    }
)
LOCAL_AUDIO_NODE_TYPES = {"LoadAudio"}
LOCAL_VIDEO_NODE_TYPES = {"LoadVideo", "VHS_LoadVideo", "VHS_LoadVideoPath"}
LOCAL_THREE_D_NODE_TYPES = {"Load3D", "Load3DAnimation"}
MODEL_VERIFICATION_HASH_AND_SIZE = "sha256_size"
MODEL_VERIFICATION_FILENAME_AND_SIZE = "filename_size"
MODEL_VERIFICATION_FILENAME_ONLY = "filename_only"
MODEL_ASSET_OWNERSHIP_EXTERNAL = "external_reference"
MODEL_HASH_CACHE_SCHEMA_VERSION = 1
MODEL_HASH_CACHE_MAX_ENTRIES = 4096
MODEL_HASH_CACHE_SAMPLE_BYTES = 1024 * 1024
DEFAULT_MODEL_HASH_CONCURRENCY = 3
MODEL_HASH_CONCURRENCY_ENV = "NOOFY_EXPORT_MODEL_HASH_CONCURRENCY"
NETWORK_VERIFICATION_FILESYSTEM_TYPES = frozenset(
    {"nfs", "nfs4", "cifs", "smbfs", "smb3", "afpfs", "ncpfs", "9p", "sshfs"}
)
MAX_BUNDLED_INPUT_ASSET_BYTES = 512 * 1024 * 1024
DISCOVERY_METADATA_FIELDS = ("description", "author", "website", "category", "tags")
CANONICAL_CATEGORY_OPTIONS = frozenset(
    {
        "Txt2img",
        "Img2img",
        "txt2audio",
        "audio2audio",
        "txt2vid",
        "img2vid",
        "imgTo3D",
        "txtTo3D",
        "txt2txt",
        "img2text",
        "audio2txt",
        "vid2vid",
        "Inpainting",
        "Outpainting",
        "Upscaling",
        "Style Transfer",
        "Swapping",
        "Character Consistency",
        "Pose Control",
        "Depth Control",
        "Canny / Line Control",
        "Background Replacement",
        "Background Removal",
        "Restoration",
        "All-in-one",
    }
)


MODEL_INPUTS: dict[str, dict[str, tuple[str, str]]] = {
    "CheckpointLoader": {
        "ckpt_name": ("checkpoints", "checkpoint"),
        "config_name": ("configs", "config"),
    },
    "CheckpointLoaderSimple": {"ckpt_name": ("checkpoints", "checkpoint")},
    "unCLIPCheckpointLoader": {"ckpt_name": ("checkpoints", "checkpoint")},
    "LoraLoader": {"lora_name": ("loras", "lora")},
    "LoraLoaderModelOnly": {"lora_name": ("loras", "lora")},
    "VAELoader": {"vae_name": ("vae", "vae")},
    "ControlNetLoader": {"control_net_name": ("controlnet", "controlnet")},
    "DiffControlNetLoader": {"control_net_name": ("controlnet", "controlnet")},
    "UNETLoader": {"unet_name": ("diffusion_models", "diffusion_model")},
    "CLIPLoader": {"clip_name": ("text_encoders", "text_encoder")},
    "DualCLIPLoader": {
        "clip_name1": ("text_encoders", "text_encoder"),
        "clip_name2": ("text_encoders", "text_encoder"),
    },
    "CLIPVisionLoader": {"clip_name": ("clip_vision", "clip_vision")},
    "StyleModelLoader": {"style_model_name": ("style_models", "style_model")},
    "GLIGENLoader": {"gligen_name": ("gligen", "gligen")},
    "UpscaleModelLoader": {"model_name": ("upscale_models", "upscale_model")},
    "LatentUpscaleModelLoader": {
        "model_name": ("latent_upscale_models", "upscale_model")
    },
}


MODEL_INPUT_NAME_HINTS: dict[str, tuple[str, str]] = {
    "ckpt_name": ("checkpoints", "checkpoint"),
    "checkpoint": ("checkpoints", "checkpoint"),
    "checkpoint_name": ("checkpoints", "checkpoint"),
    "lora_name": ("loras", "lora"),
    "vae_name": ("vae", "vae"),
    "control_net_name": ("controlnet", "controlnet"),
    "controlnet_name": ("controlnet", "controlnet"),
    "unet_name": ("diffusion_models", "diffusion_model"),
    "diffusion_model_name": ("diffusion_models", "diffusion_model"),
    "clip_name": ("text_encoders", "text_encoder"),
    "clip_name1": ("text_encoders", "text_encoder"),
    "clip_name2": ("text_encoders", "text_encoder"),
    "style_model_name": ("style_models", "style_model"),
    "gligen_name": ("gligen", "gligen"),
    "upscale_model_name": ("upscale_models", "upscale_model"),
    "model_name": ("upscale_models", "upscale_model"),
}


EXCLUDED_CUSTOM_NODE_DIRS = {
    ".cache",
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "outputs",
    "output",
    "temp",
    "tmp",
    "venv",
}


EXCLUDED_CUSTOM_NODE_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".engine",
    ".gguf",
    ".onnx",
    ".pkl",
    ".pt",
    ".pth",
    ".pyc",
    ".pyo",
    ".safetensors",
    ".sft",
    ".tmp",
}


@dataclass
class CustomNodeRecord:
    id: str
    folder_name: str
    source: str = "bundled_from_creator_machine"
    sha256_manifest: str | None = None
    included: bool = False
    requirements_files: list[str] = field(default_factory=list)
    has_install_py: bool = False
    source_path: str | None = None
    node_types: list[str] = field(default_factory=list)
    file_manifest: list[dict[str, Any]] = field(default_factory=list)
    included_size_bytes: int = 0
    excluded_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def lock_entry(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "folder_name": self.folder_name,
            "source": self.source,
            "sha256_manifest": self.sha256_manifest,
            "included": self.included,
            "requirements_files": self.requirements_files,
            "has_install_py": self.has_install_py,
            "node_types": self.node_types,
        }


@dataclass
class RuntimeMetadata:
    comfyui_version: str
    python_version: str
    platform_name: str
    gpu_backend: str
    gpu_name: str | None
    pytorch_version: str | None = None


@dataclass
class MemoryObservation:
    observed_peak_vram_mb: int | None
    observed_peak_ram_mb: int | None
    tested_input_mode: str = TEST_INPUT_MODE
    tested_resolution: str | None = None
    tested_batch_size: int = TEST_BATCH_SIZE
    gpu_name: str | None = None
    backend: str | None = None
    precision: str | None = None
    recommended_vram_mb: int | None = None
    recommended_ram_mb: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_peak_vram_mb": self.observed_peak_vram_mb,
            "observed_peak_ram_mb": self.observed_peak_ram_mb,
            "tested_input_mode": self.tested_input_mode,
            "tested_resolution": self.tested_resolution,
            "tested_batch_size": self.tested_batch_size,
            "gpu_name": self.gpu_name,
            "backend": self.backend,
            "precision": self.precision,
            "recommended_vram_mb": self.recommended_vram_mb,
            "recommended_ram_mb": self.recommended_ram_mb,
        }


class MemorySampler:
    def __init__(
        self,
        read_ram_used: Callable[[], int | None],
        read_vram_used: Callable[[], int | None],
    ) -> None:
        self._read_ram_used = read_ram_used
        self._read_vram_used = read_vram_used
        self.peak_ram_bytes: int | None = None
        self.peak_vram_bytes: int | None = None

    def sample(self) -> None:
        ram_used = self._read_ram_used()
        vram_used = self._read_vram_used()
        if ram_used is not None:
            self.peak_ram_bytes = max(self.peak_ram_bytes or 0, ram_used)
        if vram_used is not None:
            self.peak_vram_bytes = max(self.peak_vram_bytes or 0, vram_used)

    def observation(self, runtime: RuntimeMetadata) -> MemoryObservation:
        return MemoryObservation(
            observed_peak_ram_mb=bytes_to_mb(self.peak_ram_bytes),
            observed_peak_vram_mb=bytes_to_mb(self.peak_vram_bytes),
            gpu_name=runtime.gpu_name,
            backend=runtime.gpu_backend,
        )


@dataclass
class VerifyHashMetrics:
    cache_hits: int = 0
    cache_misses: int = 0
    bytes_hashed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_cache_miss(self, *, bytes_hashed: int) -> None:
        with self._lock:
            self.cache_misses += 1
            self.bytes_hashed += max(0, bytes_hashed)

    def to_dict(self) -> dict[str, int]:
        return {
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "bytes_hashed": self.bytes_hashed,
        }


@dataclass
class InputAssetCandidate:
    id: str
    node_id: str
    node_type: str
    input_name: str
    expected_kind: str
    source_path: Path | None
    filename: str
    extension: str | None
    mime_type: str | None
    size_bytes: int | None
    selectable: bool
    reason: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "input_name": self.input_name,
            "expected_kind": self.expected_kind,
            "filename": self.filename,
            "extension": self.extension,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "selectable": self.selectable,
            "included": self.selectable,
            **({"reason": self.reason} if self.reason else {}),
        }


@dataclass
class BundledInputAsset:
    candidate: InputAssetCandidate
    asset_id: str
    reference: dict[str, Any]


@dataclass(frozen=True)
class WorkflowOutputRecord:
    id: str
    label: str
    node_id: str
    node_type: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "label": self.label,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "type": self.kind,
            "kind": self.kind,
        }


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def pretty_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def prepare_workflow_for_package(
    workflow: dict[str, Any] | None,
    *,
    original_graph: dict[str, Any],
    package_graph: dict[str, Any],
    workflow_widget_bindings: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return an editable workflow with creator-local loader values redacted."""
    if not isinstance(workflow, dict):
        return None
    if not isinstance(workflow_widget_bindings, dict):
        return None
    binding_nodes = workflow_widget_bindings.get("nodes")
    if not isinstance(binding_nodes, dict):
        return None

    packaged = copy.deepcopy(workflow)
    workflow_nodes = _top_level_workflow_nodes_by_id(packaged)
    for node_id, package_node in package_graph.items():
        if not isinstance(package_node, dict):
            continue
        original_node = original_graph.get(node_id)
        if not isinstance(original_node, dict):
            continue
        package_inputs = package_node.get("inputs")
        original_inputs = original_node.get("inputs")
        if not isinstance(package_inputs, dict) or not isinstance(original_inputs, dict):
            continue
        for input_name, package_value in package_inputs.items():
            original_value = original_inputs.get(input_name)
            if (
                _is_runtime_input_placeholder(package_value)
                and original_value != package_value
            ):
                if not _replace_bound_workflow_widget_value(
                    workflow_nodes,
                    binding_nodes,
                    node_id=node_id,
                    input_name=input_name,
                    original_value=original_value,
                    package_value=package_value,
                ):
                    return None

    return packaged


def _top_level_workflow_nodes_by_id(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return {}
    return {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and "id" in node
    }


def _replace_bound_workflow_widget_value(
    workflow_nodes: dict[str, dict[str, Any]],
    binding_nodes: Any,
    *,
    node_id: str,
    input_name: str,
    original_value: Any,
    package_value: Any,
) -> bool:
    if not isinstance(binding_nodes, dict):
        return False
    node = workflow_nodes.get(str(node_id))
    widget_indexes = binding_nodes.get(str(node_id))
    if not isinstance(node, dict) or not isinstance(widget_indexes, dict):
        return False
    widget_index = widget_indexes.get(input_name)
    widgets_values = node.get("widgets_values")
    if (
        not isinstance(widget_index, int)
        or widget_index < 0
        or not isinstance(widgets_values, list)
        or widget_index >= len(widgets_values)
        or widgets_values[widget_index] != original_value
    ):
        return False
    widgets_values[widget_index] = copy.deepcopy(package_value)
    return True


def _is_runtime_input_placeholder(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("__noofy_runtime_")
        and value.endswith("_input_required__")
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024 * 8) -> str:
    if hasattr(hashlib, "file_digest"):
        with path.open("rb") as file:
            return hashlib.file_digest(file, "sha256").hexdigest()

    hasher = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


class ModelHashCache:
    def __init__(
        self,
        cache_path: Path,
        *,
        max_entries: int = MODEL_HASH_CACHE_MAX_ENTRIES,
    ) -> None:
        self.cache_path = cache_path
        self.max_entries = max_entries
        self._lock = threading.RLock()
        self._entries = self._load_entries()

    def get_valid_hash(self, path: Path, stat: os.stat_result) -> str | None:
        key = model_hash_cache_key(path)
        with self._lock:
            entry = self._entries.get(key)
            if not isinstance(entry, dict):
                return None

            cached_path = entry.get("resolved_path")
            if not isinstance(cached_path, str) or os.path.normcase(cached_path) != key:
                self._forget_entry(key)
                return None

            sha256 = normalize_sha256(entry.get("sha256"))
            if sha256 is None:
                self._forget_entry(key)
                return None

            if not model_hash_cache_entry_matches(entry, stat):
                self._forget_entry(key)
                return None

            cached_fingerprint = normalize_sha256(entry.get("content_fingerprint"))
            if cached_fingerprint is None:
                self._forget_entry(key)
                return None

            try:
                current_fingerprint = sampled_file_fingerprint(path, stat)
            except OSError:
                return None
            if current_fingerprint != cached_fingerprint:
                self._forget_entry(key)
                return None

            entry["last_used_at"] = utc_now_iso()
            return sha256

    def remember_hash(self, path: Path, stat: os.stat_result, sha256: str) -> None:
        normalized_sha256 = normalize_sha256(sha256)
        if normalized_sha256 is None:
            return

        try:
            content_fingerprint = sampled_file_fingerprint(path, stat)
        except OSError:
            return

        now = utc_now_iso()
        key = model_hash_cache_key(path)
        with self._lock:
            self._entries[key] = {
                "resolved_path": resolved_path_key(path),
                "sha256": normalized_sha256,
                "content_fingerprint": content_fingerprint,
                "size_bytes": int(stat.st_size),
                "mtime_ns": stat_mtime_ns(stat),
                "device_id": stat_device_id(stat),
                "inode": stat_inode(stat),
                "scanned_at": now,
                "last_used_at": now,
                "schema_version": MODEL_HASH_CACHE_SCHEMA_VERSION,
            }
            self._prune_entries()
            self._save_entries()

    def _load_entries(self) -> dict[str, dict[str, Any]]:
        if not self.cache_path.exists():
            return {}

        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("[Noofy Export] model hash cache could not be loaded: %s", exc)
            return {}

        if not isinstance(data, dict):
            return {}
        if data.get("schema_version") != MODEL_HASH_CACHE_SCHEMA_VERSION:
            return {}

        entries = data.get("entries")
        if not isinstance(entries, dict):
            return {}

        valid_entries: dict[str, dict[str, Any]] = {}
        for key, entry in entries.items():
            if isinstance(key, str) and isinstance(entry, dict):
                valid_entries[key] = entry
        return valid_entries

    def _save_entries(self) -> None:
        payload = {
            "schema_version": MODEL_HASH_CACHE_SCHEMA_VERSION,
            "entries": self._entries,
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.cache_path.with_name(f"{self.cache_path.name}.tmp")
            temporary_path.write_bytes(pretty_json_bytes(payload))
            temporary_path.replace(self.cache_path)
        except OSError as exc:
            logging.warning("[Noofy Export] model hash cache could not be saved: %s", exc)

    def _forget_entry(self, key: str) -> None:
        self._entries.pop(key, None)
        self._save_entries()

    def _prune_entries(self) -> None:
        if len(self._entries) <= self.max_entries:
            return

        sorted_items = sorted(
            self._entries.items(),
            key=lambda item: (
                str(item[1].get("last_used_at") or ""),
                str(item[1].get("scanned_at") or ""),
            ),
        )
        for key, _entry in sorted_items[: len(self._entries) - self.max_entries]:
            self._entries.pop(key, None)


def resolved_path_key(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


def model_hash_cache_key(path: Path) -> str:
    return os.path.normcase(resolved_path_key(path))


def model_hash_cache_entry_matches(entry: dict[str, Any], stat: os.stat_result) -> bool:
    cached_size = int_or_none(entry.get("size_bytes"))
    if cached_size != int(stat.st_size):
        return False

    cached_mtime_ns = int_or_none(entry.get("mtime_ns"))
    if cached_mtime_ns != stat_mtime_ns(stat):
        return False

    entry_device = entry.get("device_id")
    current_device = stat_device_id(stat)
    if entry_device is not None and current_device is not None:
        try:
            if int(entry_device) != current_device:
                return False
        except (TypeError, ValueError):
            return False

    entry_inode = entry.get("inode")
    current_inode = stat_inode(stat)
    if entry_inode is not None and current_inode is not None:
        try:
            if int(entry_inode) != current_inode:
                return False
        except (TypeError, ValueError):
            return False

    return True


def sampled_file_fingerprint(
    path: Path,
    stat: os.stat_result,
    chunk_size: int = MODEL_HASH_CACHE_SAMPLE_BYTES,
) -> str:
    size_bytes = int(stat.st_size)
    hasher = hashlib.sha256()
    hasher.update(f"size:{size_bytes}\0".encode("ascii"))

    with path.open("rb") as file:
        for offset, length in sampled_file_ranges(size_bytes, chunk_size):
            file.seek(offset)
            chunk = file.read(length)
            hasher.update(f"{offset}:{len(chunk)}\0".encode("ascii"))
            hasher.update(chunk)

    return hasher.hexdigest()


def sampled_file_ranges(size_bytes: int, chunk_size: int) -> list[tuple[int, int]]:
    if size_bytes <= 0:
        return []
    if size_bytes <= chunk_size * 3:
        return [(0, size_bytes)]

    middle_offset = (size_bytes - chunk_size) // 2
    return [
        (0, chunk_size),
        (middle_offset, chunk_size),
        (size_bytes - chunk_size, chunk_size),
    ]


def int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def stat_mtime_ns(stat: os.stat_result) -> int:
    fallback = int(getattr(stat, "st_mtime", 0) * 1_000_000_000)
    return int(getattr(stat, "st_mtime_ns", fallback))


def stat_device_id(stat: os.stat_result) -> int | None:
    value = getattr(stat, "st_dev", None)
    return int(value) if isinstance(value, int) else None


def stat_inode(stat: os.stat_result) -> int | None:
    value = getattr(stat, "st_ino", None)
    return int(value) if isinstance(value, int) else None


def normalize_sha256(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.removeprefix("sha256:").casefold()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        return None
    return normalized


def bytes_to_mb(value: int | None) -> int | None:
    if value is None:
        return None
    return int(round(value / (1024 * 1024)))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def slugify(value: str, fallback: str = "workflow") -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-._")
    return slug or fallback


def normalize_platform(value: str | None = None) -> str:
    name = value or sys.platform
    if name.startswith("darwin"):
        return "darwin"
    if name.startswith("win"):
        return "windows"
    if name.startswith("linux"):
        return "linux"
    return name


def normalize_gpu_backend(device_type: str | None) -> str:
    if device_type == "cuda":
        return "cuda"
    if device_type == "mps":
        return "mps"
    if device_type == "cpu":
        return "cpu"
    return "unknown"


def normalize_discovery_metadata(
    *,
    package_id: str,
    version: str,
    workflow_name: str | None,
    export_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = export_metadata if isinstance(export_metadata, dict) else {}
    display_name = clean_metadata_text(metadata.get("name")) or clean_metadata_text(
        metadata.get("display_name")
    )
    if display_name is None:
        display_name = clean_metadata_text(workflow_name) or "Exported ComfyUI Workflow"

    category = clean_metadata_text(metadata.get("category"))
    if category not in CANONICAL_CATEGORY_OPTIONS:
        category = None

    return {
        "id": package_id,
        "name": display_name,
        "display_name": display_name,
        "version": version,
        "description": clean_metadata_text(metadata.get("description")) or "",
        "author": clean_metadata_text(metadata.get("author")) or "",
        "website": clean_metadata_text(metadata.get("website")) or "",
        "category": category or "",
        "tags": clean_metadata_tags(metadata.get("tags")),
    }


def apply_metadata_mirrors(package_json: dict[str, Any], metadata: dict[str, Any]) -> None:
    package_json["metadata"] = dict(metadata)
    package_json["display_name"] = metadata["display_name"]
    for key in DISCOVERY_METADATA_FIELDS:
        package_json[key] = metadata[key]


def assert_metadata_mirrors_consistent(package_json: dict[str, Any]) -> None:
    metadata = package_json.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("package.json metadata must be an object.")
    if package_json.get("display_name") != metadata.get("display_name"):
        raise ValueError("package.json display_name does not match metadata.display_name.")
    for key in DISCOVERY_METADATA_FIELDS:
        if package_json.get(key) != metadata.get(key):
            raise ValueError(f"package.json {key} does not match metadata.{key}.")


def clean_metadata_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def clean_metadata_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        items: Iterable[Any] = value.split(",")
    elif isinstance(value, list):
        items = value
    else:
        return []
    tags: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = clean_metadata_text(item)
        if cleaned is None:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(cleaned)
    return tags


def infer_suggested_category(
    *,
    input_kinds: Iterable[str],
    output_kinds: Iterable[str],
) -> str | None:
    normalized_inputs = {kind for kind in input_kinds if kind in MEDIA_KINDS and kind != "text"}
    normalized_outputs = [kind for kind in MEDIA_KINDS_IN_ORDER if kind in set(output_kinds)]
    if len(normalized_outputs) != 1:
        return None
    output_kind = normalized_outputs[0]
    if output_kind == "file":
        return None

    media_inputs = {kind for kind in normalized_inputs if kind in {"image", "audio", "video", "3d", "file"}}
    if len(media_inputs) > 1:
        return None
    input_kind = next(iter(media_inputs), None)
    mapping = {
        (None, "image"): "Txt2img",
        ("image", "image"): "Img2img",
        (None, "audio"): "txt2audio",
        ("audio", "audio"): "audio2audio",
        (None, "video"): "txt2vid",
        ("image", "video"): "img2vid",
        ("video", "video"): "vid2vid",
        (None, "3d"): "txtTo3D",
        ("image", "3d"): "imgTo3D",
        (None, "text"): "txt2txt",
        ("image", "text"): "img2text",
        ("audio", "text"): "audio2txt",
    }
    return mapping.get((input_kind, output_kind))


def infer_static_output_kinds(graph: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            continue
        normalized = class_type.casefold()
        if any(token in normalized for token in ("saveaudio", "previewaudio", "displayaudio")):
            kinds.append("audio")
        elif any(token in normalized for token in ("savevideo", "videocombine", "displayvideo")):
            kinds.append("video")
        elif "saveimage" in normalized or "previewimage" in normalized:
            kinds.append("image")
        elif "save3d" in normalized or "export3d" in normalized or "display3d" in normalized:
            kinds.append("3d")
        elif "textoutput" in normalized or "savetext" in normalized:
            kinds.append("text")
    return [kind for kind in MEDIA_KINDS_IN_ORDER if kind in set(kinds)]


def prepare_graph_for_export(
    prompt: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    graph = copy.deepcopy(prompt)
    adjustments = {"image_inputs_preserved": 0, "batch_size_inputs": 0}

    for node in graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        if class_type in LOCAL_IMAGE_NODE_TYPES and isinstance(inputs.get("image"), str):
            adjustments["image_inputs_preserved"] += 1

        if "batch_size" in inputs and isinstance(inputs["batch_size"], int):
            inputs["batch_size"] = TEST_BATCH_SIZE
            adjustments["batch_size_inputs"] += 1

    return graph, adjustments


def collect_input_asset_candidates(
    graph: dict[str, Any],
    resolve_input_path: Callable[[str], str | Path | None] | None = None,
) -> list[InputAssetCandidate]:
    candidates: list[InputAssetCandidate] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, dict):
            continue
        for input_name, input_value in inputs.items():
            redaction = input_redaction_for(class_type, str(input_name), input_value)
            if redaction is None:
                continue
            raw_path = first_local_reference(input_value)
            source_path = resolve_local_input_path(raw_path, resolve_input_path) if raw_path else None
            extension_hint = redaction["extension_hint"]
            mime_type = redaction["mime_type_hint"]
            filename = safe_display_filename(Path(raw_path).name if raw_path else f"{redaction['expected_kind']} input")
            size_bytes: int | None = None
            selectable = False
            reason: str | None = "File could not be resolved from this ComfyUI input."
            if source_path is not None and source_path.is_file():
                try:
                    size_bytes = source_path.stat().st_size
                    selectable = size_bytes <= MAX_BUNDLED_INPUT_ASSET_BYTES
                    reason = None if selectable else "File is too large to bundle in a .noofy package."
                    filename = safe_display_filename(source_path.name)
                    extension_hint = source_path.suffix.lower() or extension_hint
                    mime_type = mimetypes.guess_type(source_path.name)[0] or mime_type
                except OSError:
                    selectable = False
                    reason = "File could not be read."
            candidate_id = input_asset_candidate_id(
                str(node_id),
                class_type,
                str(input_name),
                raw_path or "",
            )
            candidates.append(
                InputAssetCandidate(
                    id=candidate_id,
                    node_id=str(node_id),
                    node_type=class_type,
                    input_name=str(input_name),
                    expected_kind=redaction["expected_kind"],
                    source_path=source_path if selectable else None,
                    filename=filename,
                    extension=extension_hint,
                    mime_type=mime_type,
                    size_bytes=size_bytes,
                    selectable=selectable,
                    reason=reason,
                )
            )
    return candidates


def bundle_selected_input_assets(
    candidates: Iterable[InputAssetCandidate],
    selected_asset_ids: Iterable[str],
) -> dict[tuple[str, str], BundledInputAsset]:
    selected = {str(item) for item in selected_asset_ids}
    bundled: dict[tuple[str, str], BundledInputAsset] = {}
    total_size = 0
    for candidate in candidates:
        if candidate.id not in selected:
            continue
        if not candidate.selectable or candidate.source_path is None:
            continue
        total_size += candidate.size_bytes or 0
        if total_size > MAX_BUNDLED_INPUT_ASSET_BYTES:
            raise ValueError("Selected input assets are too large to bundle in a .noofy package.")
        reference, asset_id = package_asset_reference_for_candidate(candidate)
        bundled[(candidate.node_id, candidate.input_name)] = BundledInputAsset(
            candidate=candidate,
            asset_id=asset_id,
            reference=reference,
        )
    return bundled


def package_asset_reference_for_candidate(candidate: InputAssetCandidate) -> tuple[dict[str, Any], str]:
    if candidate.source_path is None:
        raise ValueError("Cannot package an unresolved input asset.")
    data = candidate.source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    suffix = candidate.source_path.suffix.lower() or candidate.extension or ".bin"
    stem = slugify(Path(candidate.filename).stem or candidate.expected_kind)
    asset_id = f"input-defaults/{digest[:16]}-{stem}{suffix}"
    reference = {
        "source": "package_asset",
        "asset_id": asset_id,
        "kind": package_asset_kind_for_candidate(candidate.expected_kind),
        "filename": candidate.filename,
        "content_type": candidate.mime_type or mimetypes.guess_type(candidate.filename)[0] or "application/octet-stream",
        "size_bytes": len(data),
        "sha256": f"sha256:{digest}",
    }
    return reference, asset_id


def package_asset_kind_for_candidate(expected_kind: str) -> str:
    return "file" if expected_kind == "text" else expected_kind


def redact_local_inputs_for_package(
    graph: dict[str, Any],
    bundled_input_assets: dict[tuple[str, str], BundledInputAsset] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    package_graph = copy.deepcopy(graph)
    adjustments = {
        "image_inputs_redacted": 0,
        "audio_inputs_redacted": 0,
        "video_inputs_redacted": 0,
        "three_d_inputs_redacted": 0,
        "text_inputs_redacted": 0,
        "file_inputs_redacted": 0,
    }
    unresolved_inputs: list[dict[str, Any]] = []

    for node_id, node in package_graph.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, dict):
            continue

        for input_name, input_value in list(inputs.items()):
            redaction = input_redaction_for(class_type, str(input_name), input_value)
            if redaction is None:
                continue

            expected_kind = redaction["expected_kind"]
            placeholder = redacted_input_value(expected_kind)
            inputs[input_name] = placeholder
            adjustment_key = f"{'three_d' if expected_kind == '3d' else expected_kind}_inputs_redacted"
            adjustments[adjustment_key] += 1
            if (str(node_id), str(input_name)) not in (bundled_input_assets or {}):
                unresolved_inputs.append(
                    {
                        "node_id": str(node_id),
                        "node_type": class_type,
                        "input_name": str(input_name),
                        "current_value": placeholder,
                        "reason": f"creator_local_{expected_kind}_not_bundled",
                        "expected_kind": expected_kind,
                        "required": redaction["required"],
                        "extension_hint": redaction["extension_hint"],
                        "mime_type_hint": redaction["mime_type_hint"],
                    }
                )

    return package_graph, adjustments, unresolved_inputs


def input_redaction_for(
    node_type: str,
    input_name: str,
    value: Any,
) -> dict[str, Any] | None:
    expected_kind = expected_input_kind(node_type, input_name, value)
    if expected_kind is None or not value_contains_local_reference(value):
        return None
    extension_hint = safe_extension_hint(value)
    return {
        "expected_kind": expected_kind,
        "required": True,
        "extension_hint": extension_hint,
        "mime_type_hint": safe_mime_type_hint(extension_hint, expected_kind),
    }


def expected_input_kind(node_type: str, input_name: str, value: Any) -> str | None:
    normalized_node = node_type.lower()
    normalized_input = input_name.lower()
    if node_type in LOCAL_IMAGE_NODE_TYPES and normalized_input == "image":
        return "image"
    if node_type in LOCAL_AUDIO_NODE_TYPES and normalized_input in {"audio", "file", "filename", "path", "audio_path"}:
        return "audio"
    if is_video_input_node_type(node_type) and normalized_input in {"video", "file", "filename", "path", "video_path"}:
        return "video"
    if node_type in LOCAL_THREE_D_NODE_TYPES and normalized_input in {"model", "mesh", "model_file", "file", "filename", "path", "model_path", "mesh_path"}:
        return "3d"
    if is_generic_file_input(node_type, input_name, value):
        return kind_from_path_like_value(value) or "file"
    if "text" in normalized_node and normalized_input in FILE_INPUT_NAMES:
        return "text"
    return None


def is_video_input_node_type(node_type: str) -> bool:
    normalized = node_type.lower()
    return node_type in LOCAL_VIDEO_NODE_TYPES or (
        "video" in normalized and any(token in normalized for token in ("load", "input", "import"))
    )


def is_generic_file_input(node_type: str, input_name: str, value: Any) -> bool:
    normalized_node = node_type.lower()
    normalized_input = input_name.lower()
    if any(media in normalized_node for media in ("image", "audio", "video", "3d", "mesh", "lora")):
        return False
    if any(model_token in normalized_node for model_token in ("checkpoint", "model", "controlnet", "embedding", "vae", "unet", "clip")):
        return False
    if normalized_input not in FILE_INPUT_NAMES and not any(
        token in normalized_input for token in ("file", "filepath", "file_path", "document", "archive", "subtitle")
    ):
        return False
    strong_node_signal = any(token in normalized_node for token in ("file", "load", "open", "import", "document", "archive", "json", "csv", "subtitle", "text"))
    return strong_node_signal or kind_from_path_like_value(value) is not None


def value_contains_local_reference(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip()) and not is_graph_link(value)
    if isinstance(value, dict):
        return any(value_contains_local_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(value_contains_local_reference(item) for item in value)
    return False


def first_local_reference(value: Any) -> str | None:
    if isinstance(value, str) and value.strip() and not is_graph_link(value):
        return value.strip()
    if isinstance(value, dict):
        for nested in value.values():
            found = first_local_reference(nested)
            if found is not None:
                return found
    if isinstance(value, list):
        for nested in value:
            found = first_local_reference(nested)
            if found is not None:
                return found
    return None


def resolve_local_input_path(
    value: str,
    resolve_input_path: Callable[[str], str | Path | None] | None,
) -> Path | None:
    if resolve_input_path is not None:
        try:
            resolved = resolve_input_path(value)
        except Exception:
            resolved = None
        if resolved:
            path = Path(resolved)
            if path.is_file():
                return path
    path = Path(value).expanduser()
    if path.is_file():
        return path
    return None


def safe_display_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(value.replace("\\", "/")).name).strip("._ ")
    return cleaned[:120] or "default.bin"


def input_asset_candidate_id(node_id: str, node_type: str, input_name: str, value: str) -> str:
    digest = hashlib.sha256(f"{node_id}\0{node_type}\0{input_name}\0{value}".encode("utf-8")).hexdigest()
    return f"asset-{digest[:20]}"


def is_graph_link(value: str) -> bool:
    return bool(re.fullmatch(r"\d+", value))


def kind_from_path_like_value(value: Any) -> str | None:
    suffix = safe_extension_hint(value)
    if suffix is None:
        return None
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in THREE_D_EXTENSIONS:
        return "3d"
    if suffix in TEXT_EXTENSIONS:
        return "text"
    return "file"


def safe_extension_hint(value: Any) -> str | None:
    if isinstance(value, dict):
        for nested in value.values():
            suffix = safe_extension_hint(nested)
            if suffix is not None:
                return suffix
        return None
    if isinstance(value, list):
        for nested in value:
            suffix = safe_extension_hint(nested)
            if suffix is not None:
                return suffix
        return None
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    suffix = candidate if candidate.startswith(".") else Path(candidate).suffix.lower()
    if re.fullmatch(r"\.[a-z0-9][a-z0-9_-]{0,15}", suffix):
        return suffix
    return None


def safe_mime_type_hint(extension: str | None, expected_kind: str) -> str | None:
    if extension:
        guessed = mimetypes.types_map.get(extension) or mimetypes.common_types.get(extension)
        if guessed:
            return guessed
    fallback = {
        "image": "image/*",
        "audio": "audio/*",
        "video": "video/*",
        "3d": "model/*",
        "text": "text/plain",
    }
    return fallback.get(expected_kind)


def redacted_input_value(kind: str) -> str:
    if kind == "image":
        return REDACTED_IMAGE_INPUT_VALUE
    safe_kind = "three_d" if kind == "3d" else kind
    return f"__noofy_runtime_{safe_kind}_input_required__"


def create_placeholder_thumbnail_bytes() -> bytes:
    from io import BytesIO

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (768, 768), (242, 244, 247))
    draw = ImageDraw.Draw(image)
    block = 96
    for y in range(0, 768, block):
        for x in range(0, 768, block):
            color = (232, 237, 243) if ((x // block) + (y // block)) % 2 else (248, 249, 250)
            draw.rectangle((x, y, x + block - 1, y + block - 1), fill=color)
    draw.rectangle((240, 240, 528, 528), outline=(82, 109, 130), width=5)
    draw.line((240, 240, 528, 528), fill=(82, 109, 130), width=5)
    draw.line((528, 240, 240, 528), fill=(82, 109, 130), width=5)
    buffer = BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def detect_custom_nodes(prompt: dict[str, Any], nodes_module: Any) -> list[CustomNodeRecord]:
    records_by_folder: dict[str, CustomNodeRecord] = {}
    mappings = getattr(nodes_module, "NODE_CLASS_MAPPINGS", {})
    loaded_dirs = getattr(nodes_module, "LOADED_MODULE_DIRS", {})

    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str):
            continue
        node_class = mappings.get(class_type)
        if node_class is None:
            continue

        python_module = getattr(node_class, "RELATIVE_PYTHON_MODULE", "nodes")
        if not isinstance(python_module, str) or not python_module.startswith("custom_nodes."):
            continue

        module_name = python_module.split(".", 1)[1].split(".", 1)[0]
        source_path = resolve_loaded_module_path(module_name, loaded_dirs)
        record = records_by_folder.get(module_name)
        if record is None:
            record = CustomNodeRecord(
                id=slugify(module_name),
                folder_name=module_name,
                source_path=os.path.abspath(source_path) if source_path else None,
            )
            records_by_folder[module_name] = record
        record.node_types.append(class_type)

    for record in records_by_folder.values():
        record.node_types = sorted(set(record.node_types))
        collect_custom_node_manifest(record)

    return sorted(records_by_folder.values(), key=lambda item: item.folder_name)


def resolve_loaded_module_path(module_name: str, loaded_dirs: dict[str, str]) -> str | None:
    exact = loaded_dirs.get(module_name)
    if exact:
        return exact

    for loaded_name, module_dir in loaded_dirs.items():
        loaded_path = Path(str(loaded_name))
        if loaded_path.name != module_name:
            continue

        file_candidate = loaded_path.with_suffix(".py")
        if file_candidate.exists():
            return str(file_candidate)

        dir_candidate = Path(module_dir) / module_name
        if dir_candidate.exists():
            return str(dir_candidate)

    return None


def collect_custom_node_manifest(record: CustomNodeRecord) -> None:
    if not record.source_path:
        record.warnings.append(f"Could not locate custom node folder for {record.folder_name}.")
        return

    source = Path(record.source_path)
    if not source.exists():
        record.warnings.append(f"Custom node folder does not exist: {record.source_path}")
        return

    if source.is_file():
        files = [source]
        root = source.parent
    else:
        root = source
        files = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in EXCLUDED_CUSTOM_NODE_DIRS]
            for filename in filenames:
                path = Path(dirpath) / filename
                if should_exclude_custom_node_file(path):
                    record.excluded_count += 1
                    continue
                files.append(path)

    file_manifest = []
    for path in sorted(files):
        rel_path = path.relative_to(root).as_posix()
        stat = path.stat()
        file_manifest.append(
            {
                "path": rel_path,
                "sha256": sha256_file(path),
                "size_bytes": stat.st_size,
            }
        )
        record.included_size_bytes += stat.st_size
        dependency_name = Path(rel_path).name
        if dependency_name in {"requirements.txt", "pyproject.toml", "setup.py"}:
            record.requirements_files.append(rel_path)
        if dependency_name == "install.py":
            record.has_install_py = True

    record.file_manifest = file_manifest
    record.requirements_files = sorted(set(record.requirements_files))
    record.sha256_manifest = sha256_bytes(canonical_json_bytes(file_manifest))
    record.included = bool(file_manifest)


def should_exclude_custom_node_file(path: Path) -> bool:
    if path.name in {".DS_Store"}:
        return True
    return path.suffix.lower() in EXCLUDED_CUSTOM_NODE_SUFFIXES


def detect_model_references(
    prompt: dict[str, Any],
    resolve_model_path: Callable[[str, str], str | None],
    hash_cache: ModelHashCache | None = None,
    *,
    metrics: VerifyHashMetrics | None = None,
    workflow: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    resolved_records: list[tuple[dict[str, Any], Path]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for node_id, node in sorted(prompt.items(), key=lambda item: node_sort_key(item[0])):
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(class_type, str) or not isinstance(inputs, dict):
            continue

        input_mapping = dict(MODEL_INPUTS.get(class_type, {}))
        for input_name, hint in MODEL_INPUT_NAME_HINTS.items():
            input_mapping.setdefault(input_name, hint)

        for input_name, (folder_name, model_type) in input_mapping.items():
            value = inputs.get(input_name)
            if not isinstance(value, str) or not value:
                continue

            resolved_folder = folder_name
            resolved_path = resolve_model_path(resolved_folder, value)
            if resolved_path is None and folder_name == "vae":
                vae_approx_path = resolve_model_path("vae_approx", value)
                if vae_approx_path is not None:
                    resolved_folder = "vae_approx"
                    resolved_path = vae_approx_path

            key = (str(node_id), class_type, input_name, value)
            if key in seen:
                continue
            seen.add(key)

            record = {
                "node_id": str(node_id),
                "input_name": input_name,
                "node_type": class_type,
                "model_type": model_type,
                "comfyui_folder": resolved_folder,
                "filename": value,
                "sha256": None,
                "size_bytes": None,
                "verification_level": MODEL_VERIFICATION_FILENAME_ONLY,
                "identity_verified_by_exporter": False,
                "local_file_available_at_export": False,
                "bundled": False,
                "asset_ownership": MODEL_ASSET_OWNERSHIP_EXTERNAL,
                "identity_warnings": [],
                "source_urls": [],
            }
            if resolved_path:
                resolved_records.append((record, Path(resolved_path)))
            else:
                record["identity_warnings"].append(
                    "ComfyUI did not resolve this model file at export time."
                )
            records.append(record)

    annotate_model_identities(resolved_records, hash_cache=hash_cache, metrics=metrics)
    attach_model_source_url_candidates(records, workflow)
    return records


def attach_model_source_url_candidates(
    records: list[dict[str, Any]],
    workflow: dict[str, Any] | None,
) -> None:
    if not records or not isinstance(workflow, dict):
        return
    candidates = collect_workflow_model_source_url_candidates(workflow)
    if not candidates:
        return
    for record in records:
        filename = record.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        node_id = record.get("node_id")
        matched_urls: list[str] = []
        for candidate in candidates:
            candidate_name = candidate.get("name")
            if isinstance(candidate_name, str) and os.path.basename(candidate_name) != os.path.basename(filename):
                continue
            candidate_node_id = candidate.get("node_id")
            if candidate_node_id is not None and node_id is not None and str(candidate_node_id) != str(node_id):
                continue
            for url in candidate.get("urls", []):
                if isinstance(url, str):
                    matched_urls.append(url)
        if matched_urls:
            record["source_urls"] = dedupe_strings(
                [*(record.get("source_urls") if isinstance(record.get("source_urls"), list) else []), *matched_urls]
            )


def collect_workflow_model_source_url_candidates(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    nodes = workflow.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = node.get("id")
            properties = node.get("properties")
            if not isinstance(properties, dict):
                continue
            for model in collect_model_property_dicts(properties):
                name = clean_metadata_text(
                    model.get("name")
                    or model.get("filename")
                    or model.get("model")
                    or model.get("model_name")
                )
                urls = collect_source_urls_from_value(model)
                if name and urls:
                    candidates.append({"node_id": node_id, "name": name, "urls": urls})
    return candidates


def collect_model_property_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        models = value.get("models")
        if isinstance(models, list):
            found.extend(item for item in models if isinstance(item, dict))
        if value.keys() & {"url", "source_url", "download_url", "source_urls"}:
            found.append(value)
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                found.extend(collect_model_property_dicts(nested))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                found.extend(collect_model_property_dicts(item))
    return found


def collect_source_urls_from_value(value: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("url", "source_url", "download_url"):
        url = sanitize_source_url(value.get(key))
        if url is not None:
            urls.append(url)
    raw_urls = value.get("source_urls")
    if isinstance(raw_urls, list):
        for item in raw_urls:
            url = sanitize_source_url(item)
            if url is not None:
                urls.append(url)
    return dedupe_strings(urls)


def sanitize_source_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if any(is_secret_query_key(key) for key, _value in parse_qsl(parts.query, keep_blank_values=True)):
        return None
    return urlunsplit(parts)


def is_secret_query_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in {
        "api_key",
        "apikey",
        "access_token",
        "token",
        "auth",
        "authorization",
        "secret",
        "password",
        "signature",
        "x_amz_signature",
        "x_goog_signature",
    }


def dedupe_strings(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def annotate_model_identities(
    records: list[tuple[dict[str, Any], Path]],
    *,
    hash_cache: ModelHashCache | None = None,
    metrics: VerifyHashMetrics | None = None,
) -> None:
    if not records:
        return

    concurrency, downgrade_reason = select_model_hash_concurrency([path for _record, path in records])
    started = time.monotonic()
    if concurrency <= 1:
        for record, path in records:
            annotate_model_identity(record, path, hash_cache=hash_cache, metrics=metrics)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    annotate_model_identity,
                    record,
                    path,
                    hash_cache=hash_cache,
                    metrics=metrics,
                )
                for record, path in records
            ]
            for future in futures:
                future.result()

    if metrics is not None:
        logging.info(
            "[Noofy Export] model verification completed",
            extra={
                "noofy_export": {
                    "model_count": len(records),
                    "selected_concurrency": concurrency,
                    "downgrade_reason": downgrade_reason,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                    **metrics.to_dict(),
                }
            },
        )


def select_model_hash_concurrency(paths: list[Path]) -> tuple[int, str]:
    if len(paths) <= 1:
        return 1, "single_model"
    try:
        configured = int(os.environ.get(MODEL_HASH_CONCURRENCY_ENV, DEFAULT_MODEL_HASH_CONCURRENCY))
    except ValueError:
        configured = DEFAULT_MODEL_HASH_CONCURRENCY
    if configured <= 1:
        return 1, "config_override"
    downgrade_reason = verification_filesystem_downgrade_reason(paths)
    if downgrade_reason is not None:
        return 1, downgrade_reason
    return max(1, min(configured, os.cpu_count() or 1, len(paths))), "none"


def verification_filesystem_downgrade_reason(paths: list[Path]) -> str | None:
    fallback: str | None = None
    roots = sorted({path.expanduser().resolve(strict=False).parent for path in paths})
    for root in roots:
        try:
            reason = filesystem_slow_reason(root)
        except Exception:
            reason = None
        if reason == "network_fs":
            return "network_fs"
        if reason is not None and fallback is None:
            fallback = reason
    return fallback


def filesystem_slow_reason(path: Path) -> str | None:
    mounts = read_linux_mounts()
    if not mounts:
        return None
    target = path.expanduser().resolve(strict=False)
    device, fstype = mount_for_path(target, mounts)
    if fstype is None:
        return None
    normalized_fstype = fstype.split(".", 1)[-1] if fstype.startswith("fuse.") else fstype
    if normalized_fstype in NETWORK_VERIFICATION_FILESYSTEM_TYPES or fstype in NETWORK_VERIFICATION_FILESYSTEM_TYPES:
        return "network_fs"
    if device and device_is_rotational(device):
        return "rotational"
    return None


def read_linux_mounts() -> list[tuple[str, str, str]]:
    try:
        raw = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 3:
            entries.append((unescape_mount_field(parts[0]), unescape_mount_field(parts[1]), parts[2]))
    return entries


def mount_for_path(target: Path, mounts: list[tuple[str, str, str]]) -> tuple[str | None, str | None]:
    best_device: str | None = None
    best_fstype: str | None = None
    best_len = -1
    for device, mount_point, fstype in mounts:
        mount_path = Path(mount_point)
        if (target == mount_path or is_relative_to(target, mount_path)) and len(mount_point) > best_len:
            best_len = len(mount_point)
            best_device = device
            best_fstype = fstype
    return best_device, best_fstype


def unescape_mount_field(value: str) -> str:
    if "\\" not in value:
        return value
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def device_is_rotational(device: str) -> bool:
    if not device.startswith("/dev/"):
        return False
    name = os.path.basename(device)
    candidates = [name]
    stripped = re.sub(r"p?\d+$", "", name)
    if stripped and stripped != name:
        candidates.append(stripped)
    for candidate in candidates:
        rotational_path = Path("/sys/block") / candidate / "queue" / "rotational"
        try:
            if rotational_path.exists():
                return rotational_path.read_text(encoding="utf-8").strip() == "1"
        except OSError:
            continue
    return False


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def annotate_model_identity(
    record: dict[str, Any],
    path: Path,
    *,
    hash_cache: ModelHashCache | None = None,
    metrics: VerifyHashMetrics | None = None,
) -> None:
    if not path.is_file():
        record["identity_warnings"].append(
            "ComfyUI resolved the model reference, but it was not a readable file."
        )
        return

    record["local_file_available_at_export"] = True
    stat: os.stat_result | None = None
    try:
        stat = path.stat()
        record["size_bytes"] = stat.st_size
    except OSError as exc:
        record["identity_warnings"].append(f"Could not read model file size: {exc}")

    cached_sha256 = hash_cache.get_valid_hash(path, stat) if hash_cache and stat else None
    if cached_sha256 is not None:
        if metrics is not None:
            metrics.record_cache_hit()
        record["sha256"] = cached_sha256
    else:
        try:
            record["sha256"] = sha256_file(path)
        except OSError as exc:
            record["identity_warnings"].append(
                f"Could not hash model file at export time: {exc}"
            )
        else:
            if metrics is not None and stat is not None:
                metrics.record_cache_miss(bytes_hashed=stat.st_size)
            if hash_cache is not None and stat is not None:
                hash_cache.remember_hash(path, stat, record["sha256"])

    if record["sha256"] and isinstance(record["size_bytes"], int):
        record["verification_level"] = MODEL_VERIFICATION_HASH_AND_SIZE
        record["identity_verified_by_exporter"] = True
    elif isinstance(record["size_bytes"], int):
        record["verification_level"] = MODEL_VERIFICATION_FILENAME_AND_SIZE
    else:
        record["verification_level"] = MODEL_VERIFICATION_FILENAME_ONLY


def collect_model_warnings(models: Iterable[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    for model in models:
        filename = model.get("filename")
        if not isinstance(filename, str):
            filename = "unknown"
        for warning in model.get("identity_warnings", []):
            if isinstance(warning, str) and warning:
                warnings.append(f"Model {filename}: {warning}")
    return warnings


def collect_runtime_metadata(
    comfyui_version: str,
    model_management: Any | None = None,
) -> RuntimeMetadata:
    device_type = None
    gpu_name = None
    pytorch_version = None
    if model_management is not None:
        try:
            device = model_management.get_torch_device()
            device_type = getattr(device, "type", None)
            gpu_name = model_management.get_torch_device_name(device)
        except Exception:
            device_type = None
        pytorch_version = getattr(model_management, "torch_version", None)

    return RuntimeMetadata(
        comfyui_version=comfyui_version,
        python_version=platform.python_version(),
        platform_name=normalize_platform(),
        gpu_backend=normalize_gpu_backend(device_type),
        gpu_name=gpu_name,
        pytorch_version=pytorch_version,
    )


def create_memory_sampler(model_management: Any | None = None) -> MemorySampler:
    def read_ram_used() -> int | None:
        try:
            import psutil

            memory = psutil.virtual_memory()
            return int(memory.total - memory.available)
        except Exception:
            return None

    def read_vram_used() -> int | None:
        if model_management is None:
            return None
        try:
            device = model_management.get_torch_device()
            if getattr(device, "type", None) in {"cpu", "mps"}:
                return None
            total = model_management.get_total_memory(device)
            free = model_management.get_free_memory(device)
            return int(total - free)
        except Exception:
            return None

    return MemorySampler(read_ram_used, read_vram_used)


def collect_history_output_declarations(
    history: dict[str, Any],
    graph: dict[str, Any],
) -> list[WorkflowOutputRecord]:
    outputs: list[WorkflowOutputRecord] = []
    seen: set[tuple[str, str]] = set()
    graph_nodes = {str(node_id): node for node_id, node in graph.items() if isinstance(node, dict)}

    for prompt_history in history.values():
        prompt_outputs = prompt_history.get("outputs", {})
        if not isinstance(prompt_outputs, dict):
            continue
        for node_id, node_output in sorted(prompt_outputs.items(), key=lambda item: node_sort_key(item[0])):
            if not isinstance(node_output, dict):
                continue
            node_id_str = str(node_id)
            node_type = str(graph_nodes.get(node_id_str, {}).get("class_type") or "UnknownNode")
            for kind in output_kinds_from_node_output(node_output):
                key = (node_id_str, kind)
                if key in seen:
                    continue
                seen.add(key)
                outputs.append(
                    WorkflowOutputRecord(
                        id=stable_output_id(node_id_str, kind),
                        label=generic_output_label(kind),
                        node_id=node_id_str,
                        node_type=node_type,
                        kind=kind,
                    )
                )

    return outputs


def node_sort_key(node_id: Any) -> tuple[int, int | str]:
    node_id_str = str(node_id)
    return (0, int(node_id_str)) if node_id_str.isdigit() else (1, node_id_str)


def output_kinds_from_node_output(node_output: dict[str, Any]) -> list[str]:
    kinds: list[str] = []
    for bucket_name, raw_items in node_output.items():
        bucket_kind = kind_from_output_bucket(bucket_name)
        if bucket_name == "text" and isinstance(raw_items, (str, list, tuple)):
            kinds.append("text")
            continue
        if bucket_name == "result" and raw_items:
            kinds.append("file")
            continue
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if isinstance(item, dict):
                kinds.append(classify_history_output_item(item, bucket_name))
            elif bucket_kind is not None:
                kinds.append(bucket_kind)
    return [kind for kind in MEDIA_KINDS_IN_ORDER if kind in set(kinds)]


MEDIA_KINDS_IN_ORDER = ("image", "audio", "video", "3d", "text", "file")


def classify_history_output_item(item: dict[str, Any], bucket_name: str) -> str:
    explicit_kind = item.get("kind")
    if explicit_kind in MEDIA_KINDS:
        return str(explicit_kind)

    content_type = str(item.get("mime_type") or item.get("content_type") or "").lower()
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("audio/"):
        return "audio"
    if content_type.startswith("video/"):
        return "video"
    if content_type.startswith("model/"):
        return "3d"
    if content_type.startswith("text/"):
        return "text"
    if content_type and content_type != "application/octet-stream":
        return "file"

    filename = item.get("filename")
    if isinstance(filename, str):
        by_extension = kind_from_path_like_value(filename)
        if by_extension is not None:
            return by_extension

    return kind_from_output_bucket(bucket_name) or "file"


def kind_from_output_bucket(bucket_name: str) -> str | None:
    normalized = bucket_name.lower()
    if normalized in {"images", "image", "gifs"}:
        return "image"
    if normalized in {"audio", "audios"}:
        return "audio"
    if normalized in {"video", "videos", "animated"}:
        return "video"
    if normalized in {"3d", "model_3d", "models_3d", "mesh", "meshes"}:
        return "3d"
    if normalized == "text":
        return "text"
    if normalized in {"file", "files", "documents"}:
        return "file"
    return None


def stable_output_id(node_id: str, kind: str) -> str:
    return slugify(f"{kind}-{node_id}", fallback=f"{kind}-output")


def generic_output_label(kind: str) -> str:
    labels = {
        "image": "Image Output",
        "audio": "Audio Output",
        "video": "Video Output",
        "3d": "3D Output",
        "text": "Text Output",
        "file": "File Output",
    }
    return labels.get(kind, "Workflow Output")


def create_thumbnail_bytes(source: Path | None = None, *, allow_generated_source: bool = False) -> bytes:
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    if source is None or not allow_generated_source or not source.exists() or kind_from_path_like_value(str(source)) != "image":
        return create_placeholder_thumbnail_bytes()

    try:
        with Image.open(source) as image:
            image = image.convert("RGB")
            image.thumbnail((768, 768))
            buffer = BytesIO()
            image.save(buffer, "PNG", optimize=True)
            return buffer.getvalue()
    except (OSError, UnidentifiedImageError):
        return create_placeholder_thumbnail_bytes()


def build_package_id(workflow_name: str | None, graph_sha256: str) -> str:
    if workflow_name:
        return slugify(workflow_name)
    return f"workflow-{graph_sha256[:12]}"


def build_package_documents(
    *,
    graph: dict[str, Any],
    workflow_name: str | None,
    runtime: RuntimeMetadata,
    custom_nodes: list[CustomNodeRecord],
    models: list[dict[str, Any]],
    outputs: list[WorkflowOutputRecord] | None = None,
    unresolved_runtime_inputs: list[dict[str, Any]] | None = None,
    hardware: MemoryObservation,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    graph_adjustments: dict[str, Any],
    warnings: list[str],
    bundled_input_assets: dict[tuple[str, str], BundledInputAsset] | None = None,
    export_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    graph_hash = sha256_bytes(canonical_json_bytes(graph))
    metadata_name = (
        clean_metadata_text(export_metadata.get("name"))
        if isinstance(export_metadata, dict)
        else None
    )
    package_id = build_package_id(metadata_name or workflow_name, graph_hash)
    package_version = "0.1.0"
    package_metadata = normalize_discovery_metadata(
        package_id=package_id,
        version=package_version,
        workflow_name=workflow_name,
        export_metadata=export_metadata,
    )

    package_json = {
        "schema_version": SCHEMA_VERSION,
        "publisher_id": "unknown",
        "package_id": package_id,
        "version": package_version,
        "source": "comfyui_noofy_export_extension",
        "trust_level": TRUST_LEVEL,
        "created_at": finished_at,
        "exporter": {"name": EXPORTER_NAME, "version": EXPORTER_VERSION},
        "engine": {
            "type": "comfyui",
            "graph_format": "comfyui_api_prompt",
            "comfyui_version": runtime.comfyui_version,
            "version_lock": True,
        },
        "unresolved_runtime_inputs": unresolved_runtime_inputs or [],
    }
    apply_metadata_mirrors(package_json, package_metadata)
    assert_metadata_mirrors_consistent(package_json)

    bundled_assets = list((bundled_input_assets or {}).values())
    dashboard_inputs = [dashboard_input_for_bundled_asset(asset) for asset in bundled_assets]
    dashboard_json = {
        "version": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "status": "not_configured",
        "inputs": dashboard_inputs,
        "outputs": [record.to_dict() for record in outputs or []],
        "sections": [],
        "controls": [],
        "notes": "Dashboard layout is configured inside Noofy creator mode.",
    }

    capsule_lock = {
        "schema_version": SCHEMA_VERSION,
        "engine": {
            "type": "comfyui",
            "comfyui_version": runtime.comfyui_version,
            "python_version": runtime.python_version,
            "platform": runtime.platform_name,
            "gpu_backend": runtime.gpu_backend,
        },
        "graph": {
            "format": "comfyui_api_prompt",
            "sha256": graph_hash,
        },
        "custom_nodes": [record.lock_entry() for record in custom_nodes],
        "models": models,
        "hardware_observations": hardware.to_dict(),
        "trust": {
            "level": TRUST_LEVEL,
            "publisher": "unknown",
            "signatures": [],
        },
    }

    export_report = {
        "schema_version": SCHEMA_VERSION,
        "export_status": "success",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(duration_seconds, 3),
        "test_run": {
            "status": "passed",
            "input_image_mode": TEST_INPUT_MODE,
            "batch_size": TEST_BATCH_SIZE,
            "graph_adjustments": graph_adjustments,
        },
        "detected": {
            "core_nodes_count": count_core_nodes(graph, custom_nodes),
            "custom_nodes_count": count_custom_node_instances(graph, custom_nodes),
            "models_count": len(models),
            "custom_node_packages_count": len(custom_nodes),
            "input_assets_included_count": len(bundled_assets),
        },
        "runtime": {
            "comfyui_version": runtime.comfyui_version,
            "python_version": runtime.python_version,
            "platform": runtime.platform_name,
            "gpu_backend": runtime.gpu_backend,
            "gpu_name": runtime.gpu_name,
            "pytorch_version": runtime.pytorch_version,
        },
        "warnings": warnings,
    }

    return {
        "package_id": package_id,
        "package_json": package_json,
        "dashboard_json": dashboard_json,
        "capsule_lock": capsule_lock,
        "export_report": export_report,
    }


def count_core_nodes(graph: dict[str, Any], custom_nodes: list[CustomNodeRecord]) -> int:
    custom_types = {node_type for record in custom_nodes for node_type in record.node_types}
    total = 0
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type not in custom_types:
            total += 1
    return total


def dashboard_input_for_bundled_asset(asset: BundledInputAsset) -> dict[str, Any]:
    candidate = asset.candidate
    control = {
        "image": "load_image",
        "audio": "load_audio",
        "video": "load_video",
        "3d": "load_3d",
    }.get(candidate.expected_kind, "load_file")
    validation: dict[str, Any] = {}
    if control == "load_file" and candidate.extension:
        validation["accepted_extensions"] = [candidate.extension]
    return {
        "id": f"input-{candidate.node_id}-{candidate.input_name}",
        "label": friendly_input_label(candidate.input_name, candidate.expected_kind),
        "control": control,
        "binding": {"node_id": candidate.node_id, "input_name": candidate.input_name},
        "default": asset.reference,
        "default_pinned": True,
        "validation": validation,
    }


def friendly_input_label(input_name: str, kind: str) -> str:
    name = input_name.replace("_", " ").strip()
    if not name:
        return f"{kind.title()} input"
    return name[:1].upper() + name[1:]


def count_custom_node_instances(graph: dict[str, Any], custom_nodes: list[CustomNodeRecord]) -> int:
    custom_types = {node_type for record in custom_nodes for node_type in record.node_types}
    total = 0
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type in custom_types:
            total += 1
    return total


def write_noofy_package(
    *,
    target_path: Path,
    graph: dict[str, Any],
    documents: dict[str, Any],
    custom_nodes: list[CustomNodeRecord],
    thumbnail_bytes: bytes,
    bundled_input_assets: dict[tuple[str, str], BundledInputAsset] | None = None,
    workflow: dict[str, Any] | None = None,
    workflow_widget_bindings: dict[str, Any] | None = None,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("package.json", pretty_json_bytes(documents["package_json"]))
        package.writestr("comfyui_graph.json", pretty_json_bytes(graph))
        if workflow is not None and workflow_widget_bindings is not None:
            package.writestr("comfyui_workflow.json", pretty_json_bytes(workflow))
            package.writestr(
                "comfyui_workflow_bindings.json",
                pretty_json_bytes(workflow_widget_bindings),
            )
        package.writestr("dashboard.json", pretty_json_bytes(documents["dashboard_json"]))
        package.writestr("capsule.lock.json", pretty_json_bytes(documents["capsule_lock"]))
        package.writestr("export-report.json", pretty_json_bytes(documents["export_report"]))
        package.writestr("assets/thumbnail.png", thumbnail_bytes)

        for asset in (bundled_input_assets or {}).values():
            if asset.candidate.source_path is None:
                continue
            package.write(asset.candidate.source_path, f"assets/{asset.asset_id}")
            package.writestr(
                f"assets/{asset.asset_id}.meta.json",
                pretty_json_bytes(asset.reference),
            )

        for record in custom_nodes:
            if record.included and record.source_path:
                add_custom_node_to_zip(package, record)


def add_custom_node_to_zip(package: zipfile.ZipFile, record: CustomNodeRecord) -> None:
    source = Path(record.source_path or "")
    root = source.parent if source.is_file() else source
    archive_root = f"custom_nodes/{record.folder_name}"

    package.writestr(
        f"{archive_root}/.noofy-file-manifest.json",
        pretty_json_bytes(
            {
                "schema_version": SCHEMA_VERSION,
                "sha256_manifest": record.sha256_manifest,
                "files": record.file_manifest,
                "excluded_count": record.excluded_count,
            }
        ),
    )

    for item in record.file_manifest:
        rel_path = item["path"]
        package.write(root / rel_path, f"{archive_root}/{rel_path}")


def build_export_filename(display_name: str) -> str:
    filename_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", display_name.strip()).strip("-._")
    return f"{filename_stem or 'workflow'}.noofy"


def output_export_path(output_directory: str, filename: str) -> Path:
    return Path(output_directory).resolve() / "noofy_exports" / filename


def flatten_warnings(custom_nodes: Iterable[CustomNodeRecord], warnings: Iterable[str]) -> list[str]:
    out = list(warnings)
    for record in custom_nodes:
        out.extend(record.warnings)
        if record.has_install_py:
            out.append(
                f"Custom node folder {record.folder_name} contains install.py; Noofy import must not execute it silently."
            )
        if record.excluded_count:
            out.append(
                f"Custom node folder {record.folder_name} had {record.excluded_count} excluded files or cache entries."
            )
    return out
