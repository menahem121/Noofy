from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.core.config import settings
from app.engine.factory import create_default_engine_service
from app.engine.service import EngineService
from app.gallery import GalleryCaptureService, GalleryStore
from app.model_inventory import ModelDownloadJobService, ModelInventoryService, ModelOwnershipStore, ModelTagStore
from app.runtime.comfyui_sidecar_service import ComfyUISidecarService
from app.settings.api_keys import ApiKeyMetadataStore, ApiKeySettingsService, create_credential_store
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
    gallery_store: GalleryStore
    api_key_service: ApiKeySettingsService
    model_folder_service: ModelFolderSettingsService
    model_tag_store: ModelTagStore
    model_ownership_store: ModelOwnershipStore
    model_inventory_service: ModelInventoryService
    model_download_service: ModelDownloadJobService


def create_default_api_services() -> ApiServices:
    return create_api_services(engine_service=create_default_engine_service())


def create_api_services(
    *,
    engine_service: EngineService,
    comfyui_sidecar_service: ComfyUISidecarService | None = None,
    user_state_service: UserStateService | None = None,
    asset_service: DashboardAssetService | None = None,
    gallery_store: GalleryStore | None = None,
    api_key_service: ApiKeySettingsService | None = None,
    model_folder_service: ModelFolderSettingsService | None = None,
    model_tag_store: ModelTagStore | None = None,
    model_ownership_store: ModelOwnershipStore | None = None,
    model_inventory_service: ModelInventoryService | None = None,
    model_download_service: ModelDownloadJobService | None = None,
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

    gallery = gallery_store or GalleryStore(settings.paths.gallery_outputs_dir)
    if getattr(engine_service, "gallery_capture_service", None) is None:
        engine_service.gallery_capture_service = GalleryCaptureService(gallery)

    tags = model_tag_store or ModelTagStore(settings.paths.settings_dir / "model-tags.json")
    ownership = model_ownership_store or ModelOwnershipStore(settings.paths.settings_dir / "model-ownership.json")
    if getattr(engine_service, "model_ownership_store", None) is None:
        setattr(engine_service, "model_ownership_store", ownership)
    folders = model_folder_service or ModelFolderSettingsService(
        store=ModelFolderSettingsStore(settings.paths.settings_dir / "model-folders.json"),
        default_noofy_models_dir=default_noofy_models_dir(settings.paths.data_dir),
        log_store=getattr(engine_service, "log_store", None),
        on_change=_apply_model_folder_change,
    )
    inventory = model_inventory_service or ModelInventoryService(
        engine_service=engine_service,
        model_folder_service=folders,
        tag_store=tags,
        ownership_store=ownership,
        log_store=getattr(engine_service, "log_store", None),
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
        gallery_store=gallery,
        api_key_service=api_key_service
        or ApiKeySettingsService(
            metadata_store=ApiKeyMetadataStore(settings.paths.settings_dir / "api-keys.json"),
            credential_store=create_credential_store(
                data_dir=settings.paths.data_dir,
                settings_dir=settings.paths.settings_dir,
            ),
            log_store=getattr(engine_service, "log_store", None),
        ),
        model_folder_service=folders,
        model_tag_store=tags,
        model_ownership_store=ownership,
        model_inventory_service=inventory,
        model_download_service=model_download_service
        or ModelDownloadJobService(
            engine_service=engine_service,
            model_folder_service=folders,
            ownership_store=ownership,
            log_store=getattr(engine_service, "log_store", None),
        ),
    )


ApiServicesFactory = Callable[[], ApiServices]
