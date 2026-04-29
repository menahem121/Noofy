import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
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
    runtime_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("NOOFY_RUNTIME_DIR", Path(__file__).resolve().parents[3] / ".noofy-runtime"))
    )
    workflows_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[1] / "workflows" / "packages"
    )
    comfyui_repo_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "ComfyUI-official-repo"
    )
    comfyui_models_dir: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[3] / "ComfyUI-official-repo" / "models"
    )

    @property
    def comfyui_host(self) -> str:
        parsed = urlparse(self.comfyui_base_url)
        return parsed.hostname or "127.0.0.1"

    @property
    def comfyui_port(self) -> int:
        parsed = urlparse(self.comfyui_base_url)
        return parsed.port or 8188


settings = Settings()
