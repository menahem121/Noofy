from fastapi.testclient import TestClient

from app.api import routes
from app.engine.models import ComfyUIRuntimeStatus
from app.main import create_app
from app.runtime.launch_settings import ComfyUILaunchSettings, comfyui_launch_response


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
            managed_vram_mode="normal",
        )

    def comfyui_launch_settings(self):
        return comfyui_launch_response(ComfyUILaunchSettings(vram_mode="normal"), mode="managed")

    async def update_comfyui_launch_settings(self, request: ComfyUILaunchSettings):
        return {
            "status": "updated",
            "settings": comfyui_launch_response(request, mode="managed").model_dump(),
            "restart_status": "started",
            "error": None,
        }

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


def test_comfyui_launch_settings_endpoint_returns_vram_options(monkeypatch) -> None:
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.get("/api/engine/comfyui/launch-settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["vram_mode"] == "normal"
    assert payload["applies_to_managed_runtime"] is True
    assert [option["value"] for option in payload["options"]] == [
        "normal",
        "gpu_only",
        "highvram",
        "lowvram",
        "novram",
        "cpu",
    ]


def test_comfyui_launch_settings_update_accepts_vram_mode(monkeypatch) -> None:
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.put("/api/engine/comfyui/launch-settings", json={"vram_mode": "lowvram"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "updated"
    assert payload["settings"]["vram_mode"] == "lowvram"
    assert payload["restart_status"] == "started"
