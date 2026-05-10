from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath


def safe_tag(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", tag).strip("-") or "unknown"


def extract_github_zip(archive_path: Path, dest: Path) -> None:
    raw_dest = dest.parent / "_raw-source"
    shutil.rmtree(raw_dest, ignore_errors=True)
    raw_dest.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            validate_zip_member(member)
            archive.extract(member, raw_dest)
    roots = [path for path in raw_dest.iterdir() if path.is_dir()]
    source_root = (
        roots[0] if len(roots) == 1 and (roots[0] / "main.py").exists() else raw_dest
    )
    if not (source_root / "main.py").exists():
        raise RuntimeError("Downloaded ComfyUI archive did not contain main.py.")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(source_root, dest)
    shutil.rmtree(raw_dest, ignore_errors=True)


def validate_zip_member(member: zipfile.ZipInfo) -> None:
    path = PurePosixPath(member.filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"Unsafe ComfyUI archive path: {member.filename}")
    mode = member.external_attr >> 16
    if mode & 0o170000 == 0o120000:
        raise RuntimeError(f"ComfyUI archive contains a symlink: {member.filename}")
