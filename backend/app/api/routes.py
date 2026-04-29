from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.engine.models import WorkflowRunRequest
from app.engine.service import EngineService, create_default_engine_service

router = APIRouter()
engine_service: EngineService = create_default_engine_service()


@router.get("/health")
async def health():
    return await engine_service.health()


@router.post("/engine/comfyui/start")
async def start_comfyui():
    return await engine_service.start_comfyui()


@router.post("/engine/comfyui/stop")
async def stop_comfyui():
    return await engine_service.stop_comfyui()


@router.get("/workflows")
async def list_workflows() -> list[dict[str, str]]:
    return engine_service.list_workflows()


@router.post("/workflows/{workflow_id}/validate")
async def validate_workflow(workflow_id: str):
    return await engine_service.validate_workflow(workflow_id)


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
