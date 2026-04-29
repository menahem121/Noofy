from fastapi.testclient import TestClient

from app.api import routes
from app.engine.models import ComfyUIRuntimeStatus
from app.main import create_app


class FakeEngineService:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def runtime_status(self) -> ComfyUIRuntimeStatus:
        return ComfyUIRuntimeStatus(
            mode="managed",
            reachable=True,
            base_url="http://127.0.0.1:9000",
            repo_dir="/tmp/ComfyUI",
            managed_process_running=True,
            pid=123,
        )

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_runtime_status_endpoint_is_lightweight(monkeypatch) -> None:
    fake_service = FakeEngineService()
    monkeypatch.setattr(routes, "engine_service", fake_service)

    with TestClient(create_app()) as client:
        response = client.get("/api/engine/comfyui/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "managed"
    assert payload["reachable"] is True
    assert payload["pid"] == 123


def test_app_shutdown_calls_engine_service_shutdown(monkeypatch) -> None:
    fake_service = FakeEngineService()
    monkeypatch.setattr(routes, "engine_service", fake_service)

    with TestClient(create_app()) as client:
        assert client.get("/api/runtime").status_code == 200

    assert fake_service.shutdown_called
