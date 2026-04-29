import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.core.paths import NoofyPaths, resolve_paths


@dataclass(frozen=True)
class Settings:
    noofy_api_token: str | None = os.environ.get("NOOFY_API_TOKEN")
    comfyui_runtime_mode: str = os.environ.get("COMFYUI_RUNTIME_MODE", "external")
    comfyui_base_url: str = os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    comfyui_ws_url: str | None = os.environ.get("COMFYUI_WS_URL")
    comfyui_python_executable: str | None = os.environ.get("COMFYUI_PYTHON_EXECUTABLE")
    comfyui_bootstrap_python_executable: str = os.environ.get("COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE", "python3")
    comfyui_managed_host: str = os.environ.get("COMFYUI_MANAGED_HOST", "127.0.0.1")
    comfyui_managed_port: int | None = (
        int(os.environ["COMFYUI_MANAGED_PORT"]) if os.environ.get("COMFYUI_MANAGED_PORT") else None
    )
    comfyui_startup_timeout_seconds: float = float(os.environ.get("COMFYUI_STARTUP_TIMEOUT_SECONDS", "60"))
    comfyui_health_poll_interval_seconds: float = float(os.environ.get("COMFYUI_HEALTH_POLL_INTERVAL_SECONDS", "0.5"))
    comfyui_torch_cuda_index_url: str | None = os.environ.get("COMFYUI_TORCH_CUDA_INDEX_URL")
    comfyui_torch_cpu_index_url: str = os.environ.get(
        "COMFYUI_TORCH_CPU_INDEX_URL",
        "https://download.pytorch.org/whl/cpu",
    )
    comfyui_max_restart_attempts: int = int(os.environ.get("COMFYUI_MAX_RESTART_ATTEMPTS", "3"))
    comfyui_restart_backoff_base: float = float(os.environ.get("COMFYUI_RESTART_BACKOFF_BASE", "2.0"))

    # Resolved app-owned directory contract.
    paths: NoofyPaths = field(default_factory=resolve_paths)

    # --- Convenience accessors (backward-compatible) ---

    @property
    def runtime_dir(self) -> Path:
        return self.paths.runtime_dir

    @property
    def workflows_dir(self) -> Path:
        """Bundled (read-only) starter workflows."""
        return self.paths.bundled_workflows_dir

    @property
    def comfyui_repo_dir(self) -> Path:
        return self.paths.comfyui_repo_dir

    @property
    def comfyui_models_dir(self) -> Path:
        """App-owned models directory (not ComfyUI-official-repo/models)."""
        return self.paths.models_dir

    @property
    def comfyui_host(self) -> str:
        parsed = urlparse(self.comfyui_base_url)
        return parsed.hostname or "127.0.0.1"

    @property
    def comfyui_port(self) -> int:
        parsed = urlparse(self.comfyui_base_url)
        return parsed.port or 8188


settings = Settings()
