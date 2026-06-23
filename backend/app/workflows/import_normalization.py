from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Iterator

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.workflows.package import (
    DashboardSchema,
    RequiredModel,
    SignedRegistryMetadata,
    UnresolvedRuntimeInput,
    WorkflowCustomNodeRecord,
    WorkflowInput,
    WorkflowPackageSignature,
)
from app.workflows.package_assets import is_package_asset_value

NOOFY_ARCHIVE_SCHEMA_VERSION = "0.1.0"
LOCAL_IMAGE_NODE_TYPES = {"LoadImage", "LoadImageMask"}
LOCAL_AUDIO_NODE_TYPES = {"LoadAudio"}
LOCAL_VIDEO_NODE_TYPES = {"LoadVideo", "VHS_LoadVideo", "VHS_LoadVideoPath"}
LOCAL_THREE_D_NODE_TYPES = {"Load3D", "Load3DAnimation"}
WORKFLOW_MEDIA_KINDS = frozenset({"image", "audio", "video", "3d", "text", "file"})
MULTIMODAL_MEDIA_INPUT_NAMES = frozenset({"image", "audio", "video", "model_file"})
FILE_INPUT_NAMES = frozenset(
    {
        "file",
        "filename",
        "path",
        "file_path",
        "filepath",
        "source",
        "input",
        "audio",
        "video",
        "image",
        "model_file",
        "text_file",
    }
)
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".aiff", ".aif", ".opus"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".gif"})
THREE_D_EXTENSIONS = frozenset({".glb", ".gltf", ".obj", ".fbx", ".stl", ".ply", ".usdz", ".dae", ".spz", ".splat", ".ksplat"})
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".srt", ".vtt"})
UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS = {
    "comfyui_launch_options",
    "launch_config",
    "launch_options",
    "runner_launch_options",
}
MODEL_SELECTOR_EXTENSIONS = frozenset(
    {
        ".bin",
        ".ckpt",
        ".gguf",
        ".onnx",
        ".pt",
        ".pth",
        ".safetensors",
        ".sft",
    }
)
KNOWN_GRAPH_MODEL_SELECTOR_INPUTS: dict[tuple[str, str], tuple[str, str]] = {
    ("ACN_ControlNet++LoaderAdvanced", "name"): ("controlnet", "controlnet"),
    ("ACN_ControlNet++LoaderSingle", "name"): ("controlnet", "controlnet"),
    ("ACN_ControlNetLoaderAdvanced", "cnet"): ("controlnet", "controlnet"),
    ("ACN_DiffControlNetLoaderAdvanced", "cnet"): ("controlnet", "controlnet"),
    ("ADE_AnimateDiffLoRALoader", "name"): (
        "animatediff_motion_lora",
        "animatediff_motion_lora",
    ),
    ("ADE_LoadAnimateDiffModel", "model_name"): (
        "animatediff_models",
        "animatediff_model",
    ),
    ("CheckpointLoader", "ckpt_name"): ("checkpoints", "checkpoint"),
    ("CheckpointLoaderSimple", "ckpt_name"): ("checkpoints", "checkpoint"),
    ("CLIPLoader", "clip_name"): ("text_encoders", "text_encoder"),
    ("CLIPLoaderGGUF", "clip_name"): ("clip", "clip"),
    ("CLIPVisionLoader", "clip_name"): ("clip_vision", "clip_vision"),
    ("ControlNetLoader", "control_net_name"): ("controlnet", "controlnet"),
    ("DualCLIPLoaderGGUF", "clip_name1"): ("clip", "clip"),
    ("DualCLIPLoaderGGUF", "clip_name2"): ("clip", "clip"),
    ("IPAdapterModelLoader", "ipadapter_file"): ("ipadapter", "ipadapter"),
    ("LoraLoader", "lora_name"): ("loras", "lora"),
    ("LoraLoaderModelOnly", "lora_name"): ("loras", "lora"),
    ("LoadBackgroundRemovalModel", "bg_removal_name"): (
        "background_removal",
        "background_removal",
    ),
    ("ONNXDetectorProvider", "model_name"): ("onnx", "onnx_detector"),
    ("QuadrupleCLIPLoaderGGUF", "clip_name1"): ("clip", "clip"),
    ("QuadrupleCLIPLoaderGGUF", "clip_name2"): ("clip", "clip"),
    ("QuadrupleCLIPLoaderGGUF", "clip_name3"): ("clip", "clip"),
    ("QuadrupleCLIPLoaderGGUF", "clip_name4"): ("clip", "clip"),
    ("SAMLoader", "model_name"): ("sams", "sam"),
    ("TripleCLIPLoaderGGUF", "clip_name1"): ("clip", "clip"),
    ("TripleCLIPLoaderGGUF", "clip_name2"): ("clip", "clip"),
    ("TripleCLIPLoaderGGUF", "clip_name3"): ("clip", "clip"),
    ("UnetLoaderGGUF", "unet_name"): ("diffusion_models", "diffusion_model"),
    ("UnetLoaderGGUFAdvanced", "unet_name"): (
        "diffusion_models",
        "diffusion_model",
    ),
    ("UNETLoader", "unet_name"): ("diffusion_models", "diffusion_model"),
    ("UpscaleModelLoader", "model_name"): ("upscale_models", "upscale_model"),
    ("VAELoader", "vae_name"): ("vae", "vae"),
}
KNOWN_UI_WIDGET_INPUT_ORDERS: dict[str, tuple[str, ...]] = {
    "ACN_ControlNet++LoaderAdvanced": ("name",),
    "ACN_ControlNet++LoaderSingle": ("name",),
    "ACN_ControlNetLoaderAdvanced": ("cnet",),
    "ACN_DiffControlNetLoaderAdvanced": ("cnet",),
    "ADE_AnimateDiffLoRALoader": ("name",),
    "ADE_LoadAnimateDiffModel": ("model_name",),
    "CheckpointLoader": ("ckpt_name",),
    "CheckpointLoaderSimple": ("ckpt_name",),
    "CLIPLoader": ("clip_name", "type", "device"),
    "CLIPLoaderGGUF": ("clip_name",),
    "CLIPTextEncode": ("text",),
    "CLIPVisionLoader": ("clip_name",),
    "ControlNetLoader": ("control_net_name",),
    "DualCLIPLoader": ("clip_name1", "clip_name2", "type", "device"),
    "DualCLIPLoaderGGUF": ("clip_name1", "clip_name2", "type"),
    "EmptyLatentImage": ("width", "height", "batch_size"),
    "IPAdapterModelLoader": ("ipadapter_file",),
    "KSampler": (
        "seed",
        "steps",
        "cfg",
        "sampler_name",
        "scheduler",
        "denoise",
    ),
    "LoraLoader": ("lora_name", "strength_model", "strength_clip"),
    "LoraLoaderModelOnly": ("lora_name", "strength_model"),
    "LoadBackgroundRemovalModel": ("bg_removal_name",),
    "LoadImage": ("image",),
    "ONNXDetectorProvider": ("model_name",),
    "QuadrupleCLIPLoaderGGUF": (
        "clip_name1",
        "clip_name2",
        "clip_name3",
        "clip_name4",
        "type",
    ),
    "SAMLoader": ("model_name",),
    "SaveImage": ("filename_prefix",),
    "TripleCLIPLoader": ("clip_name1", "clip_name2", "clip_name3"),
    "TripleCLIPLoaderGGUF": ("clip_name1", "clip_name2", "clip_name3"),
    "UltralyticsDetectorProvider": ("model_name",),
    "UnetLoaderGGUF": ("unet_name",),
    "UnetLoaderGGUFAdvanced": ("unet_name",),
    "UNETLoader": ("unet_name", "weight_dtype"),
    "UpscaleModelLoader": ("model_name",),
    "VAELoader": ("vae_name",),
}
ULTRALYTICS_DETECTOR_SELECTOR = ("UltralyticsDetectorProvider", "model_name")
ULTRALYTICS_DETECTOR_PREFIX_FOLDERS = {
    "bbox": ("ultralytics_bbox", "ultralytics_bbox"),
    "segm": ("ultralytics_segm", "ultralytics_segm"),
}


