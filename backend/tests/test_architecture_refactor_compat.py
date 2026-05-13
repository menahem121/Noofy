from pathlib import Path

from fastapi.testclient import TestClient

from app.composition import create_api_services
from app.diagnostics import LogStore
from app.main import create_app
from app.models.ownership import ModelOwnershipStore
from app.workflows.import_orchestrator import WorkflowImportOrchestrator
from app.workflows.package import RequiredModel, WorkflowMetadata, WorkflowPackage


def test_comfyui_updates_shim_reexports_private_test_helpers():
    from app.runtime.comfyui.comfyui_updates import (
        _archive_recovery_candidates,
        _extract_github_zip,
        _required_route_status_usable,
        _start_failure_is_repairable,
    )

    assert callable(_archive_recovery_candidates)
    assert callable(_extract_github_zip)
    assert callable(_required_route_status_usable)
    assert callable(_start_failure_is_repairable)


def test_run_routes_use_run_services_before_engine_facade(monkeypatch):
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    class FakeRunJobService:
        def list_job_logs(self, job_id: str, *, level=None, limit: int = 200):
            return {"job_id": job_id, "events": [], "limit": limit, "level": level}

        async def fetch_output(self, job_id: str, filename: str, subfolder: str, output_type: str):
            assert job_id == "job-1"
            assert filename == "result.png"
            assert subfolder == "preview"
            assert output_type == "output"
            return b"image-bytes", "image/png"

    class FakeRunResultService:
        async def get_result(self, job_id: str):
            return {"job_id": job_id, "status": "completed"}

        async def stream_progress_events(self, job_id: str):
            yield f'event: result\ndata: {{"job_id":"{job_id}","status":"completed"}}\n\n'

    class FakeEngineService:
        gallery_capture_service = None
        run_job_service = FakeRunJobService()
        run_result_service = FakeRunResultService()

        async def shutdown(self):
            return None

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        result = client.get("/api/jobs/job-1/result")
        output = client.get(
            "/api/jobs/job-1/outputs/view",
            params={"filename": "result.png", "subfolder": "preview", "type": "output"},
        )
        logs = client.get("/api/jobs/job-1/logs", params={"level": "info", "limit": 5})

    assert result.status_code == 200
    assert result.json() == {"job_id": "job-1", "status": "completed"}
    assert output.status_code == 200
    assert output.content == b"image-bytes"
    assert output.headers["content-type"] == "image/png"
    assert logs.status_code == 200
    assert logs.json() == {"job_id": "job-1", "events": [], "limit": 5, "level": "info"}


def test_workflow_run_routes_use_orchestrator_before_engine_facade(monkeypatch):
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    class FakeRunOrchestrator:
        async def validate_workflow(self, workflow_id: str):
            return {"workflow_id": workflow_id, "valid": True, "errors": []}

        async def run_workflow(
            self,
            workflow_id: str,
            inputs: dict,
            options: dict,
            *,
            output_preferences_snapshot=None,
        ):
            return {
                "job_id": "job-1",
                "workflow_id": workflow_id,
                "engine": "noofy",
                "status": "queued",
                "inputs": inputs,
                "options": options,
                "output_preferences_snapshot": output_preferences_snapshot,
            }

    class FakeEngineService:
        gallery_capture_service = None
        run_orchestrator = FakeRunOrchestrator()

        async def shutdown(self):
            return None

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        validation = client.post("/api/workflows/workflow-1/validate")
        run = client.post(
            "/api/workflows/workflow-1/run",
            json={
                "inputs": {"prompt": "hello"},
                "options": {"seed": 1},
                "output_preferences_snapshot": {"image": {"enabled": True}},
            },
        )

    assert validation.status_code == 200
    assert validation.json() == {"workflow_id": "workflow-1", "valid": True, "errors": []}
    assert run.status_code == 200
    assert run.json() == {
        "job_id": "job-1",
        "workflow_id": "workflow-1",
        "engine": "noofy",
        "status": "queued",
        "inputs": {"prompt": "hello"},
        "options": {"seed": 1},
        "output_preferences_snapshot": {"image": {"enabled": True}},
    }


