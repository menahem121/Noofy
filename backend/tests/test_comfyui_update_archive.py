from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.runtime.comfyui.comfyui_update_archive import extract_github_zip, safe_tag


def test_safe_tag_normalizes_path_unsafe_release_names() -> None:
    assert safe_tag("refs/tags/v1.0 rc") == "refs-tags-v1.0-rc"
    assert safe_tag("***") == "unknown"


def test_extract_github_zip_strips_single_source_root(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("ComfyUI-main/main.py", "print('ok')\n")
        archive.writestr("ComfyUI-main/requirements.txt", "aiohttp\n")

    dest = tmp_path / "source"
    extract_github_zip(archive_path, dest)

    assert (dest / "main.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert (dest / "requirements.txt").exists()


def test_extract_github_zip_rejects_unsafe_paths(tmp_path: Path) -> None:
    archive_path = tmp_path / "source.zip"
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../main.py", "bad")
    archive_path.write_bytes(payload.getvalue())

    with pytest.raises(RuntimeError, match="Unsafe ComfyUI archive path"):
        extract_github_zip(archive_path, tmp_path / "source")
