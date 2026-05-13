from fastapi import APIRouter

from app.api.deps import EngineServiceDep

router = APIRouter()


@router.get("/diagnostics")
async def diagnostics(
    engine_service: EngineServiceDep,
    workflow_id: str | None = None,
    developer_details: bool = False,
    limit: int = 200,
):
    return engine_service.diagnostics_payload(
        workflow_id=workflow_id,
        include_developer_details=developer_details,
        limit=limit,
    )


@router.get("/storage/diagnostics")
async def storage_diagnostics(engine_service: EngineServiceDep):
    return engine_service.storage_diagnostics_payload()
