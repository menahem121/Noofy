from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.api.deps import DashboardAssetServiceDep

router = APIRouter()


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
