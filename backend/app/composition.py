from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.core.config import settings
from app.diagnostics import LogStore
from app.engine.factory import create_default_engine_service
from app.engine.service import EngineService
from app.gallery import GalleryCaptureService, GalleryStore
from app.history import ActivityLogStore, HistoryService
from app.models.downloads import ModelDownloadJobService
from app.models.civitai_loras import CivitaiLoraBrowserService
from app.models.inventory import ModelInventoryService
from app.models.ownership import ModelOwnershipStore
from app.models.source_auth import provider_auth_headers_for_url
from app.models.tags import ModelTagStore
from app.runtime.comfyui.comfyui_sidecar_service import ComfyUISidecarService
from app.runtime.noofy_runtime import NoofyRuntimeUpdateService
from app.settings.api_keys import ApiKeyMetadataStore, ApiKeySettingsService, create_credential_store
from app.settings.onboarding import OnboardingSettingsService, OnboardingSettingsStore
from app.models.folders import (
    ModelFolderSettingsService,
    ModelFolderSettingsStore,
    default_noofy_models_dir,
    write_extra_model_paths_config,
)
from app.runs.job_service import RunJobService
from app.runs.orchestrator import RunOrchestrator
from app.runs.result_service import RunResultService
from app.runtime.runners.lifecycle_service import WorkflowRunnerLifecycleService
from app.workflows.assets import DashboardAssetService
from app.workflows.authoring import DashboardAuthoringService
from app.workflows.exporter import WorkflowExporter
from app.workflows.import_orchestrator import WorkflowImportOrchestrator
from app.workflows.library_service import WorkflowLibraryService
from app.runs.media_staging import MediaInputStagingResolver
from app.workflows.user_state import UserStateService


