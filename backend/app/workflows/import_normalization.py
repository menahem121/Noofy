from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any

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
THREE_D_EXTENSIONS = frozenset({".glb", ".gltf", ".obj", ".fbx", ".stl", ".ply", ".usdz", ".dae"})
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".json", ".csv", ".tsv", ".yaml", ".yml", ".xml", ".srt", ".vtt"})
UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS = {
    "comfyui_launch_options",
    "launch_config",
    "launch_options",
    "runner_launch_options",
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
    stripped = {
        k: v for k, v in dashboard_json.items() if k not in ("inputs", "outputs")
    }
    archive_status = stripped.get("status")
    if archive_status not in ("configured", "not_configured", "invalid"):
        archive_status = None
    if "version" in stripped and isinstance(stripped.get("sections"), list):
        try:
            schema = DashboardSchema.model_validate(stripped)
            if archive_status is None and not any(s.controls for s in schema.sections):
                schema = schema.model_copy(update={"status": "not_configured"})
            return schema
        except Exception:
            pass
    explicit_status = archive_status if archive_status else "not_configured"
    return DashboardSchema(
        version=NOOFY_ARCHIVE_SCHEMA_VERSION,
        status=explicit_status,
        sections=[],
    )


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
    if isinstance(value, str):
        return bool(value.strip()) and not is_graph_link(value)
    if isinstance(value, dict):
        if is_package_asset_value(value):
            return False
        return any(value_contains_local_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(value_contains_local_reference(item) for item in value)
    return False


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


def safe_mime_type_value(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9.+-]*/[a-z0-9*][a-z0-9.+*-]*", value.strip().lower()))


def redacted_input_value(kind: str) -> str:
    if kind == "image":
        return "__noofy_runtime_image_input_required__"
    safe_kind = "three_d" if kind == "3d" else kind
    return f"__noofy_runtime_{safe_kind}_input_required__"


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
