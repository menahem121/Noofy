from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ComfyUIVramMode = Literal["normal", "highvram", "lowvram", "novram", "cpu"]
DEFAULT_COMFYUI_VRAM_MODE: ComfyUIVramMode = "normal"
DEFAULT_COMFYUI_PREVIEW_METHOD = "auto"
DEFAULT_COMFYUI_PREVIEW_SIZE = 512
DEFAULT_COMFYUI_ATTENTION_BACKEND = "auto"
DEFAULT_COMFYUI_PRECISION_POLICY = "auto"
COMFYUI_PREVIEW_METHODS = frozenset({"auto", "latent2rgb", "taesd", "none"})

COMFYUI_VRAM_FLAG_BY_MODE: dict[str, list[str]] = {
    # "auto" is the runtime-profile default: defer to ComfyUI's own VRAM policy.
    "auto": [],
    "normal": [],
    "highvram": ["--highvram"],
    "lowvram": ["--lowvram"],
    "novram": ["--novram"],
    "cpu": ["--cpu"],
}

COMFYUI_ATTENTION_FLAGS_BY_BACKEND: dict[str, list[str]] = {
    # Defer to ComfyUI's attention auto-selection for the detected device.
    "auto": [],
    # Force PyTorch scaled-dot-product attention. ComfyUI does not auto-enable
    # it on every backend (notably MPS), and forcing it can change same-seed
    # outputs, so profiles opting in must ship it as a profile version change.
    "pytorch_sdpa": ["--use-pytorch-cross-attention"],
}

# Only quality-neutral precision behavior is allowed in the stable runtime;
# ComfyUI precision flags (--force-fp16, fp8 variants, ...) are quality-risk
# levers and are intentionally not mappable through launch defaults.
COMFYUI_PRECISION_POLICIES = frozenset({"auto"})

# How each RuntimeLaunchDefaults field reaches the runner. Every field must be
# registered: either emitted as launch args, emitted as process env, or
# explicitly fingerprint-only (applied during workspace preparation, not at
# process launch). A test asserts this registry stays exhaustive so fields can
# never silently become fingerprint-only by accident.
LAUNCH_DEFAULT_EMITTED_AS_ARGS = "args"
LAUNCH_DEFAULT_EMITTED_AS_ENV = "env"
LAUNCH_DEFAULT_FINGERPRINT_ONLY = "fingerprint_only"

RUNTIME_LAUNCH_DEFAULTS_FIELD_BEHAVIOR: dict[str, str] = {
    "preview_method": LAUNCH_DEFAULT_EMITTED_AS_ARGS,
    "preview_size": LAUNCH_DEFAULT_EMITTED_AS_ARGS,
    "vram_mode": LAUNCH_DEFAULT_EMITTED_AS_ARGS,
    "attention_backend": LAUNCH_DEFAULT_EMITTED_AS_ARGS,
    "precision_policy": LAUNCH_DEFAULT_EMITTED_AS_ARGS,
    "extra_model_paths_mode": LAUNCH_DEFAULT_FINGERPRINT_ONLY,
    "noofy_environment": LAUNCH_DEFAULT_EMITTED_AS_ENV,
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


def comfyui_attention_args(backend: str = DEFAULT_COMFYUI_ATTENTION_BACKEND) -> list[str]:
    if backend not in COMFYUI_ATTENTION_FLAGS_BY_BACKEND:
        raise ValueError(f"Unsupported ComfyUI attention backend: {backend}")
    return list(COMFYUI_ATTENTION_FLAGS_BY_BACKEND[backend])


def comfyui_precision_args(policy: str = DEFAULT_COMFYUI_PRECISION_POLICY) -> list[str]:
    if policy not in COMFYUI_PRECISION_POLICIES:
        raise ValueError(f"Unsupported ComfyUI precision policy: {policy}")
    return []


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
