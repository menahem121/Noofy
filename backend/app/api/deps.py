"""FastAPI dependency functions and annotated type aliases for the API layer."""

from typing import Annotated, cast

from fastapi import Depends, Request

from app.composition import ApiServices
from app.engine.service import EngineService
from app.gallery import GalleryCaptureService, GalleryStore
from app.history import HistoryService
from app.models.civitai_loras import CivitaiLoraBrowserService
from app.models.downloads import ModelDownloadJobService
from app.models.inventory import ModelInventoryService
from app.models.tags import ModelTagStore
from app.runtime.comfyui.comfyui_sidecar_service import ComfyUISidecarService
from app.runtime.noofy_runtime import NoofyRuntimeUpdateService
from app.settings.api_keys import ApiKeySettingsService
from app.settings.local_engine import LocalEngineFilesService
from app.settings.onboarding import OnboardingSettingsService
from app.models.folders import ModelFolderSettingsService
from app.runs.job_service import RunJobService
from app.runs.orchestrator import RunOrchestrator
from app.runs.result_service import RunResultService
from app.runtime.runners.lifecycle_service import WorkflowRunnerLifecycleService
from app.workflows.assets import DashboardAssetService
from app.workflows.authoring import DashboardAuthoringService
from app.workflows.exporter import WorkflowExporter
from app.workflows.import_orchestrator import WorkflowImportOrchestrator
from app.workflows.library_service import WorkflowLibraryService
from app.workflows.user_state import UserStateService


def get_api_services(request: Request) -> ApiServices:
    services = getattr(request.app.state, "api_services", None)
    if services is None:
        factory = getattr(request.app.state, "api_service_factory", None)
        if factory is None:
            raise RuntimeError("API services are not configured on app.state.")
        services = factory()
        request.app.state.api_services = services
    return services


def get_engine_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> EngineService:
    return services.engine_service


def get_comfyui_sidecar_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ComfyUISidecarService:
    return cast(ComfyUISidecarService, services.comfyui_sidecar_service)


def get_user_state_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> UserStateService:
    return services.user_state_service


def get_asset_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> DashboardAssetService:
    return services.asset_service


