from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from app.core.config import settings
from app.engine.models import WorkflowRunRequest
from app.engine.service import EngineService, create_default_engine_service
from app.runtime.comfyui_updates import ComfyUIRebuildRequest, ComfyUIUpdateRequest
from app.runtime.launch_settings import ComfyUILaunchSettings
from app.workflows.assets import AssetUploadError, DashboardAssetService
from app.workflows.importer import NoofyImportError
from app.workflows.user_state import UserStateService

router = APIRouter()
engine_service: EngineService = create_default_engine_service()
_user_state_service = UserStateService(settings.paths.user_state_dir)
_asset_service = DashboardAssetService(settings.paths.dashboard_assets_dir)


@router.get("/paths")
async def resolved_paths():
    return settings.paths.writable_status()


@router.get("/health")
async def health():
    return await engine_service.health()


@router.get("/logs")
async def list_logs(level: str | None = None, limit: int = 200):
    return engine_service.list_logs(level=level, limit=limit)


@router.get("/diagnostics")
async def diagnostics(
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
async def storage_diagnostics():
    return engine_service.storage_diagnostics_payload()


@router.get("/trust/policy")
async def trust_policy():
    return engine_service.trust_policy_payload()


@router.get("/runtime")
async def runtime_status():
    return await engine_service.runtime_status()


@router.get("/engine/comfyui/status")
async def comfyui_status():
    return await engine_service.runtime_status()


@router.get("/engine/comfyui/launch-settings")
async def comfyui_launch_settings():
    return engine_service.comfyui_launch_settings()


@router.put("/engine/comfyui/launch-settings")
async def update_comfyui_launch_settings(request: ComfyUILaunchSettings):
    return await engine_service.update_comfyui_launch_settings(request)


@router.post("/engine/comfyui/start")
async def start_comfyui():
    return await engine_service.start_comfyui()


@router.post("/engine/comfyui/bootstrap")
async def bootstrap_comfyui_runtime():
    return await engine_service.bootstrap_comfyui_runtime()


@router.post("/engine/comfyui/stop")
async def stop_comfyui():
    return await engine_service.stop_comfyui()


@router.get("/engine/comfyui/versions")
async def comfyui_versions():
    return await engine_service.comfyui_versions()


@router.post("/engine/comfyui/update")
async def update_comfyui(request: ComfyUIUpdateRequest):
    return await engine_service.update_comfyui(request)


@router.post("/engine/comfyui/rebuild")
async def rebuild_comfyui(request: ComfyUIRebuildRequest):
    return await engine_service.rebuild_comfyui(request)


@router.get("/engine/comfyui/update/status")
async def comfyui_update_status():
    return engine_service.comfyui_update_status()


@router.get("/runners")
async def list_runners():
    return [descriptor.model_dump() for descriptor in engine_service.list_runners()]


@router.get("/memory-governor/metrics")
async def memory_governor_metrics():
    return {"metrics": engine_service.memory_governor_metrics()}


@router.get("/workflows")
async def list_workflows() -> list[dict[str, object]]:
    return engine_service.list_workflows()


@router.post("/workflows/import")
async def import_workflow(
    request: Request,
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


@router.get("/workflows/{workflow_id}/package")
async def get_workflow_package(workflow_id: str):
    try:
        return engine_service.get_workflow_package(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/install-state")
async def get_workflow_install_state(workflow_id: str):
    return engine_service.get_install_state(workflow_id)


@router.get("/workflows/{workflow_id}/install-state/developer-details")
async def get_workflow_install_state_developer_details(workflow_id: str):
    return engine_service.get_install_state_developer_details(workflow_id)


@router.get("/workflows/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    try:
        return engine_service.workflow_status(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/prepare")
async def prepare_workflow(workflow_id: str):
    return await engine_service.prepare_workflow(workflow_id)


@router.delete("/workflows/{workflow_id}/prepare")
async def cancel_workflow_preparation(workflow_id: str):
    return engine_service.cancel_preparation(workflow_id)


@router.post("/workflows/{workflow_id}/runner/start")
async def start_workflow_runner(workflow_id: str):
    return await engine_service.start_workflow_runner(workflow_id)


@router.delete("/workflows/runner/queue/{queue_id}")
async def cancel_queued_runner_start(queue_id: str):
    return engine_service.cancel_queued_runner_start(queue_id)


@router.post("/workflows/{workflow_id}/runner/stop")
async def stop_workflow_runner(workflow_id: str):
    return await engine_service.stop_workflow_runner(workflow_id)


@router.post("/workflows/{workflow_id}/runner/leases")
async def open_workflow_runner_lease(workflow_id: str):
    try:
        return engine_service.open_workflow_runner_lease(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/workflows/{workflow_id}/runner/leases/{lease_id}")
async def close_workflow_runner_lease(workflow_id: str, lease_id: str):
    try:
        return engine_service.close_workflow_runner_lease(workflow_id, lease_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/validate")
async def validate_workflow(workflow_id: str):
    try:
        return await engine_service.validate_workflow(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/run")
async def run_workflow(workflow_id: str, request: WorkflowRunRequest):
    try:
        return await engine_service.run_workflow(workflow_id, request.inputs, request.options)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/progress")
async def get_progress(job_id: str):
    return await engine_service.get_progress(job_id)


@router.get("/jobs/{job_id}/logs")
async def list_job_logs(job_id: str, level: str | None = None, limit: int = 200):
    return engine_service.list_job_logs(job_id, level=level, limit=limit)


@router.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str):
    return StreamingResponse(engine_service.stream_progress_events(job_id), media_type="text/event-stream")


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str):
    return await engine_service.cancel_job(job_id)


@router.get("/jobs/{job_id}/result")
async def get_result(job_id: str):
    return await engine_service.get_result(job_id)


@router.get("/workflows/{workflow_id}/bindable-inputs")
async def get_bindable_inputs(workflow_id: str):
    try:
        return engine_service.get_bindable_inputs(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/workflows/{workflow_id}/unresolved-inputs")
async def get_unresolved_inputs(workflow_id: str):
    try:
        return engine_service.get_unresolved_inputs(workflow_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/workflows/{workflow_id}/dashboard/validate")
async def validate_dashboard(workflow_id: str, request: Request):
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
async def save_dashboard(workflow_id: str, request: Request):
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
async def export_workflow(workflow_id: str):
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
async def upload_workflow_image(workflow_id: str, image: UploadFile = File(...)):
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
async def list_available_models():
    return await engine_service.list_available_models()


# ─── User state ──────────────────────────────────────────────────────────────

@router.get("/workflows/{workflow_id}/user-state")
async def get_user_state(workflow_id: str):
    return _user_state_service.get(workflow_id)


@router.put("/workflows/{workflow_id}/user-state")
async def save_user_state(workflow_id: str, request: Request):
    from app.workflows.user_state import WorkflowUserState
    body = await request.json()
    body["workflow_id"] = workflow_id
    try:
        state = WorkflowUserState.model_validate(body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _user_state_service.save(state)


@router.delete("/workflows/{workflow_id}/user-state/values")
async def clear_user_state_values(workflow_id: str):
    return _user_state_service.clear_values(workflow_id)


@router.delete("/workflows/{workflow_id}/user-state/layout")
async def clear_user_state_layout(workflow_id: str):
    return _user_state_service.clear_layout(workflow_id)


# ─── Dashboard assets ────────────────────────────────────────────────────────

@router.post("/workflows/{workflow_id}/assets/image")
async def upload_dashboard_asset(workflow_id: str, image: UploadFile = File(...)):
    data = await image.read()
    content_type = image.content_type or "application/octet-stream"
    original_filename = image.filename or "upload"
    try:
        return _asset_service.store(data, content_type, original_filename)
    except AssetUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/assets/{asset_id}/metadata")
async def get_dashboard_asset_metadata(asset_id: str):
    try:
        path = _asset_service.asset_path(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return _asset_service.metadata(asset_id)


@router.get("/assets/{asset_id}")
async def serve_dashboard_asset(asset_id: str):
    try:
        path = _asset_service.asset_path(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Asset not found.")
    return FileResponse(path, media_type=_asset_service.content_type(asset_id))
