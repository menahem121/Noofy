"""Resolve the `uv` executable from the Noofy-controlled backend venv.

Noofy installs `uv` as a backend dependency (pyproject.toml: uv>=0.5).
Runtime/bootstrap code must never rely on the user's shell PATH for `uv`.
This module provides the single authoritative resolver.
"""

import os
import sys
from pathlib import Path


def resolve_noofy_uv_executable() -> str:
    """Return an absolute path to the `uv` binary inside the running venv.

    Resolution order:
    1. The sibling of ``sys.executable`` inside the active venv's bin/Scripts
       directory (the normal case when the backend runs from backend/.venv).
    2. Raise ``FileNotFoundError`` with a clear diagnostic if nothing is found.

    This never falls back to ``shutil.which("uv")`` so global PATH state
    cannot silently satisfy the requirement.
    """
    candidate = _venv_uv_path(Path(sys.executable))
    if candidate.is_file():
        return str(candidate)

    raise FileNotFoundError(
        f"uv executable not found in the Noofy backend venv at {candidate}.\n"
        "Run 'make install' to reinstall backend dependencies, which includes uv."
    )


def _venv_uv_path(python_executable: Path) -> Path:
    """Return the expected uv path next to a given Python executable."""
    bin_dir = python_executable.parent
    if os.name == "nt":
        return bin_dir / "uv.exe"
    return bin_dir / "uv"
