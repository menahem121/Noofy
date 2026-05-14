import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.engine import service as service_module
from app.diagnostics import LogStore
from app.engine.models import RequiredModelSummary
from app.engine.service import EngineService, IMPORT_SESSION_TTL, ImportSessionExpiredError
from app.main import create_app
from app.runtime.runners.supervisor import RunnerSupervisor
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import RequiredModel, WorkflowMetadata, WorkflowPackage
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class FakePackageStore:
    def __init__(self, package: WorkflowPackage) -> None:
        self.package = package
        self.preview_count = 0
        self.import_count = 0

    def preview_archive(self, data: bytes, **kwargs):
        self.preview_count += 1
        return self.package

    def import_archive(self, data: bytes, **kwargs):
        self.import_count += 1
        return self.package


class FakeAvailabilityService:
    def cleanup_interrupted_downloads(self) -> int:
        return 0

    def summarize(self, package: WorkflowPackage) -> RequiredModelSummary:
        return RequiredModelSummary(
            workflow_id=package.metadata.id,
            total_count=1,
            available_count=0,
            possible_match_count=0,
            missing_count=1,
            needs_manual_download_count=0,
            ready_to_run=False,
            models=[],
        )

    async def download_missing(self, package: WorkflowPackage, **kwargs):
        raise AssertionError("download should not run in this test")


