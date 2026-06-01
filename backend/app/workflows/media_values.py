from __future__ import annotations

import re
from typing import Any

from app.media_types import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from app.workflows.package import WorkflowInput

MEDIA_LOAD_CONTROLS = frozenset({"load_image", "load_image_mask", "load_audio", "load_video", "load_file", "load_3d"})
GALLERY_MEDIA_KINDS = frozenset({"image", "audio", "video", "3d"})

_ASSET_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\.[A-Za-z0-9_-]+)+$",
    re.IGNORECASE,
)

_KIND_EXTENSIONS = {
    "image": IMAGE_EXTENSIONS,
    "audio": AUDIO_EXTENSIONS,
    "video": VIDEO_EXTENSIONS,
    "3d": frozenset({".glb", ".gltf", ".obj", ".stl", ".fbx", ".ply"}),
}


def is_uploaded_asset_value(value: Any) -> bool:
    return isinstance(value, str) and bool(_ASSET_ID_RE.match(value))


def is_gallery_media_reference(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return (
        value.get("source") == "gallery"
        and isinstance(value.get("gallery_item_id"), str)
        and value.get("gallery_item_id", "").strip() != ""
        and value.get("kind") in GALLERY_MEDIA_KINDS
    )


def is_empty_media_value(value: Any) -> bool:
    return value is None or value == ""


def target_media_kind_for_input(workflow_input: WorkflowInput) -> str | None:
    return {
        "load_image": "image",
        "load_image_mask": "image",
        "load_audio": "audio",
        "load_video": "video",
        "load_3d": "3d",
        "load_file": "file",
    }.get(workflow_input.control)


def accepted_extensions_for_input(workflow_input: WorkflowInput) -> set[str]:
    explicit = _normalize_extensions(workflow_input.validation.get("accepted_extensions"))
    if explicit:
        return explicit
    kind = target_media_kind_for_input(workflow_input)
    return set(_KIND_EXTENSIONS.get(kind or "", set()))


def accepted_mime_types_for_input(workflow_input: WorkflowInput) -> set[str]:
    return _normalize_mime_types(workflow_input.validation.get("accepted_mime_types"))


def media_metadata_matches_input(
    workflow_input: WorkflowInput,
    *,
    kind: str | None,
    extension: str | None,
    mime_type: str | None,
) -> bool:
    target_kind = target_media_kind_for_input(workflow_input)
    if target_kind in GALLERY_MEDIA_KINDS and kind != target_kind:
        return False

    accepted_extensions = accepted_extensions_for_input(workflow_input)
    accepted_mimes = accepted_mime_types_for_input(workflow_input)
    normalized_extension = _normalize_extension(extension)
    normalized_mime = _normalize_mime_type(mime_type)

    if accepted_extensions and normalized_extension not in accepted_extensions:
        return False
    if accepted_mimes and normalized_mime not in accepted_mimes:
        return False
    if not accepted_extensions and target_kind in _KIND_EXTENSIONS and normalized_extension:
        return normalized_extension in _KIND_EXTENSIONS[target_kind]
    return True


def gallery_item_matches_picker_filters(
    *,
    item_kind: str,
    item_extension: str | None,
    item_mime_type: str | None,
    kind: str | None,
    accepted_extensions: list[str] | None = None,
    accepted_mime_types: list[str] | None = None,
) -> bool:
    if kind and item_kind != kind:
        return False

    extensions = _normalize_extensions(accepted_extensions)
    mime_types = _normalize_mime_types(accepted_mime_types)
    item_extension_normalized = _normalize_extension(item_extension)
    item_mime_normalized = _normalize_mime_type(item_mime_type)

    if extensions and item_extension_normalized not in extensions:
        return False
    if mime_types and item_mime_normalized not in mime_types:
        return False
    return True


def _normalize_extensions(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    normalized: set[str] = set()
    for item in value:
        extension = _normalize_extension(item)
        if extension:
            normalized.add(extension)
    return normalized


def _normalize_extension(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    extension = value.strip().lower()
    if not extension:
        return None
    if not extension.startswith("."):
        extension = f".{extension}"
    if "/" in extension or "\\" in extension or ".." in extension:
        return None
    return extension


def _normalize_mime_types(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    normalized: set[str] = set()
    for item in value:
        mime_type = _normalize_mime_type(item)
        if mime_type:
            normalized.add(mime_type)
    return normalized


def _normalize_mime_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    mime_type = value.split(";")[0].strip().lower()
    if not mime_type or "/" not in mime_type:
        return None
    return mime_type
