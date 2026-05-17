from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.deps import DashboardAssetServiceDep, WorkflowLibraryServiceDep
from app.workflows.assets import AssetUploadError

router = APIRouter()


@router.get("/workflow-icons")
async def list_workflow_icons(asset_service: DashboardAssetServiceDep):
    return {"icons": asset_service.list_workflow_icons()}


@router.post("/workflow-icons")
async def upload_workflow_icon(
    asset_service: DashboardAssetServiceDep,
    image: UploadFile = File(...),
):
    data = await image.read()
    content_type = image.content_type or "application/octet-stream"
    original_filename = image.filename or "workflow-icon"
    try:
        return asset_service.store_workflow_icon(data, content_type, original_filename)
    except AssetUploadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/workflow-icons/{icon_id:path}")
async def delete_workflow_icon(
    icon_id: str,
    asset_service: DashboardAssetServiceDep,
    library: WorkflowLibraryServiceDep,
):
    users = [
        workflow["name"]
        for workflow in library.list_workflows()
        if workflow.get("icon") == icon_id
    ]
    if users:
        raise HTTPException(
            status_code=409,
            detail=f"This icon is used by {users[0]}. Choose another icon for that workflow before deleting it.",
        )
    try:
        asset_service.delete_workflow_icon(icon_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": True, "id": icon_id}


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
