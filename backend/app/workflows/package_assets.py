from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any

from app.workflows.media_values import target_media_kind_for_input
from app.workflows.package import WorkflowInput

PACKAGE_ASSET_SOURCE = "package_asset"
PACKAGE_INPUT_DEFAULTS_PREFIX = "input-defaults"

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._/-]+$")


class PackageAssetError(ValueError):
    pass


def is_package_asset_value(value: Any) -> bool:
    return isinstance(value, dict) and value.get("source") == PACKAGE_ASSET_SOURCE


def package_asset_id(value: Any) -> str | None:
    if not is_package_asset_value(value):
        return None
    asset_id = value.get("asset_id")
    if not isinstance(asset_id, str):
        return None
    try:
        return safe_package_asset_id(asset_id)
    except PackageAssetError:
        return None


def safe_package_asset_id(asset_id: str) -> str:
    cleaned = asset_id.strip().replace("\\", "/")
    if not cleaned:
        raise PackageAssetError("Package asset reference is missing an asset id.")
    if cleaned.startswith("/") or cleaned.startswith("../") or "/../" in cleaned:
        raise PackageAssetError("Package asset reference contains an unsafe path.")
    parts = PurePosixPath(cleaned).parts
    if not parts or parts[0] != PACKAGE_INPUT_DEFAULTS_PREFIX:
        raise PackageAssetError("Package asset reference must live under input-defaults/.")
    if any(part in {"", ".", ".."} for part in parts):
        raise PackageAssetError("Package asset reference contains an unsafe path segment.")
    if not _SAFE_ID_RE.fullmatch(cleaned):
        raise PackageAssetError("Package asset reference contains unsafe characters.")
    return str(PurePosixPath(*parts))


def package_asset_archive_path(asset_id: str) -> str:
    return f"assets/{safe_package_asset_id(asset_id)}"


def package_asset_disk_path(package_dir: Path, asset_id: str) -> Path:
    safe_id = safe_package_asset_id(asset_id)
    target = package_dir / "assets" / Path(*PurePosixPath(safe_id).parts)
    try:
        target.relative_to(package_dir / "assets")
    except ValueError as exc:
        raise PackageAssetError("Package asset reference escapes the package asset directory.") from exc
    return target


def package_asset_source_candidates(package_dir: Path, asset_id: str) -> list[Path]:
    safe_id = safe_package_asset_id(asset_id)
    rel = Path(*PurePosixPath(safe_id).parts)
    candidates = [package_dir / "assets" / rel]
    source_candidate = package_dir / "source-files" / "assets" / rel
    if source_candidate not in candidates:
        candidates.append(source_candidate)
    return candidates


def validate_package_asset_reference(
    value: Any,
    *,
    workflow_input: WorkflowInput | None = None,
) -> dict[str, Any]:
    if not is_package_asset_value(value):
        raise PackageAssetError("Default value is not a package asset reference.")
    asset_id = safe_package_asset_id(str(value.get("asset_id", "")))
    kind = value.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise PackageAssetError("Package asset reference is missing a media kind.")
    expected_kind = target_media_kind_for_input(workflow_input) if workflow_input is not None else None
    if expected_kind is not None and kind != expected_kind:
        raise PackageAssetError("Package asset kind does not match the workflow input.")
    filename = value.get("filename")
    if filename is not None and not isinstance(filename, str):
        raise PackageAssetError("Package asset filename must be a string.")
    content_type = value.get("content_type")
    if content_type is not None and not isinstance(content_type, str):
        raise PackageAssetError("Package asset content type must be a string.")
    size_bytes = value.get("size_bytes")
    if size_bytes is not None and (not isinstance(size_bytes, int) or size_bytes < 0):
        raise PackageAssetError("Package asset size must be a non-negative integer.")
    sha256 = value.get("sha256")
    if sha256 is not None and (
        not isinstance(sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", sha256)
    ):
        raise PackageAssetError("Package asset sha256 must be sha256:<hex>.")
    return {
        "source": PACKAGE_ASSET_SOURCE,
        "asset_id": asset_id,
        "kind": kind,
        **({"filename": safe_display_filename(filename)} if filename else {}),
        **({"content_type": content_type} if content_type else {}),
        **({"size_bytes": size_bytes} if isinstance(size_bytes, int) else {}),
        **({"sha256": sha256} if isinstance(sha256, str) else {}),
    }


def make_package_asset_reference(
    *,
    source_path: Path,
    kind: str,
    asset_id_prefix: str = PACKAGE_INPUT_DEFAULTS_PREFIX,
    original_filename: str | None = None,
    content_type: str | None = None,
) -> tuple[dict[str, Any], str]:
    data = source_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    suffix = source_path.suffix.lower() or mimetypes.guess_extension(content_type or "") or ".bin"
    safe_name = safe_display_filename(original_filename or source_path.name) or f"default{suffix}"
    stem = Path(safe_name).stem or "default"
    extension = Path(safe_name).suffix.lower() or suffix
    asset_id = safe_package_asset_id(f"{asset_id_prefix}/{digest[:16]}-{slugify_filename(stem)}{extension}")
    guessed_content_type = content_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    return (
        {
            "source": PACKAGE_ASSET_SOURCE,
            "asset_id": asset_id,
            "kind": kind,
            "filename": safe_name,
            "content_type": guessed_content_type,
            "size_bytes": len(data),
            "sha256": f"sha256:{digest}",
        },
        asset_id,
    )


def copy_package_asset(source_path: Path, package_dir: Path, asset_id: str) -> Path:
    target = package_asset_disk_path(package_dir, asset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() != target.resolve():
        shutil.copy2(source_path, target)
    return target


def write_package_asset_metadata(package_dir: Path, reference: dict[str, Any]) -> Path:
    asset_id = safe_package_asset_id(str(reference["asset_id"]))
    meta_path = package_asset_disk_path(package_dir, f"{asset_id}.meta.json")
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(reference, indent=2, sort_keys=True), encoding="utf-8")
    return meta_path


def validate_package_asset_file(source_path: Path, reference: dict[str, Any]) -> None:
    size_bytes = reference.get("size_bytes")
    sha256 = reference.get("sha256")
    try:
        stat = source_path.stat()
    except OSError as exc:
        raise PackageAssetError("Package asset file could not be read.") from exc
    if isinstance(size_bytes, int) and stat.st_size != size_bytes:
        raise PackageAssetError("Package asset file size does not match its reference metadata.")
    if isinstance(sha256, str):
        expected = sha256.removeprefix("sha256:")
        hasher = hashlib.sha256()
        try:
            with source_path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    hasher.update(chunk)
        except OSError as exc:
            raise PackageAssetError("Package asset file could not be read.") from exc
        if hasher.hexdigest() != expected:
            raise PackageAssetError("Package asset file content does not match its reference metadata.")


def safe_display_filename(value: str | None) -> str:
    if not value:
        return ""
    name = Path(str(value).replace("\\", "/")).name
    cleaned = _SAFE_FILENAME_RE.sub("_", name).strip("._ ")
    return cleaned[:120] or "default.bin"


def slugify_filename(value: str) -> str:
    return (_SAFE_FILENAME_RE.sub("-", value).strip("-._").lower() or "default")[:64]
