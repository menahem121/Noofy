"""Tests for GET /api/paths diagnostics endpoint."""

from fastapi.testclient import TestClient

from app.api import routes
from app.engine.models import ComfyUIRuntimeStatus
from app.main import create_app


class FakeEngineService:
    async def health(self):
        return {"status": "ok"}

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
        return None


def test_paths_endpoint_returns_all_directory_entries(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.get("/api/paths")

    assert response.status_code == 200
    data = response.json()

    expected_keys = {
        "data_dir",
        "runtime_dir",
        "models_dir",
        "user_workflows_dir",
        "outputs_dir",
        "logs_dir",
        "cache_dir",
        "temp_dir",
        "bundled_workflows_dir",
        "comfyui_repo_dir",
    }
    assert expected_keys.issubset(data.keys())

    # Each entry should have path, exists, writable
    for key in expected_keys:
        assert "path" in data[key], f"Missing 'path' in {key}"
        assert "exists" in data[key], f"Missing 'exists' in {key}"
        assert "writable" in data[key], f"Missing 'writable' in {key}"


def test_paths_endpoint_protected_by_token_when_set(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        without_token = client.get("/api/paths")
        with_wrong = client.get("/api/paths", headers={"Authorization": "Bearer wrong"})
        with_correct = client.get("/api/paths", headers={"Authorization": "Bearer secret-token"})

    assert without_token.status_code == 401
    assert with_wrong.status_code == 401
    assert with_correct.status_code == 200


def test_paths_endpoint_works_without_token(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeEngineService())

    with TestClient(create_app()) as client:
        response = client.get("/api/paths")

    assert response.status_code == 200
