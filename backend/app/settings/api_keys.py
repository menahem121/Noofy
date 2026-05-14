from __future__ import annotations

import base64
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Literal, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, Field

from app.diagnostics import DiagnosticsSink

ApiKeyProvider = Literal["hugging_face", "civitai", "comfy_org"]

PROVIDER_LABELS: dict[ApiKeyProvider, str] = {
    "hugging_face": "Hugging Face",
    "civitai": "Civitai",
    "comfy_org": "ComfyUI Account API Key",
}

PROVIDER_ALIASES: dict[str, ApiKeyProvider] = {
    "hugging-face": "hugging_face",
    "hugging_face": "hugging_face",
    "hf": "hugging_face",
    "civitai": "civitai",
    "comfy-org": "comfy_org",
    "comfy_org": "comfy_org",
    "comfy": "comfy_org",
}

KEYCHAIN_SERVICE_NAME = "Noofy"
KEYCHAIN_ACCOUNT_PREFIX = "external-model-platform"
ENCRYPTED_VAULT_FILENAME = "api-key-vault.json"
ENCRYPTED_VAULT_CHECK_TEXT = b"noofy-api-key-vault-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class CredentialStoreUnavailable(RuntimeError):
    """Raised when the OS credential store cannot save or delete secrets."""


class CredentialStoreStatus(BaseModel):
    available: bool
    status: str
    error: str | None = None
    kind: str = "os-keyring"
    backend: str | None = None
    display_path: str | None = None
    guidance: str | None = None


class CredentialStore(Protocol):
    def status(self) -> CredentialStoreStatus:
        """Return whether the platform credential store appears usable."""

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        """Save a provider secret in the operating system credential store."""

    def get_secret(self, provider: ApiKeyProvider) -> str | None:
        """Read a provider secret from the operating system credential store."""

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
            _, backend_id = self._keyring()
            return CredentialStoreStatus(
                available=True,
                status="available",
                kind="os-keyring",
                backend=backend_id,
            )
        except CredentialStoreUnavailable as exc:
            return CredentialStoreStatus(
                available=False,
                status="unavailable",
                error=str(exc),
                kind="os-keyring",
                guidance=(
                    "Noofy could not find an OS-backed credential store. On headless Linux, "
                    "configure a Secret Service provider in the same D-Bus session or explicitly "
                    "opt in to encrypted-vault mode."
                ),
            )

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        try:
            keyring, _ = self._keyring()
            keyring.set_password(self.service_name, _account_name(provider), secret)
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("The operating system credential store could not save this API key.") from exc

    def get_secret(self, provider: ApiKeyProvider) -> str | None:
        try:
            keyring, _ = self._keyring()
            return keyring.get_password(self.service_name, _account_name(provider))
        except CredentialStoreUnavailable:
            raise
        except Exception as exc:
            raise CredentialStoreUnavailable("The operating system credential store could not read this API key.") from exc

    def delete_secret(self, provider: ApiKeyProvider) -> None:
        try:
            keyring, _ = self._keyring()
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
        return keyring, backend_id


