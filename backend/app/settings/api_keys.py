from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.engine.diagnostics import DiagnosticsSink

ApiKeyProvider = Literal["hugging_face", "civitai"]

PROVIDER_LABELS: dict[ApiKeyProvider, str] = {
    "hugging_face": "Hugging Face",
    "civitai": "Civitai",
}

PROVIDER_ALIASES: dict[str, ApiKeyProvider] = {
    "hugging-face": "hugging_face",
    "hugging_face": "hugging_face",
    "hf": "hugging_face",
    "civitai": "civitai",
}

KEYCHAIN_SERVICE_NAME = "Noofy"
KEYCHAIN_ACCOUNT_PREFIX = "external-model-platform"


class CredentialStoreUnavailable(RuntimeError):
    """Raised when the OS credential store cannot save or delete secrets."""


class CredentialStoreStatus(BaseModel):
    available: bool
    status: str
    error: str | None = None


class CredentialStore(Protocol):
    def status(self) -> CredentialStoreStatus:
        """Return whether the platform credential store appears usable."""

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        """Save a provider secret in the operating system credential store."""

    def delete_secret(self, provider: ApiKeyProvider) -> None:
        """Remove a provider secret from the operating system credential store."""


class ApiKeyProviderMetadata(BaseModel):
    provider: ApiKeyProvider
    label: str
    configured: bool = False
    last_four: str | None = None


class ApiKeySettingsResponse(BaseModel):
    providers: dict[ApiKeyProvider, ApiKeyProviderMetadata]
    credential_store: CredentialStoreStatus


class ApiKeyUpdateRequest(BaseModel):
    api_key: str = Field(min_length=1, max_length=8192)


class ApiKeyUpdateResult(BaseModel):
    status: Literal["saved", "cleared"]
    provider: ApiKeyProviderMetadata


class ApiKeyMetadataStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> dict[ApiKeyProvider, ApiKeyProviderMetadata]:
        metadata = _default_metadata()
        if not self.path.exists():
            return metadata
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return metadata

        providers = raw.get("providers") if isinstance(raw, dict) else None
        if not isinstance(providers, dict):
            return metadata

        for raw_provider, raw_value in providers.items():
            provider = provider_from_slug(raw_provider)
            if provider is None or not isinstance(raw_value, dict):
                continue
            try:
                parsed = ApiKeyProviderMetadata.model_validate({
                    **raw_value,
                    "provider": provider,
                    "label": PROVIDER_LABELS[provider],
                })
            except Exception:
                continue
            metadata[provider] = parsed
        return metadata

    def write(self, metadata: dict[ApiKeyProvider, ApiKeyProviderMetadata]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1",
            "providers": {
                provider: metadata[provider].model_dump(mode="json")
                for provider in sorted(metadata)
            },
        }
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class KeyringCredentialStore:
    def __init__(self, *, service_name: str = KEYCHAIN_SERVICE_NAME) -> None:
        self.service_name = service_name

    def status(self) -> CredentialStoreStatus:
        try:
            self._keyring()
            return CredentialStoreStatus(available=True, status="available")
        except CredentialStoreUnavailable as exc:
            return CredentialStoreStatus(available=False, status="unavailable", error=str(exc))

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        try:
            keyring = self._keyring()
            keyring.set_password(self.service_name, _account_name(provider), secret)
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("The operating system credential store could not save this API key.") from exc

    def delete_secret(self, provider: ApiKeyProvider) -> None:
        try:
            keyring = self._keyring()
            keyring.delete_password(self.service_name, _account_name(provider))
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            name = exc.__class__.__name__
            if name == "PasswordDeleteError":
                return
            raise CredentialStoreUnavailable("The operating system credential store could not delete this API key.") from exc

    def _keyring(self):
        try:
            import keyring
        except Exception as exc:
            raise CredentialStoreUnavailable("No OS credential store backend is installed.") from exc

        backend = keyring.get_keyring()
        module = backend.__class__.__module__.lower()
        name = backend.__class__.__name__.lower()
        backend_id = f"{module}.{name}"
        blocked_markers = (
            "keyring.backends.fail",
            "keyring.backends.null",
            "keyrings.alt.file",
            "plaintext",
            "cryptedfile",
        )
        if any(marker in backend_id for marker in blocked_markers):
            raise CredentialStoreUnavailable("No OS-backed credential store is available.")
        return keyring


class ApiKeySettingsService:
    def __init__(
        self,
        *,
        metadata_store: ApiKeyMetadataStore,
        credential_store: CredentialStore | None = None,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.metadata_store = metadata_store
        self.credential_store = credential_store or KeyringCredentialStore()
        self.log_store = log_store

    def settings(self) -> ApiKeySettingsResponse:
        return ApiKeySettingsResponse(
            providers=self.metadata_store.read(),
            credential_store=self.credential_store.status(),
        )

    def save_key(self, provider: ApiKeyProvider, api_key: str) -> ApiKeyUpdateResult:
        secret = api_key.strip()
        if not secret:
            raise ValueError("API key cannot be empty.")

        try:
            self.credential_store.set_secret(provider, secret)
        except CredentialStoreUnavailable:
            self._record("error", "External API key could not be saved", provider)
            raise

        metadata = self.metadata_store.read()
        metadata[provider] = ApiKeyProviderMetadata(
            provider=provider,
            label=PROVIDER_LABELS[provider],
            configured=True,
            last_four=secret[-4:],
        )
        self.metadata_store.write(metadata)
        self._record("info", "External API key saved", provider)
        return ApiKeyUpdateResult(status="saved", provider=metadata[provider])

    def clear_key(self, provider: ApiKeyProvider) -> ApiKeyUpdateResult:
        try:
            self.credential_store.delete_secret(provider)
        except CredentialStoreUnavailable:
            self._record("error", "External API key could not be cleared", provider)
            raise

        metadata = self.metadata_store.read()
        metadata[provider] = ApiKeyProviderMetadata(
            provider=provider,
            label=PROVIDER_LABELS[provider],
            configured=False,
            last_four=None,
        )
        self.metadata_store.write(metadata)
        self._record("info", "External API key cleared", provider)
        return ApiKeyUpdateResult(status="cleared", provider=metadata[provider])

    def _record(self, level: str, message: str, provider: ApiKeyProvider) -> None:
        if self.log_store is None:
            return
        self.log_store.add(
            level,  # type: ignore[arg-type]
            message,
            "settings.api_keys",
            details={"provider": provider},
        )


def provider_from_slug(value: str) -> ApiKeyProvider | None:
    return PROVIDER_ALIASES.get(value.strip().lower())


def _default_metadata() -> dict[ApiKeyProvider, ApiKeyProviderMetadata]:
    return {
        provider: ApiKeyProviderMetadata(provider=provider, label=label)
        for provider, label in PROVIDER_LABELS.items()
    }


def _account_name(provider: ApiKeyProvider) -> str:
    return f"{KEYCHAIN_ACCOUNT_PREFIX}:{provider}"

