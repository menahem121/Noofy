import os
from pathlib import Path

from fastapi import APIRouter

from app.api.deps import EngineServiceDep, ModelFolderServiceDep
from app.core.config import settings

router = APIRouter()


@router.get("/paths")
async def resolved_paths(model_folder_service: ModelFolderServiceDep):
    entries = settings.paths.writable_status()
    model_folder_settings = model_folder_service.settings(ensure_folders=False)
    active_models_dir = Path(model_folder_settings.noofy_models_dir)
    entries["models_dir"] = {
        "path": str(active_models_dir),
        "exists": active_models_dir.exists(),
        "writable": os.access(active_models_dir, os.W_OK) if active_models_dir.exists() else False,
    }
    return entries


@router.get("/health")
async def health(engine_service: EngineServiceDep):
    return await engine_service.health()


@router.get("/logs")
async def list_logs(
    engine_service: EngineServiceDep,
    level: str | None = None,
    limit: int = 200,
):
    return engine_service.list_logs(level=level, limit=limit)
