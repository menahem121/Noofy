from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.diagnostics import LogStore
from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.runtime.dependencies.dependency_lock import (
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyWheel,
    ResolverMetadata,
    with_computed_lock_hash,
)
from app.runtime.dependencies.dependency_lock_store import ResolvedDependencyLockStore
from app.runtime.dependencies.isolation import (
    InstallState,
    InstallStatus,
    InstalledModelReference,
    SmokeTestStatus,
)
from app.runtime.storage.storage_gc import (
    RuntimeStorageArtifactKind,
    RuntimeStorageGarbageCollector,
    RuntimeStorageGcAction,
    RuntimeStorageGcConfig,
    RuntimeStorageRoots,
)
from app.runtime.runners.supervisor import RunnerDescriptor, RunnerKind, RunnerStatus


def _roots(tmp_path: Path) -> RuntimeStorageRoots:
    roots = RuntimeStorageRoots(
        dependency_envs_dir=tmp_path / "runtime-store" / "envs",
        runner_workspaces_dir=tmp_path / "runtime-store" / "runner-workspaces",
        install_transactions_dir=tmp_path / "runtime-store" / "transactions",
        workflow_packages_store_dir=tmp_path / "workflow-store" / "packages",
        bundled_workflows_dir=tmp_path / "bundled-packages",
        user_workflows_dir=tmp_path / "user-packages",
        custom_node_cache_dir=tmp_path / "custom-node-cache",
        wheel_cache_dir=tmp_path / "wheel-cache",
        model_blobs_dir=tmp_path / "model-store" / "blobs" / "sha256",
        model_materialized_dir=tmp_path / "model-store" / "materialized",
        dependency_locks_dir=tmp_path / "runtime-store" / "dependency-locks",
    )
    for directory in (
        roots.dependency_envs_dir,
        roots.runner_workspaces_dir,
        roots.install_transactions_dir,
        roots.workflow_packages_store_dir,
        roots.bundled_workflows_dir,
        roots.user_workflows_dir,
        roots.custom_node_cache_dir,
        roots.wheel_cache_dir,
        roots.model_blobs_dir,
        roots.model_materialized_dir,
        roots.dependency_locks_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return roots


def _state(
    capsule_fingerprint: str,
    *,
    dependency_env_fingerprint: str | None = None,
    runner_workspace_fingerprint: str | None = None,
    model_references: list[InstalledModelReference] | None = None,
    status: InstallStatus = InstallStatus.READY,
) -> InstallState:
    return InstallState(
        schema_version="0.1.0",
        capsule_fingerprint=capsule_fingerprint,
        status=status,
        smoke_test_status=SmokeTestStatus.PASSED,
        dependency_env_fingerprint=dependency_env_fingerprint,
        runner_workspace_fingerprint=runner_workspace_fingerprint,
        model_references=model_references or [],
    )


def _write_dependency_env(
    roots: RuntimeStorageRoots, fingerprint: str, *, lock_hash: str = "sha256:lock"
) -> Path:
    path = roots.dependency_envs_dir / f"dep-env-{fingerprint.removeprefix('sha256:')}"
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "status": "ready",
                "dependency_lock_hash": lock_hash,
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_runner_workspace(roots: RuntimeStorageRoots, fingerprint: str) -> Path:
    path = (
        roots.runner_workspaces_dir
        / f"runner-workspace-{fingerprint.removeprefix('sha256:')}"
    )
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(
        json.dumps({"fingerprint": fingerprint, "status": "ready"}),
        encoding="utf-8",
    )
    return path


def _write_model_blob(roots: RuntimeStorageRoots, sha: str, content: bytes) -> Path:
    path = roots.model_blobs_dir / sha / "blob"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _write_model_view(
    roots: RuntimeStorageRoots, fingerprint: str, content: bytes = b"view"
) -> Path:
    path = (
        roots.model_materialized_dir
        / "views"
        / f"model-view-{fingerprint.removeprefix('sha256:')}"
    )
    path.mkdir(parents=True, exist_ok=True)
    (path / "checkpoints").mkdir()
    (path / "checkpoints" / "model.safetensors").write_bytes(content)
    return path


def _model_ref(blob_path: Path, materialized_path: Path) -> InstalledModelReference:
    return InstalledModelReference(
        requirement_id="model",
        comfyui_folder="checkpoints",
        filename="model.safetensors",
        verification_level=ModelVerificationLevel.SHA256_SIZE,
        asset_ownership=AssetOwnership.NOOFY_DOWNLOADED,
        model_id="model",
        sha256="sha256:" + ("a" * 64),
        size_bytes=max(blob_path.stat().st_size, 1),
        blob_path=str(blob_path),
        materialized_path=str(materialized_path),
    )


def _old(path: Path, *, days: int = 30) -> None:
    timestamp = (datetime.now(UTC) - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    if path.is_dir():
        for child in path.rglob("*"):
            os.utime(child, (timestamp, timestamp), follow_symlinks=False)


def test_reference_index_keeps_shared_artifacts_after_one_workflow_is_removed(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    dep_fp = "sha256:" + ("d" * 64)
    runner_fp = "sha256:" + ("r" * 64)
    blob = _write_model_blob(roots, "a" * 64, b"shared-model")
    view = _write_model_view(roots, "v" * 64)
    dep_path = _write_dependency_env(roots, dep_fp)
    runner_path = _write_runner_workspace(roots, runner_fp)
    _old(dep_path)
    _old(runner_path)

    states = [
        _state(
            "capsule-a",
            dependency_env_fingerprint=dep_fp,
            runner_workspace_fingerprint=runner_fp,
            model_references=[
                _model_ref(blob, view / "checkpoints" / "model.safetensors")
            ],
        ),
        _state(
            "capsule-b",
            dependency_env_fingerprint=dep_fp,
            runner_workspace_fingerprint=runner_fp,
            model_references=[
                _model_ref(blob, view / "checkpoints" / "model.safetensors")
            ],
        ),
    ]
    index = RuntimeStorageGarbageCollector(
        roots=roots, install_states=states, log_store=LogStore()
    ).build_reference_index()
    dep_artifact = next(
        artifact for artifact in index.artifacts if artifact.path == dep_path
    )
    assert dep_artifact.referenced_workflows == {"capsule-a", "capsule-b"}

    result = RuntimeStorageGarbageCollector(
        roots=roots, install_states=states[1:], log_store=LogStore()
    ).collect_garbage()

    assert dep_path.exists()
    assert runner_path.exists()
    assert blob.exists()
    assert all(
        decision.action is not RuntimeStorageGcAction.DELETE
        for decision in result.decisions
        if decision.path in {dep_path, runner_path, blob}
    )


def test_gc_skips_artifacts_for_active_or_idle_warm_runners(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    dep_fp = "sha256:" + ("e" * 64)
    runner_fp = "sha256:" + ("f" * 64)
    dep_path = _write_dependency_env(roots, dep_fp)
    runner_path = _write_runner_workspace(roots, runner_fp)
    _old(dep_path)
    _old(runner_path)
    runner = RunnerDescriptor(
        runner_id="runner-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:8188",
        fingerprint="runner-fingerprint",
        status=RunnerStatus.IDLE_WARM,
        dependency_env_fingerprint=dep_fp,
        runner_workspace_fingerprint=runner_fp,
        open_workflow_lease_count=1,
        open_workflow_lease_ids=["lease-1"],
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        runner_descriptors=[runner],
        log_store=LogStore(),
    ).collect_garbage()

    assert dep_path.exists()
    assert runner_path.exists()
    assert any(
        decision.action is RuntimeStorageGcAction.SKIP_ACTIVE_RUNNER
        for decision in result.decisions
    )


def test_gc_never_deletes_user_local_model_sources(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    source = tmp_path / "user-models" / "local.safetensors"
    source.parent.mkdir()
    source.write_bytes(b"user-owned")
    ref = InstalledModelReference(
        requirement_id="local",
        comfyui_folder="checkpoints",
        filename="local.safetensors",
        verification_level=ModelVerificationLevel.FILENAME_SIZE,
        asset_ownership=AssetOwnership.USER_LOCAL,
        size_bytes=source.stat().st_size,
        source_path=str(source),
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[_state("capsule-local", model_references=[ref])],
        log_store=LogStore(),
    ).collect_garbage(confirm_large_model_deletion=True)

    assert source.exists()
    assert any(
        decision.path == source
        and decision.action is RuntimeStorageGcAction.SKIP_USER_LOCAL_SOURCE
        for decision in result.decisions
    )


def test_gc_removes_expired_quarantine_but_keeps_recent_transaction(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    expired = roots.install_transactions_dir / "install-expired"
    recent = roots.install_transactions_dir / "install-recent"
    expired.mkdir()
    recent.mkdir()
    now = datetime.now(UTC)
    (expired / "quarantine.json").write_text(
        json.dumps(
            {
                "status": "quarantined",
                "retain_until": (now - timedelta(seconds=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (recent / "quarantine.json").write_text(
        json.dumps(
            {
                "status": "quarantined",
                "retain_until": (now + timedelta(days=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots, install_states=[], log_store=LogStore()
    ).collect_garbage(now=now)

    assert not expired.exists()
    assert recent.exists()
    assert any(
        decision.path == expired and decision.action is RuntimeStorageGcAction.DELETE
        for decision in result.decisions
    )
    assert any(
        decision.path == recent
        and decision.action is RuntimeStorageGcAction.SKIP_RETENTION_WINDOW
        for decision in result.decisions
    )


def test_gc_applies_lru_cap_without_deleting_referenced_wheel(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    dep_fp = "sha256:" + ("9" * 64)
    keep = roots.wheel_cache_dir / "keep.whl"
    old = roots.wheel_cache_dir / "old.whl"
    new = roots.wheel_cache_dir / "new.whl"
    keep.write_bytes(b"keep")
    old.write_bytes(b"old!")
    new.write_bytes(b"new!")
    _old(old, days=40)
    _old(new, days=10)
    lock = with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id="profile",
            runtime_profile_variant_id="variant",
            runtime_profile_manifest_hash="sha256:" + ("1" * 64),
            install_policy_version="policy",
            resolver=ResolverMetadata(name="resolver", version="1"),
            wheels=[
                ResolvedDependencyWheel(
                    name="keep",
                    version="1.0.0",
                    wheel_filename="keep.whl",
                    sha256="sha256:" + ("2" * 64),
                    source_kind=DependencySourceKind.APPROVED_CACHE,
                    approved_cache_ref="keep.whl",
                    relationship=DependencyRelationship.DIRECT,
                    resolver_name="resolver",
                    resolver_version="1",
                )
            ],
        )
    )
    ResolvedDependencyLockStore(roots.dependency_locks_dir).write(lock)
    _write_dependency_env(roots, dep_fp, lock_hash=lock.lock_hash or "")

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[_state("capsule-wheel", dependency_env_fingerprint=dep_fp)],
        config=RuntimeStorageGcConfig(wheel_cache_cap_bytes=8),
        log_store=LogStore(),
    ).collect_garbage()

    assert keep.exists()
    assert not old.exists()
    assert new.exists()
    assert any(
        decision.path == old and decision.reason == "cache LRU cap exceeded"
        for decision in result.decisions
    )


def test_gc_removes_orphan_materialized_model_view_after_retention(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    view = _write_model_view(roots, "0" * 64)
    _old(view)

    result = RuntimeStorageGarbageCollector(
        roots=roots, install_states=[], log_store=LogStore()
    ).collect_garbage()

    assert not view.exists()
    assert any(
        decision.path == view and decision.action is RuntimeStorageGcAction.DELETE
        for decision in result.decisions
    )


def test_internal_model_blob_deletes_without_user_confirmation(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    blob = _write_model_blob(roots, "b" * 64, b"01234567890")

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        log_store=LogStore(),
    ).collect_garbage(confirm_large_model_deletion=False)

    assert not blob.exists()
    assert any(
        decision.path == blob
        and decision.action is RuntimeStorageGcAction.DELETE
        and "Noofy-owned model blob" in decision.reason
        for decision in result.decisions
    )


def test_model_blob_deletion_removes_verification_record_and_directory(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    blob = _write_model_blob(roots, "c" * 64, b"blob-bytes")
    record = blob.with_name("verified.json")
    record.write_text("{}", encoding="utf-8")

    RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        config=RuntimeStorageGcConfig(),
        log_store=LogStore(),
    ).collect_garbage(confirm_large_model_deletion=True)

    # The content-addressed directory is the deletion unit: the verification
    # record must not survive the blob it describes.
    assert not blob.exists()
    assert not record.exists()
    assert not blob.parent.exists()


def test_internal_blob_duplicate_of_visible_noofy_model_is_reclaimed(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    content = b"visible-copy"
    blob = _write_model_blob(roots, "d" * 64, content)
    visible = tmp_path / "Noofy Models" / "checkpoints" / "model.safetensors"
    visible.parent.mkdir(parents=True)
    visible.write_bytes(content)
    view = _write_model_view(roots, "6" * 64, content=content)
    ref = _model_ref(blob, view / "checkpoints" / "model.safetensors").model_copy(
        update={"source_path": str(visible)}
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[_state("capsule-visible-source", model_references=[ref])],
        log_store=LogStore(),
    ).collect_garbage()

    assert visible.exists()
    assert not blob.parent.exists()
    assert view.exists()
    assert any(
        decision.path == blob
        and decision.action is RuntimeStorageGcAction.DELETE
        for decision in result.decisions
    )


def test_internal_blob_backing_visible_symlink_is_retained(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    content = b"visible-symlink"
    blob = _write_model_blob(roots, "e" * 64, content)
    visible = tmp_path / "Noofy Models" / "checkpoints" / "model.safetensors"
    visible.parent.mkdir(parents=True)
    visible.symlink_to(blob)
    view = _write_model_view(roots, "7" * 64, content=content)
    ref = _model_ref(blob, view / "checkpoints" / "model.safetensors").model_copy(
        update={"source_path": str(visible)}
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[_state("capsule-visible-symlink", model_references=[ref])],
        log_store=LogStore(),
    ).collect_garbage()

    assert visible.exists()
    assert blob.exists()
    assert all(
        decision.action is not RuntimeStorageGcAction.DELETE
        for decision in result.decisions
        if decision.path == blob
    )


def test_gc_compacts_large_recent_quarantined_transaction(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    transaction = roots.install_transactions_dir / "install-large"
    payload = transaction / "model-views" / "view-a"
    payload.mkdir(parents=True)
    (payload / "model.safetensors").write_bytes(b"0123456789")
    (transaction / "quarantine.json").write_text(
        json.dumps(
            {
                "status": "quarantined",
                "retain_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    (transaction / "transaction.json").write_text(
        json.dumps({"status": "quarantined"}),
        encoding="utf-8",
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        config=RuntimeStorageGcConfig(transaction_compaction_bytes=1),
        log_store=LogStore(),
    ).collect_garbage()

    assert transaction.exists()
    assert not (transaction / "model-views").exists()
    assert (transaction / "payload-cleanup-summary.json").exists()
    assert any(
        decision.path == transaction
        and decision.action is RuntimeStorageGcAction.COMPACT
        for decision in result.decisions
    )


def test_gc_transaction_cap_compacts_quarantined_payloads(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    transactions: list[Path] = []
    for name in ("install-old", "install-new"):
        transaction = roots.install_transactions_dir / name
        payload = transaction / "model-blobs" / name
        payload.mkdir(parents=True)
        (payload / "blob").write_bytes(b"0123456789")
        (transaction / "quarantine.json").write_text(
            json.dumps(
                {
                    "status": "quarantined",
                    "retain_until": (
                        datetime.now(UTC) + timedelta(days=1)
                    ).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        (transaction / "transaction.json").write_text(
            json.dumps({"status": "quarantined"}),
            encoding="utf-8",
        )
        transactions.append(transaction)
    _old(transactions[0], days=40)

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        config=RuntimeStorageGcConfig(
            transaction_compaction_bytes=1000,
            quarantined_transaction_cap_bytes=12,
        ),
        log_store=LogStore(),
    ).collect_garbage()

    assert not (transactions[0] / "model-blobs").exists()
    assert any(
        decision.path == transactions[0]
        and decision.action is RuntimeStorageGcAction.COMPACT
        and decision.reason == "quarantined transaction cap exceeded"
        for decision in result.decisions
    )


def test_gc_low_disk_compacts_recent_quarantine(tmp_path: Path) -> None:
    roots = _roots(tmp_path)
    transaction = roots.install_transactions_dir / "install-low-disk"
    payload = transaction / "runner-workspaces" / "workspace"
    payload.mkdir(parents=True)
    (payload / "main.py").write_text("print('x')\n", encoding="utf-8")
    (transaction / "quarantine.json").write_text(
        json.dumps(
            {
                "status": "quarantined",
                "retain_until": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        config=RuntimeStorageGcConfig(transaction_compaction_bytes=1000),
        log_store=LogStore(),
    ).collect_garbage(aggressive=True)

    assert transaction.exists()
    assert not (transaction / "runner-workspaces").exists()
    assert any(
        decision.path == transaction
        and decision.action is RuntimeStorageGcAction.COMPACT
        for decision in result.decisions
    )


def test_stale_ready_state_does_not_protect_invalid_model_view(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    view = _write_model_view(roots, "5" * 64)
    _old(view)
    missing_source = tmp_path / "models" / "missing.safetensors"
    ref = InstalledModelReference(
        requirement_id="local",
        comfyui_folder="checkpoints",
        filename="model.safetensors",
        verification_level=ModelVerificationLevel.SHA256_SIZE,
        asset_ownership=AssetOwnership.USER_LOCAL,
        sha256="sha256:" + ("a" * 64),
        size_bytes=10,
        source_path=str(missing_source),
        materialized_path=str(view / "checkpoints" / "model.safetensors"),
        materialized_file_verified=True,
    )
    state = _state("capsule-stale", model_references=[ref])

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[state],
        model_reference_validator=lambda refs: ["local model source missing"],
        config=RuntimeStorageGcConfig(orphan_model_view_retention_days=0),
        log_store=LogStore(),
    ).collect_garbage()

    assert state.status is InstallStatus.READY
    assert not view.exists()
    assert any(
        decision.path == view and decision.action is RuntimeStorageGcAction.DELETE
        for decision in result.decisions
    )


def test_unknown_non_view_materialization_is_indexed_but_skipped(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    moss = roots.model_materialized_dir / "moss-tts"
    moss.mkdir()
    (moss / "model.safetensors").write_bytes(b"unknown")
    _old(moss)

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        log_store=LogStore(),
    ).collect_garbage(aggressive=True)

    assert moss.exists()
    assert any(
        decision.path == moss
        and decision.action is RuntimeStorageGcAction.SKIP_UNCLASSIFIED
        for decision in result.decisions
    )


def test_rebuildable_non_view_materialization_can_be_cleaned(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    legacy = roots.model_materialized_dir / "checkpoints"
    legacy.mkdir()
    (legacy / ".noofy-materialization.json").write_text(
        json.dumps({"owner": "noofy", "rebuildable": True}),
        encoding="utf-8",
    )
    (legacy / "model.safetensors").write_bytes(b"cached")
    _old(legacy)

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[],
        config=RuntimeStorageGcConfig(orphan_model_view_retention_days=0),
        log_store=LogStore(),
    ).collect_garbage()

    assert not legacy.exists()
    assert any(
        decision.path == legacy
        and decision.action is RuntimeStorageGcAction.DELETE
        for decision in result.decisions
    )


def test_reference_index_tracks_cache_and_package_archive_metadata(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    (roots.custom_node_cache_dir / "custom-node-a").mkdir()
    (roots.custom_node_cache_dir / "custom-node-a" / "file.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    package_dir = roots.workflow_packages_store_dir / "publisher" / "package" / "0.1.0"
    package_dir.mkdir(parents=True)
    (package_dir / "source-archive.noofy").write_bytes(b"archive")

    index = RuntimeStorageGarbageCollector(
        roots=roots, install_states=[], log_store=LogStore()
    ).build_reference_index()

    kinds = {artifact.kind for artifact in index.artifacts}
    assert RuntimeStorageArtifactKind.CUSTOM_NODE_SOURCE_CACHE_ENTRY in kinds
    assert RuntimeStorageArtifactKind.PACKAGE_ARCHIVE in kinds


def test_gc_keeps_custom_node_source_cache_referenced_by_installed_workflow(
    tmp_path: Path,
) -> None:
    roots = _roots(tmp_path)
    referenced_cache_entry = roots.custom_node_cache_dir / "abc123"
    orphan_cache_entry = roots.custom_node_cache_dir / "orphan"
    (referenced_cache_entry / "source").mkdir(parents=True)
    (referenced_cache_entry / "source" / "node.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    (orphan_cache_entry / "source").mkdir(parents=True)
    (orphan_cache_entry / "source" / "node.py").write_text(
        "x = 'orphan' * 100\n", encoding="utf-8"
    )
    _write_workflow_with_cached_custom_node(
        roots,
        workflow_id="publisher__cached-workflow__0.1.0",
        capsule_fingerprint="capsule-cached",
        source_cache_ref="abc123/source",
    )

    result = RuntimeStorageGarbageCollector(
        roots=roots,
        install_states=[_state("capsule-cached")],
        config=RuntimeStorageGcConfig(custom_node_source_cache_cap_bytes=1),
        log_store=LogStore(),
    ).collect_garbage()

    assert referenced_cache_entry.exists()
    assert not orphan_cache_entry.exists()
    assert any(
        decision.path == referenced_cache_entry
        and decision.action is RuntimeStorageGcAction.SKIP_REFERENCED
        and decision.referenced_workflows == ("publisher__cached-workflow__0.1.0",)
        for decision in result.decisions
    )


def _write_workflow_with_cached_custom_node(
    roots: RuntimeStorageRoots,
    *,
    workflow_id: str,
    capsule_fingerprint: str,
    source_cache_ref: str,
) -> None:
    package_dir = (
        roots.workflow_packages_store_dir / "publisher" / "cached-workflow" / "0.1.0"
    )
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {
                    "id": workflow_id,
                    "name": "Cached Workflow",
                    "version": "0.1.0",
                },
                "identity": {
                    "publisher_id": "publisher",
                    "package_id": "cached-workflow",
                    "version": "0.1.0",
                    "trust_level": "quarantined_community",
                    "source": "test",
                },
                "engine": "comfyui",
                "comfyui_graph": {},
                "dashboard": {"version": "0.1.0", "sections": []},
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "capsule.lock.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "workflow": {
                    "publisher_id": "publisher",
                    "package_id": "cached-workflow",
                    "version": "0.1.0",
                    "trust_level": "quarantined_community",
                    "source": "test",
                },
                "engine": {
                    "type": "comfyui",
                    "comfyui_version": "test",
                    "core_source_hash": "sha256:" + ("c" * 64),
                },
                "runtime": {
                    "runtime_profile_id": "profile",
                    "runtime_profile_variant_id": "variant",
                    "runtime_profile_manifest_hash": "sha256:" + ("a" * 64),
                    "runtime_profile_catalog_version": "0.1.0",
                    "fingerprint_schema_version": "0.1.0",
                    "dependency_env_fingerprint": "sha256:" + ("d" * 64),
                    "runner_fingerprint": "sha256:" + ("r" * 64),
                    "capsule_fingerprint": capsule_fingerprint,
                    "os": "linux",
                    "architecture": "x64",
                    "python_version": "3.12",
                    "python_build_id": "cpython-3.12",
                    "gpu_backend": "cpu",
                    "dependency_lock_hash": "sha256:" + ("e" * 64),
                    "runner_workspace_hash": "sha256:" + ("f" * 64),
                },
                "custom_nodes": [
                    {
                        "package_id": "cached-node",
                        "source": "registry_metadata:cached-node",
                        "source_ref": "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                        "source_content_hash": "sha256:" + ("b" * 64),
                        "source_cache_ref": source_cache_ref,
                        "trust_level": "quarantined_community",
                        "node_types": ["CachedNode"],
                    }
                ],
                "dependencies": {
                    "lock_file": "lock",
                    "install_policy": "quarantined-community-v1",
                },
                "trust": {"level": "quarantined_community", "publisher": "publisher"},
            }
        ),
        encoding="utf-8",
    )
