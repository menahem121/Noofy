"""Resolved dependency lock schema and strict install policy helpers.

Phase 5b treats raw dependency declarations as input data only. The runtime
environment contract is a Noofy-owned resolved lock made of wheel facts,
runtime profile facts, and resolver metadata.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.runtime.fingerprints import dependency_env_fingerprint, sha256_fingerprint
from app.runtime.isolation import SHA256_PATTERN, CapsuleLock
from app.source_policy import SourcePolicy

DEPENDENCY_LOCK_SCHEMA_VERSION = "0.1.0"
DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION = "quarantined-community-v1"

_NORMALIZED_NAME_PATTERN = re.compile(r"[-_.]+")
_VALID_IMPORT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_UNSAFE_REQUIREMENT_PREFIXES = (
    "-e ",
    "--editable ",
    "-r ",
    "--requirement ",
    "-c ",
    "--constraint ",
    "--extra-index-url",
    "--find-links",
    "-f ",
    "--index-url",
    "--no-binary",
    "--only-binary",
    "--trusted-host",
)


class DependencyRelationship(StrEnum):
    CORE = "core"
    DIRECT = "direct"
    TRANSITIVE = "transitive"


class DependencySourceKind(StrEnum):
    INDEX = "index"
    APPROVED_CACHE = "approved_cache"


class DependencyPolicyErrorCode(StrEnum):
    CONFLICTING_RESOLUTION = "conflicting_resolution"
    CROSS_RUNTIME_LOCK_MERGE = "cross_runtime_lock_merge"
    EDITABLE_INSTALL_NOT_ALLOWED = "editable_install_not_allowed"
    HASH_MISMATCH = "hash_mismatch"
    INSTALL_SCRIPT_NOT_ALLOWED = "install_script_not_allowed"
    MISSING_HASH = "missing_hash"
    MISSING_WHEEL = "missing_wheel"
    NATIVE_BUILD_NOT_ALLOWED = "native_build_not_allowed"
    PROJECT_CODE_EXECUTION_REQUIRED = "project_code_execution_required"
    SOURCE_POLICY_MISMATCH = "source_policy_mismatch"
    SDIST_NOT_ALLOWED = "sdist_not_allowed"
    UNSUPPORTED_DEPENDENCY_DECLARATION = "unsupported_dependency_declaration"
    UNAPPROVED_SOURCE = "unapproved_source"


class DependencyPolicyError(RuntimeError):
    def __init__(self, code: DependencyPolicyErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class ResolverMetadata(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    command: str | None = None


class ResolvedDependencyWheel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    wheel_filename: str = Field(min_length=1)
    sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    source_kind: DependencySourceKind
    source_index_url: str | None = None
    approved_cache_ref: str | None = None
    platform_tags: list[str] = Field(default_factory=list)
    environment_marker: str | None = None
    import_names: list[str] = Field(default_factory=list)
    relationship: DependencyRelationship
    requested_by: list[str] = Field(default_factory=list)
    resolver_name: str = Field(min_length=1)
    resolver_version: str = Field(min_length=1)
    source_distribution: bool = False
    native_build_required: bool = False
    install_script: bool = False

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str) -> str:
        return normalize_package_name(value)

    @field_validator("wheel_filename")
    @classmethod
    def _validate_wheel_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value:
            raise ValueError("wheel_filename must be a filename, not a path")
        if value in {"", ".", ".."}:
            raise ValueError("wheel_filename must be safe")
        return value

    @field_validator("approved_cache_ref")
    @classmethod
    def _validate_cache_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parts = value.split("/")
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("approved_cache_ref must be a safe relative path")
        if "\\" in value:
            raise ValueError("approved_cache_ref must use forward slashes")
        return value

    @field_validator("import_names")
    @classmethod
    def _validate_import_names(cls, value: list[str]) -> list[str]:
        names = sorted(set(value))
        for name in names:
            if not _VALID_IMPORT_NAME_RE.match(name):
                raise ValueError("import_names must be valid dotted Python import names")
        return names

    @model_validator(mode="after")
    def _validate_source_fields(self) -> ResolvedDependencyWheel:
        if self.source_kind is DependencySourceKind.INDEX and not self.source_index_url:
            raise ValueError("source_index_url is required for index wheels")
        if self.source_kind is DependencySourceKind.APPROVED_CACHE and not self.approved_cache_ref:
            raise ValueError("approved_cache_ref is required for approved-cache wheels")
        return self


class ResolvedDependencyLock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default=DEPENDENCY_LOCK_SCHEMA_VERSION, min_length=1)
    lock_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_variant_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    install_policy_version: str = Field(min_length=1)
    source_policy: SourcePolicy | None = None
    resolver: ResolverMetadata
    wheels: list[ResolvedDependencyWheel] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_resolver_copied_to_wheels(self) -> ResolvedDependencyLock:
        for wheel in self.wheels:
            if wheel.resolver_name != self.resolver.name or wheel.resolver_version != self.resolver.version:
                raise ValueError("wheel resolver metadata must match lock resolver metadata")
        return self


class DependencyDeclaration(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_file: str = Field(min_length=1)
    requirement: str = Field(min_length=1)


class DependencyMarkerPolicyFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: DependencyPolicyErrorCode
    source_file: str = Field(min_length=1)
    message: str = Field(min_length=1)


class DependencyMarkerInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    declarations: list[DependencyDeclaration] = Field(default_factory=list)
    findings: list[DependencyMarkerPolicyFinding] = Field(default_factory=list)

    @property
    def supported(self) -> bool:
        return not self.findings


def normalize_package_name(name: str) -> str:
    return _NORMALIZED_NAME_PATTERN.sub("-", name).lower()


def resolved_dependency_lock_hash(lock: ResolvedDependencyLock) -> str:
    """Hash resolved facts, excluding the self-referential lock_hash field."""
    return sha256_fingerprint(lock.model_dump(mode="json", exclude_none=True, exclude={"lock_hash"}))


def with_computed_lock_hash(lock: ResolvedDependencyLock) -> ResolvedDependencyLock:
    return lock.model_copy(update={"lock_hash": resolved_dependency_lock_hash(lock)})


def core_dependency_lock_from_capsule(capsule_lock: CapsuleLock) -> ResolvedDependencyLock:
    """Represent an already-pinned managed-core dependency lock."""
    runtime = capsule_lock.runtime
    return ResolvedDependencyLock(
        lock_hash=runtime.dependency_lock_hash,
        runtime_profile_id=runtime.runtime_profile_id,
        runtime_profile_variant_id=runtime.runtime_profile_variant_id,
        runtime_profile_manifest_hash=runtime.runtime_profile_manifest_hash,
        install_policy_version=capsule_lock.dependencies.install_policy,
        source_policy=capsule_lock.source_policy,
        resolver=ResolverMetadata(
            name="noofy-managed-core",
            version=DEPENDENCY_LOCK_SCHEMA_VERSION,
            command="bundled core dependency lock",
        ),
        wheels=[],
    )


def dependency_env_fingerprint_for_resolved_lock(
    lock: ResolvedDependencyLock,
    *,
    os_name: str,
    architecture: str,
    python_build_id: str,
    torch_wheel_build_tag: str,
    torch_backend: str,
    native_dependency_constraints: dict[str, object] | None = None,
) -> str:
    """Compute a dependency-env fingerprint from resolved lock/profile facts."""
    lock_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
    return dependency_env_fingerprint(
        runtime_profile_id=lock.runtime_profile_id,
        runtime_profile_manifest_hash=lock.runtime_profile_manifest_hash,
        runtime_profile_variant_id=lock.runtime_profile_variant_id,
        os_name=os_name,
        architecture=architecture,
        python_build_id=python_build_id,
        torch_wheel_build_tag=torch_wheel_build_tag,
        torch_backend=torch_backend,
        dependency_lock_hash=lock_hash,
        native_dependency_constraints=native_dependency_constraints or {},
        install_policy_version=lock.install_policy_version,
    )


def merge_resolved_dependency_locks(
    core_lock: ResolvedDependencyLock,
    custom_node_locks: Iterable[ResolvedDependencyLock],
) -> ResolvedDependencyLock:
    """Merge profile-core and custom-node locks into one dependency-env lock."""
    merged: dict[str, ResolvedDependencyWheel] = {}
    custom_locks = tuple(custom_node_locks)
    for lock in (core_lock, *custom_locks):
        _require_same_runtime(core_lock, lock)
        for wheel in lock.wheels:
            existing = merged.get(wheel.name)
            if existing is None:
                merged[wheel.name] = wheel
                continue
            merged[wheel.name] = _merge_wheel_or_raise(existing, wheel)

    merged_lock = core_lock.model_copy(
        update={
            "wheels": sorted(merged.values(), key=lambda item: (item.name, item.version, item.wheel_filename)),
            "source_policy": _merged_source_policy(core_lock, custom_locks),
            "lock_hash": None,
        }
    )
    return with_computed_lock_hash(merged_lock)


def validate_quarantined_community_lock(
    lock: ResolvedDependencyLock,
    *,
    approved_index_urls: Iterable[str] = (),
    wheel_cache_dir: Path | None = None,
) -> None:
    """Validate the default community policy before any environment install."""
    approved_indexes = set(approved_index_urls)
    for wheel in lock.wheels:
        if wheel.source_distribution or not wheel.wheel_filename.endswith(".whl"):
            raise DependencyPolicyError(
                DependencyPolicyErrorCode.SDIST_NOT_ALLOWED,
                f"Dependency {wheel.name} is not resolved to a wheel.",
            )
        if wheel.sha256 is None:
            raise DependencyPolicyError(
                DependencyPolicyErrorCode.MISSING_HASH,
                f"Dependency {wheel.name} is missing a wheel hash.",
            )
        if wheel.native_build_required:
            raise DependencyPolicyError(
                DependencyPolicyErrorCode.NATIVE_BUILD_NOT_ALLOWED,
                f"Dependency {wheel.name} requires a native source build.",
            )
        if wheel.install_script:
            raise DependencyPolicyError(
                DependencyPolicyErrorCode.INSTALL_SCRIPT_NOT_ALLOWED,
                f"Dependency {wheel.name} declares an install script.",
            )
        if wheel.source_kind is DependencySourceKind.INDEX:
            if wheel.source_index_url not in approved_indexes:
                raise DependencyPolicyError(
                    DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
                    f"Dependency {wheel.name} uses an unapproved index.",
                )
        elif wheel_cache_dir is not None:
            _verify_cached_wheel(wheel, wheel_cache_dir)


def validate_dependency_lock_source_policy(
    lock: ResolvedDependencyLock,
    expected_policy: SourcePolicy | None,
) -> None:
    if expected_policy is None or not lock.wheels:
        return
    if lock.source_policy is None:
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.SOURCE_POLICY_MISMATCH,
            "Resolved dependency lock is missing source-policy metadata.",
        )
    if not lock.source_policy.automatic_preparation_allowed:
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.SOURCE_POLICY_MISMATCH,
            "Resolved dependency lock was created under a blocked source policy.",
        )
    if _source_policy_identity(lock.source_policy) != _source_policy_identity(expected_policy):
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.SOURCE_POLICY_MISMATCH,
            "Resolved dependency lock source policy does not match the workflow capsule policy.",
        )


def inspect_dependency_marker_files(source_dir: Path) -> DependencyMarkerInspection:
    """Read standard dependency marker files as data without importing code."""
    declarations: list[DependencyDeclaration] = []
    findings: list[DependencyMarkerPolicyFinding] = []

    requirements_path = source_dir / "requirements.txt"
    if requirements_path.exists():
        _inspect_requirements_file(requirements_path, declarations, findings)

    pyproject_path = source_dir / "pyproject.toml"
    if pyproject_path.exists():
        _inspect_pyproject_file(pyproject_path, declarations, findings)

    setup_path = source_dir / "setup.py"
    if setup_path.exists():
        findings.append(
            DependencyMarkerPolicyFinding(
                code=DependencyPolicyErrorCode.PROJECT_CODE_EXECUTION_REQUIRED,
                source_file="setup.py",
                message="setup.py dependency extraction is unsupported because it requires executing project code.",
            )
        )

    return DependencyMarkerInspection(declarations=declarations, findings=findings)


def _require_same_runtime(reference: ResolvedDependencyLock, candidate: ResolvedDependencyLock) -> None:
    fields = (
        "runtime_profile_id",
        "runtime_profile_variant_id",
        "runtime_profile_manifest_hash",
        "install_policy_version",
    )
    if any(getattr(reference, field) != getattr(candidate, field) for field in fields):
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.CROSS_RUNTIME_LOCK_MERGE,
            "Resolved dependency locks target different runtime profiles or install policies.",
        )
    if (
        reference.source_policy is not None
        and candidate.source_policy is not None
        and _source_policy_identity(reference.source_policy) != _source_policy_identity(candidate.source_policy)
    ):
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.SOURCE_POLICY_MISMATCH,
            "Resolved dependency locks were created under different source policies.",
        )


def _merged_source_policy(
    core_lock: ResolvedDependencyLock,
    custom_node_locks: Iterable[ResolvedDependencyLock],
) -> SourcePolicy | None:
    if core_lock.source_policy is not None:
        return core_lock.source_policy
    for lock in custom_node_locks:
        if lock.source_policy is not None:
            return lock.source_policy
    return None


def _source_policy_identity(policy: SourcePolicy) -> dict[str, object]:
    return {
        "policy_version": policy.policy_version,
        "trust_level": policy.trust_level,
        "source_policy": policy.source_policy,
        "package_source_type": policy.package_source_type.value,
        "automatic_preparation_allowed": policy.automatic_preparation_allowed,
        "allowed_registry_origins": sorted(policy.allowed_registry_origins),
        "allowed_source_origins": sorted(policy.allowed_source_origins),
        "allowed_model_origins": sorted(policy.allowed_model_origins),
        "registry_id": policy.registry_id,
        "registry_snapshot_hash": policy.registry_snapshot_hash,
        "model_source_trust": policy.model_source_trust.value,
        "community_preparation_opt_in_required": policy.community_preparation_opt_in_required,
        "community_preparation_opted_in": policy.community_preparation_opted_in,
    }


def _merge_wheel_or_raise(
    existing: ResolvedDependencyWheel,
    candidate: ResolvedDependencyWheel,
) -> ResolvedDependencyWheel:
    comparable_existing = existing.model_dump(mode="json", exclude={"relationship", "requested_by"})
    comparable_candidate = candidate.model_dump(mode="json", exclude={"relationship", "requested_by"})
    if comparable_existing != comparable_candidate:
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.CONFLICTING_RESOLUTION,
            f"Conflicting resolved dependency for {existing.name}.",
        )
    requested_by = sorted(set(existing.requested_by) | set(candidate.requested_by))
    relationship = _merged_relationship(existing.relationship, candidate.relationship)
    return existing.model_copy(update={"relationship": relationship, "requested_by": requested_by})


def _merged_relationship(
    left: DependencyRelationship,
    right: DependencyRelationship,
) -> DependencyRelationship:
    if DependencyRelationship.CORE in {left, right}:
        return DependencyRelationship.CORE
    if DependencyRelationship.DIRECT in {left, right}:
        return DependencyRelationship.DIRECT
    return DependencyRelationship.TRANSITIVE


def _verify_cached_wheel(wheel: ResolvedDependencyWheel, wheel_cache_dir: Path) -> None:
    assert wheel.approved_cache_ref is not None
    path = (wheel_cache_dir / wheel.approved_cache_ref).resolve()
    cache_root = wheel_cache_dir.resolve()
    if cache_root not in path.parents and path != cache_root:
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
            f"Dependency {wheel.name} points outside the approved wheel cache.",
        )
    if not path.exists():
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.MISSING_WHEEL,
            f"Dependency {wheel.name} is missing from the approved wheel cache.",
        )
    if path.name != wheel.wheel_filename:
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
            f"Dependency {wheel.name} cache reference does not match the resolved wheel filename.",
        )
    actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    if actual.lower() != wheel.sha256.lower():
        raise DependencyPolicyError(
            DependencyPolicyErrorCode.HASH_MISMATCH,
            f"Dependency {wheel.name} wheel hash does not match the resolved lock.",
        )


def _inspect_requirements_file(
    path: Path,
    declarations: list[DependencyDeclaration],
    findings: list[DependencyMarkerPolicyFinding],
) -> None:
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if _is_unsupported_requirement_line(line):
            findings.append(
                DependencyMarkerPolicyFinding(
                    code=DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
                    source_file="requirements.txt",
                    message=f"Unsupported requirements.txt entry at line {line_number}.",
                )
            )
            continue
        declarations.append(DependencyDeclaration(source_file="requirements.txt", requirement=line))


def _inspect_pyproject_file(
    path: Path,
    declarations: list[DependencyDeclaration],
    findings: list[DependencyMarkerPolicyFinding],
) -> None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        findings.append(
            DependencyMarkerPolicyFinding(
                code=DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
                source_file="pyproject.toml",
                message=f"Invalid pyproject.toml: {error}",
            )
        )
        return

    project = data.get("project")
    if not isinstance(project, dict):
        return

    dynamic = project.get("dynamic") or []
    if "dependencies" in dynamic or "optional-dependencies" in dynamic:
        findings.append(
            DependencyMarkerPolicyFinding(
                code=DependencyPolicyErrorCode.PROJECT_CODE_EXECUTION_REQUIRED,
                source_file="pyproject.toml",
                message="Dynamic pyproject dependencies require executing project code.",
            )
        )
        return

    dependencies = project.get("dependencies") or []
    if isinstance(dependencies, list):
        for requirement in dependencies:
            if isinstance(requirement, str):
                declarations.append(DependencyDeclaration(source_file="pyproject.toml", requirement=requirement))

    optional_dependencies = project.get("optional-dependencies") or {}
    if isinstance(optional_dependencies, dict):
        for requirements in optional_dependencies.values():
            if not isinstance(requirements, list):
                continue
            for requirement in requirements:
                if isinstance(requirement, str):
                    declarations.append(DependencyDeclaration(source_file="pyproject.toml", requirement=requirement))


def _is_unsupported_requirement_line(line: str) -> bool:
    normalized = line.lower()
    if normalized.startswith("-") or normalized.startswith(_UNSAFE_REQUIREMENT_PREFIXES):
        return True
    if normalized.startswith(("git+", "hg+", "svn+", "bzr+")):
        return True
    if "://" in normalized:
        return True
    if " @ file:" in normalized or normalized.startswith(("./", "../", "/")):
        return True
    return False