@dataclass(frozen=True)
class ApiServices:
    engine_service: EngineService
    comfyui_sidecar_service: object
    user_state_service: UserStateService
    asset_service: DashboardAssetService
    gallery_store: GalleryStore
    api_key_service: ApiKeySettingsService
    onboarding_service: OnboardingSettingsService
    model_folder_service: ModelFolderSettingsService
    model_tag_store: ModelTagStore
    model_ownership_store: ModelOwnershipStore
    model_inventory_service: ModelInventoryService
    model_download_service: ModelDownloadJobService
    noofy_runtime_update_service: NoofyRuntimeUpdateService
    workflow_library_service: WorkflowLibraryService | None
    dashboard_authoring_service: DashboardAuthoringService | None
    workflow_exporter: WorkflowExporter | None
    workflow_import_orchestrator: WorkflowImportOrchestrator | None
    workflow_runner_lifecycle_service: WorkflowRunnerLifecycleService | None
    run_job_service: RunJobService | None
    run_orchestrator: RunOrchestrator | None
    run_result_service: RunResultService | None
    history_service: HistoryService | None
    civitai_lora_service: CivitaiLoraBrowserService | None = None


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
    onboarding_service: OnboardingSettingsService | None = None,
    model_folder_service: ModelFolderSettingsService | None = None,
    model_tag_store: ModelTagStore | None = None,
    model_ownership_store: ModelOwnershipStore | None = None,
    model_inventory_service: ModelInventoryService | None = None,
    model_download_service: ModelDownloadJobService | None = None,
    noofy_runtime_update_service: NoofyRuntimeUpdateService | None = None,
    history_service: HistoryService | None = None,
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

    gallery = gallery_store or GalleryStore(
        settings.paths.gallery_outputs_dir,
        log_store=getattr(engine_service, "log_store", None),
    )
    history = history_service or getattr(engine_service, "history_service", None)
    if history is None:
        history = HistoryService(
            store=ActivityLogStore(
                settings.paths.data_dir / "history" / "activity.db",
                log_store=getattr(engine_service, "log_store", None),
            ),
            workflow_library_store=getattr(engine_service, "workflow_library_store", None),
            workflow_loader=getattr(engine_service, "workflow_loader", None),
            log_store=getattr(engine_service, "log_store", None),
        )
        setattr(engine_service, "history_service", history)
    if getattr(engine_service, "gallery_capture_service", None) is None:
        engine_service.gallery_capture_service = GalleryCaptureService(
            gallery, log_store=getattr(engine_service, "log_store", None)
        )
    run_result_service = getattr(engine_service, "run_result_service", None)
    if run_result_service is not None:
        run_result_service.gallery_capture_service = getattr(
            engine_service,
            "gallery_capture_service",
            None,
        )
        run_result_service.history_service = history
    capture_service = getattr(engine_service, "gallery_capture_service", None)
    run_job_service = getattr(engine_service, "run_job_service", None)
    if capture_service is not None and run_job_service is not None:
        async def _resolve_gallery_result(job_id: str):
            return await run_job_service.adapter_for_job(job_id).get_result(job_id)

        capture_service.configure(
            resolve_result=_resolve_gallery_result,
            stream_output=run_job_service.stream_output,
            on_items_changed=history.attach_gallery_items,
        )
    run_orchestrator = getattr(engine_service, "run_orchestrator", None)
    if run_orchestrator is not None:
        run_orchestrator.history_service = history
    workflow_library_service = getattr(engine_service, "workflow_library_service", None)
    if workflow_library_service is not None:
        workflow_library_service.history_service = history

    tags = model_tag_store or ModelTagStore(settings.paths.settings_dir / "model-tags.json")
    ownership = model_ownership_store or ModelOwnershipStore(settings.paths.settings_dir / "model-ownership.json")
    if getattr(engine_service, "model_ownership_store", None) is None:
        setattr(engine_service, "model_ownership_store", ownership)
    workflow_import_orchestrator = getattr(engine_service, "workflow_import_orchestrator", None)
    if (
        workflow_import_orchestrator is not None
        and getattr(workflow_import_orchestrator, "model_ownership_store", None) is None
    ):
        setattr(workflow_import_orchestrator, "model_ownership_store", ownership)
    if workflow_import_orchestrator is not None:
        workflow_import_orchestrator.history_service = history
    user_state = user_state_service or UserStateService(settings.paths.user_state_dir)
    assets = asset_service or DashboardAssetService(
        settings.paths.dashboard_assets_dir,
        log_store=getattr(engine_service, "log_store", None),
    )
    if workflow_import_orchestrator is not None:
        workflow_import_orchestrator.user_state_service = user_state
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
    api_keys = api_key_service or ApiKeySettingsService(
        metadata_store=ApiKeyMetadataStore(settings.paths.settings_dir / "api-keys.json"),
        credential_store=create_credential_store(
            data_dir=settings.paths.data_dir,
            settings_dir=settings.paths.settings_dir,
        ),
        log_store=getattr(engine_service, "log_store", None),
    )
    onboarding = onboarding_service or OnboardingSettingsService(
        store=OnboardingSettingsStore(settings.paths.settings_dir / "onboarding.json"),
        log_store=getattr(engine_service, "log_store", None),
    )
    run_orchestrator = getattr(engine_service, "run_orchestrator", None)
    if run_orchestrator is not None:
        run_orchestrator.credential_resolver = api_keys.get_key
        run_orchestrator.media_staging_resolver = MediaInputStagingResolver(
            dashboard_assets_dir=getattr(assets, "assets_dir", settings.paths.dashboard_assets_dir),
            gallery_store=gallery,
            log_store=getattr(engine_service, "log_store", None),
            workflow_store_dir=settings.paths.workflow_packages_store_dir,
            dashboard_overrides_dir=settings.paths.workflow_dashboard_overrides_dir,
            package_search_roots=[
                settings.workflows_dir,
                settings.paths.user_workflows_dir,
                settings.paths.workflow_packages_store_dir,
            ],
        )
    model_availability_service = getattr(engine_service, "model_availability_service", None)
    provider_resolver = getattr(model_availability_service, "provider_resolver", None)
    if provider_resolver is not None:
        provider_resolver.api_key_resolver = api_keys.get_key
    capsule_installer = getattr(engine_service, "capsule_installer", None)
    model_store = getattr(capsule_installer, "model_store", None)
    if model_store is not None and hasattr(model_store, "download_headers_resolver"):
        model_store.download_headers_resolver = lambda url: provider_auth_headers_for_url(
            url,
            api_keys.get_key,
        )
    downloads = model_download_service or ModelDownloadJobService(
        engine_service=engine_service,
        model_folder_service=folders,
        ownership_store=ownership,
        log_store=getattr(engine_service, "log_store", None),
    )
    developer_runtime_override = settings.developer_runtime_override_active
    noofy_runtime_log_store = getattr(engine_service, "log_store", None) or LogStore()
    noofy_runtime_updates = noofy_runtime_update_service or NoofyRuntimeUpdateService(
        paths=settings.paths,
        packaged_runtime=settings.packaged_runtime_active,
        developer_override=developer_runtime_override,
        update_repo=settings.noofy_runtime_update_repo,
        bundled_resource_dir=(
            Path(settings.noofy_bundled_resource_dir)
            if settings.noofy_bundled_resource_dir
            else None
        ),
        log_store=noofy_runtime_log_store,
    )
    civitai_lora_service = CivitaiLoraBrowserService(
        engine_service=engine_service,
        api_key_service=api_keys,
        model_folder_service=folders,
        model_download_service=downloads,
        log_store=getattr(engine_service, "log_store", None),
    )

    return ApiServices(
        engine_service=engine_service,
        comfyui_sidecar_service=(
            comfyui_sidecar_service
            if comfyui_sidecar_service is not None
            else getattr(engine_service, "comfyui_sidecar_service", engine_service)
        ),
        user_state_service=user_state,
        asset_service=assets,
        gallery_store=gallery,
        api_key_service=api_keys,
        onboarding_service=onboarding,
        model_folder_service=folders,
        model_tag_store=tags,
        model_ownership_store=ownership,
        model_inventory_service=inventory,
        model_download_service=downloads,
        noofy_runtime_update_service=noofy_runtime_updates,
        workflow_library_service=workflow_library_service,
        dashboard_authoring_service=getattr(engine_service, "dashboard_authoring", None),
        workflow_exporter=getattr(engine_service, "workflow_exporter", None),
        workflow_import_orchestrator=workflow_import_orchestrator,
        workflow_runner_lifecycle_service=getattr(engine_service, "workflow_runner_lifecycle_service", None),
        run_job_service=getattr(engine_service, "run_job_service", None),
        run_orchestrator=run_orchestrator,
        run_result_service=run_result_service,
        history_service=history,
        civitai_lora_service=civitai_lora_service,
    )


ApiServicesFactory = Callable[[], ApiServices]
