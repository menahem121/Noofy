from fastapi.testclient import TestClient

from app.composition import ApiServices
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


class FakeRunResultService:
    async def stream_progress_events(self, job_id: str):
        yield 'event: progress\ndata: {"job_id":"' + job_id + '","status":"running"}\n\n'


class FakeRunJobService:
    async def fetch_output(self, job_id: str, filename: str, subfolder: str, output_type: str):
        return b"image-bytes", "image/png"


class FakeWorkflowExporter:
    def export_archive(self, workflow_id: str, input_values=None, export_metadata=None):
        del input_values, export_metadata
        return b"noofy-archive", f"{workflow_id}.noofy"

    def export_comfyui_graph(self, workflow_id: str, input_values=None):
        del input_values
        return b'{"workflow": true}', f"{workflow_id}.json"


def _services(
    engine_service=None,
    *,
    run_job_service=None,
    run_result_service=None,
    workflow_exporter=None,
) -> ApiServices:
    placeholder = object()
    return ApiServices(
        engine_service=engine_service or FakeEngineService(),
        comfyui_sidecar_service=placeholder,
        user_state_service=placeholder,
        asset_service=placeholder,
        gallery_store=placeholder,
        api_key_service=placeholder,
        model_folder_service=placeholder,
        model_tag_store=placeholder,
        model_ownership_store=placeholder,
        model_inventory_service=placeholder,
        model_download_service=placeholder,
        workflow_library_service=None,
        dashboard_authoring_service=None,
        workflow_exporter=workflow_exporter,
        workflow_import_orchestrator=None,
        workflow_runner_lifecycle_service=None,
        run_job_service=run_job_service,
        run_orchestrator=None,
        run_result_service=run_result_service,
        history_service=None,
    )


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

    with TestClient(
        create_app(
            services=_services(run_result_service=FakeRunResultService()),
        )
    ) as client:
        response = client.get("/api/jobs/job-1/events?token=secret-token")

    assert response.status_code == 200
    assert "event: progress" in response.text


def test_job_event_stream_rejects_missing_or_wrong_query_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(
        create_app(
            services=_services(run_result_service=FakeRunResultService()),
        )
    ) as client:
        missing = client.get("/api/jobs/job-1/events")
        wrong = client.get("/api/jobs/job-1/events?token=wrong-token")

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_job_output_view_accepts_query_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(
        create_app(
            services=_services(run_job_service=FakeRunJobService()),
        )
    ) as client:
        response = client.get(
            "/api/jobs/job-1/outputs/view?filename=result.png&type=output&token=secret-token"
        )

    assert response.status_code == 200
    assert response.content == b"image-bytes"


def test_workflow_export_downloads_accept_query_token(monkeypatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")

    with TestClient(
        create_app(
            services=_services(workflow_exporter=FakeWorkflowExporter()),
        )
    ) as client:
        noofy_export = client.get("/api/workflows/text_to_image_v0/export?token=secret-token")
        comfy_export = client.get(
            "/api/workflows/text_to_image_v0/export/comfyui-json?token=secret-token"
        )

    assert noofy_export.status_code == 200
    assert noofy_export.content == b"noofy-archive"
    assert comfy_export.status_code == 200
    assert comfy_export.content == b'{"workflow": true}'


def test_workflow_export_posts_pass_current_input_values(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    class RecordingWorkflowExporter(FakeWorkflowExporter):
        archive_values = None
        graph_values = None

        export_metadata = None

        def export_archive(self, workflow_id: str, input_values=None, export_metadata=None):
            self.archive_values = input_values
            self.export_metadata = export_metadata
            return b"noofy-archive", f"{workflow_id}.noofy"

        def export_comfyui_graph(self, workflow_id: str, input_values=None):
            self.graph_values = input_values
            return b'{"workflow": true}', f"{workflow_id}.json"

    workflow_exporter = RecordingWorkflowExporter()

    with TestClient(
        create_app(
            services=_services(workflow_exporter=workflow_exporter),
        )
    ) as client:
        noofy_export = client.post(
            "/api/workflows/text_to_image_v0/export",
            json={
                "input_values": {"prompt": "visible prompt"},
                "export_metadata": {"name": "Reviewed Export"},
            },
        )
        comfy_export = client.post(
            "/api/workflows/text_to_image_v0/export/comfyui-json",
            json={"input_values": {"prompt": "visible prompt"}},
        )

    assert noofy_export.status_code == 200
    assert comfy_export.status_code == 200
    assert workflow_exporter.archive_values == {"prompt": "visible prompt"}
    assert workflow_exporter.export_metadata == {"name": "Reviewed Export"}
    assert workflow_exporter.graph_values == {"prompt": "visible prompt"}
