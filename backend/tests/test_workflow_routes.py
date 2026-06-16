from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import workflows as workflow_routes


class BlockingLibrary:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def workflow_details(self, workflow_id: str) -> dict[str, object]:
        self.calls.append(("details", workflow_id))
        return {"workflow_id": workflow_id}

    def model_availability_summary(self, workflow_id: str) -> dict[str, object]:
        self.calls.append(("model_summary", workflow_id))
        return {"workflow_id": workflow_id, "ready_to_run": True}


@pytest.mark.anyio
async def test_workflow_detail_and_model_summary_routes_offload_blocking_library_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = BlockingLibrary()
    offloaded: list[str] = []

    async def fake_to_thread(func, *args, **kwargs):
        offloaded.append(func.__name__)
        return func(*args, **kwargs)

    monkeypatch.setattr(workflow_routes.asyncio, "to_thread", fake_to_thread)

    details = await workflow_routes.get_workflow_details("workflow-1", library)  # type: ignore[arg-type]
    summary = await workflow_routes.get_workflow_model_summary("workflow-1", library)  # type: ignore[arg-type]

    assert details == {"workflow_id": "workflow-1"}
    assert summary == {"workflow_id": "workflow-1", "ready_to_run": True}
    assert offloaded == ["workflow_details", "model_availability_summary"]
    assert library.calls == [
        ("details", "workflow-1"),
        ("model_summary", "workflow-1"),
    ]


@pytest.mark.anyio
async def test_workflow_detail_route_preserves_missing_workflow_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingLibrary:
        def workflow_details(self, workflow_id: str) -> dict[str, object]:
            raise KeyError(workflow_id)

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(workflow_routes.asyncio, "to_thread", fake_to_thread)

    with pytest.raises(HTTPException) as exc_info:
        await workflow_routes.get_workflow_details("missing", MissingLibrary())  # type: ignore[arg-type]

    assert exc_info.value.status_code == 404
