from __future__ import annotations

import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol
from uuid import uuid4

from pydantic import ValidationError

from app.archive_safety import (
    PathSafetyError,
    StreamLimitError,
    contained_destination,
    copy_stream_limited,
)
from app.core.config import settings
from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.custom_nodes import (
    core_node_manifest_catalog_for_runtime_profiles,
    load_core_node_manifest_catalog,
)
from app.runtime.dependencies.isolation import CapsuleLock, HardwareObservations, ModelLock, TrustLevel
from app.runtime.profiles import RuntimeProfileCatalog
from app.runtime.node_registry import (
    CustomNodeSourceCache,
    CustomNodeSourceResolutionRequest,
    NodeRegistryResolutionError,
    NodeRegistryResolutionErrorCode,
    NodeRegistrySourceKind,
    NodeRegistryResolver,
    NodeRegistrySource,
)
from app.source_policy import SourcePolicy
from app.trust import (
    TrustVerificationResult,
    TrustVerificationStatus,
    TrustVerifier,
    imported_archive_trust_payload,
)
from app.workflows.package import (
    DashboardSchema,
    RequiredModel,
    UnresolvedRuntimeInput,
    WorkflowAssetMetadata,
    WorkflowCustomNodeRecord,
    WorkflowImportMetadata,
    WorkflowInput,
    WorkflowMetadata,
    WorkflowOutput,
    WorkflowPackage,
    WorkflowPackageIdentity,
    SignedRegistryMetadata,
    WorkflowPackageSignature,
    WorkflowSmokeTests,
)
from app.workflows.package_assets import (
    PackageAssetError,
    is_package_asset_value,
    package_asset_archive_path,
    validate_package_asset_reference,
)
from app.workflows.archive_validation import (
    ArchiveValidationError,
    ignored_archive_member,
    safe_archive_name,
    single_wrapper_root,
    strip_wrapper_root,
    validate_archive_members,
    zip_member_is_symlink,
)
from app.workflows.model_grouping import unique_required_models
from app.workflows.package_persistence import write_imported_package_transaction
from app.workflows.import_runtime_profile import (
    RuntimeProfileSelectionError,
    current_architecture,
    current_os_name,
    has_nvidia_gpu,
    preferred_gpu_backend,
    select_import_runtime_profile,
)
from app.workflows.import_policy import (
    explicit_node_registry_source,
    graph_node_types,
    import_status,
    import_status_message,
    is_resolvable_workflow_node_type,
    non_bundled_required_custom_node_records,
    package_with_import_resolution_status,
    package_with_source_policy,
    package_with_trust_verification,
    source_policy_status_for_import,
    trust_level_from_string,
)
from app.workflows.import_normalization import (
    ImportNormalizationError,
    comfyui_api_graph_from_ui_workflow,
    comfyui_workflow_node_types,
    detect_unresolved_runtime_inputs,
    executable_comfyui_workflow_definition_node_types,
    filter_resolved_runtime_inputs,
    has_nonempty_launch_option,
    is_comfyui_ui_workflow,
    normalize_asset_ownership,
    normalize_custom_nodes,
    normalize_dashboard,
    normalize_model_verification_level,
    normalize_models,
    required_models_from_comfyui_workflow,
    normalize_unresolved_runtime_inputs,
    normalize_signed_registry_metadata,
    normalize_signatures,
    normalize_trust_level,
    normalized_display_name,
    observed_hardware,
    optional_string_field,
    raw_comfyui_api_graph,
    reject_unsupported_exported_launch_options,
    repair_misclassified_multimodal_text_inputs,
    string_field,
)
from app.workflows.import_capsule_lock import (
    ImportCapsuleLockError,
    hardware_observations_from_package,
    imported_package_capsule_lock as build_imported_package_capsule_lock,
    launch_config_hash,
    model_id,
    model_locks_from_package,
)
from app.workflows.library import workflow_package_display_name
from app.workflows.store_paths import (
    assert_path_within,
    imported_workflow_id,
    mutable_package_dir,
    package_identity_dir,
    safe_store_segment,
)
from app.workflows.widget_metadata import normalize_comfyui_widget_metadata

NOOFY_ARCHIVE_SCHEMA_VERSION = "0.1.0"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_JSON_BYTES = 16 * 1024 * 1024
REQUIRED_FILES = {
    "package.json",
    "comfyui_graph.json",
    "capsule.lock.json",
    "export-report.json",
}
class NoofyImportError(RuntimeError):
    """Raised when a `.noofy` archive cannot be safely imported."""


class WorkflowDataImporter(Protocol):
    def normalize(self) -> WorkflowPackage: ...
    def trust_payload(self) -> dict[str, Any]: ...
    def extract_source_files(self, target_dir: Path) -> None: ...


class GitHubCustomNodeUrlResolver(Protocol):
    def resolve(self, url: str, *, node_type: str) -> tuple[str, NodeRegistrySource]: ...


class GitHubCustomNodeUrlResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitHubCustomNodeSearchTarget:
    package_id: str | None
    names: tuple[str, ...]
    node_types: tuple[str, ...]
    node_titles: tuple[str, ...]
    family_terms: tuple[str, ...]


@dataclass(frozen=True)
class GitHubCustomNodeCandidate:
    candidate_id: str
    owner: str
    repo: str
    html_url: str
    description: str | None
    stargazers_count: int
    updated_at: str | None
    source: NodeRegistrySource
    evidence: tuple[str, ...]
    evidence_score: int
    name_match: str
    confidence: str
    specific_match_score: int = 0


class DefaultGitHubCustomNodeUrlResolver:
    """Resolve user-provided GitHub repository URLs into pinned archive facts."""

    def resolve(self, url: str, *, node_type: str) -> tuple[str, NodeRegistrySource]:
        owner, repo, requested_ref = _parse_github_repo_url(url)
        commit_sha = _github_commit_sha(owner, repo, requested_ref)
        archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/{commit_sha}"
        archive_hash = _sha256_url(archive_url)
        package_id = safe_store_segment(f"github-{owner}-{repo}")
        return package_id, NodeRegistrySource(
            source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
            source_url=archive_url,
            source_ref=commit_sha,
            source_content_hash=f"sha256:{archive_hash}",
            archive_subdir=f"{repo}-{commit_sha}",
            source_repo_url=f"https://github.com/{owner}/{repo}",
        )

    def find_candidates(
        self,
        target: GitHubCustomNodeSearchTarget,
    ) -> list[GitHubCustomNodeCandidate]:
        candidates: dict[str, dict[str, Any]] = {}
        for query in _github_custom_node_search_queries(target):
            for item in _github_search_repositories(query):
                full_name = item.get("full_name")
                if not isinstance(full_name, str) or "/" not in full_name:
                    continue
                candidates.setdefault(full_name.casefold(), item)
                if len(candidates) >= 8:
                    break
            if len(candidates) >= 8:
                break

        inspected: list[GitHubCustomNodeCandidate] = []
        for item in candidates.values():
            try:
                inspected.append(_inspect_github_custom_node_candidate(item, target))
            except GitHubCustomNodeUrlResolutionError:
                continue
        return inspected


