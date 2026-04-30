from fastapi.testclient import TestClient

from app.api import routes
from app.main import create_app
from app.runtime.supervisor import RunnerDescriptor, RunnerKind, RunnerStatus


class FakeEngineService:
    def __init__(self) -> None:
        self.prepared_workflows: list[str] = []

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

    def get_install_state(self, workflow_id: str):
        if workflow_id == "missing":
            return {
                "workflow_id": workflow_id,
                "capsule_fingerprint": None,
                "status": "unsupported",
                "user_facing_message": "Unsupported",
                "installed_at": None,
                "last_used_at": None,
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
            "smoke_test_status": "not_run",
            "last_error": None,
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
            "smoke_test_status": "not_run",
            "last_error": None,
        }

    async def validate_workflow(self, workflow_id: str):
        if workflow_id == "missing":
            raise KeyError(workflow_id)
        return {"workflow_id": workflow_id, "valid": True, "missing_models": [], "errors": []}

    async def shutdown(self) -> None:
        return None


def test_runners_endpoint_returns_registered_descriptors(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.get("/api/runners")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["runner_id"] == "core"
    assert payload[0]["kind"] == "core_comfyui"
    assert payload[0]["status"] == "ready"


def test_install_state_endpoint_returns_payload_shape(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.get("/api/workflows/text_to_image_v0/install-state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_id"] == "text_to_image_v0"
    assert payload["status"] == "pending"
    assert payload["user_facing_message"] == "Not started"


def test_install_state_unknown_workflow_uses_unsupported_payload(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.get("/api/workflows/missing/install-state")

    assert response.status_code == 200
    assert response.json()["status"] == "unsupported"


def test_prepare_endpoint_calls_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()
    monkeypatch.setattr(routes, "engine_service", fake_service)

    with TestClient(create_app()) as client:
        response = client.post("/api/workflows/text_to_image_v0/prepare")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert fake_service.prepared_workflows == ["text_to_image_v0"]


def test_validate_unknown_workflow_returns_404(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.post("/api/workflows/missing/validate")

    assert response.status_code == 404
