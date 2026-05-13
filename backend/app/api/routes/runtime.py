from fastapi import APIRouter

from app.api.deps import EngineServiceDep

router = APIRouter()


@router.get("/runtime")
async def runtime_status(engine_service: EngineServiceDep):
    return await engine_service.runtime_status()


@router.get("/resources")
async def resource_snapshot(engine_service: EngineServiceDep):
    return engine_service.resource_snapshot()
