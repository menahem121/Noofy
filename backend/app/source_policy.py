from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

SHA256_PATTERN = r"^(sha256:)?[0-9a-fA-F]{64}$"
SOURCE_POLICY_VERSION = "phase6-local-0.1"


class PackageSourceType(StrEnum):
    BUNDLED = "bundled"
    NOOFY_ARCHIVE_IMPORT = "noofy_archive_import"
    REGISTRY = "registry"
    UNKNOWN = "unknown"


class ModelSourceTrust(StrEnum):
    NONE = "none"
    FILENAME_ONLY = "filename_only"
    FILENAME_SIZE = "filename_size"
    HASHED = "hashed"
    MIXED = "mixed"


class SourcePolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_version: str = SOURCE_POLICY_VERSION
    trust_level: str = Field(min_length=1)
    source_policy: str = Field(min_length=1)
    package_source_type: PackageSourceType = PackageSourceType.UNKNOWN
    automatic_preparation_allowed: bool
    allowed_registry_origins: list[str] = Field(default_factory=list)
    allowed_source_origins: list[str] = Field(default_factory=list)
    allowed_model_origins: list[str] = Field(default_factory=list)
    registry_id: str | None = None
    registry_snapshot_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    model_source_trust: ModelSourceTrust = ModelSourceTrust.NONE
    community_preparation_opt_in_required: bool = False
    community_preparation_opted_in: bool = False
    trust_verification_status: str | None = None
    policy_status: str = "active"
