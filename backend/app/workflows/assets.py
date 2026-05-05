from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import uuid
from pathlib import Path

ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
MAX_ASSET_BYTES = 25 * 1024 * 1024  # 25 MB


class AssetUploadError(ValueError):
    pass


class DashboardAssetService:
    def __init__(self, assets_dir: Path) -> None:
        self._dir = assets_dir

    def store(self, data: bytes, content_type: str, original_filename: str) -> dict[str, str]:
        if len(data) > MAX_ASSET_BYTES:
            raise AssetUploadError("File exceeds the 25 MB size limit.")

        if content_type not in ALLOWED_MIME_TYPES:
            raise AssetUploadError(f"File type '{content_type}' is not allowed.")

        detected = _detect_image_content_type(data)
        if detected is None:
            raise AssetUploadError("File does not appear to be a valid image.")
        if detected != content_type:
            raise AssetUploadError(f"File content does not match '{content_type}'.")

        ext = _safe_ext(content_type)
        asset_id = f"{uuid.uuid4()}{ext}"

        self._dir.mkdir(parents=True, exist_ok=True)
        asset_path = self._dir / asset_id
        meta_path = self._dir / f"{asset_id}.meta.json"

        _atomic_write_bytes(asset_path, data)
        _atomic_write_json(meta_path, {"asset_id": asset_id, "original_filename": original_filename})

        return {"asset_id": asset_id, "original_filename": original_filename}

    def metadata(self, asset_id: str) -> dict[str, str]:
        safe = _validate_asset_id(asset_id)
        meta_path = self._dir / f"{safe}.meta.json"
        metadata: dict[str, str] = {
            "asset_id": safe,
            "original_filename": safe,
            "content_type": self.content_type(safe),
        }
        if not meta_path.exists():
            return metadata

        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return metadata

        if isinstance(raw, dict):
            original_filename = raw.get("original_filename")
            if isinstance(original_filename, str) and original_filename.strip():
                metadata["original_filename"] = original_filename
        return metadata

    def asset_path(self, asset_id: str) -> Path:
        safe = _validate_asset_id(asset_id)
        return self._dir / safe

    def content_type(self, asset_id: str) -> str:
        safe = _validate_asset_id(asset_id)
        mime, _ = mimetypes.guess_type(safe)
        return mime or "application/octet-stream"


def _safe_ext(content_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(content_type, ".bin")


def _detect_image_content_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    return None


def _validate_asset_id(asset_id: str) -> str:
    # Must be UUID + known extension; no path separators allowed.
    if "/" in asset_id or "\\" in asset_id or ".." in asset_id:
        raise ValueError(f"Invalid asset_id: {asset_id!r}")
    # Must end with a known extension.
    if not any(asset_id.endswith(ext) for ext in (".jpg", ".png", ".webp", ".gif")):
        raise ValueError(f"Invalid asset_id: {asset_id!r}")
    return asset_id


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_json(target: Path, data: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
