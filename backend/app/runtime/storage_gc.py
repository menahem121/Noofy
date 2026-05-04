"""Derived runtime-store reference index and garbage collection.

The v1 storage cleaner derives reachability from install-state records,
workflow package/capsule records, and live runner descriptors each time it
runs. It intentionally does not persist reference-count files.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from app.artifacts import AssetOwnership
from app.core.paths import NoofyPaths
from app.engine.diagnostics import LogStore
from app.runtime.dependency_lock_store import ResolvedDependencyLockStore
from app.runtime.install_transactions import INSTALL_QUARANTINE_FILENAME
from app.runtime.isolation import CapsuleLock, InstallState, InstallStatus
from app.runtime.model_gc import model_reference_cleanup_policy
from app.runtime.supervisor import RunnerDescriptor, RunnerStatus
from app.workflows.package import WorkflowPackage

DEFAULT_FAILED_TRANSACTION_RETENTION_DAYS = 7
DEFAULT_UNREFERENCED_RUNTIME_RETENTION_DAYS = 14
DEFAULT_ORPHAN_MODEL_VIEW_RETENTION_DAYS = 7
DEFAULT_WHEEL_CACHE_CAP_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_CUSTOM_NODE_SOURCE_CACHE_CAP_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_PACKAGE_ARCHIVE_CACHE_CAP_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_LARGE_MODEL_CONFIRMATION_BYTES = 1024 * 1024 * 1024


class RuntimeStorageArtifactKind(StrEnum):
    DEPENDENCY_ENV = "dependency_env"
    RUNNER_WORKSPACE = "runner_workspace"
    MODEL_BLOB = "model_blob"
    MODEL_VIEW = "model_view"
    TRANSACTION = "transaction"
    WHEEL_CACHE_ENTRY = "wheel_cache_entry"
    CUSTOM_NODE_SOURCE_CACHE_ENTRY = "custom_node_source_cache_entry"
    PACKAGE_ARCHIVE = "package_archive"


class RuntimeStorageGcAction(StrEnum):
    KEEP = "keep"
    DELETE = "delete"
    SKIP_ACTIVE_RUNNER = "skip_active_runner"
    SKIP_REFERENCED = "skip_referenced"
    SKIP_RETENTION_WINDOW = "skip_retention_window"
    SKIP_LARGE_MODEL_CONFIRMATION = "skip_large_model_confirmation"
    SKIP_USER_LOCAL_SOURCE = "skip_user_local_source"


@dataclass(frozen=True)
class RuntimeStorageGcConfig:
    failed_transaction_retention_days: int = DEFAULT_FAILED_TRANSACTION_RETENTION_DAYS
    unreferenced_runtime_retention_days: int = DEFAULT_UNREFERENCED_RUNTIME_RETENTION_DAYS
    orphan_model_view_retention_days: int = DEFAULT_ORPHAN_MODEL_VIEW_RETENTION_DAYS
    wheel_cache_cap_bytes: int = DEFAULT_WHEEL_CACHE_CAP_BYTES
    custom_node_source_cache_cap_bytes: int = DEFAULT_CUSTOM_NODE_SOURCE_CACHE_CAP_BYTES
    package_archive_cache_cap_bytes: int = DEFAULT_PACKAGE_ARCHIVE_CACHE_CAP_BYTES
    large_model_confirmation_bytes: int = DEFAULT_LARGE_MODEL_CONFIRMATION_BYTES
    pinned_dependency_env_fingerprints: frozenset[str] = frozenset()
    pinned_runner_workspace_fingerprints: frozenset[str] = frozenset()
    pinned_model_blob_paths: frozenset[str] = frozenset()


@dataclass(frozen=True)
class RuntimeStorageRoots:
    dependency_envs_dir: Path
    runner_workspaces_dir: Path
    install_transactions_dir: Path
    workflow_packages_store_dir: Path
    bundled_workflows_dir: Path
    user_workflows_dir: Path
    custom_node_cache_dir: Path
    wheel_cache_dir: Path
    model_blobs_dir: Path
    model_materialized_dir: Path
    dependency_locks_dir: Path

    @classmethod
    def from_paths(cls, paths: NoofyPaths) -> RuntimeStorageRoots:
        return cls(
            dependency_envs_dir=paths.dependency_envs_dir,
            runner_workspaces_dir=paths.runner_workspaces_dir,
            install_transactions_dir=paths.install_transactions_dir,
            workflow_packages_store_dir=paths.workflow_packages_store_dir,
            bundled_workflows_dir=paths.bundled_workflows_dir,
            user_workflows_dir=paths.user_workflows_dir,
            custom_node_cache_dir=paths.custom_node_cache_dir,
            wheel_cache_dir=paths.wheel_cache_dir,
            model_blobs_dir=paths.model_blobs_dir,
            model_materialized_dir=paths.model_materialized_dir,
            dependency_locks_dir=paths.dependency_locks_dir,
        )


@dataclass
class RuntimeStorageArtifactMetadata:
    kind: RuntimeStorageArtifactKind
    path: Path
    size_bytes: int
    created_at: str | None
    last_used_at: str | None
    referenced_workflows: set[str] = field(default_factory=set)
    status: str = "unreferenced"
    trust_level: str | None = None
    fingerprint: str | None = None
    protected: bool = False
    developer_details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeStorageGcDecision:
    artifact_kind: RuntimeStorageArtifactKind
    path: Path
    action: RuntimeStorageGcAction
    reason: str
    size_bytes: int
    referenced_workflows: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeStorageGcResult:
    decisions: list[RuntimeStorageGcDecision]
    bytes_deleted: int = 0

    @property
    def deleted_paths(self) -> list[Path]:
        return [decision.path for decision in self.decisions if decision.action is RuntimeStorageGcAction.DELETE]


@dataclass(frozen=True)
class RuntimeStorageReferenceIndex:
    artifacts: list[RuntimeStorageArtifactMetadata]

    def by_kind(self, kind: RuntimeStorageArtifactKind) -> list[RuntimeStorageArtifactMetadata]:
        return [artifact for artifact in self.artifacts if artifact.kind is kind]

    def to_diagnostics(self) -> dict[str, object]:
        return {
            "artifacts": [
                {
                    "kind": artifact.kind.value,
                    "path": str(artifact.path),
                    "size_bytes": artifact.size_bytes,
                    "created_at": artifact.created_at,
                    "last_used_at": artifact.last_used_at,
                    "referenced_workflows": sorted(artifact.referenced_workflows),
                    "status": artifact.status,
                    "trust_level": artifact.trust_level,
                    "fingerprint": artifact.fingerprint,
                    "protected": artifact.protected,
                    "developer_details": artifact.developer_details,
                }
                for artifact in self.artifacts
            ]
        }


@dataclass(frozen=True)
class _WorkflowRecord:
    workflow_id: str
    capsule_fingerprint: str | None
    trust_level: str | None
    package_dir: Path
    source_archive_path: Path | None
    custom_node_source_cache_refs: tuple[str, ...] = ()


class RuntimeStorageGarbageCollector:
    def __init__(
        self,
        *,
        roots: RuntimeStorageRoots,
        install_states: Iterable[InstallState],
        runner_descriptors: Iterable[RunnerDescriptor] = (),
        config: RuntimeStorageGcConfig | None = None,
        log_store: LogStore | None = None,
    ) -> None:
        self.roots = roots
        self.install_states = list(install_states)
        self.runner_descriptors = list(runner_descriptors)
        self.config = config or RuntimeStorageGcConfig()
        self.log_store = log_store or LogStore()

    def build_reference_index(self) -> RuntimeStorageReferenceIndex:
        workflow_records = _scan_workflow_records(
            [
                self.roots.bundled_workflows_dir,
                self.roots.user_workflows_dir,
                self.roots.workflow_packages_store_dir,
            ]
        )
        workflows_by_capsule = {
            record.capsule_fingerprint: record
            for record in workflow_records
            if record.capsule_fingerprint is not None
        }
        root_states = [
            state
            for state in self.install_states
            if state.status in {InstallStatus.READY, InstallStatus.PREPARED_NEEDS_INPUT_SETUP}
        ]
        workflow_for_state = {
            state.capsule_fingerprint: _workflow_id_for_state(state, workflows_by_capsule)
            for state in root_states
        }
        workflow_trust_by_id = {
            workflow_for_state[state.capsule_fingerprint]: workflows_by_capsule.get(state.capsule_fingerprint).trust_level
            if state.capsule_fingerprint in workflows_by_capsule
            else None
            for state in root_states
        }
        referenced_dependency_envs: dict[str, set[str]] = {}
        referenced_runner_workspaces: dict[str, set[str]] = {}
        referenced_model_blobs: dict[Path, set[str]] = {}
        referenced_model_views: dict[Path, set[str]] = {}
        protected_user_sources: set[Path] = set()

        for state in root_states:
            workflow_id = workflow_for_state[state.capsule_fingerprint]
            if state.dependency_env_fingerprint:
                referenced_dependency_envs.setdefault(state.dependency_env_fingerprint, set()).add(workflow_id)
            if state.runner_workspace_fingerprint:
                referenced_runner_workspaces.setdefault(state.runner_workspace_fingerprint, set()).add(workflow_id)
            for model_ref in state.model_references:
                policy = model_reference_cleanup_policy(model_ref)
                if model_ref.asset_ownership in {AssetOwnership.USER_LOCAL, AssetOwnership.EXTERNAL_REFERENCE}:
                    if policy.source_path is not None:
                        protected_user_sources.add(policy.source_path)
                if policy.source_path is not None and model_ref.asset_ownership in {
                    AssetOwnership.NOOFY_DOWNLOADED,
                    AssetOwnership.NOOFY_IMPORTED,
                }:
                    referenced_model_blobs.setdefault(policy.source_path, set()).add(workflow_id)
                if policy.materialized_path is not None:
                    model_view = _model_view_root_for_path(self.roots.model_materialized_dir, policy.materialized_path)
                    if model_view is not None:
                        referenced_model_views.setdefault(model_view, set()).add(workflow_id)

        for runner in self.runner_descriptors:
            if not _runner_protects_artifacts(runner):
                continue
            workflow_id = f"runner:{runner.runner_id}"
            if runner.dependency_env_fingerprint:
                referenced_dependency_envs.setdefault(runner.dependency_env_fingerprint, set()).add(workflow_id)
            if runner.runner_workspace_fingerprint:
                referenced_runner_workspaces.setdefault(runner.runner_workspace_fingerprint, set()).add(workflow_id)
            if runner.model_view_fingerprint:
                referenced_model_views.setdefault(
                    self.roots.model_materialized_dir
                    / "views"
                    / f"model-view-{_safe_fingerprint(runner.model_view_fingerprint)}",
                    set(),
                ).add(workflow_id)

        referenced_wheels = self._referenced_wheel_cache_paths(referenced_dependency_envs)
        referenced_custom_node_sources = self._referenced_custom_node_source_cache_paths(
            workflow_records,
            workflow_for_state,
        )
        package_archive_refs = self._referenced_package_archives(workflow_records, workflow_for_state)

        artifacts: list[RuntimeStorageArtifactMetadata] = []
        artifacts.extend(
            self._dependency_env_artifacts(referenced_dependency_envs, workflow_trust_by_id)
        )
        artifacts.extend(
            self._runner_workspace_artifacts(referenced_runner_workspaces, workflow_trust_by_id)
        )
        artifacts.extend(self._model_blob_artifacts(referenced_model_blobs))
        artifacts.extend(self._model_view_artifacts(referenced_model_views))
        artifacts.extend(self._transaction_artifacts())
        artifacts.extend(self._cache_entry_artifacts(RuntimeStorageArtifactKind.WHEEL_CACHE_ENTRY, self.roots.wheel_cache_dir, referenced_wheels))
        artifacts.extend(
            self._cache_entry_artifacts(
                RuntimeStorageArtifactKind.CUSTOM_NODE_SOURCE_CACHE_ENTRY,
                self.roots.custom_node_cache_dir,
                referenced_custom_node_sources,
            )
        )
        artifacts.extend(self._package_archive_artifacts(package_archive_refs))

        for source in protected_user_sources:
            artifacts.append(
                RuntimeStorageArtifactMetadata(
                    kind=RuntimeStorageArtifactKind.MODEL_BLOB,
                    path=source,
                    size_bytes=_path_size(source),
                    created_at=_stat_time(source, "ctime"),
                    last_used_at=_stat_time(source, "mtime"),
                    status="user_local_source_protected",
                    protected=True,
                    developer_details={"ownership": "user_local"},
                )
            )

        return RuntimeStorageReferenceIndex(artifacts=artifacts)

    def collect_garbage(
        self,
        *,
        dry_run: bool = False,
        confirm_large_model_deletion: bool = False,
        now: datetime | None = None,
    ) -> RuntimeStorageGcResult:
        now = now or datetime.now(UTC)
        index = self.build_reference_index()
        decisions: list[RuntimeStorageGcDecision] = []
        bytes_deleted = 0

        for artifact in index.artifacts:
            decision = self._retention_decision(
                artifact,
                now=now,
                confirm_large_model_deletion=confirm_large_model_deletion,
            )
            if decision is None:
                continue
            deleted = self._apply_decision(decision, dry_run=dry_run)
            bytes_deleted += deleted
            decisions.append(decision)

        for cap_kind, cap_bytes in (
            (RuntimeStorageArtifactKind.WHEEL_CACHE_ENTRY, self.config.wheel_cache_cap_bytes),
            (RuntimeStorageArtifactKind.CUSTOM_NODE_SOURCE_CACHE_ENTRY, self.config.custom_node_source_cache_cap_bytes),
            (RuntimeStorageArtifactKind.PACKAGE_ARCHIVE, self.config.package_archive_cache_cap_bytes),
        ):
            cap_decisions = self._cap_decisions(index.by_kind(cap_kind), cap_bytes)
            for decision in cap_decisions:
                deleted = self._apply_decision(decision, dry_run=dry_run)
                bytes_deleted += deleted
                decisions.append(decision)

        if decisions:
            self.log_store.add(
                "info",
                "Runtime storage garbage collection completed",
                "runtime.storage_gc",
                details={
                    "dry_run": dry_run,
                    "decisions": [
                        {
                            "path": str(decision.path),
                            "kind": decision.artifact_kind.value,
                            "action": decision.action.value,
                            "reason": decision.reason,
                            "size_bytes": decision.size_bytes,
                        }
                        for decision in decisions
                    ],
                    "bytes_deleted": bytes_deleted,
                },
            )

        return RuntimeStorageGcResult(decisions=decisions, bytes_deleted=bytes_deleted)

    def _retention_decision(
        self,
        artifact: RuntimeStorageArtifactMetadata,
        *,
        now: datetime,
        confirm_large_model_deletion: bool,
    ) -> RuntimeStorageGcDecision | None:
        refs = tuple(sorted(artifact.referenced_workflows))
        if artifact.protected:
            if artifact.status == "active_runner_protected" or _refs_include_runner(artifact.referenced_workflows):
                action = RuntimeStorageGcAction.SKIP_ACTIVE_RUNNER
                reason = "artifact is protected by an active or idle-warm runner"
            elif artifact.status == "user_local_source_protected":
                action = RuntimeStorageGcAction.SKIP_USER_LOCAL_SOURCE
                reason = artifact.status
            else:
                action = RuntimeStorageGcAction.SKIP_REFERENCED
                reason = "artifact is pinned by storage policy"
            return RuntimeStorageGcDecision(
                artifact.kind,
                artifact.path,
                action,
                reason,
                artifact.size_bytes,
                refs,
            )
        if refs:
            return RuntimeStorageGcDecision(
                artifact.kind,
                artifact.path,
                RuntimeStorageGcAction.SKIP_REFERENCED,
                "artifact is referenced by a GC root",
                artifact.size_bytes,
                refs,
            )
        if artifact.kind is RuntimeStorageArtifactKind.TRANSACTION:
            retain_until = artifact.developer_details.get("retain_until")
            if isinstance(retain_until, str):
                parsed = _parse_datetime(retain_until)
                if parsed is not None and parsed > now:
                    return RuntimeStorageGcDecision(
                        artifact.kind,
                        artifact.path,
                        RuntimeStorageGcAction.SKIP_RETENTION_WINDOW,
                        "quarantine retention window has not expired",
                        artifact.size_bytes,
                        refs,
                    )
            elif not _older_than(
                artifact.last_used_at or artifact.created_at,
                now,
                self.config.failed_transaction_retention_days,
            ):
                return RuntimeStorageGcDecision(
                    artifact.kind,
                    artifact.path,
                    RuntimeStorageGcAction.SKIP_RETENTION_WINDOW,
                    "failed transaction retention window has not expired",
                    artifact.size_bytes,
                    refs,
                )
            return RuntimeStorageGcDecision(
                artifact.kind,
                artifact.path,
                RuntimeStorageGcAction.DELETE,
                "expired failed transaction or unreferenced staging directory",
                artifact.size_bytes,
                refs,
            )
        if artifact.kind in {RuntimeStorageArtifactKind.DEPENDENCY_ENV, RuntimeStorageArtifactKind.RUNNER_WORKSPACE}:
            if not _older_than(artifact.last_used_at or artifact.created_at, now, self.config.unreferenced_runtime_retention_days):
                return RuntimeStorageGcDecision(
                    artifact.kind,
                    artifact.path,
                    RuntimeStorageGcAction.SKIP_RETENTION_WINDOW,
                    "unreferenced runtime artifact retention window has not expired",
                    artifact.size_bytes,
                    refs,
                )
            return RuntimeStorageGcDecision(
                artifact.kind,
                artifact.path,
                RuntimeStorageGcAction.DELETE,
                "unreferenced runtime artifact retention expired",
                artifact.size_bytes,
                refs,
            )
        if artifact.kind is RuntimeStorageArtifactKind.MODEL_VIEW:
            if not _older_than(artifact.last_used_at or artifact.created_at, now, self.config.orphan_model_view_retention_days):
                return RuntimeStorageGcDecision(
                    artifact.kind,
                    artifact.path,
                    RuntimeStorageGcAction.SKIP_RETENTION_WINDOW,
                    "orphan materialized model-view retention window has not expired",
                    artifact.size_bytes,
                    refs,
                )
            return RuntimeStorageGcDecision(
                artifact.kind,
                artifact.path,
                RuntimeStorageGcAction.DELETE,
                "orphan materialized model-view retention expired",
                artifact.size_bytes,
                refs,
            )
        if artifact.kind is RuntimeStorageArtifactKind.MODEL_BLOB and artifact.path.is_relative_to(self.roots.model_blobs_dir):
            if artifact.size_bytes >= self.config.large_model_confirmation_bytes and not confirm_large_model_deletion:
                return RuntimeStorageGcDecision(
                    artifact.kind,
                    artifact.path,
                    RuntimeStorageGcAction.SKIP_LARGE_MODEL_CONFIRMATION,
                    "large model blob requires explicit cleanup confirmation",
                    artifact.size_bytes,
                    refs,
                )
            return RuntimeStorageGcDecision(
                artifact.kind,
                artifact.path,
                RuntimeStorageGcAction.DELETE,
                "unreferenced Noofy-owned model blob",
                artifact.size_bytes,
                refs,
            )
        return None

    def _cap_decisions(
        self,
        artifacts: list[RuntimeStorageArtifactMetadata],
        cap_bytes: int,
    ) -> list[RuntimeStorageGcDecision]:
        total = sum(artifact.size_bytes for artifact in artifacts)
        if total <= cap_bytes:
            return []
        decisions: list[RuntimeStorageGcDecision] = []
        reclaim_needed = total - cap_bytes
        reclaimed = 0
        candidates = [
            artifact
            for artifact in artifacts
            if not artifact.protected and not artifact.referenced_workflows
        ]
        for artifact in sorted(candidates, key=lambda item: item.last_used_at or item.created_at or ""):
            decisions.append(
                RuntimeStorageGcDecision(
                    artifact.kind,
                    artifact.path,
                    RuntimeStorageGcAction.DELETE,
                    "cache LRU cap exceeded",
                    artifact.size_bytes,
                    tuple(sorted(artifact.referenced_workflows)),
                )
            )
            reclaimed += artifact.size_bytes
            if reclaimed >= reclaim_needed:
                break
        return decisions

    def _apply_decision(self, decision: RuntimeStorageGcDecision, *, dry_run: bool) -> int:
        if decision.action is not RuntimeStorageGcAction.DELETE:
            return 0
        size = decision.size_bytes
        if dry_run:
            return 0
        _delete_path(decision.path)
        return size

    def _dependency_env_artifacts(
        self,
        refs: dict[str, set[str]],
        workflow_trust_by_id: dict[str, str | None],
    ) -> list[RuntimeStorageArtifactMetadata]:
        artifacts: list[RuntimeStorageArtifactMetadata] = []
        for path in sorted(self.roots.dependency_envs_dir.glob("dep-env-*")):
            if not path.is_dir():
                continue
            manifest = _read_json(path / "manifest.json")
            fingerprint = manifest.get("fingerprint") if isinstance(manifest.get("fingerprint"), str) else _fingerprint_from_dir(path, "dep-env-")
            workflows = set(refs.get(fingerprint, set()))
            protected = fingerprint in self.config.pinned_dependency_env_fingerprints or _refs_include_runner(workflows)
            artifacts.append(
                _metadata_for_path(
                    RuntimeStorageArtifactKind.DEPENDENCY_ENV,
                    path,
                    fingerprint=fingerprint,
                    referenced_workflows=workflows,
                    status="active_runner_protected" if protected else str(manifest.get("status") or ("referenced" if workflows else "unreferenced")),
                    protected=protected,
                    trust_level=_trust_for_workflows(workflows, workflow_trust_by_id),
                    developer_details={"manifest": manifest},
                )
            )
        return artifacts

    def _runner_workspace_artifacts(
        self,
        refs: dict[str, set[str]],
        workflow_trust_by_id: dict[str, str | None],
    ) -> list[RuntimeStorageArtifactMetadata]:
        artifacts: list[RuntimeStorageArtifactMetadata] = []
        for path in sorted(self.roots.runner_workspaces_dir.glob("runner-workspace-*")):
            if not path.is_dir():
                continue
            manifest = _read_json(path / "manifest.json")
            fingerprint = manifest.get("fingerprint") if isinstance(manifest.get("fingerprint"), str) else _fingerprint_from_dir(path, "runner-workspace-")
            workflows = set(refs.get(fingerprint, set()))
            protected = fingerprint in self.config.pinned_runner_workspace_fingerprints or _refs_include_runner(workflows)
            artifacts.append(
                _metadata_for_path(
                    RuntimeStorageArtifactKind.RUNNER_WORKSPACE,
                    path,
                    fingerprint=fingerprint,
                    referenced_workflows=workflows,
                    status="active_runner_protected" if protected else str(manifest.get("status") or ("referenced" if workflows else "unreferenced")),
                    protected=protected,
                    trust_level=_trust_for_workflows(workflows, workflow_trust_by_id),
                    developer_details={"manifest": manifest},
                )
            )
        return artifacts

    def _model_blob_artifacts(self, refs: dict[Path, set[str]]) -> list[RuntimeStorageArtifactMetadata]:
        artifacts: list[RuntimeStorageArtifactMetadata] = []
        for path in sorted(self.roots.model_blobs_dir.glob("*/blob")):
            if not path.is_file():
                continue
            workflows = set(refs.get(path, set()))
            protected = str(path) in self.config.pinned_model_blob_paths
            artifacts.append(
                _metadata_for_path(
                    RuntimeStorageArtifactKind.MODEL_BLOB,
                    path,
                    fingerprint=path.parent.name,
                    referenced_workflows=workflows,
                    status="referenced" if workflows else "unreferenced",
                    protected=protected,
                )
            )
        return artifacts

    def _model_view_artifacts(self, refs: dict[Path, set[str]]) -> list[RuntimeStorageArtifactMetadata]:
        artifacts: list[RuntimeStorageArtifactMetadata] = []
        for path in sorted((self.roots.model_materialized_dir / "views").glob("model-view-*")):
            if not path.is_dir():
                continue
            workflows = set(refs.get(path, set()))
            artifacts.append(
                _metadata_for_path(
                    RuntimeStorageArtifactKind.MODEL_VIEW,
                    path,
                    fingerprint=_fingerprint_from_dir(path, "model-view-"),
                    referenced_workflows=workflows,
                    status="active_runner_protected" if _refs_include_runner(workflows) else ("referenced" if workflows else "orphan"),
                    protected=_refs_include_runner(workflows),
                )
            )
        return artifacts

    def _transaction_artifacts(self) -> list[RuntimeStorageArtifactMetadata]:
        artifacts: list[RuntimeStorageArtifactMetadata] = []
        if not self.roots.install_transactions_dir.exists():
            return artifacts
        for path in sorted(self.roots.install_transactions_dir.glob("install-*")):
            if not path.is_dir():
                continue
            quarantine = _read_json(path / INSTALL_QUARANTINE_FILENAME)
            manifest = _read_json(path / "transaction.json")
            details = {"manifest": manifest, "quarantine": quarantine}
            if isinstance(quarantine.get("retain_until"), str):
                details["retain_until"] = quarantine["retain_until"]
            artifacts.append(
                _metadata_for_path(
                    RuntimeStorageArtifactKind.TRANSACTION,
                    path,
                    status=str(quarantine.get("status") or manifest.get("status") or "stale"),
                    developer_details=details,
                )
            )
        return artifacts

    def _cache_entry_artifacts(
        self,
        kind: RuntimeStorageArtifactKind,
        root: Path,
        refs: dict[Path, set[str]],
    ) -> list[RuntimeStorageArtifactMetadata]:
        if not root.exists():
            return []
        artifacts: list[RuntimeStorageArtifactMetadata] = []
        for path in sorted(root.iterdir()):
            workflows = set(refs.get(path, set()))
            artifacts.append(
                _metadata_for_path(
                    kind,
                    path,
                    referenced_workflows=workflows,
                    status="referenced" if workflows else "unreferenced",
                )
            )
        return artifacts

    def _package_archive_artifacts(self, refs: dict[Path, set[str]]) -> list[RuntimeStorageArtifactMetadata]:
        artifacts: list[RuntimeStorageArtifactMetadata] = []
        if not self.roots.workflow_packages_store_dir.exists():
            return artifacts
        for path in sorted(self.roots.workflow_packages_store_dir.glob("*/*/*/source-archive.noofy")):
            workflows = set(refs.get(path, set()))
            artifacts.append(
                _metadata_for_path(
                    RuntimeStorageArtifactKind.PACKAGE_ARCHIVE,
                    path,
                    referenced_workflows=workflows,
                    status="referenced" if workflows else "unreferenced",
                )
            )
        return artifacts

    def _referenced_wheel_cache_paths(self, dependency_env_refs: dict[str, set[str]]) -> dict[Path, set[str]]:
        store = ResolvedDependencyLockStore(self.roots.dependency_locks_dir)
        refs: dict[Path, set[str]] = {}
        for fingerprint, workflows in dependency_env_refs.items():
            manifest = _read_json(self.roots.dependency_envs_dir / f"dep-env-{_safe_fingerprint(fingerprint)}" / "manifest.json")
            lock_hash = manifest.get("dependency_lock_hash")
            if not isinstance(lock_hash, str):
                continue
            try:
                lock = store.read(lock_hash)
            except Exception:
                continue
            for wheel in lock.wheels:
                if wheel.approved_cache_ref:
                    refs.setdefault(self.roots.wheel_cache_dir / wheel.approved_cache_ref, set()).update(workflows)
        return refs

    def _referenced_package_archives(
        self,
        workflow_records: list[_WorkflowRecord],
        workflow_for_state: dict[str, str],
    ) -> dict[Path, set[str]]:
        rooted_workflows = set(workflow_for_state.values())
        refs: dict[Path, set[str]] = {}
        for record in workflow_records:
            if record.source_archive_path is None:
                continue
            if record.workflow_id in rooted_workflows:
                refs.setdefault(record.source_archive_path, set()).add(record.workflow_id)
        return refs

    def _referenced_custom_node_source_cache_paths(
        self,
        workflow_records: list[_WorkflowRecord],
        workflow_for_state: dict[str, str],
    ) -> dict[Path, set[str]]:
        rooted_workflows = set(workflow_for_state.values())
        refs: dict[Path, set[str]] = {}
        for record in workflow_records:
            if record.workflow_id not in rooted_workflows:
                continue
            for source_cache_ref in record.custom_node_source_cache_refs:
                refs.setdefault(
                    _custom_node_source_cache_entry_path(self.roots.custom_node_cache_dir, source_cache_ref),
                    set(),
                ).add(
                    record.workflow_id,
                )
        return refs


def _metadata_for_path(
    kind: RuntimeStorageArtifactKind,
    path: Path,
    *,
    fingerprint: str | None = None,
    referenced_workflows: set[str] | None = None,
    status: str = "unreferenced",
    trust_level: str | None = None,
    protected: bool = False,
    developer_details: dict[str, object] | None = None,
) -> RuntimeStorageArtifactMetadata:
    return RuntimeStorageArtifactMetadata(
        kind=kind,
        path=path,
        size_bytes=_path_size(path),
        created_at=_stat_time(path, "ctime"),
        last_used_at=_stat_time(path, "mtime"),
        referenced_workflows=referenced_workflows or set(),
        status=status,
        trust_level=trust_level,
        fingerprint=fingerprint,
        protected=protected,
        developer_details=developer_details or {},
    )


def _scan_workflow_records(roots: Iterable[Path]) -> list[_WorkflowRecord]:
    records: list[_WorkflowRecord] = []
    seen_package_dirs: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        for package_path in sorted({*root.glob("*/package.json"), *root.glob("*/*/*/package.json")}):
            package_dir = package_path.parent
            if package_dir in seen_package_dirs:
                continue
            seen_package_dirs.add(package_dir)
            package = _read_package(package_path)
            if package is None:
                continue
            capsule = _read_capsule(package_dir / "capsule.lock.json")
            records.append(
                _WorkflowRecord(
                    workflow_id=package.metadata.id,
                    capsule_fingerprint=capsule.runtime.capsule_fingerprint if capsule else None,
                    trust_level=package.identity.trust_level if package.identity else None,
                    package_dir=package_dir,
                    source_archive_path=(package_dir / "source-archive.noofy")
                    if (package_dir / "source-archive.noofy").exists()
                    else None,
                    custom_node_source_cache_refs=tuple(
                        sorted(
                            custom_node.source_cache_ref
                            for custom_node in capsule.custom_nodes
                            if custom_node.source_cache_ref is not None
                        )
                    )
                    if capsule
                    else (),
                )
            )
    return records


def _custom_node_source_cache_entry_path(root: Path, source_cache_ref: str) -> Path:
    return root / Path(source_cache_ref).parts[0]


def _read_package(path: Path) -> WorkflowPackage | None:
    try:
        return WorkflowPackage.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _read_capsule(path: Path) -> CapsuleLock | None:
    try:
        return CapsuleLock.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _workflow_id_for_state(state: InstallState, workflows_by_capsule: dict[str, _WorkflowRecord]) -> str:
    record = workflows_by_capsule.get(state.capsule_fingerprint)
    return record.workflow_id if record else state.capsule_fingerprint


def _trust_for_workflows(workflows: set[str], workflow_trust_by_id: dict[str, str | None]) -> str | None:
    trust = sorted(
        {
            workflow_trust_by_id[workflow_id]
            for workflow_id in workflows
            if workflow_id in workflow_trust_by_id and workflow_trust_by_id[workflow_id] is not None
        }
    )
    return trust[0] if len(trust) == 1 else None


def _model_view_root_for_path(materialized_dir: Path, path: Path) -> Path | None:
    views_dir = materialized_dir / "views"
    try:
        relative = path.relative_to(views_dir)
    except ValueError:
        return None
    if len(relative.parts) < 2 or not relative.parts[0].startswith("model-view-"):
        return None
    return views_dir / relative.parts[0]


def _runner_protects_artifacts(runner: RunnerDescriptor) -> bool:
    if runner.current_job_id:
        return True
    if runner.open_workflow_lease_count > 0:
        return True
    if runner.status in {
        RunnerStatus.RUNNING,
        RunnerStatus.QUEUED,
        RunnerStatus.QUEUED_PENDING_SWITCH,
        RunnerStatus.QUEUED_PENDING_MEMORY,
        RunnerStatus.IDLE_WARM,
        RunnerStatus.SWITCHING,
        RunnerStatus.LOADING_MODEL,
        RunnerStatus.RETRYING_AFTER_MEMORY_CLEANUP,
        RunnerStatus.WAITING_FOR_MEMORY_RELEASE,
    }:
        return True
    if runner.closed_view_cooldown_expires_at:
        expires = _parse_datetime(runner.closed_view_cooldown_expires_at)
        return expires is None or expires > datetime.now(UTC)
    return False


def _refs_include_runner(workflows: set[str]) -> bool:
    return any(workflow.startswith("runner:") for workflow in workflows)


def _fingerprint_from_dir(path: Path, prefix: str) -> str:
    return "sha256:" + path.name.removeprefix(prefix)


def _safe_fingerprint(fingerprint: str) -> str:
    return fingerprint.replace("sha256:", "").replace("/", "_").replace("\\", "_").replace(":", "_")


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _path_size(path: Path) -> int:
    if not path.exists() and not path.is_symlink():
        return 0
    if path.is_file() or path.is_symlink():
        try:
            return path.lstat().st_size
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        try:
            total += child.lstat().st_size
        except OSError:
            continue
    return total


def _stat_time(path: Path, field_name: str) -> str | None:
    try:
        stat = path.lstat()
    except OSError:
        return None
    value = stat.st_ctime if field_name == "ctime" else stat.st_mtime
    return datetime.fromtimestamp(value, UTC).isoformat()


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _older_than(value: str | None, now: datetime, days: int) -> bool:
    if value is None:
        return False
    parsed = _parse_datetime(value)
    if parsed is None:
        return False
    return parsed <= now - timedelta(days=days)


def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return
    parent = path.parent
    if parent.name and parent.name != path.anchor:
        try:
            parent.rmdir()
        except OSError:
            pass
