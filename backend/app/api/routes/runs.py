from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from app.api.deps import RunJobServiceDep, RunResultServiceDep

router = APIRouter()


@router.get("/jobs/{job_id}/progress")
async def get_progress(job_id: str, job_service: RunJobServiceDep):
    return await job_service.get_progress(job_id)


@router.get("/jobs/{job_id}/logs")
async def list_job_logs(
    job_id: str,
    job_service: RunJobServiceDep,
    level: str | None = None,
    limit: int = 200,
):
    return job_service.list_job_logs(job_id, level=level, limit=limit)


@router.get("/jobs/{job_id}/events")
async def stream_job_events(job_id: str, result_service: RunResultServiceDep):
    return StreamingResponse(
        result_service.stream_progress_events(job_id),
        media_type="text/event-stream",
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, job_service: RunJobServiceDep):
    return await job_service.cancel_job(job_id)


@router.get("/jobs/{job_id}/result")
async def get_result(job_id: str, result_service: RunResultServiceDep):
    return await result_service.get_result(job_id)


@router.get("/jobs/{job_id}/outputs/view")
async def get_job_output_view(
    job_id: str,
    job_service: RunJobServiceDep,
    filename: str,
    subfolder: str = "",
    output_type: str = Query("output", alias="type"),
):
    try:
        content, media_type = await job_service.fetch_output(
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
