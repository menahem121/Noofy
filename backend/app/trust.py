from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.artifacts import ModelVerificationLevel
from app.engine.diagnostics import DiagnosticsSink
from app.runtime.isolation import (
    CapsuleLock,
    TrustLevel,
)
from app.source_policy import (
    SOURCE_POLICY_VERSION,
    ModelSourceTrust,
    PackageSourceType,
    SourcePolicy,
)
from app.workflows.package import (
    SignedRegistryMetadata,
    WorkflowPackage,
    WorkflowPackageSignature,
)

TRUST_SIGNATURE_PAYLOAD_SCHEMA_VERSION = "0.1.0"
TRUST_KEYRING_SCHEMA_VERSION = "0.1.0"
ED25519_ALGORITHM = "ed25519"
HMAC_SHA256_ALGORITHM = "hmac-sha256"
SUPPORTED_SIGNATURE_ALGORITHMS = {ED25519_ALGORITHM, HMAC_SHA256_ALGORITHM}


@dataclass(frozen=True)
class TrustLevelCopy:
    label: str
    summary: str
    badge_tone: str
    can_prepare_automatically: bool
    requires_explicit_opt_in: bool
    source_policy: str


TRUST_LEVEL_COPY: dict[TrustLevel, TrustLevelCopy] = {
    TrustLevel.NOOFY_VERIFIED: TrustLevelCopy(
        label="Noofy Verified",
        summary="Built or reviewed for Noofy's managed runtime.",
        badge_tone="verified",
        can_prepare_automatically=True,
        requires_explicit_opt_in=False,
        source_policy="noofy_verified_sources_only",
    ),
    TrustLevel.REGISTRY_LOCKED: TrustLevelCopy(
        label="Registry Locked",
        summary="Uses pinned sources from trusted registry metadata.",
        badge_tone="locked",
        can_prepare_automatically=True,
        requires_explicit_opt_in=False,
        source_policy="signed_registry_or_pinned_registry_sources",
    ),
    TrustLevel.QUARANTINED_COMMUNITY: TrustLevelCopy(
        label="Community",
        summary="Community workflow prepared only after permission and isolated resolution.",
        badge_tone="community",
        can_prepare_automatically=True,
        requires_explicit_opt_in=True,
        source_policy="explicit_opt_in_and_isolated_capsule_required",
    ),
    TrustLevel.UNSUPPORTED: TrustLevelCopy(
        label="Unsupported",
        summary="Noofy cannot prepare this workflow automatically.",
        badge_tone="unsupported",
        can_prepare_automatically=False,
        requires_explicit_opt_in=False,
        source_policy="blocked",
    ),
}


def trust_level_from_string(value: str | None) -> TrustLevel:
    if value in {item.value for item in TrustLevel}:
        return TrustLevel(value)
    return TrustLevel.UNSUPPORTED


def workflow_trust_payload(package: WorkflowPackage) -> dict[str, Any]:
    identity = package.identity
    level = trust_level_from_string(
        identity.trust_level if identity else "noofy_verified"
    )
    copy = TRUST_LEVEL_COPY[level]
    signatures = list(identity.signatures if identity else [])
    signature = identity.signature if identity else None
    signed_registry_metadata = identity.signed_registry_metadata if identity else None
    signature_present = bool(signature or signatures or signed_registry_metadata)
    verification_status = _package_trust_verification_status(package)

    return {
        "level": level.value,
        "label": copy.label,
        "summary": copy.summary,
        "badge_tone": copy.badge_tone,
        "can_prepare_automatically": copy.can_prepare_automatically,
        "requires_explicit_opt_in": copy.requires_explicit_opt_in,
        "source_policy": copy.source_policy,
        "signature_status": _signature_status(
            level=level,
            source=identity.source if identity else "bundled",
            signature_present=signature_present,
            signed_registry_metadata_present=signed_registry_metadata is not None,
            verification_status=verification_status,
        ),
    }


