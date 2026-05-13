from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import HistoryServiceDep
from app.history import HistoryEventStatus, HistoryEventType, HistoryQuery

router = APIRouter()


@router.get("/history")
async def list_history(
    history: HistoryServiceDep,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    type: HistoryEventType | None = None,
    status: HistoryEventStatus | None = None,
    workflow_id: str | None = None,
    q: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    sort: Literal["newest", "oldest"] = "newest",
):
    query = HistoryQuery(
        limit=limit,
        cursor=cursor,
        type=type,
        status=status,
        workflow_id=workflow_id,
        q=q,
        created_after=created_after.isoformat() if created_after else None,
        created_before=created_before.isoformat() if created_before else None,
        sort=sort,
    )
    return history.list_events(query)


@router.get("/history/{event_id}")
async def get_history_event(event_id: str, history: HistoryServiceDep):
    event = history.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="History event not found.")
    return event
