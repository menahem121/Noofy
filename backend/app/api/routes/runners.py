from fastapi import APIRouter

from app.api.deps import EngineServiceDep

router = APIRouter()


@router.get("/runners")
async def list_runners(engine_service: EngineServiceDep):
    return [descriptor.model_dump() for descriptor in engine_service.list_runners()]


@router.get("/memory-governor/metrics")
async def memory_governor_metrics(engine_service: EngineServiceDep):
    return {"metrics": engine_service.memory_governor_metrics()}
