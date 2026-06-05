from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ComfyUIVramMode = Literal["normal", "highvram", "lowvram", "novram", "cpu"]
DEFAULT_COMFYUI_VRAM_MODE: ComfyUIVramMode = "normal"
DEFAULT_COMFYUI_PREVIEW_METHOD = "auto"
DEFAULT_COMFYUI_PREVIEW_SIZE = 512
COMFYUI_PREVIEW_METHODS = frozenset({"auto", "latent2rgb", "taesd", "none"})

COMFYUI_VRAM_FLAG_BY_MODE: dict[str, list[str]] = {
    "normal": [],
    "highvram": ["--highvram"],
    "lowvram": ["--lowvram"],
    "novram": ["--novram"],
    "cpu": ["--cpu"],
}


class ComfyUILaunchOption(BaseModel):
    value: ComfyUIVramMode
    label: str
    description: str


COMFYUI_VRAM_OPTIONS = [
    ComfyUILaunchOption(
        value="cpu",
        label="CPU only",
        description="Runs without GPU acceleration, usually the slowest",
    ),
    ComfyUILaunchOption(
        value="novram",
        label="No VRAM",
        description="Extreme memory-saving mode, very slow but may still use GPU",
    ),
    ComfyUILaunchOption(
        value="lowvram",
        label="Low VRAM",
        description="For smaller GPUs",
    ),
    ComfyUILaunchOption(
        value="normal",
        label="Normal VRAM",
        description="Recommended",
    ),
    ComfyUILaunchOption(
        value="highvram",
        label="High VRAM",
        description="Faster if you have lots of VRAM",
    ),
]


class ComfyUILaunchSettings(BaseModel):
    vram_mode: ComfyUIVramMode = DEFAULT_COMFYUI_VRAM_MODE


class ComfyUILaunchSettingsResponse(ComfyUILaunchSettings):
    options: list[ComfyUILaunchOption] = Field(default_factory=lambda: list(COMFYUI_VRAM_OPTIONS))
    applies_to_managed_runtime: bool = True
    disabled_reason: str | None = None


class ComfyUILaunchSettingsUpdateResult(BaseModel):
    status: str
    settings: ComfyUILaunchSettingsResponse
    restart_status: str | None = None
    error: str | None = None


class ComfyUILaunchSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> ComfyUILaunchSettings:
        if not self.path.exists():
            return ComfyUILaunchSettings()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return ComfyUILaunchSettings.model_validate(data)
        except Exception:
            return ComfyUILaunchSettings()

    def write(self, settings: ComfyUILaunchSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(settings.model_dump(), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


def comfyui_vram_args(mode: str) -> list[str]:
    if mode not in COMFYUI_VRAM_FLAG_BY_MODE:
        raise ValueError(f"Unsupported ComfyUI VRAM mode: {mode}")
    return list(COMFYUI_VRAM_FLAG_BY_MODE[mode])


def comfyui_preview_args(
    method: str = DEFAULT_COMFYUI_PREVIEW_METHOD,
    size: int = DEFAULT_COMFYUI_PREVIEW_SIZE,
) -> list[str]:
    if method not in COMFYUI_PREVIEW_METHODS:
        raise ValueError(f"Unsupported ComfyUI preview method: {method}")
    if size <= 0:
        raise ValueError(f"Unsupported ComfyUI preview size: {size}")
    return ["--preview-method", method, "--preview-size", str(size)]


def comfyui_launch_response(
    settings: ComfyUILaunchSettings,
    *,
    mode: str,
) -> ComfyUILaunchSettingsResponse:
    applies = mode == "managed"
    return ComfyUILaunchSettingsResponse(
        vram_mode=settings.vram_mode,
        applies_to_managed_runtime=applies,
        disabled_reason=None if applies else "VRAM launch settings apply only to managed ComfyUI mode.",
    )
