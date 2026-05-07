"""Canonical app-owned directory resolver for Noofy.

Platform defaults:
  macOS:   ~/Library/Application Support/Noofy
  Windows: %APPDATA%\\Noofy
  Linux:   ~/.local/share/noofy

Override everything:  NOOFY_DATA_DIR
Targeted overrides:   NOOFY_RUNTIME_DIR, NOOFY_MODELS_DIR, NOOFY_WORKFLOWS_DIR,
                      NOOFY_INPUT_DIR, NOOFY_OUTPUTS_DIR, NOOFY_LOGS_DIR, NOOFY_CACHE_DIR,
                      NOOFY_TEMP_DIR, COMFYUI_REPO_DIR
Packaged resources:   NOOFY_BUNDLED_RESOURCE_DIR, NOOFY_BUNDLED_COMFYUI_DIR,
                      NOOFY_BUNDLED_WORKFLOWS_DIR
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


def _bundled_resource_dir(env: dict[str, str]) -> Path | None:
    value = env.get("NOOFY_BUNDLED_RESOURCE_DIR")
    return Path(value) if value else None


@dataclass(frozen=True)
class NoofyPaths:
    """Resolved canonical directory layout for Noofy."""

    data_dir: Path
    runtime_dir: Path
    models_dir: Path
    user_workflows_dir: Path
    input_dir: Path
    outputs_dir: Path
    logs_dir: Path
    cache_dir: Path
    temp_dir: Path
    bundled_workflows_dir: Path
    comfyui_repo_dir: Path

    @property
    def runtime_store_dir(self) -> Path:
        return self.data_dir / "runtime-store"

    @property
    def dependency_envs_dir(self) -> Path:
        return self.runtime_store_dir / "envs"

    @property
    def dependency_locks_dir(self) -> Path:
        return self.runtime_store_dir / "dependency-locks"

    @property
    def runner_workspaces_dir(self) -> Path:
        return self.runtime_store_dir / "runner-workspaces"

    @property
    def core_engines_dir(self) -> Path:
        return self.runtime_store_dir / "core-engines"

    @property
    def core_envs_dir(self) -> Path:
        return self.runtime_store_dir / "core-envs"

    @property
    def install_transactions_dir(self) -> Path:
        return self.runtime_store_dir / "transactions"

    @property
    def user_state_dir(self) -> Path:
        return self.data_dir / "user-state"

    @property
    def comfyui_custom_nodes_dir(self) -> Path:
        return self.data_dir / "custom_nodes"

    @property
    def comfyui_user_dir(self) -> Path:
        return self.user_state_dir / "comfyui"

    @property
    def comfyui_database_file(self) -> Path:
        return self.comfyui_user_dir / "comfyui.db"

    @property
    def python_cache_dir(self) -> Path:
        return self.cache_dir / "python"

    @property
    def dashboard_assets_dir(self) -> Path:
        return self.data_dir / "dashboard-assets"

    @property
    def workflow_store_dir(self) -> Path:
        return self.data_dir / "workflow-store"

    @property
    def workflow_packages_store_dir(self) -> Path:
        return self.workflow_store_dir / "packages"

    @property
    def custom_node_cache_dir(self) -> Path:
        return self.data_dir / "custom-node-cache"

    @property
    def wheel_cache_dir(self) -> Path:
        return self.data_dir / "wheel-cache"

    @property
    def model_store_dir(self) -> Path:
        return self.data_dir / "model-store"

    @property
    def model_blobs_dir(self) -> Path:
        return self.model_store_dir / "blobs" / "sha256"

    @property
    def model_refs_dir(self) -> Path:
        return self.model_store_dir / "refs"

    @property
    def model_materialized_dir(self) -> Path:
        return self.model_store_dir / "materialized"

    @property
    def trust_dir(self) -> Path:
        return self.data_dir / "trust"

    @property
    def trust_keys_file(self) -> Path:
        return self.trust_dir / "trusted-keys.json"

    def ensure_directories(self) -> None:
        """Lazily create all app-owned writable directories.

        ``bundled_workflows_dir`` and ``comfyui_repo_dir`` are intentionally
        excluded – they are read-only / managed elsewhere.
        """
        for directory in (
            self.data_dir,
            self.runtime_dir,
            self.runtime_store_dir,
            self.dependency_envs_dir,
            self.dependency_locks_dir,
            self.runner_workspaces_dir,
            self.core_engines_dir,
            self.core_envs_dir,
            self.install_transactions_dir,
            self.workflow_store_dir,
            self.workflow_packages_store_dir,
            self.custom_node_cache_dir,
            self.wheel_cache_dir,
            self.model_store_dir,
            self.model_blobs_dir,
            self.model_refs_dir,
            self.model_materialized_dir,
            self.trust_dir,
            self.models_dir,
            self.comfyui_custom_nodes_dir,
            self.user_workflows_dir,
            self.input_dir,
            self.outputs_dir,
            self.logs_dir,
            self.cache_dir,
            self.python_cache_dir,
            self.temp_dir,
            self.user_state_dir,
            self.comfyui_user_dir,
            self.dashboard_assets_dir,
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
            ("runtime_store_dir", self.runtime_store_dir),
            ("dependency_envs_dir", self.dependency_envs_dir),
            ("dependency_locks_dir", self.dependency_locks_dir),
            ("runner_workspaces_dir", self.runner_workspaces_dir),
            ("core_engines_dir", self.core_engines_dir),
            ("core_envs_dir", self.core_envs_dir),
            ("install_transactions_dir", self.install_transactions_dir),
            ("workflow_store_dir", self.workflow_store_dir),
            ("workflow_packages_store_dir", self.workflow_packages_store_dir),
            ("custom_node_cache_dir", self.custom_node_cache_dir),
            ("wheel_cache_dir", self.wheel_cache_dir),
            ("model_store_dir", self.model_store_dir),
            ("model_blobs_dir", self.model_blobs_dir),
            ("model_refs_dir", self.model_refs_dir),
            ("model_materialized_dir", self.model_materialized_dir),
            ("trust_dir", self.trust_dir),
            ("trust_keys_file", self.trust_keys_file),
            ("models_dir", self.models_dir),
            ("comfyui_custom_nodes_dir", self.comfyui_custom_nodes_dir),
            ("user_workflows_dir", self.user_workflows_dir),
            ("input_dir", self.input_dir),
            ("outputs_dir", self.outputs_dir),
            ("logs_dir", self.logs_dir),
            ("cache_dir", self.cache_dir),
            ("python_cache_dir", self.python_cache_dir),
            ("temp_dir", self.temp_dir),
            ("bundled_workflows_dir", self.bundled_workflows_dir),
            ("comfyui_repo_dir", self.comfyui_repo_dir),
            ("user_state_dir", self.user_state_dir),
            ("comfyui_user_dir", self.comfyui_user_dir),
            ("comfyui_database_file", self.comfyui_database_file),
            ("dashboard_assets_dir", self.dashboard_assets_dir),
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
    input_dir = Path(env["NOOFY_INPUT_DIR"]) if env.get("NOOFY_INPUT_DIR") else base / "input"
    outputs_dir = Path(env["NOOFY_OUTPUTS_DIR"]) if env.get("NOOFY_OUTPUTS_DIR") else base / "outputs"
    logs_dir = Path(env["NOOFY_LOGS_DIR"]) if env.get("NOOFY_LOGS_DIR") else base / "logs"
    cache_dir = Path(env["NOOFY_CACHE_DIR"]) if env.get("NOOFY_CACHE_DIR") else base / "cache"
    temp_dir = Path(env["NOOFY_TEMP_DIR"]) if env.get("NOOFY_TEMP_DIR") else base / "temp"

    bundled_resource_dir = _bundled_resource_dir(env)
    bundled_workflows_dir = (
        Path(env["NOOFY_BUNDLED_WORKFLOWS_DIR"])
        if env.get("NOOFY_BUNDLED_WORKFLOWS_DIR")
        else (
            bundled_resource_dir / "noofy-runtime" / "backend" / "app" / "workflows" / "packages"
            if bundled_resource_dir is not None
            else _BACKEND_APP_DIR / "workflows" / "packages"
        )
    )

    if env.get("COMFYUI_REPO_DIR"):
        comfyui_repo_dir = Path(env["COMFYUI_REPO_DIR"])
    elif env.get("NOOFY_BUNDLED_COMFYUI_DIR"):
        comfyui_repo_dir = Path(env["NOOFY_BUNDLED_COMFYUI_DIR"])
    elif bundled_resource_dir is not None:
        comfyui_repo_dir = bundled_resource_dir / "noofy-runtime" / "comfyui"
    else:
        comfyui_repo_dir = _PROJECT_ROOT / "third_party" / "comfyui"

    return NoofyPaths(
        data_dir=base,
        runtime_dir=runtime_dir,
        models_dir=models_dir,
        user_workflows_dir=user_workflows_dir,
        input_dir=input_dir,
        outputs_dir=outputs_dir,
        logs_dir=logs_dir,
        cache_dir=cache_dir,
        temp_dir=temp_dir,
        bundled_workflows_dir=bundled_workflows_dir,
        comfyui_repo_dir=comfyui_repo_dir,
    )
