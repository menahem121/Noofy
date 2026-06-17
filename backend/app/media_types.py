from __future__ import annotations

from pathlib import Path
from typing import Any

MEDIA_KINDS = frozenset({"image", "audio", "video", "3d", "file"})
MEDIA_OUTPUT_BUCKETS = ("images", "audio", "video", "videos", "gifs", "3d", "files", "text")

IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
AUDIO_EXTENSIONS = frozenset({".wav", ".mp3", ".flac", ".ogg", ".m4a"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm", ".mkv"})
THREE_D_EXTENSIONS = frozenset({".glb", ".gltf", ".obj", ".stl", ".fbx", ".ply", ".usdz", ".dae", ".spz", ".splat", ".ksplat"})


def classify_media_kind(
    item: dict[str, Any],
    bucket_name: str,
    declared_kind: str | None = None,
) -> str:
    if declared_kind in MEDIA_KINDS:
        return str(declared_kind)

    item_kind = item.get("kind") or item.get("type")
    if item_kind in MEDIA_KINDS:
        return str(item_kind)

    mime_type = str(item.get("mime_type") or item.get("content_type") or "").lower()
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("model/"):
        return "3d"

    suffix = Path(str(item.get("filename") or "")).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in AUDIO_EXTENSIONS:
        return "audio"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in THREE_D_EXTENSIONS:
        return "3d"

    if mime_type and mime_type != "application/octet-stream":
        return "file"
    if suffix:
        return "file"

    weak_fallbacks = {
        "images": "image",
        "gifs": "image",
        "audio": "audio",
        "video": "video",
        "videos": "video",
        "3d": "3d",
        "files": "file",
        "text": "file",
    }
    return weak_fallbacks.get(bucket_name, "file")
