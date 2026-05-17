from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.api.deps import (
    CivitaiLoraServiceDep,
)
from app.models.civitai_loras import (
    CivitaiLoraDownloadRequest,
    CivitaiLoraError,
    CivitaiLoraSearchRequest,
)

router = APIRouter()

CIVITAI_PREVIEW_HOST = "image.civitai.com"
MAX_CIVITAI_PREVIEW_BYTES = 12 * 1024 * 1024
MAX_CIVITAI_PREVIEW_REDIRECTS = 3


@router.post("/model-sources/civitai/search-loras")
async def search_civitai_loras(
    request: CivitaiLoraSearchRequest,
    service: CivitaiLoraServiceDep,
):
    try:
        return await service.search_loras(request)
    except CivitaiLoraError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.post("/model-sources/civitai/download")
async def download_civitai_lora(
    request: CivitaiLoraDownloadRequest,
    service: CivitaiLoraServiceDep,
):
    try:
        return await service.start_download(request)
    except CivitaiLoraError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.get("/model-sources/civitai/preview")
async def proxy_civitai_preview(url: str):
    _validate_civitai_preview_url(url)
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=20) as client:
            return await _fetch_civitai_preview(client, url)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail={"code": "preview_unavailable", "message": "CivitAI preview image is unavailable."}) from exc


def _validate_civitai_preview_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise HTTPException(status_code=400, detail={"code": "invalid_preview_url", "message": "Invalid CivitAI preview URL."})
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail={"code": "invalid_preview_url", "message": "Invalid CivitAI preview URL."})
    try:
        port = parsed.port
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "invalid_preview_url", "message": "Invalid CivitAI preview URL."}) from None
    if port not in (None, 443):
        raise HTTPException(status_code=400, detail={"code": "invalid_preview_url", "message": "Invalid CivitAI preview URL."})
    if parsed.hostname.casefold() != CIVITAI_PREVIEW_HOST:
        raise HTTPException(status_code=400, detail={"code": "invalid_preview_url", "message": "Invalid CivitAI preview URL."})


async def _fetch_civitai_preview(client: httpx.AsyncClient, url: str) -> Response:
    current_url = url
    for _ in range(MAX_CIVITAI_PREVIEW_REDIRECTS + 1):
        _validate_civitai_preview_url(current_url)
        async with client.stream("GET", current_url) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise HTTPException(status_code=502, detail={"code": "preview_unavailable", "message": "CivitAI preview image is unavailable."})
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "image/jpeg")
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail={"code": "invalid_preview_type", "message": "CivitAI preview is not an image."})
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_CIVITAI_PREVIEW_BYTES:
                        raise HTTPException(status_code=413, detail={"code": "preview_too_large", "message": "CivitAI preview image is too large."})
                except ValueError:
                    pass
            chunks: list[bytes] = []
            total_bytes = 0
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if total_bytes > MAX_CIVITAI_PREVIEW_BYTES:
                    raise HTTPException(status_code=413, detail={"code": "preview_too_large", "message": "CivitAI preview image is too large."})
                chunks.append(chunk)
            return Response(content=b"".join(chunks), media_type=content_type, headers={"Cache-Control": "public, max-age=300"})
    raise HTTPException(status_code=502, detail={"code": "preview_unavailable", "message": "CivitAI preview image is unavailable."})
