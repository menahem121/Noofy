import json
from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.runtime.dependencies.custom_nodes import CustomNodeWorkspaceMaterializer
from app.runtime.dependencies.dependency_env import DependencyEnvironmentInstallRequest
from app.runtime.dependencies.dependency_lock import (
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyWheel,
    ResolverMetadata,
    with_computed_lock_hash,
)
from app.runtime.dependencies.dependency_lock_store import ResolvedDependencyLockStore
from app.runtime.dependencies.isolation import CapsuleLock, InstallStatus, SmokeTestStatus
from app.runtime.profiles import RuntimeProfileErrorCode, RuntimeProfileResolutionError, load_runtime_profile_catalog
from app.runtime.storage.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.storage.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore
from app.source_policy import SourcePolicy


class _FakeDependencyEnvInstaller:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[DependencyEnvironmentInstallRequest] = []

    def install(self, request: DependencyEnvironmentInstallRequest) -> None:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("dependency install failed")
        request.target_dir.mkdir(parents=True)
        (request.target_dir / "venv").mkdir()
        (request.target_dir / "install.marker").write_text("installed", encoding="utf-8")


def _capsule_lock(
    *,
    runner_fingerprint: str = "sha256:" + ("c" * 64),
) -> CapsuleLock:
    return CapsuleLock.model_validate(
        {
            "schema_version": "0.1.0",
            "workflow": {
                "publisher_id": "noofy",
                "package_id": "text_to_image_v0",
                "version": "0.1.0",
                "trust_level": "noofy_verified",
                "source": "bundled",
            },
            "engine": {
                "type": "comfyui",
                "comfyui_version": "milestone-1",
                "core_source_hash": "sha256:" + ("a" * 64),
            },
            "runtime": {
                "runtime_profile_id": "noofy-comfyui-v1-default",
                "runtime_profile_variant_id": "darwin-arm64-mps-dev",
                "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
                "runtime_profile_catalog_version": "0.1.0",
                "fingerprint_schema_version": "0.1.0",
                "dependency_env_fingerprint": "sha256:" + ("b" * 64),
                "runner_fingerprint": runner_fingerprint,
                "capsule_fingerprint": "sha256:" + ("d" * 64),
                "os": "darwin",
                "architecture": "arm64",
                "python_version": "3.11",
                "python_build_id": "cpython-3.11-noofy-dev",
                "gpu_backend": "mps",
                "dependency_lock_hash": "sha256:" + ("e" * 64),
                "runner_workspace_hash": "sha256:" + ("f" * 64),
            },
            "custom_nodes": [],
            "dependencies": {
                "lock_file": "core.lock",
                "install_policy": "core_only_no_community",
            },
            "models": [],
            "trust": {
                "level": "noofy_verified",
                "publisher": "Noofy",
            },
        }
    )


def _preparer(tmp_path: Path) -> RuntimeWorkspacePreparer:
    return RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=LogStore(),
    )


def _dependency_lock_for_capsule(capsule: CapsuleLock) -> ResolvedDependencyLock:
    return with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id=capsule.runtime.runtime_profile_id,
            runtime_profile_variant_id=capsule.runtime.runtime_profile_variant_id,
            runtime_profile_manifest_hash=capsule.runtime.runtime_profile_manifest_hash,
            install_policy_version=capsule.dependencies.install_policy,
            resolver=ResolverMetadata(name="uv", version="0.9.0"),
            wheels=[],
        )
    )


def _community_source_policy(package_source_type: str) -> SourcePolicy:
    return SourcePolicy(
        trust_level="quarantined_community",
        source_policy="explicit_opt_in_and_isolated_capsule_required",
        package_source_type=package_source_type,
        automatic_preparation_allowed=True,
        allowed_source_origins=["explicit-metadata", "registry-locked"],
        allowed_model_origins=["hashed-download", "huggingface.co", "user-local"],
        model_source_trust="hashed",
        community_preparation_opt_in_required=True,
        community_preparation_opted_in=True,
        trust_verification_status="not_required",
    )


def _capsule_with_dependency_lock() -> tuple[CapsuleLock, ResolvedDependencyLock]:
    base = _capsule_lock()
    lock = _dependency_lock_for_capsule(base)
    data = base.model_dump(mode="json")
    data["runtime"]["dependency_lock_hash"] = lock.lock_hash
    return CapsuleLock.model_validate(data), lock


def _profile_checked_preparer(tmp_path: Path) -> RuntimeWorkspacePreparer:
    return RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        runtime_profile_catalog=load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json")),
        log_store=LogStore(),
    )


