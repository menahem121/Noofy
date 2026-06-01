from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, StrictBool

from app.api.deps import GalleryStoreDep, HistoryServiceDep

router = APIRouter()


class GalleryFavoriteUpdateRequest(BaseModel):
    favorite: StrictBool


@router.get("/gallery")
async def list_gallery(
    gallery_store: GalleryStoreDep,
    kind: str | None = None,
    search: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100),
    cursor: str | None = None,
    accepted_extensions: list[str] | None = Query(default=None),
    accepted_mime_types: list[str] | None = Query(default=None),
):
    return gallery_store.list_items(
        kind=kind,
        search=search,
        limit=limit,
        cursor=cursor,
        accepted_extensions=accepted_extensions,
        accepted_mime_types=accepted_mime_types,
    )


@router.get("/gallery/{item_id}")
async def get_gallery_item(item_id: str, gallery_store: GalleryStoreDep):
    item = gallery_store.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Gallery item not found.")
    return item


@router.get("/gallery/{item_id}/content")
async def get_gallery_content(item_id: str, gallery_store: GalleryStoreDep, download: bool = False):
    item = gallery_store.get_item(item_id)
    path = gallery_store.content_path(item_id)
    if item is None or path is None:
        raise HTTPException(status_code=404, detail="Gallery item not found.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Gallery item file is missing.")
    headers = {}
    if download:
        filename = Path(item.filename).name.strip() or "noofy-output"
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
    return FileResponse(path, media_type=item.mime_type or "application/octet-stream", headers=headers)


@router.get("/gallery/{item_id}/image")
async def get_gallery_image(item_id: str, gallery_store: GalleryStoreDep):
    """Compatibility alias for image URLs persisted by older History rows."""
    return await get_gallery_content(item_id, gallery_store)


@router.get("/gallery/{item_id}/thumbnail")
async def get_gallery_thumbnail(item_id: str, gallery_store: GalleryStoreDep):
    path = gallery_store.content_path(item_id, thumbnail=True)
    if path is None:
        raise HTTPException(status_code=404, detail="Gallery thumbnail not found.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Gallery thumbnail file is missing.")
    return FileResponse(path, media_type="image/webp")


@router.delete("/gallery/{item_id}")
async def delete_gallery_item(
    item_id: str, gallery_store: GalleryStoreDep, history_service: HistoryServiceDep
):
    try:
        deleted = gallery_store.delete_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Gallery item not found.")
    history_service.attach_gallery_items(deleted.job_id, gallery_store.items_for_job(deleted.job_id))
    return {"id": item_id, "deleted": True}


@router.put("/gallery/{item_id}/favorite")
async def update_gallery_favorite(
    item_id: str,
    request: GalleryFavoriteUpdateRequest,
    gallery_store: GalleryStoreDep,
):
    item = gallery_store.set_favorite(item_id, request.favorite)
    if item is None:
        raise HTTPException(status_code=404, detail="Gallery item not found.")
    return item
