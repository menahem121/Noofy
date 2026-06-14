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
ALLOWED_VIDEO_MIME_TYPES = frozenset(
    {
        "video/mp4",
        "application/mp4",
        "video/quicktime",
        "video/x-quicktime",
        "video/webm",
        "application/webm",
        "video/x-matroska",
        "video/matroska",
        "application/x-matroska",
        "application/octet-stream",
    }
)
ALLOWED_THREE_D_MIME_TYPES = frozenset(
    {
        "model/gltf-binary",
        "model/gltf+json",
        "model/obj",
        "model/stl",
        "model/fbx",
        "model/ply",
        "application/json",
        "application/octet-stream",
        "text/plain",
    }
)
MAX_ASSET_BYTES = 25 * 1024 * 1024  # 25 MB
MAX_AUDIO_ASSET_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB
MAX_VIDEO_ASSET_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB
MAX_FILE_ASSET_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB
MAX_THREE_D_ASSET_BYTES = 100 * 1024 * 1024 * 1024  # 100 GB
MAX_GLTF_JSON_BYTES = 16 * 1024 * 1024  # 16 MB
MAX_WORKFLOW_ICON_SIZE = 256
_STREAM_CHUNK_BYTES = 1024 * 1024


class AssetUploadError(ValueError):
    pass


