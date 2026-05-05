from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ComfyUIVramMode = Literal["normal", "gpu_only", "highvram", "lowvram", "novram", "cpu"]

COMFYUI_VRAM_FLAG_BY_MODE: dict[str, list[str]] = {
    "normal": [],
    "gpu_only": ["--gpu-only"],
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
        value="normal",
        label="Normal VRAM",
        description="Use ComfyUI's default memory behavior.",
    ),
    ComfyUILaunchOption(
        value="gpu_only",
        label="GPU only",
        description="Pass --gpu-only and keep all supported work on the GPU.",
    ),
    ComfyUILaunchOption(
        value="highvram",
        label="High VRAM",
        description="Pass --highvram and keep models in GPU memory after use.",
    ),
    ComfyUILaunchOption(
        value="lowvram",
        label="Low VRAM",
        description="Pass --lowvram and split model work to reduce VRAM use.",
    ),
    ComfyUILaunchOption(
        value="novram",
        label="No VRAM",
        description="Pass --novram when low VRAM mode is not enough.",
    ),
    ComfyUILaunchOption(
        value="cpu",
        label="CPU only",
        description="Pass --cpu and run ComfyUI without GPU acceleration.",
    ),
]


class ComfyUILaunchSettings(BaseModel):
    vram_mode: ComfyUIVramMode = "normal"


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
