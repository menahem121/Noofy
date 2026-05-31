from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import GalleryCaptureServiceDep, RunJobServiceDep, RunResultServiceDep

router = APIRouter()


@router.get("/jobs/{job_id}/gallery")
async def get_job_gallery_status(job_id: str, gallery_capture: GalleryCaptureServiceDep):
    return gallery_capture.job_status(job_id)


@router.post("/jobs/{job_id}/gallery/{control_id}")
async def save_job_output_to_gallery(
    job_id: str, control_id: str, gallery_capture: GalleryCaptureServiceDep
):
    try:
        return gallery_capture.schedule_output_save(job_id, control_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/jobs/{job_id}/gallery/{control_id}/cancel")
async def cancel_job_output_gallery_save(
    job_id: str, control_id: str, gallery_capture: GalleryCaptureServiceDep
):
    try:
        return gallery_capture.cancel_output_save(job_id, control_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    try:
        return await job_service.cancel_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/jobs/{job_id}/result")
async def get_result(job_id: str, result_service: RunResultServiceDep):
    return await result_service.get_result(job_id)


@router.get("/jobs/{job_id}/outputs/view")
async def get_job_output_view(
    job_id: str,
    job_service: RunJobServiceDep,
    request: Request,
    filename: str,
    subfolder: str = "",
    output_type: str = Query("output", alias="type"),
    download: bool = False,
):
    try:
        output = await job_service.stream_output(
            job_id,
            filename,
            subfolder,
            output_type,
            request.headers.get("range"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    headers = dict(output.headers)
    if download:
        safe_filename = Path(filename.replace("\\", "/")).name.strip() or "noofy-output"
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(safe_filename)}"
    return StreamingResponse(
        output.body,
        status_code=output.status_code,
        media_type=output.media_type,
        headers=headers,
    )
