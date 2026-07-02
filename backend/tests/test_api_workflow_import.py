import asyncio
import dataclasses
import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.diagnostics import LogStore
from app.engine.models import (
    ImportModelDownloadProgressItem,
    ModelDownloadSummary,
    RequiredModelSummary,
)
from app.engine.service import EngineService
from app.main import create_app
from app.models.ownership import ModelOwnershipStore
from app.runtime.runners.supervisor import RunnerSupervisor
from app.source_policy import SourcePolicy
from app.workflows import model_availability as availability_module
from app.workflows.import_orchestrator import (
    IMPORT_SESSION_TTL,
    ImportRequiresCustomNodeResolutionError,
    ImportSessionExpiredError,
    WorkflowImportOrchestrator,
    _ImportModelDownloadJob,
    _PendingWorkflowImport,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.model_availability import ModelAvailabilityService
from app.workflows.package import (
    RequiredModel,
    WorkflowCustomNodeRecord,
    WorkflowImportMetadata,
    WorkflowMetadata,
    WorkflowPackage,
)
from app.workflows.validator import WorkflowPackageValidator
from conftest import make_api_services


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class FakePackageStore:
    def __init__(self, package: WorkflowPackage) -> None:
        self.package = package
        self.preview_count = 0
        self.import_count = 0
        self.prepared_import_count = 0
        self.prepared_package: WorkflowPackage | None = None

    def preview_archive(self, data: bytes, **kwargs):
        self.preview_count += 1
        return self.package

    def import_archive(self, data: bytes, **kwargs):
        self.import_count += 1
        return self.package

    def import_prepared_archive(self, data: bytes, *, package: WorkflowPackage, **kwargs):
        self.prepared_import_count += 1
        self.prepared_package = package
        return self.package


class FakeAvailabilityService:
    def __init__(self) -> None:
        self.summarize_calls: list[dict[str, object]] = []

    def cleanup_interrupted_downloads(self) -> int:
        return 0

    def summarize(self, package: WorkflowPackage, **kwargs) -> RequiredModelSummary:
        self.summarize_calls.append(dict(kwargs))
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
        self.workflow_import_orchestrator = self
        self.workflow_library_service = self
        self.imported_payload: bytes | None = None
        self.imported_filename: str | None = None
        self.previewed_payload: bytes | None = None
        self.previewed_filename: str | None = None
        self.allow_unverified_community_preparation = False
        self.committed_duplicate_action: str | None = None
        self.custom_node_urls: dict[str, str] | None = None
        self.approved_candidate_id: str | None = None
        self.no_custom_nodes_session_id: str | None = None
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

    def resolve_import_custom_nodes_from_urls(
        self,
        import_session_id: str,
        *,
        urls_by_node_type: dict[str, str],
    ):
        if import_session_id not in self.pending_sessions:
            raise KeyError(import_session_id)
        self.custom_node_urls = urls_by_node_type
        return {
            "import_session_id": import_session_id,
            "workflow_id": "unknown__custom_node_workflow__0.1.0",
            "status": "needs_input_setup",
            "user_facing_message": "Needs input setup",
            "workflow": {
                "id": "unknown__custom_node_workflow__0.1.0",
                "name": "Custom node workflow",
                "version": "0.1.0",
                "description": "",
            },
            "required_model_count": 0,
            "custom_node_count": 1,
            "unresolved_input_count": 1,
            "model_summary": None,
            "custom_node_resolution": None,
        }

    def approve_import_custom_node_candidate(
        self,
        import_session_id: str,
        *,
        candidate_id: str,
    ):
        if import_session_id not in self.pending_sessions:
            raise KeyError(import_session_id)
        self.approved_candidate_id = candidate_id
        return {
            "import_session_id": import_session_id,
            "workflow_id": "unknown__custom_node_workflow__0.1.0",
            "status": "needs_input_setup",
            "user_facing_message": "Needs input setup",
            "workflow": {
                "id": "unknown__custom_node_workflow__0.1.0",
                "name": "Custom node workflow",
                "version": "0.1.0",
                "description": "",
            },
            "required_model_count": 0,
            "custom_node_count": 1,
            "unresolved_input_count": 1,
            "model_summary": None,
            "custom_node_resolution": None,
        }

    def mark_import_has_no_custom_nodes(self, import_session_id: str):
        if import_session_id not in self.pending_sessions:
            raise KeyError(import_session_id)
        self.no_custom_nodes_session_id = import_session_id
        return {
            "import_session_id": import_session_id,
            "workflow_id": "unknown__custom_node_workflow__0.1.0",
            "status": "needs_comfyui_update",
            "user_facing_message": "Update managed ComfyUI, then retry preparation.",
            "workflow": {
                "id": "unknown__custom_node_workflow__0.1.0",
                "name": "Custom node workflow",
                "version": "0.1.0",
                "description": "",
            },
            "required_model_count": 0,
            "custom_node_count": 0,
            "unresolved_input_count": 0,
            "model_summary": None,
            "custom_node_resolution": {
                "status": "needs_comfyui_update",
                "user_facing_message": "Update managed ComfyUI, then retry preparation.",
                "unresolved_node_types": ["NewCoreNode"],
                "ambiguous_node_types": [],
                "github_url_fields": [{"node_type": "NewCoreNode", "label": "NewCoreNode"}],
                "can_provide_github_urls": True,
                "can_mark_no_custom_nodes": True,
                "update_guidance": "Update managed ComfyUI from Settings to a newer version, then retry preparation.",
            },
        }

    def commit_workflow_import(self, import_session_id: str, *, duplicate_action: str | None = None):
        self.committed_duplicate_action = duplicate_action
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

    def workflow_package_payload(self, workflow_id: str):
        return self.get_workflow_package(workflow_id)

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


class FakeComfyUISidecarService:
    def __init__(self) -> None:
        self.started = False
        self.runtime_manager = SimpleNamespace(log_store=LogStore())

    async def start_comfyui(self):
        self.started = True
        return SimpleNamespace(status="skipped")


def _import_test_app(engine_service):
    sidecar_service = FakeComfyUISidecarService()
    return create_app(
        services=make_api_services(
            engine_service=engine_service,
            comfyui_sidecar_service=sidecar_service,
            workflow_library_service=getattr(
                engine_service,
                "workflow_library_service",
                None,
            ),
            dashboard_authoring_service=None,
            workflow_exporter=None,
            workflow_import_orchestrator=getattr(
                engine_service,
                "workflow_import_orchestrator",
                None,
            ),
            workflow_runner_lifecycle_service=None,
            run_job_service=None,
            run_orchestrator=None,
            run_result_service=None,
            history_service=None,
        )
    )


def test_import_test_app_lifespan_supports_managed_runtime_mode(monkeypatch) -> None:
    fake_service = FakeImportService()
    app = _import_test_app(fake_service)
    sidecar_service = app.state.api_services.comfyui_sidecar_service
    monkeypatch.setattr(
        main_module,
        "settings",
        dataclasses.replace(main_module.settings, comfyui_runtime_mode="managed"),
    )

    async def run_lifespan() -> None:
        async with main_module.lifespan(app):
            await asyncio.sleep(0)

    asyncio.run(run_lifespan())

    assert sidecar_service.started is True
    assert fake_service.shutdown_called is True


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


def _unresolved_custom_node_package() -> WorkflowPackage:
    return WorkflowPackage(
        metadata=WorkflowMetadata(
            id="local__txt2audio_moss_tts__0.1.0",
            name="txt2audio_MOSS-TTS",
            version="0.1.0",
        ),
        engine="comfyui",
        comfyui_graph={"1": {"class_type": "MossTTSModelLoader", "inputs": {}}},
        custom_nodes=[
            WorkflowCustomNodeRecord(
                id="comfyui-moss-tts",
                folder_name="comfyui-moss-tts",
                source="registry_metadata:comfyui-moss-tts",
                included=False,
                node_types=["MossTTSModelLoader", "MossTTSGenerate"],
            )
        ],
        import_metadata=WorkflowImportMetadata(
            status="missing_custom_nodes",
            user_facing_message="Noofy could not automatically find this workflow extension.",
            developer_details={
                "source_resolution": {
                    "status": "failed",
                    "mode": "manual_url",
                    "reason": "github_search_no_candidate",
                    "package_id": "comfyui-moss-tts",
                    "missing_custom_node": {
                        "package_id": "comfyui-moss-tts",
                        "node_types": ["MossTTSModelLoader", "MossTTSGenerate"],
                    },
                    "unresolved_node_types": [
                        "MossTTSGenerate",
                        "MossTTSModelLoader",
                    ],
                    "ambiguous_node_types": [],
                    "automatic_resolution_failures": [
                        "Noofy searched package names and node types but did not find a reliable candidate."
                    ],
                }
            },
        ),
    )


def _resolved_custom_node_package() -> WorkflowPackage:
    return WorkflowPackage(
        metadata=WorkflowMetadata(
            id="local__resolved_custom_node__0.1.0",
            name="Resolved custom node",
            version="0.1.0",
        ),
        engine="comfyui",
        comfyui_graph={"1": {"class_type": "MagicSampler", "inputs": {}}},
        custom_nodes=[
            WorkflowCustomNodeRecord(
                id="magic-pack",
                folder_name="magic-pack",
                source="https://example.test/magic-pack/archive/pinned.zip",
                included=True,
                node_types=["MagicSampler"],
                source_ref="7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                source_content_hash="sha256:" + ("1" * 64),
                source_cache_ref="cache/source",
            )
        ],
        import_metadata=WorkflowImportMetadata(
            status="imported",
            user_facing_message="Imported",
            developer_details={
                "source_resolution": {
                    "status": "resolved",
                    "resolved_custom_nodes": [
                        {
                            "package_id": "magic-pack",
                            "resolution_method": "user_github_url",
                            "source_ref": "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                        }
                    ],
                }
            },
        ),
    )


def test_import_workflow_endpoint_passes_archive_bytes_to_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(_import_test_app(fake_service)) as client:
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

    with TestClient(_import_test_app(fake_service)) as client:
        response = client.post(
            "/api/workflows/import?filename=test.noofy&allow_unverified_community_preparation=true",
            content=b"archive-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 200
    assert fake_service.allow_unverified_community_preparation is True


def test_preview_workflow_import_stores_unresolved_node_resolution_without_pending_session(tmp_path) -> None:
    package_store = FakePackageStore(_unresolved_custom_node_package())
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        imported_package_store=package_store,
        model_availability_service=FakeAvailabilityService(),
    )

    preview = service.preview_workflow_import(
        b"archive",
        original_filename="txt2audio_MOSS-TTS.noofy",
        allow_unverified_community_preparation=True,
    )

    assert preview.import_session_id is None
    assert preview.custom_node_resolution is not None
    assert preview.custom_node_resolution["mode"] == "manual_url"
    assert preview.custom_node_resolution["package_id"] == "comfyui-moss-tts"
    assert preview.custom_node_resolution["unresolved_node_types"] == [
        "MossTTSGenerate",
        "MossTTSModelLoader",
    ]
    assert package_store.preview_count == 1
    assert package_store.import_count == 0
    assert package_store.prepared_import_count == 1


def test_direct_workflow_import_stores_unresolved_node_resolution(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    package_store = FakePackageStore(_unresolved_custom_node_package())
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        imported_package_store=package_store,
        model_availability_service=FakeAvailabilityService(),
    )

    with TestClient(_import_test_app(service)) as client:
        response = client.post(
            "/api/workflows/import?filename=txt2audio_MOSS-TTS.noofy&allow_unverified_community_preparation=true",
            content=b"archive",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "missing_custom_nodes"
    assert payload["custom_node_resolution"]["mode"] == "manual_url"
    assert payload["custom_node_resolution"]["package_id"] == "comfyui-moss-tts"
    assert package_store.preview_count == 1
    assert package_store.import_count == 0
    assert package_store.prepared_import_count == 1


def test_direct_import_orchestrator_stores_unresolved_node_resolution(
    tmp_path,
) -> None:
    package_store = FakePackageStore(_unresolved_custom_node_package())
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        imported_package_store=package_store,
        model_availability_service=FakeAvailabilityService(),
    )

    payload = service.import_workflow_archive(
        b"archive",
        original_filename="txt2audio_MOSS-TTS.noofy",
        allow_unverified_community_preparation=True,
    )

    assert payload["custom_node_resolution"]["mode"] == "manual_url"
    assert package_store.preview_count == 1
    assert package_store.import_count == 0
    assert package_store.prepared_import_count == 1


def test_resolved_custom_node_resolution_does_not_keep_import_pending(tmp_path) -> None:
    package_store = FakePackageStore(_resolved_custom_node_package())
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        imported_package_store=package_store,
        model_availability_service=FakeAvailabilityService(),
    )

    preview = service.preview_workflow_import(
        b"archive",
        original_filename="resolved-custom-node.noofy",
        allow_unverified_community_preparation=True,
    )

    assert preview.import_session_id is None
    assert preview.custom_node_resolution is None
    assert package_store.preview_count == 1
    assert package_store.import_count == 0
    assert package_store.prepared_import_count == 1
    assert package_store.prepared_package is package_store.package
    assert package_store.prepared_package.custom_nodes[0].source_cache_ref == "cache/source"
    assert (
        package_store.prepared_package.custom_nodes[0].source_ref
        == "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50"
    )
    assert (
        package_store.prepared_package.custom_nodes[0].source_content_hash
        == "sha256:" + ("1" * 64)
    )


def test_import_custom_node_url_resolution_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(_import_test_app(fake_service)) as client:
        response = client.post(
            "/api/workflows/import/import-session-1/custom-nodes/resolve-from-urls",
            json={
                "urls_by_node_type": {
                    "MissingSampler": "https://github.com/example/custom-node"
                }
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_input_setup"
    assert fake_service.custom_node_urls == {
        "MissingSampler": "https://github.com/example/custom-node"
    }


def test_import_custom_node_candidate_approval_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(_import_test_app(fake_service)) as client:
        response = client.post(
            "/api/workflows/import/import-session-1/custom-nodes/approve-candidate",
            json={"candidate_id": "candidate-1"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "needs_input_setup"
    assert fake_service.approved_candidate_id == "candidate-1"


def test_import_no_custom_nodes_endpoint_returns_update_guidance(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(_import_test_app(fake_service)) as client:
        response = client.post(
            "/api/workflows/import/import-session-1/custom-nodes/no-custom-nodes",
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_comfyui_update"
    assert payload["custom_node_resolution"]["unresolved_node_types"] == [
        "NewCoreNode"
    ]
    assert "Update managed ComfyUI from Settings" in payload["custom_node_resolution"]["update_guidance"]
    assert fake_service.no_custom_nodes_session_id == "import-session-1"


def test_preview_workflow_import_endpoint_returns_staged_model_summary(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(_import_test_app(fake_service)) as client:
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


def test_preview_workflow_import_endpoint_maps_custom_node_resolution_guard_to_conflict(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    def raise_custom_node_resolution(*args, **kwargs):
        raise ImportRequiresCustomNodeResolutionError(
            "This workflow needs custom-node resolution before it can be imported. Use staged import preview.",
            custom_node_resolution={
                "mode": "manual_url",
                "package_id": "comfyui-moss-tts",
                "unresolved_node_types": ["MossTTSGenerate"],
            },
        )

    fake_service.preview_workflow_import = raise_custom_node_resolution

    with TestClient(_import_test_app(fake_service)) as client:
        response = client.post(
            "/api/workflows/import/preview?filename=txt2audio_MOSS-TTS.noofy",
            content=b"archive-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "custom-node resolution" in detail["message"]
    assert detail["custom_node_resolution"]["mode"] == "manual_url"
    assert detail["custom_node_resolution"]["package_id"] == "comfyui-moss-tts"


def test_preview_workflow_import_verifies_exact_local_model_before_prompting(tmp_path) -> None:
    payload = b"local-model"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "model.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)
    package = WorkflowPackage(
        metadata=WorkflowMetadata(
            id="local_model_workflow",
            name="Local Model Workflow",
            version="0.1.0",
        ),
        engine="comfyui",
        required_models=[
            RequiredModel(
                node_id="loader-1",
                input_name="ckpt_name",
                folder="checkpoints",
                filename="model.safetensors",
                checksum=f"sha256:{sha}",
                size_bytes=len(payload),
                verification_level="sha256_size",
            )
        ],
        comfyui_graph={},
    )
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        imported_package_store=FakePackageStore(package),
        model_availability_service=ModelAvailabilityService(
            model_roots=[noofy_root],
            noofy_models_dir=noofy_root,
            log_store=LogStore(),
        ),
    )

    preview = service.preview_workflow_import(b"archive")

    assert preview.import_session_id is not None
    assert preview.model_summary is not None
    assert preview.model_summary.ready_to_run is False
    assert preview.model_summary.models[0].status == "checking"
    assert preview.model_summary.models[0].requirement_id == "loader-1:ckpt_name:checkpoints/model.safetensors"
    verification = service.workflow_import_orchestrator.import_model_verification_status(
        preview.import_session_id
    )
    assert verification.status == "completed"
    assert verification.model_summary is not None
    assert verification.model_summary.ready_to_run is True
    assert len(verification.model_summary.models) == 1
    assert verification.model_summary.models[0].status == "available"
    assert verification.model_summary.models[0].requirement_id == "loader-1:ckpt_name:checkpoints/model.safetensors"
    assert verification.model_summary.models[0].matched_sha256 == sha


def test_preview_workflow_import_groups_shared_model_references(tmp_path) -> None:
    payload = b"shared-local-model"
    sha = hashlib.sha256(payload).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    model_path = noofy_root / "checkpoints" / "model.safetensors"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(payload)

    def _node(node_id: str, node_type: str) -> RequiredModel:
        return RequiredModel(
            node_id=node_id,
            node_type=node_type,
            input_name="ckpt_name",
            folder="checkpoints",
            filename="model.safetensors",
            checksum=f"sha256:{sha}",
            size_bytes=len(payload),
            verification_level="sha256_size",
        )

    package = WorkflowPackage(
        metadata=WorkflowMetadata(
            id="shared_model_workflow",
            name="Shared Model Workflow",
            version="0.1.0",
        ),
        engine="comfyui",
        required_models=[
            _node("loader-1", "FirstLoader"),
            _node("loader-2", "SecondLoader"),
            _node("loader-3", "ThirdLoader"),
        ],
        comfyui_graph={},
    )
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        imported_package_store=FakePackageStore(package),
        model_availability_service=ModelAvailabilityService(
            model_roots=[noofy_root],
            noofy_models_dir=noofy_root,
            log_store=LogStore(),
        ),
    )

    preview = service.preview_workflow_import(b"archive")

    assert preview.import_session_id is not None
    assert preview.model_summary is not None
    assert preview.required_model_count == 1
    assert preview.model_summary.total_count == 1
    assert preview.model_summary.models[0].status == "checking"
    assert preview.model_summary.models[0].reference_count == 3
    verification = service.workflow_import_orchestrator.import_model_verification_status(
        preview.import_session_id
    )
    assert verification.status == "completed"
    assert verification.total_models == 1
    assert verification.verified_models == 1
    assert verification.model_summary is not None
    assert verification.model_summary.ready_to_run is True
    assert len(verification.model_summary.models) == 1
    assert verification.model_summary.models[0].reference_count == 3
    assert [reference.node_id for reference in verification.model_summary.models[0].references] == [
        "loader-1",
        "loader-2",
        "loader-3",
    ]


def test_import_model_verification_status_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    def verification_status(import_session_id: str):
        assert import_session_id == "import-session-1"
        return {
            "job_id": "model-verification-1",
            "import_session_id": "import-session-1",
            "workflow_id": "unknown__model_workflow__0.1.0",
            "status": "running",
            "user_facing_message": "Verifying local model files...",
            "current_model_filename": "model.safetensors",
            "current_model_index": 1,
            "total_models": 2,
            "verified_models": 1,
            "percent": 50.0,
            "models": [],
            "model_summary": None,
        }

    fake_service.import_model_verification_status = verification_status  # type: ignore[attr-defined]

    with TestClient(_import_test_app(fake_service)) as client:
        response = client.get("/api/workflows/import/import-session-1/model-verification")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "running"
    assert payload["percent"] == 50.0


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


def test_staged_import_commit_reuses_previewed_package_without_blocking_on_model_verification(tmp_path) -> None:
    service = _staged_import_engine_service(tmp_path)
    package_store = service.workflow_import_orchestrator.imported_package_store
    availability_service = service.model_availability_service
    preview = service.preview_workflow_import(b"archive")
    session_id = preview.import_session_id
    assert session_id is not None

    service.commit_workflow_import(session_id)

    assert package_store.preview_count == 1
    assert package_store.import_count == 0
    assert package_store.prepared_import_count == 1
    assert package_store.prepared_package is package_store.package
    assert not any(call.get("verify_hashes") is True for call in availability_service.summarize_calls)


@pytest.mark.anyio
async def test_import_orchestrator_schedules_initial_preparation_for_resolved_custom_nodes(
    tmp_path,
) -> None:
    package = WorkflowPackage(
        metadata=WorkflowMetadata(
            id="local__custom_node_workflow__0.1.0",
            name="Custom node workflow",
            version="0.1.0",
        ),
        engine="comfyui",
        comfyui_graph={"1": {"class_type": "MagicSampler", "inputs": {}}},
        custom_nodes=[
            WorkflowCustomNodeRecord(
                id="magic-pack",
                folder_name="magic-pack",
                source="https://example.test/magic-pack/archive/pinned.zip",
                included=True,
                node_types=["MagicSampler"],
                source_ref="7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                source_content_hash="sha256:" + ("1" * 64),
                source_cache_ref="cache/source",
            )
        ],
        source_policy=SourcePolicy(
            trust_level="quarantined_community",
            source_policy="isolated_community_sources",
            automatic_preparation_allowed=True,
            allowed_source_origins=["registry-locked"],
            model_source_trust="filename_only",
            community_preparation_opted_in=True,
        ),
    )
    prepare_calls: list[str] = []

    async def prepare(workflow_id: str) -> dict[str, object]:
        prepare_calls.append(workflow_id)
        return {"status": "ready"}

    class Library:
        def workflow_summary(self, package: WorkflowPackage) -> dict[str, object]:
            return {"id": package.metadata.id, "name": package.metadata.name}

    orchestrator = WorkflowImportOrchestrator(
        imported_package_store=FakePackageStore(package),
        workflow_library_service=Library(),
        model_availability_service=FakeAvailabilityService(),
        log_store=LogStore(),
        post_import_preparer=prepare,
    )

    orchestrator.import_workflow_archive(b"archive")
    await asyncio.sleep(0)

    assert prepare_calls == ["local__custom_node_workflow__0.1.0"]


@pytest.mark.anyio
async def test_import_orchestrator_schedules_initial_preparation_without_custom_nodes(
    tmp_path,
) -> None:
    package = WorkflowPackage(
        metadata=WorkflowMetadata(
            id="local__future_core_workflow__0.1.0",
            name="Future core workflow",
            version="0.1.0",
        ),
        engine="comfyui",
        comfyui_graph={"1": {"class_type": "FutureCoreNode", "inputs": {}}},
        import_metadata=WorkflowImportMetadata(
            source_format="comfyui_api_json",
            original_filename="future-core.json",
            imported_at="2026-06-25T00:00:00Z",
            status="imported",
            user_facing_message="Imported",
            developer_details={"raw_comfyui_json": {"executable_node_types": ["FutureCoreNode"]}},
        ),
        source_policy=SourcePolicy(
            trust_level="quarantined_community",
            source_policy="isolated_community_sources",
            automatic_preparation_allowed=True,
            allowed_source_origins=["registry-locked"],
            model_source_trust="filename_only",
            community_preparation_opted_in=True,
        ),
    )
    prepare_calls: list[str] = []

    async def prepare(workflow_id: str) -> dict[str, object]:
        prepare_calls.append(workflow_id)
        return {"status": "ready"}

    class Library:
        def workflow_summary(self, package: WorkflowPackage) -> dict[str, object]:
            return {"id": package.metadata.id, "name": package.metadata.name}

    orchestrator = WorkflowImportOrchestrator(
        imported_package_store=FakePackageStore(package),
        workflow_library_service=Library(),
        model_availability_service=FakeAvailabilityService(),
        log_store=LogStore(),
        post_import_preparer=prepare,
    )

    orchestrator.import_workflow_archive(b"archive")
    await asyncio.sleep(0)

    assert prepare_calls == ["local__future_core_workflow__0.1.0"]


def test_pending_import_session_stays_alive_during_active_download(tmp_path) -> None:
    service = _staged_import_engine_service(tmp_path)
    preview = service.preview_workflow_import(b"archive")
    session_id = preview.import_session_id
    assert session_id is not None
    pending = service._pending_workflow_imports[session_id]
    pending.updated_at = datetime.now(UTC) - IMPORT_SESSION_TTL - timedelta(seconds=1)
    pending.active_download_job_id = "active-job"
    service._import_model_download_jobs["active-job"] = _ImportModelDownloadJob(
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


@pytest.mark.anyio
async def test_import_download_job_marks_partial_successes_as_downloaded(
    tmp_path,
) -> None:
    model_root = tmp_path / "Noofy Models"
    ownership = ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json")
    package = WorkflowPackage(
        metadata=WorkflowMetadata(
            id="partial_download_workflow",
            name="Partial download workflow",
            version="0.1.0",
        ),
        engine="comfyui",
        required_models=[
            RequiredModel(
                folder="diffusion_models",
                filename="missing.safetensors",
                size_bytes=117,
                verification_level="filename_size",
            ),
            RequiredModel(
                folder="text_encoders",
                filename="downloaded.safetensors",
                size_bytes=87,
                verification_level="filename_size",
            ),
        ],
        comfyui_graph={},
    )

    class PartialAvailabilityService:
        noofy_models_dir = model_root

        async def download_missing(self, package, **kwargs):  # type: ignore[no-untyped-def]
            downloaded = model_root / "text_encoders" / "downloaded.safetensors"
            downloaded.parent.mkdir(parents=True)
            downloaded.write_bytes(b"downloaded")
            return ModelDownloadSummary(
                workflow_id=package.metadata.id,
                status="completed_with_errors",
                user_facing_message="Some downloads failed.",
                downloaded_count=1,
                failed_count=1,
                model_summary=RequiredModelSummary(
                    workflow_id=package.metadata.id,
                    total_count=2,
                    available_count=1,
                    possible_match_count=0,
                    missing_count=0,
                    needs_manual_download_count=1,
                    ready_to_run=False,
                    models=[],
                ),
            )

    orchestrator = WorkflowImportOrchestrator(
        imported_package_store=FakePackageStore(package),
        workflow_library_service=object(),  # type: ignore[arg-type]
        model_availability_service=PartialAvailabilityService(),  # type: ignore[arg-type]
        log_store=LogStore(),
        model_ownership_store=ownership,
    )
    now = datetime.now(UTC)
    job = _ImportModelDownloadJob(
        job_id="download-job-1",
        import_session_id="import-session-1",
        workflow_id=package.metadata.id,
        cancel_event=asyncio.Event(),
        task=None,
        status="queued",
        user_facing_message="Model download is queued.",
        started_at=now,
        updated_at=now,
        total_models=2,
    )
    orchestrator._pending_workflow_imports["import-session-1"] = _PendingWorkflowImport(
        data=b"archive",
        original_filename=None,
        allow_unverified_community_preparation=False,
        package=package,
        created_at=now,
        updated_at=now,
        active_download_job_id=job.job_id,
    )
    orchestrator._import_model_download_jobs[job.job_id] = job

    await orchestrator._run_import_model_download_job(job.job_id)

    assert job.status == "completed_with_errors"
    assert ownership.origin_for_model("text_encoders/downloaded.safetensors") == "downloaded"
    assert ownership.origin_for_model("diffusion_models/missing.safetensors") is None


@pytest.mark.anyio
async def test_import_download_status_points_terminal_failure_at_failed_model() -> None:
    orchestrator = object.__new__(WorkflowImportOrchestrator)
    now = datetime.now(UTC)
    job = _ImportModelDownloadJob(
        job_id="download-job-1",
        import_session_id="import-session-1",
        workflow_id="workflow-1",
        cancel_event=asyncio.Event(),
        task=None,
        status="completed_with_errors",
        user_facing_message="Some downloads failed.",
        started_at=now,
        updated_at=now,
        total_models=2,
        current_model_filename="encoder.safetensors",
        current_model_index=2,
        models={
            "diffusion_models/longcat.safetensors": ImportModelDownloadProgressItem(
                requirement_id="diffusion_models/longcat.safetensors",
                filename="longcat.safetensors",
                status="needs_manual_download",
                status_label="Needs manual download",
                total_bytes=117,
                message="Noofy could not find a reliable automatic download source.",
            ),
            "text_encoders/encoder.safetensors": ImportModelDownloadProgressItem(
                requirement_id="text_encoders/encoder.safetensors",
                filename="encoder.safetensors",
                status="succeeded",
                status_label="Downloaded",
                bytes_downloaded=87,
                total_bytes=87,
            ),
        },
    )

    status = orchestrator._import_download_job_status(job)

    assert status.current_model_filename == "longcat.safetensors"
    assert status.current_model_index == 1
    assert status.status == "completed_with_errors"


def test_staged_import_download_commit_and_cancel_endpoints(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(_import_test_app(fake_service)) as client:
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


def test_staged_import_commit_accepts_duplicate_action(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(_import_test_app(fake_service)) as client:
        response = client.post(
            "/api/workflows/import/import-session-1/commit",
            json={"duplicate_action": "copy"},
        )

    assert response.status_code == 200
    assert fake_service.committed_duplicate_action == "copy"


def test_get_workflow_package_endpoint_returns_normalized_record(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(_import_test_app(FakeImportService())) as client:
        response = client.get("/api/workflows/unknown__eraserv4.5__0.1.0/package")

    assert response.status_code == 200
    assert response.json()["metadata"]["id"] == "unknown__eraserv4.5__0.1.0"


def test_get_workflow_package_endpoint_returns_404_for_unknown_workflow(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(_import_test_app(FakeImportService())) as client:
        response = client.get("/api/workflows/missing/package")

    assert response.status_code == 404


def test_trust_policy_endpoint_returns_public_key_metadata_only(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(_import_test_app(FakeImportService())) as client:
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


def test_import_model_verification_runs_async_and_logs_metrics(tmp_path, monkeypatch) -> None:
    # Two locally-present SHA256 models exercise the async (parallel) verification path
    # end-to-end and the structured completion diagnostic. Force the parallel happy path
    # so the assertions are deterministic regardless of the test host's filesystem.
    monkeypatch.setattr(
        availability_module, "_verification_filesystem_downgrade_reason", lambda roots: None
    )
    monkeypatch.setattr(
        availability_module,
        "settings",
        dataclasses.replace(availability_module.settings, model_verification_max_concurrency=4),
    )
    payload_a = b"local-model-a"
    payload_b = b"local-model-b-longer"
    sha_a = hashlib.sha256(payload_a).hexdigest()
    sha_b = hashlib.sha256(payload_b).hexdigest()
    noofy_root = tmp_path / "Noofy Models"
    (noofy_root / "checkpoints").mkdir(parents=True)
    (noofy_root / "checkpoints" / "a.safetensors").write_bytes(payload_a)
    (noofy_root / "checkpoints" / "b.safetensors").write_bytes(payload_b)
    package = WorkflowPackage(
        metadata=WorkflowMetadata(id="multi_model_wf", name="Multi Model", version="0.1.0"),
        engine="comfyui",
        required_models=[
            RequiredModel(
                node_id="loader-a",
                input_name="ckpt_name",
                folder="checkpoints",
                filename="a.safetensors",
                checksum=f"sha256:{sha_a}",
                size_bytes=len(payload_a),
                verification_level="sha256_size",
            ),
            RequiredModel(
                node_id="loader-b",
                input_name="ckpt_name",
                folder="checkpoints",
                filename="b.safetensors",
                checksum=f"sha256:{sha_b}",
                size_bytes=len(payload_b),
                verification_level="sha256_size",
            ),
        ],
        comfyui_graph={},
    )
    main_log = LogStore()
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(tmp_path / "packages"),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=RunnerSupervisor(),
        runtime_manager=StubRuntimeManager(),
        log_store=main_log,
        imported_package_store=FakePackageStore(package),
        model_availability_service=ModelAvailabilityService(
            model_roots=[noofy_root],
            noofy_models_dir=noofy_root,
            log_store=LogStore(),
        ),
    )
    orchestrator = service.workflow_import_orchestrator

    async def run() -> str:
        # A running loop forces the async parallel verification job (not the sync fallback).
        preview = service.preview_workflow_import(b"archive")
        for _ in range(500):
            status = orchestrator.import_model_verification_status(preview.import_session_id)
            if status.status in {"completed", "failed"}:
                break
            await asyncio.sleep(0.01)
        return preview.import_session_id

    session_id = asyncio.run(run())

    verification = orchestrator.import_model_verification_status(session_id)
    assert verification.status == "completed"
    assert verification.model_summary is not None
    assert verification.model_summary.ready_to_run is True
    assert {m.status for m in verification.model_summary.models} == {"available"}

    completed = [
        event
        for event in main_log.list_events().events
        if event.message == "Model verification completed"
    ]
    assert len(completed) == 1
    details = completed[0].details or {}
    assert details["model_count"] == 2
    assert details["file_count"] == 2
    # Cold cache: both files hashed exactly once, nothing served from cache.
    assert details["cache_hits"] == 0
    assert details["cache_misses"] == 2
    assert details["bytes_hashed"] == len(payload_a) + len(payload_b)
    assert details["selected_concurrency"] >= 1
    # Forced happy path: no filesystem clamp, so no serial-downgrade warning is emitted.
    assert details["downgrade_reason"] == "none"
    assert not [
        event
        for event in main_log.list_events().events
        if event.message.startswith("Model verification running serially")
    ]
    # Both the summary and the top-level models list are normalized to the package's
    # declared order (deterministic despite completion-ordered parallel results).
    assert [m.filename for m in verification.model_summary.models] == [
        "a.safetensors",
        "b.safetensors",
    ]
    assert [m.filename for m in verification.models] == ["a.safetensors", "b.safetensors"]