def test_workflow_import_routes_use_import_orchestrator_before_engine_facade(
    monkeypatch,
):
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    class FakeImportOrchestrator:
        def import_workflow_archive(
            self,
            data: bytes,
            *,
            original_filename=None,
            allow_unverified_community_preparation: bool = False,
        ):
            return {
                "route": "import",
                "bytes": len(data),
                "filename": original_filename,
                "allow": allow_unverified_community_preparation,
            }

        def preview_workflow_import(
            self,
            data: bytes,
            *,
            original_filename=None,
            allow_unverified_community_preparation: bool = False,
        ):
            return {
                "route": "preview",
                "bytes": len(data),
                "filename": original_filename,
                "allow": allow_unverified_community_preparation,
            }

        def start_missing_model_download_for_import(self, import_session_id: str):
            return {"route": "download", "import_session_id": import_session_id}

        def import_model_download_status(self, import_session_id: str, job_id: str):
            return {
                "route": "download_status",
                "import_session_id": import_session_id,
                "job_id": job_id,
            }

        def cancel_import_model_download_job(self, import_session_id: str, job_id: str):
            return {
                "route": "download_cancel",
                "import_session_id": import_session_id,
                "job_id": job_id,
            }

        def commit_workflow_import(self, import_session_id: str):
            return {"route": "commit", "import_session_id": import_session_id}

        def cancel_workflow_import(self, import_session_id: str):
            return {"route": "cancel", "import_session_id": import_session_id}

    class FakeEngineService:
        gallery_capture_service = None
        workflow_import_orchestrator = FakeImportOrchestrator()

        async def shutdown(self):
            return None

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        imported = client.post(
            "/api/workflows/import",
            params={
                "filename": "workflow.noofy",
                "allow_unverified_community_preparation": "true",
            },
            content=b"archive",
        )
        preview = client.post("/api/workflows/import/preview", content=b"archive")
        download = client.post("/api/workflows/import/session-1/download-models")
        status = client.get("/api/workflows/import/session-1/download-models/job-1")
        canceled_download = client.post(
            "/api/workflows/import/session-1/download-models/job-1/cancel"
        )
        committed = client.post("/api/workflows/import/session-1/commit")
        canceled = client.delete("/api/workflows/import/session-1")

    assert imported.status_code == 200
    assert imported.json() == {
        "route": "import",
        "bytes": 7,
        "filename": "workflow.noofy",
        "allow": True,
    }
    assert preview.status_code == 200
    assert preview.json() == {
        "route": "preview",
        "bytes": 7,
        "filename": None,
        "allow": False,
    }
    assert download.json() == {"route": "download", "import_session_id": "session-1"}
    assert status.json() == {
        "route": "download_status",
        "import_session_id": "session-1",
        "job_id": "job-1",
    }
    assert canceled_download.json() == {
        "route": "download_cancel",
        "import_session_id": "session-1",
        "job_id": "job-1",
    }
    assert committed.json() == {"route": "commit", "import_session_id": "session-1"}
    assert canceled.json() == {"route": "cancel", "import_session_id": "session-1"}


