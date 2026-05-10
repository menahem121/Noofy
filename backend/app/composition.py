from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.config import settings
from app.engine.factory import create_default_engine_service
from app.engine.service import EngineService
from app.runtime.comfyui_sidecar_service import ComfyUISidecarService
from app.settings.api_keys import ApiKeyMetadataStore, ApiKeySettingsService
from app.settings.model_folders import (
    ModelFolderSettingsService,
    ModelFolderSettingsStore,
    default_noofy_models_dir,
    write_extra_model_paths_config,
)
from app.workflows.assets import DashboardAssetService
from app.workflows.user_state import UserStateService


@dataclass(frozen=True)
class ApiServices:
    engine_service: EngineService
    comfyui_sidecar_service: object
    user_state_service: UserStateService
    asset_service: DashboardAssetService
    api_key_service: ApiKeySettingsService
    model_folder_service: ModelFolderSettingsService


def create_default_api_services() -> ApiServices:
    return create_api_services(engine_service=create_default_engine_service())


def create_api_services(
    *,
    engine_service: EngineService,
    comfyui_sidecar_service: ComfyUISidecarService | None = None,
    user_state_service: UserStateService | None = None,
    asset_service: DashboardAssetService | None = None,
    api_key_service: ApiKeySettingsService | None = None,
    model_folder_service: ModelFolderSettingsService | None = None,
) -> ApiServices:
    extra_model_paths_config = settings.paths.runtime_store_dir / "settings" / "extra-model-paths.yaml"

    def _apply_model_folder_change(noofy_models_dir, external_comfyui_models_dir) -> None:
        write_extra_model_paths_config(
            extra_model_paths_config,
            noofy_models_dir=noofy_models_dir,
            external_comfyui_models_dir=external_comfyui_models_dir,
        )
        apply_settings = getattr(engine_service, "apply_model_folder_settings", None)
        if callable(apply_settings):
            apply_settings(
                noofy_models_dir,
                external_comfyui_models_dir,
                extra_model_paths_config=extra_model_paths_config,
            )

    return ApiServices(
        engine_service=engine_service,
        comfyui_sidecar_service=(
            comfyui_sidecar_service
            if comfyui_sidecar_service is not None
            else getattr(engine_service, "comfyui_sidecar_service", engine_service)
        ),
        user_state_service=user_state_service or UserStateService(settings.paths.user_state_dir),
        asset_service=asset_service or DashboardAssetService(settings.paths.dashboard_assets_dir),
        api_key_service=api_key_service
        or ApiKeySettingsService(
            metadata_store=ApiKeyMetadataStore(settings.paths.settings_dir / "api-keys.json"),
            log_store=getattr(engine_service, "log_store", None),
        ),
        model_folder_service=model_folder_service
        or ModelFolderSettingsService(
            store=ModelFolderSettingsStore(settings.paths.settings_dir / "model-folders.json"),
            default_noofy_models_dir=default_noofy_models_dir(settings.paths.data_dir),
            log_store=getattr(engine_service, "log_store", None),
            on_change=_apply_model_folder_change,
        ),
    )


ApiServicesFactory = Callable[[], ApiServices]