def _source_preparer(tmp_path: Path) -> RuntimeWorkspacePreparer:
    source_dir = tmp_path / "ComfyUI-source"
    source_dir.mkdir()
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (source_dir / "folder_paths.py").write_text("# fake folder paths\n", encoding="utf-8")
    (source_dir / "comfy").mkdir()
    (source_dir / "comfy" / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()
    model_view_dir = tmp_path / "model-view"
    model_view_dir.mkdir()
    (model_view_dir / "checkpoints").mkdir()
    return RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=source_dir,
        model_view_dir=model_view_dir,
        log_store=LogStore(),
    )


def test_prepare_creates_staged_dependency_and_runner_manifests(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()

    prepared = preparer.prepare(capsule)

    assert prepared.dependency_env_manifest.status is InstallStatus.CHECKING_COMPATIBILITY
    assert prepared.runner_workspace_manifest.status is InstallStatus.CHECKING_COMPATIBILITY
    assert prepared.dependency_env_path.name == f"dep-env-{capsule.runtime.dependency_env_fingerprint.removeprefix('sha256:')}"
    assert prepared.runner_workspace_path.name == f"runner-workspace-{capsule.runtime.runner_fingerprint.removeprefix('sha256:')}"
    assert (prepared.dependency_env_path / "manifest.json").exists()
    assert (prepared.runner_workspace_path / "manifest.json").exists()
    assert prepared.install_transaction is not None
    assert prepared.dependency_env_path.is_relative_to(prepared.install_transaction.root_dir)
    assert prepared.runner_workspace_path.is_relative_to(prepared.install_transaction.root_dir)
    assert not preparer.dependency_env_store.manifest_path(prepared.dependency_env_manifest.fingerprint).exists()
    assert not preparer.runner_workspace_store.manifest_path(prepared.runner_workspace_manifest.fingerprint).exists()


def test_prepare_reads_current_comfyui_source_from_provider(tmp_path: Path) -> None:
    first_source = tmp_path / "first-source"
    first_source.mkdir()
    (first_source / "main.py").write_text("first\n", encoding="utf-8")
    second_source = tmp_path / "second-source"
    second_source.mkdir()
    (second_source / "main.py").write_text("second\n", encoding="utf-8")
    active_source = [first_source]
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(
            tmp_path / "runner-workspaces"
        ),
        comfyui_source_dir_provider=lambda: active_source[0],
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    first = preparer.prepare(_capsule_lock(runner_fingerprint="sha256:" + ("1" * 64)))
    active_source[0] = second_source
    second = preparer.prepare(_capsule_lock(runner_fingerprint="sha256:" + ("2" * 64)))

    assert (first.runner_workspace_path / "main.py").read_text(encoding="utf-8") == "first\n"
    assert (second.runner_workspace_path / "main.py").read_text(encoding="utf-8") == "second\n"


def test_prepare_installs_dependency_env_in_transaction_before_promotion(tmp_path: Path) -> None:
    capsule, lock = _capsule_with_dependency_lock()
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_locks={lock.lock_hash: lock},
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    assert len(installer.requests) == 1
    assert prepared.install_transaction is not None
    assert installer.requests[0].target_dir == prepared.dependency_env_path
    assert installer.requests[0].target_dir.is_relative_to(prepared.install_transaction.root_dir)
    assert (prepared.dependency_env_path / "install.marker").read_text(encoding="utf-8") == "installed"
    assert (prepared.dependency_env_path / "manifest.json").exists()
    assert prepared.install_transaction.root_dir.exists()
    assert not preparer.dependency_env_store.manifest_path(prepared.dependency_env_manifest.fingerprint).exists()


def test_prepare_reuses_ready_dependency_env_without_reinstalling(tmp_path: Path) -> None:
    capsule, lock = _capsule_with_dependency_lock()
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_locks={lock.lock_hash: lock},
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )
    ready = preparer.mark_ready(
        preparer.prepare(capsule),
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    second = preparer.prepare(capsule)

    assert len(installer.requests) == 1
    assert second.dependency_env_path == ready.dependency_env_path
    assert second.dependency_env_manifest.status is InstallStatus.READY


def test_duplicate_staged_preparations_promote_one_ready_dependency_env(tmp_path: Path) -> None:
    capsule, lock = _capsule_with_dependency_lock()
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_locks={lock.lock_hash: lock},
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    first = preparer.prepare(capsule)
    second = preparer.prepare(capsule)

    ready_first = preparer.mark_ready(
        first,
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )
    ready_second = preparer.mark_ready(
        second,
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    assert ready_first.dependency_env_path == ready_second.dependency_env_path
    assert ready_first.dependency_env_manifest.status is InstallStatus.READY
    assert ready_second.dependency_env_manifest.status is InstallStatus.READY
    assert len(list((tmp_path / "envs").glob("dep-env-*"))) == 1
    assert len(list((tmp_path / "runner-workspaces").glob("runner-workspace-*"))) == 1
    assert list((tmp_path / "transactions").glob("install-*")) == []


def test_prepare_loads_resolved_dependency_lock_from_store(tmp_path: Path) -> None:
    capsule, lock = _capsule_with_dependency_lock()
    lock_store = ResolvedDependencyLockStore(tmp_path / "dependency-locks")
    lock_store.write(lock)
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_store=lock_store,
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    assert installer.requests[0].lock == lock
    assert (prepared.dependency_env_path / "install.marker").exists()


def test_prepare_uses_runtime_matching_dependency_lock_for_shared_hash(tmp_path: Path) -> None:
    base = _capsule_lock()
    stale_lock = ResolvedDependencyLock(
        lock_hash=base.runtime.dependency_lock_hash,
        runtime_profile_id=base.runtime.runtime_profile_id,
        runtime_profile_variant_id="linux-x64-cuda130",
        runtime_profile_manifest_hash=base.runtime.runtime_profile_manifest_hash,
        install_policy_version=base.dependencies.install_policy,
        resolver=ResolverMetadata(name="noofy-managed-core", version="0.1.0"),
        wheels=[],
    )
    current_lock = ResolvedDependencyLock(
        lock_hash=base.runtime.dependency_lock_hash,
        runtime_profile_id=base.runtime.runtime_profile_id,
        runtime_profile_variant_id=base.runtime.runtime_profile_variant_id,
        runtime_profile_manifest_hash=base.runtime.runtime_profile_manifest_hash,
        install_policy_version=base.dependencies.install_policy,
        resolver=ResolverMetadata(name="noofy-managed-core", version="0.1.0"),
        wheels=[],
    )
    lock_store = ResolvedDependencyLockStore(tmp_path / "dependency-locks")
    lock_store.write(stale_lock)
    lock_store.write(current_lock)
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_store=lock_store,
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    preparer.prepare(base)

    assert installer.requests[0].lock == current_lock


def test_prepare_uses_generated_dependency_lock_identity_when_capsule_hash_is_stale(tmp_path: Path) -> None:
    base = _capsule_lock()
    lock = _dependency_lock_for_capsule(base)
    source_files_dir = tmp_path / "source-files"
    (source_files_dir / "custom_nodes" / "node-a").mkdir(parents=True)
    (source_files_dir / "custom_nodes" / "node-a" / "requirements.txt").write_text("demo>=1\n", encoding="utf-8")

    class FakeResolver:
        def resolve(self, request) -> ResolvedDependencyLock:
            return lock

    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_resolver=FakeResolver(),
        custom_node_source_files_dir=source_files_dir,
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(base)

    assert prepared.dependency_env_manifest.dependency_lock_hash == lock.lock_hash
    assert prepared.dependency_env_manifest.fingerprint != base.runtime.dependency_env_fingerprint
    assert prepared.runner_workspace_manifest.dependency_env_fingerprint == prepared.dependency_env_manifest.fingerprint
    assert installer.requests[0].lock == lock


def test_prepare_keeps_capsule_profile_abi_for_dependency_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, lock = _capsule_with_dependency_lock()
    source_files_dir = tmp_path / "source-files"
    (source_files_dir / "custom_nodes" / "node-a").mkdir(parents=True)
    (source_files_dir / "custom_nodes" / "node-a" / "requirements.txt").write_text(
        "demo>=1\n",
        encoding="utf-8",
    )

    class FakeResolver:
        def __init__(self) -> None:
            self.python_version: str | None = None

        def resolve(self, request) -> ResolvedDependencyLock:
            self.python_version = request.python_version
            return lock

    resolver = FakeResolver()
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_resolver=resolver,
        custom_node_source_files_dir=source_files_dir,
        dependency_transactions_dir=tmp_path / "transactions",
        dependency_python_executable_provider=lambda: "/opt/noofy/runtime/python",
        log_store=LogStore(),
    )
    monkeypatch.setattr(
        "app.runtime.storage.workspace_preparer.detect_python_major_minor",
        lambda python_executable: "3.11",
    )

    prepared = preparer.prepare(base)

    assert resolver.python_version == "3.11"
    assert prepared.dependency_env_manifest.python_version == "3.11"
    assert prepared.dependency_env_manifest.python_build_id == "cpython-3.11-noofy-dev"
    assert prepared.dependency_env_manifest.fingerprint == base.runtime.dependency_env_fingerprint
    assert installer.requests[0].python_version == "3.11"
    assert installer.requests[0].python_executable == "/opt/noofy/runtime/python"


def test_prepare_rejects_dependency_python_executable_with_wrong_profile_abi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base, lock = _capsule_with_dependency_lock()
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_locks={lock.lock_hash: lock},
        dependency_transactions_dir=tmp_path / "transactions",
        dependency_python_executable_provider=lambda: "/opt/noofy/runtime/python",
        log_store=LogStore(),
    )
    monkeypatch.setattr(
        "app.runtime.storage.workspace_preparer.detect_python_major_minor",
        lambda python_executable: "3.14",
    )

    with pytest.raises(RuntimeError, match="Dependency environment Python ABI"):
        preparer.prepare(base)

    assert installer.requests == []