def workflow_source_policy(
    package: WorkflowPackage,
    *,
    community_preparation_opted_in: bool = False,
    policy_status: str = "active",
) -> SourcePolicy:
    identity = package.identity
    level = trust_level_from_string(
        identity.trust_level if identity else "noofy_verified"
    )
    copy = TRUST_LEVEL_COPY[level]
    signed_registry_metadata = identity.signed_registry_metadata if identity else None
    verification_status = _package_trust_verification_status(package)
    package_source_type = _package_source_type(
        identity.source if identity else "bundled"
    )
    automatic_preparation_allowed = copy.can_prepare_automatically and (
        level is not TrustLevel.QUARANTINED_COMMUNITY or community_preparation_opted_in
    )
    return SourcePolicy(
        trust_level=level,
        source_policy=copy.source_policy,
        package_source_type=package_source_type,
        automatic_preparation_allowed=automatic_preparation_allowed,
        allowed_registry_origins=_allowed_registry_origins(
            level, signed_registry_metadata
        ),
        allowed_source_origins=_allowed_source_origins(level, signed_registry_metadata),
        allowed_model_origins=_allowed_model_origins(level),
        registry_id=(
            signed_registry_metadata.registry_id
            if signed_registry_metadata is not None
            else None
        ),
        registry_snapshot_hash=(
            signed_registry_metadata.snapshot_hash
            if signed_registry_metadata is not None
            else None
        ),
        model_source_trust=_model_source_trust(package),
        community_preparation_opt_in_required=copy.requires_explicit_opt_in,
        community_preparation_opted_in=community_preparation_opted_in,
        trust_verification_status=verification_status,
        policy_status=policy_status,
    )


def capsule_source_policy(capsule_lock: CapsuleLock) -> SourcePolicy:
    if capsule_lock.source_policy is not None:
        return capsule_lock.source_policy
    level = capsule_lock.trust.level
    copy = TRUST_LEVEL_COPY[level]
    source = capsule_lock.workflow.source or "unknown"
    automatic_preparation_allowed = copy.can_prepare_automatically
    return SourcePolicy(
        trust_level=level,
        source_policy=copy.source_policy,
        package_source_type=_package_source_type(source),
        automatic_preparation_allowed=automatic_preparation_allowed,
        allowed_source_origins=_allowed_source_origins(level, None),
        allowed_model_origins=_allowed_model_origins(level),
        model_source_trust=(
            ModelSourceTrust.HASHED if capsule_lock.models else ModelSourceTrust.NONE
        ),
        community_preparation_opt_in_required=copy.requires_explicit_opt_in,
        community_preparation_opted_in=automatic_preparation_allowed
        and level is TrustLevel.QUARANTINED_COMMUNITY,
    )


class TrustSignaturePurpose(StrEnum):
    PACKAGE = "package"
    REGISTRY = "registry"
    BOTH = "both"


class TrustedSignatureKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    algorithm: str = HMAC_SHA256_ALGORITHM
    secret: str | None = Field(default=None, min_length=1)
    public_key: str | None = Field(default=None, min_length=1)
    purpose: TrustSignaturePurpose = TrustSignaturePurpose.BOTH
    revoked: bool = False
    not_before: datetime | None = None
    expires_at: datetime | None = None
    policy_versions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_key_material(self) -> "TrustedSignatureKey":
        if self.algorithm == HMAC_SHA256_ALGORITHM and not self.secret:
            raise ValueError("hmac-sha256 trust keys require a secret")
        if self.algorithm == ED25519_ALGORITHM:
            if not self.public_key:
                raise ValueError("ed25519 trust keys require a public_key")
            if self.secret is not None:
                raise ValueError("ed25519 trust keys must not include signing secrets")
        return self


