from fastapi import APIRouter, Response

from app.api.deps import EngineServiceDep
from app.core.session import backend_session_payload

router = APIRouter()


@router.get("/runtime")
async def runtime_status(engine_service: EngineServiceDep, response: Response):
    _set_dynamic_response_headers(response)
    status = await engine_service.runtime_status()
    payload = status.model_dump(mode="json") if hasattr(status, "model_dump") else dict(status)
    return {**payload, **backend_session_payload()}


@router.get("/resources")
async def resource_snapshot(engine_service: EngineServiceDep, response: Response):
    _set_dynamic_response_headers(response)
    return engine_service.resource_snapshot()


def _set_dynamic_response_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
