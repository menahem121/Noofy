"""Helpers for matching isolated dependency environments to runner Python."""

from __future__ import annotations

import subprocess


def detect_python_major_minor(python_executable: str, *, timeout: float = 5) -> str | None:
    """Return the interpreter ABI version as ``major.minor`` without importing app code."""
    try:
        result = subprocess.run(
            [
                python_executable,
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    version = result.stdout.strip()
    if not version:
        return None
    parts = version.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        return None
    return version
