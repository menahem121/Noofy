from fastapi.testclient import TestClient

from app.api import routes
from app.main import create_app


class FakeImportService:
    def __init__(self) -> None:
        self.imported_payload: bytes | None = None
        self.imported_filename: str | None = None
        self.shutdown_called = False

    def import_workflow_archive(self, data: bytes, *, original_filename: str | None = None):
        self.imported_payload = data
        self.imported_filename = original_filename
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

    def get_workflow_package(self, workflow_id: str):
        if workflow_id == "missing":
            raise KeyError(workflow_id)
        return {
            "metadata": {"id": workflow_id, "name": "Imported", "version": "0.1.0"},
            "custom_nodes": [],
            "required_models": [],
        }

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_import_workflow_endpoint_passes_archive_bytes_to_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()
    monkeypatch.setattr(routes, "engine_service", fake_service)

    with TestClient(create_app()) as client:
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


def test_get_workflow_package_endpoint_returns_normalized_record(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeImportService())

    with TestClient(create_app()) as client:
        response = client.get("/api/workflows/unknown__eraserv4.5__0.1.0/package")

    assert response.status_code == 200
    assert response.json()["metadata"]["id"] == "unknown__eraserv4.5__0.1.0"


def test_get_workflow_package_endpoint_returns_404_for_unknown_workflow(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    monkeypatch.setattr(routes, "engine_service", FakeImportService())

    with TestClient(create_app()) as client:
        response = client.get("/api/workflows/missing/package")

    assert response.status_code == 404
