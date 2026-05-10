from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine.diagnostics import LogStore
from app.main import create_app
from app.settings.api_keys import (
    ApiKeyMetadataStore,
    ApiKeyProvider,
    ApiKeySettingsService,
    CredentialStoreStatus,
)


class FakeEngineService:
    async def shutdown(self) -> None:
        return None


class FakeCredentialStore:
    def __init__(self) -> None:
        self.secrets: dict[ApiKeyProvider, str] = {}

    def status(self) -> CredentialStoreStatus:
        return CredentialStoreStatus(available=True, status="available")

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        self.secrets[provider] = secret

    def get_secret(self, provider: ApiKeyProvider) -> str | None:
        return self.secrets.get(provider)

    def delete_secret(self, provider: ApiKeyProvider) -> None:
        self.secrets.pop(provider, None)


def _service(tmp_path: Path, store: FakeCredentialStore, log_store: LogStore | None = None) -> ApiKeySettingsService:
    return ApiKeySettingsService(
        metadata_store=ApiKeyMetadataStore(tmp_path / "settings" / "api-keys.json"),
        credential_store=store,
        log_store=log_store,
    )


def test_api_key_service_stores_secret_in_credential_store_and_metadata_only(tmp_path: Path) -> None:
    credential_store = FakeCredentialStore()
    service = _service(tmp_path, credential_store)

    result = service.save_key("hugging_face", "hf_example_secret_1234")

    metadata_file = tmp_path / "settings" / "api-keys.json"
    assert result.provider.configured is True
    assert result.provider.last_four == "1234"
    assert credential_store.secrets["hugging_face"] == "hf_example_secret_1234"
    assert "hf_example_secret_1234" not in metadata_file.read_text(encoding="utf-8")
    assert "1234" in metadata_file.read_text(encoding="utf-8")


def test_api_key_service_clear_removes_secret_and_metadata(tmp_path: Path) -> None:
    credential_store = FakeCredentialStore()
    service = _service(tmp_path, credential_store)
    service.save_key("civitai", "civitai_secret_5678")

    result = service.clear_key("civitai")

    assert result.status == "cleared"
    assert result.provider.configured is False
    assert result.provider.last_four is None
    assert "civitai" not in credential_store.secrets


def test_api_key_service_diagnostics_do_not_include_secret(tmp_path: Path) -> None:
    credential_store = FakeCredentialStore()
    log_store = LogStore()
    service = _service(tmp_path, credential_store, log_store)

    service.save_key("hugging_face", "hf_secret_never_log_me")

    payload = log_store.list_events().model_dump(mode="json")
    assert "hf_secret_never_log_me" not in str(payload)
    assert payload["events"][-1]["details"] == {"provider": "hugging_face"}


def test_api_key_routes_return_metadata_without_exposing_secret(tmp_path: Path) -> None:
    credential_store = FakeCredentialStore()
    service = _service(tmp_path, credential_store)

    with TestClient(
        create_app(
            engine_service=FakeEngineService(),
            api_key_service=service,
        )
    ) as client:
        save_response = client.put(
            "/api/settings/apis/hugging-face/key",
            json={"api_key": "hf_route_secret_abcd"},
        )
        settings_response = client.get("/api/settings/apis")

    assert save_response.status_code == 200
    assert settings_response.status_code == 200
    assert credential_store.secrets["hugging_face"] == "hf_route_secret_abcd"
    assert "hf_route_secret_abcd" not in save_response.text
    assert "hf_route_secret_abcd" not in settings_response.text
    assert settings_response.json()["providers"]["hugging_face"] == {
        "provider": "hugging_face",
        "label": "Hugging Face",
        "configured": True,
        "last_four": "abcd",
    }


@pytest.mark.parametrize("provider", ["hugging-face", "hugging_face", "hf", "civitai"])
def test_api_key_routes_accept_known_provider_slugs(tmp_path: Path, provider: str) -> None:
    credential_store = FakeCredentialStore()
    service = _service(tmp_path, credential_store)

    with TestClient(create_app(engine_service=FakeEngineService(), api_key_service=service)) as client:
        response = client.put(f"/api/settings/apis/{provider}/key", json={"api_key": "token_1234"})

    assert response.status_code == 200