class FakeImportService:
    def __init__(self) -> None:
        self.imported_payload: bytes | None = None
        self.imported_filename: str | None = None
        self.previewed_payload: bytes | None = None
        self.previewed_filename: str | None = None
        self.allow_unverified_community_preparation = False
        self.pending_sessions: set[str] = {"import-session-1"}
        self.shutdown_called = False

    def import_workflow_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ):
        self.imported_payload = data
        self.imported_filename = original_filename
        self.allow_unverified_community_preparation = allow_unverified_community_preparation
        return {
            "workflow_id": "unknown__eraserv4.5__0.1.0",
            "status": "needs_input_setup",
            "user_facing_message": "Needs input setup",
            "workflow": {
                "id": "unknown__eraserv4.5__0.1.0",
                "name": "EraserV4.5",
                "version": "0.1.0",
                "description": "",
                "publisher_id": "unknown",
                "package_id": "eraserv4.5",
                "trust_level": "quarantined_community",
            },
            "required_model_count": 2,
            "custom_node_count": 5,
            "unresolved_input_count": 1,
        }

    def preview_workflow_import(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ):
        self.previewed_payload = data
        self.previewed_filename = original_filename
        self.allow_unverified_community_preparation = allow_unverified_community_preparation
        return {
            "import_session_id": "import-session-1",
            "workflow_id": "unknown__model_workflow__0.1.0",
            "status": "imported",
            "user_facing_message": "Imported",
            "workflow": {
                "id": "unknown__model_workflow__0.1.0",
                "name": "Model workflow",
                "version": "0.1.0",
                "description": "",
                "publisher_id": "unknown",
                "package_id": "model_workflow",
                "trust_level": "quarantined_community",
            },
            "required_model_count": 1,
            "custom_node_count": 0,
            "unresolved_input_count": 0,
            "model_summary": {
                "workflow_id": "unknown__model_workflow__0.1.0",
                "total_count": 1,
                "available_count": 0,
                "possible_match_count": 0,
                "missing_count": 1,
                "needs_manual_download_count": 0,
                "ready_to_run": False,
                "models": [
                    {
                        "requirement_id": "checkpoints/model.safetensors",
                        "filename": "model.safetensors",
                        "model_type": "checkpoint",
                        "folder": "checkpoints",
                        "verification_level": "sha256_size",
                        "size_bytes": 12,
                        "source_urls": ["https://huggingface.co/example/model.safetensors"],
                        "source_availability": "known",
                        "status": "missing",
                        "status_label": "Missing",
                        "asset_ownership": "external_reference",
                        "node_id": None,
                        "node_type": None,
                        "input_name": None,
                        "source_path": None,
                        "matched_root": None,
                        "matched_sha256": None,
                        "matched_size_bytes": None,
                        "message": None,
                    }
                ],
            },
        }

    def start_missing_model_download_for_import(self, import_session_id: str):
        if import_session_id not in self.pending_sessions:
            raise KeyError(import_session_id)
        return {
            "job_id": "download-job-1",
            "import_session_id": import_session_id,
            "workflow_id": "unknown__model_workflow__0.1.0",
            "status": "queued",
            "user_facing_message": "Model download is queued.",
        }

    def import_model_download_status(self, import_session_id: str, job_id: str):
        if import_session_id not in self.pending_sessions or job_id != "download-job-1":
            raise KeyError(job_id)
        return {
            "job_id": job_id,
            "import_session_id": import_session_id,
            "workflow_id": "unknown__model_workflow__0.1.0",
            "status": "completed",
            "user_facing_message": "Model download check finished.",
            "current_model_filename": "model.safetensors",
            "current_model_index": 1,
            "total_models": 1,
            "bytes_downloaded": 12,
            "total_bytes": 12,
            "percent": 100,
            "speed_bytes_per_second": 1000,
            "models": [
                {
                    "requirement_id": "checkpoints/model.safetensors",
                    "filename": "model.safetensors",
                    "status": "completed",
                    "status_label": "Completed",
                    "bytes_downloaded": 12,
                    "total_bytes": 12,
                    "message": None,
                }
            ],
            "model_summary": {
                "workflow_id": "unknown__model_workflow__0.1.0",
                "total_count": 1,
                "available_count": 1,
                "possible_match_count": 0,
                "missing_count": 0,
                "needs_manual_download_count": 0,
                "ready_to_run": True,
                "models": [],
            },
        }

    def cancel_import_model_download_job(self, import_session_id: str, job_id: str):
        status = self.import_model_download_status(import_session_id, job_id)
        status["status"] = "canceled"
        status["user_facing_message"] = "Model download was canceled."
        return status

    def commit_workflow_import(self, import_session_id: str):
        if import_session_id not in self.pending_sessions:
            raise KeyError(import_session_id)
        self.pending_sessions.remove(import_session_id)
        return {
            "import_session_id": None,
            "workflow_id": "unknown__model_workflow__0.1.0",
            "status": "imported",
            "user_facing_message": "Imported",
            "workflow": {
                "id": "unknown__model_workflow__0.1.0",
                "name": "Model workflow",
                "version": "0.1.0",
                "description": "",
                "publisher_id": "unknown",
                "package_id": "model_workflow",
                "trust_level": "quarantined_community",
            },
            "required_model_count": 1,
            "custom_node_count": 0,
            "unresolved_input_count": 0,
            "model_summary": None,
        }

    def cancel_workflow_import(self, import_session_id: str):
        existed = import_session_id in self.pending_sessions
        self.pending_sessions.discard(import_session_id)
        return {
            "import_session_id": import_session_id,
            "status": "canceled" if existed else "not_found",
        }

    def get_workflow_package(self, workflow_id: str):
        if workflow_id == "missing":
            raise KeyError(workflow_id)
        return {
            "metadata": {"id": workflow_id, "name": "Imported", "version": "0.1.0"},
            "custom_nodes": [],
            "required_models": [],
        }

    def trust_policy_payload(self):
        return {
            "schema_version": "0.1.0",
            "signature_payload_schema_version": "0.1.0",
            "development_hmac_allowed": False,
            "trusted_key_count": 1,
            "trusted_keys": [
                {
                    "key_id": "registry-test-key",
                    "algorithm": "ed25519",
                    "purpose": "registry",
                    "revoked": False,
                    "not_before": None,
                    "expires_at": None,
                    "policy_versions": ["phase6-local-0.1"],
                }
            ],
            "trust_levels": {},
            "imported_trusted_claims_require_verified_evidence": True,
            "secrets_exposed": False,
        }

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _staged_import_engine_service(tmp_path) -> EngineService:
    package = WorkflowPackage(
        metadata=WorkflowMetadata(
            id="ttl_workflow",
            name="TTL Workflow",
            version="0.1.0",
        ),
        engine="comfyui",
        required_models=[
            RequiredModel(
                folder="checkpoints",
                filename="model.safetensors",
                size_bytes=12,
                verification_level="filename_size",
            )
        ],
        comfyui_graph={},
    )
    return EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        imported_package_store=FakePackageStore(package),
        model_availability_service=FakeAvailabilityService(),
    )


