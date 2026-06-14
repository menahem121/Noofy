from __future__ import annotations

import zipfile
from pathlib import PurePosixPath

from app.archive_safety import (
    MaterializedPathIndex,
    PathSafetyError,
    ignored_archive_member,
    safe_relative_posix_path,
    zip_member_unsafe_reason,
)


class ArchiveValidationError(RuntimeError):
    """Raised when a workflow archive cannot be safely inspected."""


def validate_archive_members(
    archive: zipfile.ZipFile,
    *,
    required_files: set[str],
    max_files: int,
    max_total_uncompressed_bytes: int,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > max_files:
        raise ArchiveValidationError("Workflow package contains too many files.")

    total_uncompressed = 0
    raw_members: dict[str, zipfile.ZipInfo] = {}
    raw_path_index = MaterializedPathIndex()
    for info in infos:
        archive_name = (
            info.filename[:-1]
            if info.is_dir() and info.filename.endswith("/")
            else info.filename
        )
        name = safe_archive_name(archive_name)
        unsafe_reason = zip_member_unsafe_reason(info)
        if unsafe_reason == "symlink":
            raise ArchiveValidationError(
                f"Workflow package contains an unsupported symlink: {name}"
            )
        if unsafe_reason == "special_file":
            raise ArchiveValidationError(
                f"Workflow package contains an unsupported special file: {name}"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > max_total_uncompressed_bytes:
            raise ArchiveValidationError("Workflow package expands to too much data.")
        if info.is_dir():
            continue
        if ignored_archive_member(name):
            continue
        try:
            raw_path_index.add(name)
        except PathSafetyError:
            raise ArchiveValidationError(
                f"Workflow package contains duplicate file path: {name}"
            ) from None
        raw_members[name] = info

    root_prefix = single_wrapper_root(raw_members, required_files=required_files)
    members: dict[str, zipfile.ZipInfo] = {}
    path_index = MaterializedPathIndex()
    for name, info in raw_members.items():
        normalized_name = strip_wrapper_root(name, root_prefix)
        if normalized_name is None or ignored_archive_member(normalized_name):
            continue
        try:
            path_index.add(normalized_name)
        except PathSafetyError:
            raise ArchiveValidationError(
                f"Workflow package contains duplicate file path: {normalized_name}"
            ) from None
        members[normalized_name] = info

    missing = sorted(required_files - set(members))
    if missing:
        raise ArchiveValidationError(
            "Workflow package is missing required files: " + ", ".join(missing)
        )
    return members


def safe_archive_name(name: str) -> str:
    try:
        return safe_relative_posix_path(name, allow_nested=True)
    except PathSafetyError as exc:
        if exc.reason == "path_traversal" and PurePosixPath(name).is_absolute():
            raise ArchiveValidationError(
                f"Workflow package contains an absolute path: {name}"
            ) from exc
        raise ArchiveValidationError(
            f"Workflow package contains an unsafe path: {name}"
        ) from exc


def single_wrapper_root(
    members: dict[str, zipfile.ZipInfo],
    *,
    required_files: set[str],
) -> str | None:
    if required_files <= set(members):
        return None

    roots: set[str] = set()
    for name in members:
        parts = PurePosixPath(name).parts
        if len(parts) > 1:
            roots.add(parts[0])

    for root in sorted(roots):
        root_files = {
            str(PurePosixPath(*PurePosixPath(name).parts[1:]))
            for name in members
            if PurePosixPath(name).parts[:1] == (root,)
            and len(PurePosixPath(name).parts) > 1
        }
        if required_files <= root_files:
            return root
    return None


def strip_wrapper_root(name: str, root_prefix: str | None) -> str | None:
    if root_prefix is None:
        return name
    parts = PurePosixPath(name).parts
    if parts[:1] != (root_prefix,):
        return None
    if len(parts) == 1:
        return None
    return str(PurePosixPath(*parts[1:]))


def zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return zip_member_unsafe_reason(info) == "symlink"
