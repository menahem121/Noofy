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
    CredentialStoreUnavailable,
    EncryptedVaultCredentialStore,
    KeyringCredentialStore,
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


@pytest.mark.parametrize("module_name", ["keyring.backends.fail", "keyrings.alt.file"])
def test_keyring_credential_store_rejects_plaintext_and_fail_backends(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
) -> None:
    import keyring

    class BlockedBackend:
        pass

    BlockedBackend.__module__ = module_name
    monkeypatch.setattr(keyring, "get_keyring", lambda: BlockedBackend())

    status = KeyringCredentialStore().status()

    assert status.available is False
    assert status.kind == "os-keyring"
    assert status.error == "No OS-backed credential store is available."


def test_encrypted_vault_stores_secret_encrypted_and_metadata_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "app-data"
    passphrase_file = tmp_path / "config" / "api-key-vault.passphrase"
    passphrase_file.parent.mkdir()
    passphrase_file.write_text("vault passphrase\n", encoding="utf-8")
    passphrase_file.chmod(0o600)
    store = EncryptedVaultCredentialStore(
        vault_path=data_dir / "settings" / "api-key-vault.json",
        passphrase_file=passphrase_file,
        data_dir=data_dir,
        repo_root=tmp_path / "repo",
    )
    service = _service(tmp_path, store)

    service.save_key("hugging_face", "hf_encrypted_secret_1234")

    vault_text = (data_dir / "settings" / "api-key-vault.json").read_text(encoding="utf-8")
    metadata_text = (tmp_path / "settings" / "api-keys.json").read_text(encoding="utf-8")
    assert store.get_secret("hugging_face") == "hf_encrypted_secret_1234"
    assert "hf_encrypted_secret_1234" not in vault_text
    assert "hf_encrypted_secret_1234" not in metadata_text
    assert "vault passphrase" not in vault_text
    assert '"last_four": "1234"' in metadata_text