def test_workflow_runner_lifecycle_routes_use_lifecycle_service_before_engine_facade(
    monkeypatch,
):
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    class FakeRunnerLifecycle:
        def get_install_state(self, workflow_id: str):
            return {"workflow_id": workflow_id, "status": "ready"}

        def get_install_state_developer_details(self, workflow_id: str):
            return {"workflow_id": workflow_id, "developer_details": {"source": "lifecycle"}}

        async def prepare_workflow(self, workflow_id: str):
            return {"workflow_id": workflow_id, "status": "prepared"}

        async def start_workflow_runner(self, workflow_id: str):
            return {"workflow_id": workflow_id, "status": "running", "runner": None}

        async def stop_workflow_runner(self, workflow_id: str):
            return {"workflow_id": workflow_id, "status": "stopped", "runner": None}

        def cancel_queued_runner_start(self, queue_id: str):
            return {"queue_id": queue_id, "status": "canceled", "workflow_id": "workflow-1"}

        def open_workflow_runner_lease(self, workflow_id: str):
            return {
                "workflow_id": workflow_id,
                "status": "idle_warm",
                "lease_id": "lease-1",
                "runner": None,
            }

        def close_workflow_runner_lease(self, workflow_id: str, lease_id: str):
            return {
                "workflow_id": workflow_id,
                "status": "idle",
                "lease_id": lease_id,
                "runner": None,
            }

    class FakeEngineService:
        gallery_capture_service = None
        workflow_runner_lifecycle_service = FakeRunnerLifecycle()

        async def shutdown(self):
            return None

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        install = client.get("/api/workflows/workflow-1/install-state")
        details = client.get("/api/workflows/workflow-1/install-state/developer-details")
        prepared = client.post("/api/workflows/workflow-1/prepare")
        started = client.post("/api/workflows/workflow-1/runner/start")
        stopped = client.post("/api/workflows/workflow-1/runner/stop")
        canceled = client.delete("/api/workflows/runner/queue/queue-1")
        opened = client.post("/api/workflows/workflow-1/runner/leases")
        closed = client.delete("/api/workflows/workflow-1/runner/leases/lease-1")

    assert install.status_code == 200
    assert install.json() == {"workflow_id": "workflow-1", "status": "ready"}
    assert details.status_code == 200
    assert details.json() == {
        "workflow_id": "workflow-1",
        "developer_details": {"source": "lifecycle"},
    }
    assert prepared.status_code == 200
    assert prepared.json() == {"workflow_id": "workflow-1", "status": "prepared"}
    assert started.status_code == 200
    assert started.json() == {
        "workflow_id": "workflow-1",
        "status": "running",
        "runner": None,
    }
    assert stopped.status_code == 200
    assert stopped.json() == {
        "workflow_id": "workflow-1",
        "status": "stopped",
        "runner": None,
    }
    assert canceled.status_code == 200
    assert canceled.json() == {
        "queue_id": "queue-1",
        "status": "canceled",
        "workflow_id": "workflow-1",
    }
    assert opened.status_code == 200
    assert opened.json() == {
        "workflow_id": "workflow-1",
        "status": "idle_warm",
        "lease_id": "lease-1",
        "runner": None,
    }
    assert closed.status_code == 200
    assert closed.json() == {
        "workflow_id": "workflow-1",
        "status": "idle",
        "lease_id": "lease-1",
        "runner": None,
    }


def test_create_api_services_wires_ownership_store_into_import_orchestrator(
    tmp_path: Path,
):
    class FakeImportOrchestrator:
        model_ownership_store = None

    class FakeEngineService:
        gallery_capture_service = None
        workflow_import_orchestrator = FakeImportOrchestrator()

        async def shutdown(self):
            return None

    ownership = ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json")
    engine = FakeEngineService()

    services = create_api_services(
        engine_service=engine,
        model_ownership_store=ownership,
    )

    assert services.model_ownership_store is ownership
    assert engine.model_ownership_store is ownership
    assert engine.workflow_import_orchestrator.model_ownership_store is ownership
    assert services.workflow_import_orchestrator is engine.workflow_import_orchestrator


def test_import_orchestrator_marks_completed_import_downloads_as_downloaded(
    tmp_path: Path,
):
    model_root = tmp_path / "models"
    model_path = model_root / "checkpoints" / "downloaded.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"model")
    ownership = ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json")

    class FakeModelAvailability:
        noofy_models_dir = model_root

    orchestrator = WorkflowImportOrchestrator(
        imported_package_store=object(),  # type: ignore[arg-type]
        workflow_library_service=object(),  # type: ignore[arg-type]
        model_availability_service=FakeModelAvailability(),  # type: ignore[arg-type]
        log_store=LogStore(),
        model_ownership_store=ownership,
    )
    package = WorkflowPackage(
        metadata=WorkflowMetadata(id="workflow", name="Workflow", version="1.0.0"),
        engine="comfyui",
        comfyui_graph={},
        required_models=[
            RequiredModel(folder="checkpoints", filename="downloaded.safetensors")
        ],
    )

    orchestrator._mark_import_downloads_as_noofy_downloaded(package)

    assert ownership.origin_for_model("checkpoints/downloaded.safetensors") == "downloaded"
