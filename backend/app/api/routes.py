import os
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.api.schemas import (
    ApiKeyUpdateRequest,
    ComfyUILaunchSettings,
    ComfyUIRebuildRequest,
    ComfyUIUpdateRequest,
    ModelFolderUpdateRequest,
)
from app.composition import ApiServices
from app.core.config import settings
from app.engine.models import WorkflowRunRequest
from app.engine.service import EngineService, ImportSessionExpiredError
from app.runtime.comfyui_sidecar_service import ComfyUISidecarService
from app.settings.api_keys import (
    ApiKeySettingsService,
    CredentialStoreUnavailable,
    provider_from_slug,
)
from app.settings.model_folders import ModelFolderSettingsService
from app.workflows.assets import AssetUploadError, DashboardAssetService
from app.workflows.importer import NoofyImportError
from app.workflows.user_state import UserStateService

router = APIRouter()


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


def get_api_key_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ApiKeySettingsService:
    return services.api_key_service


def get_model_folder_service(
    services: Annotated[ApiServices, Depends(get_api_services)],
) -> ModelFolderSettingsService:
    return services.model_folder_service


EngineServiceDep = Annotated[EngineService, Depends(get_engine_service)]
ComfyUISidecarServiceDep = Annotated[
    ComfyUISidecarService,
    Depends(get_comfyui_sidecar_service),
]
UserStateServiceDep = Annotated[UserStateService, Depends(get_user_state_service)]
DashboardAssetServiceDep = Annotated[DashboardAssetService, Depends(get_asset_service)]
ApiKeyServiceDep = Annotated[ApiKeySettingsService, Depends(get_api_key_service)]
ModelFolderServiceDep = Annotated[ModelFolderSettingsService, Depends(get_model_folder_service)]


@router.get("/paths")
async def resolved_paths(model_folder_service: ModelFolderServiceDep):
    entries = settings.paths.writable_status()
    model_folder_settings = model_folder_service.settings(ensure_folders=False)
    active_models_dir = Path(model_folder_settings.noofy_models_dir)
    entries["models_dir"] = {
        "path": str(active_models_dir),
        "exists": active_models_dir.exists(),
        "writable": os.access(active_models_dir, os.W_OK)
        if active_models_dir.exists()
        else False,
    }
    return entries


@router.get("/health")
async def health(engine_service: EngineServiceDep):
    return await engine_service.health()


@router.get("/logs")
async def list_logs(
    engine_service: EngineServiceDep,
    level: str | None = None,
    limit: int = 200,
):
    return engine_service.list_logs(level=level, limit=limit)


@router.get("/diagnostics")
async def diagnostics(
    engine_service: EngineServiceDep,
    workflow_id: str | None = None,
    developer_details: bool = False,
    limit: int = 200,
):
    return engine_service.diagnostics_payload(
        workflow_id=workflow_id,
        include_developer_details=developer_details,
        limit=limit,
    )


@router.get("/storage/diagnostics")
async def storage_diagnostics(engine_service: EngineServiceDep):
    return engine_service.storage_diagnostics_payload()


@router.get("/settings/apis")
async def api_key_settings(api_key_service: ApiKeyServiceDep):
    return api_key_service.settings()


@router.get("/settings/model-folders")
async def model_folder_settings(model_folder_service: ModelFolderServiceDep):
    return model_folder_service.settings()


