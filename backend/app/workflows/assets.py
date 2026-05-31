from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import shutil
import tempfile
import uuid
import wave
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image, UnidentifiedImageError

from app.diagnostics import DiagnosticsSink

ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
ALLOWED_AUDIO_MIME_TYPES = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp3",
        "audio/flac",
        "audio/x-flac",
        "audio/ogg",
        "application/ogg",
        "audio/mp4",
        "audio/x-m4a",
        "audio/m4a",
        "application/octet-stream",
    }
)
MAX_ASSET_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_AUDIO_ASSET_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB
MAX_WORKFLOW_ICON_SIZE = 256
_STREAM_CHUNK_BYTES = 1024 * 1024


class AssetUploadError(ValueError):
    pass


class DashboardAssetService:
    def __init__(self, assets_dir: Path, log_store: DiagnosticsSink | None = None) -> None:
        self._dir = assets_dir
        self.log_store = log_store

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

    def store_audio_stream(
        self,
        source: BinaryIO,
        content_type: str,
        original_filename: str,
        *,
        declared_size: int | None = None,
    ) -> dict[str, Any]:
        original_filename = _safe_original_filename(original_filename)
        if declared_size is not None and declared_size > MAX_AUDIO_ASSET_BYTES:
            self._log_audio_upload_failure("File exceeds the 100 GB size limit.")
            raise AssetUploadError("File exceeds the 100 GB size limit.")

        guessed_content_type = _normalized_audio_content_type(
            content_type,
            original_filename,
        )
        if guessed_content_type not in ALLOWED_AUDIO_MIME_TYPES:
            self._log_audio_upload_failure(f"File type '{content_type}' is not allowed.")
            raise AssetUploadError(f"File type '{content_type}' is not allowed.")

        self._dir.mkdir(parents=True, exist_ok=True)
        if declared_size is not None:
            try:
                _ensure_disk_space(self._dir, declared_size)
            except AssetUploadError as exc:
                self._log_audio_upload_failure(str(exc))
                raise

        asset_id: str | None = None
        tmp_path: str | None = None
        total = 0
        first_bytes = b""
        fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=".audio-upload.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = source.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    if not first_bytes:
                        first_bytes = bytes(chunk[:4096])
                    total += len(chunk)
                    if total > MAX_AUDIO_ASSET_BYTES:
                        raise AssetUploadError("File exceeds the 100 GB size limit.")
                    if total % (64 * 1024 * 1024) < len(chunk):
                        _ensure_disk_space(self._dir, len(chunk))
                    f.write(chunk)

            detected = _detect_audio_content_type(first_bytes)
            if detected is None:
                raise AssetUploadError("File does not appear to be a valid audio file.")
            if not _audio_content_type_matches(guessed_content_type, detected):
                raise AssetUploadError(f"File content does not match '{content_type}'.")

            ext = _safe_audio_ext(detected)
            _validate_audio_filename_extension(original_filename, ext)
            asset_id = f"{uuid.uuid4()}{ext}"
            asset_path = self._dir / asset_id
            meta_path = self._dir / f"{asset_id}.meta.json"
            duration_seconds = _audio_duration_seconds(Path(tmp_path), detected)

            os.replace(tmp_path, asset_path)
            tmp_path = None
            metadata = {
                "asset_id": asset_id,
                "kind": "audio",
                "original_filename": original_filename,
                "content_type": _canonical_audio_content_type(detected),
                "size": total,
                "format": ext.removeprefix("."),
            }
            if duration_seconds is not None:
                metadata["duration_seconds"] = duration_seconds
            _atomic_write_json(meta_path, metadata)
            if self.log_store is not None:
                self.log_store.add(
                    "info",
                    "Stored dashboard audio asset",
                    "workflow.assets",
                    details={
                        "asset_id": asset_id,
                        "content_type": metadata["content_type"],
                        "format": metadata["format"],
                        "size": total,
                    },
                )
            return metadata
        except Exception as exc:
            if tmp_path is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_path)
            if asset_id:
                with contextlib.suppress(FileNotFoundError):
                    (self._dir / asset_id).unlink()
                with contextlib.suppress(FileNotFoundError):
                    (self._dir / f"{asset_id}.meta.json").unlink()
            self._log_audio_upload_failure(str(exc))
            raise

    def _log_audio_upload_failure(self, error: str) -> None:
        if self.log_store is not None:
            self.log_store.add(
                "warning",
                "Dashboard audio asset upload failed",
                "workflow.assets",
                details={"error": error},
            )

    def list_workflow_icons(self) -> list[dict[str, str]]:
        if not self._dir.exists():
            return []
        icons: list[dict[str, str]] = []
        for meta_path in sorted(self._dir.glob("*.meta.json")):
            try:
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict) or raw.get("kind") != "workflow_icon":
                continue
            asset_id = raw.get("asset_id")
            if not isinstance(asset_id, str):
                continue
            try:
                safe = _validate_asset_id(asset_id)
            except ValueError:
                continue
            if not self.asset_path(safe).exists():
                continue
            original_filename = raw.get("original_filename")
            icons.append(
                {
                    "id": f"asset:{safe}",
                    "asset_id": safe,
                    "label": original_filename if isinstance(original_filename, str) and original_filename else safe,
                    "kind": "custom",
                    "url": f"/api/assets/{safe}",
                }
            )
        return icons

    def store_workflow_icon(self, data: bytes, content_type: str, original_filename: str) -> dict[str, str]:
        if len(data) > MAX_ASSET_BYTES:
            raise AssetUploadError("File exceeds the 25 MB size limit.")
        if content_type not in ALLOWED_MIME_TYPES:
            raise AssetUploadError(f"File type '{content_type}' is not allowed.")
        detected = _detect_image_content_type(data)
        if detected is None:
            raise AssetUploadError("File does not appear to be a valid image.")
        if detected != content_type:
            raise AssetUploadError(f"File content does not match '{content_type}'.")

        optimized = _optimized_icon_png(data)
        asset_id = f"{uuid.uuid4()}.png"
        self._dir.mkdir(parents=True, exist_ok=True)
        asset_path = self._dir / asset_id
        meta_path = self._dir / f"{asset_id}.meta.json"
        _atomic_write_bytes(asset_path, optimized)
        _atomic_write_json(
            meta_path,
            {
                "asset_id": asset_id,
                "kind": "workflow_icon",
                "original_filename": original_filename,
            },
        )
        return {
            "id": f"asset:{asset_id}",
            "asset_id": asset_id,
            "label": original_filename,
            "kind": "custom",
            "url": f"/api/assets/{asset_id}",
        }

    def delete_workflow_icon(self, icon_id: str) -> None:
        asset_id = _asset_id_from_icon_id(icon_id)
        meta_path = self._dir / f"{asset_id}.meta.json"
        if not meta_path.exists():
            raise ValueError("Workflow icon not found.")
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Workflow icon metadata is invalid.") from exc
        if not isinstance(raw, dict) or raw.get("kind") != "workflow_icon":
            raise ValueError("Only imported workflow icons can be deleted.")
        with contextlib.suppress(FileNotFoundError):
            (self._dir / asset_id).unlink()
        with contextlib.suppress(FileNotFoundError):
            meta_path.unlink()

    def metadata(self, asset_id: str) -> dict[str, Any]:
        safe = _validate_asset_id(asset_id)
        meta_path = self._dir / f"{safe}.meta.json"
        metadata: dict[str, Any] = {
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
            content_type = raw.get("content_type")
            if isinstance(content_type, str) and content_type.strip():
                metadata["content_type"] = content_type
            if raw.get("kind") == "audio":
                metadata["kind"] = "audio"
                if isinstance(raw.get("size"), int):
                    metadata["size"] = raw["size"]
                if isinstance(raw.get("format"), str):
                    metadata["format"] = raw["format"]
                if isinstance(raw.get("duration_seconds"), (int, float)):
                    metadata["duration_seconds"] = raw["duration_seconds"]
        return metadata

    def asset_path(self, asset_id: str) -> Path:
        safe = _validate_asset_id(asset_id)
        return self._dir / safe

    def content_type(self, asset_id: str) -> str:
        safe = _validate_asset_id(asset_id)
        meta_path = self._dir / f"{safe}.meta.json"
        if meta_path.exists():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                raw = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("content_type"), str):
                    return raw["content_type"]
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


