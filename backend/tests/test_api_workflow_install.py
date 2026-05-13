from fastapi.testclient import TestClient

from app.main import create_app
from app.runtime.runners.supervisor import RunnerDescriptor, RunnerKind, RunnerStatus


class FakeEngineService:
    def __init__(self) -> None:
        self.prepared_workflows: list[str] = []
        self.started_workflow_runners: list[str] = []
        self.stopped_workflow_runners: list[str] = []
        self.opened_runner_leases: list[str] = []
        self.closed_runner_leases: list[tuple[str, str]] = []
        self.canceled_runner_start_queues: list[str] = []

    def list_runners(self):
        return [
            RunnerDescriptor(
                runner_id="core",
                kind=RunnerKind.CORE_COMFYUI,
                base_url="http://127.0.0.1:8188",
                ws_url="ws://127.0.0.1:8188/ws",
                fingerprint="core",
                status=RunnerStatus.READY,
            )
        ]

    def memory_governor_metrics(self):
        return {"memory_retry_attempted": 1, "workflow_run_blocked_by_memory": 2}

    def get_install_state(self, workflow_id: str):
        if workflow_id == "missing":
            return {
                "workflow_id": workflow_id,
                "capsule_fingerprint": None,
                "status": "unsupported",
                "user_facing_message": "Unsupported",
                "installed_at": None,
                "last_used_at": None,
                "dependency_env_path": None,
                "runner_workspace_path": None,
                "smoke_test_status": "not_run",
                "last_error": None,
            }
        return {
            "workflow_id": workflow_id,
            "capsule_fingerprint": "fp-1",
            "status": "pending",
            "user_facing_message": "Not started",
            "installed_at": None,
            "last_used_at": None,
            "dependency_env_path": "/tmp/noofy/dep-env",
            "runner_workspace_path": "/tmp/noofy/runner-workspace",
            "smoke_test_status": "not_run",
            "last_error": None,
            "developer_details_available": False,
            "source_policy": {
                "policy_version": "phase6-local-0.1",
                "trust_level": "noofy_verified",
                "source_policy": "noofy_verified_sources_only",
                "package_source_type": "bundled",
                "automatic_preparation_allowed": True,
                "allowed_registry_origins": ["noofy-verified"],
                "allowed_source_origins": ["noofy-verified"],
                "allowed_model_origins": ["hashed-download", "huggingface.co", "noofy-verified", "user-local"],
                "model_source_trust": "hashed",
                "community_preparation_opt_in_required": False,
                "community_preparation_opted_in": False,
                "policy_status": "active",
            },
        }

    def get_install_state_developer_details(self, workflow_id: str):
        return {
            "workflow_id": workflow_id,
            "developer_details": {
                "last_error": "runner import failed",
                "dependency_env_path": "[local-path-redacted]",
            },
        }

    def workflow_status(self, workflow_id: str):
        if workflow_id == "missing":
            raise KeyError(workflow_id)
        return {
            "workflow_id": workflow_id,
            "workflow": {"id": workflow_id, "name": "Text to Image"},
            "install": self.get_install_state(workflow_id),
            "required_actions": [
                {
                    "kind": "prepare_workflow",
                    "status": "available",
                    "user_facing_message": "Prepare this workflow before running it.",
                }
            ],
            "runner": None,
            "runner_status": "not_started",
            "can_prepare": True,
            "can_cancel_preparation": False,
            "can_cancel_job": False,
        }

    def cancel_preparation(self, workflow_id: str):
        return {
            "workflow_id": workflow_id,
            "status": "no_active_cancelable_preparation",
            "user_facing_message": "No preparation is currently running for this workflow.",
            "cancelable": False,
        }

    def diagnostics_payload(self, *, workflow_id=None, include_developer_details=False, limit=200):
        event = {
            "id": 1,
            "timestamp": "2026-05-03T00:00:00+00:00",
            "level": "warning",
            "message": "Capsule preparation failed",
            "source": "engine.service",
            "workflow_id": workflow_id,
            "job_id": None,
            "correlation_ids": {"workflow_id": workflow_id, "runner_id": "runner-1"},
        }
        if include_developer_details:
            event["developer_details"] = {"runner_id": "runner-1", "token": "[redacted]"}
        return {"events": [event]}

    def storage_diagnostics_payload(self):
        return {
            "artifacts": [
                {
                    "kind": "dependency_env",
                    "path": "/tmp/noofy/runtime-store/envs/dep-env-a",
                    "size_bytes": 12,
                    "created_at": "2026-05-03T00:00:00+00:00",
                    "last_used_at": "2026-05-03T00:00:00+00:00",
                    "referenced_workflows": ["text_to_image_v0"],
                    "status": "ready",
                    "trust_level": "noofy_verified",
                    "fingerprint": "sha256:a",
                    "protected": False,
                    "developer_details": {},
                }
            ]
        }

    async def prepare_workflow(self, workflow_id: str):
        self.prepared_workflows.append(workflow_id)
        return {
            "workflow_id": workflow_id,
            "capsule_fingerprint": "fp-1",
            "status": "ready",
            "user_facing_message": "Ready",
            "installed_at": "2026-04-30T00:00:00+00:00",
            "last_used_at": None,
            "dependency_env_path": "/tmp/noofy/dep-env",
            "runner_workspace_path": "/tmp/noofy/runner-workspace",
            "smoke_test_status": "not_run",
            "last_error": None,
            "source_policy": self.get_install_state(workflow_id)["source_policy"],
        }

    async def start_workflow_runner(self, workflow_id: str):
        self.started_workflow_runners.append(workflow_id)
        return {
            "workflow_id": workflow_id,
            "status": "ready",
            "runner": {
                "runner_id": f"runner-{workflow_id}",
                "kind": "isolated_comfyui",
                "base_url": "http://127.0.0.1:9100",
                "ws_url": "ws://127.0.0.1:9100/ws",
                "fingerprint": "runner-fp",
                "status": "ready",
            },
            "pid": 4242,
            "install_status": "ready",
            "error": None,
        }

    async def stop_workflow_runner(self, workflow_id: str):
        self.stopped_workflow_runners.append(workflow_id)
        return {
            "workflow_id": workflow_id,
            "status": "stopped",
            "runner": None,
            "pid": None,
            "error": None,
        }

    def cancel_queued_runner_start(self, queue_id: str):
        self.canceled_runner_start_queues.append(queue_id)
        return {
            "queue_id": queue_id,
            "workflow_id": "text_to_image_v0",
            "status": "canceled",
        }

    def open_workflow_runner_lease(self, workflow_id: str):
        self.opened_runner_leases.append(workflow_id)
        return {
            "workflow_id": workflow_id,
            "status": "idle_warm",
            "lease_id": "lease-1",
            "runner": {
                "runner_id": f"runner-{workflow_id}",
                "kind": "isolated_comfyui",
                "base_url": "http://127.0.0.1:9100",
                "ws_url": "ws://127.0.0.1:9100/ws",
                "fingerprint": "runner-fp",
                "status": "idle_warm",
            },
        }

    def close_workflow_runner_lease(self, workflow_id: str, lease_id: str):
        self.closed_runner_leases.append((workflow_id, lease_id))
        return {
            "workflow_id": workflow_id,
            "status": "idle",
            "lease_id": lease_id,
            "runner": {
                "runner_id": f"runner-{workflow_id}",
                "kind": "isolated_comfyui",
                "base_url": "http://127.0.0.1:9100",
                "ws_url": "ws://127.0.0.1:9100/ws",
                "fingerprint": "runner-fp",
                "status": "idle",
            },
        }

    async def validate_workflow(self, workflow_id: str):
        if workflow_id == "missing":
            raise KeyError(workflow_id)
        return {"workflow_id": workflow_id, "valid": True, "missing_models": [], "errors": []}

    async def shutdown(self) -> None:
        return None


