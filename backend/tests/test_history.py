from __future__ import annotations

import json
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.composition import ApiServices
from app.engine.models import JobResult
from app.gallery import GalleryInputSnapshot, RunSubmissionSnapshot
from app.history import ActivityEventCreate, ActivityLogStore, HistoryQuery, HistoryService
from app.main import create_app
from app.runs.result_service import RunResultService
from app.workflows.library import WorkflowLibraryStore
from app.workflows.loader import WorkflowPackageLoader


class FakeEngineService:
    async def shutdown(self) -> None:
        return None


class FakeAdapter:
    async def get_result(self, job_id: str) -> JobResult:
        return JobResult(job_id=job_id, status="completed")


class FakeJobService:
    def adapter_for_job(self, job_id: str) -> FakeAdapter:
        return FakeAdapter()


def _record_event(
    store: ActivityLogStore,
    *,
    event_id: str,
    created_at: datetime,
    type: str = "run",
    status: str = "completed",
    title: str = "Workflow run completed",
    workflow_id: str = "wf",
    workflow_name: str = "Workflow",
) -> None:
    store.record_event(
        ActivityEventCreate(
            type=type,
            status=status,
            title=title,
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            created_at=created_at,
            source_event_id=event_id,
        )
    )


def test_activity_log_store_filters_paginates_retains_and_loads_detail(tmp_path: Path) -> None:
    store = ActivityLogStore(tmp_path / "history.db", max_events=3)
    base = datetime(2026, 5, 13, tzinfo=UTC)
    _record_event(store, event_id="evt-1", created_at=base, workflow_name="Alpha")
    _record_event(store, event_id="evt-2", created_at=base + timedelta(minutes=1), workflow_name="Beta")
    _record_event(
        store,
        event_id="evt-3",
        created_at=base + timedelta(minutes=2),
        type="workflow_imported",
        status="installed",
        title="Workflow imported",
        workflow_name="Gamma",
    )
    _record_event(store, event_id="evt-4", created_at=base + timedelta(minutes=3), workflow_name="Delta")

    first = store.list_events(HistoryQuery(limit=2, sort="newest"))
    assert first.total == 3
    assert [event.id for event in first.events] == ["evt-4", "evt-3"]
    assert first.has_more is True

    second = store.list_events(HistoryQuery(limit=2, cursor=first.next_cursor, sort="newest"))
    assert [event.id for event in second.events] == ["evt-2"]
    assert store.get_event("evt-1") is None

    imported = store.list_events(HistoryQuery(type="workflow_imported"))
    assert [event.workflow_name for event in imported.events] == ["Gamma"]

    old_event = store.record_event(
        ActivityEventCreate(
            type="run",
            status="completed",
            title="Old run",
            workflow_id="wf",
            workflow_name="Old Workflow",
            created_at=base - timedelta(days=1),
            source_event_id="evt-old",
        )
    )
    assert old_event.id == "evt-old"
    assert store.get_event("evt-old") is None


def test_history_service_imports_existing_run_summaries_idempotently(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    package_dir = packages_dir / "text_to_image"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": "text_to_image", "name": "Text to Image", "version": "1.0.0"},
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": {},
                "inputs": [],
                "outputs": [],
                "custom_nodes": [],
                "unresolved_runtime_inputs": [],
            }
        ),
        encoding="utf-8",
    )
    library = WorkflowLibraryStore(tmp_path / "library")
    started = datetime(2026, 5, 13, 10, 0, tzinfo=UTC)
    library.record_run_result(
        workflow_id="text_to_image",
        job_id="job-1",
        status="completed",
        started_at=started,
        finished_at=started + timedelta(seconds=4),
    )
    service = HistoryService(
        store=ActivityLogStore(tmp_path / "history.db"),
        workflow_library_store=library,
        workflow_loader=WorkflowPackageLoader(packages_dir),
    )

    first = service.list_events(HistoryQuery())
    second = service.list_events(HistoryQuery())

    assert first.total == 1
    assert second.total == 1
    assert first.events[0].workflow_name == "Text to Image"
    assert first.events[0].can_open_workflow is True


def test_history_api_splits_summary_and_detail_payloads(tmp_path: Path) -> None:
    history = HistoryService(store=ActivityLogStore(tmp_path / "history.db"))
    history.store.record_event(
        ActivityEventCreate(
            type="run",
            status="completed",
            title="Workflow run completed",
            workflow_id="wf",
            workflow_name="Prompt Workflow",
            created_at=datetime(2026, 5, 13, tzinfo=UTC),
            prompt="A studio portrait",
            used_settings={"Prompt": "A studio portrait", "Steps": 20},
            source_event_id="run:job-1",
        )
    )
    services = ApiServices(
        engine_service=FakeEngineService(),
        comfyui_sidecar_service=None,
        user_state_service=None,
        asset_service=None,
        gallery_store=None,
        api_key_service=None,
        onboarding_service=None,
        model_folder_service=None,
        model_tag_store=None,
        model_ownership_store=None,
        model_inventory_service=None,
        model_download_service=None,
        workflow_library_service=None,
        dashboard_authoring_service=None,
        workflow_exporter=None,
        workflow_import_orchestrator=None,
        workflow_runner_lifecycle_service=None,
        run_job_service=None,
        run_orchestrator=None,
        run_result_service=None,
        history_service=history,
    )
    client = TestClient(create_app(services=services))

    summary = client.get("/api/history").json()
    event = summary["events"][0]

    assert summary["total"] == 1
    assert "prompt" not in event
    assert "used_settings" not in event

    detail = client.get(f"/api/history/{event['id']}").json()
    assert detail["prompt"] == "A studio portrait"
    assert detail["used_settings"]["Steps"] == 20

    invalid = client.get("/api/history?type=raw_comfyui")
    assert invalid.status_code == 422


def test_run_result_service_records_run_activity_with_safe_dashboard_settings(tmp_path: Path) -> None:
    library = WorkflowLibraryStore(tmp_path / "library")
    history = HistoryService(store=ActivityLogStore(tmp_path / "history.db"))
    started = datetime(2026, 5, 13, 9, 59, tzinfo=UTC)
    snapshot = RunSubmissionSnapshot(
        workflow_id="wf",
        workflow_title="Prompt Workflow",
        dashboard_version="1.0",
        values={"prompt": "A studio portrait"},
        inputs=[
            GalleryInputSnapshot(
                input_id="prompt",
                label="Prompt",
                control_type="text",
                value="A studio portrait",
            )
        ],
    )
    service = RunResultService(
        job_service=FakeJobService(),
        log_store=None,
        job_workflows={"job-1": "wf"},
        job_started_at={"job-1": started},
        job_run_snapshots={"job-1": snapshot},
        finish_memory_sampling=lambda job_id: _async_none(),
        record_memory_observation=lambda result: None,
        maybe_retry_after_memory_cleanup=lambda result: _async_none(),
        workflow_library_store=library,
        history_service=history,
    )

    asyncio.run(service.get_result("job-1"))

    summary = history.list_events(HistoryQuery())
    detail = history.get_event(summary.events[0].id)
    assert summary.total == 1
    assert summary.events[0].workflow_name == "Prompt Workflow"
    assert detail is not None
    assert detail.prompt == "A studio portrait"
    assert detail.used_settings["Prompt"] == "A studio portrait"
    assert library.run_history_summary("wf").run_count == 1


async def _async_none(*args, **kwargs):
    return None
