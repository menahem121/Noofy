from __future__ import annotations

from pathlib import Path


def model_key(folder: str, filename: str) -> str:
    normalized_filename = filename.replace("\\", "/")
    return f"{folder}/{normalized_filename}"


def parse_model_key(key: str) -> tuple[str, str]:
    folder, separator, filename = key.partition("/")
    if not separator or not folder or not filename:
        raise ValueError("Model key must include a folder and filename.")
    if Path(filename).is_absolute() or "\\" in filename:
        raise ValueError("Model filename must be relative to its model folder.")
    parts = Path(filename).parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("Model filename must stay inside its model folder.")
    return folder, filename


def ensure_inside(path: Path, root: Path) -> None:
    resolved = path.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("Model files must stay inside the configured Noofy Models folder.")
