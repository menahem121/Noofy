from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import Fp8ConversionServiceDep
from app.workflows.fp8_conversion import Fp8ConversionConflictError

router = APIRouter()


class Fp8ModelReference(BaseModel):
    folder: str
    filename: str


class Fp8AlternativeDownloadRequest(Fp8ModelReference):
    url: str


@router.post("/workflows/{workflow_id}/fp8-compatibility/convert")
async def start_fp8_conversion(
    workflow_id: str,
    request: Fp8ModelReference,
    service: Fp8ConversionServiceDep,
):
    try:
        return service.start(workflow_id, request.folder, request.filename)
    except Fp8ConversionConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "fp8_conversion_already_running",
                "message": str(exc),
                "job_id": exc.job_id,
            },
        ) from exc
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "fp8_conversion_model_not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "fp8_conversion_error", "message": str(exc)},
        ) from exc


@router.get("/workflows/{workflow_id}/fp8-compatibility/convert/{job_id}")
async def get_fp8_conversion(
    workflow_id: str,
    job_id: str,
    service: Fp8ConversionServiceDep,
):
    try:
        return service.status(workflow_id, job_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "fp8_conversion_not_found", "message": str(exc)},
        ) from exc


@router.post("/workflows/{workflow_id}/fp8-compatibility/convert/{job_id}/cancel")
async def cancel_fp8_conversion(
    workflow_id: str,
    job_id: str,
    service: Fp8ConversionServiceDep,
):
    try:
        return service.cancel(workflow_id, job_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "fp8_conversion_not_found", "message": str(exc)},
        ) from exc


@router.post("/workflows/{workflow_id}/fp8-compatibility/download")
async def start_fp8_alternative_download(
    workflow_id: str,
    request: Fp8AlternativeDownloadRequest,
    service: Fp8ConversionServiceDep,
):
    try:
        return service.start_alternative_download(
            workflow_id,
            request.folder,
            request.filename,
            request.url,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "fp8_compatibility_model_not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "fp8_alternative_download_error", "message": str(exc)},
        ) from exc


@router.post("/workflows/{workflow_id}/fp8-compatibility/dismiss")
async def dismiss_fp8_compatibility(
    workflow_id: str,
    request: Fp8ModelReference,
    service: Fp8ConversionServiceDep,
):
    service.record_dismissal(workflow_id, request.folder, request.filename)
    return {"status": "dismissed"}