class DashboardAssetService:
    def __init__(self, assets_dir: Path, log_store: DiagnosticsSink | None = None) -> None:
        self._dir = assets_dir
        self.log_store = log_store

    @property
    def assets_dir(self) -> Path:
        return self._dir

    def list_asset_ids(self) -> set[str]:
        if not self._dir.exists():
            return set()
        return {
            path.name
            for path in self._dir.iterdir()
            if path.is_file() and not path.name.endswith(".meta.json")
        }

    def delete(self, asset_id: str) -> bool:
        safe = _validate_asset_id(asset_id)
        removed = False
        for path in (self._dir / safe, self._dir / f"{safe}.meta.json"):
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                continue
        return removed

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

    def store_image_from_file(
        self,
        source_path: Path,
        content_type: str | None,
        original_filename: str,
        *,
        source_gallery_item_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            data = source_path.read_bytes()
        except OSError as exc:
            raise AssetUploadError("Image file could not be read.") from exc

        normalized_content_type = _normalized_image_content_type(content_type, original_filename)
        metadata = self._validated_image_metadata(data, normalized_content_type)
        ext = _safe_ext(metadata["content_type"])
        asset_id = f"{uuid.uuid4()}{ext}"
        asset_path = self._dir / asset_id
        meta_path = self._dir / f"{asset_id}.meta.json"
        record = {
            "asset_id": asset_id,
            "kind": "image",
            "original_filename": _safe_original_filename(original_filename),
            "content_type": metadata["content_type"],
            "size": len(data),
            "format": ext.removeprefix("."),
            "width": metadata["width"],
            "height": metadata["height"],
        }
        if source_gallery_item_id:
            record["source_gallery_item_id"] = source_gallery_item_id

        self._dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(asset_path, data)
        _atomic_write_json(meta_path, record)
        if self.log_store is not None:
            self.log_store.add(
                "info",
                "Stored dashboard image asset",
                "workflow.assets",
                details={
                    "asset_id": asset_id,
                    "content_type": record["content_type"],
                    "size": len(data),
                    "source_gallery_item_id": source_gallery_item_id,
                },
            )
        return record

    def store_masked_image(
        self,
        *,
        source_asset_id: str,
        mask_data: bytes,
        mask_content_type: str,
        original_filename: str = "mask.png",
    ) -> dict[str, Any]:
        safe_source_id = _validate_asset_id(source_asset_id)
        source_path = self.asset_path(safe_source_id)
        if not source_path.exists():
            raise AssetUploadError("Source image asset was not found.")

        if len(mask_data) > MAX_ASSET_BYTES:
            raise AssetUploadError("Mask exceeds the 25 MB size limit.")
        if _normalized_image_content_type(mask_content_type, original_filename) != "image/png":
            raise AssetUploadError("Mask must be a PNG image.")
        if _detect_image_content_type(mask_data) != "image/png":
            raise AssetUploadError("Mask content does not appear to be a PNG image.")

        try:
            with Image.open(source_path) as source_image:
                source_rgba = source_image.convert("RGBA")
            with Image.open(BytesIO(mask_data)) as mask_image:
                mask_rgba = mask_image.convert("RGBA")
        except (OSError, UnidentifiedImageError) as exc:
            raise AssetUploadError("Source image or mask could not be opened.") from exc

        if source_rgba.size != mask_rgba.size:
            raise AssetUploadError("Mask dimensions must match the source image.")

        mask_alpha = mask_rgba.getchannel("A")
        output_alpha = mask_alpha.point(lambda value: 255 - value)
        output_image = source_rgba.copy()
        output_image.putalpha(output_alpha)

        output = BytesIO()
        output_image.save(output, format="PNG", compress_level=4)
        data = output.getvalue()

        source_metadata = self.metadata(safe_source_id)
        root_source_id = source_metadata.get("source_asset_id")
        if not isinstance(root_source_id, str) or not root_source_id.strip():
            root_source_id = safe_source_id

        record = {
            "asset_id": f"{uuid.uuid4()}.png",
            "kind": "image",
            "original_filename": _masked_original_filename(source_metadata.get("original_filename"), safe_source_id),
            "content_type": "image/png",
            "size": len(data),
            "format": "png",
            "width": output_image.width,
            "height": output_image.height,
            "has_mask": True,
            "source_asset_id": root_source_id,
        }
        source_gallery_item_id = source_metadata.get("source_gallery_item_id")
        if isinstance(source_gallery_item_id, str) and source_gallery_item_id.strip():
            record["source_gallery_item_id"] = source_gallery_item_id

        self._dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(self._dir / record["asset_id"], data)
        _atomic_write_json(self._dir / f"{record['asset_id']}.meta.json", record)
        if self.log_store is not None:
            self.log_store.add(
                "info",
                "Stored dashboard masked image asset",
                "workflow.assets",
                details={
                    "asset_id": record["asset_id"],
                    "source_asset_id": root_source_id,
                    "source_gallery_item_id": record.get("source_gallery_item_id"),
                    "width": output_image.width,
                    "height": output_image.height,
                    "size": len(data),
                },
            )
        return record

    def _validated_image_metadata(self, data: bytes, content_type: str) -> dict[str, Any]:
        if len(data) > MAX_ASSET_BYTES:
            raise AssetUploadError("File exceeds the 25 MB size limit.")
        if content_type not in ALLOWED_MIME_TYPES:
            raise AssetUploadError(f"File type '{content_type}' is not allowed.")
        detected = _detect_image_content_type(data)
        if detected is None:
            raise AssetUploadError("File does not appear to be a valid image.")
        if detected != content_type:
            raise AssetUploadError(f"File content does not match '{content_type}'.")
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
        except (OSError, UnidentifiedImageError) as exc:
            raise AssetUploadError("File does not appear to be a valid image.") from exc
        return {"content_type": detected, "width": width, "height": height}

    def store_audio_stream(
        self,
        source: BinaryIO,
        content_type: str,
        original_filename: str,
        *,
        declared_size: int | None = None,
    ) -> dict[str, Any]:
        return self._store_large_media_stream(
            source,
            content_type,
            original_filename,
            kind="audio",
            declared_size=declared_size,
            max_bytes=MAX_AUDIO_ASSET_BYTES,
            allowed_mime_types=ALLOWED_AUDIO_MIME_TYPES,
            normalize_content_type=_normalized_audio_content_type,
            detect_content_type=_detect_audio_content_type,
            content_type_matches=_audio_content_type_matches,
            extension_for=lambda filename, detected, declared: _safe_audio_ext(detected),
            validate_filename_extension=_validate_audio_filename_extension,
            canonical_content_type=lambda filename, detected: _canonical_audio_content_type(detected),
            extra_metadata=lambda path, detected: _audio_metadata(path, detected),
        )

    def store_video_stream(
        self,
        source: BinaryIO,
        content_type: str,
        original_filename: str,
        *,
        declared_size: int | None = None,
    ) -> dict[str, Any]:
        return self._store_large_media_stream(
            source,
            content_type,
            original_filename,
            kind="video",
            declared_size=declared_size,
            max_bytes=MAX_VIDEO_ASSET_BYTES,
            allowed_mime_types=ALLOWED_VIDEO_MIME_TYPES,
            normalize_content_type=_normalized_video_content_type,
            detect_content_type=_detect_video_container_family,
            content_type_matches=_video_content_type_matches,
            extension_for=_video_extension_for,
            validate_filename_extension=_validate_video_filename_extension,
            canonical_content_type=_canonical_video_content_type,
        )

    def store_file_stream(
        self,
        source: BinaryIO,
        content_type: str,
        original_filename: str,
        *,
        accepted_extensions: list[str],
        accepted_mime_types: list[str],
        declared_size: int | None = None,
    ) -> dict[str, Any]:
        rules = _normalize_file_accept_rules(accepted_extensions, accepted_mime_types)
        normalized_content_type = _normalized_file_content_type(content_type)
        selected_extension = _file_extension_for(original_filename, normalized_content_type, rules)

        def extension_for(filename: str, _detected: str, declared: str) -> str:
            return selected_extension

        return self._store_large_media_stream(
            source,
            content_type,
            original_filename,
            kind="file",
            declared_size=declared_size,
            max_bytes=MAX_FILE_ASSET_BYTES,
            allowed_mime_types=None,
            normalize_content_type=lambda declared, _filename: _normalized_file_content_type(declared),
            detect_content_type=lambda _data: "file",
            content_type_matches=lambda _declared, _detected: True,
            extension_for=extension_for,
            validate_filename_extension=lambda _filename, _ext: None,
            canonical_content_type=lambda _filename, _detected: normalized_content_type,
            extra_metadata=lambda _path, _detected: {"extension": selected_extension},
        )

    def store_three_d_stream(
        self,
        source: BinaryIO,
        content_type: str,
        original_filename: str,
        *,
        declared_size: int | None = None,
    ) -> dict[str, Any]:
        extension = _validate_three_d_filename_extension(original_filename)
        return self._store_large_media_stream(
            source,
            content_type,
            original_filename,
            kind="3d",
            declared_size=declared_size,
            max_bytes=MAX_THREE_D_ASSET_BYTES,
            allowed_mime_types=ALLOWED_THREE_D_MIME_TYPES,
            normalize_content_type=_normalized_three_d_content_type,
            detect_content_type=lambda _data: "3d",
            content_type_matches=lambda _declared, _detected: True,
            extension_for=lambda _filename, _detected, _declared: extension,
            validate_filename_extension=lambda _filename, _ext: None,
            canonical_content_type=lambda _filename, _detected: _canonical_three_d_content_type(extension),
            extra_metadata=lambda path, _detected: _three_d_metadata(path, extension),
        )

    def _store_large_media_stream(
        self,
        source: BinaryIO,
        content_type: str,
        original_filename: str,
        *,
        kind: str,
        declared_size: int | None,
        max_bytes: int,
        allowed_mime_types: frozenset[str] | None,
        normalize_content_type,
        detect_content_type,
        content_type_matches,
        extension_for,
        validate_filename_extension,
        canonical_content_type,
        extra_metadata=None,
    ) -> dict[str, Any]:
        original_filename = _safe_original_filename(original_filename)
        if declared_size is not None and declared_size > max_bytes:
            self._log_media_upload_failure(kind, "File exceeds the 100 GB size limit.")
            raise AssetUploadError("File exceeds the 100 GB size limit.")

        normalized_content_type = normalize_content_type(content_type, original_filename)
        if allowed_mime_types is not None and normalized_content_type not in allowed_mime_types:
            self._log_media_upload_failure(kind, f"File type '{content_type}' is not allowed.")
            raise AssetUploadError(f"File type '{content_type}' is not allowed.")

        self._dir.mkdir(parents=True, exist_ok=True)
        if declared_size is not None:
            try:
                _ensure_disk_space(self._dir, declared_size, kind)
            except AssetUploadError as exc:
                self._log_media_upload_failure(kind, str(exc))
                raise

        asset_id: str | None = None
        tmp_path: str | None = None
        total = 0
        first_bytes = b""
        fd, tmp_path = tempfile.mkstemp(dir=self._dir, suffix=f".{kind}-upload.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                while True:
                    chunk = source.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    if len(first_bytes) < 4096:
                        first_bytes += bytes(chunk[: 4096 - len(first_bytes)])
                    total += len(chunk)
                    if total > max_bytes:
                        raise AssetUploadError("File exceeds the 100 GB size limit.")
                    if total % (64 * 1024 * 1024) < len(chunk):
                        _ensure_disk_space(self._dir, len(chunk), kind)
                    f.write(chunk)

            detected = detect_content_type(first_bytes)
            if detected is None:
                raise AssetUploadError(f"File does not appear to be a valid {kind} file.")
            if not content_type_matches(normalized_content_type, detected):
                raise AssetUploadError(f"File content does not match '{content_type}'.")

            ext = extension_for(original_filename, detected, normalized_content_type)
            validate_filename_extension(original_filename, ext)
            asset_id = f"{uuid.uuid4()}{ext}"
            asset_path = self._dir / asset_id
            meta_path = self._dir / f"{asset_id}.meta.json"
            metadata = {
                "asset_id": asset_id,
                "kind": kind,
                "original_filename": original_filename,
                "content_type": canonical_content_type(original_filename, detected),
                "size": total,
                "format": ext.removeprefix("."),
            }
            if extra_metadata is not None:
                metadata.update(extra_metadata(Path(tmp_path), detected))

            os.replace(tmp_path, asset_path)
            tmp_path = None
            _atomic_write_json(meta_path, metadata)
            if self.log_store is not None:
                self.log_store.add(
                    "info",
                    f"Stored dashboard {kind} asset",
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
            self._log_media_upload_failure(kind, str(exc))
            raise

    def _log_media_upload_failure(self, kind: str, error: str) -> None:
        if self.log_store is not None:
            self.log_store.add(
                "warning",
                f"Dashboard {kind} asset upload failed",
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
            if raw.get("kind") in {"audio", "video", "3d"}:
                metadata["kind"] = raw["kind"]
                if isinstance(raw.get("size"), int):
                    metadata["size"] = raw["size"]
                if isinstance(raw.get("format"), str):
                    metadata["format"] = raw["format"]
                if isinstance(raw.get("extension"), str):
                    metadata["extension"] = raw["extension"]
                if isinstance(raw.get("duration_seconds"), (int, float)):
                    metadata["duration_seconds"] = raw["duration_seconds"]
                if isinstance(raw.get("width"), int):
                    metadata["width"] = raw["width"]
                if isinstance(raw.get("height"), int):
                    metadata["height"] = raw["height"]
                if isinstance(raw.get("fps"), (int, float)):
                    metadata["fps"] = raw["fps"]
            elif raw.get("kind") == "file":
                metadata["kind"] = "file"
                if isinstance(raw.get("size"), int):
                    metadata["size"] = raw["size"]
                if isinstance(raw.get("format"), str):
                    metadata["format"] = raw["format"]
                if isinstance(raw.get("extension"), str):
                    metadata["extension"] = raw["extension"]
            elif raw.get("kind") == "image":
                metadata["kind"] = "image"
                if isinstance(raw.get("size"), int):
                    metadata["size"] = raw["size"]
                if isinstance(raw.get("format"), str):
                    metadata["format"] = raw["format"]
                if isinstance(raw.get("width"), int):
                    metadata["width"] = raw["width"]
                if isinstance(raw.get("height"), int):
                    metadata["height"] = raw["height"]
                if isinstance(raw.get("has_mask"), bool):
                    metadata["has_mask"] = raw["has_mask"]
                if isinstance(raw.get("source_asset_id"), str):
                    metadata["source_asset_id"] = raw["source_asset_id"]
                if isinstance(raw.get("source_gallery_item_id"), str):
                    metadata["source_gallery_item_id"] = raw["source_gallery_item_id"]
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


def _normalized_image_content_type(content_type: str | None, original_filename: str) -> str:
    normalized = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    if normalized != "application/octet-stream":
        return normalized
    guessed, _ = mimetypes.guess_type(original_filename)
    return (guessed or normalized).lower()


def _masked_original_filename(original_filename: Any, fallback_asset_id: str) -> str:
    raw = original_filename if isinstance(original_filename, str) and original_filename.strip() else fallback_asset_id
    safe = _safe_original_filename(raw)
    path = Path(safe)
    stem = path.stem or "image"
    return f"{stem}-mask.png"


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


def _validate_three_d_filename_extension(original_filename: str) -> str:
    suffix = Path(_safe_original_filename(original_filename)).suffix.lower()
    if suffix not in {".glb", ".gltf", ".obj", ".stl", ".fbx", ".ply"}:
        raise AssetUploadError(
            f"File extension '{suffix or '<missing>'}' is not supported. Use GLB, GLTF, OBJ, STL, FBX, or PLY."
        )
    return suffix


def _normalized_three_d_content_type(content_type: str, original_filename: str) -> str:
    normalized = _normalized_file_content_type(content_type)
    if normalized == "application/json" and Path(original_filename).suffix.lower() == ".gltf":
        return "model/gltf+json"
    if normalized != "application/octet-stream":
        return normalized
    return _canonical_three_d_content_type(Path(original_filename).suffix.lower())


def _canonical_three_d_content_type(extension: str) -> str:
    return {
        ".glb": "model/gltf-binary",
        ".gltf": "model/gltf+json",
        ".obj": "model/obj",
        ".stl": "model/stl",
        ".fbx": "model/fbx",
        ".ply": "model/ply",
    }.get(extension, "application/octet-stream")


def _three_d_metadata(path: Path, extension: str) -> dict[str, str]:
    if extension == ".gltf":
        _validate_self_contained_gltf(path)
    return {"extension": extension}


def _validate_self_contained_gltf(path: Path) -> None:
    if path.stat().st_size > MAX_GLTF_JSON_BYTES:
        raise AssetUploadError("GLTF JSON exceeds the 16 MB preview limit. Convert this model to GLB.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetUploadError("GLTF file is not valid JSON. Convert this model to GLB.") from exc
    if not isinstance(payload, dict):
        raise AssetUploadError("GLTF file is invalid. Convert this model to GLB.")
    def validate_dependencies(value: Any) -> None:
        if isinstance(value, dict):
            uri = value.get("uri")
            if isinstance(uri, str) and not uri.startswith("data:"):
                raise AssetUploadError("Multi-file GLTF models are not supported yet. Convert this model to GLB.")
            for child in value.values():
                validate_dependencies(child)
        elif isinstance(value, list):
            for child in value:
                validate_dependencies(child)

    validate_dependencies(payload)


def _normalized_file_content_type(content_type: str) -> str:
    return (content_type or "application/octet-stream").split(";")[0].strip().lower() or "application/octet-stream"


def _normalize_file_accept_rules(
    accepted_extensions: list[str],
    accepted_mime_types: list[str],
) -> tuple[set[str], set[str]]:
    extensions: set[str] = set()
    for raw in accepted_extensions:
        if not isinstance(raw, str):
            continue
        value = raw.strip().lower()
        if not value:
            continue
        if not value.startswith("."):
            value = f".{value}"
        if "/" in value or "\\" in value or ".." in value or not value[1:]:
            raise AssetUploadError(f"Accepted file extension '{raw}' is invalid.")
        if not all(part and part.replace("-", "").replace("_", "").isalnum() for part in value.split(".")[1:]):
            raise AssetUploadError(f"Accepted file extension '{raw}' is invalid.")
        extensions.add(value)

    mime_types: set[str] = set()
    for raw in accepted_mime_types:
        if not isinstance(raw, str):
            continue
        value = raw.split(";")[0].strip().lower()
        if not value:
            continue
        if "/" not in value or any(char.isspace() for char in value):
            raise AssetUploadError(f"Accepted MIME type '{raw}' is invalid.")
        mime_types.add(value)

    if not extensions and not mime_types:
        raise AssetUploadError("This file input does not declare accepted file types.")
    return extensions, mime_types


def _file_extension_for(
    original_filename: str,
    declared_content_type: str,
    rules: tuple[set[str], set[str]],
) -> str:
    accepted_extensions, accepted_mime_types = rules
    filename = _safe_original_filename(original_filename).lower()
    matched_extension = _matched_file_extension(filename, accepted_extensions)

    if accepted_extensions and matched_extension is None:
        raise AssetUploadError("File extension is not allowed for this input.")

    normalized_content_type = _normalized_file_content_type(declared_content_type)
    if accepted_mime_types:
        if normalized_content_type == "application/octet-stream":
            if matched_extension is None:
                raise AssetUploadError("File type is not allowed for this input.")
        elif normalized_content_type not in accepted_mime_types:
            raise AssetUploadError(f"File type '{declared_content_type}' is not allowed for this input.")

    extension = matched_extension or _safe_file_suffix(filename)
    return extension or ".bin"


def _matched_file_extension(filename: str, accepted_extensions: set[str]) -> str | None:
    for extension in sorted(accepted_extensions, key=len, reverse=True):
        if filename.endswith(extension):
            return extension
    return None


def _safe_file_suffix(filename: str) -> str:
    suffixes = Path(filename).suffixes
    if not suffixes:
        return ""
    suffix = "".join(suffixes).lower()
    if "/" in suffix or "\\" in suffix or ".." in suffix:
        return ""
    if not all(part and part.replace("-", "").replace("_", "").isalnum() for part in suffix.split(".")[1:]):
        return ""
    return suffix[:64]


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


def _audio_metadata(path: Path, detected_content_type: str) -> dict[str, float]:
    duration_seconds = _audio_duration_seconds(path, detected_content_type)
    return {"duration_seconds": duration_seconds} if duration_seconds is not None else {}


def _normalized_video_content_type(content_type: str, original_filename: str) -> str:
    normalized = (content_type or "application/octet-stream").split(";")[0].strip().lower()
    aliases = {
        "application/mp4": "video/mp4",
        "video/x-quicktime": "video/quicktime",
        "application/webm": "video/webm",
        "video/matroska": "video/x-matroska",
        "application/x-matroska": "video/x-matroska",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized != "application/octet-stream":
        return normalized
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }.get(Path(original_filename).suffix.lower(), normalized)


def _detect_video_container_family(data: bytes) -> str | None:
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "iso-bmff"
    if data.startswith(b"\x1aE\xdf\xa3"):
        return "ebml"
    return None


def _video_content_type_matches(declared: str, detected_family: str) -> bool:
    if declared == "application/octet-stream":
        return True
    allowed_by_family = {
        "iso-bmff": {"video/mp4", "video/quicktime"},
        "ebml": {"video/webm", "video/x-matroska"},
    }
    return declared in allowed_by_family.get(detected_family, set())


def _video_extension_for(original_filename: str, detected_family: str, declared_content_type: str) -> str:
    suffix = Path(original_filename).suffix.lower()
    allowed_by_family = {
        "iso-bmff": {".mp4", ".mov"},
        "ebml": {".webm", ".mkv"},
    }
    if suffix not in allowed_by_family.get(detected_family, set()):
        raise AssetUploadError(f"File extension '{suffix or '<missing>'}' does not match the video content.")
    expected_content_type = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }[suffix]
    if declared_content_type != expected_content_type:
        raise AssetUploadError(f"File extension '{suffix}' does not match '{declared_content_type}'.")
    return suffix


def _validate_video_filename_extension(original_filename: str, detected_ext: str) -> None:
    suffix = Path(original_filename).suffix.lower()
    if suffix not in {".mp4", ".mov", ".webm", ".mkv"}:
        raise AssetUploadError(f"File extension '{suffix or '<missing>'}' is not allowed.")
    if suffix != detected_ext:
        raise AssetUploadError(f"File extension '{suffix}' does not match the video content.")


def _canonical_video_content_type(original_filename: str, detected_family: str) -> str:
    suffix = Path(original_filename).suffix.lower()
    mapping = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
    }
    if suffix not in mapping:
        raise AssetUploadError(f"File extension '{suffix or '<missing>'}' is not allowed.")
    return mapping[suffix]


def _ensure_disk_space(directory: Path, required_bytes: int, kind: str) -> None:
    try:
        usage = shutil.disk_usage(directory)
    except OSError:
        return
    reserve = 256 * 1024 * 1024
    if usage.free < required_bytes + reserve:
        raise AssetUploadError(f"Not enough free disk space to store this {kind} file.")


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
    stem, *suffix_parts = asset_id.split(".")
    try:
        uuid.UUID(stem)
    except ValueError as exc:
        raise ValueError(f"Invalid asset_id: {asset_id!r}") from exc
    if not suffix_parts or len(".".join(suffix_parts)) > 96:
        raise ValueError(f"Invalid asset_id: {asset_id!r}")
    for part in suffix_parts:
        if not part or not part.replace("-", "").replace("_", "").isalnum():
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
