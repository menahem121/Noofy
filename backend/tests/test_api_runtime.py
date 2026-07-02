import asyncio
import types

import pytest
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from app.api.schemas import ComfyUILaunchSettings
from app.composition import ApiServices, create_api_services
from app.core.config import settings as real_settings
from app.diagnostics import LogStore
from app.engine.models import BackendHealthReport, ComfyUIRuntimeStatus, EngineOutputStream
from app import main as main_module
from app.main import create_app, _sanitized_request_validation_exception_handler
from app.runtime.comfyui.launch_settings import comfyui_launch_response
from tests.service_factory import make_api_services


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

    def resource_snapshot(self):
        return {
            "observed_at": "2026-05-08T10:00:00+00:00",
            "cpu": {"available": True, "percent": 23.0, "used_mb": None, "total_mb": None, "free_mb": None, "source": "test", "error": None},
            "ram": {"available": True, "percent": 35.0, "used_mb": 11_264, "total_mb": 32_768, "free_mb": 21_504, "source": "test", "error": None},
            "vram": {"available": False, "percent": None, "used_mb": None, "total_mb": None, "free_mb": None, "source": None, "error": "vram_unavailable"},
            "backend": "cpu",
            "device_name": None,
            "memory_pressure": "low",
        }

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


class FakeRunJobService:
    def __init__(self, log_store: LogStore | None = None) -> None:
        self.log_store = log_store
        self.range_header: str | None = None

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        assert job_id == "job-1"
        assert filename == "result.png"
        assert subfolder == "preview"
        assert output_type == "output"
        return b"image-bytes", "image/png"

    async def stream_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
        range_header: str | None = None,
    ) -> EngineOutputStream:
        self.range_header = range_header
        content, media_type = await self.fetch_output(job_id, filename, subfolder, output_type)

        async def body():
            yield content

        return EngineOutputStream(
            body=body(),
            media_type=media_type,
            status_code=206 if range_header else 200,
            headers={"content-range": "bytes 0-4/11"} if range_header else {},
        )

    def list_job_logs(self, job_id: str, *, level=None, limit: int = 200):
        assert self.log_store is not None
        return self.log_store.list_events(job_id=job_id, level=level, limit=limit)


def _services(engine_service=None, *, run_job_service=None) -> ApiServices:
    return make_api_services(
        engine_service=engine_service or FakeEngineService(),
        workflow_library_service=None,
        dashboard_authoring_service=None,
        workflow_exporter=None,
        workflow_import_orchestrator=None,
        workflow_runner_lifecycle_service=None,
        run_job_service=run_job_service,
        run_orchestrator=None,
        run_result_service=None,
        history_service=None,
    )


class FakeComfyUISidecarService:
    async def runtime_status(self) -> ComfyUIRuntimeStatus:
        return ComfyUIRuntimeStatus(
            mode="managed",
            reachable=True,
            base_url="http://127.0.0.1:9100",
            repo_dir="/tmp/SidecarComfyUI",
            managed_process_running=True,
            pid=456,
            managed_vram_mode="lowvram",
        )

    def comfyui_launch_settings(self):
        return comfyui_launch_response(ComfyUILaunchSettings(vram_mode="lowvram"), mode="managed")

    async def update_comfyui_launch_settings(self, request: ComfyUILaunchSettings):
        return {
            "status": "updated",
            "settings": comfyui_launch_response(request, mode="managed").model_dump(),
            "restart_status": "started",
            "error": None,
        }

    async def start_comfyui(self):
        return {"status": "started"}


class SharedDiagnosticsEngineService(FakeEngineService):
    def __init__(self, log_store: LogStore) -> None:
        super().__init__()
        self.log_store = log_store

    def list_logs(self, *, level=None, limit: int = 200):
        return self.log_store.list_events(level=level, limit=limit)

    def list_job_logs(self, job_id: str, *, level=None, limit: int = 200):
        return self.log_store.list_events(job_id=job_id, level=level, limit=limit)

    async def health(self):
        return BackendHealthReport(
            status="degraded",
            comfyui=await self.runtime_status(),
            workflow_package_count=0,
            workflows=[],
            latest_error=self.log_store.latest_error(),
        )


