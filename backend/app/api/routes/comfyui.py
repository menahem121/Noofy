from fastapi import APIRouter

from app.api.deps import ComfyUISidecarServiceDep
from app.api.schemas import ComfyUILaunchSettings, ComfyUIRebuildRequest, ComfyUIUpdateRequest

router = APIRouter()


@router.get("/engine/comfyui/status")
async def comfyui_status(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.runtime_status()


@router.get("/engine/comfyui/launch-settings")
async def comfyui_launch_settings(sidecar_service: ComfyUISidecarServiceDep):
    return sidecar_service.comfyui_launch_settings()


@router.put("/engine/comfyui/launch-settings")
async def update_comfyui_launch_settings(
    request: ComfyUILaunchSettings,
    sidecar_service: ComfyUISidecarServiceDep,
):
    return await sidecar_service.update_comfyui_launch_settings(request)


@router.post("/engine/comfyui/start")
async def start_comfyui(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.start_comfyui()


@router.post("/engine/comfyui/bootstrap")
async def bootstrap_comfyui_runtime(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.bootstrap_comfyui_runtime()


@router.post("/engine/comfyui/stop")
async def stop_comfyui(sidecar_service: ComfyUISidecarServiceDep):
    return await sidecar_service.stop_comfyui()


@router.get("/engine/comfyui/versions")
async def comfyui_versions(
    sidecar_service: ComfyUISidecarServiceDep,
    check_upstream: bool = False,
):
    return await sidecar_service.comfyui_versions(check_upstream=check_upstream)


@router.post("/engine/comfyui/update")
async def update_comfyui(
    request: ComfyUIUpdateRequest,
    sidecar_service: ComfyUISidecarServiceDep,
):
    return await sidecar_service.update_comfyui(request)


@router.post("/engine/comfyui/rebuild")
async def rebuild_comfyui(
    request: ComfyUIRebuildRequest,
    sidecar_service: ComfyUISidecarServiceDep,
):
    return await sidecar_service.rebuild_comfyui(request)


@router.get("/engine/comfyui/update/status")
async def comfyui_update_status(sidecar_service: ComfyUISidecarServiceDep):
    return sidecar_service.comfyui_update_status()
