from __future__ import annotations

import io
import stat
import zipfile

import pytest

from app.workflows.archive_validation import (
    ArchiveValidationError,
    validate_archive_members,
)


REQUIRED_FILES = {
    "package.json",
    "comfyui_graph.json",
    "capsule.lock.json",
    "export-report.json",
}


def _zip_bytes(entries: dict[str, str]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return payload.getvalue()


def test_validate_archive_members_strips_single_wrapper_root() -> None:
    data = _zip_bytes(
        {
            "workflow/package.json": "{}",
            "workflow/comfyui_graph.json": "{}",
            "workflow/capsule.lock.json": "{}",
            "workflow/export-report.json": "{}",
            "workflow/assets/thumbnail.png": "image",
            "__MACOSX/._ignored": "ignored",
        }
    )

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = validate_archive_members(
            archive,
            required_files=REQUIRED_FILES,
            max_files=20,
            max_total_uncompressed_bytes=1024,
        )

    assert set(members) == {
        "package.json",
        "comfyui_graph.json",
        "capsule.lock.json",
        "export-report.json",
        "assets/thumbnail.png",
    }


def test_validate_archive_members_rejects_traversal() -> None:
    data = _zip_bytes({"../evil.txt": "bad"})

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(ArchiveValidationError, match="unsafe path"):
            validate_archive_members(
                archive,
                required_files=REQUIRED_FILES,
                max_files=20,
                max_total_uncompressed_bytes=1024,
            )


@pytest.mark.parametrize(
    "archive_path",
    [
        "workflow//package.json",
        "workflow/./package.json",
        "workflow\\package.json",
    ],
)
def test_validate_archive_members_rejects_invalid_path_shape(
    archive_path: str,
) -> None:
    data = _zip_bytes({archive_path: "{}"})

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(ArchiveValidationError, match="unsafe path"):
            validate_archive_members(
                archive,
                required_files=REQUIRED_FILES,
                max_files=20,
                max_total_uncompressed_bytes=1024,
            )


def test_validate_archive_members_rejects_case_insensitive_collision() -> None:
    data = _zip_bytes(
        {
            "workflow/package.json": "{}",
            "workflow/Package.json": "{}",
        }
    )

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(ArchiveValidationError, match="duplicate file path"):
            validate_archive_members(
                archive,
                required_files=REQUIRED_FILES,
                max_files=20,
                max_total_uncompressed_bytes=1024,
            )


def test_validate_archive_members_rejects_file_directory_collision() -> None:
    data = _zip_bytes(
        {
            "workflow/custom_nodes": "file",
            "workflow/custom_nodes/node.py": "nested",
        }
    )

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with pytest.raises(ArchiveValidationError, match="duplicate file path"):
            validate_archive_members(
                archive,
                required_files=REQUIRED_FILES,
                max_files=20,
                max_total_uncompressed_bytes=1024,
            )


def test_validate_archive_members_rejects_special_file() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        special = zipfile.ZipInfo("workflow/custom_nodes/device")
        special.create_system = 3
        special.external_attr = (stat.S_IFCHR | 0o600) << 16
        archive.writestr(special, b"")

    with zipfile.ZipFile(io.BytesIO(payload.getvalue())) as archive:
        with pytest.raises(ArchiveValidationError, match="special file"):
            validate_archive_members(
                archive,
                required_files=REQUIRED_FILES,
                max_files=20,
                max_total_uncompressed_bytes=1024,
            )
