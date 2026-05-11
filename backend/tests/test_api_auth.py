from fastapi.testclient import TestClient

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

    async def stream_progress_events(self, job_id: str):
        yield 'event: progress\ndata: {"job_id":"' + job_id + '","status":"running"}\n\n'

    async def fetch_output(self, job_id: str, filename: str, subfolder: str, output_type: str):
        return b"image-bytes", "image/png"

    async def shutdown(self) -> None:
        return None


def test_api_requests_succeed_without_configured_token(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/runtime")

    assert response.status_code == 200


def test_api_rejects_missing_token_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/runtime")

    assert response.status_code == 401


def test_api_rejects_wrong_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/runtime", headers={"Authorization": "Bearer wrong-token"})

    assert response.status_code == 401


def test_api_accepts_bearer_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/runtime", headers={"Authorization": "Bearer secret-token"})

    assert response.status_code == 200


def test_cors_preflight_for_tauri_origin_succeeds_with_token_enabled(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.options(
            "/api/runtime",
            headers={
                "Origin": "tauri://localhost",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "tauri://localhost"


def test_job_event_stream_accepts_query_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/jobs/job-1/events?token=secret-token")

    assert response.status_code == 200
    assert "event: progress" in response.text


def test_job_event_stream_rejects_missing_or_wrong_query_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        missing = client.get("/api/jobs/job-1/events")
        wrong = client.get("/api/jobs/job-1/events?token=wrong-token")

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_job_output_view_accepts_query_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get(
            "/api/jobs/job-1/outputs/view?filename=result.png&type=output&token=secret-token"
        )

    assert response.status_code == 200
    assert response.content == b"image-bytes"
