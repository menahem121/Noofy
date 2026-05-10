from __future__ import annotations

import io
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