@router.put("/settings/model-folders")
async def update_model_folder_settings(
    request: ModelFolderUpdateRequest,
    model_folder_service: ModelFolderServiceDep,
):
    try:
        return model_folder_service.update(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/settings/apis/{provider}/key")
async def save_api_key(
    provider: str,
    request: ApiKeyUpdateRequest,
    api_key_service: ApiKeyServiceDep,
):
    resolved_provider = provider_from_slug(provider)
    if resolved_provider is None:
        raise HTTPException(status_code=404, detail="Unknown API key provider.")
    try:
        return api_key_service.save_key(resolved_provider, request.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CredentialStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/settings/apis/{provider}/key")
async def clear_api_key(
    provider: str,
    api_key_service: ApiKeyServiceDep,
):
    resolved_provider = provider_from_slug(provider)
    if resolved_provider is None:
        raise HTTPException(status_code=404, detail="Unknown API key provider.")
    try:
        return api_key_service.clear_key(resolved_provider)
    except CredentialStoreUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/trust/policy")
async def trust_policy(engine_service: EngineServiceDep):
    return engine_service.trust_policy_payload()


@router.get("/runtime")
async def runtime_status(engine_service: EngineServiceDep):
    return await engine_service.runtime_status()


@router.get("/resources")
async def resource_snapshot(engine_service: EngineServiceDep):
    return engine_service.resource_snapshot()


@router.get("/engine/comfyui/status")
async def comfyui_status(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.runtime_status()


@router.get("/engine/comfyui/launch-settings")
async def comfyui_launch_settings(sidecar_service: ComfyUISidecarServiceDep):
    return sidecar_service.comfyui_launch_settings()


@router.put("/engine/comfyui/launch-settings")
async def update_comfyui_launch_settings(
    request: ComfyUILaunchSettings,
    sidecar_service: ComfyUISidecarServiceDep,
):
    return await sidecar_service.update_comfyui_launch_settings(request)


@router.post("/engine/comfyui/start")
async def start_comfyui(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.start_comfyui()


@router.post("/engine/comfyui/bootstrap")
async def bootstrap_comfyui_runtime(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.bootstrap_comfyui_runtime()


@router.post("/engine/comfyui/stop")
async def stop_comfyui(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.stop_comfyui()


@router.get("/engine/comfyui/versions")
async def comfyui_versions(
    sidecar_service: ComfyUISidecarServiceDep,
    check_upstream: bool = False,
):
    return await sidecar_service.comfyui_versions(check_upstream=check_upstream)


@router.post("/engine/comfyui/update")
async def update_comfyui(
    request: ComfyUIUpdateRequest,
    sidecar_service: ComfyUISidecarServiceDep,
):
    return await sidecar_service.update_comfyui(request)


@router.post("/engine/comfyui/rebuild")
async def rebuild_comfyui(
    request: ComfyUIRebuildRequest,
    sidecar_service: ComfyUISidecarServiceDep,
):
    return await sidecar_service.rebuild_comfyui(request)


@router.get("/engine/comfyui/update/status")
async def comfyui_update_status(sidecar_service: ComfyUISidecarServiceDep):
    return sidecar_service.comfyui_update_status()


@router.get("/runners")
async def list_runners(engine_service: EngineServiceDep):
    return [descriptor.model_dump() for descriptor in engine_service.list_runners()]


@router.get("/memory-governor/metrics")
async def memory_governor_metrics(engine_service: EngineServiceDep):
    return {"metrics": engine_service.memory_governor_metrics()}


@router.get("/workflows")
async def list_workflows(engine_service: EngineServiceDep) -> list[dict[str, object]]:
    return engine_service.list_workflows()


@router.post("/workflows/import")
async def import_workflow(
    request: Request,
    engine_service: EngineServiceDep,
    filename: str | None = None,
    allow_unverified_community_preparation: bool = False,
):
    try:
        return engine_service.import_workflow_archive(
            await request.body(),
            original_filename=filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )
    except NoofyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workflows/import/preview")
async def preview_workflow_import(
    request: Request,
    engine_service: EngineServiceDep,
    filename: str | None = None,
    allow_unverified_community_preparation: bool = False,
):
    try:
        return engine_service.preview_workflow_import(
            await request.body(),
            original_filename=filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )
    except NoofyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/workflows/import/{import_session_id}/download-models")
async def download_import_missing_models(
    import_session_id: str,
    engine_service: EngineServiceDep,
):
    try:
        return engine_service.start_missing_model_download_for_import(import_session_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/import/{import_session_id}/download-models/{job_id}")
async def get_import_model_download_status(
    import_session_id: str,
    job_id: str,
    engine_service: EngineServiceDep,
):
    try:
        return engine_service.import_model_download_status(import_session_id, job_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/import/{import_session_id}/download-models/{job_id}/cancel")
async def cancel_import_model_download(
    import_session_id: str,
    job_id: str,
    engine_service: EngineServiceDep,
):
    try:
        return engine_service.cancel_import_model_download_job(import_session_id, job_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/import/{import_session_id}/commit")
async def commit_workflow_import(
    import_session_id: str,
    engine_service: EngineServiceDep,
):
    try:
        return engine_service.commit_workflow_import(import_session_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NoofyImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/workflows/import/{import_session_id}")
async def cancel_workflow_import(
    import_session_id: str,
    engine_service: EngineServiceDep,
):
    try:
        return engine_service.cancel_workflow_import(import_session_id)
    except ImportSessionExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/package")
async def get_workflow_package(workflow_id: str, engine_service: EngineServiceDep):
    try:
        return engine_service.get_workflow_package(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/model-summary")
async def get_workflow_model_summary(workflow_id: str, engine_service: EngineServiceDep):
    try:
        return engine_service.model_availability_summary(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/install-state")
async def get_workflow_install_state(workflow_id: str, engine_service: EngineServiceDep):
    return engine_service.get_install_state(workflow_id)


@router.get("/workflows/{workflow_id}/install-state/developer-details")
async def get_workflow_install_state_developer_details(
    workflow_id: str,
    engine_service: EngineServiceDep,
):
    return engine_service.get_install_state_developer_details(workflow_id)


@router.get("/workflows/{workflow_id}/status")
async def get_workflow_status(workflow_id: str, engine_service: EngineServiceDep):
    try:
        return engine_service.workflow_status(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/prepare")
async def prepare_workflow(workflow_id: str, engine_service: EngineServiceDep):
    return await engine_service.prepare_workflow(workflow_id)


@router.delete("/workflows/{workflow_id}/prepare")
async def cancel_workflow_preparation(workflow_id: str, engine_service: EngineServiceDep):
    return engine_service.cancel_preparation(workflow_id)


@router.post("/workflows/{workflow_id}/runner/start")
async def start_workflow_runner(workflow_id: str, engine_service: EngineServiceDep):
    return await engine_service.start_workflow_runner(workflow_id)


@router.delete("/workflows/runner/queue/{queue_id}")
async def cancel_queued_runner_start(queue_id: str, engine_service: EngineServiceDep):
    return engine_service.cancel_queued_runner_start(queue_id)


@router.post("/workflows/{workflow_id}/runner/stop")
async def stop_workflow_runner(workflow_id: str, engine_service: EngineServiceDep):
    return await engine_service.stop_workflow_runner(workflow_id)


@router.post("/workflows/{workflow_id}/runner/leases")
async def open_workflow_runner_lease(workflow_id: str, engine_service: EngineServiceDep):
    try:
        return engine_service.open_workflow_runner_lease(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/workflows/{workflow_id}/runner/leases/{lease_id}")
async def close_workflow_runner_lease(
    workflow_id: str,
    lease_id: str,
    engine_service: EngineServiceDep,
):
    try:
        return engine_service.close_workflow_runner_lease(workflow_id, lease_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/validate")
async def validate_workflow(workflow_id: str, engine_service: EngineServiceDep):
    try:
        return await engine_service.validate_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    request: WorkflowRunRequest,
    engine_service: EngineServiceDep,
):
    try:
        return await engine_service.run_workflow(workflow_id, request.inputs, request.options)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/progress")
async def get_progress(job_id: str, engine_service: EngineServiceDep):
    return await engine_service.get_progress(job_id)


@router.get("/jobs/{job_id}/logs")
async def list_job_logs(
    job_id: str,
    engine_service: EngineServiceDep,
    level: str | None = None,
    limit: int = 200,
):
    return engine_service.list_job_logs(job_id, level=level, limit=limit)


@router.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str, engine_service: EngineServiceDep):
    return StreamingResponse(
        engine_service.stream_progress_events(job_id),
        media_type="text/event-stream",
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, engine_service: EngineServiceDep):
    return await engine_service.cancel_job(job_id)


@router.get("/jobs/{job_id}/result")
async def get_result(job_id: str, engine_service: EngineServiceDep):
    return await engine_service.get_result(job_id)


@router.get("/jobs/{job_id}/outputs/view")
async def get_job_output_view(
    job_id: str,
    engine_service: EngineServiceDep,
    filename: str,
    subfolder: str = "",
    output_type: str = Query("output", alias="type"),
):
    try:
        content, media_type = await engine_service.fetch_output(
            job_id,
            filename,
            subfolder,
            output_type,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(content=content, media_type=media_type)


@router.get("/workflows/{workflow_id}/bindable-inputs")
async def get_bindable_inputs(workflow_id: str, engine_service: EngineServiceDep):
    try:
        return engine_service.get_bindable_inputs(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/unresolved-inputs")
async def get_unresolved_inputs(workflow_id: str, engine_service: EngineServiceDep):
    try:
        return engine_service.get_unresolved_inputs(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/dashboard/validate")
async def validate_dashboard(
    workflow_id: str,
    request: Request,
    engine_service: EngineServiceDep,
):
    try:
        body = await request.json()
        return engine_service.validate_dashboard(
            workflow_id,
            inputs=body.get("inputs", []),
            dashboard=body.get("dashboard", {}),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/workflows/{workflow_id}/dashboard")
async def save_dashboard(
    workflow_id: str,
    request: Request,
    engine_service: EngineServiceDep,
):
    try:
        body = await request.json()
        return engine_service.save_dashboard(
            workflow_id,
            inputs=body.get("inputs", []),
            dashboard=body.get("dashboard", {}),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/export")
async def export_workflow(workflow_id: str, engine_service: EngineServiceDep):
    try:
        archive_bytes, filename = engine_service.export_workflow_archive(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=archive_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/workflows/{workflow_id}/uploads/image")
async def upload_workflow_image(
    workflow_id: str,
    engine_service: EngineServiceDep,
    image: UploadFile = File(...),
):
    try:
        data = await image.read()
        result = await engine_service.upload_workflow_image(
            workflow_id,
            filename=image.filename or "upload.png",
            data=data,
            content_type=image.content_type or "image/png",
        )
        return result
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/models")
async def list_available_models(engine_service: EngineServiceDep):
    return await engine_service.list_available_models()


# ─── User state ──────────────────────────────────────────────────────────────

@router.get("/workflows/{workflow_id}/user-state")
async def get_user_state(
    workflow_id: str,
    user_state_service: UserStateServiceDep,
):
    return user_state_service.get(workflow_id)


@router.put("/workflows/{workflow_id}/user-state")
async def save_user_state(
    workflow_id: str,
    request: Request,
    user_state_service: UserStateServiceDep,
):
    from app.workflows.user_state import WorkflowUserState
    body = await request.json()
    body["workflow_id"] = workflow_id
    try:
        state = WorkflowUserState.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return user_state_service.save(state)


@router.delete("/workflows/{workflow_id}/user-state/values")
async def clear_user_state_values(
    workflow_id: str,
    user_state_service: UserStateServiceDep,
):
    return user_state_service.clear_values(workflow_id)


@router.delete("/workflows/{workflow_id}/user-state/layout")
async def clear_user_state_layout(
    workflow_id: str,
    user_state_service: UserStateServiceDep,
):
    return user_state_service.clear_layout(workflow_id)


# ─── Dashboard assets ────────────────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/assets/image")
async def upload_dashboard_asset(
    workflow_id: str,
    asset_service: DashboardAssetServiceDep,
    image: UploadFile = File(...),
):
    data = await image.read()
    content_type = image.content_type or "application/octet-stream"
    original_filename = image.filename or "upload"
    try:
        return asset_service.store(data, content_type, original_filename)
    except AssetUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/assets/{asset_id}/metadata")
async def get_dashboard_asset_metadata(
    asset_id: str,
    asset_service: DashboardAssetServiceDep,
):
    try:
        path = asset_service.asset_path(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return asset_service.metadata(asset_id)


@router.get("/assets/{asset_id}")
async def serve_dashboard_asset(
    asset_id: str,
    asset_service: DashboardAssetServiceDep,
):
    try:
        path = asset_service.asset_path(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(path, media_type=asset_service.content_type(asset_id))
