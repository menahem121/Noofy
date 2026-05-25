from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import platform
import re
import sys
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


EXPORTER_NAME = "Noofy ComfyUI Export Extension"
EXPORTER_VERSION = "0.1.0"
SCHEMA_VERSION = "0.1.0"
TRUST_LEVEL = "public_unverified"
TEST_INPUT_MODE = "workflow_current_load_image_inputs"
TEST_BATCH_SIZE = 1
LOCAL_IMAGE_NODE_TYPES = {"LoadImage", "LoadImageMask"}
REDACTED_IMAGE_INPUT_VALUE = "__noofy_runtime_image_input_required__"
MODEL_VERIFICATION_HASH_AND_SIZE = "sha256_size"
MODEL_VERIFICATION_FILENAME_AND_SIZE = "filename_size"
MODEL_VERIFICATION_FILENAME_ONLY = "filename_only"
MODEL_ASSET_OWNERSHIP_EXTERNAL = "external_reference"
MODEL_HASH_CACHE_SCHEMA_VERSION = 1
MODEL_HASH_CACHE_MAX_ENTRIES = 4096
MODEL_HASH_CACHE_SAMPLE_BYTES = 1024 * 1024


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


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def pretty_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024 * 8) -> str:
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


def redact_local_image_inputs_for_package(
    graph: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    package_graph = copy.deepcopy(graph)
    adjustments = {"image_inputs_redacted": 0}

    for node in package_graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        inputs = node.get("inputs")
        if class_type not in LOCAL_IMAGE_NODE_TYPES or not isinstance(inputs, dict):
            continue

        image_value = inputs.get("image")
        if isinstance(image_value, str) and image_value:
            inputs["image"] = REDACTED_IMAGE_INPUT_VALUE
            adjustments["image_inputs_redacted"] += 1

    return package_graph, adjustments


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
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for node_id, node in sorted(prompt.items(), key=lambda item: str(item[0])):
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
                annotate_model_identity(record, Path(resolved_path), hash_cache=hash_cache)
            else:
                record["identity_warnings"].append(
                    "ComfyUI did not resolve this model file at export time."
                )
            records.append(record)

    return records


def annotate_model_identity(
    record: dict[str, Any],
    path: Path,
    *,
    hash_cache: ModelHashCache | None = None,
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
        record["sha256"] = cached_sha256
    else:
        try:
            record["sha256"] = sha256_file(path)
        except OSError as exc:
            record["identity_warnings"].append(
                f"Could not hash model file at export time: {exc}"
            )
        else:
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


def collect_history_output_paths(history: dict[str, Any], get_directory_by_type: Callable[[str], str | None]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()

    for prompt_history in history.values():
        outputs = prompt_history.get("outputs", {})
        if not isinstance(outputs, dict):
            continue
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for items in node_output.values():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    item_type = item.get("type")
                    filename = item.get("filename")
                    if item_type not in {"output", "temp"} or not filename:
                        continue
                    base = get_directory_by_type(item_type)
                    if not base:
                        continue
                    base_path = Path(base).resolve()
                    full_path = (base_path / item.get("subfolder", "") / filename).resolve()
                    try:
                        full_path.relative_to(base_path)
                    except ValueError:
                        continue
                    if full_path.exists() and full_path not in seen:
                        seen.add(full_path)
                        paths.append(full_path)

    return paths


def create_thumbnail_bytes(source: Path | None) -> bytes:
    from io import BytesIO

    from PIL import Image

    if source is None or not source.exists():
        return create_placeholder_thumbnail_bytes()

    with Image.open(source) as image:
        image = image.convert("RGB")
        image.thumbnail((768, 768))
        buffer = BytesIO()
        image.save(buffer, "PNG", optimize=True)
        return buffer.getvalue()


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
    hardware: MemoryObservation,
    started_at: str,
    finished_at: str,
    duration_seconds: float,
    graph_adjustments: dict[str, Any],
    warnings: list[str],
) -> dict[str, Any]:
    graph_hash = sha256_bytes(canonical_json_bytes(graph))
    package_id = build_package_id(workflow_name, graph_hash)
    display_name = workflow_name or "Exported ComfyUI Workflow"

    package_json = {
        "schema_version": SCHEMA_VERSION,
        "publisher_id": "unknown",
        "package_id": package_id,
        "version": "0.1.0",
        "display_name": display_name,
        "description": "",
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
    }

    dashboard_json = {
        "schema_version": SCHEMA_VERSION,
        "status": "not_configured",
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
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("package.json", pretty_json_bytes(documents["package_json"]))
        package.writestr("comfyui_graph.json", pretty_json_bytes(graph))
        package.writestr("dashboard.json", pretty_json_bytes(documents["dashboard_json"]))
        package.writestr("capsule.lock.json", pretty_json_bytes(documents["capsule_lock"]))
        package.writestr("export-report.json", pretty_json_bytes(documents["export_report"]))
        package.writestr("assets/thumbnail.png", thumbnail_bytes)

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


def build_export_filename(package_id: str) -> str:
    return f"{slugify(package_id)}.noofy"


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