def test_encrypted_vault_replaces_and_clears_secret(tmp_path: Path) -> None:
    data_dir = tmp_path / "app-data"
    passphrase_file = tmp_path / "config" / "api-key-vault.passphrase"
    passphrase_file.parent.mkdir()
    passphrase_file.write_text("vault passphrase", encoding="utf-8")
    passphrase_file.chmod(0o600)
    store = EncryptedVaultCredentialStore(
        vault_path=data_dir / "settings" / "api-key-vault.json",
        passphrase_file=passphrase_file,
        data_dir=data_dir,
        repo_root=tmp_path / "repo",
    )

    store.set_secret("civitai", "first-secret")
    store.set_secret("civitai", "second-secret")
    store.delete_secret("civitai")

    assert store.get_secret("civitai") is None
    assert "first-secret" not in (data_dir / "settings" / "api-key-vault.json").read_text(encoding="utf-8")
    assert "second-secret" not in (data_dir / "settings" / "api-key-vault.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("path_kind", ["data", "vault", "passphrase"])
def test_encrypted_vault_rejects_repo_local_paths_by_default(tmp_path: Path, path_kind: str) -> None:
    repo_root = tmp_path / "Noofy"
    safe_data_dir = tmp_path / "app-data"
    safe_passphrase_file = tmp_path / "config" / "api-key-vault.passphrase"
    safe_passphrase_file.parent.mkdir()
    safe_passphrase_file.write_text("vault passphrase", encoding="utf-8")
    safe_passphrase_file.chmod(0o600)

    data_dir = repo_root / ".noofy-runtime" / "data" if path_kind == "data" else safe_data_dir
    vault_path = repo_root / "settings" / "api-key-vault.json" if path_kind == "vault" else data_dir / "settings" / "api-key-vault.json"
    passphrase_file = repo_root / "api-key-vault.passphrase" if path_kind == "passphrase" else safe_passphrase_file
    if path_kind == "passphrase":
        passphrase_file.parent.mkdir(parents=True, exist_ok=True)
        passphrase_file.write_text("vault passphrase", encoding="utf-8")
        passphrase_file.chmod(0o600)

    store = EncryptedVaultCredentialStore(
        vault_path=vault_path,
        passphrase_file=passphrase_file,
        data_dir=data_dir,
        repo_root=repo_root,
    )

    status = store.status()

    assert status.available is False
    assert status.kind == "encrypted-vault"
    assert status.display_path == "<app-data>/settings/api-key-vault.json"
    assert str(repo_root) not in status.model_dump_json()


def test_encrypted_vault_unsafe_override_allows_repo_local_paths(tmp_path: Path) -> None:
    repo_root = tmp_path / "Noofy"
    data_dir = repo_root / ".noofy-runtime" / "data"
    passphrase_file = repo_root / "api-key-vault.passphrase"
    passphrase_file.parent.mkdir(parents=True)
    passphrase_file.write_text("vault passphrase", encoding="utf-8")
    passphrase_file.chmod(0o600)
    store = EncryptedVaultCredentialStore(
        vault_path=data_dir / "settings" / "api-key-vault.json",
        passphrase_file=passphrase_file,
        data_dir=data_dir,
        repo_root=repo_root,
        allow_repo_local_secret_storage=True,
    )

    store.set_secret("hugging_face", "repo-local-dev-secret")

    assert store.get_secret("hugging_face") == "repo-local-dev-secret"


def test_encrypted_vault_fails_closed_for_bad_passphrase_configuration(tmp_path: Path) -> None:
    data_dir = tmp_path / "app-data"
    passphrase_file = tmp_path / "config" / "api-key-vault.passphrase"
    passphrase_file.parent.mkdir()
    passphrase_file.write_text("vault passphrase", encoding="utf-8")
    passphrase_file.chmod(0o600)
    store = EncryptedVaultCredentialStore(
        vault_path=data_dir / "settings" / "api-key-vault.json",
        passphrase_file=passphrase_file,
        data_dir=data_dir,
        repo_root=tmp_path / "repo",
    )
    store.set_secret("hugging_face", "hf_secret")
    passphrase_file.write_text("wrong passphrase", encoding="utf-8")
    passphrase_file.chmod(0o600)

    with pytest.raises(CredentialStoreUnavailable):
        store.get_secret("hugging_face")

    passphrase_file.chmod(0o644)
    status = store.status()
    assert status.available is False
    assert "passphrase" in (status.error or "")
    assert "wrong passphrase" not in status.model_dump_json()


def test_encrypted_vault_route_responses_do_not_expose_secret_paths_or_passphrase(tmp_path: Path) -> None:
    data_dir = tmp_path / "app-data"
    passphrase_file = tmp_path / "config" / "api-key-vault.passphrase"
    passphrase_file.parent.mkdir()
    passphrase_file.write_text("vault passphrase", encoding="utf-8")
    passphrase_file.chmod(0o600)
    store = EncryptedVaultCredentialStore(
        vault_path=data_dir / "settings" / "api-key-vault.json",
        passphrase_file=passphrase_file,
        data_dir=data_dir,
        repo_root=tmp_path / "repo",
    )
    service = _service(tmp_path, store, LogStore())

    with TestClient(create_app(engine_service=FakeEngineService(), api_key_service=service)) as client:
        save_response = client.put("/api/settings/apis/hugging-face/key", json={"api_key": "hf_secret_route_9999"})
        settings_response = client.get("/api/settings/apis")

    combined = save_response.text + settings_response.text + str(service.log_store.list_events().model_dump(mode="json"))
    assert save_response.status_code == 200
    assert "hf_secret_route_9999" not in combined
    assert "vault passphrase" not in combined
    assert str(passphrase_file) not in combined
    assert str(data_dir) not in combined
    assert "<app-data>/settings/api-key-vault.json" in settings_response.text