def test_import_workflow_endpoint_passes_archive_bytes_to_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post(
            "/api/workflows/import?filename=test.noofy",
            content=b"archive-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_input_setup"
    assert response.json()["workflow"]["trust_level"] == "quarantined_community"
    assert fake_service.imported_payload == b"archive-bytes"
    assert fake_service.imported_filename == "test.noofy"
    assert fake_service.allow_unverified_community_preparation is False


def test_import_workflow_endpoint_passes_community_preparation_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post(
            "/api/workflows/import?filename=test.noofy&allow_unverified_community_preparation=true",
            content=b"archive-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 200
    assert fake_service.allow_unverified_community_preparation is True


def test_preview_workflow_import_endpoint_returns_staged_model_summary(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post(
            "/api/workflows/import/preview?filename=model.noofy",
            content=b"archive-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["import_session_id"] == "import-session-1"
    assert payload["model_summary"]["models"][0]["status"] == "missing"
    assert fake_service.previewed_payload == b"archive-bytes"
    assert fake_service.previewed_filename == "model.noofy"


def test_pending_import_session_expires_after_ttl(tmp_path) -> None:
    service = _staged_import_engine_service(tmp_path)
    preview = service.preview_workflow_import(b"archive")
    session_id = preview.import_session_id
    assert session_id is not None
    service._pending_workflow_imports[session_id].updated_at = (
        datetime.now(UTC) - IMPORT_SESSION_TTL - timedelta(seconds=1)
    )

    with pytest.raises(ImportSessionExpiredError):
        service.commit_workflow_import(session_id)
    assert session_id not in service._pending_workflow_imports


def test_staged_import_commit_reuses_previewed_package_for_model_summary(tmp_path) -> None:
    service = _staged_import_engine_service(tmp_path)
    package_store = service.workflow_import_orchestrator.imported_package_store
    preview = service.preview_workflow_import(b"archive")
    session_id = preview.import_session_id
    assert session_id is not None

    service.commit_workflow_import(session_id)

    assert package_store.preview_count == 1
    assert package_store.import_count == 1


def test_pending_import_session_stays_alive_during_active_download(tmp_path) -> None:
    service = _staged_import_engine_service(tmp_path)
    preview = service.preview_workflow_import(b"archive")
    session_id = preview.import_session_id
    assert session_id is not None
    pending = service._pending_workflow_imports[session_id]
    pending.updated_at = datetime.now(UTC) - IMPORT_SESSION_TTL - timedelta(seconds=1)
    pending.active_download_job_id = "active-job"
    service._import_model_download_jobs["active-job"] = service_module._ImportModelDownloadJob(
        job_id="active-job",
        import_session_id=session_id,
        workflow_id="ttl_workflow",
        cancel_event=asyncio.Event(),
        task=None,
        status="running",
        user_facing_message="Downloading required models...",
        started_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        total_models=1,
    )

    assert service._pending_import_or_raise(session_id) is pending
    assert session_id in service._pending_workflow_imports


def test_staged_import_download_commit_and_cancel_endpoints(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        download = client.post("/api/workflows/import/import-session-1/download-models")
        status = client.get("/api/workflows/import/import-session-1/download-models/download-job-1")
        canceled = client.post("/api/workflows/import/import-session-1/download-models/download-job-1/cancel")
        commit = client.post("/api/workflows/import/import-session-1/commit")
        cancel = client.delete("/api/workflows/import/import-session-1")

    assert download.status_code == 200
    assert download.json()["job_id"] == "download-job-1"
    assert status.status_code == 200
    assert status.json()["percent"] == 100
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert commit.status_code == 200
    assert commit.json()["workflow_id"] == "unknown__model_workflow__0.1.0"
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "not_found"


def test_get_workflow_package_endpoint_returns_normalized_record(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeImportService())) as client:
        response = client.get("/api/workflows/unknown__eraserv4.5__0.1.0/package")

    assert response.status_code == 200
    assert response.json()["metadata"]["id"] == "unknown__eraserv4.5__0.1.0"


def test_get_workflow_package_endpoint_returns_404_for_unknown_workflow(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeImportService())) as client:
        response = client.get("/api/workflows/missing/package")

    assert response.status_code == 404


def test_trust_policy_endpoint_returns_public_key_metadata_only(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeImportService())) as client:
        response = client.get("/api/trust/policy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trusted_key_count"] == 1
    assert payload["trusted_keys"][0] == {
        "key_id": "registry-test-key",
        "algorithm": "ed25519",
        "purpose": "registry",
        "revoked": False,
        "not_before": None,
        "expires_at": None,
        "policy_versions": ["phase6-local-0.1"],
    }
    assert payload["secrets_exposed"] is False
    assert "secret" not in str(payload["trusted_keys"]).casefold()
    assert "local-secret" not in str(payload)
