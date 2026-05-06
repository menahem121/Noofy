import asyncio
import types

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.core.config import settings as real_settings
from app.engine.models import ComfyUIRuntimeStatus
from app import main as main_module
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


# ---------------------------------------------------------------------------
# Lifespan background-startup tests
# ---------------------------------------------------------------------------


class FakeEngineServiceWithStart(FakeEngineService):
    def __init__(self) -> None:
        super().__init__()
        self.start_called = False
        self.start_delay: float = 0.0

    async def start_comfyui(self):
        self.start_called = True
        if self.start_delay:
            await asyncio.sleep(self.start_delay)
        return {"status": "started"}


def _patch_runtime_mode(monkeypatch, mode: str) -> None:
    """Patch the settings object imported into app.main (frozen dataclass workaround)."""
    fake_settings = types.SimpleNamespace(**{
        **{k: getattr(real_settings, k) for k in real_settings.__dataclass_fields__},
        "comfyui_runtime_mode": mode,
    })
    monkeypatch.setattr(main_module, "settings", fake_settings)


def test_lifespan_managed_mode_fires_start_in_background(monkeypatch) -> None:
    fake_service = FakeEngineServiceWithStart()
    monkeypatch.setattr(routes, "engine_service", fake_service)
    _patch_runtime_mode(monkeypatch, "managed")

    with TestClient(create_app()) as client:
        # Backend must be reachable immediately — before start_comfyui() finishes
        response = client.get("/api/runtime")
        assert response.status_code == 200

    assert fake_service.start_called


def test_lifespan_external_mode_does_not_call_start(monkeypatch) -> None:
    fake_service = FakeEngineServiceWithStart()
    monkeypatch.setattr(routes, "engine_service", fake_service)
    _patch_runtime_mode(monkeypatch, "external")

    with TestClient(create_app()) as client:
        client.get("/api/runtime")

    assert not fake_service.start_called


def test_lifespan_managed_mode_start_failure_does_not_crash_backend(monkeypatch) -> None:
    class FailingEngineService(FakeEngineService):
        async def start_comfyui(self):
            raise RuntimeError("simulated ComfyUI startup failure")

    fake_service = FailingEngineService()
    monkeypatch.setattr(routes, "engine_service", fake_service)
    _patch_runtime_mode(monkeypatch, "managed")

    with TestClient(create_app()) as client:
        response = client.get("/api/runtime")
        assert response.status_code == 200
