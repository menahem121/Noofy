from fastapi.testclient import TestClient

from app.main import create_app


class FakeImportService:
    def __init__(self) -> None:
        self.imported_payload: bytes | None = None
        self.imported_filename: str | None = None
        self.allow_unverified_community_preparation = False
        self.shutdown_called = False

    def import_workflow_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ):
        self.imported_payload = data
        self.imported_filename = original_filename
        self.allow_unverified_community_preparation = allow_unverified_community_preparation
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

    def trust_policy_payload(self):
        return {
            "schema_version": "0.1.0",
            "signature_payload_schema_version": "0.1.0",
            "development_hmac_allowed": False,
            "trusted_key_count": 1,
            "trusted_keys": [
                {
                    "key_id": "registry-test-key",
                    "algorithm": "ed25519",
                    "purpose": "registry",
                    "revoked": False,
                    "not_before": None,
                    "expires_at": None,
                    "policy_versions": ["phase6-local-0.1"],
                }
            ],
            "trust_levels": {},
            "imported_trusted_claims_require_verified_evidence": True,
            "secrets_exposed": False,
        }

    async def shutdown(self) -> None:
        self.shutdown_called = True


def test_import_workflow_endpoint_passes_archive_bytes_to_service(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(create_app(engine_service=fake_service)) as client:
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
    assert fake_service.allow_unverified_community_preparation is False


def test_import_workflow_endpoint_passes_community_preparation_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)
    fake_service = FakeImportService()

    with TestClient(create_app(engine_service=fake_service)) as client:
        response = client.post(
            "/api/workflows/import?filename=test.noofy&allow_unverified_community_preparation=true",
            content=b"archive-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )

    assert response.status_code == 200
    assert fake_service.allow_unverified_community_preparation is True


def test_get_workflow_package_endpoint_returns_normalized_record(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeImportService())) as client:
        response = client.get("/api/workflows/unknown__eraserv4.5__0.1.0/package")

    assert response.status_code == 200
    assert response.json()["metadata"]["id"] == "unknown__eraserv4.5__0.1.0"


def test_get_workflow_package_endpoint_returns_404_for_unknown_workflow(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeImportService())) as client:
        response = client.get("/api/workflows/missing/package")

    assert response.status_code == 404


def test_trust_policy_endpoint_returns_public_key_metadata_only(monkeypatch) -> None:
    monkeypatch.delenv("NOOFY_API_TOKEN", raising=False)

    with TestClient(create_app(engine_service=FakeImportService())) as client:
        response = client.get("/api/trust/policy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["trusted_key_count"] == 1
    assert payload["trusted_keys"][0] == {
        "key_id": "registry-test-key",
        "algorithm": "ed25519",
        "purpose": "registry",
        "revoked": False,
        "not_before": None,
        "expires_at": None,
        "policy_versions": ["phase6-local-0.1"],
    }
    assert payload["secrets_exposed"] is False
    assert "secret" not in str(payload["trusted_keys"]).casefold()
    assert "local-secret" not in str(payload)
