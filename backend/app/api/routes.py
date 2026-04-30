from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.engine.models import WorkflowRunRequest
from app.engine.service import EngineService, create_default_engine_service

router = APIRouter()
engine_service: EngineService = create_default_engine_service()


@router.get("/paths")
async def resolved_paths():
    return settings.paths.writable_status()


@router.get("/health")
async def health():
    return await engine_service.health()


@router.get("/logs")
async def list_logs(level: str | None = None, limit: int = 200):
    return engine_service.list_logs(level=level, limit=limit)


@router.get("/runtime")
async def runtime_status():
    return await engine_service.runtime_status()


@router.get("/engine/comfyui/status")
async def comfyui_status():
    return await engine_service.runtime_status()


@router.post("/engine/comfyui/start")
async def start_comfyui():
    return await engine_service.start_comfyui()


@router.post("/engine/comfyui/bootstrap")
async def bootstrap_comfyui_runtime():
    return await engine_service.bootstrap_comfyui_runtime()


@router.post("/engine/comfyui/stop")
async def stop_comfyui():
    return await engine_service.stop_comfyui()


@router.get("/runners")
async def list_runners():
    return [descriptor.model_dump() for descriptor in engine_service.list_runners()]


@router.get("/workflows")
async def list_workflows() -> list[dict[str, str]]:
    return engine_service.list_workflows()


@router.get("/workflows/{workflow_id}/install-state")
async def get_workflow_install_state(workflow_id: str):
    return engine_service.get_install_state(workflow_id)


@router.post("/workflows/{workflow_id}/prepare")
async def prepare_workflow(workflow_id: str):
    return await engine_service.prepare_workflow(workflow_id)


@router.post("/workflows/{workflow_id}/runner/start")
async def start_workflow_runner(workflow_id: str):
    return await engine_service.start_workflow_runner(workflow_id)


@router.post("/workflows/{workflow_id}/runner/stop")
async def stop_workflow_runner(workflow_id: str):
    return await engine_service.stop_workflow_runner(workflow_id)


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


@router.get("/models")
async def list_available_models():
    return await engine_service.list_available_models()
