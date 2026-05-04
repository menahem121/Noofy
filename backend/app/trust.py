from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.runtime.isolation import TrustLevel
from app.workflows.package import SignedRegistryMetadata, WorkflowPackage, WorkflowPackageSignature

TRUST_SIGNATURE_PAYLOAD_SCHEMA_VERSION = "0.1.0"
TRUST_KEYRING_SCHEMA_VERSION = "0.1.0"


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
        label="Quarantined Community",
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
    level = trust_level_from_string(identity.trust_level if identity else "noofy_verified")
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


class TrustSignaturePurpose(StrEnum):
    PACKAGE = "package"
    REGISTRY = "registry"
    BOTH = "both"


class TrustedSignatureKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key_id: str = Field(min_length=1)
    algorithm: str = "hmac-sha256"
    secret: str = Field(min_length=1)
    purpose: TrustSignaturePurpose = TrustSignaturePurpose.BOTH


class TrustKeyring(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = TRUST_KEYRING_SCHEMA_VERSION
    keys: list[TrustedSignatureKey] = Field(default_factory=list)


class TrustVerificationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    VERIFIED = "verified"
    MISSING_SIGNATURE = "missing_signature"
    MISSING_SIGNED_REGISTRY_METADATA = "missing_signed_registry_metadata"
    UNKNOWN_KEY = "unknown_key"
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
    def __init__(self, keys: list[TrustedSignatureKey] | None = None) -> None:
        self.keys_by_id = {key.key_id: key for key in keys or []}

    def policy_payload(self) -> dict[str, Any]:
        return {
            "schema_version": TRUST_KEYRING_SCHEMA_VERSION,
            "signature_payload_schema_version": TRUST_SIGNATURE_PAYLOAD_SCHEMA_VERSION,
            "trusted_key_count": len(self.keys_by_id),
            "trusted_keys": [
                {
                    "key_id": key.key_id,
                    "algorithm": key.algorithm,
                    "purpose": key.purpose.value,
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
        if requested_trust_level in {TrustLevel.QUARANTINED_COMMUNITY, TrustLevel.UNSUPPORTED}:
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
        if not signed_registry_metadata.key_id or not signed_registry_metadata.algorithm:
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
        if key.algorithm != algorithm or algorithm != "hmac-sha256":
            return TrustVerificationResult(
                requested_trust_level=requested_trust_level,
                effective_trust_level=TrustLevel.UNSUPPORTED,
                status=TrustVerificationStatus.UNSUPPORTED_ALGORITHM,
                key_id=key_id,
                algorithm=algorithm,
                evidence_type=evidence_type,
                developer_details={**details, "trusted_key_algorithm": key.algorithm},
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
        expected = hmac_sha256_signature(payload, key.secret)
        if not hmac.compare_digest(_strip_hmac_prefix(signature), expected):
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
        "package_payload_hash": _canonical_json_sha256(package_payload),
    }


def hmac_sha256_signature(payload: dict[str, Any], secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        _canonical_json_bytes(payload),
        hashlib.sha256,
    ).hexdigest()


def load_trust_verifier(
    path: Path,
    *,
    log_store: Any | None = None,
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
    return TrustVerifier(keyring.keys)


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


def _strip_hmac_prefix(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("hmac-sha256:"):
        return stripped.removeprefix("hmac-sha256:")
    return stripped


def _package_trust_verification_status(package: WorkflowPackage) -> str | None:
    if package.import_metadata is None:
        return None
    value = package.import_metadata.developer_details.get("trust_verification")
    if isinstance(value, dict):
        status = value.get("status")
        if isinstance(status, str):
            return status
    return None
