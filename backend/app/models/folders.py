from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from app.diagnostics import DiagnosticsSink

MODEL_FOLDER_SETTINGS_SCHEMA_VERSION = "1"
NOOFY_MODELS_FOLDER_NAME = "Noofy Models"

COMFYUI_MODEL_CATEGORIES = [
    "configs",
    "audio_encoders",
    "background_removal",
    "checkpoints",
    "clip",
    "clip_vision",
    "controlnet",
    "diffusers",
    "diffusion_models",
    "embeddings",
    "frame_interpolation",
    "gligen",
    "hypernetworks",
    "latent_upscale_models",
    "loras",
    "model_patches",
    "optical_flow",
    "photomaker",
    "style_models",
    "text_encoders",
    "unet",
    "upscale_models",
    "vae",
    "vae_approx",
]

ModelFolderChangeCallback = Callable[[Path, Path | None], None]


class ModelFolderSettings(BaseModel):
    noofy_models_dir: str
    external_comfyui_models_dir: str | None = None


class ModelFolderSettingsResponse(ModelFolderSettings):
    categories: list[str]
    noofy_folder_exists: bool
    external_folder_exists: bool | None = None


class ModelFolderUpdateRequest(BaseModel):
    noofy_models_dir: str | None = None
    external_comfyui_models_dir: str | None = None


class ModelFolderUpdateResult(BaseModel):
    status: str
    settings: ModelFolderSettingsResponse
    restart_required: bool = False


class ModelFolderSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self, *, default_noofy_models_dir: Path) -> ModelFolderSettings:
        if not self.path.exists():
            return ModelFolderSettings(noofy_models_dir=str(default_noofy_models_dir))
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return ModelFolderSettings(noofy_models_dir=str(default_noofy_models_dir))
        if not isinstance(data, dict):
            return ModelFolderSettings(noofy_models_dir=str(default_noofy_models_dir))
        noofy_dir = _path_value(data.get("noofy_models_dir")) or default_noofy_models_dir
        if _looks_inside_repo_comfyui(noofy_dir.resolve(strict=False)):
            noofy_dir = default_noofy_models_dir
        external_dir = _path_value(data.get("external_comfyui_models_dir"))
        if external_dir and _looks_inside_repo_comfyui(external_dir.resolve(strict=False)):
            external_dir = None
        return ModelFolderSettings(
            noofy_models_dir=str(noofy_dir),
            external_comfyui_models_dir=str(external_dir) if external_dir else None,
        )

    def write(self, settings: ModelFolderSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": MODEL_FOLDER_SETTINGS_SCHEMA_VERSION,
            "noofy_models_dir": settings.noofy_models_dir,
            "external_comfyui_models_dir": settings.external_comfyui_models_dir,
        }
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class ModelFolderSettingsService:
    def __init__(
        self,
        *,
        store: ModelFolderSettingsStore,
        default_noofy_models_dir: Path,
        log_store: DiagnosticsSink | None = None,
        on_change: ModelFolderChangeCallback | None = None,
    ) -> None:
        self.store = store
        self.default_noofy_models_dir = default_noofy_models_dir
        self.log_store = log_store
        self.on_change = on_change

    def settings(self, *, ensure_folders: bool = True) -> ModelFolderSettingsResponse:
        settings = self.store.read(default_noofy_models_dir=self.default_noofy_models_dir)
        noofy_dir = Path(settings.noofy_models_dir).expanduser()
        if ensure_folders:
            ensure_model_subfolders(noofy_dir)
        return _response(settings)

    def update(self, request: ModelFolderUpdateRequest) -> ModelFolderUpdateResult:
        current = self.store.read(default_noofy_models_dir=self.default_noofy_models_dir)
        noofy_dir = (
            Path(request.noofy_models_dir).expanduser()
            if request.noofy_models_dir
            else Path(current.noofy_models_dir)
        )
        external_dir = (
            Path(request.external_comfyui_models_dir).expanduser()
            if request.external_comfyui_models_dir
            else (
                Path(current.external_comfyui_models_dir)
                if current.external_comfyui_models_dir
                else None
            )
        )

        if request.external_comfyui_models_dir == "":
            external_dir = None

        _validate_noofy_models_dir(noofy_dir)
        if external_dir is not None:
            _validate_external_comfyui_models_dir(external_dir)

        ensure_model_subfolders(noofy_dir)
        updated = ModelFolderSettings(
            noofy_models_dir=str(noofy_dir),
            external_comfyui_models_dir=str(external_dir) if external_dir else None,
        )
        self.store.write(updated)
        if self.on_change is not None:
            self.on_change(noofy_dir, external_dir)

        status = "unchanged" if updated == current else "updated"
        restart_required = updated != current
        self._record("info", "Model folder settings updated", updated)
        return ModelFolderUpdateResult(
            status=status,
            settings=_response(updated),
            restart_required=restart_required,
        )

    def _record(self, level: str, message: str, settings: ModelFolderSettings) -> None:
        if self.log_store is None:
            return
        self.log_store.add(
            level,  # type: ignore[arg-type]
            message,
            "models.folders",
            details={
                "noofy_models_dir": settings.noofy_models_dir,
                "external_comfyui_models_dir": settings.external_comfyui_models_dir,
            },
        )