class ImportNormalizationError(RuntimeError):
    """Raised when normalized archive metadata has unsupported app semantics."""


def string_field(data: dict[str, Any], key: str, *, fallback: str) -> str:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def optional_string_field(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_source_urls(value: Any, *, fallback: Any = None) -> list[str]:
    urls: list[str] = []

    def add(candidate: str) -> None:
        cleaned = candidate.strip()
        if cleaned:
            urls.append(cleaned)

    def add_value(raw: Any) -> None:
        if isinstance(raw, str):
            add(raw)
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if isinstance(item, str):
                add(item)

    add_value(value)
    add_value(fallback)

    normalized: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def normalized_display_name(data: dict[str, Any], *, fallback: str) -> str:
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        nested_display_name = metadata.get("display_name")
        if isinstance(nested_display_name, str) and nested_display_name.strip():
            value = nested_display_name.strip()
        else:
            value = string_field(data, "display_name", fallback="").strip() or None
            if value is None:
                nested_name = metadata.get("name")
                value = nested_name.strip() if isinstance(nested_name, str) and nested_name.strip() else None
    else:
        value = None
    if value is None:
        value = string_field(data, "display_name", fallback=fallback)
    cleaned = value.lstrip("*").strip()
    return cleaned or fallback


def normalize_trust_level(value: Any) -> str:
    if value == "noofy_verified":
        return "noofy_verified"
    if value == "registry_locked":
        return "registry_locked"
    if value in {"public_unverified", "quarantined_community"}:
        return "quarantined_community"
    return "unsupported"


def normalize_signatures(value: Any) -> list[WorkflowPackageSignature]:
    if not isinstance(value, list):
        return []
    signatures: list[WorkflowPackageSignature] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key_id = item.get("key_id")
        algorithm = item.get("algorithm")
        signature_value = item.get("value")
        if not all(
            isinstance(field, str) and field.strip()
            for field in (key_id, algorithm, signature_value)
        ):
            continue
        signatures.append(
            WorkflowPackageSignature(
                key_id=key_id.strip(),
                algorithm=algorithm.strip(),
                value=signature_value.strip(),
            )
        )
    return signatures


def normalize_signed_registry_metadata(value: Any) -> SignedRegistryMetadata | None:
    if not isinstance(value, dict):
        return None
    registry_id = value.get("registry_id")
    snapshot_hash = value.get("snapshot_hash")
    signature = value.get("signature")
    if not all(
        isinstance(field, str) and field.strip()
        for field in (registry_id, snapshot_hash, signature)
    ):
        return None
    return SignedRegistryMetadata(
        registry_id=registry_id.strip(),
        snapshot_hash=snapshot_hash.strip(),
        signature=signature.strip(),
        key_id=optional_string_field(value, "key_id"),
        algorithm=optional_string_field(value, "algorithm"),
    )


def normalize_models(capsule_json: dict[str, Any]) -> list[RequiredModel]:
    models = capsule_json.get("models", [])
    if not isinstance(models, list):
        return []

    normalized: list[RequiredModel] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        folder = string_field(model, "comfyui_folder", fallback="")
        filename = string_field(model, "filename", fallback="")
        if not folder or not filename:
            continue
        source_urls = normalize_source_urls(
            model.get("source_urls"),
            fallback=model.get("source_url"),
        )
        sha256 = model.get("sha256")
        checksum = None
        if isinstance(sha256, str) and sha256:
            checksum = sha256 if sha256.startswith("sha256:") else f"sha256:{sha256}"
        size_bytes_value = model.get("size_bytes")
        size_bytes = size_bytes_value if isinstance(size_bytes_value, int) else None
        model_type = model.get("model_type")
        node_id = model.get("node_id")
        node_type = model.get("node_type")
        input_name = model.get("input_name")
        identity_verified = model.get("identity_verified_by_exporter")
        local_file_available = model.get("local_file_available_at_export")
        bundled = model.get("bundled")
        architecture_family = optional_string_field(model, "architecture_family")
        architecture_family_confidence = optional_string_field(model, "architecture_family_confidence")
        architecture_family_source = optional_string_field(model, "architecture_family_source")
        identity_warnings = [
            item for item in model.get("identity_warnings", []) if isinstance(item, str)
        ]
        normalized.append(
            RequiredModel(
                folder=folder,
                filename=filename,
                node_id=node_id.strip() if isinstance(node_id, str) and node_id.strip() else None,
                node_type=node_type.strip() if isinstance(node_type, str) and node_type.strip() else None,
                input_name=input_name.strip() if isinstance(input_name, str) and input_name.strip() else None,
                source_url=source_urls[0] if source_urls else None,
                source_urls=source_urls,
                checksum=checksum,
                model_type=model_type if isinstance(model_type, str) else None,
                size_bytes=size_bytes,
                verification_level=normalize_model_verification_level(
                    model,
                    checksum=checksum,
                    size_bytes=size_bytes,
                ),
                identity_verified_by_exporter=(
                    identity_verified
                    if isinstance(identity_verified, bool)
                    else checksum is not None and size_bytes is not None
                ),
                local_file_available_at_export=(
                    local_file_available
                    if isinstance(local_file_available, bool)
                    else None
                ),
                bundled=bundled if isinstance(bundled, bool) else False,
                asset_ownership=normalize_asset_ownership(
                    model.get("asset_ownership")
                ),
                identity_warnings=identity_warnings,
                architecture_family=architecture_family,
                architecture_family_confidence=architecture_family_confidence,
                architecture_family_source=architecture_family_source,
            )
        )
    return normalized


def model_source_urls_from_comfyui_workflow(
    comfyui_workflow: dict[str, Any],
) -> dict[tuple[str, str], list[str]]:
    return {
        (model.folder, model.filename): list(model.source_urls)
        for model in required_models_from_comfyui_workflow(comfyui_workflow)
        if model.source_urls
    }


def required_models_from_comfyui_workflow(
    comfyui_workflow: dict[str, Any],
    *,
    comfyui_graph: dict[str, Any] | None = None,
) -> list[RequiredModel]:
    graph_bindings = _comfyui_graph_model_bindings(comfyui_graph or {})
    models: list[RequiredModel] = []
    seen: set[tuple[str | None, str | None, str, str]] = set()
    source_urls_by_target: dict[tuple[str, str], list[str]] = {}
    for node in _iter_comfyui_workflow_nodes(comfyui_workflow):
        node_id = _node_id_string(node.get("id"))
        node_type = optional_string_field(node, "type")
        properties = node.get("properties")
        if not isinstance(properties, dict):
            continue
        model_entries = properties.get("models")
        if not isinstance(model_entries, list):
            continue
        for entry in model_entries:
            if not isinstance(entry, dict):
                continue
            folder = optional_string_field(entry, "directory")
            filename = optional_string_field(entry, "name")
            if not folder or not filename:
                continue
            source_urls = normalize_source_urls(
                entry.get("source_urls"),
                fallback=entry.get("url"),
            )
            if source_urls and (folder, filename) not in source_urls_by_target:
                source_urls_by_target[(folder, filename)] = list(source_urls)
            graph_node_id, input_name = _matching_graph_model_binding(
                graph_bindings,
                node_id=node_id,
                node_type=node_type,
                filename=filename,
            )
            resolved_node_id = graph_node_id or node_id
            key = (resolved_node_id, input_name, folder, filename)
            if key in seen:
                continue
            seen.add(key)
            models.append(
                RequiredModel(
                    folder=folder,
                    filename=filename,
                    node_id=resolved_node_id,
                    node_type=node_type,
                    input_name=input_name,
                    source_url=source_urls[0] if source_urls else None,
                    source_urls=source_urls,
                    model_type=_model_type_from_workflow_model(folder, node_type),
                    verification_level=ModelVerificationLevel.FILENAME_ONLY,
                    identity_verified_by_exporter=False,
                    bundled=False,
                    asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
                )
            )
    for selector in _iter_comfyui_workflow_widget_model_selectors(comfyui_workflow):
        node_id, node_type, input_name, folder, model_type, filename, source_urls = selector
        key = (node_id, input_name, folder, filename)
        if key in seen:
            continue
        seen.add(key)
        if source_urls and (folder, filename) not in source_urls_by_target:
            source_urls_by_target[(folder, filename)] = list(source_urls)
        models.append(
            RequiredModel(
                folder=folder,
                filename=filename,
                node_id=node_id,
                node_type=node_type,
                input_name=input_name,
                source_url=source_urls[0] if source_urls else None,
                source_urls=list(source_urls),
                model_type=model_type,
                verification_level=ModelVerificationLevel.FILENAME_ONLY,
                identity_verified_by_exporter=False,
                bundled=False,
                asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
            )
        )
    for selector in _iter_comfyui_graph_model_selectors(comfyui_graph or {}):
        node_id, node_type, input_name, folder, model_type, filename, graph_source_urls = selector
        key = (node_id, input_name, folder, filename)
        if key in seen:
            continue
        seen.add(key)
        source_urls = graph_source_urls or source_urls_by_target.get((folder, filename), [])
        models.append(
            RequiredModel(
                folder=folder,
                filename=filename,
                node_id=node_id,
                node_type=node_type,
                input_name=input_name,
                source_url=source_urls[0] if source_urls else None,
                source_urls=list(source_urls),
                model_type=model_type,
                verification_level=ModelVerificationLevel.FILENAME_ONLY,
                identity_verified_by_exporter=False,
                bundled=False,
                asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
            )
        )
    return models


def _comfyui_graph_model_bindings(
    comfyui_graph: dict[str, Any],
) -> dict[tuple[str | None, str], list[tuple[str, str]]]:
    bindings: dict[tuple[str | None, str], list[tuple[str, str]]] = {}
    for raw_node_id, raw_node in comfyui_graph.items():
        if not isinstance(raw_node, dict):
            continue
        node_id = str(raw_node_id)
        node_type = optional_string_field(raw_node, "class_type")
        inputs = raw_node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for input_name, value in inputs.items():
            if not isinstance(input_name, str) or not isinstance(value, str):
                continue
            bindings.setdefault((node_type, value), []).append((node_id, input_name))
    return bindings


def _matching_graph_model_binding(
    graph_bindings: dict[tuple[str | None, str], list[tuple[str, str]]],
    *,
    node_id: str | None,
    node_type: str | None,
    filename: str,
) -> tuple[str | None, str | None]:
    candidates = graph_bindings.get((node_type, filename), [])
    if not candidates:
        return None, None
    if node_id:
        for graph_node_id, input_name in candidates:
            if graph_node_id == node_id or graph_node_id.endswith(f":{node_id}"):
                return graph_node_id, input_name
    return candidates[0]


def _iter_comfyui_graph_model_selectors(
    comfyui_graph: dict[str, Any],
) -> Iterator[tuple[str, str, str, str, str, str, list[str]]]:
    for raw_node_id, raw_node in comfyui_graph.items():
        if not isinstance(raw_node, dict):
            continue
        node_type = optional_string_field(raw_node, "class_type")
        if not node_type:
            continue
        inputs = raw_node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        selector_count = sum(
            1
            for raw_input_name in inputs
            if isinstance(raw_input_name, str)
            and (
                (node_type, raw_input_name) == ULTRALYTICS_DETECTOR_SELECTOR
                or (node_type, raw_input_name) in KNOWN_GRAPH_MODEL_SELECTOR_INPUTS
            )
        )
        for input_name, value in inputs.items():
            if not isinstance(input_name, str):
                continue
            ultralytics_selector = _ultralytics_detector_selector(
                node_type,
                input_name,
                value,
            )
            if ultralytics_selector is not None:
                folder, model_type, filename = ultralytics_selector
                source_urls = _graph_selector_source_urls(
                    raw_node,
                    input_name,
                    selector_count=selector_count,
                )
                yield str(raw_node_id), node_type, input_name, folder, model_type, filename, source_urls
                continue
            selector = KNOWN_GRAPH_MODEL_SELECTOR_INPUTS.get((node_type, input_name))
            if selector is None:
                continue
            filename = _safe_graph_model_selector_filename(value)
            if filename is None:
                continue
            folder, model_type = selector
            source_urls = _graph_selector_source_urls(
                raw_node,
                input_name,
                selector_count=selector_count,
            )
            yield str(raw_node_id), node_type, input_name, folder, model_type, filename, source_urls


def _iter_comfyui_workflow_widget_model_selectors(
    comfyui_workflow: dict[str, Any],
) -> Iterator[tuple[str, str, str, str, str, str, list[str]]]:
    for node in _iter_comfyui_workflow_nodes(comfyui_workflow):
        node_id = _node_id_string(node.get("id"))
        node_type = optional_string_field(node, "type")
        if not node_id or not node_type:
            continue
        widget_values = node.get("widgets_values")
        if not isinstance(widget_values, list):
            continue
        input_order = KNOWN_UI_WIDGET_INPUT_ORDERS.get(node_type)
        if not input_order:
            continue
        for input_name, value in _ui_widget_values_by_input(node_type, widget_values):
            ultralytics_selector = _ultralytics_detector_selector(
                node_type,
                input_name,
                value,
            )
            if ultralytics_selector is not None:
                folder, model_type, filename = ultralytics_selector
                yield node_id, node_type, input_name, folder, model_type, filename, []
                continue
            selector = KNOWN_GRAPH_MODEL_SELECTOR_INPUTS.get((node_type, input_name))
            if selector is None:
                continue
            filename = _safe_graph_model_selector_filename(value)
            if filename is None:
                continue
            folder, model_type = selector
            source_urls = _workflow_model_source_urls_for_widget(
                node,
                folder=folder,
                filename=filename,
            )
            yield node_id, node_type, input_name, folder, model_type, filename, source_urls


def _ultralytics_detector_selector(
    node_type: str,
    input_name: str,
    value: Any,
) -> tuple[str, str, str] | None:
    if (node_type, input_name) != ULTRALYTICS_DETECTOR_SELECTOR:
        return None
    if not isinstance(value, str):
        return None
    selector = value.strip()
    if not selector or "\\" in selector:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", selector):
        return None
    parts = selector.split("/")
    if len(parts) != 2:
        return None
    prefix, filename_value = parts
    target = ULTRALYTICS_DETECTOR_PREFIX_FOLDERS.get(prefix)
    if target is None:
        return None
    filename = _safe_graph_model_selector_filename(filename_value)
    if filename is None:
        return None
    folder, model_type = target
    return folder, model_type, filename


def _graph_selector_source_urls(
    node: dict[str, Any],
    input_name: str,
    *,
    selector_count: int,
) -> list[str]:
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return []
    urls: list[str] = []
    for key in (
        f"{input_name}_source_url",
        f"{input_name}_source_urls",
        f"{input_name}_url",
        f"{input_name}_download_url",
    ):
        urls.extend(normalize_source_urls(inputs.get(key)))
    if selector_count == 1:
        for key in ("source_url", "source_urls", "url", "download_url"):
            urls.extend(normalize_source_urls(inputs.get(key)))
    return _http_source_urls(urls)


def _workflow_model_source_urls_for_widget(
    node: dict[str, Any],
    *,
    folder: str,
    filename: str,
) -> list[str]:
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return []
    model_entries = properties.get("models")
    if not isinstance(model_entries, list):
        return []
    for entry in model_entries:
        if not isinstance(entry, dict):
            continue
        entry_folder = optional_string_field(entry, "directory")
        entry_filename = optional_string_field(entry, "name")
        if entry_folder != folder or entry_filename != filename:
            continue
        return _http_source_urls(
            normalize_source_urls(entry.get("source_urls"), fallback=entry.get("url"))
        )
    return []


def _http_source_urls(urls: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            continue
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def _safe_graph_model_selector_filename(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    filename = value.strip()
    if not filename or "\x00" in filename or "\n" in filename or "\r" in filename:
        return None
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", filename):
        return None
    if "/" in filename or "\\" in filename:
        return None
    if Path(filename).name != filename:
        return None
    if Path(filename).suffix.casefold() not in MODEL_SELECTOR_EXTENSIONS:
        return None
    return filename


def _model_type_from_workflow_model(folder: str, node_type: str | None) -> str:
    normalized_node = (node_type or "").casefold()
    if "backgroundremoval" in normalized_node or folder == "background_removal":
        return "background_removal"
    return folder


def _node_id_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return None


def _iter_comfyui_workflow_nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        nodes = value.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict):
                    yield node
        definitions = value.get("definitions")
        if isinstance(definitions, dict):
            for definition in definitions.values():
                yield from _iter_comfyui_workflow_nodes(definition)
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_comfyui_workflow_nodes(item)


def comfyui_workflow_node_types(workflow: dict[str, Any]) -> set[str]:
    node_types: set[str] = set()
    for node in _iter_comfyui_workflow_nodes(workflow):
        node_type = optional_string_field(node, "type")
        if node_type:
            node_types.add(node_type)
    return node_types


def executable_comfyui_workflow_definition_node_types(
    workflow: dict[str, Any],
) -> set[str]:
    """Return node types from UI definitions proven referenced by active nodes."""
    active_definition_ids = _active_ui_definition_ids(workflow)
    if not active_definition_ids:
        return set()
    node_types: set[str] = set()
    for definition_id, definition in _iter_ui_workflow_definitions(workflow):
        if definition_id not in active_definition_ids:
            continue
        for node in _iter_comfyui_workflow_nodes(definition):
            node_type = optional_string_field(node, "type")
            if node_type:
                node_types.add(node_type)
    return node_types


def _active_ui_definition_ids(workflow: dict[str, Any]) -> set[str]:
    active: set[str] = set()
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return active
    available_ids = {definition_id for definition_id, _ in _iter_ui_workflow_definitions(workflow)}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_type = optional_string_field(node, "type")
        for candidate in _ui_node_definition_references(node):
            if candidate in available_ids:
                active.add(candidate)
        if not node_type:
            continue
        for prefix in ("workflow/", "workflow>"):
            if not node_type.startswith(prefix):
                continue
            candidate = node_type.removeprefix(prefix)
            if candidate in available_ids:
                active.add(candidate)
    return active


def _ui_node_definition_references(node: dict[str, Any]) -> set[str]:
    references: set[str] = set()
    for key in ("definition_id", "subgraph_id", "workflow_id", "subgraph"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            references.add(value.strip())
    properties = node.get("properties")
    if isinstance(properties, dict):
        for key in ("definition_id", "subgraph_id", "workflow_id", "subgraph"):
            value = properties.get(key)
            if isinstance(value, str) and value.strip():
                references.add(value.strip())
    return references


def _iter_ui_workflow_definitions(
    workflow: dict[str, Any],
) -> Iterator[tuple[str, dict[str, Any]]]:
    definitions = workflow.get("definitions")
    if not isinstance(definitions, dict):
        return
    for key, value in definitions.items():
        if isinstance(value, dict):
            yield str(key), value
    subgraphs = definitions.get("subgraphs")
    if isinstance(subgraphs, list):
        for index, value in enumerate(subgraphs):
            if not isinstance(value, dict):
                continue
            definition_id = (
                optional_string_field(value, "id")
                or optional_string_field(value, "name")
                or str(index)
            )
            yield definition_id, value


def is_comfyui_api_graph(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    found_node = False
    for raw_node in value.values():
        if not isinstance(raw_node, dict):
            return False
        if not isinstance(raw_node.get("class_type"), str):
            return False
        inputs = raw_node.get("inputs")
        if inputs is not None and not isinstance(inputs, dict):
            return False
        found_node = True
    return found_node


def is_comfyui_ui_workflow(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    nodes = value.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return False
    for node in nodes:
        if (
            isinstance(node, dict)
            and _node_id_string(node.get("id")) is not None
            and optional_string_field(node, "type") is not None
        ):
            return True
    return False


def raw_comfyui_api_graph(payload: dict[str, Any]) -> dict[str, Any] | None:
    if is_comfyui_api_graph(payload):
        return payload
    prompt = payload.get("prompt")
    if is_comfyui_api_graph(prompt):
        return prompt
    return None


def comfyui_api_graph_from_ui_workflow(
    workflow: dict[str, Any],
) -> dict[str, Any]:
    if not is_comfyui_ui_workflow(workflow):
        return {}
    links_by_id = _ui_workflow_links_by_id(workflow)
    graph: dict[str, Any] = {}
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return graph
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = _node_id_string(node.get("id"))
        node_type = optional_string_field(node, "type")
        if not node_id or not node_type:
            continue
        inputs: dict[str, Any] = {}
        raw_inputs = node.get("inputs")
        if isinstance(raw_inputs, list):
            for raw_input in raw_inputs:
                if not isinstance(raw_input, dict):
                    continue
                input_name = optional_string_field(raw_input, "name")
                if not input_name:
                    continue
                source = links_by_id.get(_ui_link_id(raw_input.get("link")))
                if source is None:
                    continue
                inputs[input_name] = [source[0], source[1]]
        widget_values = node.get("widgets_values")
        input_order = KNOWN_UI_WIDGET_INPUT_ORDERS.get(node_type)
        if isinstance(widget_values, list) and input_order:
            for input_name, value in _ui_widget_values_by_input(node_type, widget_values):
                if input_name in inputs:
                    continue
                if value is None:
                    continue
                inputs[input_name] = value
        graph[node_id] = {"class_type": node_type, "inputs": inputs}
    return graph


def _ui_workflow_links_by_id(
    workflow: dict[str, Any],
) -> dict[str, tuple[str, int]]:
    links = workflow.get("links")
    if not isinstance(links, list):
        return {}
    result: dict[str, tuple[str, int]] = {}
    for link in links:
        parsed = _parse_ui_workflow_link(link)
        if parsed is None:
            continue
        link_id, origin_id, origin_slot = parsed
        result[link_id] = (origin_id, origin_slot)
    return result


def _ui_widget_values_by_input(
    node_type: str,
    widget_values: list[Any],
) -> Iterator[tuple[str, Any]]:
    input_order = KNOWN_UI_WIDGET_INPUT_ORDERS.get(node_type)
    if not input_order:
        return
    values = list(widget_values)
    if (
        node_type == "KSampler"
        and len(values) >= 7
        and isinstance(values[1], str)
        and values[1] in {"fixed", "increment", "decrement", "randomize"}
    ):
        values = [values[0], *values[2:]]
    for index, input_name in enumerate(input_order):
        if index >= len(values):
            continue
        yield input_name, values[index]


def _parse_ui_workflow_link(value: Any) -> tuple[str, str, int] | None:
    if isinstance(value, list) and len(value) >= 3:
        link_id = _ui_link_id(value[0])
        origin_id = _node_id_string(value[1])
        origin_slot = _int_index(value[2])
        if link_id is not None and origin_id is not None and origin_slot is not None:
            return link_id, origin_id, origin_slot
    if isinstance(value, dict):
        link_id = _ui_link_id(value.get("id") or value.get("link_id"))
        origin_id = _node_id_string(
            _first_present(
                value,
                ("origin_id", "origin", "from_node_id", "source_node_id"),
            )
        )
        origin_slot = _int_index(
            _first_present(value, ("origin_slot", "from_slot", "source_slot"))
        )
        if link_id is not None and origin_id is not None and origin_slot is not None:
            return link_id, origin_id, origin_slot
    return None


def _ui_link_id(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return None


def _int_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _first_present(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def normalize_model_verification_level(
    model: dict[str, Any],
    *,
    checksum: str | None,
    size_bytes: int | None,
) -> str:
    value = model.get("verification_level")
    if value in {item.value for item in ModelVerificationLevel}:
        return str(value)
    if checksum is not None and size_bytes is not None:
        return ModelVerificationLevel.SHA256_SIZE.value
    if size_bytes is not None:
        return ModelVerificationLevel.FILENAME_SIZE.value
    return ModelVerificationLevel.FILENAME_ONLY.value


def normalize_asset_ownership(value: Any) -> str:
    if value in {item.value for item in AssetOwnership}:
        return str(value)
    return AssetOwnership.EXTERNAL_REFERENCE.value


def normalize_custom_nodes(
    capsule_json: dict[str, Any],
    package_json: dict[str, Any] | None = None,
) -> list[WorkflowCustomNodeRecord]:
    nodes = capsule_json.get("custom_nodes", [])
    if not isinstance(nodes, list):
        nodes = []
    package_nodes = package_json.get("custom_nodes", []) if package_json is not None else []
    if not isinstance(package_nodes, list):
        package_nodes = []
    package_nodes_by_id = {
        str(node_id): package_node
        for package_node in package_nodes
        if isinstance(package_node, dict)
        for node_id in (package_node.get("id"), package_node.get("package_id"))
        if isinstance(node_id, str) and node_id
    }
    if not nodes:
        nodes = package_nodes

    normalized: list[WorkflowCustomNodeRecord] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        package_id = string_field(
            node,
            "package_id",
            fallback=string_field(node, "id", fallback="custom-node"),
        )
        package_node = package_nodes_by_id.get(package_id)
        merged = {**package_node, **node} if isinstance(package_node, dict) else node
        folder_name = string_field(
            merged,
            "folder_name",
            fallback=package_id,
        )
        node_id = string_field(merged, "id", fallback=package_id)
        requirements_files = [
            item for item in merged.get("requirements_files", []) if isinstance(item, str)
        ]
        node_types = [
            item for item in merged.get("node_types", []) if isinstance(item, str)
        ]
        normalized.append(
            WorkflowCustomNodeRecord(
                id=node_id,
                folder_name=folder_name,
                source=string_field(merged, "source", fallback="unknown"),
                included=bool(merged.get("included", True)),
                node_types=node_types,
                requirements_files=requirements_files,
                has_install_py=bool(merged.get("has_install_py")),
                sha256_manifest=(
                    merged.get("sha256_manifest")
                    if isinstance(merged.get("sha256_manifest"), str)
                    else None
                ),
                source_ref=(
                    merged.get("source_ref")
                    if isinstance(merged.get("source_ref"), str)
                    else None
                ),
                source_content_hash=(
                    merged.get("source_content_hash")
                    if isinstance(merged.get("source_content_hash"), str)
                    else None
                ),
                source_cache_ref=(
                    merged.get("source_cache_ref")
                    if isinstance(merged.get("source_cache_ref"), str)
                    else None
                ),
                source_archive_subdir=optional_string_field(
                    merged, "source_archive_subdir"
                )
                or optional_string_field(merged, "archive_subdir"),
                source_repo_url=optional_string_field(merged, "source_repo_url")
                or optional_string_field(merged, "repo_url")
                or optional_string_field(merged, "repository_url"),
                resolution_method=(
                    merged.get("resolution_method")
                    if isinstance(merged.get("resolution_method"), str)
                    else None
                ),
            )
        )
    return normalized


def normalize_dashboard(
    dashboard_json: dict[str, Any],
) -> DashboardSchema:
    schema, _diagnostics = normalize_dashboard_with_diagnostics(dashboard_json)
    return schema


def normalize_dashboard_with_diagnostics(
    dashboard_json: dict[str, Any],
) -> tuple[DashboardSchema, dict[str, Any]]:
    stripped = {
        k: v for k, v in dashboard_json.items() if k not in ("inputs", "outputs")
    }
    archive_status = stripped.get("status")
    diagnostics: dict[str, Any] = {
        "source": "dashboard.json" if dashboard_json else "missing_dashboard_json",
        "source_status": archive_status if isinstance(archive_status, str) else None,
        "parse_status": "missing_or_legacy",
        "normalizations": [],
        "downgraded_to_setup_required": False,
    }
    if archive_status not in ("configured", "not_configured", "invalid"):
        if archive_status is not None:
            diagnostics["normalizations"].append("invalid_status_reset")
        archive_status = None
    if "version" in stripped and isinstance(stripped.get("sections"), list):
        try:
            schema = DashboardSchema.model_validate(stripped)
            if archive_status is None and not any(s.controls for s in schema.sections):
                schema = schema.model_copy(update={"status": "not_configured"})
                diagnostics["normalizations"].append("empty_dashboard_marked_not_configured")
            elif archive_status is None:
                diagnostics["normalizations"].append("missing_status_defaulted")
            diagnostics["parse_status"] = (
                "normalized" if diagnostics["normalizations"] else "parsed"
            )
            diagnostics["effective_status"] = schema.status
            return schema, diagnostics
        except Exception as exc:
            diagnostics["parse_status"] = "rejected"
            diagnostics["downgraded_to_setup_required"] = True
            diagnostics["rejection_reason"] = exc.__class__.__name__
            diagnostics["rejection_summary"] = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    schema = DashboardSchema(
        version=NOOFY_ARCHIVE_SCHEMA_VERSION,
        status="not_configured",
        sections=[],
    )
    diagnostics["effective_status"] = schema.status
    if dashboard_json:
        diagnostics["downgraded_to_setup_required"] = True
    return schema, diagnostics


def detect_unresolved_runtime_inputs(
    graph: dict[str, Any],
) -> list[UnresolvedRuntimeInput]:
    unresolved: list[UnresolvedRuntimeInput] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        node_type = node.get("class_type")
        inputs = node.get("inputs")
        if not isinstance(node_type, str) or not isinstance(inputs, dict):
            continue
        for input_name, input_value in inputs.items():
            expected_kind = expected_runtime_input_kind(node_type, str(input_name), input_value)
            if expected_kind is None or not value_contains_local_reference(input_value):
                continue
            extension_hint = safe_extension_hint(input_value)
            unresolved.append(
                UnresolvedRuntimeInput(
                    node_id=str(node_id),
                    node_type=node_type,
                    input_name=str(input_name),
                    current_value=redacted_input_value(expected_kind),
                    reason=f"creator_local_{expected_kind}_not_bundled",
                    expected_kind=expected_kind,
                    required=True,
                    extension_hint=extension_hint,
                    mime_type_hint=safe_mime_type_hint(extension_hint, expected_kind),
                )
            )
    return unresolved


def normalize_unresolved_runtime_inputs(value: Any) -> list[UnresolvedRuntimeInput]:
    if not isinstance(value, list):
        return []

    normalized: list[UnresolvedRuntimeInput] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        node_id = optional_clean_string(item.get("node_id"))
        node_type = optional_clean_string(item.get("node_type"))
        input_name = optional_clean_string(item.get("input_name"))
        if node_id is None or node_type is None or input_name is None:
            continue
        expected_kind = optional_clean_string(item.get("expected_kind"))
        if expected_kind not in WORKFLOW_MEDIA_KINDS:
            expected_kind = kind_from_path_like_value(item.get("extension_hint"))
        normalized_kind = expected_kind or "file"
        extension_hint = safe_extension_hint(item.get("extension_hint"))
        mime_type_hint = safe_mime_type_hint(extension_hint, normalized_kind)
        explicit_mime_type_hint = optional_clean_string(item.get("mime_type_hint"))
        if explicit_mime_type_hint and safe_mime_type_value(explicit_mime_type_hint):
            mime_type_hint = explicit_mime_type_hint
        normalized.append(
            UnresolvedRuntimeInput(
                node_id=node_id,
                node_type=node_type,
                input_name=input_name,
                current_value=redacted_input_value(normalized_kind),
                reason=unresolved_input_reason(normalized_kind),
                expected_kind=normalized_kind,
                required=item.get("required") if isinstance(item.get("required"), bool) else True,
                extension_hint=extension_hint,
                mime_type_hint=mime_type_hint,
            )
        )
    return normalized


def expected_runtime_input_kind(node_type: str, input_name: str, value: Any) -> str | None:
    normalized_node = node_type.lower()
    normalized_input = input_name.lower()
    if node_type in LOCAL_IMAGE_NODE_TYPES and normalized_input == "image":
        return "image"
    if node_type in LOCAL_AUDIO_NODE_TYPES and normalized_input in {"audio", "file", "filename", "path", "audio_path"}:
        return "audio"
    if is_video_input_node_type(node_type) and normalized_input in {"video", "file", "filename", "path", "video_path"}:
        return "video"
    if (
        node_type in LOCAL_THREE_D_NODE_TYPES
        or (
            any(token in normalized_node for token in ("3d", "mesh", "glb", "gltf"))
            and any(token in normalized_node for token in ("load", "input", "import"))
        )
    ) and normalized_input in {"model", "mesh", "model_file", "file", "filename", "path", "model_path", "mesh_path"}:
        return "3d"
    if "text" in normalized_node and normalized_input in FILE_INPUT_NAMES:
        value_kind = kind_from_path_like_value(value)
        if normalized_input not in MULTIMODAL_MEDIA_INPUT_NAMES or value_kind == "text":
            return "text"
    if is_generic_file_input(node_type, input_name, value):
        return kind_from_path_like_value(value) or "file"
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
    strong_node_signal = any(
        token in normalized_node
        for token in ("file", "load", "open", "import", "document", "archive", "json", "csv", "subtitle", "text")
    )
    return strong_node_signal or kind_from_path_like_value(value) is not None


def value_contains_local_reference(value: Any) -> bool:
    if is_graph_link(value):
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        if is_package_asset_value(value):
            return False
        return any(value_contains_local_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(value_contains_local_reference(item) for item in value)
    return False


def is_graph_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and not isinstance(value[0], bool)
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


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


def safe_mime_type_value(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9*][a-z0-9.+*-]*", value.strip().lower()))


def redacted_input_value(kind: str) -> str:
    if kind == "image":
        return "__noofy_runtime_image_input_required__"
    safe_kind = "three_d" if kind == "3d" else kind
    return f"__noofy_runtime_{safe_kind}_input_required__"


def repair_misclassified_multimodal_text_inputs(
    graph: dict[str, Any],
    unresolved_inputs: list[UnresolvedRuntimeInput],
) -> tuple[dict[str, Any], list[UnresolvedRuntimeInput]]:
    """Remove legacy text-file sentinels assigned to optional media sockets."""
    protected_text_paths = {
        (runtime_input.node_id, runtime_input.input_name)
        for runtime_input in unresolved_inputs
        if runtime_input.expected_kind == "text"
        and runtime_input.extension_hint in TEXT_EXTENSIONS
    }
    invalid_bindings: set[tuple[str, str]] = set()
    for raw_node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        node_id = str(raw_node_id)
        node_type = str(node.get("class_type") or node.get("type") or "").casefold()
        if "text" not in node_type:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for input_name, value in inputs.items():
            normalized_input = str(input_name).casefold()
            binding = (node_id, str(input_name))
            if binding in protected_text_paths:
                continue
            if normalized_input not in MULTIMODAL_MEDIA_INPUT_NAMES:
                continue
            if value == redacted_input_value("text"):
                invalid_bindings.add(binding)

    if not invalid_bindings:
        return graph, unresolved_inputs

    repaired_graph = dict(graph)
    for node_id, input_name in invalid_bindings:
        node = repaired_graph.get(node_id)
        if not isinstance(node, dict):
            continue
        repaired_inputs = dict(node.get("inputs") or {})
        repaired_inputs.pop(input_name, None)
        repaired_graph[node_id] = {**node, "inputs": repaired_inputs}

    return repaired_graph, [
        runtime_input
        for runtime_input in unresolved_inputs
        if (runtime_input.node_id, runtime_input.input_name) not in invalid_bindings
    ]


def unresolved_input_reason(kind: str) -> str:
    return f"creator_local_{kind}_not_bundled"


def optional_clean_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def filter_resolved_runtime_inputs(
    unresolved_inputs: list[UnresolvedRuntimeInput],
    workflow_inputs: list[WorkflowInput],
) -> list[UnresolvedRuntimeInput]:
    resolved_bindings = {
        (workflow_input.binding.node_id, workflow_input.binding.input_name)
        for workflow_input in workflow_inputs
    }
    return [
        runtime_input
        for runtime_input in unresolved_inputs
        if (runtime_input.node_id, runtime_input.input_name) not in resolved_bindings
    ]


def observed_hardware(
    capsule_json: dict[str, Any], export_report: dict[str, Any]
) -> dict[str, Any]:
    observed: dict[str, Any] = {}
    capsule_observations = capsule_json.get("hardware_observations")
    if isinstance(capsule_observations, dict):
        observed.update(capsule_observations)
    runtime = export_report.get("runtime")
    if isinstance(runtime, dict):
        observed["export_runtime"] = runtime
    test_run = export_report.get("test_run")
    if isinstance(test_run, dict):
        observed["test_run"] = test_run
    return observed


def reject_unsupported_exported_launch_options(data: dict[str, Any]) -> None:
    launch_keys = sorted(
        key
        for key in UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS
        if has_nonempty_launch_option(data, key)
    )
    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        launch_keys.extend(
            f"runtime.{key}"
            for key in sorted(UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS)
            if has_nonempty_launch_option(runtime, key)
        )
    if launch_keys:
        raise ImportNormalizationError(
            "Workflow package declares unsupported launch options: "
            + ", ".join(launch_keys)
        )


def has_nonempty_launch_option(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    return value not in (None, {}, [], "")