class TrustKeyring(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = TRUST_KEYRING_SCHEMA_VERSION
    allow_development_hmac: bool = False
    keys: list[TrustedSignatureKey] = Field(default_factory=list)


class TrustVerificationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    VERIFIED = "verified"
    MISSING_SIGNATURE = "missing_signature"
    MISSING_SIGNED_REGISTRY_METADATA = "missing_signed_registry_metadata"
    UNKNOWN_KEY = "unknown_key"
    REVOKED_KEY = "revoked_key"
    EXPIRED_KEY = "expired_key"
    KEY_NOT_YET_VALID = "key_not_yet_valid"
    POLICY_VERSION_MISMATCH = "policy_version_mismatch"
    UNSUPPORTED_ALGORITHM = "unsupported_algorithm"
    INVALID_SIGNATURE = "invalid_signature"


class TrustVerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    requested_trust_level: TrustLevel
    effective_trust_level: TrustLevel
    status: TrustVerificationStatus
    verified: bool = False
    key_id: str | None = None
    algorithm: str | None = None
    evidence_type: str | None = None
    developer_details: dict[str, object] = Field(default_factory=dict)


class TrustVerifier:
    def __init__(
        self,
        keys: list[TrustedSignatureKey] | None = None,
        *,
        allow_development_hmac: bool = False,
        current_time: datetime | None = None,
    ) -> None:
        self.keys_by_id = {key.key_id: key for key in keys or []}
        self.allow_development_hmac = allow_development_hmac
        self.current_time = current_time

    def policy_payload(self) -> dict[str, Any]:
        return {
            "schema_version": TRUST_KEYRING_SCHEMA_VERSION,
            "signature_payload_schema_version": TRUST_SIGNATURE_PAYLOAD_SCHEMA_VERSION,
            "development_hmac_allowed": self.allow_development_hmac,
            "trusted_key_count": len(self.keys_by_id),
            "trusted_keys": [
                {
                    "key_id": key.key_id,
                    "algorithm": key.algorithm,
                    "purpose": key.purpose.value,
                    "revoked": key.revoked,
                    "not_before": _datetime_payload(key.not_before),
                    "expires_at": _datetime_payload(key.expires_at),
                    "policy_versions": list(key.policy_versions),
                }
                for key in sorted(self.keys_by_id.values(), key=lambda key: key.key_id)
            ],
            "trust_levels": {
                level.value: {
                    "label": copy.label,
                    "summary": copy.summary,
                    "source_policy": copy.source_policy,
                    "requires_explicit_opt_in": copy.requires_explicit_opt_in,
                    "can_prepare_automatically": copy.can_prepare_automatically,
                }
                for level, copy in TRUST_LEVEL_COPY.items()
            },
        }

    def verify_imported_package(
        self,
        *,
        requested_trust_level: TrustLevel,
        payload: dict[str, Any],
        signatures: list[WorkflowPackageSignature],
        signed_registry_metadata: SignedRegistryMetadata | None,
    ) -> TrustVerificationResult:
        if requested_trust_level in {
            TrustLevel.QUARANTINED_COMMUNITY,
            TrustLevel.UNSUPPORTED,
        }:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=requested_trust_level,
                status=TrustVerificationStatus.NOT_REQUIRED,
                verified=False,
            )
        if requested_trust_level is TrustLevel.NOOFY_VERIFIED:
            return self._verify_package_signature(
                requested_trust_level=requested_trust_level,
                payload=payload,
                signatures=signatures,
            )
        if requested_trust_level is TrustLevel.REGISTRY_LOCKED:
            return self._verify_registry_metadata(
                requested_trust_level=requested_trust_level,
                payload=payload,
                signed_registry_metadata=signed_registry_metadata,
            )
        return TrustVerificationResult(
            requested_trust_level=requested_trust_level,
            effective_trust_level=TrustLevel.UNSUPPORTED,
            status=TrustVerificationStatus.UNSUPPORTED_ALGORITHM,
            developer_details={"reason": "unknown_trust_level"},
        )

    def _verify_package_signature(
        self,
        *,
        requested_trust_level: TrustLevel,
        payload: dict[str, Any],
        signatures: list[WorkflowPackageSignature],
    ) -> TrustVerificationResult:
        if not signatures:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.MISSING_SIGNATURE,
                evidence_type="package_signature",
            )
        for signature in signatures:
            result = self._verify_signature(
                requested_trust_level=requested_trust_level,
                effective_trust_level=requested_trust_level,
                payload=payload,
                key_id=signature.key_id,
                algorithm=signature.algorithm,
                signature=signature.value,
                purpose=TrustSignaturePurpose.PACKAGE,
                evidence_type="package_signature",
            )
            if result.verified:
                return result
        return result

    def _verify_registry_metadata(
        self,
        *,
        requested_trust_level: TrustLevel,
        payload: dict[str, Any],
        signed_registry_metadata: SignedRegistryMetadata | None,
    ) -> TrustVerificationResult:
        if signed_registry_metadata is None:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.MISSING_SIGNED_REGISTRY_METADATA,
                evidence_type="signed_registry_metadata",
            )
        if (
            not signed_registry_metadata.key_id
            or not signed_registry_metadata.algorithm
        ):
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.MISSING_SIGNED_REGISTRY_METADATA,
                evidence_type="signed_registry_metadata",
                developer_details={"reason": "missing_key_id_or_algorithm"},
            )
        registry_payload = registry_signature_payload(
            package_payload=payload,
            registry_id=signed_registry_metadata.registry_id,
            snapshot_hash=signed_registry_metadata.snapshot_hash,
        )
        return self._verify_signature(
            requested_trust_level=requested_trust_level,
            effective_trust_level=requested_trust_level,
            payload=registry_payload,
            key_id=signed_registry_metadata.key_id,
            algorithm=signed_registry_metadata.algorithm,
            signature=signed_registry_metadata.signature,
            purpose=TrustSignaturePurpose.REGISTRY,
            evidence_type="signed_registry_metadata",
            extra_details={
                "registry_id": signed_registry_metadata.registry_id,
                "snapshot_hash": signed_registry_metadata.snapshot_hash,
            },
        )

    def _verify_signature(
        self,
        *,
        requested_trust_level: TrustLevel,
        effective_trust_level: TrustLevel,
        payload: dict[str, Any],
        key_id: str,
        algorithm: str,
        signature: str,
        purpose: TrustSignaturePurpose,
        evidence_type: str,
        extra_details: dict[str, object] | None = None,
    ) -> TrustVerificationResult:
        key = self.keys_by_id.get(key_id)
        details = extra_details or {}
        if key is None:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.UNKNOWN_KEY,
                key_id=key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details=details,
            )
        if (
            key.algorithm != algorithm
            or algorithm not in SUPPORTED_SIGNATURE_ALGORITHMS
        ):
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.UNSUPPORTED_ALGORITHM,
                key_id=key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details={**details, "trusted_key_algorithm": key.algorithm},
            )
        if key.algorithm == HMAC_SHA256_ALGORITHM and not self.allow_development_hmac:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.UNSUPPORTED_ALGORITHM,
                key_id=key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details={**details, "reason": "development_hmac_disabled"},
            )
        if key.purpose not in {purpose, TrustSignaturePurpose.BOTH}:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.UNKNOWN_KEY,
                key_id=key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details={**details, "reason": "key_purpose_mismatch"},
            )
        lifecycle_result = self._verify_key_lifecycle(
            requested_trust_level=requested_trust_level,
            key=key,
            payload=payload,
            algorithm=algorithm,
            evidence_type=evidence_type,
            details=details,
        )
        if lifecycle_result is not None:
            return lifecycle_result
        verified = False
        if algorithm == HMAC_SHA256_ALGORITHM and key.secret is not None:
            expected = hmac_sha256_signature(payload, key.secret)
            verified = hmac.compare_digest(_strip_hmac_prefix(signature), expected)
        elif algorithm == ED25519_ALGORITHM and key.public_key is not None:
            verified = ed25519_verify_signature(
                payload, public_key=key.public_key, signature=signature
            )
        if not verified:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.INVALID_SIGNATURE,
                key_id=key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details=details,
            )
        return TrustVerificationResult(
            requested_trust_level=requested_trust_level,
            effective_trust_level=effective_trust_level,
            status=TrustVerificationStatus.VERIFIED,
            verified=True,
            key_id=key_id,
            algorithm=algorithm,
            evidence_type=evidence_type,
            developer_details=details,
        )

    def _verify_key_lifecycle(
        self,
        *,
        requested_trust_level: TrustLevel,
        key: TrustedSignatureKey,
        payload: dict[str, Any],
        algorithm: str,
        evidence_type: str,
        details: dict[str, object],
    ) -> TrustVerificationResult | None:
        now = (
            _coerce_utc(self.current_time)
            if self.current_time is not None
            else datetime.now(UTC)
        )
        if key.revoked:
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.REVOKED_KEY,
                key_id=key.key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details=details,
            )
        if key.not_before is not None and now < _coerce_utc(key.not_before):
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.KEY_NOT_YET_VALID,
                key_id=key.key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details={
                    **details,
                    "not_before": _datetime_payload(key.not_before),
                },
            )
        if key.expires_at is not None and now >= _coerce_utc(key.expires_at):
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.EXPIRED_KEY,
                key_id=key.key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details={
                    **details,
                    "expires_at": _datetime_payload(key.expires_at),
                },
            )
        policy_version = _signature_payload_policy_version(payload)
        if key.policy_versions and policy_version not in set(key.policy_versions):
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.POLICY_VERSION_MISMATCH,
                key_id=key.key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details={
                    **details,
                    "signature_policy_version": policy_version,
                    "trusted_key_policy_versions": list(key.policy_versions),
                },
            )
        return None