def test_prepare_resolves_dependency_manifest_with_app_workflow_id(tmp_path: Path) -> None:
    base = _capsule_lock()
    lock = _dependency_lock_for_capsule(base)
    app_workflow_id = "unknown__text_to_image_v0__0.1.0"
    source_files_dir = tmp_path / "source-files"
    (source_files_dir / "custom_nodes" / "node-a").mkdir(parents=True)
    (source_files_dir / "custom_nodes" / "node-a" / "requirements.txt").write_text(
        "demo>=1\n",
        encoding="utf-8",
    )

    class FakeResolver:
        def __init__(self) -> None:
            self.workflow_id: str | None = None
            self.source_dirs: list[Path] = []

        def resolve(self, request) -> ResolvedDependencyLock:
            self.workflow_id = request.workflow_id
            self.source_dirs = request.source_dirs
            return lock

    resolver = FakeResolver()
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_resolver=resolver,
        custom_node_source_files_dir_resolver=lambda workflow_id: (
            source_files_dir if workflow_id == app_workflow_id else None
        ),
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(base, workflow_id=app_workflow_id)

    assert resolver.workflow_id == app_workflow_id
    assert resolver.source_dirs == [source_files_dir / "custom_nodes" / "node-a"]
    assert prepared.dependency_env_manifest.dependency_lock_hash == lock.lock_hash
    assert installer.requests[0].lock == lock


