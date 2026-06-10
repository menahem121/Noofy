from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.runtime.comfyui.launch_settings import (
    DEFAULT_COMFYUI_ATTENTION_BACKEND,
    DEFAULT_COMFYUI_PRECISION_POLICY,
    DEFAULT_COMFYUI_PREVIEW_METHOD,
    DEFAULT_COMFYUI_PREVIEW_SIZE,
)
from app.source_policy import SourcePolicy

SHA256_PATTERN = r"^(sha256:)?[0-9a-fA-F]{64}$"


def _validate_relative_path(value: str, *, field_name: str, allow_nested: bool) -> str:
    if "\\" in value:
        raise ValueError(f"{field_name} must not contain path separators")
    parts = value.split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{field_name} must be a safe relative path")
    if not allow_nested and len(parts) != 1:
        raise ValueError(f"{field_name} must be a filename, not a path")
    return value


class TrustLevel(StrEnum):
    NOOFY_VERIFIED = "noofy_verified"
    REGISTRY_LOCKED = "registry_locked"
    QUARANTINED_COMMUNITY = "quarantined_community"
    UNSUPPORTED = "unsupported"


class InstallStatus(StrEnum):
    PENDING = "pending"
    IMPORTED = "imported"
    NEEDS_INPUT_SETUP = "needs_input_setup"
    PREPARING = "preparing"
    RESOLVING_RUNTIME_PROFILE = "resolving_runtime_profile"
    RESOLVING_MODELS = "resolving_models"
    RESOLVING_DEPENDENCIES = "resolving_dependencies"
    MATERIALIZING_CUSTOM_NODES = "materializing_custom_nodes"
    MATERIALIZING_MODEL_VIEW = "materializing_model_view"
    DOWNLOADING = "downloading"
    CHECKING_COMPATIBILITY = "checking_compatibility"
    SMOKE_TESTING = "smoke_testing"
    READY = "ready"
    PREPARED_NEEDS_INPUT_SETUP = "prepared_needs_input_setup"
    CANNOT_PREPARE_AUTOMATICALLY = "cannot_prepare_automatically"
    UNSUPPORTED_RUNTIME_PROFILE = "unsupported_runtime_profile"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class SmokeTestStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"


class SmokeStageStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class SmokeStageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SmokeStageStatus = SmokeStageStatus.NOT_RUN
    message: str | None = None
    details: dict[str, object] = Field(default_factory=dict)


class SmokeTestReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dependency_env: SmokeStageResult = Field(default_factory=SmokeStageResult)
    custom_node_import: SmokeStageResult = Field(default_factory=SmokeStageResult)
    runner_health: SmokeStageResult = Field(default_factory=SmokeStageResult)
    workflow_execution: SmokeStageResult = Field(default_factory=SmokeStageResult)


class PackageIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    publisher_id: str = Field(min_length=1)
    package_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    trust_level: TrustLevel
    source: str | None = None
    signature: str | None = None


class RuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_variant_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    runtime_profile_catalog_version: str = Field(min_length=1)
    fingerprint_schema_version: str = Field(min_length=1)
    dependency_env_fingerprint: str = Field(min_length=1)
    runner_fingerprint: str = Field(min_length=1)
    runner_process_compatibility_key: str | None = None
    capsule_fingerprint: str = Field(min_length=1)
    preview_method: str = DEFAULT_COMFYUI_PREVIEW_METHOD
    preview_size: int = DEFAULT_COMFYUI_PREVIEW_SIZE
    vram_mode: str = "auto"
    attention_backend: str = DEFAULT_COMFYUI_ATTENTION_BACKEND
    precision_policy: str = DEFAULT_COMFYUI_PRECISION_POLICY
    noofy_environment: dict[str, str] = Field(default_factory=dict)
    os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    python_build_id: str = Field(min_length=1)
    gpu_backend: str = Field(min_length=1)
    dependency_lock_hash: str = Field(min_length=1)
    runner_workspace_hash: str = Field(min_length=1)


class EngineIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["comfyui"]
    comfyui_version: str = Field(min_length=1)
    core_source_hash: str = Field(min_length=1)


class CustomNodeLock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_ref: str | None = None
    source_content_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    source_cache_ref: str | None = None
    commit: str | None = None
    version: str | None = None
    trust_level: TrustLevel
    node_types: list[str] = Field(default_factory=list)

    @field_validator("source_cache_ref")
    @classmethod
    def _validate_source_cache_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_relative_path(value, field_name="source_cache_ref", allow_nested=True)


class DependencyLock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    lock_file: str = Field(min_length=1)
    install_policy: str = Field(min_length=1)


