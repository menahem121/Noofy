from __future__ import annotations

import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

DEFAULT_STREAM_CHUNK_BYTES = 1024 * 1024


class PathSafetyError(ValueError):
    def __init__(self, reason: str, path: str) -> None:
        super().__init__(f"{reason}: {path}")
        self.reason = reason
        self.path = path


class StreamLimitError(ValueError):
    def __init__(self, *, max_bytes: int, copied_bytes: int) -> None:
        super().__init__("stream exceeds the configured byte limit")
        self.max_bytes = max_bytes
        self.copied_bytes = copied_bytes


def safe_relative_posix_path(value: str, *, allow_nested: bool) -> str:
    if "\\" in value:
        raise PathSafetyError("backslash", value)
    path = PurePosixPath(value)
    raw_parts = value.split("/")
    if path.is_absolute() or not path.parts or any(
        part in {"", ".", ".."} for part in raw_parts
    ):
        raise PathSafetyError("path_traversal", value)
    if not allow_nested and len(path.parts) != 1:
        raise PathSafetyError("nested_path", value)
    return value


@dataclass
class MaterializedPathIndex:
    file_paths: set[str] = field(default_factory=set)
    directory_paths: set[str] = field(default_factory=set)
    explicit_paths: set[str] = field(default_factory=set)

    def add(self, value: str, *, is_directory: bool = False) -> None:
        casefolded_parts = tuple(
            part.casefold() for part in PurePosixPath(value).parts
        )
        casefolded_path = "/".join(casefolded_parts)
        parent_paths = {
            "/".join(casefolded_parts[:index])
            for index in range(1, len(casefolded_parts))
        }
        if casefolded_path in self.explicit_paths or bool(parent_paths & self.file_paths):
            raise PathSafetyError("collision", value)
        if is_directory:
            if casefolded_path in self.file_paths:
                raise PathSafetyError("collision", value)
            self.directory_paths.add(casefolded_path)
        else:
            if casefolded_path in self.directory_paths:
                raise PathSafetyError("collision", value)
            self.file_paths.add(casefolded_path)
        self.explicit_paths.add(casefolded_path)
        self.directory_paths.update(parent_paths)


def zip_member_unsafe_reason(info: zipfile.ZipInfo) -> str | None:
    file_type = stat.S_IFMT(info.external_attr >> 16)
    if file_type == stat.S_IFLNK:
        return "symlink"
    if not info.is_dir() and file_type not in {0, stat.S_IFREG}:
        return "special_file"
    return None


def ignored_archive_member(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (
        not parts
        or parts[0] == "__MACOSX"
        or parts[-1] == ".DS_Store"
        or parts[-1].startswith("._")
    )


def path_is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def contained_destination(root: Path, relative_path: str) -> Path:
    if "\\" in relative_path:
        raise PathSafetyError("backslash", relative_path)
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    resolved_root = root.resolve(strict=False)
    resolved_destination = destination.resolve(strict=False)
    if (
        resolved_destination == resolved_root
        or not path_is_within(resolved_root, resolved_destination)
    ):
        raise PathSafetyError("destination_escape", relative_path)
    return destination


def copy_stream_limited(
    source: BinaryIO,
    destination: BinaryIO,
    *,
    max_bytes: int,
    chunk_bytes: int = DEFAULT_STREAM_CHUNK_BYTES,
) -> int:
    if max_bytes < 0:
        raise StreamLimitError(max_bytes=max_bytes, copied_bytes=0)
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    copied_bytes = 0
    while True:
        chunk = source.read(min(chunk_bytes, max_bytes - copied_bytes + 1))
        if not chunk:
            return copied_bytes
        copied_bytes += len(chunk)
        if copied_bytes > max_bytes:
            raise StreamLimitError(
                max_bytes=max_bytes,
                copied_bytes=copied_bytes,
            )
        destination.write(chunk)
