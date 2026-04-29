"""Canonical app-owned directory resolver for Noofy.

Platform defaults:
  macOS:   ~/Library/Application Support/Noofy
  Windows: %APPDATA%\\Noofy
  Linux:   ~/.local/share/noofy

Override everything:  NOOFY_DATA_DIR
Targeted overrides:   NOOFY_RUNTIME_DIR, NOOFY_MODELS_DIR, NOOFY_WORKFLOWS_DIR,
                      NOOFY_OUTPUTS_DIR, NOOFY_LOGS_DIR, NOOFY_CACHE_DIR,
                      NOOFY_TEMP_DIR, COMFYUI_REPO_DIR
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# Bundled starter workflows ship inside the repo / packaged app.
_BACKEND_APP_DIR = Path(__file__).resolve().parents[1]
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _platform_data_dir(env: dict[str, str] | None = None) -> Path:
    """Return the per-user app-data base directory for the current OS."""
    _env = env if env is not None else dict(os.environ)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Noofy"
    if sys.platform == "win32":
        appdata = _env.get("APPDATA")
        if appdata:
            return Path(appdata) / "Noofy"
        return Path.home() / "AppData" / "Roaming" / "Noofy"
    # Linux / other
    xdg = _env.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / "noofy"
    return Path.home() / ".local" / "share" / "noofy"


@dataclass(frozen=True)
class NoofyPaths:
    """Resolved canonical directory layout for Noofy."""

    data_dir: Path
    runtime_dir: Path
    models_dir: Path
    user_workflows_dir: Path
    outputs_dir: Path
    logs_dir: Path
    cache_dir: Path
    temp_dir: Path
    bundled_workflows_dir: Path
    comfyui_repo_dir: Path

    def ensure_directories(self) -> None:
        """Lazily create all app-owned writable directories.

        ``bundled_workflows_dir`` and ``comfyui_repo_dir`` are intentionally
        excluded – they are read-only / managed elsewhere.
        """
        for directory in (
            self.data_dir,
            self.runtime_dir,
            self.models_dir,
            self.user_workflows_dir,
            self.outputs_dir,
            self.logs_dir,
            self.cache_dir,
            self.temp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def writable_status(self) -> dict[str, dict[str, object]]:
        """Return a JSON-friendly dict of each directory and its writability."""
        entries: dict[str, dict[str, object]] = {}
        for label, directory in self._all_named():
            entries[label] = {
                "path": str(directory),
                "exists": directory.exists(),
                "writable": os.access(directory, os.W_OK) if directory.exists() else False,
            }
        return entries

    def _all_named(self) -> list[tuple[str, Path]]:
        return [
            ("data_dir", self.data_dir),
            ("runtime_dir", self.runtime_dir),
            ("models_dir", self.models_dir),
            ("user_workflows_dir", self.user_workflows_dir),
            ("outputs_dir", self.outputs_dir),
            ("logs_dir", self.logs_dir),
            ("cache_dir", self.cache_dir),
            ("temp_dir", self.temp_dir),
            ("bundled_workflows_dir", self.bundled_workflows_dir),
            ("comfyui_repo_dir", self.comfyui_repo_dir),
        ]


def resolve_paths(
    *,
    env: dict[str, str] | None = None,
) -> NoofyPaths:
    """Build ``NoofyPaths`` from environment variables with platform defaults.

    Parameters
    ----------
    env:
        Optional environment dict (defaults to ``os.environ``).
        Passing an explicit dict simplifies testing.
    """
    if env is None:
        env = dict(os.environ)

    base = Path(env["NOOFY_DATA_DIR"]) if env.get("NOOFY_DATA_DIR") else _platform_data_dir(env)

    # NOOFY_RUNTIME_DIR is backward-compatible – it only overrides runtime_dir.
    runtime_dir = Path(env["NOOFY_RUNTIME_DIR"]) if env.get("NOOFY_RUNTIME_DIR") else base / "runtime"

    models_dir = Path(env["NOOFY_MODELS_DIR"]) if env.get("NOOFY_MODELS_DIR") else base / "models"
    user_workflows_dir = Path(env["NOOFY_WORKFLOWS_DIR"]) if env.get("NOOFY_WORKFLOWS_DIR") else base / "workflows"
    outputs_dir = Path(env["NOOFY_OUTPUTS_DIR"]) if env.get("NOOFY_OUTPUTS_DIR") else base / "outputs"
    logs_dir = Path(env["NOOFY_LOGS_DIR"]) if env.get("NOOFY_LOGS_DIR") else base / "logs"
    cache_dir = Path(env["NOOFY_CACHE_DIR"]) if env.get("NOOFY_CACHE_DIR") else base / "cache"
    temp_dir = Path(env["NOOFY_TEMP_DIR"]) if env.get("NOOFY_TEMP_DIR") else base / "temp"

    bundled_workflows_dir = _BACKEND_APP_DIR / "workflows" / "packages"

    comfyui_repo_dir = (
        Path(env["COMFYUI_REPO_DIR"]) if env.get("COMFYUI_REPO_DIR") else _PROJECT_ROOT / "ComfyUI-official-repo"
    )

    return NoofyPaths(
        data_dir=base,
        runtime_dir=runtime_dir,
        models_dir=models_dir,
        user_workflows_dir=user_workflows_dir,
        outputs_dir=outputs_dir,
        logs_dir=logs_dir,
        cache_dir=cache_dir,
        temp_dir=temp_dir,
        bundled_workflows_dir=bundled_workflows_dir,
        comfyui_repo_dir=comfyui_repo_dir,
    )