def default_noofy_models_dir(data_dir: Path) -> Path:
    try:
        home = Path.home()
    except RuntimeError:
        return data_dir / NOOFY_MODELS_FOLDER_NAME
    if str(home) and str(home) != ".":
        return home / "Documents" / NOOFY_MODELS_FOLDER_NAME
    return data_dir / NOOFY_MODELS_FOLDER_NAME


def ensure_model_subfolders(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for category in COMFYUI_MODEL_CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)


def write_extra_model_paths_config(
    path: Path,
    *,
    noofy_models_dir: Path,
    external_comfyui_models_dir: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    roots = [("noofy_models", noofy_models_dir, True)]
    if external_comfyui_models_dir is not None:
        roots.append(("external_comfyui_models", external_comfyui_models_dir, False))
    lines = [
        "# Generated by Noofy. Do not edit while Noofy is running.",
        "# This lets managed ComfyUI see Noofy's model folders.",
        "",
    ]
    for name, root, is_default in roots:
        lines.append(f"{name}:")
        lines.append(f"  base_path: {json.dumps(str(root))}")
        if is_default:
            lines.append("  is_default: true")
        for category in COMFYUI_MODEL_CATEGORIES:
            lines.append(f"  {category}: {json.dumps(category)}")
        lines.append("")
    tmp = path.with_suffix(f"{path.suffix}.tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(path)


def _response(settings: ModelFolderSettings) -> ModelFolderSettingsResponse:
    noofy_dir = Path(settings.noofy_models_dir)
    external_dir = (
        Path(settings.external_comfyui_models_dir)
        if settings.external_comfyui_models_dir
        else None
    )
    return ModelFolderSettingsResponse(
        noofy_models_dir=str(noofy_dir),
        external_comfyui_models_dir=str(external_dir) if external_dir else None,
        categories=list(COMFYUI_MODEL_CATEGORIES),
        noofy_folder_exists=noofy_dir.exists(),
        external_folder_exists=external_dir.exists() if external_dir else None,
    )


def _path_value(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def _validate_noofy_models_dir(path: Path) -> None:
    resolved = path.expanduser().resolve(strict=False)
    if _looks_inside_repo_comfyui(resolved):
        raise ValueError("Noofy Models cannot be stored inside the bundled ComfyUI source folder.")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("Noofy Models location must be a folder.")
    existing_parent = _nearest_existing_parent(resolved)
    if existing_parent is None or not os.access(existing_parent, os.W_OK):
        raise ValueError("Noofy cannot write to that folder location.")


def _validate_external_comfyui_models_dir(path: Path) -> None:
    resolved = path.expanduser().resolve(strict=False)
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("Existing ComfyUI models folder must be an existing folder.")
    if _looks_inside_repo_comfyui(resolved):
        raise ValueError("Do not connect the bundled ComfyUI repo models folder.")


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return current if current.is_dir() else current.parent


def _looks_inside_repo_comfyui(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts}
    return "third_party" in parts and "comfyui" in parts