class ImportedWorkflowPackageStore:
    """Stores normalized imported workflow packages under workflow-store."""

    def __init__(
        self,
        root_dir: Path,
        *,
        log_store: DiagnosticsSink,
        dashboard_assets_dir: Path | None = None,
        node_registry_resolver: NodeRegistryResolver | None = None,
        custom_node_source_cache: CustomNodeSourceCache | None = None,
        custom_node_github_resolver: GitHubCustomNodeUrlResolver | None = None,
        trust_verifier: TrustVerifier | None = None,
        runtime_profile_catalog_provider: Callable[[], RuntimeProfileCatalog] | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.log_store = log_store
        self.dashboard_assets_dir = dashboard_assets_dir or settings.paths.dashboard_assets_dir
        self.node_registry_resolver = node_registry_resolver
        self.custom_node_source_cache = custom_node_source_cache
        self.custom_node_github_resolver = (
            custom_node_github_resolver or DefaultGitHubCustomNodeUrlResolver()
        )
        self.trust_verifier = trust_verifier or TrustVerifier()
        self.runtime_profile_catalog_provider = runtime_profile_catalog_provider

    def preview_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ) -> WorkflowPackage:
        importer = workflow_importer_for_data(data, original_filename=original_filename)
        package = importer.normalize()
        package = self._with_detected_raw_comfyui_custom_nodes(package)
        package = self._with_verified_import_trust(package, importer.trust_payload())
        package = _package_with_source_policy(
            package,
            community_preparation_opted_in=allow_unverified_community_preparation,
            policy_status=_source_policy_status_for_import(package),
        )
        return self._with_automatic_custom_node_resolution(
            package,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )

    def import_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
        duplicate_action: str | None = None,
    ) -> WorkflowPackage:
        try:
            importer = workflow_importer_for_data(data, original_filename=original_filename)
            package = importer.normalize()
            package = self._with_detected_raw_comfyui_custom_nodes(package)
            package = self._with_verified_import_trust(
                package, importer.trust_payload()
            )
            package = _package_with_source_policy(
                package,
                community_preparation_opted_in=allow_unverified_community_preparation,
                policy_status=_source_policy_status_for_import(package),
            )
            package = self._with_automatic_custom_node_resolution(
                package,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
            )
            return self._persist_imported_archive(
                data,
                importer=importer,
                package=package,
                original_filename=original_filename,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
                duplicate_action=duplicate_action,
            )
        except Exception as exc:
            self._log_import_failure(original_filename, exc)
            raise

    def import_prepared_archive(
        self,
        data: bytes,
        *,
        package: WorkflowPackage,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
        duplicate_action: str | None = None,
    ) -> WorkflowPackage:
        try:
            return self._persist_imported_archive(
                data,
                importer=workflow_importer_for_data(data, original_filename=original_filename),
                package=package,
                original_filename=original_filename,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
                duplicate_action=duplicate_action,
            )
        except Exception as exc:
            self._log_import_failure(original_filename, exc)
            raise

    def resolve_custom_nodes_from_github_urls(
        self,
        package: WorkflowPackage,
        *,
        urls_by_node_type: dict[str, str],
        allow_unverified_community_preparation: bool,
    ) -> WorkflowPackage:
        source_resolution = _source_resolution_details(package)
        if not source_resolution:
            return package
        unresolved_node_types = [
            item for item in source_resolution.get("unresolved_node_types", [])
            if isinstance(item, str)
        ]
        ambiguous_node_types = [
            item.get("node_type")
            for item in source_resolution.get("ambiguous_node_types", [])
            if isinstance(item, dict) and isinstance(item.get("node_type"), str)
        ]
        node_types = sorted(set(unresolved_node_types + ambiguous_node_types))
        if not node_types:
            return package

        records_by_id = {record.id: record for record in package.custom_nodes}
        resolved_reports: list[dict[str, object]] = []
        still_unresolved: list[str] = []
        failed: list[dict[str, object]] = []
        for node_type in node_types:
            raw_url = urls_by_node_type.get(node_type, "").strip()
            if not raw_url:
                still_unresolved.append(node_type)
                continue
            try:
                package_id, source = self.custom_node_github_resolver.resolve(
                    raw_url,
                    node_type=node_type,
                )
            except Exception as exc:
                failed.append(
                    {
                        "node_type": node_type,
                        "url": raw_url,
                        "error": str(exc),
                    }
                )
                continue
            _remove_superseded_custom_node_records(
                records_by_id,
                package_id=package_id,
                node_types=[node_type],
            )
            existing = records_by_id.get(package_id)
            if existing is None:
                records_by_id[package_id] = WorkflowCustomNodeRecord(
                    id=package_id,
                    folder_name=package_id,
                    source=source.source_url,
                    included=False,
                    node_types=[node_type],
                    source_ref=source.source_ref,
                    source_content_hash=source.source_content_hash,
                    source_archive_subdir=source.archive_subdir,
                    source_repo_url=source.source_repo_url,
                    resolution_method="user_github_url",
                )
            else:
                records_by_id[package_id] = existing.model_copy(
                    update={
                        "source": source.source_url,
                        "included": False,
                        "node_types": sorted(set(existing.node_types) | {node_type}),
                        "source_ref": source.source_ref,
                        "source_content_hash": source.source_content_hash,
                        "source_archive_subdir": source.archive_subdir,
                        "source_repo_url": source.source_repo_url,
                        "resolution_method": "user_github_url",
                    }
                )
            resolved_reports.append(
                {
                    "node_type": node_type,
                    "package_id": package_id,
                    "source_ref": source.source_ref,
                    "resolution_method": "user_github_url",
                }
            )

        updated = package.model_copy(update={"custom_nodes": list(records_by_id.values())})
        if still_unresolved or failed:
            return _missing_raw_custom_nodes(
                updated,
                reason="user_github_url_resolution_failed",
                unresolved_node_types=still_unresolved,
                ambiguous_node_types=[],
                resolved_custom_nodes=resolved_reports,
                failed_custom_nodes=failed,
            )

        updated = _package_with_raw_custom_node_detection(
            updated,
            {
                "status": "resolved_from_user_github_urls",
                "required_custom_node_types": node_types,
                "resolved_custom_nodes": resolved_reports,
            },
        )
        updated = _package_with_source_policy(
            updated,
            community_preparation_opted_in=allow_unverified_community_preparation,
            policy_status=_source_policy_status_for_import(updated),
        )
        return self._with_resolved_community_sources(
            updated,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )

    def resolve_custom_nodes_from_approved_candidate(
        self,
        package: WorkflowPackage,
        *,
        candidate_id: str,
        allow_unverified_community_preparation: bool,
    ) -> WorkflowPackage:
        source_resolution = _source_resolution_details(package)
        candidate = source_resolution.get("candidate")
        if not isinstance(candidate, dict) or candidate.get("candidate_id") != candidate_id:
            return _missing_custom_node_resolution(
                package,
                reason="approved_candidate_not_found",
                unresolved_node_types=_source_resolution_node_types(source_resolution),
                failed_custom_nodes=[
                    {
                        "candidate_id": candidate_id,
                        "error": "Candidate is no longer available for this import session.",
                    }
                ],
            )
        source_payload = candidate.get("source")
        if not isinstance(source_payload, dict):
            return _missing_custom_node_resolution(
                package,
                reason="approved_candidate_missing_source",
                unresolved_node_types=_source_resolution_node_types(source_resolution),
            )
        try:
            source = NodeRegistrySource.model_validate(source_payload)
        except ValidationError as exc:
            return _missing_custom_node_resolution(
                package,
                reason="approved_candidate_invalid_source",
                unresolved_node_types=_source_resolution_node_types(source_resolution),
                failed_custom_nodes=[
                    {
                        "candidate_id": candidate_id,
                        "error": str(exc),
                    }
                ],
            )
        package_id = _package_id_for_github_source(source, fallback=candidate.get("repo"))
        node_types = _source_resolution_node_types(source_resolution)
        updated = self._package_with_candidate_source(
            package,
            package_id=package_id,
            node_types=node_types,
            source=source,
            resolution_method="github_search_approved",
        )
        return self._with_resolved_community_sources(
            updated,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )

    def mark_no_custom_nodes_guidance(self, package: WorkflowPackage) -> WorkflowPackage:
        source_resolution = _source_resolution_details(package)
        unresolved = []
        ambiguous = []
        if source_resolution:
            unresolved = [
                item for item in source_resolution.get("unresolved_node_types", [])
                if isinstance(item, str)
            ]
            ambiguous = [
                item for item in source_resolution.get("ambiguous_node_types", [])
                if isinstance(item, dict)
            ]
        return _package_with_import_resolution_status(
            package,
            status="needs_comfyui_update",
            message=(
                "Noofy could not find these nodes in the selected managed ComfyUI runtime. "
                "Update managed ComfyUI from Settings to a newer version, then retry preparation."
            ),
            source_resolution={
                "status": "needs_comfyui_update",
                "reason": "user_asserted_no_custom_nodes",
                "unresolved_node_types": unresolved,
                "ambiguous_node_types": ambiguous,
                "update_guidance": (
                    "Update managed ComfyUI from Settings to a newer version, then retry preparation. "
                    "If the nodes are still missing, provide the GitHub URL for the custom node repository."
                ),
            },
        )

    def _with_automatic_custom_node_resolution(
        self,
        package: WorkflowPackage,
        *,
        allow_unverified_community_preparation: bool,
    ) -> WorkflowPackage:
        if not _package_needs_automatic_custom_node_resolution(package):
            return package
        if (
            package.source_policy is not None
            and not package.source_policy.automatic_preparation_allowed
        ):
            return package

        registry_attempt = self._with_resolved_community_sources(
            package,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
            allow_missing_status=True,
        )
        if not _package_needs_automatic_custom_node_resolution(registry_attempt):
            return registry_attempt
        package = registry_attempt

        if not _source_policy_allows_github_custom_node_resolution(package.source_policy):
            return _package_with_import_resolution_status(
                package,
                status="unsupported",
                message="Unsupported workflow",
                source_resolution={
                    "status": "failed",
                    "reason": "source_policy_blocks_github_resolution",
                    "unresolved_node_types": _source_resolution_node_types(
                        _source_resolution_details(package)
                    ),
                    "automatic_resolution_failures": [
                        "This package policy only allows verified or Noofy-controlled custom-node sources."
                    ],
                },
            )

        target = _github_search_target_for_package(package)
        if target is None:
            return package
        candidates, search_failures = self._github_custom_node_candidates(target)
        if not candidates:
            return _missing_custom_node_resolution(
                package,
                reason="github_search_no_candidate",
                unresolved_node_types=list(target.node_types),
                missing_package_id=target.package_id,
                automatic_resolution_failures=search_failures
                or [
                    "Noofy searched package names and node types but did not find a candidate with enough ComfyUI evidence."
                ],
            )

        candidates = sorted(candidates, key=_github_candidate_sort_key, reverse=True)
        best = candidates[0]
        if best.confidence == "high":
            package_id = _package_id_for_github_source(best.source, fallback=best.repo)
            updated = self._package_with_candidate_source(
                package,
                package_id=package_id,
                node_types=list(target.node_types),
                source=best.source,
                resolution_method="github_search_auto",
            )
            resolved = self._with_resolved_community_sources(
                updated,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
                allow_missing_status=True,
            )
            self.log_store.add(
                "info",
                "Automatically resolved custom-node source from GitHub search",
                "workflow.import",
                workflow_id=package.metadata.id,
                details={
                    "candidate_id": best.candidate_id,
                    "repo": f"{best.owner}/{best.repo}",
                    "node_types": list(target.node_types),
                    "evidence_score": best.evidence_score,
                },
            )
            return resolved

        if best.confidence == "medium":
            return _missing_custom_node_resolution(
                package,
                reason="github_search_needs_user_approval",
                unresolved_node_types=list(target.node_types),
                missing_package_id=target.package_id,
                candidate=_github_candidate_payload(best, include_source=True),
                automatic_resolution_failures=[
                    "Noofy found a likely repository, but the match is not strong enough for silent install."
                ],
            )

        return _missing_custom_node_resolution(
            package,
            reason="github_search_low_confidence",
            unresolved_node_types=list(target.node_types),
            missing_package_id=target.package_id,
            candidate=_github_candidate_payload(best, include_source=True),
            automatic_resolution_failures=[
                "Noofy found search results, but none had enough ComfyUI evidence for automatic installation."
            ],
        )

    def _github_custom_node_candidates(
        self,
        target: GitHubCustomNodeSearchTarget,
    ) -> tuple[list[GitHubCustomNodeCandidate], list[str]]:
        finder = getattr(self.custom_node_github_resolver, "find_candidates", None)
        if finder is None:
            return [], []
        try:
            candidates = finder(target)
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Automatic GitHub custom-node search failed",
                "workflow.import",
                details={"error": str(exc), "node_types": list(target.node_types)},
            )
            return [], [f"GitHub search failed: {exc}"]
        return (
            [
                candidate
                for candidate in candidates
                if isinstance(candidate, GitHubCustomNodeCandidate)
            ],
            [],
        )

    def _package_with_candidate_source(
        self,
        package: WorkflowPackage,
        *,
        package_id: str,
        node_types: list[str],
        source: NodeRegistrySource,
        resolution_method: str,
    ) -> WorkflowPackage:
        records_by_id = {record.id: record for record in package.custom_nodes}
        _remove_superseded_custom_node_records(
            records_by_id,
            package_id=package_id,
            node_types=node_types,
        )
        existing = records_by_id.get(package_id)
        if existing is None:
            records_by_id[package_id] = WorkflowCustomNodeRecord(
                id=package_id,
                folder_name=package_id,
                source=source.source_url,
                included=False,
                node_types=sorted(set(node_types)),
                source_ref=source.source_ref,
                source_content_hash=source.source_content_hash,
                source_cache_ref=None,
                source_archive_subdir=source.archive_subdir,
                source_repo_url=source.source_repo_url,
                resolution_method=resolution_method,
            )
        else:
            records_by_id[package_id] = existing.model_copy(
                update={
                    "source": source.source_url,
                    "included": False,
                    "node_types": sorted(set(existing.node_types) | set(node_types)),
                    "source_ref": source.source_ref,
                    "source_content_hash": source.source_content_hash,
                    "source_cache_ref": None,
                    "source_archive_subdir": source.archive_subdir,
                    "source_repo_url": source.source_repo_url,
                    "resolution_method": resolution_method,
                }
            )
        return package.model_copy(update={"custom_nodes": list(records_by_id.values())})

    def _persist_imported_archive(
        self,
        data: bytes,
        *,
        importer: WorkflowDataImporter,
        package: WorkflowPackage,
        original_filename: str | None,
        allow_unverified_community_preparation: bool,
        duplicate_action: str | None,
    ) -> WorkflowPackage:
        if duplicate_action == "copy":
            package = _package_imported_as_copy(package, self.root_dir)
        package = self._with_detected_raw_comfyui_custom_nodes(package)
        package = _package_with_source_policy(
            package,
            community_preparation_opted_in=allow_unverified_community_preparation,
            policy_status=_source_policy_status_for_import(package),
        )
        if (
            package.import_metadata is None
            or package.import_metadata.status != "missing_custom_nodes"
        ):
            package = self._with_resolved_community_sources(
                package,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
            )
        target_dir = self.package_dir(package)
        runtime_resolution_unavailable: dict[str, object] | None = None
        try:
            app_capsule_lock: CapsuleLock | None = self._build_capsule_lock(package)
        except ImportCapsuleLockError as exc:
            if isinstance(exc.__cause__, RuntimeProfileSelectionError):
                runtime_resolution_unavailable = _unsupported_local_runtime_resolution(
                    exc.__cause__
                )
            if runtime_resolution_unavailable is not None:
                self.log_store.add(
                    "warning",
                    "Capsule lock unavailable — no runtime profile for this platform",
                    "workflow.import",
                    details={
                        **runtime_resolution_unavailable,
                        "error": str(exc),
                    },
                )
                app_capsule_lock = None
            else:
                raise NoofyImportError(str(exc)) from exc
        try:
            write_imported_package_transaction(
                root_dir=self.root_dir,
                target_dir=target_dir,
                package=package,
                app_capsule_lock=app_capsule_lock,
                runtime_resolution_unavailable=runtime_resolution_unavailable,
                archive_data=data,
                original_filename=original_filename,
                schema_version=NOOFY_ARCHIVE_SCHEMA_VERSION,
                extract_source_files=importer.extract_source_files,
                dashboard_assets_dir=self.dashboard_assets_dir,
                replace_existing=duplicate_action == "replace",
            )
        except FileExistsError as exc:
            raise NoofyImportError(str(exc)) from exc

        self.log_store.add(
            "info",
            "Imported workflow package",
            "workflow.import",
            workflow_id=package.metadata.id,
            details={
                "publisher_id": (
                    package.identity.publisher_id if package.identity else None
                ),
                "package_id": package.identity.package_id if package.identity else None,
                "version": package.identity.version if package.identity else None,
                "custom_node_count": len(package.custom_nodes),
                "required_model_count": len(unique_required_models(package.required_models)),
                **_raw_comfyui_log_details(package),
                "unresolved_input_count": len(package.unresolved_runtime_inputs),
            },
        )
        return package

    def persist_model_identities(self, package: WorkflowPackage) -> None:
        package_dir = self.package_dir(package)
        package_file = package_dir / "package.json"
        capsule_file = package_dir / "capsule.lock.json"
        assert_path_within(self.root_dir, package_file, purpose="update imported workflow models")
        assert_path_within(self.root_dir, capsule_file, purpose="update imported workflow capsule")

        package_payload = json.loads(package_file.read_text(encoding="utf-8"))
        package_payload["required_models"] = [
            model.model_dump(mode="json", exclude_none=True)
            for model in package.required_models
        ]
        try:
            capsule_lock = self._build_capsule_lock(package)
        except ImportCapsuleLockError as exc:
            if not isinstance(exc.__cause__, RuntimeProfileSelectionError):
                raise
            capsule_lock = None

        package_tmp = package_file.with_suffix(".json.tmp")
        capsule_tmp = capsule_file.with_suffix(f".json.{uuid4().hex}.tmp")
        try:
            package_tmp.write_text(
                json.dumps(package_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if capsule_lock is not None:
                capsule_tmp.write_text(
                    capsule_lock.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            package_tmp.replace(package_file)
            if capsule_lock is not None:
                capsule_tmp.replace(capsule_file)
        finally:
            package_tmp.unlink(missing_ok=True)
            capsule_tmp.unlink(missing_ok=True)
        self.log_store.add(
            "info",
            "Persisted updated workflow model identities",
            "workflow.models",
            workflow_id=package.metadata.id,
            details={
                "model_count": len(unique_required_models(package.required_models)),
                "capsule_lock_updated": capsule_lock is not None,
            },
        )

    def refresh_capsule_lock(self, package: WorkflowPackage) -> CapsuleLock | None:
        """Refresh the app-owned lock when the selected runtime profile changes."""
        capsule_file = self.package_dir(package) / "capsule.lock.json"
        assert_path_within(
            self.root_dir,
            capsule_file,
            purpose="refresh imported workflow capsule",
        )
        try:
            capsule_lock = self._build_capsule_lock(package)
        except ImportCapsuleLockError as exc:
            if not isinstance(exc.__cause__, RuntimeProfileSelectionError):
                raise NoofyImportError(str(exc)) from exc
            return None

        try:
            current = CapsuleLock.model_validate_json(
                capsule_file.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            current = None
        if current == capsule_lock:
            return capsule_lock

        capsule_tmp = capsule_file.with_suffix(".json.tmp")
        try:
            capsule_tmp.write_text(
                capsule_lock.model_dump_json(indent=2),
                encoding="utf-8",
            )
            capsule_tmp.replace(capsule_file)
        finally:
            capsule_tmp.unlink(missing_ok=True)
        self.log_store.add(
            "info",
            "Refreshed imported workflow runtime capsule",
            "workflow.runtime",
            workflow_id=package.metadata.id,
            details={
                "previous_capsule_fingerprint": (
                    current.runtime.capsule_fingerprint if current is not None else None
                ),
                "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                "runtime_profile_manifest_hash": (
                    capsule_lock.runtime.runtime_profile_manifest_hash
                ),
            },
        )
        return capsule_lock

    def _build_capsule_lock(self, package: WorkflowPackage) -> CapsuleLock:
        catalog = (
            self.runtime_profile_catalog_provider()
            if self.runtime_profile_catalog_provider is not None
            else None
        )
        if catalog is None:
            return build_imported_package_capsule_lock(package)
        return build_imported_package_capsule_lock(
            package,
            runtime_profile_catalog=catalog,
        )

    def _log_import_failure(
        self,
        original_filename: str | None,
        exc: Exception,
    ) -> None:
        self.log_store.add(
            "warning",
            "Workflow import failed",
            "workflow.import",
            details={
                "original_filename": original_filename,
                "error": str(exc),
            },
        )

    def package_dir(self, package: WorkflowPackage) -> Path:
        package_dir = mutable_package_dir(self.root_dir, package)
        if package_dir is None:
            raise NoofyImportError("Imported package is missing identity metadata")
        return package_dir

    def has_package_identity(self, package: WorkflowPackage) -> bool:
        package_dir = mutable_package_dir(self.root_dir, package)
        return package_dir is not None and package_dir.exists()

    def _with_detected_raw_comfyui_custom_nodes(
        self,
        package: WorkflowPackage,
    ) -> WorkflowPackage:
        raw_details = _raw_comfyui_details(package)
        if raw_details is None:
            return package

        core_node_types = self._selected_core_runtime_node_types()
        detected_node_types = _raw_comfyui_required_node_types(package)
        missing_node_types = sorted(
            node_type
            for node_type in detected_node_types
            if node_type not in core_node_types
        )
        raw_details = {
            **raw_details,
            "executable_node_types": sorted(detected_node_types),
            "core_node_count": len(core_node_types),
            "required_custom_node_types": missing_node_types,
        }
        package = _package_with_raw_comfyui_details(package, raw_details)
        if not missing_node_types:
            return _package_with_raw_custom_node_detection(
                package,
                {
                    "status": "core_only",
                    "required_custom_node_types": [],
                },
            )

        if self.node_registry_resolver is None:
            self.log_store.add(
                "warning",
                "Raw ComfyUI JSON custom-node registry unavailable",
                "workflow.import",
                workflow_id=package.metadata.id,
                details={"unresolved_node_types": missing_node_types},
            )
            return _unsupported_raw_custom_nodes(
                package,
                reason="custom_node_registry_not_configured",
                unresolved_node_types=missing_node_types,
            )

        records_by_id = {record.id: record for record in package.custom_nodes}
        node_types_by_package_id: dict[str, set[str]] = {
            record.id: set(record.node_types) for record in package.custom_nodes
        }
        resolved_reports: list[dict[str, object]] = []
        unresolved_node_types: list[str] = []
        ambiguous_node_types: list[dict[str, object]] = []
        for node_type in missing_node_types:
            if any(node_type in node_types for node_types in node_types_by_package_id.values()):
                continue
            try:
                entry, method = self.node_registry_resolver.registry_entry_for_node_types(
                    [node_type]
                )
            except NodeRegistryResolutionError as exc:
                if exc.code is NodeRegistryResolutionErrorCode.AMBIGUOUS_NODE_TYPE:
                    ambiguous_node_types.append(
                        {
                            "node_type": node_type,
                            "package_ids": exc.developer_details.get(
                                "package_ids", []
                            ),
                        }
                    )
                else:
                    unresolved_node_types.append(node_type)
                continue
            node_types_by_package_id.setdefault(entry.package_id, set()).add(node_type)
            resolved_reports.append(
                {
                    "node_type": node_type,
                    "package_id": entry.package_id,
                    "resolution_method": method,
                }
            )

        if unresolved_node_types or ambiguous_node_types:
            self.log_store.add(
                "warning",
                "Raw ComfyUI JSON custom-node requirements could not be resolved",
                "workflow.import",
                workflow_id=package.metadata.id,
                details={
                    "unresolved_node_types": unresolved_node_types,
                    "ambiguous_node_types": ambiguous_node_types,
                    "resolved_custom_nodes": resolved_reports,
                },
            )
            return _unsupported_raw_custom_nodes(
                package,
                reason="unresolved_custom_node_types",
                unresolved_node_types=unresolved_node_types,
                ambiguous_node_types=ambiguous_node_types,
                resolved_custom_nodes=resolved_reports,
            )

        for package_id, node_types in node_types_by_package_id.items():
            existing = records_by_id.get(package_id)
            if existing is not None:
                records_by_id[package_id] = existing.model_copy(
                    update={"node_types": sorted(set(existing.node_types) | node_types)}
                )
                continue
            records_by_id[package_id] = WorkflowCustomNodeRecord(
                id=package_id,
                folder_name=package_id,
                source=f"registry_metadata:{package_id}",
                included=False,
                node_types=sorted(node_types),
            )

        updated_package = package.model_copy(
            update={"custom_nodes": list(records_by_id.values())}
        )
        self.log_store.add(
            "info",
            "Raw ComfyUI JSON custom-node requirements detected",
            "workflow.import",
            workflow_id=package.metadata.id,
            details={
                "required_custom_node_types": missing_node_types,
                "custom_node_package_ids": sorted(node_types_by_package_id),
                "source_format": raw_details.get("source_format"),
            },
        )
        return _package_with_raw_custom_node_detection(
            updated_package,
            {
                "status": "resolved_to_registry_packages",
                "required_custom_node_types": missing_node_types,
                "resolved_custom_nodes": resolved_reports,
            },
        )

    def _selected_core_runtime_node_types(self) -> set[str]:
        catalog = (
            self.runtime_profile_catalog_provider()
            if self.runtime_profile_catalog_provider is not None
            else None
        )
        if catalog is None:
            from app.runtime.profiles import load_runtime_profile_catalog
            from app.runtime.profiles.profiles import DEFAULT_RUNTIME_PROFILE_CATALOG_PATH

            catalog = load_runtime_profile_catalog(DEFAULT_RUNTIME_PROFILE_CATALOG_PATH)
        core_catalog = core_node_manifest_catalog_for_runtime_profiles(
            load_core_node_manifest_catalog(),
            catalog,
        )
        profile, variant = _select_import_runtime_profile(catalog.profiles)
        manifest = core_catalog.get(
            runtime_profile_id=profile.runtime_profile_id,
            runtime_profile_variant_id=variant.runtime_profile_variant_id,
            runtime_profile_manifest_hash=profile.runtime_profile_manifest_hash,
        )
        return set(manifest.node_types)

    def _with_resolved_community_sources(
        self,
        package: WorkflowPackage,
        *,
        allow_unverified_community_preparation: bool,
        allow_missing_status: bool = False,
    ) -> WorkflowPackage:
        required_records = _non_bundled_required_custom_node_records(package)
        if not required_records:
            return package

        if package.identity is None:
            return _package_with_import_resolution_status(
                package,
                status="unsupported",
                message="Unsupported workflow",
                source_resolution={
                    "status": "failed",
                    "reason": "missing_identity",
                },
            )
        trust_level = _trust_level_from_string(package.identity.trust_level)
        if trust_level is TrustLevel.UNSUPPORTED:
            return package
        if (
            trust_level is TrustLevel.QUARANTINED_COMMUNITY
            and not allow_unverified_community_preparation
        ):
            return _package_with_import_resolution_status(
                package,
                status="blocked_by_policy",
                message="Needs permission to prepare community workflow",
                source_resolution={
                    "status": "blocked_by_policy",
                    "reason": "community_opt_in_required",
                    "unresolved_custom_nodes": [
                        record.id for record in required_records
                    ],
                },
            )
        if self.node_registry_resolver is None or self.custom_node_source_cache is None:
            return _package_with_import_resolution_status(
                package,
                status="unsupported",
                message="Unsupported workflow",
                source_resolution={
                    "status": "failed",
                    "reason": "source_resolution_not_configured",
                    "unresolved_custom_nodes": [
                        record.id for record in required_records
                    ],
                },
            )

        records_by_id = {record.id: record for record in package.custom_nodes}
        resolved_reports: list[dict[str, object]] = []
        for record in required_records:
            try:
                explicit_source = _explicit_node_registry_source(record)
                resolved = self.node_registry_resolver.resolve(
                    CustomNodeSourceResolutionRequest(
                        package_id=record.id,
                        node_types=record.node_types,
                        trust_level=trust_level,
                        allow_unverified_community_preparation=allow_unverified_community_preparation,
                        explicit_source=explicit_source,
                        source_policy=package.source_policy,
                    )
                )
                cached = self.custom_node_source_cache.materialize(
                    resolved.source,
                    source_policy=package.source_policy,
                    source_origins=resolved.source_policy_origins(),
                )
            except NodeRegistryResolutionError as exc:
                self.log_store.add(
                    "warning",
                    "Imported workflow custom-node source resolution failed",
                    "workflow.import",
                    workflow_id=package.metadata.id,
                    details={
                        "package_id": record.id,
                        "code": exc.code.value,
                        **exc.developer_details,
                    },
                )
                if allow_missing_status:
                    return _missing_custom_node_resolution(
                        package,
                        reason=exc.code.value,
                        missing_package_id=record.id,
                        unresolved_node_types=record.node_types,
                        failed_custom_nodes=[
                            {
                                "package_id": record.id,
                                "node_types": record.node_types,
                                "code": exc.code.value,
                                "developer_details": exc.developer_details,
                            }
                        ],
                    )
                return _package_with_import_resolution_status(
                    package,
                    status="unsupported",
                    message="Unsupported workflow",
                    source_resolution={
                        "status": "failed",
                        "package_id": record.id,
                        "code": exc.code.value,
                        "developer_details": exc.developer_details,
                    },
                )
            if resolved.package_id != record.id:
                records_by_id.pop(record.id, None)
            resolution_method = (
                resolved.resolution_method
                if record.resolution_method == "missing_bundled_source"
                else record.resolution_method or resolved.resolution_method
            )
            records_by_id[resolved.package_id] = record.model_copy(
                update={
                    "id": resolved.package_id,
                    "folder_name": resolved.package_id,
                    "included": True,
                    "source": resolved.source.source_url,
                    "source_ref": resolved.source.source_ref,
                    "source_content_hash": resolved.source.source_content_hash,
                    "source_cache_ref": cached.source_cache_ref,
                    "source_archive_subdir": resolved.source.archive_subdir,
                    "source_repo_url": resolved.source.source_repo_url,
                    "resolution_method": resolution_method,
                }
            )
            resolved_reports.append(
                {
                    "package_id": resolved.package_id,
                    "resolution_method": resolution_method,
                    "source_ref": resolved.source.source_ref,
                    "source_cache_ref": cached.source_cache_ref,
                    "source_archive_subdir": resolved.source.archive_subdir,
                    "source_repo_url": resolved.source.source_repo_url,
                }
            )

        updated_package = package.model_copy(
            update={"custom_nodes": list(records_by_id.values())}
        )
        _status = _import_status(
            updated_package.unresolved_runtime_inputs, updated_package.dashboard
        )
        return _package_with_import_resolution_status(
            updated_package,
            status=_status,
            message=_import_status_message(_status),
            source_resolution={
                "status": "resolved",
                "resolved_custom_nodes": resolved_reports,
            },
        )

    def _with_verified_import_trust(
        self,
        package: WorkflowPackage,
        trust_payload: dict[str, Any],
    ) -> WorkflowPackage:
        if package.identity is None:
            return package
        requested_trust_level = _trust_level_from_string(package.identity.trust_level)
        verification = self.trust_verifier.verify_imported_package(
            requested_trust_level=requested_trust_level,
            payload=trust_payload,
            signatures=package.identity.signatures,
            signed_registry_metadata=package.identity.signed_registry_metadata,
        )
        package = _package_with_trust_verification(package, verification)
        if verification.status in {
            TrustVerificationStatus.NOT_REQUIRED,
            TrustVerificationStatus.VERIFIED,
        }:
            return package
        self.log_store.add(
            "warning",
            "Imported workflow trust verification failed",
            "workflow.import",
            workflow_id=package.metadata.id,
            details=verification.model_dump(mode="json"),
        )
        return _package_with_import_resolution_status(
            package,
            status="unsupported",
            message="Unsupported workflow",
            source_resolution={
                "status": "failed",
                "reason": "trust_verification_failed",
                "trust_verification": verification.model_dump(mode="json"),
            },
        )


class NoofyArchiveImporter:
    """Safely inspects a `.noofy` zip archive as data."""

    def __init__(self, data: bytes, *, original_filename: str | None = None) -> None:
        if len(data) > MAX_ARCHIVE_BYTES:
            raise NoofyImportError(
                "Workflow package is too large to import automatically."
            )
        self.data = data
        self.original_filename = original_filename
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise NoofyImportError(
                "Workflow package is not a valid .noofy archive."
            ) from exc
        self.members = self._validated_members()
        self._json_cache: dict[str, dict[str, Any]] = {}

    def normalize(self) -> WorkflowPackage:
        package_json = self._read_json("package.json")
        graph = self._read_json("comfyui_graph.json")
        dashboard_json = (
            self._read_json("dashboard.json")
            if "dashboard.json" in self.members
            else {}
        )
        capsule_json = self._read_json("capsule.lock.json")
        export_report = self._read_json("export-report.json")

        if not isinstance(graph, dict):
            raise NoofyImportError("Workflow graph must be a JSON object.")

        publisher_id = _string_field(package_json, "publisher_id", fallback="unknown")
        package_id = _string_field(package_json, "package_id", fallback="workflow")
        version = _string_field(package_json, "version", fallback="0.1.0")
        workflow_id = imported_workflow_id(publisher_id, package_id, version)
        trust_level = _normalized_package_trust_level(package_json)
        display_name = _normalized_display_name(package_json, fallback=package_id)
        metadata_fields = _normalized_metadata_fields(package_json)

        models = _with_comfyui_workflow_model_source_urls(
            _normalize_models(capsule_json),
            (
                self._read_json("comfyui_workflow.json")
                if "comfyui_workflow.json" in self.members
                else None
            ),
            graph,
        )
        custom_nodes = _custom_nodes_with_recoverable_missing_bundled_sources(
            _normalize_custom_nodes(capsule_json, package_json),
            self.members,
        )
        dashboard = _normalize_dashboard(dashboard_json)
        dashboard_inputs = [
            WorkflowInput.model_validate(i)
            for i in (dashboard_json.get("inputs") or [])
            if isinstance(i, dict)
        ]
        self._validate_package_asset_defaults(dashboard_inputs)
        dashboard_outputs = [
            WorkflowOutput.model_validate(o)
            for o in (dashboard_json.get("outputs") or [])
            if isinstance(o, dict)
        ]
        unresolved_inputs = filter_resolved_runtime_inputs(
            merge_unresolved_runtime_inputs(
                normalize_unresolved_runtime_inputs(
                    package_json.get("unresolved_runtime_inputs")
                ),
                _detect_unresolved_runtime_inputs(graph),
            ),
            dashboard_inputs,
        )
        graph, unresolved_inputs = repair_misclassified_multimodal_text_inputs(
            graph,
            unresolved_inputs,
        )
        observed_hardware = _observed_hardware(capsule_json, export_report)

        # Validate configured dashboards — an invalid one routes to needs_input_setup.
        dashboard_valid = True
        if dashboard.status == "configured":
            from app.workflows.validator import (
                WorkflowPackageValidator,
            )  # local to avoid circular import

            _validator = WorkflowPackageValidator()
            _candidate = WorkflowPackage(
                metadata=WorkflowMetadata(
                    id=workflow_id,
                    name=display_name,
                    display_name=display_name,
                    version=version,
                    description="",
                    author=publisher_id,
                ),
                display_name=display_name,
                identity=None,
                engine="comfyui",
                required_models=[],
                comfyui_graph=graph,
                inputs=dashboard_inputs,
                outputs=dashboard_outputs,
                dashboard=dashboard,
                custom_nodes=[],
                unresolved_runtime_inputs=[],
                smoke_tests=WorkflowSmokeTests(),
            )
            _result = _validator.validate_structure(_candidate)
            dashboard_valid = _result.valid

        import_status = _import_status(
            unresolved_inputs, dashboard, dashboard_valid=dashboard_valid
        )

        try:
            return WorkflowPackage(
                metadata=WorkflowMetadata(
                    id=workflow_id,
                    name=display_name,
                    display_name=display_name,
                    version=version,
                    description=metadata_fields["description"],
                    author=metadata_fields["author"] or publisher_id,
                    website=metadata_fields["website"],
                    category=metadata_fields["category"],
                    tags=metadata_fields["tags"],
                    icon=metadata_fields["icon"],
                ),
                display_name=display_name,
                identity=WorkflowPackageIdentity(
                    publisher_id=publisher_id,
                    package_id=package_id,
                    version=version,
                    trust_level=trust_level,
                    source="noofy_archive_import",
                    signature=_optional_string_field(package_json, "signature"),
                    signatures=_normalize_signatures(package_json.get("signatures")),
                    signed_registry_metadata=_normalize_signed_registry_metadata(
                        package_json.get("signed_registry_metadata")
                    ),
                ),
                engine="comfyui",
                required_models=models,
                comfyui_graph=graph,
                inputs=dashboard_inputs,
                outputs=dashboard_outputs,
                dashboard=dashboard,
                custom_nodes=custom_nodes,
                unresolved_runtime_inputs=unresolved_inputs,
                assets=WorkflowAssetMetadata(
                    thumbnail=(
                        "source-files/assets/thumbnail.png"
                        if "assets/thumbnail.png" in self.members
                        else None
                    )
                ),
                export_report=export_report,
                exported_package=package_json,
                exported_capsule=capsule_json,
                comfyui_widget_metadata=normalize_comfyui_widget_metadata(
                    package_json.get("comfyui_widget_metadata"),
                    graph=graph,
                ),
                observed_hardware=observed_hardware,
                smoke_tests=WorkflowSmokeTests.model_validate(
                    package_json.get("smoke_tests") or {}
                ),
                import_metadata=WorkflowImportMetadata(
                    original_filename=self.original_filename,
                    imported_at=datetime.now(UTC).isoformat(),
                    source_archive_sha256=f"sha256:{hashlib.sha256(self.data).hexdigest()}",
                    status=import_status,
                    user_facing_message=_import_status_message(import_status),
                ),
            )
        except ValidationError as exc:
            raise NoofyImportError(
                "Workflow package metadata could not be normalized."
            ) from exc

    def trust_payload(self) -> dict[str, Any]:
        return imported_archive_trust_payload(
            package_json=self._read_json("package.json"),
            comfyui_graph=self._read_json("comfyui_graph.json"),
            dashboard_json=(
                self._read_json("dashboard.json")
                if "dashboard.json" in self.members
                else {}
            ),
            capsule_json=self._read_json("capsule.lock.json"),
            export_report=self._read_json("export-report.json"),
        )

    def extract_source_files(self, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        extracted_bytes = 0
        for name, info in self.members.items():
            if info.is_dir():
                continue
            try:
                target = contained_destination(target_dir, name)
            except PathSafetyError as exc:
                raise NoofyImportError(
                    "Workflow package contains an unsafe path."
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with (
                    self.archive.open(info, "r") as source,
                    target.open("xb") as dest,
                ):
                    copied_bytes = copy_stream_limited(
                        source,
                        dest,
                        max_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES - extracted_bytes,
                    )
            except StreamLimitError as exc:
                target.unlink(missing_ok=True)
                raise NoofyImportError(
                    "Workflow package expands to too much data."
                ) from exc
            extracted_bytes += copied_bytes
            if copied_bytes != info.file_size:
                target.unlink(missing_ok=True)
                raise NoofyImportError(
                    "Workflow package member size could not be verified."
                )

    def _validate_package_asset_defaults(self, inputs: list[WorkflowInput]) -> None:
        for workflow_input in inputs:
            if not is_package_asset_value(workflow_input.default):
                continue
            try:
                reference = validate_package_asset_reference(
                    workflow_input.default,
                    workflow_input=workflow_input,
                )
                member_name = package_asset_archive_path(reference["asset_id"])
            except PackageAssetError as exc:
                raise NoofyImportError(
                    f"Workflow input '{workflow_input.id}' has an invalid packaged default asset."
                ) from exc
            if member_name not in self.members:
                raise NoofyImportError(
                    f"Workflow input '{workflow_input.id}' references a packaged default asset that is missing from the archive."
                )
            info = self.members[member_name]
            if isinstance(reference.get("size_bytes"), int) and info.file_size != reference["size_bytes"]:
                raise NoofyImportError(
                    f"Workflow input '{workflow_input.id}' references a packaged default asset with mismatched size metadata."
                )
            sha256 = reference.get("sha256")
            if isinstance(sha256, str):
                digest = hashlib.sha256(self.archive.read(info)).hexdigest()
                if digest != sha256.removeprefix("sha256:"):
                    raise NoofyImportError(
                        f"Workflow input '{workflow_input.id}' references a packaged default asset with mismatched content metadata."
                    )

    def _read_json(self, name: str) -> dict[str, Any]:
        cached = self._json_cache.get(name)
        if cached is not None:
            return cached
        info = self.members.get(name)
        if info is None:
            raise NoofyImportError(f"Workflow package is missing {name}.")
        if info.file_size > MAX_JSON_BYTES:
            raise NoofyImportError(f"{name} is too large to import automatically.")
        try:
            payload = json.loads(self.archive.read(info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NoofyImportError(f"{name} is not valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise NoofyImportError(f"{name} must contain a JSON object.")
        self._json_cache[name] = payload
        return payload

    def _validated_members(self) -> dict[str, zipfile.ZipInfo]:
        try:
            return validate_archive_members(
                self.archive,
                required_files=REQUIRED_FILES,
                max_files=MAX_ARCHIVE_FILES,
                max_total_uncompressed_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES,
            )
        except ArchiveValidationError as exc:
            raise NoofyImportError(str(exc)) from exc


class RawComfyUIJsonImporter:
    """Normalizes a raw ComfyUI JSON workflow into an imported package."""

    def __init__(self, data: bytes, *, original_filename: str | None = None) -> None:
        if len(data) > MAX_JSON_BYTES:
            raise NoofyImportError("Workflow JSON is too large to import automatically.")
        self.data = data
        self.original_filename = original_filename
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NoofyImportError("Workflow JSON is not valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise NoofyImportError("Workflow JSON must contain a JSON object.")
        self.payload = payload
        self._package: WorkflowPackage | None = None
        self._source_files: dict[str, dict[str, Any]] | None = None

    def normalize(self) -> WorkflowPackage:
        if self._package is not None:
            return self._package
        graph = raw_comfyui_api_graph(self.payload)
        source_format = "api_graph" if graph is not None else None
        comfyui_workflow: dict[str, Any] | None = None
        synthesized_graph = False
        if graph is None and is_comfyui_ui_workflow(self.payload):
            comfyui_workflow = self.payload
            graph = comfyui_api_graph_from_ui_workflow(self.payload)
            source_format = "ui_workflow"
            synthesized_graph = True
        if graph is None or not graph:
            raise NoofyImportError(
                "Workflow JSON must be a ComfyUI API graph or a saved ComfyUI workflow."
            )

        package_id = _raw_comfyui_package_id(self.original_filename, self.payload)
        version = "0.1.0"
        publisher_id = "local"
        workflow_id = imported_workflow_id(publisher_id, package_id, version)
        display_name = _raw_comfyui_display_name(
            self.original_filename,
            self.payload,
            fallback=package_id,
        )
        required_models = required_models_from_comfyui_workflow(
            comfyui_workflow or {},
            comfyui_graph=graph,
        )
        api_node_types = sorted(_graph_node_types(graph))
        ui_node_types = (
            sorted(comfyui_workflow_node_types(comfyui_workflow))
            if comfyui_workflow is not None
            else []
        )
        executable_ui_node_types = (
            sorted(executable_comfyui_workflow_definition_node_types(comfyui_workflow))
            if comfyui_workflow is not None
            else []
        )
        all_node_types = sorted(set(api_node_types) | set(ui_node_types))
        dashboard = DashboardSchema(version=NOOFY_ARCHIVE_SCHEMA_VERSION, status="not_configured")
        unresolved_inputs = filter_resolved_runtime_inputs(
            _detect_unresolved_runtime_inputs(graph),
            [],
        )
        import_status = _import_status(unresolved_inputs, dashboard)
        package_json = {
            "schema_version": NOOFY_ARCHIVE_SCHEMA_VERSION,
            "publisher_id": publisher_id,
            "package_id": package_id,
            "version": version,
            "trust_level": TrustLevel.QUARANTINED_COMMUNITY.value,
            "metadata": {
                "display_name": display_name,
                "description": "",
                "author": "",
                "tags": ["ComfyUI JSON"],
            },
            "source_policy": "local",
        }
        capsule_json = {
            "schema_version": NOOFY_ARCHIVE_SCHEMA_VERSION,
            "models": [
                {
                    "comfyui_folder": model.folder,
                    "filename": model.filename,
                    "model_type": model.model_type,
                    "node_id": model.node_id,
                    "node_type": model.node_type,
                    "input_name": model.input_name,
                    "source_urls": list(model.source_urls),
                    "asset_ownership": model.asset_ownership.value,
                }
                for model in required_models
            ],
            "custom_nodes": [],
        }
        export_report = {
            "schema_version": NOOFY_ARCHIVE_SCHEMA_VERSION,
            "source": "raw_comfyui_json_import",
            "source_format": source_format,
            "synthesized_api_graph": synthesized_graph,
        }
        dashboard_json = dashboard.model_dump(mode="json", exclude_none=True)
        dashboard_json["inputs"] = []
        dashboard_json["outputs"] = []
        developer_details = {
            "raw_comfyui_json": {
                "source_format": source_format,
                "synthesized_api_graph": synthesized_graph,
                "required_model_count": len(unique_required_models(required_models)),
                "api_node_types": api_node_types,
                "ui_node_types": ui_node_types,
                "executable_ui_node_types": executable_ui_node_types,
                "node_types": all_node_types,
            }
        }
        self._source_files = {
            "package.json": package_json,
            "comfyui_graph.json": graph,
            "dashboard.json": dashboard_json,
            "capsule.lock.json": capsule_json,
            "export-report.json": export_report,
        }
        if comfyui_workflow is not None:
            self._source_files["comfyui_workflow.json"] = comfyui_workflow
        try:
            self._package = WorkflowPackage(
                metadata=WorkflowMetadata(
                    id=workflow_id,
                    name=display_name,
                    display_name=display_name,
                    version=version,
                    description="",
                    author=publisher_id,
                    tags=["ComfyUI JSON"],
                ),
                display_name=display_name,
                identity=WorkflowPackageIdentity(
                    publisher_id=publisher_id,
                    package_id=package_id,
                    version=version,
                    trust_level=TrustLevel.QUARANTINED_COMMUNITY.value,
                    source="raw_comfyui_json_import",
                ),
                engine="comfyui",
                required_models=required_models,
                comfyui_graph=graph,
                inputs=[],
                outputs=[],
                dashboard=dashboard,
                custom_nodes=[],
                unresolved_runtime_inputs=unresolved_inputs,
                export_report=export_report,
                exported_package=package_json,
                exported_capsule=capsule_json,
                observed_hardware={},
                smoke_tests=WorkflowSmokeTests(),
                import_metadata=WorkflowImportMetadata(
                    original_filename=self.original_filename,
                    imported_at=datetime.now(UTC).isoformat(),
                    source_archive_sha256=f"sha256:{hashlib.sha256(self.data).hexdigest()}",
                    status=import_status,
                    user_facing_message=_import_status_message(import_status),
                    developer_details=developer_details,
                ),
            )
        except ValidationError as exc:
            raise NoofyImportError("Workflow JSON could not be normalized.") from exc
        return self._package

    def trust_payload(self) -> dict[str, Any]:
        self.normalize()
        assert self._source_files is not None
        return imported_archive_trust_payload(
            package_json=self._source_files["package.json"],
            comfyui_graph=self._source_files["comfyui_graph.json"],
            dashboard_json=self._source_files["dashboard.json"],
            capsule_json=self._source_files["capsule.lock.json"],
            export_report=self._source_files["export-report.json"],
        )

    def extract_source_files(self, target_dir: Path) -> None:
        self.normalize()
        assert self._source_files is not None
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in self._source_files.items():
            (target_dir / name).write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        (target_dir / "raw-comfyui-workflow.json").write_bytes(self.data)


def workflow_importer_for_data(
    data: bytes,
    *,
    original_filename: str | None = None,
) -> WorkflowDataImporter:
    try:
        return NoofyArchiveImporter(data, original_filename=original_filename)
    except NoofyImportError as archive_error:
        try:
            return RawComfyUIJsonImporter(data, original_filename=original_filename)
        except NoofyImportError as json_error:
            if _workflow_import_extension(original_filename) == ".json":
                raise json_error from archive_error
            raise archive_error from json_error


def _workflow_import_extension(original_filename: str | None) -> str:
    if not original_filename:
        return ""
    return Path(original_filename).suffix.casefold()


def _raw_comfyui_package_id(
    original_filename: str | None,
    payload: dict[str, Any],
) -> str:
    for value in (
        payload.get("name"),
        payload.get("title"),
        Path(original_filename).stem if original_filename else None,
    ):
        if isinstance(value, str) and value.strip():
            return safe_store_segment(value.strip())
    return "comfyui-workflow"


def _raw_comfyui_display_name(
    original_filename: str | None,
    payload: dict[str, Any],
    *,
    fallback: str,
) -> str:
    for value in (
        payload.get("name"),
        payload.get("title"),
        Path(original_filename).stem if original_filename else None,
    ):
        if isinstance(value, str) and value.strip():
            cleaned = value.strip().replace("_", " ").replace("-", " ")
            return " ".join(cleaned.split())
    return fallback


def _raw_comfyui_log_details(package: WorkflowPackage) -> dict[str, object]:
    details = _raw_comfyui_details(package)
    if not isinstance(details, dict):
        return {}
    return {"raw_comfyui_json": details}


def _raw_comfyui_details(package: WorkflowPackage) -> dict[str, Any] | None:
    details = (
        package.import_metadata.developer_details.get("raw_comfyui_json")
        if package.import_metadata is not None
        else None
    )
    return details if isinstance(details, dict) else None


def _source_resolution_details(package: WorkflowPackage) -> dict[str, Any] | None:
    details = (
        package.import_metadata.developer_details.get("source_resolution")
        if package.import_metadata is not None
        else None
    )
    return details if isinstance(details, dict) else None


def _package_needs_automatic_custom_node_resolution(package: WorkflowPackage) -> bool:
    source_resolution = _source_resolution_details(package)
    if source_resolution is not None and source_resolution.get("status") in {
        "failed",
        "missing_custom_nodes",
    }:
        return True
    return bool(_non_bundled_required_custom_node_records(package))


def _source_resolution_node_types(source_resolution: dict[str, Any] | None) -> list[str]:
    if not isinstance(source_resolution, dict):
        return []
    node_types: set[str] = {
        item
        for item in source_resolution.get("unresolved_node_types", [])
        if isinstance(item, str)
    }
    for item in source_resolution.get("ambiguous_node_types", []):
        if isinstance(item, dict) and isinstance(item.get("node_type"), str):
            node_types.add(item["node_type"])
    missing = source_resolution.get("missing_custom_node")
    if isinstance(missing, dict):
        node_types.update(
            item for item in missing.get("node_types", []) if isinstance(item, str)
        )
    return sorted(node_types)


def _github_search_target_for_package(
    package: WorkflowPackage,
) -> GitHubCustomNodeSearchTarget | None:
    source_resolution = _source_resolution_details(package)
    node_types = set(_source_resolution_node_types(source_resolution))
    names: set[str] = set()
    node_titles: set[str] = set()
    missing_package_id: str | None = None
    raw_details = _raw_comfyui_details(package)
    if isinstance(source_resolution, dict):
        missing_package_id_value = source_resolution.get("package_id")
        if (
            isinstance(missing_package_id_value, str)
            and missing_package_id_value
            and not _is_generic_custom_node_search_name(missing_package_id_value)
        ):
            missing_package_id = missing_package_id_value
            names.add(missing_package_id_value)
        missing = source_resolution.get("missing_custom_node")
        if isinstance(missing, dict):
            for key in ("package_id", "name"):
                value = missing.get(key)
                if (
                    isinstance(value, str)
                    and value
                    and not _is_generic_custom_node_search_name(value)
                ):
                    names.add(value)
                    missing_package_id = missing_package_id or value

    for record in _non_bundled_required_custom_node_records(package):
        names.update(
            item
            for item in (record.id, record.folder_name)
            if item and not _is_generic_custom_node_search_name(item)
        )
        node_types.update(record.node_types)
        missing_package_id = missing_package_id or record.id

    node_titles.update(_raw_comfyui_titles_for_node_types(package, node_types))
    family_terms = set(_github_family_terms(node_types, node_titles))

    if raw_details is None:
        if package.identity is not None and not _is_generic_custom_node_search_name(
            package.identity.package_id
        ):
            names.add(package.identity.package_id)
        if not _is_generic_custom_node_search_name(package.metadata.name):
            names.add(package.metadata.name)
        if (
            package.metadata.display_name
            and not _is_generic_custom_node_search_name(package.metadata.display_name)
        ):
            names.add(package.metadata.display_name)

    if missing_package_id is None and family_terms:
        missing_package_id = _preferred_github_family_package_id(family_terms)
    names = {name for name in names if name.strip()}
    node_types = {item for item in node_types if item.strip()}
    node_titles = {item for item in node_titles if item.strip()}
    family_terms = {item for item in family_terms if item.strip()}
    if not names and not node_types and not node_titles and not family_terms:
        return None
    return GitHubCustomNodeSearchTarget(
        package_id=missing_package_id,
        names=tuple(sorted(names)),
        node_types=tuple(sorted(node_types)),
        node_titles=tuple(sorted(node_titles)),
        family_terms=tuple(sorted(family_terms)),
    )


GENERIC_CUSTOM_NODE_SEARCH_NAMES = {
    "api graph",
    "comfyui graph",
    "comfyui workflow",
    "comfyui json",
    "graph",
    "unknown",
    "workflow",
}


GENERIC_NODE_FAMILY_WORDS = {
    "api",
    "custom",
    "generate",
    "generator",
    "graph",
    "loader",
    "model",
    "node",
    "nodes",
    "preview",
    "workflow",
}


def _is_generic_custom_node_search_name(value: str) -> bool:
    normalized = _normalized_search_phrase(value)
    if not normalized:
        return True
    if normalized in GENERIC_CUSTOM_NODE_SEARCH_NAMES:
        return True
    compact = normalized.replace(" ", "")
    return compact in {
        item.replace(" ", "") for item in GENERIC_CUSTOM_NODE_SEARCH_NAMES
    }


def _raw_comfyui_titles_for_node_types(
    package: WorkflowPackage,
    node_types: set[str],
) -> list[str]:
    if not node_types:
        return []
    titles: list[str] = []
    for node in package.comfyui_graph.values():
        if not isinstance(node, dict) or node.get("class_type") not in node_types:
            continue
        meta = node.get("_meta")
        title = meta.get("title") if isinstance(meta, dict) else None
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
    return titles


def _github_family_terms(
    node_types: set[str],
    node_titles: set[str],
) -> list[str]:
    terms: set[str] = set()
    for value in sorted(node_titles) + sorted(node_types):
        words = _node_family_words(value)
        if len(words) >= 2:
            terms.update(_github_family_term_variants(" ".join(words)))
        elif words:
            terms.update(_github_family_term_variants(words[0]))
    return sorted(terms)


def _node_family_words(value: str) -> list[str]:
    words = _github_search_words(value)
    while words and words[-1] in GENERIC_NODE_FAMILY_WORDS:
        words.pop()
    if words and words[0] == "comfyui":
        words = words[1:]
    meaningful = [word for word in words if word not in GENERIC_NODE_FAMILY_WORDS]
    return meaningful or words


def _github_search_words(value: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [
        item.casefold()
        for item in re.sub(r"[^A-Za-z0-9]+", " ", spaced).split()
        if item
    ]


def _github_family_term_variants(value: str) -> set[str]:
    phrase = _normalized_search_phrase(value)
    if not phrase:
        return set()
    dash = phrase.replace(" ", "-")
    compact = phrase.replace(" ", "")
    return {
        phrase,
        dash,
        phrase.replace(" ", "_"),
        compact,
        f"comfyui {phrase}",
        f"comfyui-{dash}",
    }


def _preferred_github_family_package_id(family_terms: set[str]) -> str:
    normalized_terms: set[str] = set()
    for term in family_terms:
        phrase = _normalized_search_phrase(term)
        if phrase.startswith("comfyui "):
            phrase = phrase.removeprefix("comfyui ").strip()
        if phrase:
            normalized_terms.add(phrase)
    preferred = sorted(
        normalized_terms,
        key=lambda item: (len(item.split()) < 2, len(item), item),
    )[0]
    return safe_store_segment(f"comfyui-{preferred}")


def _normalized_search_phrase(value: str) -> str:
    return " ".join(_github_search_words(value))


def _missing_custom_node_resolution(
    package: WorkflowPackage,
    *,
    reason: str,
    unresolved_node_types: list[str],
    missing_package_id: str | None = None,
    candidate: dict[str, object] | None = None,
    automatic_resolution_failures: list[str] | None = None,
    failed_custom_nodes: list[dict[str, object]] | None = None,
) -> WorkflowPackage:
    mode = (
        "candidate_approval"
        if isinstance(candidate, dict) and candidate.get("confidence") == "medium"
        else "manual_url"
    )
    message = (
        "Noofy found a possible workflow extension."
        if mode == "candidate_approval"
        else "Noofy could not automatically find this workflow extension."
    )
    source_resolution: dict[str, object] = {
        "status": "failed",
        "mode": mode,
        "reason": reason,
        "unresolved_node_types": sorted(set(unresolved_node_types)),
        "ambiguous_node_types": [],
        "resolved_custom_nodes": [],
        "failed_custom_nodes": failed_custom_nodes or [],
        "automatic_resolution_failures": automatic_resolution_failures or [],
    }
    if missing_package_id:
        source_resolution["package_id"] = missing_package_id
        source_resolution["missing_custom_node"] = {
            "package_id": missing_package_id,
            "node_types": sorted(set(unresolved_node_types)),
        }
    if candidate is not None:
        source_resolution["candidate"] = candidate
    if _raw_comfyui_details(package) is not None:
        package = _package_with_raw_custom_node_detection(
            package,
            {
                "status": "missing_custom_nodes",
                **source_resolution,
            },
        )
    return _package_with_import_resolution_status(
        package,
        status="missing_custom_nodes",
        message=message,
        source_resolution=source_resolution,
    )


def _package_id_for_github_source(
    source: NodeRegistrySource,
    *,
    fallback: object,
) -> str:
    repo_url = source.source_repo_url or source.source_url
    try:
        owner, repo, _ = _parse_github_repo_url(repo_url)
        return safe_store_segment(f"github-{owner}-{repo}")
    except GitHubCustomNodeUrlResolutionError:
        if isinstance(fallback, str) and fallback:
            return safe_store_segment(f"github-{fallback}")
        return safe_store_segment("github-custom-node")


def _remove_superseded_custom_node_records(
    records_by_id: dict[str, WorkflowCustomNodeRecord],
    *,
    package_id: str,
    node_types: list[str],
) -> None:
    covered_node_types = set(node_types)
    if not covered_node_types:
        return
    for record_id, record in list(records_by_id.items()):
        if record_id == package_id:
            continue
        if not record.node_types or covered_node_types.isdisjoint(record.node_types):
            continue
        has_unusable_source = (
            record.resolution_method == "missing_bundled_source"
            or (
                not record.included
                and (
                    not record.source.startswith("https://")
                    or record.source_content_hash is None
                )
            )
        )
        if has_unusable_source:
            records_by_id.pop(record_id, None)


def _source_policy_allows_github_custom_node_resolution(
    source_policy: SourcePolicy | None,
) -> bool:
    if source_policy is None:
        return True
    return (
        source_policy.automatic_preparation_allowed
        and source_policy.community_preparation_opted_in
        and "explicit-metadata" in set(source_policy.allowed_source_origins)
    )


def _custom_nodes_with_recoverable_missing_bundled_sources(
    custom_nodes: list[WorkflowCustomNodeRecord],
    members: dict[str, zipfile.ZipInfo],
) -> list[WorkflowCustomNodeRecord]:
    updated: list[WorkflowCustomNodeRecord] = []
    for node in custom_nodes:
        if (
            node.included
            and _is_bundled_custom_node_source(node.source)
            and not _bundled_custom_node_member_exists(members, node)
        ):
            updated.append(
                node.model_copy(
                    update={
                        "included": False,
                        "resolution_method": "missing_bundled_source",
                    }
                )
            )
            continue
        updated.append(node)
    return updated


def _is_bundled_custom_node_source(source: str) -> bool:
    return (
        source == "bundled_archive"
        or source == "bundled_from_creator_machine"
        or source.startswith("bundled_archive:")
        or source.startswith("source-files/custom_nodes/")
    )


def _bundled_custom_node_member_exists(
    members: dict[str, zipfile.ZipInfo],
    node: WorkflowCustomNodeRecord,
) -> bool:
    explicit_folder = _bundled_custom_node_source_folder(node.source)
    for folder_name in (explicit_folder, node.folder_name, node.id):
        if folder_name and _archive_custom_node_folder_exists(members, folder_name):
            return True
    wanted = _normalized_custom_node_folder_name(node.folder_name or node.id)
    for name in members:
        parts = Path(name).parts
        if len(parts) >= 2 and parts[0] == "custom_nodes":
            if _normalized_custom_node_folder_name(parts[1]) == wanted:
                return True
    return False


def _archive_custom_node_folder_exists(
    members: dict[str, zipfile.ZipInfo],
    folder_name: str,
) -> bool:
    prefixes = (
        f"custom_nodes/{folder_name}/",
        f"source-files/custom_nodes/{folder_name}/",
    )
    return any(name.startswith(prefixes) for name in members)


def _bundled_custom_node_source_folder(source: str) -> str | None:
    if source.startswith("bundled_archive:"):
        value = source.split(":", 1)[1]
    elif source.startswith("source-files/custom_nodes/"):
        value = source.removeprefix("source-files/custom_nodes/").strip("/")
    else:
        return None
    if "/" in value or "\\" in value or value in {"", ".", ".."}:
        return None
    return value


def _normalized_custom_node_folder_name(value: str) -> str:
    return value.replace("_", "-").casefold()


def _package_with_raw_comfyui_details(
    package: WorkflowPackage,
    raw_details: dict[str, object],
) -> WorkflowPackage:
    if package.import_metadata is None:
        return package
    developer_details = dict(package.import_metadata.developer_details)
    developer_details["raw_comfyui_json"] = raw_details
    return package.model_copy(
        update={
            "import_metadata": package.import_metadata.model_copy(
                update={"developer_details": developer_details}
            )
        }
    )


def _package_with_raw_custom_node_detection(
    package: WorkflowPackage,
    detection: dict[str, object],
) -> WorkflowPackage:
    raw_details = dict(_raw_comfyui_details(package) or {})
    raw_details["custom_node_detection"] = detection
    return _package_with_raw_comfyui_details(package, raw_details)


def _raw_comfyui_required_node_types(package: WorkflowPackage) -> set[str]:
    raw_details = _raw_comfyui_details(package) or {}
    detected: set[str] = set(_graph_node_types(package.comfyui_graph))
    for key in ("executable_ui_node_types",):
        value = raw_details.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and _is_resolvable_workflow_node_type(item):
                detected.add(item)
    return detected


def _missing_raw_custom_nodes(
    package: WorkflowPackage,
    *,
    reason: str,
    unresolved_node_types: list[str],
    ambiguous_node_types: list[dict[str, object]] | None = None,
    resolved_custom_nodes: list[dict[str, object]] | None = None,
    failed_custom_nodes: list[dict[str, object]] | None = None,
) -> WorkflowPackage:
    source_resolution = {
        "status": "failed",
        "reason": reason,
        "unresolved_node_types": sorted(unresolved_node_types),
        "ambiguous_node_types": ambiguous_node_types or [],
        "resolved_custom_nodes": resolved_custom_nodes or [],
        "failed_custom_nodes": failed_custom_nodes or [],
    }
    package = _package_with_raw_custom_node_detection(
        package,
        {
            "status": "missing_custom_nodes",
            **source_resolution,
        },
    )
    return _package_with_import_resolution_status(
        package,
        status="missing_custom_nodes",
        message="Noofy could not find the required custom nodes for this workflow.",
        source_resolution=source_resolution,
    )


def _unsupported_raw_custom_nodes(
    package: WorkflowPackage,
    *,
    reason: str,
    unresolved_node_types: list[str],
    ambiguous_node_types: list[dict[str, object]] | None = None,
    resolved_custom_nodes: list[dict[str, object]] | None = None,
) -> WorkflowPackage:
    return _missing_raw_custom_nodes(
        package,
        reason=reason,
        unresolved_node_types=unresolved_node_types,
        ambiguous_node_types=ambiguous_node_types,
        resolved_custom_nodes=resolved_custom_nodes,
    )


def _import_status(
    unresolved_inputs: list[UnresolvedRuntimeInput],
    dashboard: DashboardSchema | None = None,
    dashboard_valid: bool = True,
) -> str:
    return import_status(unresolved_inputs, dashboard, dashboard_valid)


def _import_status_message(status: str) -> str:
    return import_status_message(status)


def _package_with_import_resolution_status(
    package: WorkflowPackage,
    *,
    status: str,
    message: str,
    source_resolution: dict[str, object],
) -> WorkflowPackage:
    return package_with_import_resolution_status(
        package,
        status=status,
        message=message,
        source_resolution=source_resolution,
    )


def _parse_github_repo_url(url: str) -> tuple[str, str, str | None]:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "https" or parsed.netloc.casefold() != "github.com":
        raise GitHubCustomNodeUrlResolutionError(
            "Paste a GitHub repository URL that starts with https://github.com/."
        )
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        raise GitHubCustomNodeUrlResolutionError(
            "Paste the GitHub repository URL for this custom node."
        )
    owner, repo = parts[0], parts[1]
    if owner in {".", ".."} or repo in {".", ".."}:
        raise GitHubCustomNodeUrlResolutionError("GitHub repository URL is invalid.")
    ref: str | None = None
    if len(parts) >= 4 and parts[2] in {"tree", "commit"}:
        ref = "/".join(parts[3:])
    return owner, repo.removesuffix(".git"), ref


def _github_custom_node_search_queries(
    target: GitHubCustomNodeSearchTarget,
) -> list[str]:
    terms: list[str] = []
    for name in [
        target.package_id,
        *target.names,
        *target.family_terms,
        *target.node_types,
        *target.node_titles,
    ]:
        if not name:
            continue
        if _is_generic_custom_node_search_name(name):
            continue
        terms.extend(_github_search_term_variants(name))
    seen: set[str] = set()
    queries: list[str] = []
    for term in terms:
        normalized = term.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        quoted = urllib.parse.quote(normalized)
        queries.append(f"{quoted}+in:name")
    return queries[:10]


def _github_search_term_variants(value: str) -> list[str]:
    cleaned = value.strip()
    if not cleaned:
        return []
    normalized = cleaned.replace("_", "-").replace(" ", "-")
    words = [part for part in normalized.split("-") if part]
    variants = {cleaned, cleaned.casefold(), normalized, normalized.casefold()}
    if words:
        variants.add("-".join(words))
        variants.add("_".join(words))
        variants.add(" ".join(words))
        variants.add("".join(words))
    return [item for item in variants if item.strip()]


def _github_search_repositories(query: str) -> list[dict[str, Any]]:
    url = f"https://api.github.com/search/repositories?q={query}&per_page=5"
    try:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Noofy",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GitHubCustomNodeUrlResolutionError(
            "Noofy could not search GitHub for this workflow extension."
        ) from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _inspect_github_custom_node_candidate(
    payload: dict[str, Any],
    target: GitHubCustomNodeSearchTarget,
) -> GitHubCustomNodeCandidate:
    owner_payload = payload.get("owner")
    owner = owner_payload.get("login") if isinstance(owner_payload, dict) else None
    repo = payload.get("name")
    html_url = payload.get("html_url")
    if not all(isinstance(item, str) and item for item in (owner, repo, html_url)):
        raise GitHubCustomNodeUrlResolutionError("GitHub repository result is invalid.")
    default_branch = payload.get("default_branch")
    commit_sha = _github_commit_sha(
        owner,
        repo,
        default_branch if isinstance(default_branch, str) and default_branch else None,
    )
    archive_url = f"https://codeload.github.com/{owner}/{repo}/zip/{commit_sha}"
    archive_bytes = _download_url_bytes(archive_url)
    archive_hash = hashlib.sha256(archive_bytes).hexdigest()
    evidence, evidence_score, specific_match_score = _github_custom_node_archive_evidence(
        archive_bytes,
        target=target,
        repo=repo,
    )
    name_match = _github_candidate_name_match(repo, target)
    confidence = _github_candidate_confidence(
        name_match=name_match,
        evidence_score=evidence_score,
        specific_match_score=specific_match_score,
    )
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url=archive_url,
        source_ref=commit_sha,
        source_content_hash=f"sha256:{archive_hash}",
        archive_subdir=f"{repo}-{commit_sha}",
        source_repo_url=html_url,
    )
    candidate_id = _github_candidate_id(owner, repo, commit_sha, target.node_types)
    description = payload.get("description")
    stars = payload.get("stargazers_count")
    updated_at = payload.get("updated_at")
    return GitHubCustomNodeCandidate(
        candidate_id=candidate_id,
        owner=owner,
        repo=repo,
        html_url=html_url,
        description=description if isinstance(description, str) else None,
        stargazers_count=stars if isinstance(stars, int) else 0,
        updated_at=updated_at if isinstance(updated_at, str) else None,
        source=source,
        evidence=tuple(evidence),
        evidence_score=evidence_score,
        name_match=name_match,
        confidence=confidence,
        specific_match_score=specific_match_score,
    )


def _github_custom_node_archive_evidence(
    archive_bytes: bytes,
    *,
    target: GitHubCustomNodeSearchTarget,
    repo: str,
) -> tuple[list[str], int, int]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as exc:
        raise GitHubCustomNodeUrlResolutionError(
            "GitHub repository archive could not be inspected."
        ) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise GitHubCustomNodeUrlResolutionError(
                "GitHub repository archive contains too many files."
            )
        total_size = 0
        python_files: list[str] = []
        readme_files: list[str] = []
        install_files: list[str] = []
        text_samples: list[str] = []
        for info in infos:
            if info.is_dir() or _ignored_archive_member(info.filename):
                continue
            if zip_member_is_symlink(info):
                raise GitHubCustomNodeUrlResolutionError(
                    "GitHub repository archive contains an unsupported symlink."
                )
            total_size += info.file_size
            if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise GitHubCustomNodeUrlResolutionError(
                    "GitHub repository archive expands to too much data."
                )
            name = _safe_archive_name(info.filename)
            relative_name = _strip_github_archive_root(name)
            lower_name = relative_name.casefold()
            if lower_name.endswith(".py"):
                python_files.append(relative_name)
            if Path(lower_name).name in {
                "requirements.txt",
                "pyproject.toml",
                "install.py",
                "setup.py",
            }:
                install_files.append(relative_name)
            if Path(lower_name).name.startswith("readme"):
                readme_files.append(relative_name)
            if (
                lower_name.endswith(".py")
                or Path(lower_name).name.startswith("readme")
            ) and info.file_size <= 512 * 1024:
                text = _read_zip_text_sample(archive, info)
                if text:
                    text_samples.append(text)

    combined_text = "\n".join(text_samples)
    combined_lower = combined_text.casefold()
    evidence: list[str] = []
    score = 0
    specific_score = 0
    if "NODE_CLASS_MAPPINGS" in combined_text:
        evidence.append("NODE_CLASS_MAPPINGS found in Python source")
        score += 5
    matching_node_types = [
        node_type
        for node_type in target.node_types
        if node_type in combined_text or node_type.casefold() in combined_lower
    ]
    if matching_node_types:
        evidence.append("Required node type appears in source")
        score += 4
        specific_score += 5
    matching_family_terms = _github_matching_family_terms(combined_text, target)
    if matching_family_terms:
        evidence.append("Required node family appears in source or README")
        score += 4
        specific_score += 4
    repo_family_match = _github_repo_family_match_strength(repo, target)
    if repo_family_match:
        evidence.append("Repository name matches required node family")
        score += 3
        specific_score += 5 if repo_family_match == "exact" else 4
    if "comfyui" in combined_lower or "custom node" in combined_lower:
        evidence.append("README or source mentions ComfyUI custom nodes")
        score += 2
    if python_files:
        evidence.append("Repository contains Python custom-node files")
        score += 1
    if install_files:
        evidence.append("Repository includes install or dependency metadata")
        score += 1
    if readme_files:
        evidence.append("Repository includes README documentation")
    return evidence, score, specific_score


def _read_zip_text_sample(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    try:
        with archive.open(info, "r") as source:
            data = source.read(min(info.file_size, 128 * 1024))
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _strip_github_archive_root(name: str) -> str:
    parts = Path(name).parts
    if len(parts) <= 1:
        return name
    return str(Path(*parts[1:]))


def _github_candidate_name_match(
    repo: str,
    target: GitHubCustomNodeSearchTarget,
) -> str:
    target_keys = {
        _github_name_key(name)
        for name in [target.package_id, *target.names, *target.family_terms]
        if name
    }
    repo_key = _github_name_key(repo)
    if repo_key in target_keys:
        return "exact"
    if any(repo_key.endswith(key) or key.endswith(repo_key) for key in target_keys):
        return "strong"
    return "weak"


def _github_matching_family_terms(
    text: str,
    target: GitHubCustomNodeSearchTarget,
) -> list[str]:
    text_lower = text.casefold()
    text_key = _github_name_key(text)
    matches: list[str] = []
    for term in target.family_terms:
        phrase = _normalized_search_phrase(term)
        if not phrase:
            continue
        phrase_key = _github_name_key(phrase)
        if phrase in text_lower or phrase_key in text_key:
            matches.append(term)
    return sorted(set(matches))


def _github_repo_family_match_strength(
    repo: str,
    target: GitHubCustomNodeSearchTarget,
) -> str | None:
    repo_key = _github_name_key(repo)
    for term in target.family_terms:
        term_key = _github_name_key(term)
        if not term_key:
            continue
        if repo_key == term_key or repo_key == f"comfyui{term_key}":
            return "exact"
        if repo_key.endswith(term_key) or term_key.endswith(repo_key):
            return "strong"
    return None


def _github_candidate_confidence(
    *,
    name_match: str,
    evidence_score: int,
    specific_match_score: int,
) -> str:
    if specific_match_score <= 0:
        return "low"
    if (
        name_match in {"exact", "strong"}
        and evidence_score >= 6
        and specific_match_score >= 4
    ):
        return "high"
    if evidence_score >= 4 or (
        name_match in {"exact", "strong"} and evidence_score >= 3
    ):
        return "medium"
    return "low"


def _github_candidate_sort_key(
    candidate: GitHubCustomNodeCandidate,
) -> tuple[int, int, int, int, int]:
    confidence_rank = {"high": 3, "medium": 2, "low": 1}.get(candidate.confidence, 0)
    name_rank = {"exact": 3, "strong": 2, "weak": 1}.get(candidate.name_match, 0)
    return (
        candidate.specific_match_score,
        name_rank,
        candidate.evidence_score,
        confidence_rank,
        candidate.stargazers_count,
    )


def _github_name_key(value: str) -> str:
    return "".join(char for char in value.casefold() if char.isalnum())


def _github_candidate_id(
    owner: str,
    repo: str,
    commit_sha: str,
    node_types: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {
            "owner": owner,
            "repo": repo,
            "commit_sha": commit_sha,
            "node_types": sorted(node_types),
        },
        sort_keys=True,
    )
    return "github-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _github_candidate_payload(
    candidate: GitHubCustomNodeCandidate,
    *,
    include_source: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_id": candidate.candidate_id,
        "owner": candidate.owner,
        "repo": candidate.repo,
        "name": candidate.repo,
        "repo_url": candidate.html_url,
        "description": candidate.description,
        "stars": candidate.stargazers_count,
        "updated_at": candidate.updated_at,
        "evidence": list(candidate.evidence),
        "evidence_score": candidate.evidence_score,
        "specific_match_score": candidate.specific_match_score,
        "name_match": candidate.name_match,
        "confidence": candidate.confidence,
    }
    if include_source:
        payload["source"] = candidate.source.model_dump(mode="json", exclude_none=True)
    return payload


def _github_commit_sha(owner: str, repo: str, ref: str | None) -> str:
    if ref and _is_full_git_sha(ref):
        return ref
    api_url = (
        f"https://api.github.com/repos/{owner}/{repo}/commits/{urllib.parse.quote(ref, safe='')}"
        if ref
        else f"https://api.github.com/repos/{owner}/{repo}/commits/HEAD"
    )
    try:
        request = urllib.request.Request(
            api_url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Noofy",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GitHubCustomNodeUrlResolutionError(
            "Noofy could not resolve that GitHub repository to a pinned commit."
        ) from exc
    sha = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not _is_full_git_sha(sha):
        raise GitHubCustomNodeUrlResolutionError(
            "Noofy could not resolve that GitHub repository to a pinned commit."
        )
    return sha


def _download_url_bytes(
    url: str,
    *,
    max_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES,
) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise GitHubCustomNodeUrlResolutionError(
            "Noofy could not download and verify the GitHub custom-node archive."
        ) from exc
    if len(data) > max_bytes:
        raise GitHubCustomNodeUrlResolutionError(
            "GitHub custom-node archive is too large to inspect automatically."
        )
    return data


def _sha256_url(url: str) -> str:
    digest = hashlib.sha256()
    data = _download_url_bytes(url)
    digest.update(data)
    return digest.hexdigest()


def _is_full_git_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdefABCDEF" for char in value)


def _package_with_trust_verification(
    package: WorkflowPackage,
    verification: TrustVerificationResult,
) -> WorkflowPackage:
    return package_with_trust_verification(package, verification)


def _package_with_source_policy(
    package: WorkflowPackage,
    *,
    community_preparation_opted_in: bool,
    policy_status: str = "active",
) -> WorkflowPackage:
    return package_with_source_policy(
        package,
        community_preparation_opted_in=community_preparation_opted_in,
        policy_status=policy_status,
    )


def _source_policy_status_for_import(package: WorkflowPackage) -> str:
    return source_policy_status_for_import(package)


def _non_bundled_required_custom_node_records(
    package: WorkflowPackage,
) -> list[WorkflowCustomNodeRecord]:
    return non_bundled_required_custom_node_records(package)


def _graph_node_types(graph: dict[str, Any]) -> set[str]:
    return graph_node_types(graph)


def _is_resolvable_workflow_node_type(node_type: str) -> bool:
    return is_resolvable_workflow_node_type(node_type)


def _explicit_node_registry_source(
    record: WorkflowCustomNodeRecord,
) -> NodeRegistrySource | None:
    return explicit_node_registry_source(record)


def _safe_archive_name(name: str) -> str:
    try:
        return safe_archive_name(name)
    except ArchiveValidationError as exc:
        raise NoofyImportError(str(exc)) from exc


def _ignored_archive_member(name: str) -> bool:
    return ignored_archive_member(name)


def _single_wrapper_root(members: dict[str, zipfile.ZipInfo]) -> str | None:
    return single_wrapper_root(members, required_files=REQUIRED_FILES)


def _strip_wrapper_root(name: str, root_prefix: str | None) -> str | None:
    return strip_wrapper_root(name, root_prefix)


def _safe_store_segment(value: str) -> str:
    return safe_store_segment(value)


def _package_imported_as_copy(package: WorkflowPackage, root_dir: Path) -> WorkflowPackage:
    if package.identity is None:
        raise NoofyImportError("Cannot import a copy of a package without identity metadata.")

    original_identity = package.identity
    display_name = workflow_package_display_name(package)
    copy_index = 1
    while True:
        suffix = "-copy" if copy_index == 1 else f"-copy-{copy_index}"
        package_id = f"{original_identity.package_id}{suffix}"
        name_suffix = " Copy" if copy_index == 1 else f" Copy {copy_index}"
        identity = WorkflowPackageIdentity(
            publisher_id="local",
            package_id=package_id,
            version=original_identity.version,
            trust_level=TrustLevel.QUARANTINED_COMMUNITY.value,
            source="local_copy",
            signature=None,
            signatures=[],
            signed_registry_metadata=None,
        )
        if not package_identity_dir(root_dir, identity).exists():
            workflow_id = imported_workflow_id(
                identity.publisher_id,
                identity.package_id,
                identity.version,
            )
            import_metadata = package.import_metadata
            if import_metadata is not None:
                developer_details = dict(import_metadata.developer_details)
                developer_details.pop("trust_verification", None)
                developer_details["copied_from_identity"] = original_identity.model_dump(mode="json")
                import_metadata = import_metadata.model_copy(
                    update={"developer_details": developer_details}
                )
            return package.model_copy(
                update={
                    "identity": identity,
                    "display_name": f"{display_name}{name_suffix}",
                    "metadata": package.metadata.model_copy(
                        update={
                            "id": workflow_id,
                            "name": f"{display_name}{name_suffix}",
                            "display_name": f"{display_name}{name_suffix}",
                        }
                    ),
                    "import_metadata": import_metadata,
                    "source_policy": None,
                }
            )
        copy_index += 1


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return zip_member_is_symlink(info)


def _string_field(data: dict[str, Any], key: str, *, fallback: str) -> str:
    return string_field(data, key, fallback=fallback)


def _optional_string_field(data: dict[str, Any], key: str) -> str | None:
    return optional_string_field(data, key)


def _normalized_display_name(data: dict[str, Any], *, fallback: str) -> str:
    return normalized_display_name(data, fallback=fallback)


def _normalized_metadata_fields(data: dict[str, Any]) -> dict[str, Any]:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    def text(key: str) -> str:
        nested = metadata.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return ""

    tags_value = metadata.get("tags")
    if not isinstance(tags_value, list):
        tags_value = data.get("tags")
    tags = []
    seen = set()
    if isinstance(tags_value, list):
        for item in tags_value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if not cleaned or cleaned.casefold() in seen:
                continue
            seen.add(cleaned.casefold())
            tags.append(cleaned)

    return {
        "description": text("description"),
        "author": text("author"),
        "website": text("website"),
        "category": text("category"),
        "tags": tags,
        "icon": text("icon"),
    }


def _normalize_trust_level(value: Any) -> str:
    return normalize_trust_level(value)


def _normalized_package_trust_level(package_json: dict[str, Any]) -> str:
    trust_level = _normalize_trust_level(package_json.get("trust_level"))
    source_policy = package_json.get("source_policy")
    if trust_level == "unsupported" and source_policy == "local":
        return "quarantined_community"
    return trust_level


def _normalize_signatures(value: Any) -> list[WorkflowPackageSignature]:
    return normalize_signatures(value)


def _normalize_signed_registry_metadata(value: Any) -> SignedRegistryMetadata | None:
    return normalize_signed_registry_metadata(value)


def _normalize_models(capsule_json: dict[str, Any]) -> list[RequiredModel]:
    return normalize_models(capsule_json)


def _with_comfyui_workflow_model_source_urls(
    models: list[RequiredModel],
    comfyui_workflow: dict[str, Any] | None,
    comfyui_graph: dict[str, Any] | None = None,
) -> list[RequiredModel]:
    if not isinstance(comfyui_workflow, dict) and not isinstance(comfyui_graph, dict):
        return models
    workflow_models = required_models_from_comfyui_workflow(
        comfyui_workflow or {},
        comfyui_graph=comfyui_graph,
    )
    if not workflow_models:
        return models
    existing_by_target = {(model.folder, model.filename): model for model in models}
    merged = list(models)
    for workflow_model in workflow_models:
        target = (workflow_model.folder, workflow_model.filename)
        model = existing_by_target.get(target)
        if model is None:
            existing_by_target[target] = workflow_model
            merged.append(workflow_model)
            continue
        if model.source_urls or model.source_url:
            continue
        if not workflow_model.source_urls:
            continue
        model.source_urls = list(workflow_model.source_urls)
        model.source_url = workflow_model.source_urls[0]
    return merged

def _normalize_model_verification_level(
    model: dict[str, Any],
    *,
    checksum: str | None,
    size_bytes: int | None,
) -> str:
    return normalize_model_verification_level(
        model,
        checksum=checksum,
        size_bytes=size_bytes,
    )


def _normalize_asset_ownership(value: Any) -> str:
    return normalize_asset_ownership(value)


def _normalize_custom_nodes(
    capsule_json: dict[str, Any],
    package_json: dict[str, Any] | None = None,
) -> list[WorkflowCustomNodeRecord]:
    return normalize_custom_nodes(capsule_json, package_json)


def _normalize_dashboard(
    dashboard_json: dict[str, Any],
) -> DashboardSchema:
    return normalize_dashboard(dashboard_json)


def merge_unresolved_runtime_inputs(
    exported_inputs: list[UnresolvedRuntimeInput],
    detected_inputs: list[UnresolvedRuntimeInput],
) -> list[UnresolvedRuntimeInput]:
    merged: list[UnresolvedRuntimeInput] = []
    seen: set[tuple[str, str]] = set()
    for runtime_input in exported_inputs:
        key = (runtime_input.node_id, runtime_input.input_name)
        if key in seen:
            continue
        seen.add(key)
        merged.append(runtime_input)
    for runtime_input in detected_inputs:
        key = (runtime_input.node_id, runtime_input.input_name)
        if key in seen:
            continue
        seen.add(key)
        merged.append(runtime_input)
    return merged


def _detect_unresolved_runtime_inputs(
    graph: dict[str, Any],
) -> list[UnresolvedRuntimeInput]:
    return detect_unresolved_runtime_inputs(graph)


def _observed_hardware(
    capsule_json: dict[str, Any], export_report: dict[str, Any]
) -> dict[str, Any]:
    return observed_hardware(capsule_json, export_report)


def imported_package_capsule_lock(package: WorkflowPackage) -> CapsuleLock:
    try:
        return build_imported_package_capsule_lock(package)
    except ImportCapsuleLockError as exc:
        raise NoofyImportError(str(exc)) from exc


def _unsupported_local_runtime_resolution(
    exc: RuntimeProfileSelectionError,
) -> dict[str, object] | None:
    os_name = current_os_name()
    architecture = current_architecture()
    if os_name != "darwin" or architecture != "x64":
        return None
    expected_message = f"No supported runtime profile variant for {os_name}/{architecture}."
    if str(exc) != expected_message:
        return None
    return {
        "selection_stage": "unavailable",
        "reason": "unsupported_local_runtime_platform",
        "os": os_name,
        "architecture": architecture,
    }


def _select_import_runtime_profile(
    profiles,
):
    """Select the local v1 runtime variant for imported package preparation.

    This is a Phase 5c bridge: imported app-owned capsule locks need concrete
    runtime facts before Phase 5d/5e can prepare artifacts. Later phases can
    replace this with an adapter-aware resolution pass before install.
    """
    try:
        return select_import_runtime_profile(profiles)
    except RuntimeProfileSelectionError as exc:
        raise NoofyImportError(str(exc)) from exc


def _reject_unsupported_exported_launch_options(data: dict[str, Any]) -> None:
    try:
        reject_unsupported_exported_launch_options(data)
    except ImportNormalizationError as exc:
        raise NoofyImportError(str(exc)) from exc


def _has_nonempty_launch_option(data: dict[str, Any], key: str) -> bool:
    return has_nonempty_launch_option(data, key)


def _current_os_name() -> str:
    return current_os_name()


def _current_architecture() -> str:
    return current_architecture()


def _preferred_gpu_backend(os_name: str, architecture: str) -> str:
    return preferred_gpu_backend(os_name, architecture)


def _has_nvidia_gpu() -> bool:
    return has_nvidia_gpu()


def _trust_level_from_string(value: str) -> TrustLevel:
    return trust_level_from_string(value)


def _model_locks_from_package(package: WorkflowPackage) -> list[ModelLock]:
    return model_locks_from_package(package)


def _model_id(model: RequiredModel) -> str:
    return model_id(model)


def _hardware_observations_from_package(
    package: WorkflowPackage,
) -> HardwareObservations:
    return hardware_observations_from_package(package)


def _launch_config_hash(
    engine: str, variant, enabled_custom_node_hash: str
) -> str:
    return launch_config_hash(engine, variant, enabled_custom_node_hash)