def imported_archive_trust_payload(
    *,
    package_json: dict[str, Any],
    comfyui_graph: dict[str, Any],
    dashboard_json: dict[str, Any],
    capsule_json: dict[str, Any],
    export_report: dict[str, Any],
) -> dict[str, Any]:
    package_without_evidence = dict(package_json)
    for key in ("signature", "signatures", "signed_registry_metadata"):
        package_without_evidence.pop(key, None)
    return {
        "schema_version": TRUST_SIGNATURE_PAYLOAD_SCHEMA_VERSION,
        "package": _canonical_json_value(package_without_evidence),
        "content_hashes": {
            "comfyui_graph": _canonical_json_sha256(comfyui_graph),
            "dashboard": _canonical_json_sha256(dashboard_json),
            "capsule_lock": _canonical_json_sha256(capsule_json),
            "export_report": _canonical_json_sha256(export_report),
        },
    }


def registry_signature_payload(
    *,
    package_payload: dict[str, Any],
    registry_id: str,
    snapshot_hash: str,
) -> dict[str, Any]:
    return {
        "schema_version": TRUST_SIGNATURE_PAYLOAD_SCHEMA_VERSION,
        "registry_id": registry_id,
        "snapshot_hash": snapshot_hash,
        "source_policy_version": _signature_payload_policy_version(package_payload),
        "package_payload_hash": _canonical_json_sha256(package_payload),
    }