def test_prepare_merges_core_lock_with_custom_node_dependency_lock(tmp_path: Path) -> None:
    base = _capsule_lock()
    core_lock = ResolvedDependencyLock(
        lock_hash=base.runtime.dependency_lock_hash,
        runtime_profile_id=base.runtime.runtime_profile_id,
        runtime_profile_variant_id=base.runtime.runtime_profile_variant_id,
        runtime_profile_manifest_hash=base.runtime.runtime_profile_manifest_hash,
        install_policy_version=base.dependencies.install_policy,
        resolver=ResolverMetadata(name="uv", version="0.9.0"),
        wheels=[],
    )
    custom_lock = ResolvedDependencyLock(
        runtime_profile_id=base.runtime.runtime_profile_id,
        runtime_profile_variant_id=base.runtime.runtime_profile_variant_id,
        runtime_profile_manifest_hash=base.runtime.runtime_profile_manifest_hash,
        install_policy_version=base.dependencies.install_policy,
        resolver=ResolverMetadata(name="uv", version="0.9.0"),
        wheels=[
            ResolvedDependencyWheel(
                name="Demo_Package",
                version="1.0.0",
                wheel_filename="demo-package-1.0.0-py3-none-any.whl",
                sha256="sha256:" + ("a" * 64),
                source_kind=DependencySourceKind.APPROVED_CACHE,
                approved_cache_ref="demo-package-1.0.0-py3-none-any.whl",
                platform_tags=["py3-none-any"],
                relationship=DependencyRelationship.DIRECT,
                requested_by=["node-a"],
                resolver_name="uv",
                resolver_version="0.9.0",
            )
        ],
    )
    source_files_dir = tmp_path / "source-files"
    (source_files_dir / "custom_nodes" / "node-a").mkdir(parents=True)
    (source_files_dir / "custom_nodes" / "node-a" / "requirements.txt").write_text("demo-package==1.0.0\n", encoding="utf-8")

    class FakeResolver:
        def resolve(self, request) -> ResolvedDependencyLock:
            return with_computed_lock_hash(custom_lock.model_copy(update={"source_policy": request.source_policy}))

    installer = _FakeDependencyEnvInstaller()
    lock_store = ResolvedDependencyLockStore(tmp_path / "dependency-locks")
    lock_store.write(core_lock)
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_store=lock_store,
        dependency_lock_resolver=FakeResolver(),
        custom_node_source_files_dir=source_files_dir,
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(base)

    installed_lock = installer.requests[0].lock
    assert installed_lock.lock_hash == prepared.dependency_env_manifest.dependency_lock_hash
    assert installed_lock.lock_hash != base.runtime.dependency_lock_hash
    assert [wheel.name for wheel in installed_lock.wheels] == ["demo-package"]
    assert lock_store.exists(installed_lock.lock_hash)