def test_runtime_status_endpoint_is_lightweight(monkeypatch) -> None:
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.get("/api/engine/comfyui/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "managed"
    assert payload["reachable"] is True
    assert payload["pid"] == 123


def test_comfyui_status_endpoint_uses_sidecar_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(
        create_app(
            engine_service=FakeEngineService(),
            comfyui_sidecar_service=FakeComfyUISidecarService(),
        )
    ) as client:
        runtime_response = client.get("/api/runtime")
        second_runtime_response = client.get("/api/runtime")
        comfyui_response = client.get("/api/engine/comfyui/status")

    assert runtime_response.status_code == 200
    assert runtime_response.headers["cache-control"] == "no-store"
    assert runtime_response.headers["pragma"] == "no-cache"
    assert runtime_response.headers["expires"] == "0"
    assert runtime_response.json()["base_url"] == "http://127.0.0.1:9000"
    assert runtime_response.json()["backend_session_id"].startswith("bs-")
    assert runtime_response.json()["backend_session_started_at"]
    assert second_runtime_response.json()["backend_session_id"] == runtime_response.json()["backend_session_id"]
    assert comfyui_response.status_code == 200
    assert comfyui_response.json()["base_url"] == "http://127.0.0.1:9100"
    assert comfyui_response.json()["pid"] == 456


def test_create_app_defers_default_service_factory_until_lifespan(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeEngineService()
    factory_calls = 0

    def service_factory():
        nonlocal factory_calls
        factory_calls += 1
        return create_api_services(engine_service=fake_service)

    app = create_app(service_factory=service_factory)

    assert factory_calls == 0

    with TestClient(app) as client:
        response = client.get("/api/runtime")

    assert response.status_code == 200
    assert factory_calls == 1
    assert fake_service.shutdown_called


def test_diagnostic_api_endpoints_read_from_shared_injected_store(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    log_store = LogStore()
    log_store.add("info", "Global diagnostic", "test")
    log_store.add("warning", "Job diagnostic", "test", job_id="job-1")
    latest_error = log_store.add("error", "Latest failure", "test", job_id="job-1")

    with TestClient(
        create_app(
            services=_services(
                SharedDiagnosticsEngineService(log_store),
                run_job_service=FakeRunJobService(log_store),
            )
        )
    ) as client:
        logs_response = client.get("/api/logs")
        job_logs_response = client.get("/api/jobs/job-1/logs")
        health_response = client.get("/api/health")

    assert logs_response.status_code == 200
    assert job_logs_response.status_code == 200
    assert health_response.status_code == 200
    logs_payload = logs_response.json()
    job_logs_payload = job_logs_response.json()
    health_payload = health_response.json()

    assert list(logs_payload) == ["events"]
    assert logs_payload["events"][-1]["message"] == "Latest failure"
    assert list(job_logs_payload) == ["events"]
    assert [event["message"] for event in job_logs_payload["events"]] == [
        "Job diagnostic",
        "Latest failure",
    ]
    assert health_payload["latest_error"]["id"] == latest_error.id
    assert health_payload["latest_error"]["message"] == "Latest failure"


@pytest.mark.anyio
async def test_request_validation_handler_does_not_echo_request_inputs() -> None:
    response = await _sanitized_request_validation_exception_handler(
        request=object(),
        exc=RequestValidationError(
            [
                {
                    "loc": ("body", "inputs", "prompt"),
                    "msg": "Input should be a valid string",
                    "type": "string_type",
                    "input": "private prompt with api_key=secret",
                }
            ]
        ),
    )

    body = response.body.decode()
    assert response.status_code == 422
    assert "Request validation failed." in body
    assert "private prompt" not in body
    assert "secret" not in body


def test_resource_snapshot_endpoint_uses_backend_observer(monkeypatch) -> None:
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.get("/api/resources")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    payload = response.json()
    assert payload["cpu"]["percent"] == 23.0
    assert payload["ram"]["used_mb"] == 11264
    assert payload["vram"]["available"] is False


def test_job_output_view_endpoint_returns_backend_owned_media(monkeypatch) -> None:

    with TestClient(
        create_app(
            services=_services(run_job_service=FakeRunJobService()),
        )
    ) as client:
        response = client.get(
            "/api/jobs/job-1/outputs/view",
            params={
                "filename": "result.png",
                "subfolder": "preview",
                "type": "output",
            },
        )

    assert response.status_code == 200
    assert response.content == b"image-bytes"
    assert response.headers["content-type"] == "image/png"


def test_job_output_view_endpoint_forwards_range_requests(monkeypatch) -> None:
    run_job_service = FakeRunJobService()

    with TestClient(
        create_app(
            services=_services(run_job_service=run_job_service),
        )
    ) as client:
        response = client.get(
            "/api/jobs/job-1/outputs/view",
            params={"filename": "result.png", "subfolder": "preview", "type": "output"},
            headers={"Range": "bytes=0-4"},
        )

    assert run_job_service.range_header == "bytes=0-4"
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 0-4/11"


def test_app_shutdown_calls_engine_service_shutdown(monkeypatch) -> None:
    fake_service = FakeEngineService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        assert client.get("/api/runtime").status_code == 200

    assert fake_service.shutdown_called


def test_comfyui_launch_settings_endpoint_returns_vram_options(monkeypatch) -> None:

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get("/api/engine/comfyui/launch-settings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["vram_mode"] == "normal"
    assert payload["applies_to_managed_runtime"] is True
    assert [option["value"] for option in payload["options"]] == [
        "cpu",
        "novram",
        "lowvram",
        "normal",
        "highvram",
    ]


def test_comfyui_launch_settings_update_accepts_vram_mode(monkeypatch) -> None:

    with TestClient(create_app(engine_service=FakeEngineService())) as client:
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
    _patch_runtime_mode(monkeypatch, "managed")

    with TestClient(create_app(engine_service=fake_service)) as client:
        # Backend must be reachable immediately — before start_comfyui() finishes
        response = client.get("/api/runtime")
        assert response.status_code == 200

    assert fake_service.start_called


def test_lifespan_external_mode_does_not_call_start(monkeypatch) -> None:
    fake_service = FakeEngineServiceWithStart()
    _patch_runtime_mode(monkeypatch, "external")

    with TestClient(create_app(engine_service=fake_service)) as client:
        client.get("/api/runtime")

    assert not fake_service.start_called


def test_lifespan_managed_mode_start_failure_does_not_crash_backend(monkeypatch) -> None:
    class FailingEngineService(FakeEngineService):
        async def start_comfyui(self):
            raise RuntimeError("simulated ComfyUI startup failure")

    fake_service = FailingEngineService()
    _patch_runtime_mode(monkeypatch, "managed")

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.get("/api/runtime")
        assert response.status_code == 200