def test_runners_endpoint_returns_registered_descriptors(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/runners")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["runner_id"] == "core"
    assert payload[0]["kind"] == "core_comfyui"
    assert payload[0]["status"] == "ready"


def test_memory_governor_metrics_endpoint_returns_counters(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/memory-governor/metrics")

    assert response.status_code == 200
    assert response.json() == {
        "metrics": {
            "memory_retry_attempted": 1,
            "workflow_run_blocked_by_memory": 2,
        }
    }


def test_install_state_endpoint_returns_payload_shape(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/workflows/text_to_image_v0/install-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "text_to_image_v0"
    assert payload["status"] == "pending"
    assert payload["user_facing_message"] == "Not started"
    assert payload["dependency_env_path"] == "/tmp/noofy/dep-env"
    assert payload["runner_workspace_path"] == "/tmp/noofy/runner-workspace"
    assert payload["developer_details_available"] is False
    assert payload["source_policy"]["trust_level"] == "noofy_verified"
    assert payload["source_policy"]["source_policy"] == "noofy_verified_sources_only"
    assert payload["source_policy"]["automatic_preparation_allowed"] is True


def test_install_state_developer_details_endpoint_returns_technical_details(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/workflows/text_to_image_v0/install-state/developer-details")

    assert response.status_code == 200
    assert response.json()["developer_details"]["last_error"] == "runner import failed"


def test_install_state_unknown_workflow_uses_unsupported_payload(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/workflows/missing/install-state")

    assert response.status_code == 200
    assert response.json()["status"] == "unsupported"


def test_workflow_status_endpoint_includes_install_required_actions_and_runner_state(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/workflows/text_to_image_v0/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "text_to_image_v0"
    assert payload["install"]["status"] == "pending"
    assert payload["install"]["source_policy"]["policy_status"] == "active"
    assert payload["required_actions"][0]["kind"] == "prepare_workflow"
    assert payload["runner_status"] == "not_started"


def test_workflow_status_unknown_workflow_returns_404(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/workflows/missing/status")

    assert response.status_code == 404


def test_prepare_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post("/api/workflows/text_to_image_v0/prepare")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["source_policy"]["source_policy"] == "noofy_verified_sources_only"
    assert fake_service.prepared_workflows == ["text_to_image_v0"]


def test_cancel_prepare_endpoint_reports_no_active_cancelable_preparation(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.delete("/api/workflows/text_to_image_v0/prepare")

    assert response.status_code == 200
    assert response.json()["status"] == "no_active_cancelable_preparation"


def test_diagnostics_endpoint_hides_developer_details_by_default(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/diagnostics?workflow_id=text_to_image_v0")

    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["correlation_ids"]["runner_id"] == "runner-1"
    assert "developer_details" not in event


def test_diagnostics_endpoint_exposes_redacted_developer_details_when_requested(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/diagnostics?workflow_id=text_to_image_v0&developer_details=true")

    assert response.status_code == 200
    assert response.json()["events"][0]["developer_details"]["token"] == "[redacted]"


def test_storage_diagnostics_endpoint_returns_reference_index(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/storage/diagnostics")

    assert response.status_code == 200
    assert response.json()["artifacts"][0]["kind"] == "dependency_env"


def test_start_workflow_runner_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post("/api/workflows/text_to_image_v0/runner/start")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["runner"]["kind"] == "isolated_comfyui"
    assert fake_service.started_workflow_runners == ["text_to_image_v0"]


def test_cancel_queued_runner_start_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.delete("/api/workflows/runner/queue/queue-1")

    assert response.status_code == 200
    assert response.json()["status"] == "canceled"
    assert fake_service.canceled_runner_start_queues == ["queue-1"]


def test_stop_workflow_runner_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post("/api/workflows/text_to_image_v0/runner/stop")

    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    assert fake_service.stopped_workflow_runners == ["text_to_image_v0"]


def test_open_workflow_runner_lease_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post("/api/workflows/text_to_image_v0/runner/leases")

    assert response.status_code == 200
    assert response.json()["status"] == "idle_warm"
    assert response.json()["lease_id"] == "lease-1"
    assert fake_service.opened_runner_leases == ["text_to_image_v0"]


def test_close_workflow_runner_lease_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.delete("/api/workflows/text_to_image_v0/runner/leases/lease-1")

    assert response.status_code == 200
    assert response.json()["status"] == "idle"
    assert response.json()["lease_id"] == "lease-1"
    assert fake_service.closed_runner_leases == [("text_to_image_v0", "lease-1")]


def test_validate_unknown_workflow_returns_404(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.post("/api/workflows/missing/validate")

    assert response.status_code == 404