class EncryptedVaultCredentialStore:
    def __init__(
        self,
        *,
        vault_path: Path,
        passphrase_file: Path,
        data_dir: Path,
        repo_root: Path = PROJECT_ROOT,
        allow_repo_local_secret_storage: bool = False,
    ) -> None:
        self.vault_path = vault_path.expanduser()
        self.passphrase_file = passphrase_file.expanduser()
        self.data_dir = data_dir.expanduser()
        self.repo_root = repo_root.expanduser()
        self.allow_repo_local_secret_storage = allow_repo_local_secret_storage

    def status(self) -> CredentialStoreStatus:
        try:
            self._load_or_initialize(create=False)
            return CredentialStoreStatus(
                available=True,
                status="available",
                kind="encrypted-vault",
                backend="encrypted-vault",
                display_path="<app-data>/settings/api-key-vault.json",
                guidance="Encrypted API key vault is configured.",
            )
        except CredentialStoreUnavailable as exc:
            return CredentialStoreStatus(
                available=False,
                status="unavailable",
                error=str(exc),
                kind="encrypted-vault",
                backend="encrypted-vault",
                display_path="<app-data>/settings/api-key-vault.json",
                guidance=(
                    "Encrypted-vault mode requires app data and the passphrase file to live outside "
                    "the Noofy repo checkout, with a readable passphrase file using 0600 permissions."
                ),
            )

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        vault, key = self._load_or_initialize(create=True)
        aesgcm = AESGCM(key)
        nonce = secrets.token_bytes(12)
        ciphertext = aesgcm.encrypt(nonce, secret.encode("utf-8"), provider.encode("utf-8"))
        entries = _vault_entries(vault)
        entries[provider] = {
            "nonce": _b64encode(nonce),
            "ciphertext": _b64encode(ciphertext),
        }
        vault["entries"] = entries
        self._write_vault(vault)

    def get_secret(self, provider: ApiKeyProvider) -> str | None:
        vault, key = self._load_or_initialize(create=False)
        entry = _vault_entries(vault).get(provider)
        if entry is None:
            return None
        try:
            nonce = _b64decode(entry["nonce"])
            ciphertext = _b64decode(entry["ciphertext"])
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, provider.encode("utf-8"))
        except (KeyError, TypeError, ValueError, InvalidTag) as exc:
            raise CredentialStoreUnavailable("Encrypted API key vault could not be decrypted.") from exc
        return plaintext.decode("utf-8")

    def delete_secret(self, provider: ApiKeyProvider) -> None:
        vault, _ = self._load_or_initialize(create=False)
        entries = _vault_entries(vault)
        entries.pop(provider, None)
        vault["entries"] = entries
        self._write_vault(vault)

    def _load_or_initialize(self, *, create: bool) -> tuple[dict[str, Any], bytes]:
        self._validate_paths()
        passphrase = self._read_passphrase()
        if not self.vault_path.exists():
            if not create:
                return self._new_vault(passphrase)
            vault = self._new_vault(passphrase)[0]
            return vault, self._derive_key(passphrase, _b64decode(vault["kdf"]["salt"]))

        try:
            raw = json.loads(self.vault_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CredentialStoreUnavailable("Encrypted API key vault could not be read.") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") != "1":
            raise CredentialStoreUnavailable("Encrypted API key vault has an unsupported format.")

        try:
            kdf = raw["kdf"]
            salt = _b64decode(kdf["salt"])
            key = self._derive_key(passphrase, salt)
            check = raw["check"]
            AESGCM(key).decrypt(
                _b64decode(check["nonce"]),
                _b64decode(check["ciphertext"]),
                b"vault-check",
            )
        except (KeyError, TypeError, ValueError, InvalidTag) as exc:
            raise CredentialStoreUnavailable("Encrypted API key vault could not be decrypted.") from exc
        return raw, key

    def _new_vault(self, passphrase: bytes) -> tuple[dict[str, Any], bytes]:
        salt = secrets.token_bytes(16)
        key = self._derive_key(passphrase, salt)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, ENCRYPTED_VAULT_CHECK_TEXT, b"vault-check")
        return {
            "schema_version": "1",
            "kdf": {
                "name": "scrypt",
                "salt": _b64encode(salt),
                "n": 2**14,
                "r": 8,
                "p": 1,
                "length": 32,
            },
            "check": {
                "nonce": _b64encode(nonce),
                "ciphertext": _b64encode(ciphertext),
            },
            "entries": {},
        }, key

    def _derive_key(self, passphrase: bytes, salt: bytes) -> bytes:
        return Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(passphrase)

    def _read_passphrase(self) -> bytes:
        if not self.passphrase_file.is_absolute():
            raise CredentialStoreUnavailable("Encrypted API key vault passphrase file must be an absolute path.")
        if not self.passphrase_file.exists() or not self.passphrase_file.is_file():
            raise CredentialStoreUnavailable("Encrypted API key vault passphrase file is missing.")
        if os.name == "posix":
            mode = stat.S_IMODE(self.passphrase_file.stat().st_mode)
            if mode & 0o077:
                raise CredentialStoreUnavailable("Encrypted API key vault passphrase file must use 0600 permissions.")
        try:
            passphrase = self.passphrase_file.read_bytes().rstrip(b"\r\n")
        except Exception as exc:
            raise CredentialStoreUnavailable("Encrypted API key vault passphrase file could not be read.") from exc
        if not passphrase:
            raise CredentialStoreUnavailable("Encrypted API key vault passphrase file is empty.")
        return passphrase

    def _validate_paths(self) -> None:
        if self._repo_local_paths_allowed():
            return
        if _is_relative_to(self.data_dir.resolve(strict=False), self.repo_root.resolve(strict=False)):
            raise CredentialStoreUnavailable(
                "Encrypted API key vault cannot use a Noofy data directory inside the repo checkout."
            )
        if _is_relative_to(self.vault_path.resolve(strict=False), self.repo_root.resolve(strict=False)):
            raise CredentialStoreUnavailable("Encrypted API key vault file cannot be inside the Noofy repo checkout.")
        if _is_relative_to(self.passphrase_file.resolve(strict=False), self.repo_root.resolve(strict=False)):
            raise CredentialStoreUnavailable(
                "Encrypted API key vault passphrase file cannot be inside the Noofy repo checkout."
            )

    def _repo_local_paths_allowed(self) -> bool:
        return self.allow_repo_local_secret_storage

    def _write_vault(self, vault: dict[str, Any]) -> None:
        try:
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.vault_path.with_suffix(f"{self.vault_path.suffix}.tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            fd = os.open(tmp, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(vault, file, indent=2, sort_keys=True)
            os.replace(tmp, self.vault_path)
            if os.name == "posix":
                self.vault_path.chmod(0o600)
        except Exception as exc:
            raise CredentialStoreUnavailable("Encrypted API key vault could not be written.") from exc


class UnavailableCredentialStore:
    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        self.message = message
        self.kind = kind

    def status(self) -> CredentialStoreStatus:
        return CredentialStoreStatus(
            available=False,
            status="unavailable",
            error=self.message,
            kind=self.kind,
            guidance="Check the API key storage configuration.",
        )

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        raise CredentialStoreUnavailable(self.message)

    def get_secret(self, provider: ApiKeyProvider) -> str | None:
        raise CredentialStoreUnavailable(self.message)

    def delete_secret(self, provider: ApiKeyProvider) -> None:
        raise CredentialStoreUnavailable(self.message)


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

    def get_key(self, provider: ApiKeyProvider) -> str | None:
        metadata = self.metadata_store.read().get(provider)
        if metadata is None or not metadata.configured:
            return None
        try:
            return self.credential_store.get_secret(provider)
        except CredentialStoreUnavailable:
            self._record("warning", "External API key could not be read", provider)
            return None

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


def create_credential_store(
    *,
    data_dir: Path,
    settings_dir: Path,
    env: dict[str, str] | None = None,
) -> CredentialStore:
    _env = env if env is not None else dict(os.environ)
    mode = _env.get("NOOFY_API_KEY_STORE", "os-keyring").strip().lower()
    if mode in {"", "os-keyring", "keyring"}:
        return KeyringCredentialStore()
    if mode == "encrypted-vault":
        passphrase_file = _env.get("NOOFY_API_KEY_VAULT_PASSPHRASE_FILE")
        if not passphrase_file:
            return UnavailableCredentialStore(
                "Encrypted API key vault requires NOOFY_API_KEY_VAULT_PASSPHRASE_FILE.",
                kind="encrypted-vault",
            )
        return EncryptedVaultCredentialStore(
            vault_path=settings_dir / ENCRYPTED_VAULT_FILENAME,
            passphrase_file=Path(passphrase_file),
            data_dir=data_dir,
            allow_repo_local_secret_storage=_truthy(_env.get("NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE")),
        )
    return UnavailableCredentialStore(
        "Unknown API key credential store mode.",
        kind=mode or "unknown",
    )


def _vault_entries(vault: dict[str, Any]) -> dict[str, Any]:
    entries = vault.get("entries")
    if isinstance(entries, dict):
        return entries
    return {}


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