def test_prepare_ignores_stale_core_lock_source_policy_when_merging_custom_node_lock(
    tmp_path: Path,
) -> None:
    policy = _community_source_policy("noofy_archive_import")
    data = _capsule_lock().model_dump(mode="json")
    data["workflow"]["trust_level"] = "quarantined_community"
    data["workflow"]["source"] = "noofy_archive_import"
    data["dependencies"]["install_policy"] = "isolated-community-index-build-v2"
    data["trust"] = {"level": "quarantined_community", "publisher": "Noofy"}
    data["source_policy"] = policy.model_dump(mode="json")
    capsule = CapsuleLock.model_validate(data)
    stale_core_lock = ResolvedDependencyLock(
        lock_hash=capsule.runtime.dependency_lock_hash,
        runtime_profile_id=capsule.runtime.runtime_profile_id,
        runtime_profile_variant_id=capsule.runtime.runtime_profile_variant_id,
        runtime_profile_manifest_hash=capsule.runtime.runtime_profile_manifest_hash,
        install_policy_version=capsule.dependencies.install_policy,
        source_policy=_community_source_policy("unknown"),
        resolver=ResolverMetadata(name="noofy-managed-core", version="0.1.0"),
        wheels=[],
    )
    custom_lock = ResolvedDependencyLock(
        runtime_profile_id=capsule.runtime.runtime_profile_id,
        runtime_profile_variant_id=capsule.runtime.runtime_profile_variant_id,
        runtime_profile_manifest_hash=capsule.runtime.runtime_profile_manifest_hash,
        install_policy_version=capsule.dependencies.install_policy,
        resolver=ResolverMetadata(name="uv", version="0.9.0"),
        wheels=[
            ResolvedDependencyWheel(
                name="Demo_Package",
                version="1.0.0",
                wheel_filename="demo-package-1.0.0-py3-none-any.whl",
                sha256="sha256:" + ("a" * 64),
                source_kind=DependencySourceKind.APPROVED_CACHE,
                approved_cache_ref="demo-package-1.0.0-py3-none-any.whl",
                platform_tags=["py3-none-any"],
                relationship=DependencyRelationship.DIRECT,
                requested_by=["node-a"],
                resolver_name="uv",
                resolver_version="0.9.0",
            )
        ],
    )
    source_files_dir = tmp_path / "source-files"
    (source_files_dir / "custom_nodes" / "node-a").mkdir(parents=True)
    (source_files_dir / "custom_nodes" / "node-a" / "requirements.txt").write_text(
        "demo-package==1.0.0\n",
        encoding="utf-8",
    )

    class FakeResolver:
        def resolve(self, request) -> ResolvedDependencyLock:
            return with_computed_lock_hash(
                custom_lock.model_copy(update={"source_policy": request.source_policy})
            )

    installer = _FakeDependencyEnvInstaller()
    lock_store = ResolvedDependencyLockStore(tmp_path / "dependency-locks")
    lock_store.write(stale_core_lock)
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_store=lock_store,
        dependency_lock_resolver=FakeResolver(),
        custom_node_source_files_dir=source_files_dir,
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    installed_lock = installer.requests[0].lock
    assert installed_lock.source_policy == policy
    assert installed_lock.lock_hash == prepared.dependency_env_manifest.dependency_lock_hash
    assert [wheel.name for wheel in installed_lock.wheels] == ["demo-package"]


def test_prepare_generates_candidate_dependency_lock_from_cached_non_bundled_source(tmp_path: Path) -> None:
    base = _capsule_lock()
    capsule = _with_cached_custom_node(base, source_cache_ref="abc123/source")
    custom_lock = ResolvedDependencyLock(
        runtime_profile_id=capsule.runtime.runtime_profile_id,
        runtime_profile_variant_id=capsule.runtime.runtime_profile_variant_id,
        runtime_profile_manifest_hash=capsule.runtime.runtime_profile_manifest_hash,
        install_policy_version=capsule.dependencies.install_policy,
        resolver=ResolverMetadata(name="uv", version="0.9.0"),
        wheels=[
            ResolvedDependencyWheel(
                name="Demo_Package",
                version="1.0.0",
                wheel_filename="demo-package-1.0.0-py3-none-any.whl",
                sha256="sha256:" + ("a" * 64),
                source_kind=DependencySourceKind.APPROVED_CACHE,
                approved_cache_ref="demo-package-1.0.0-py3-none-any.whl",
                platform_tags=["py3-none-any"],
                relationship=DependencyRelationship.DIRECT,
                requested_by=["cached-node"],
                resolver_name="uv",
                resolver_version="0.9.0",
            )
        ],
    )
    cached_source_dir = tmp_path / "source-cache" / "abc123" / "source"
    cached_source_dir.mkdir(parents=True)
    (cached_source_dir / "requirements.txt").write_text("demo-package==1.0.0\n", encoding="utf-8")
    _write_source_cache_manifest(cached_source_dir)

    class FakeResolver:
        def __init__(self) -> None:
            self.source_dirs: list[Path] = []

        def resolve(self, request) -> ResolvedDependencyLock:
            self.source_dirs = request.source_dirs
            return with_computed_lock_hash(custom_lock.model_copy(update={"source_policy": request.source_policy}))

    resolver = FakeResolver()
    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_lock_resolver=resolver,
        custom_node_source_cache_dir=tmp_path / "source-cache",
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    assert resolver.source_dirs == [cached_source_dir]
    installed_lock = installer.requests[0].lock
    assert prepared.dependency_env_manifest.dependency_lock_hash == installed_lock.lock_hash
    assert installed_lock.lock_hash != base.runtime.dependency_lock_hash
    assert [wheel.name for wheel in installed_lock.wheels] == ["demo-package"]


