from fastapi import APIRouter, HTTPException

from app.api.deps import ModelDownloadServiceDep, ModelInventoryServiceDep, ModelTagStoreDep
from app.models.schemas import (
    ModelDownloadStartRequest,
    ModelImportRequest,
    ModelTagAssignmentRequest,
    ModelTagCreateRequest,
)

router = APIRouter()


@router.get("/models")
async def list_models(inventory: ModelInventoryServiceDep):
    return await inventory.inventory()


@router.post("/models/import")
async def import_models(request: ModelImportRequest, inventory: ModelInventoryServiceDep):
    try:
        return inventory.import_models(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "model_import_error", "message": str(exc)},
        ) from exc


@router.delete("/models/{model_key:path}")
async def delete_model(model_key: str, inventory: ModelInventoryServiceDep):
    try:
        return inventory.delete_model(model_key)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "model_not_found", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "model_delete_error", "message": str(exc)},
        ) from exc


@router.post("/models/tags")
async def create_model_tag(request: ModelTagCreateRequest, model_tag_store: ModelTagStoreDep):
    try:
        return model_tag_store.create_tag(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "model_tag_error", "message": str(exc)},
        ) from exc


@router.put("/models/{model_key:path}/tags")
async def update_model_tags(
    model_key: str,
    request: ModelTagAssignmentRequest,
    model_tag_store: ModelTagStoreDep,
):
    return {
        "model_key": model_key,
        "tag_ids": model_tag_store.set_model_tags(model_key, request.tag_ids),
    }


@router.post("/models/downloads")
async def start_model_download(
    request: ModelDownloadStartRequest,
    model_download_service: ModelDownloadServiceDep,
):
    try:
        return model_download_service.start(request)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "model_download_error", "message": str(exc)},
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "model_download_not_found", "message": str(exc)},
        ) from exc


@router.get("/models/downloads/active")
async def get_active_model_download(model_download_service: ModelDownloadServiceDep):
    return model_download_service.active()


@router.get("/models/downloads/{job_id}")
async def get_model_download(job_id: str, model_download_service: ModelDownloadServiceDep):
    try:
        return model_download_service.status(job_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "model_download_not_found", "message": str(exc)},
        ) from exc


@router.post("/models/downloads/{job_id}/cancel")
async def cancel_model_download(job_id: str, model_download_service: ModelDownloadServiceDep):
    try:
        return model_download_service.cancel(job_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "model_download_not_found", "message": str(exc)},
        ) from exc