class ModelLock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(gt=0)
    source_urls: list[str] = Field(default_factory=list)
    comfyui_folder: str = Field(min_length=1)
    filename: str = Field(min_length=1)

    @field_validator("comfyui_folder")
    @classmethod
    def _validate_comfyui_folder(cls, value: str) -> str:
        return _validate_relative_path(value, field_name="comfyui_folder", allow_nested=True)

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        return _validate_relative_path(value, field_name="filename", allow_nested=False)


class InstalledModelReference(BaseModel):
    """Mutable local reference from an installed workflow to a model asset.

    This records what the current machine resolved for a model requirement.
    It is intentionally separate from immutable capsule model locks so later
    install and cleanup code can distinguish Noofy-owned blobs from user-owned
    local files.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    requirement_id: str = Field(min_length=1)
    comfyui_folder: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    verification_level: ModelVerificationLevel
    asset_ownership: AssetOwnership
    model_id: str | None = None
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    size_bytes: int | None = Field(default=None, gt=0)
    store_ref: str | None = None
    blob_path: str | None = None
    materialized_path: str | None = None
    materialization_strategy: str | None = None
    materialized_file_verified: bool | None = None
    source_path: str | None = None

    @field_validator("comfyui_folder")
    @classmethod
    def _validate_comfyui_folder(cls, value: str) -> str:
        return _validate_relative_path(value, field_name="comfyui_folder", allow_nested=True)

    @field_validator("filename")
    @classmethod
    def _validate_filename(cls, value: str) -> str:
        return _validate_relative_path(value, field_name="filename", allow_nested=False)


class HardwareObservations(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    observed_peak_vram_mb: int | None = Field(default=None, ge=0)
    observed_peak_ram_mb: int | None = Field(default=None, ge=0)
    tested_resolution: str | None = None
    tested_batch_size: int | None = Field(default=None, ge=1)
    gpu_name: str | None = None
    os: str | None = None
    backend: str | None = None
    precision: str | None = None
    recommended_vram_mb: int | None = Field(default=None, ge=0)
    recommended_ram_mb: int | None = Field(default=None, ge=0)


class TrustMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: TrustLevel
    publisher: str | None = None
    signatures: list[str] = Field(default_factory=list)


class CapsuleLock(BaseModel):
    """Immutable resolved runtime facts for a workflow capsule."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    workflow: PackageIdentity
    engine: EngineIdentity
    runtime: RuntimeIdentity
    custom_nodes: list[CustomNodeLock] = Field(default_factory=list)
    dependencies: DependencyLock
    models: list[ModelLock] = Field(default_factory=list)
    hardware_observations: HardwareObservations = Field(default_factory=HardwareObservations)
    trust: TrustMetadata
    source_policy: SourcePolicy | None = None


class InstallState(BaseModel):
    """Mutable local state for a workflow capsule installed on this machine."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    schema_version: str = Field(min_length=1)
    capsule_fingerprint: str = Field(min_length=1)
    status: InstallStatus
    installed_at: str | None = None
    last_used_at: str | None = None
    runtime_profile_variant_id: str | None = None
    runtime_profile_manifest_hash: str | None = None
    runtime_profile_catalog_version: str | None = None
    dependency_env_fingerprint: str | None = None
    runner_workspace_fingerprint: str | None = None
    runner_process_compatibility_key: str | None = None
    dependency_env_path: str | None = None
    runner_workspace_path: str | None = None
    model_references: list[InstalledModelReference] = Field(default_factory=list)
    smoke_test_status: SmokeTestStatus = SmokeTestStatus.NOT_RUN
    smoke_test_report: SmokeTestReport = Field(default_factory=SmokeTestReport)
    last_error: str | None = None


class DependencyEnvManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_variant_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    runtime_profile_catalog_version: str = Field(min_length=1)
    fingerprint_schema_version: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    python_build_id: str = Field(min_length=1)
    os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    gpu_backend: str = Field(min_length=1)
    dependency_lock_hash: str = Field(min_length=1)
    install_policy_version: str = Field(min_length=1)
    status: InstallStatus
    smoke_test_status: SmokeTestStatus = SmokeTestStatus.NOT_RUN


class RunnerWorkspaceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: str = Field(min_length=1)
    fingerprint: str = Field(min_length=1)
    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_variant_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    runtime_profile_catalog_version: str = Field(min_length=1)
    fingerprint_schema_version: str = Field(min_length=1)
    dependency_env_fingerprint: str = Field(min_length=1)
    comfyui_version: str = Field(min_length=1)
    comfyui_source_hash: str = Field(min_length=1)
    enabled_custom_node_hash: str = Field(min_length=1)
    launch_config_hash: str = Field(min_length=1)
    model_view_hash: str | None = None
    status: InstallStatus
    smoke_test_status: SmokeTestStatus = SmokeTestStatus.NOT_RUN
