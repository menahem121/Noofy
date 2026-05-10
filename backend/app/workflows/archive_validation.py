from __future__ import annotations

import stat
import zipfile
from pathlib import PurePosixPath


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
    for info in infos:
        name = safe_archive_name(info.filename)
        if zip_member_is_symlink(info):
            raise ArchiveValidationError(
                f"Workflow package contains an unsupported symlink: {name}"
            )
        total_uncompressed += info.file_size
        if total_uncompressed > max_total_uncompressed_bytes:
            raise ArchiveValidationError("Workflow package expands to too much data.")
        if info.is_dir():
            continue
        if ignored_archive_member(name):
            continue
        if name in raw_members:
            raise ArchiveValidationError(
                f"Workflow package contains duplicate file path: {name}"
            )
        raw_members[name] = info

    root_prefix = single_wrapper_root(raw_members, required_files=required_files)
    members: dict[str, zipfile.ZipInfo] = {}
    for name, info in raw_members.items():
        normalized_name = strip_wrapper_root(name, root_prefix)
        if normalized_name is None or ignored_archive_member(normalized_name):
            continue
        if normalized_name in members:
            raise ArchiveValidationError(
                f"Workflow package contains duplicate file path: {normalized_name}"
            )
        members[normalized_name] = info

    missing = sorted(required_files - set(members))
    if missing:
        raise ArchiveValidationError(
            "Workflow package is missing required files: " + ", ".join(missing)
        )
    return members


def safe_archive_name(name: str) -> str:
    if "\\" in name:
        raise ArchiveValidationError(f"Workflow package contains an unsafe path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise ArchiveValidationError(
            f"Workflow package contains an absolute path: {name}"
        )
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveValidationError(f"Workflow package contains an unsafe path: {name}")
    return str(path)


def ignored_archive_member(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (
        not parts
        or parts[0] == "__MACOSX"
        or parts[-1] == ".DS_Store"
        or parts[-1].startswith("._")
    )


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
    return stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK
