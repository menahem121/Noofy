from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, StrictBool

from app.api.deps import GalleryStoreDep

router = APIRouter()


class GalleryFavoriteUpdateRequest(BaseModel):
    favorite: StrictBool


@router.get("/gallery")
async def list_gallery(gallery_store: GalleryStoreDep):
    return gallery_store.list_items()


@router.get("/gallery/{item_id}")
async def get_gallery_item(item_id: str, gallery_store: GalleryStoreDep):
    item = gallery_store.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Gallery item not found.")
    return item


@router.get("/gallery/{item_id}/image")
async def get_gallery_image(item_id: str, gallery_store: GalleryStoreDep):
    item = gallery_store.get_item(item_id)
    path = gallery_store.image_path(item_id)
    if item is None or path is None:
        raise HTTPException(status_code=404, detail="Gallery image not found.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Gallery image file is missing.")
    return FileResponse(path, media_type=item.mime_type or "application/octet-stream")


@router.get("/gallery/{item_id}/thumbnail")
async def get_gallery_thumbnail(item_id: str, gallery_store: GalleryStoreDep):
    path = gallery_store.image_path(item_id, thumbnail=True)
    if path is None:
        raise HTTPException(status_code=404, detail="Gallery thumbnail not found.")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Gallery thumbnail file is missing.")
    return FileResponse(path, media_type="image/webp")


@router.delete("/gallery/{item_id}")
async def delete_gallery_item(item_id: str, gallery_store: GalleryStoreDep):
    try:
        deleted = gallery_store.delete_item(item_id)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Gallery item not found.")
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