def test_prepare_materializes_cached_non_bundled_source_into_runner_workspace(tmp_path: Path) -> None:
    trusted_core = tmp_path / "trusted-comfyui"
    trusted_custom_nodes = trusted_core / "custom_nodes"
    trusted_custom_nodes.mkdir(parents=True)
    trusted_file = trusted_custom_nodes / "trusted.py"
    trusted_file.write_text("trusted\n", encoding="utf-8")
    (trusted_core / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")

    source_files_dir = tmp_path / "source-files"
    source_files_dir.mkdir()
    graph = {"1": {"class_type": "CachedNode", "inputs": {}}}
    (source_files_dir / "comfyui_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    cached_source_dir = tmp_path / "source-cache" / "abc123" / "source"
    cached_source_dir.mkdir(parents=True)
    (cached_source_dir / "node.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    _write_source_cache_manifest(cached_source_dir)

    capsule = _with_cached_custom_node(_capsule_lock(), source_cache_ref="abc123/source")
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=trusted_core,
        custom_node_materializer=_cached_node_materializer(),
        custom_node_source_files_dir=source_files_dir,
        custom_node_source_cache_dir=tmp_path / "source-cache",
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    assert (prepared.runner_workspace_path / "custom_nodes" / "cached-node" / "node.py").exists()
    assert trusted_file.read_text(encoding="utf-8") == "trusted\n"


def test_prepare_materializes_cached_non_bundled_source_without_source_files_dir(
    tmp_path: Path,
) -> None:
    trusted_core = tmp_path / "trusted-comfyui"
    trusted_custom_nodes = trusted_core / "custom_nodes"
    trusted_custom_nodes.mkdir(parents=True)
    trusted_file = trusted_custom_nodes / "trusted.py"
    trusted_file.write_text("trusted\n", encoding="utf-8")
    (trusted_core / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")

    cached_source_dir = tmp_path / "source-cache" / "abc123" / "source"
    cached_source_dir.mkdir(parents=True)
    (cached_source_dir / "node.py").write_text(
        "NODE_CLASS_MAPPINGS = {}\n",
        encoding="utf-8",
    )
    _write_source_cache_manifest(cached_source_dir)

    capsule = _with_cached_custom_node(_capsule_lock(), source_cache_ref="abc123/source")
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=trusted_core,
        custom_node_materializer=_cached_node_materializer(),
        custom_node_source_cache_dir=tmp_path / "source-cache",
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    assert (prepared.runner_workspace_path / "custom_nodes" / "cached-node" / "node.py").exists()
    assert trusted_file.read_text(encoding="utf-8") == "trusted\n"


def test_cached_non_bundled_source_requires_pinned_source_facts(tmp_path: Path) -> None:
    source_files_dir = tmp_path / "source-files"
    source_files_dir.mkdir()
    (source_files_dir / "comfyui_graph.json").write_text(
        json.dumps({"1": {"class_type": "CachedNode", "inputs": {}}}),
        encoding="utf-8",
    )
    capsule = _with_cached_custom_node(
        _capsule_lock(),
        source_cache_ref="abc123/source",
        source_ref=None,
    )
    cached_source_dir = tmp_path / "source-cache" / "abc123" / "source"
    cached_source_dir.mkdir(parents=True)
    _write_source_cache_manifest(cached_source_dir)
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        custom_node_materializer=_cached_node_materializer(),
        custom_node_source_files_dir=source_files_dir,
        custom_node_source_cache_dir=tmp_path / "source-cache",
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    with pytest.raises(RuntimeError, match="missing pinned source facts"):
        preparer.prepare(capsule)


def test_failed_dependency_env_install_leaves_no_ready_manifest(tmp_path: Path) -> None:
    capsule, lock = _capsule_with_dependency_lock()
    installer = _FakeDependencyEnvInstaller(fail=True)
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=installer,
        dependency_locks={lock.lock_hash: lock},
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    with pytest.raises(RuntimeError, match="dependency install failed"):
        preparer.prepare(capsule)

    assert len(installer.requests) == 1
    assert list((tmp_path / "envs").glob("*")) == []
    transactions_dir = tmp_path / "transactions"
    quarantines = list(transactions_dir.glob("install-*/quarantine.json"))
    assert len(quarantines) == 1
    marker = json.loads(quarantines[0].read_text(encoding="utf-8"))
    assert marker["reason"] == "dependency install failed"


def test_prepare_requires_matching_resolved_dependency_lock_for_env_install(tmp_path: Path) -> None:
    capsule, _ = _capsule_with_dependency_lock()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        dependency_env_installer=_FakeDependencyEnvInstaller(),
        dependency_locks={},
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    with pytest.raises(RuntimeError, match="Resolved dependency lock is not available"):
        preparer.prepare(capsule)

    assert list((tmp_path / "envs").glob("*")) == []


def test_prepare_reuses_existing_ready_manifests(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()
    first = preparer.mark_ready(
        preparer.prepare(capsule),
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    second = preparer.prepare(capsule)

    assert second.dependency_env_path == first.dependency_env_path
    assert second.runner_workspace_path == first.runner_workspace_path
    assert second.dependency_env_manifest.status is InstallStatus.READY
    assert second.runner_workspace_manifest.status is InstallStatus.READY
    assert second.runner_workspace_manifest.smoke_test_status is SmokeTestStatus.PASSED


def test_mark_ready_promotes_staged_manifests_after_smoke_passes(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()
    staged_dependency = preparer._dependency_env_manifest(capsule).model_copy(
        update={"status": InstallStatus.PREPARING}
    )
    staged_runner = preparer._runner_workspace_manifest(capsule).model_copy(
        update={"status": InstallStatus.PREPARING}
    )
    preparer.dependency_env_store.save_staged(staged_dependency)
    preparer.runner_workspace_store.save_staged(staged_runner)

    prepared = preparer.prepare(capsule)
    ready = preparer.mark_ready(
        prepared,
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    assert ready.dependency_env_manifest.status is InstallStatus.READY
    assert ready.runner_workspace_manifest.status is InstallStatus.READY
    assert ready.runner_workspace_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert preparer.dependency_env_store.read(prepared.dependency_env_manifest.fingerprint).status is InstallStatus.READY
    assert preparer.runner_workspace_store.read(prepared.runner_workspace_manifest.fingerprint).status is InstallStatus.READY
    assert preparer.runner_workspace_store.read(prepared.runner_workspace_manifest.fingerprint).smoke_test_status is SmokeTestStatus.PASSED


def test_different_runner_workspaces_can_share_ready_dependency_env(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    first_capsule = _capsule_lock(runner_fingerprint="sha256:" + ("c" * 64))
    second_capsule = _capsule_lock(runner_fingerprint="sha256:" + ("d" * 64))
    first = preparer.mark_ready(
        preparer.prepare(first_capsule),
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=first_capsule.workflow.package_id,
    )

    second = preparer.prepare(second_capsule)

    assert second.dependency_env_path == first.dependency_env_path
    assert second.dependency_env_manifest.status is InstallStatus.READY
    assert second.dependency_env_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert second.runner_workspace_path != first.runner_workspace_path
    assert second.runner_workspace_manifest.status is InstallStatus.CHECKING_COMPATIBILITY


def test_mark_ready_returns_existing_ready_manifest_without_mutating_it(tmp_path: Path) -> None:
    preparer = _preparer(tmp_path)
    capsule = _capsule_lock()
    ready = preparer.mark_ready(
        preparer.prepare(capsule),
        smoke_test_status=SmokeTestStatus.PASSED,
        workflow_id=capsule.workflow.package_id,
    )

    second = preparer.mark_ready(
        ready,
        smoke_test_status=SmokeTestStatus.NOT_RUN,
        workflow_id=capsule.workflow.package_id,
    )

    assert second.dependency_env_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert second.runner_workspace_manifest.smoke_test_status is SmokeTestStatus.PASSED
    assert preparer.dependency_env_store.read(capsule.runtime.dependency_env_fingerprint).smoke_test_status is SmokeTestStatus.PASSED
    assert preparer.runner_workspace_store.read(capsule.runtime.runner_fingerprint).smoke_test_status is SmokeTestStatus.PASSED


def test_prepare_materializes_runnable_runner_workspace_source_view(tmp_path: Path) -> None:
    preparer = _source_preparer(tmp_path)
    capsule = _capsule_lock()

    prepared = preparer.prepare(capsule)

    assert (prepared.runner_workspace_path / "main.py").exists()
    assert (prepared.runner_workspace_path / "folder_paths.py").exists()
    assert (prepared.runner_workspace_path / "comfy").exists()
    assert (prepared.runner_workspace_path / "custom_nodes").is_dir()
    assert list((prepared.runner_workspace_path / "custom_nodes").iterdir()) == []
    assert (prepared.runner_workspace_path / "models" / "checkpoints").exists()
    assert (prepared.runner_workspace_path / "input").is_dir()
    assert (prepared.runner_workspace_path / "output").is_dir()
    assert (prepared.runner_workspace_path / "temp").is_dir()
    assert (prepared.runner_workspace_path / "user").is_dir()


def test_prepare_refuses_to_reuse_ready_workspace_with_missing_source_view(tmp_path: Path) -> None:
    preparer = _source_preparer(tmp_path)
    capsule = _capsule_lock()
    manifest = preparer._runner_workspace_manifest(capsule)
    preparer.dependency_env_store.write_new(preparer._dependency_env_manifest(capsule))
    preparer.runner_workspace_store.write_new(manifest)

    with pytest.raises(RuntimeError, match="Ready runner workspace is missing materialized entries"):
        preparer.prepare(capsule)


def test_prepare_resolves_runtime_profile_before_staging_artifacts(tmp_path: Path) -> None:
    preparer = _profile_checked_preparer(tmp_path)
    capsule = _capsule_with_catalog_profile()

    prepared = preparer.prepare(capsule)

    assert prepared.dependency_env_manifest.runtime_profile_id == "noofy-comfyui-v1-default"
    assert prepared.runner_workspace_manifest.runtime_profile_variant_id == "darwin-arm64-mps"


def test_prepare_fails_missing_runtime_profile_before_staging_artifacts(tmp_path: Path) -> None:
    preparer = _profile_checked_preparer(tmp_path)
    data = _capsule_with_catalog_profile().model_dump(mode="json")
    data["runtime"]["runtime_profile_id"] = "missing-profile"
    capsule = CapsuleLock.model_validate(data)

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        preparer.prepare(capsule)

    assert exc.value.code is RuntimeProfileErrorCode.MISSING_RUNTIME_PROFILE
    assert list((tmp_path / "envs").glob("*")) == []
    assert list((tmp_path / "runner-workspaces").glob("*")) == []


def test_prepare_fails_profile_hash_mismatch_before_staging_artifacts(tmp_path: Path) -> None:
    preparer = _profile_checked_preparer(tmp_path)
    data = _capsule_with_catalog_profile().model_dump(mode="json")
    data["runtime"]["runtime_profile_manifest_hash"] = "sha256:" + ("8" * 64)
    capsule = CapsuleLock.model_validate(data)

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        preparer.prepare(capsule)

    assert exc.value.code is RuntimeProfileErrorCode.PROFILE_MANIFEST_HASH_MISMATCH
    assert list((tmp_path / "envs").glob("*")) == []
    assert list((tmp_path / "runner-workspaces").glob("*")) == []


def _capsule_with_catalog_profile() -> CapsuleLock:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    profile = catalog.profiles[0]
    variant = profile.variants[0]
    data = _capsule_lock().model_dump(mode="json")
    data["runtime"].update(
        {
            "runtime_profile_id": profile.runtime_profile_id,
            "runtime_profile_variant_id": variant.runtime_profile_variant_id,
            "runtime_profile_manifest_hash": profile.runtime_profile_manifest_hash,
            "runtime_profile_catalog_version": catalog.schema_version,
            "os": variant.os,
            "architecture": variant.architecture,
            "python_version": variant.python_version,
            "python_build_id": variant.python_build_id,
            "gpu_backend": variant.gpu_backend_profile,
            "dependency_lock_hash": variant.core_dependency_lock_hash,
        }
    )
    return CapsuleLock.model_validate(data)


def _with_cached_custom_node(
    capsule: CapsuleLock,
    *,
    source_cache_ref: str,
    source_ref: str | None = "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
    source_content_hash: str | None = "sha256:" + ("1" * 64),
) -> CapsuleLock:
    data = capsule.model_dump(mode="json")
    data["workflow"]["trust_level"] = "quarantined_community"
    data["dependencies"]["install_policy"] = "quarantined-community-v1"
    data["trust"] = {"level": "quarantined_community", "publisher": "Noofy"}
    data["custom_nodes"] = [
        {
            "package_id": "cached-node",
            "source": "registry_metadata:cached-node",
            "source_ref": source_ref,
            "source_content_hash": source_content_hash,
            "source_cache_ref": source_cache_ref,
            "trust_level": "quarantined_community",
            "node_types": ["CachedNode"],
        }
    ]
    return CapsuleLock.model_validate(data)


def _cached_node_materializer() -> CustomNodeWorkspaceMaterializer:
    return CustomNodeWorkspaceMaterializer()


def _write_source_cache_manifest(
    source_dir: Path,
    *,
    source_cache_ref: str = "abc123/source",
    source_ref: str = "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
    source_content_hash: str = "sha256:" + ("1" * 64),
) -> None:
    (source_dir.parent / "noofy-custom-node-source-cache-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "source_kind": "git_zip_archive",
                "source_url": "https://example.test/cached-node/archive/7b3f5d0.zip",
                "source_ref": source_ref,
                "source_content_hash": source_content_hash,
                "source_cache_ref": source_cache_ref,
            }
        ),
        encoding="utf-8",
    )
