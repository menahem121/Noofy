from fastapi import APIRouter, Response

from app.api.deps import EngineServiceDep

router = APIRouter()


@router.get("/runtime")
async def runtime_status(engine_service: EngineServiceDep, response: Response):
    _set_dynamic_response_headers(response)
    return await engine_service.runtime_status()


@router.get("/resources")
async def resource_snapshot(engine_service: EngineServiceDep, response: Response):
    _set_dynamic_response_headers(response)
    return engine_service.resource_snapshot()


def _set_dynamic_response_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