def hmac_sha256_signature(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        canonical_trust_payload_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def canonical_trust_payload_bytes(payload: dict[str, Any]) -> bytes:
    return _canonical_json_bytes(payload)


def ed25519_verify_signature(
    payload: dict[str, Any], *, public_key: str, signature: str
) -> bool:
    try:
        public_key_bytes = _decode_signature_material(
            _strip_algorithm_prefix(public_key, ED25519_ALGORITHM),
            expected_len=32,
        )
        signature_bytes = _decode_signature_material(
            _strip_algorithm_prefix(signature, ED25519_ALGORITHM),
            expected_len=64,
        )
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature_bytes,
            canonical_trust_payload_bytes(payload),
        )
    except (ValueError, InvalidSignature):
        return False
    return True


def load_trust_verifier(
    path: Path,
    *,
    log_store: DiagnosticsSink | None = None,
) -> TrustVerifier:
    if not path.exists():
        return TrustVerifier()
    try:
        with path.open("r", encoding="utf-8") as file:
            keyring = TrustKeyring.model_validate(json.load(file))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        if log_store is not None:
            log_store.add(
                "warning",
                "Trust keyring could not be loaded",
                "trust.keyring",
                details={
                    "path": str(path),
                    "error": str(exc),
                },
            )
        return TrustVerifier()
    if keyring.schema_version != TRUST_KEYRING_SCHEMA_VERSION:
        if log_store is not None:
            log_store.add(
                "warning",
                "Trust keyring schema version is unsupported",
                "trust.keyring",
                details={
                    "path": str(path),
                    "schema_version": keyring.schema_version,
                    "supported_schema_version": TRUST_KEYRING_SCHEMA_VERSION,
                },
            )
        return TrustVerifier()
    return TrustVerifier(
        keyring.keys,
        allow_development_hmac=keyring.allow_development_hmac
        or _development_hmac_enabled_from_env(),
    )


def _signature_status(
    *,
    level: TrustLevel,
    source: str | None,
    signature_present: bool,
    signed_registry_metadata_present: bool,
    verification_status: str | None,
) -> str:
    if source == "bundled":
        return "bundled_trusted_core"
    if verification_status:
        return verification_status
    if signed_registry_metadata_present:
        return "signed_registry_metadata"
    if signature_present:
        return "signed_package_metadata"
    if level is TrustLevel.UNSUPPORTED:
        return "not_applicable"
    return "missing"


def _canonical_json_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _canonical_json_value(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_json_value(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, sort_keys=True))