def get_gallery_store(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> GalleryStore:
    return services.gallery_store


def get_api_key_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ApiKeySettingsService:
    return services.api_key_service


def get_onboarding_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> OnboardingSettingsService:
    return services.onboarding_service


def get_model_folder_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ModelFolderSettingsService:
    return services.model_folder_service


def get_model_tag_store(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ModelTagStore:
    return services.model_tag_store


def get_model_download_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ModelDownloadJobService:
    return services.model_download_service


def get_noofy_runtime_update_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> NoofyRuntimeUpdateService:
    return services.noofy_runtime_update_service


def get_local_engine_files_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> LocalEngineFilesService:
    return services.local_engine_files_service


def get_model_inventory_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ModelInventoryService:
    return services.model_inventory_service


def get_civitai_lora_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> CivitaiLoraBrowserService:
    if services.civitai_lora_service is None:
        raise RuntimeError("CivitaiLoraBrowserService is not configured.")
    return services.civitai_lora_service


EngineServiceDep = Annotated[EngineService, Depends(get_engine_service)]
ComfyUISidecarServiceDep = Annotated[ComfyUISidecarService, Depends(get_comfyui_sidecar_service)]
UserStateServiceDep = Annotated[UserStateService, Depends(get_user_state_service)]
DashboardAssetServiceDep = Annotated[DashboardAssetService, Depends(get_asset_service)]
GalleryStoreDep = Annotated[GalleryStore, Depends(get_gallery_store)]
ApiKeyServiceDep = Annotated[ApiKeySettingsService, Depends(get_api_key_service)]
OnboardingServiceDep = Annotated[OnboardingSettingsService, Depends(get_onboarding_service)]
ModelFolderServiceDep = Annotated[ModelFolderSettingsService, Depends(get_model_folder_service)]
ModelTagStoreDep = Annotated[ModelTagStore, Depends(get_model_tag_store)]
ModelDownloadServiceDep = Annotated[ModelDownloadJobService, Depends(get_model_download_service)]
ModelInventoryServiceDep = Annotated[ModelInventoryService, Depends(get_model_inventory_service)]
CivitaiLoraServiceDep = Annotated[CivitaiLoraBrowserService, Depends(get_civitai_lora_service)]
NoofyRuntimeUpdateServiceDep = Annotated[
    NoofyRuntimeUpdateService,
    Depends(get_noofy_runtime_update_service),
]
LocalEngineFilesServiceDep = Annotated[
    LocalEngineFilesService,
    Depends(get_local_engine_files_service),
]


def get_gallery_capture_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> GalleryCaptureService:
    capture = getattr(services.engine_service, "gallery_capture_service", None)
    if capture is None:
        raise RuntimeError("Gallery capture is not configured.")
    return capture


GalleryCaptureServiceDep = Annotated[GalleryCaptureService, Depends(get_gallery_capture_service)]


def get_workflow_library_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> WorkflowLibraryService:
    if services.workflow_library_service is None:
        raise RuntimeError("WorkflowLibraryService is not configured.")
    return services.workflow_library_service


WorkflowLibraryServiceDep = Annotated[WorkflowLibraryService, Depends(get_workflow_library_service)]


def get_dashboard_authoring_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> DashboardAuthoringService:
    if services.dashboard_authoring_service is None:
        raise RuntimeError("DashboardAuthoringService is not configured.")
    return services.dashboard_authoring_service


def get_workflow_exporter(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> WorkflowExporter:
    if services.workflow_exporter is None:
        raise RuntimeError("WorkflowExporter is not configured.")
    return services.workflow_exporter


DashboardAuthoringServiceDep = Annotated[DashboardAuthoringService, Depends(get_dashboard_authoring_service)]
WorkflowExporterDep = Annotated[WorkflowExporter, Depends(get_workflow_exporter)]


def get_workflow_import_orchestrator(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> WorkflowImportOrchestrator:
    if services.workflow_import_orchestrator is None:
        raise RuntimeError("WorkflowImportOrchestrator is not configured.")
    return services.workflow_import_orchestrator


WorkflowImportOrchestratorDep = Annotated[
    WorkflowImportOrchestrator,
    Depends(get_workflow_import_orchestrator),
]


def get_workflow_runner_lifecycle_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> WorkflowRunnerLifecycleService:
    if services.workflow_runner_lifecycle_service is None:
        raise RuntimeError("WorkflowRunnerLifecycleService is not configured.")
    return services.workflow_runner_lifecycle_service


WorkflowRunnerLifecycleServiceDep = Annotated[
    WorkflowRunnerLifecycleService,
    Depends(get_workflow_runner_lifecycle_service),
]


def get_run_job_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> RunJobService:
    if services.run_job_service is None:
        raise RuntimeError("RunJobService is not configured.")
    return services.run_job_service


RunJobServiceDep = Annotated[RunJobService, Depends(get_run_job_service)]


def get_run_orchestrator(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> RunOrchestrator:
    if services.run_orchestrator is None:
        raise RuntimeError("RunOrchestrator is not configured.")
    return services.run_orchestrator


RunOrchestratorDep = Annotated[RunOrchestrator, Depends(get_run_orchestrator)]


def get_run_result_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> RunResultService:
    if services.run_result_service is None:
        raise RuntimeError("RunResultService is not configured.")
    return services.run_result_service


RunResultServiceDep = Annotated[RunResultService, Depends(get_run_result_service)]


def get_history_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> HistoryService:
    if services.history_service is None:
        raise RuntimeError("HistoryService is not configured.")
    return services.history_service


HistoryServiceDep = Annotated[HistoryService, Depends(get_history_service)]