def _normalized_audio_content_type(content_type: str, original_filename: str) -> str:
    normalized = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    if normalized != "application/octet-stream":
        return normalized
    guessed, _ = mimetypes.guess_type(original_filename)
    return (guessed or normalized).lower()


def _safe_audio_ext(content_type: str) -> str:
    mapping = {
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/flac": ".flac",
        "audio/x-flac": ".flac",
        "audio/ogg": ".ogg",
        "application/ogg": ".ogg",
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/m4a": ".m4a",
    }
    return mapping.get(content_type, ".bin")


def _validate_audio_filename_extension(original_filename: str, detected_ext: str) -> None:
    suffix = Path(original_filename).suffix.lower()
    if not suffix:
        return
    if suffix not in {".wav", ".mp3", ".flac", ".ogg", ".m4a"}:
        raise AssetUploadError(f"File extension '{suffix}' is not allowed.")
    if suffix != detected_ext:
        raise AssetUploadError(f"File extension '{suffix}' does not match the audio content.")


def _safe_original_filename(original_filename: str) -> str:
    return Path(original_filename.replace("\\", "/")).name.strip() or "upload"


def _canonical_audio_content_type(content_type: str) -> str:
    mapping = {
        "audio/x-wav": "audio/wav",
        "audio/mp3": "audio/mpeg",
        "audio/x-flac": "audio/flac",
        "application/ogg": "audio/ogg",
        "audio/x-m4a": "audio/mp4",
        "audio/m4a": "audio/mp4",
    }
    return mapping.get(content_type, content_type)


