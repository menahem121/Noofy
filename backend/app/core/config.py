import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Settings:
    comfyui_base_url: str = os.environ.get("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    comfyui_ws_url: str = os.environ.get("COMFYUI_WS_URL", "ws://127.0.0.1:8188/ws")
    comfyui_python_executable: str = os.environ.get("COMFYUI_PYTHON_EXECUTABLE", "python3")
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