def _decode_signature_material(value: str, *, expected_len: int) -> bytes:
    stripped = value.strip()
    for prefix in ("base64:", "hex:"):
        if stripped.startswith(prefix):
            stripped = stripped.removeprefix(prefix)
            break
    if len(stripped) == expected_len * 2:
        try:
            decoded = bytes.fromhex(stripped)
        except ValueError:
            decoded = b""
        if len(decoded) == expected_len:
            return decoded
    try:
        decoded = base64.b64decode(stripped, validate=True)
    except binascii.Error as exc:
        raise ValueError("signature material is not valid base64 or hex") from exc
    if len(decoded) != expected_len:
        raise ValueError("signature material has the wrong length")
    return decoded


def _strip_algorithm_prefix(value: str, algorithm: str) -> str:
    stripped = value.strip()
    prefix = f"{algorithm}:"
    if stripped.startswith(prefix):
        return stripped.removeprefix(prefix)
    return stripped


def _strip_hmac_prefix(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith(f"{HMAC_SHA256_ALGORITHM}:"):
        return stripped.removeprefix(f"{HMAC_SHA256_ALGORITHM}:")
    return stripped


def _signature_payload_policy_version(payload: dict[str, Any]) -> str:
    version = payload.get("source_policy_version")
    if isinstance(version, str) and version:
        return version
    package = payload.get("package")
    if isinstance(package, dict):
        source_policy = package.get("source_policy")
        if isinstance(source_policy, dict):
            policy_version = source_policy.get("policy_version")
            if isinstance(policy_version, str) and policy_version:
                return policy_version
    return SOURCE_POLICY_VERSION


def _datetime_payload(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _coerce_utc(value).isoformat()


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _development_hmac_enabled_from_env() -> bool:
    return os.environ.get("NOOFY_ALLOW_HMAC_TRUST_KEYS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _package_trust_verification_status(package: WorkflowPackage) -> str | None:
    if package.import_metadata is None:
        return None
    value = package.import_metadata.developer_details.get("trust_verification")
    if isinstance(value, dict):
        status = value.get("status")
        if isinstance(status, str):
            return status
    return None


def _package_source_type(value: str | None) -> PackageSourceType:
    if value == "bundled":
        return PackageSourceType.BUNDLED
    if value == "noofy_archive_import":
        return PackageSourceType.NOOFY_ARCHIVE_IMPORT
    if value == "registry":
        return PackageSourceType.REGISTRY
    return PackageSourceType.UNKNOWN


def _allowed_registry_origins(
    level: TrustLevel,
    signed_registry_metadata: SignedRegistryMetadata | None,
) -> list[str]:
    if signed_registry_metadata is not None:
        return [signed_registry_metadata.registry_id]
    if level is TrustLevel.NOOFY_VERIFIED:
        return ["noofy-verified"]
    return []


def _allowed_source_origins(
    level: TrustLevel,
    signed_registry_metadata: SignedRegistryMetadata | None,
) -> list[str]:
    if level is TrustLevel.UNSUPPORTED:
        return []
    if level is TrustLevel.NOOFY_VERIFIED:
        return ["noofy-verified"]
    if level is TrustLevel.REGISTRY_LOCKED:
        return (
            [signed_registry_metadata.registry_id]
            if signed_registry_metadata is not None
            else ["registry-locked"]
        )
    if level is TrustLevel.QUARANTINED_COMMUNITY:
        return ["explicit-metadata", "registry-locked"]
    return ["explicit-metadata"]


def _allowed_model_origins(level: TrustLevel) -> list[str]:
    if level is TrustLevel.UNSUPPORTED:
        return []
    if level is TrustLevel.NOOFY_VERIFIED:
        return ["hashed-download", "huggingface.co", "noofy-verified", "user-local"]
    if level is TrustLevel.REGISTRY_LOCKED:
        return ["hashed-download", "huggingface.co", "registry-locked", "user-local"]
    return ["hashed-download", "huggingface.co", "user-local"]


def _model_source_trust(package: WorkflowPackage) -> ModelSourceTrust:
    if not package.required_models:
        return ModelSourceTrust.NONE
    levels = {model.verification_level for model in package.required_models}
    if levels <= {ModelVerificationLevel.SHA256_SIZE}:
        return ModelSourceTrust.HASHED
    if levels <= {ModelVerificationLevel.FILENAME_SIZE}:
        return ModelSourceTrust.FILENAME_SIZE
    if levels <= {ModelVerificationLevel.FILENAME_ONLY}:
        return ModelSourceTrust.FILENAME_ONLY
    return ModelSourceTrust.MIXED
