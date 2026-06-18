"""Phase 5k custom-node registry and source-resolution primitives.

The trusted backend treats registry metadata and downloaded custom-node source
archives as data. This module resolves pinned source facts and verifies source
archives into Noofy's source cache, but it does not import custom-node modules
or run project setup code.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import urllib.request
import zipfile
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.archive_safety import (
    MaterializedPathIndex,
    PathSafetyError,
    StreamLimitError,
    contained_destination,
    copy_stream_limited,
    ignored_archive_member,
    safe_relative_posix_path,
    zip_member_unsafe_reason,
)
from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.isolation import SHA256_PATTERN, CustomNodeLock, TrustLevel
from app.source_policy import SourcePolicy

NODE_REGISTRY_SCHEMA_VERSION = "0.1.0"
DEFAULT_NODE_REGISTRY_PATH = Path(__file__).with_name("node_registry_catalog.json")
DEFAULT_NODE_TYPE_MAPPINGS_PATH = Path(__file__).with_name("node_type_mappings.json")
CUSTOM_NODE_SOURCE_CACHE_MANIFEST_FILENAME = (
    "noofy-custom-node-source-cache-manifest.json"
)
_BLOCKED_FLOATING_REFS = {"", "head", "latest", "main", "master", "trunk"}
MAX_CUSTOM_NODE_SOURCE_ARCHIVE_FILES = 20_000
MAX_CUSTOM_NODE_SOURCE_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


class NodeRegistrySourceKind(StrEnum):
    HTTPS_ZIP_ARCHIVE = "https_zip_archive"
    GIT_ZIP_ARCHIVE = "git_zip_archive"


class NodeRegistryResolutionErrorCode(StrEnum):
    AMBIGUOUS_NODE_TYPE = "ambiguous_node_type"
    COMMUNITY_OPT_IN_REQUIRED = "community_opt_in_required"
    HASH_MISMATCH = "hash_mismatch"
    MISSING_PINNED_SOURCE_REF = "missing_pinned_source_ref"
    MISSING_SOURCE_CONTENT_HASH = "missing_source_content_hash"
    POLICY_BLOCKED_TRUST_LEVEL = "policy_blocked_trust_level"
    REGISTRY_SNAPSHOT_MISMATCH = "registry_snapshot_mismatch"
    SOURCE_RESOLVED = "source_resolved"
    SOURCE_POLICY_BLOCKED = "source_policy_blocked"
    UNAPPROVED_SOURCE_URL = "unapproved_source_url"
    UNKNOWN_NODE_TYPE = "unknown_node_type"
    UNKNOWN_PACKAGE = "unknown_package"
    UNPINNED_SOURCE_REF = "unpinned_source_ref"
    UNSAFE_ARCHIVE_PATH = "unsafe_archive_path"
    UNSUPPORTED_ARCHIVE_FORMAT = "unsupported_archive_format"


class NodeRegistryResolutionError(RuntimeError):
    def __init__(
        self,
        code: NodeRegistryResolutionErrorCode,
        user_message: str,
        *,
        developer_details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.developer_details = developer_details or {}


class NodeRegistryDiagnostic(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: NodeRegistryResolutionErrorCode
    message: str = Field(min_length=1)
    developer_details: dict[str, object] = Field(default_factory=dict)


class NodeRegistrySource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_kind: NodeRegistrySourceKind
    source_url: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_content_hash: str = Field(pattern=SHA256_PATTERN)
    archive_subdir: str | None = None
    source_repo_url: str | None = None

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("custom-node sources must use https URLs")
        return value

    @field_validator("source_repo_url")
    @classmethod
    def _validate_source_repo_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError("custom-node repository URLs must use https URLs")
        return value

    @field_validator("archive_subdir")
    @classmethod
    def _validate_archive_subdir(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.rstrip("/")
        try:
            safe_relative_posix_path(normalized, allow_nested=True)
        except PathSafetyError as exc:
            raise ValueError("archive_subdir must be a safe relative path") from exc
        return normalized

    @model_validator(mode="after")
    def _validate_pinned_ref(self) -> NodeRegistrySource:
        if self.source_ref.strip().casefold() in _BLOCKED_FLOATING_REFS:
            raise ValueError(
                "source_ref must be pinned, not a floating branch or alias"
            )
        if (
            self.source_kind == NodeRegistrySourceKind.GIT_ZIP_ARCHIVE
            and not _is_full_git_commit_sha(self.source_ref)
        ):
            raise ValueError("git archive source_ref must be a resolved commit SHA")
        return self


class NodeRegistryEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str = Field(min_length=1)
    display_name: str | None = None
    trust_level: TrustLevel = TrustLevel.REGISTRY_LOCKED
    node_types: list[str] = Field(default_factory=list)
    sources: list[NodeRegistrySource] = Field(min_length=1)

    @field_validator("node_types")
    @classmethod
    def _sort_unique_node_types(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class NoofyNodeRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default=NODE_REGISTRY_SCHEMA_VERSION, min_length=1)
    registry_id: str = Field(min_length=1)
    registry_snapshot_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    entries: list[NodeRegistryEntry] = Field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> NoofyNodeRegistry:
        with path.open("r", encoding="utf-8") as file:
            return cls.model_validate(json.load(file))

    def entry_for_package(self, package_id: str) -> NodeRegistryEntry | None:
        normalized = _normalize_package_id(package_id)
        for entry in self.entries:
            if _normalize_package_id(entry.package_id) == normalized:
                return entry
        return None

    def entries_for_node_type(self, node_type: str) -> list[NodeRegistryEntry]:
        return [entry for entry in self.entries if node_type in entry.node_types]


class NodeTypeMappingCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_type_to_package_id: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> NodeTypeMappingCatalog:
        with path.open("r", encoding="utf-8") as file:
            return cls.model_validate(json.load(file))


def load_noofy_node_registry(
    path: Path = DEFAULT_NODE_REGISTRY_PATH,
) -> NoofyNodeRegistry:
    if not path.exists():
        return NoofyNodeRegistry(registry_id="noofy-empty-local-registry")
    return NoofyNodeRegistry.load(path)


def load_node_type_mapping_catalog(
    path: Path = DEFAULT_NODE_TYPE_MAPPINGS_PATH,
) -> NodeTypeMappingCatalog:
    if not path.exists():
        return NodeTypeMappingCatalog()
    return NodeTypeMappingCatalog.load(path)


class CustomNodeSourceResolutionRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str | None = None
    node_types: list[str] = Field(default_factory=list)
    trust_level: TrustLevel
    allow_unverified_community_preparation: bool = False
    explicit_source: NodeRegistrySource | None = None
    source_policy: SourcePolicy | None = None

    @field_validator("node_types")
    @classmethod
    def _sort_unique_node_types(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class ResolvedCustomNodeSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    package_id: str = Field(min_length=1)
    registry_id: str | None = None
    display_name: str | None = None
    trust_level: TrustLevel
    node_types: list[str] = Field(default_factory=list)
    source: NodeRegistrySource
    resolution_method: str = Field(min_length=1)
    diagnostics: list[NodeRegistryDiagnostic] = Field(default_factory=list)

    def source_policy_origins(self) -> list[str]:
        origins: list[str] = []
        if self.registry_id:
            origins.append(self.registry_id)
        if self.resolution_method == "explicit_metadata":
            origins.append("explicit-metadata")
        if self.trust_level is TrustLevel.NOOFY_VERIFIED:
            origins.append("noofy-verified")
        if self.trust_level is TrustLevel.REGISTRY_LOCKED:
            origins.append("registry-locked")
        return sorted(set(origins))

    def to_custom_node_lock(
        self, cached_source: CachedCustomNodeSource | None = None
    ) -> CustomNodeLock:
        return CustomNodeLock(
            package_id=self.package_id,
            source=f"{self.resolution_method}:{self.package_id}",
            source_ref=self.source.source_ref,
            source_content_hash=self.source.source_content_hash,
            source_cache_ref=(
                cached_source.source_cache_ref if cached_source is not None else None
            ),
            source_repo_url=self.source.source_repo_url,
            trust_level=self.trust_level,
            node_types=self.node_types,
        )


class NodeRegistryResolver:
    def __init__(
        self,
        *,
        registry: NoofyNodeRegistry,
        log_store: DiagnosticsSink,
        mappings: NodeTypeMappingCatalog | None = None,
    ) -> None:
        self.registry = registry
        self.mappings = mappings or NodeTypeMappingCatalog()
        self.log_store = log_store

    def resolve(
        self, request: CustomNodeSourceResolutionRequest
    ) -> ResolvedCustomNodeSource:
        try:
            self._check_opt_in(request)
            self._check_source_policy_is_preparable(request)
            if request.explicit_source is not None:
                self._check_explicit_source_policy(request)
                package_id = request.package_id or _package_id_from_source_url(
                    request.explicit_source.source_url
                )
                resolved = ResolvedCustomNodeSource(
                    package_id=package_id,
                    trust_level=request.trust_level,
                    node_types=request.node_types,
                    source=request.explicit_source,
                    resolution_method="explicit_metadata",
                    diagnostics=[
                        NodeRegistryDiagnostic(
                            code=NodeRegistryResolutionErrorCode.SOURCE_RESOLVED,
                            message="Resolved from explicit Noofy package metadata.",
                            developer_details={
                                "source_url": request.explicit_source.source_url
                            },
                        )
                    ],
                )
                self._log_success(resolved)
                return resolved

            entry, method = self._resolve_registry_entry(request)
            source = entry.sources[0]
            _validate_source_policy(source)
            self._check_registry_source_policy(request, entry)
            self._check_trust_policy(request, entry)
            resolved = ResolvedCustomNodeSource(
                package_id=entry.package_id,
                registry_id=self.registry.registry_id,
                display_name=entry.display_name,
                trust_level=entry.trust_level,
                node_types=entry.node_types,
                source=source,
                resolution_method=method,
                diagnostics=[
                    NodeRegistryDiagnostic(
                        code=NodeRegistryResolutionErrorCode.SOURCE_RESOLVED,
                        message=f"Resolved custom-node source through {method}.",
                        developer_details={
                            "registry_id": self.registry.registry_id,
                            "package_id": entry.package_id,
                            "source_ref": source.source_ref,
                        },
                    )
                ],
            )
            self._log_success(resolved)
            return resolved
        except NodeRegistryResolutionError as exc:
            self.log_store.add(
                "warning",
                "Custom-node source resolution failed",
                "runtime.node_registry",
                details={"code": exc.code.value, **exc.developer_details},
            )
            raise

    def registry_entry_for_node_types(
        self,
        node_types: list[str],
    ) -> tuple[NodeRegistryEntry, str]:
        """Return the registry package selected for node types without fetching it.

        Import preview uses this to turn raw graph node types into package
        requirements before the normal source-policy and cache materialization
        path runs.
        """
        return self._resolve_registry_entry(
            CustomNodeSourceResolutionRequest(
                node_types=node_types,
                trust_level=TrustLevel.QUARANTINED_COMMUNITY,
                allow_unverified_community_preparation=True,
            )
        )

    def _check_opt_in(self, request: CustomNodeSourceResolutionRequest) -> None:
        if (
            request.trust_level is TrustLevel.QUARANTINED_COMMUNITY
            and not request.allow_unverified_community_preparation
        ):
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.COMMUNITY_OPT_IN_REQUIRED,
                "This community workflow needs permission before Noofy prepares workflow extensions.",
                developer_details={"trust_level": request.trust_level.value},
            )

    def _check_source_policy_is_preparable(
        self, request: CustomNodeSourceResolutionRequest
    ) -> None:
        policy = request.source_policy
        if policy is None:
            return
        if policy.trust_level != request.trust_level.value:
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED,
                "Noofy cannot prepare this workflow extension because its source policy does not match the workflow trust level.",
                developer_details={
                    "policy_trust_level": policy.trust_level,
                    "request_trust_level": request.trust_level.value,
                },
            )
        if not policy.automatic_preparation_allowed:
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED,
                "This workflow needs permission before Noofy prepares workflow extensions.",
                developer_details={
                    "trust_level": policy.trust_level,
                    "policy_status": policy.policy_status,
                    "source_policy": policy.source_policy,
                },
            )

    def _resolve_registry_entry(
        self,
        request: CustomNodeSourceResolutionRequest,
    ) -> tuple[NodeRegistryEntry, str]:
        if request.package_id:
            entry = self.registry.entry_for_package(request.package_id)
            if entry is not None:
                return entry, "registry_metadata"

        mapped_package_ids = {
            self.mappings.node_type_to_package_id[node_type]
            for node_type in request.node_types
            if node_type in self.mappings.node_type_to_package_id
        }
        if len(mapped_package_ids) == 1:
            package_id = next(iter(mapped_package_ids))
            entry = self.registry.entry_for_package(package_id)
            if entry is not None:
                return entry, "node_type_mapping"

        matching_entries: dict[str, NodeRegistryEntry] = {}
        for node_type in request.node_types:
            for entry in self.registry.entries_for_node_type(node_type):
                matching_entries[entry.package_id] = entry
        if not matching_entries:
            if request.package_id:
                raise NodeRegistryResolutionError(
                    NodeRegistryResolutionErrorCode.UNKNOWN_PACKAGE,
                    "Noofy does not know how to prepare one required workflow extension.",
                    developer_details={
                        "package_id": request.package_id,
                        "node_types": request.node_types,
                    },
                )
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.UNKNOWN_NODE_TYPE,
                "Noofy does not know how to prepare one required workflow extension.",
                developer_details={"node_types": request.node_types},
            )
        if len(matching_entries) > 1:
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.AMBIGUOUS_NODE_TYPE,
                "Noofy found more than one possible source for a workflow extension.",
                developer_details={"package_ids": sorted(matching_entries)},
            )
        return next(iter(matching_entries.values())), "registry_metadata"

    def _check_explicit_source_policy(
        self, request: CustomNodeSourceResolutionRequest
    ) -> None:
        policy = request.source_policy
        if policy is None:
            return
        if "explicit-metadata" not in set(policy.allowed_source_origins):
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED,
                "Noofy cannot prepare this workflow extension from package metadata under the current source policy.",
                developer_details={
                    "source_policy": policy.source_policy,
                    "allowed_source_origins": policy.allowed_source_origins,
                    "source_url": (
                        request.explicit_source.source_url
                        if request.explicit_source
                        else None
                    ),
                },
            )

    def _check_registry_source_policy(
        self,
        request: CustomNodeSourceResolutionRequest,
        entry: NodeRegistryEntry,
    ) -> None:
        policy = request.source_policy
        if policy is None:
            return
        allowed_registries = set(policy.allowed_registry_origins)
        if allowed_registries and self.registry.registry_id not in allowed_registries:
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED,
                "Noofy cannot prepare this workflow extension from the active registry under the current source policy.",
                developer_details={
                    "registry_id": self.registry.registry_id,
                    "allowed_registry_origins": sorted(allowed_registries),
                    "package_id": entry.package_id,
                },
            )
        if policy.registry_snapshot_hash is not None:
            if self.registry.registry_snapshot_hash != policy.registry_snapshot_hash:
                raise NodeRegistryResolutionError(
                    NodeRegistryResolutionErrorCode.REGISTRY_SNAPSHOT_MISMATCH,
                    "Noofy cannot prepare this workflow extension because the registry snapshot does not match the package policy.",
                    developer_details={
                        "registry_id": self.registry.registry_id,
                        "policy_registry_snapshot_hash": policy.registry_snapshot_hash,
                        "active_registry_snapshot_hash": self.registry.registry_snapshot_hash,
                        "package_id": entry.package_id,
                    },
                )
        allowed_sources = set(policy.allowed_source_origins)
        if not allowed_sources:
            return
        accepted_sources = {self.registry.registry_id}
        if entry.trust_level is TrustLevel.NOOFY_VERIFIED:
            accepted_sources.add("noofy-verified")
        if entry.trust_level is TrustLevel.REGISTRY_LOCKED:
            accepted_sources.add("registry-locked")
        if accepted_sources.isdisjoint(allowed_sources):
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED,
                "Noofy cannot prepare this workflow extension from the resolved source under the current source policy.",
                developer_details={
                    "package_id": entry.package_id,
                    "entry_trust_level": entry.trust_level.value,
                    "registry_id": self.registry.registry_id,
                    "allowed_source_origins": sorted(allowed_sources),
                },
            )

    def _check_trust_policy(
        self,
        request: CustomNodeSourceResolutionRequest,
        entry: NodeRegistryEntry,
    ) -> None:
        if (
            request.trust_level is TrustLevel.NOOFY_VERIFIED
            and entry.trust_level is not TrustLevel.NOOFY_VERIFIED
        ):
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.POLICY_BLOCKED_TRUST_LEVEL,
                "This verified workflow references an extension source that is not verified.",
                developer_details={
                    "request_trust_level": request.trust_level.value,
                    "entry_trust_level": entry.trust_level.value,
                    "package_id": entry.package_id,
                },
            )

    def _log_success(self, resolved: ResolvedCustomNodeSource) -> None:
        self.log_store.add(
            "info",
            "Resolved custom-node source",
            "runtime.node_registry",
            details={
                "package_id": resolved.package_id,
                "resolution_method": resolved.resolution_method,
                "source_kind": resolved.source.source_kind.value,
                "source_ref": resolved.source.source_ref,
            },
        )


class SourceArchiveFetcher(Protocol):
    def fetch(self, url: str) -> bytes:
        """Return archive bytes for an approved URL."""


class UrlLibSourceArchiveFetcher:
    def fetch(self, url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read()


class CachedCustomNodeSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_cache_ref: str = Field(min_length=1)
    source_dir: Path
    source_content_hash: str = Field(pattern=SHA256_PATTERN)
    manifest_path: Path


class CustomNodeSourceCache:
    def __init__(
        self,
        *,
        cache_dir: Path,
        log_store: DiagnosticsSink,
        fetcher: SourceArchiveFetcher | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.fetcher = fetcher or UrlLibSourceArchiveFetcher()
        self.log_store = log_store

    def materialize(
        self,
        source: NodeRegistrySource,
        *,
        source_policy: SourcePolicy | None = None,
        source_origins: list[str] | None = None,
    ) -> CachedCustomNodeSource:
        _validate_source_policy(source)
        _validate_cache_source_policy(
            source_policy,
            source_origins=source_origins or [],
            source_url=source.source_url,
        )
        archive_bytes = self.fetcher.fetch(source.source_url)
        expected_hash = _normalized_sha256(source.source_content_hash)
        actual_hash = hashlib.sha256(archive_bytes).hexdigest()
        if actual_hash != expected_hash:
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.HASH_MISMATCH,
                "Noofy could not verify a downloaded workflow extension.",
                developer_details={
                    "source_url": source.source_url,
                    "expected_sha256": expected_hash,
                    "actual_sha256": actual_hash,
                },
            )

        target_dir = self.cache_dir / actual_hash
        source_dir = target_dir / "source"
        manifest_path = target_dir / CUSTOM_NODE_SOURCE_CACHE_MANIFEST_FILENAME
        if source_dir.exists() and manifest_path.exists():
            self._validate_existing_manifest(source, manifest_path, actual_hash)
            return CachedCustomNodeSource(
                source_cache_ref=f"{actual_hash}/source",
                source_dir=source_dir,
                source_content_hash=f"sha256:{actual_hash}",
                manifest_path=manifest_path,
            )
        if target_dir.exists():
            shutil.rmtree(target_dir)

        transaction_dir = self.cache_dir / "_transactions" / actual_hash
        shutil.rmtree(transaction_dir, ignore_errors=True)
        transaction_source_dir = transaction_dir / "source"
        transaction_source_dir.mkdir(parents=True)
        try:
            _extract_verified_zip_archive(
                archive_bytes,
                transaction_source_dir,
                archive_subdir=source.archive_subdir,
            )
            manifest = {
                "schema_version": NODE_REGISTRY_SCHEMA_VERSION,
                "source_kind": source.source_kind.value,
                "source_url": source.source_url,
                "source_repo_url": source.source_repo_url,
                "source_ref": source.source_ref,
                "source_content_hash": f"sha256:{actual_hash}",
                "source_cache_ref": f"{actual_hash}/source",
            }
            (transaction_dir / CUSTOM_NODE_SOURCE_CACHE_MANIFEST_FILENAME).write_text(
                json.dumps(manifest, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            if target_dir.exists():
                shutil.rmtree(transaction_dir, ignore_errors=True)
            else:
                transaction_dir.replace(target_dir)
        finally:
            shutil.rmtree(transaction_dir, ignore_errors=True)

        self.log_store.add(
            "info",
            "Cached custom-node source archive",
            "runtime.node_registry",
            details={
                "source_ref": source.source_ref,
                "source_cache_ref": f"{actual_hash}/source",
            },
        )
        return CachedCustomNodeSource(
            source_cache_ref=f"{actual_hash}/source",
            source_dir=source_dir,
            source_content_hash=f"sha256:{actual_hash}",
            manifest_path=manifest_path,
        )

    def _validate_existing_manifest(
        self,
        source: NodeRegistrySource,
        manifest_path: Path,
        actual_hash: str,
    ) -> None:
        with manifest_path.open("r", encoding="utf-8") as file:
            manifest = json.load(file)
        expected = {
            "source_url": source.source_url,
            "source_ref": source.source_ref,
            "source_content_hash": f"sha256:{actual_hash}",
            "source_cache_ref": f"{actual_hash}/source",
        }
        mismatches = {
            key: {"expected": value, "actual": manifest.get(key)}
            for key, value in expected.items()
            if manifest.get(key) != value
        }
        if mismatches:
            raise NodeRegistryResolutionError(
                NodeRegistryResolutionErrorCode.HASH_MISMATCH,
                "Noofy could not verify a cached workflow extension.",
                developer_details={
                    "manifest_path": str(manifest_path),
                    "mismatches": mismatches,
                },
            )


def _validate_source_policy(source: NodeRegistrySource) -> None:
    if (
        not source.source_ref
        or source.source_ref.strip().casefold() in _BLOCKED_FLOATING_REFS
        or (
            source.source_kind == NodeRegistrySourceKind.GIT_ZIP_ARCHIVE
            and not _is_full_git_commit_sha(source.source_ref)
        )
    ):
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.UNPINNED_SOURCE_REF,
            "Noofy cannot prepare a workflow extension without a pinned source version.",
            developer_details={
                "source_ref": source.source_ref,
                "source_url": source.source_url,
            },
        )
    if not source.source_content_hash:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.MISSING_SOURCE_CONTENT_HASH,
            "Noofy cannot prepare a workflow extension without a verified source hash.",
            developer_details={
                "source_ref": source.source_ref,
                "source_url": source.source_url,
            },
        )
    if not source.source_url.startswith("https://"):
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.UNAPPROVED_SOURCE_URL,
            "Noofy cannot prepare a workflow extension from this source.",
            developer_details={"source_url": source.source_url},
        )


def _validate_cache_source_policy(
    source_policy: SourcePolicy | None,
    *,
    source_origins: list[str],
    source_url: str,
) -> None:
    if source_policy is None:
        return
    if not source_policy.automatic_preparation_allowed:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED,
            "This workflow needs permission before Noofy downloads workflow extensions.",
            developer_details={
                "source_policy": source_policy.source_policy,
                "policy_status": source_policy.policy_status,
                "source_url": source_url,
            },
        )
    allowed_sources = set(source_policy.allowed_source_origins)
    if allowed_sources and allowed_sources.isdisjoint(source_origins):
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED,
            "Noofy cannot download this workflow extension under the current source policy.",
            developer_details={
                "source_policy": source_policy.source_policy,
                "allowed_source_origins": sorted(allowed_sources),
                "source_origins": sorted(set(source_origins)),
                "source_url": source_url,
            },
        )


def _extract_verified_zip_archive(
    archive_bytes: bytes,
    target_dir: Path,
    *,
    archive_subdir: str | None,
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if not members:
                raise NodeRegistryResolutionError(
                    NodeRegistryResolutionErrorCode.UNSUPPORTED_ARCHIVE_FORMAT,
                    "Noofy could not read the workflow extension archive.",
                    developer_details={"reason": "empty_archive"},
                )
            subdir_prefix = f"{archive_subdir.rstrip('/')}/" if archive_subdir else None
            extracted_count = 0
            extracted_bytes = 0
            path_index = MaterializedPathIndex()
            for member in members:
                relative_name = member.filename
                if subdir_prefix is not None:
                    if not relative_name.startswith(subdir_prefix):
                        continue
                    relative_name = relative_name.removeprefix(subdir_prefix)
                if not relative_name:
                    continue
                if ignored_archive_member(relative_name):
                    continue
                unsafe_reason = zip_member_unsafe_reason(member)
                if unsafe_reason == "symlink":
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        "Noofy found an unsafe path inside a workflow extension archive.",
                        developer_details={
                            "boundary": "archive_member_path",
                            "archive_path": member.filename,
                            "reason": "symlink",
                        },
                    )
                if unsafe_reason == "special_file":
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        "Noofy found an unsafe path inside a workflow extension archive.",
                        developer_details={
                            "boundary": "archive_member_path",
                            "archive_path": member.filename,
                            "reason": "special_file",
                        },
                    )
                try:
                    safe_name = safe_relative_posix_path(
                        relative_name,
                        allow_nested=True,
                    )
                    path_index.add(safe_name)
                except PathSafetyError as exc:
                    message = (
                        "Noofy found conflicting paths inside a workflow extension archive."
                        if exc.reason == "collision"
                        else "Noofy found an unsafe path inside a workflow extension archive."
                    )
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        message,
                        developer_details={
                            "boundary": "archive_member_path",
                            "archive_path": member.filename,
                            "reason": exc.reason,
                        },
                    ) from exc
                extracted_count += 1
                if extracted_count > MAX_CUSTOM_NODE_SOURCE_ARCHIVE_FILES:
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        "Noofy found too many files inside a workflow extension archive.",
                        developer_details={
                            "boundary": "archive_member_path",
                            "reason": "oversized",
                            "max_files": MAX_CUSTOM_NODE_SOURCE_ARCHIVE_FILES,
                        },
                    )
                if (
                    extracted_bytes + member.file_size
                    > MAX_CUSTOM_NODE_SOURCE_UNCOMPRESSED_BYTES
                ):
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        "Noofy found too much data inside a workflow extension archive.",
                        developer_details={
                            "boundary": "archive_member_path",
                            "reason": "oversized",
                            "max_uncompressed_bytes": MAX_CUSTOM_NODE_SOURCE_UNCOMPRESSED_BYTES,
                        },
                    )
                try:
                    destination = contained_destination(target_dir, safe_name)
                except PathSafetyError as exc:
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        "Noofy found an unsafe path inside a workflow extension archive.",
                        developer_details={
                            "boundary": "archive_member_path",
                            "archive_path": member.filename,
                            "reason": exc.reason,
                        },
                    ) from exc
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with (
                        archive.open(member) as source_file,
                        destination.open("xb") as destination_file,
                    ):
                        copied_bytes = copy_stream_limited(
                            source_file,
                            destination_file,
                            max_bytes=(
                                MAX_CUSTOM_NODE_SOURCE_UNCOMPRESSED_BYTES
                                - extracted_bytes
                            ),
                        )
                except StreamLimitError as exc:
                    destination.unlink(missing_ok=True)
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        "Noofy found too much data inside a workflow extension archive.",
                        developer_details={
                            "boundary": "archive_member_path",
                            "archive_path": member.filename,
                            "reason": "oversized",
                            "max_uncompressed_bytes": (
                                MAX_CUSTOM_NODE_SOURCE_UNCOMPRESSED_BYTES
                            ),
                            "copied_bytes": extracted_bytes + exc.copied_bytes,
                        },
                    ) from exc
                extracted_bytes += copied_bytes
                if copied_bytes != member.file_size:
                    destination.unlink(missing_ok=True)
                    raise NodeRegistryResolutionError(
                        NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH,
                        "Noofy could not verify a workflow extension archive member.",
                        developer_details={
                            "boundary": "archive_member_path",
                            "archive_path": member.filename,
                            "reason": "size_mismatch",
                            "declared_size_bytes": member.file_size,
                            "copied_size_bytes": copied_bytes,
                        },
                    )
            if extracted_count == 0:
                raise NodeRegistryResolutionError(
                    NodeRegistryResolutionErrorCode.UNSUPPORTED_ARCHIVE_FORMAT,
                    "Noofy could not read the workflow extension archive.",
                    developer_details={
                        "reason": "archive_subdir_not_found",
                        "archive_subdir": archive_subdir,
                    },
                )
    except zipfile.BadZipFile as exc:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.UNSUPPORTED_ARCHIVE_FORMAT,
            "Noofy could not read the workflow extension archive.",
            developer_details={"reason": "bad_zip"},
        ) from exc


def _normalized_sha256(value: str) -> str:
    return value.removeprefix("sha256:").lower()


def _is_full_git_commit_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdefABCDEF" for char in value)


def _normalize_package_id(value: str) -> str:
    return value.replace("_", "-").casefold()


def _package_id_from_source_url(source_url: str) -> str:
    stem = source_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".zip")
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-" for char in stem
    )
    return cleaned.strip(".-_") or "custom-node"