def _detect_audio_content_type(data: bytes) -> str | None:
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "audio/mpeg"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    return None


def _audio_content_type_matches(declared: str, detected: str) -> bool:
    if declared == "application/octet-stream":
        return True
    return _canonical_audio_content_type(declared) == _canonical_audio_content_type(detected)


def _audio_duration_seconds(path: Path, detected_content_type: str) -> float | None:
    if detected_content_type != "audio/wav":
        return None
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            return audio.getnframes() / frame_rate if frame_rate > 0 else None
    except (EOFError, OSError, wave.Error):
        return None


def _ensure_disk_space(directory: Path, required_bytes: int) -> None:
    try:
        usage = shutil.disk_usage(directory)
    except OSError:
        return
    reserve = 256 * 1024 * 1024
    if usage.free < required_bytes + reserve:
        raise AssetUploadError("Not enough free disk space to store this audio file.")


def _optimized_icon_png(data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            icon = image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise AssetUploadError("File does not appear to be a valid image.") from exc

    if icon.width > MAX_WORKFLOW_ICON_SIZE or icon.height > MAX_WORKFLOW_ICON_SIZE:
        icon.thumbnail((MAX_WORKFLOW_ICON_SIZE, MAX_WORKFLOW_ICON_SIZE), Image.Resampling.LANCZOS)

    output = BytesIO()
    icon.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _asset_id_from_icon_id(icon_id: str) -> str:
    if not icon_id.startswith("asset:"):
        raise ValueError("Only imported workflow icons can be deleted.")
    return _validate_asset_id(icon_id.removeprefix("asset:"))


def workflow_icon_asset_id(icon_id: str) -> str:
    return _asset_id_from_icon_id(icon_id)


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
    if not any(asset_id.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".wav", ".mp3", ".flac", ".ogg", ".m4a")):
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
