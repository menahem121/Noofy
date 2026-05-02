"""Prepare runtime-store manifests and workspaces for verified capsules.

This Phase 4 slice stages dependency-env and runner-workspace manifest records
for the core verified path. When configured with the bundled ComfyUI source
path it also materializes a runnable source view for the runner workspace
without installing community custom nodes. Staged manifests are promoted to
immutable ready manifests only after the installer has completed its smoke
check.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.engine.diagnostics import LogStore
from app.runtime.dependency_env import (
    DependencyEnvironmentInstallRequest,
    DependencyEnvironmentInstaller,
)
from app.runtime.dependency_lock import (
    ResolvedDependencyLock,
    core_dependency_lock_from_capsule,
    dependency_env_fingerprint_for_resolved_lock,
    merge_resolved_dependency_locks,
    resolved_dependency_lock_hash,
)
from app.runtime.dependency_lock_store import ResolvedDependencyLockStore
from app.runtime.dependency_resolver import (
    DependencyResolutionRequest,
    UvDependencyLockResolver,
    custom_node_dependency_source_dirs,
)
from app.runtime.fingerprints import runner_workspace_fingerprint, sha256_fingerprint
from app.runtime.isolation import (
    CapsuleLock,
    DependencyEnvManifest,
    InstallStatus,
    RunnerWorkspaceManifest,
    SmokeTestStatus,
)
from app.runtime.profiles import (
    RuntimeProfileCatalog,
    RuntimeProfileErrorCode,
    RuntimeProfileResolutionError,
    RuntimeProfileSelection,
    resolve_runtime_profile,
)
from app.runtime.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)

RUNTIME_MANIFEST_SCHEMA_VERSION = "0.1.0"


@dataclass(frozen=True)
class PreparedRuntimeWorkspace:
    dependency_env_manifest: DependencyEnvManifest
    runner_workspace_manifest: RunnerWorkspaceManifest
    dependency_env_path: Path
    runner_workspace_path: Path


class RuntimeWorkspacePreparer:
    def __init__(
        self,
        *,
        dependency_env_store: DependencyEnvManifestStore,
        runner_workspace_store: RunnerWorkspaceManifestStore,
        comfyui_source_dir: Path | None = None,
        model_view_dir: Path | None = None,
        runtime_profile_catalog: RuntimeProfileCatalog | None = None,
        dependency_env_installer: DependencyEnvironmentInstaller | None = None,
        dependency_locks: Mapping[str, ResolvedDependencyLock] | None = None,
        dependency_lock_store: ResolvedDependencyLockStore | None = None,
        dependency_lock_resolver: UvDependencyLockResolver | None = None,
        custom_node_source_files_dir: Path | None = None,
        custom_node_source_files_dir_resolver: Callable[[str], Path | None] | None = None,
        dependency_transactions_dir: Path | None = None,
        log_store: LogStore | None = None,
    ) -> None:
        self.dependency_env_store = dependency_env_store
        self.runner_workspace_store = runner_workspace_store
        self.comfyui_source_dir = comfyui_source_dir
        self.model_view_dir = model_view_dir
        self.runtime_profile_catalog = runtime_profile_catalog
        self.dependency_env_installer = dependency_env_installer
        self.dependency_locks = dict(dependency_locks or {})
        self.dependency_lock_store = dependency_lock_store
        self.dependency_lock_resolver = dependency_lock_resolver
        self.custom_node_source_files_dir = custom_node_source_files_dir
        self.custom_node_source_files_dir_resolver = custom_node_source_files_dir_resolver
        self.dependency_transactions_dir = dependency_transactions_dir or (
            dependency_env_store.root_dir.parent / "transactions"
        )
        self.log_store = log_store or LogStore()

    def prepare(self, capsule_lock: CapsuleLock) -> PreparedRuntimeWorkspace:
        profile_selection = self._resolve_runtime_profile(capsule_lock)
        dependency_manifest = self._dependency_env_manifest(
            capsule_lock,
            status=InstallStatus.CHECKING_COMPATIBILITY,
        )
        dependency_manifest = self._maybe_resolve_dependency_manifest(
            capsule_lock,
            dependency_manifest,
            profile_selection,
        )
        runner_manifest = self._runner_workspace_manifest(
            capsule_lock,
            dependency_env_fingerprint=dependency_manifest.fingerprint,
            profile_selection=profile_selection,
            status=InstallStatus.CHECKING_COMPATIBILITY,
        )

        dependency_manifest = self._ensure_staged_dependency_env(dependency_manifest, capsule_lock.workflow.package_id)
        runner_manifest = self._ensure_staged_runner_workspace(runner_manifest, capsule_lock.workflow.package_id)

        return PreparedRuntimeWorkspace(
            dependency_env_manifest=dependency_manifest,
            runner_workspace_manifest=runner_manifest,
            dependency_env_path=self.dependency_env_store.artifact_dir(dependency_manifest.fingerprint),
            runner_workspace_path=self.runner_workspace_store.artifact_dir(runner_manifest.fingerprint),
        )

    def mark_ready(
        self,
        prepared_workspace: PreparedRuntimeWorkspace,
        *,
        smoke_test_status: SmokeTestStatus,
        workflow_id: str,
    ) -> PreparedRuntimeWorkspace:
        dependency_manifest = prepared_workspace.dependency_env_manifest.model_copy(
            update={
                "status": InstallStatus.READY,
                "smoke_test_status": smoke_test_status,
            }
        )
        runner_manifest = prepared_workspace.runner_workspace_manifest.model_copy(
            update={
                "status": InstallStatus.READY,
                "smoke_test_status": smoke_test_status,
            }
        )

        stored_dependency = self.dependency_env_store.read(dependency_manifest.fingerprint)
        if stored_dependency.status is InstallStatus.READY:
            dependency_manifest = stored_dependency
        else:
            self.dependency_env_store.save_staged(dependency_manifest)
            self.log_store.add(
                "info",
                "Promoted dependency environment manifest",
                "runtime.workspace",
                workflow_id=workflow_id,
                details={
                    "fingerprint": dependency_manifest.fingerprint,
                    "smoke_test_status": smoke_test_status.value,
                },
            )
        stored_runner = self.runner_workspace_store.read(runner_manifest.fingerprint)
        if stored_runner.status is InstallStatus.READY:
            runner_manifest = stored_runner
        else:
            self.runner_workspace_store.save_staged(runner_manifest)
            self.log_store.add(
                "info",
                "Promoted runner workspace manifest",
                "runtime.workspace",
                workflow_id=workflow_id,
                details={
                    "fingerprint": runner_manifest.fingerprint,
                    "smoke_test_status": smoke_test_status.value,
                },
            )

        return PreparedRuntimeWorkspace(
            dependency_env_manifest=dependency_manifest,
            runner_workspace_manifest=runner_manifest,
            dependency_env_path=prepared_workspace.dependency_env_path,
            runner_workspace_path=prepared_workspace.runner_workspace_path,
        )

    def _ensure_staged_dependency_env(
        self,
        manifest: DependencyEnvManifest,
        workflow_id: str,
    ) -> DependencyEnvManifest:
        if self.dependency_env_store.exists(manifest.fingerprint):
            existing = self.dependency_env_store.read(manifest.fingerprint)
            if existing.status is InstallStatus.READY:
                self.log_store.add(
                    "info",
                    "Reusing dependency environment manifest",
                    "runtime.workspace",
                    workflow_id=workflow_id,
                    details={
                        "fingerprint": existing.fingerprint,
                        "status": existing.status.value,
                    },
                )
                return existing
            if self.dependency_env_installer is not None:
                return self._install_staged_dependency_env(manifest, workflow_id)
            if existing != manifest:
                self.dependency_env_store.save_staged(manifest)
                self.log_store.add(
                    "info",
                    "Updated staged dependency environment manifest",
                    "runtime.workspace",
                    workflow_id=workflow_id,
                    details={"fingerprint": manifest.fingerprint},
                )
                return manifest
            self.log_store.add(
                "info",
                "Reusing staged dependency environment manifest",
                "runtime.workspace",
                workflow_id=workflow_id,
                details={"fingerprint": existing.fingerprint},
            )
            return existing
        if self.dependency_env_installer is not None:
            return self._install_staged_dependency_env(manifest, workflow_id)
        self.dependency_env_store.save_staged(manifest)
        self.log_store.add(
            "info",
            "Created staged dependency environment manifest",
            "runtime.workspace",
            workflow_id=workflow_id,
            details={"fingerprint": manifest.fingerprint},
        )
        return manifest

    def _install_staged_dependency_env(
        self,
        manifest: DependencyEnvManifest,
        workflow_id: str,
    ) -> DependencyEnvManifest:
        assert self.dependency_env_installer is not None
        lock = self._resolved_dependency_lock(manifest, workflow_id)
        staging_dir = self.dependency_transactions_dir / (
            f"dep-env-{_safe_fingerprint(manifest.fingerprint)}-{uuid4().hex}"
        )
        try:
            self.dependency_env_installer.install(
                DependencyEnvironmentInstallRequest(
                    lock=lock,
                    target_dir=staging_dir,
                    python_version=manifest.python_version,
                    workflow_id=workflow_id,
                )
            )
            staging_dir.mkdir(parents=True, exist_ok=True)
            (staging_dir / "manifest.json").write_text(
                manifest.model_dump_json(indent=2),
                encoding="utf-8",
            )
            self._promote_dependency_env_staging(staging_dir, manifest)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise

        self.log_store.add(
            "info",
            "Installed staged dependency environment",
            "runtime.workspace",
            workflow_id=workflow_id,
            details={
                "fingerprint": manifest.fingerprint,
                "dependency_lock_hash": manifest.dependency_lock_hash,
            },
        )
        return self.dependency_env_store.read(manifest.fingerprint)

    def _resolved_dependency_lock(self, manifest: DependencyEnvManifest, workflow_id: str) -> ResolvedDependencyLock:
        lock = self._find_existing_dependency_lock(manifest.dependency_lock_hash)
        source_files_dir = self._custom_node_source_files_dir(workflow_id)
        if lock is None and self.dependency_lock_resolver is not None and source_files_dir is not None:
            lock = self.dependency_lock_resolver.resolve(
                DependencyResolutionRequest(
                    source_dirs=custom_node_dependency_source_dirs(source_files_dir),
                    runtime_profile_id=manifest.runtime_profile_id,
                    runtime_profile_variant_id=manifest.runtime_profile_variant_id,
                    runtime_profile_manifest_hash=manifest.runtime_profile_manifest_hash,
                    install_policy_version=manifest.install_policy_version,
                    python_version=manifest.python_version,
                    python_platform=_uv_python_platform(manifest.os, manifest.architecture),
                    workflow_id=workflow_id,
                )
            )
            self._remember_dependency_lock(lock)
        if lock is None:
            raise RuntimeError(
                "Resolved dependency lock is not available for dependency environment install: "
                f"{manifest.dependency_lock_hash}"
            )
        if lock.runtime_profile_id != manifest.runtime_profile_id:
            raise RuntimeError("Resolved dependency lock runtime profile does not match dependency manifest.")
        if lock.runtime_profile_variant_id != manifest.runtime_profile_variant_id:
            raise RuntimeError("Resolved dependency lock runtime variant does not match dependency manifest.")
        if lock.runtime_profile_manifest_hash != manifest.runtime_profile_manifest_hash:
            raise RuntimeError("Resolved dependency lock profile hash does not match dependency manifest.")
        if lock.install_policy_version != manifest.install_policy_version:
            raise RuntimeError("Resolved dependency lock install policy does not match dependency manifest.")
        lock_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        if lock_hash != manifest.dependency_lock_hash:
            raise RuntimeError("Resolved dependency lock hash does not match dependency manifest.")
        return lock

    def _find_existing_dependency_lock(self, lock_hash: str) -> ResolvedDependencyLock | None:
        lock = self.dependency_locks.get(lock_hash)
        if lock is not None:
            return lock
        if self.dependency_lock_store is not None and self.dependency_lock_store.exists(lock_hash):
            lock = self.dependency_lock_store.read(lock_hash)
            self.dependency_locks[lock_hash] = lock
            return lock
        return None

    def _remember_dependency_lock(self, lock: ResolvedDependencyLock) -> None:
        lock_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        self.dependency_locks[lock_hash] = lock
        if self.dependency_lock_store is not None:
            self.dependency_lock_store.write(lock)

    def _custom_node_source_files_dir(self, workflow_id: str) -> Path | None:
        if self.custom_node_source_files_dir_resolver is not None:
            return self.custom_node_source_files_dir_resolver(workflow_id)
        return self.custom_node_source_files_dir

    def _promote_dependency_env_staging(
        self,
        staging_dir: Path,
        manifest: DependencyEnvManifest,
    ) -> None:
        artifact_dir = self.dependency_env_store.artifact_dir(manifest.fingerprint)
        if self.dependency_env_store.exists(manifest.fingerprint):
            existing = self.dependency_env_store.read(manifest.fingerprint)
            if existing.status is InstallStatus.READY:
                shutil.rmtree(staging_dir, ignore_errors=True)
                return
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        artifact_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.replace(artifact_dir)

    def _ensure_staged_runner_workspace(
        self,
        manifest: RunnerWorkspaceManifest,
        workflow_id: str,
    ) -> RunnerWorkspaceManifest:
        workspace_path = self.runner_workspace_store.artifact_dir(manifest.fingerprint)
        if self.runner_workspace_store.exists(manifest.fingerprint):
            existing = self.runner_workspace_store.read(manifest.fingerprint)
            if existing.status is InstallStatus.READY:
                self._validate_ready_runner_workspace(workspace_path)
                self.log_store.add(
                    "info",
                    "Reusing runner workspace manifest",
                    "runtime.workspace",
                    workflow_id=workflow_id,
                    details={
                        "fingerprint": existing.fingerprint,
                        "status": existing.status.value,
                    },
                )
                return existing
            self._materialize_runner_workspace(workspace_path, workflow_id)
            if existing != manifest:
                self.runner_workspace_store.save_staged(manifest)
                self.log_store.add(
                    "info",
                    "Updated staged runner workspace manifest",
                    "runtime.workspace",
                    workflow_id=workflow_id,
                    details={"fingerprint": manifest.fingerprint},
                )
                return manifest
            self.log_store.add(
                "info",
                "Reusing staged runner workspace manifest",
                "runtime.workspace",
                workflow_id=workflow_id,
                details={"fingerprint": existing.fingerprint},
            )
            return existing
        self._materialize_runner_workspace(workspace_path, workflow_id)
        self.runner_workspace_store.save_staged(manifest)
        self.log_store.add(
            "info",
            "Created staged runner workspace manifest",
            "runtime.workspace",
            workflow_id=workflow_id,
            details={"fingerprint": manifest.fingerprint},
        )
        return manifest

    def _dependency_env_manifest(
        self,
        capsule_lock: CapsuleLock,
        *,
        status: InstallStatus = InstallStatus.READY,
        smoke_test_status: SmokeTestStatus = SmokeTestStatus.NOT_RUN,
    ) -> DependencyEnvManifest:
        runtime = capsule_lock.runtime
        return DependencyEnvManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            fingerprint=runtime.dependency_env_fingerprint,
            runtime_profile_id=runtime.runtime_profile_id,
            runtime_profile_variant_id=runtime.runtime_profile_variant_id,
            runtime_profile_manifest_hash=runtime.runtime_profile_manifest_hash,
            runtime_profile_catalog_version=runtime.runtime_profile_catalog_version,
            fingerprint_schema_version=runtime.fingerprint_schema_version,
            python_version=runtime.python_version,
            python_build_id=runtime.python_build_id,
            os=runtime.os,
            architecture=runtime.architecture,
            gpu_backend=runtime.gpu_backend,
            dependency_lock_hash=runtime.dependency_lock_hash,
            install_policy_version=capsule_lock.dependencies.install_policy,
            status=status,
            smoke_test_status=smoke_test_status,
        )

    def _maybe_resolve_dependency_manifest(
        self,
        capsule_lock: CapsuleLock,
        manifest: DependencyEnvManifest,
        profile_selection: RuntimeProfileSelection | None,
    ) -> DependencyEnvManifest:
        if self.dependency_env_installer is None:
            return manifest
        source_files_dir = self._custom_node_source_files_dir(capsule_lock.workflow.package_id)
        source_dirs = custom_node_dependency_source_dirs(source_files_dir) if source_files_dir is not None else []
        if self.dependency_lock_resolver is not None and source_dirs:
            existing_core_lock = self._find_existing_dependency_lock(manifest.dependency_lock_hash)
            custom_node_lock = self.dependency_lock_resolver.resolve(
                DependencyResolutionRequest(
                    source_dirs=source_dirs,
                    runtime_profile_id=manifest.runtime_profile_id,
                    runtime_profile_variant_id=manifest.runtime_profile_variant_id,
                    runtime_profile_manifest_hash=manifest.runtime_profile_manifest_hash,
                    install_policy_version=manifest.install_policy_version,
                    python_version=manifest.python_version,
                    python_platform=_uv_python_platform(manifest.os, manifest.architecture),
                    workflow_id=capsule_lock.workflow.package_id,
                )
            )
            if existing_core_lock is not None:
                lock = merge_resolved_dependency_locks(existing_core_lock, [custom_node_lock])
            elif custom_node_lock.wheels:
                lock = merge_resolved_dependency_locks(core_dependency_lock_from_capsule(capsule_lock), [custom_node_lock])
            else:
                lock = custom_node_lock
            self._remember_dependency_lock(lock)
        else:
            lock = self._find_existing_dependency_lock(manifest.dependency_lock_hash)
        if lock is None:
            return manifest
        lock_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        if lock_hash == manifest.dependency_lock_hash:
            return manifest
        dependency_fingerprint = dependency_env_fingerprint_for_resolved_lock(
            lock,
            os_name=manifest.os,
            architecture=manifest.architecture,
            python_build_id=manifest.python_build_id,
            torch_wheel_build_tag=profile_selection.variant.torch_wheel_build_tag
            if profile_selection is not None
            else manifest.gpu_backend,
            torch_backend=manifest.gpu_backend,
        )
        self.log_store.add(
            "info",
            "Using locally resolved dependency lock for workflow",
            "runtime.workspace",
            workflow_id=capsule_lock.workflow.package_id,
            details={
                "original_dependency_lock_hash": manifest.dependency_lock_hash,
                "resolved_dependency_lock_hash": lock_hash,
                "dependency_env_fingerprint": dependency_fingerprint,
            },
        )
        return manifest.model_copy(
            update={
                "fingerprint": dependency_fingerprint,
                "dependency_lock_hash": lock_hash,
            }
        )

    def _resolve_runtime_profile(self, capsule_lock: CapsuleLock) -> RuntimeProfileSelection | None:
        if self.runtime_profile_catalog is None:
            return None
        runtime = capsule_lock.runtime
        selection = resolve_runtime_profile(
            self.runtime_profile_catalog,
            runtime_profile_id=runtime.runtime_profile_id,
            runtime_profile_variant_id=runtime.runtime_profile_variant_id,
            os_name=runtime.os,
            architecture=runtime.architecture,
            gpu_backend_profile=runtime.gpu_backend,
        )
        if selection.profile.runtime_profile_manifest_hash != runtime.runtime_profile_manifest_hash:
            raise RuntimeProfileResolutionError(
                RuntimeProfileErrorCode.PROFILE_MANIFEST_HASH_MISMATCH,
                "Workflow runtime profile manifest hash does not match the installed catalog.",
            )
        return selection

    def _runner_workspace_manifest(
        self,
        capsule_lock: CapsuleLock,
        *,
        dependency_env_fingerprint: str | None = None,
        profile_selection: RuntimeProfileSelection | None = None,
        status: InstallStatus = InstallStatus.READY,
        smoke_test_status: SmokeTestStatus = SmokeTestStatus.NOT_RUN,
    ) -> RunnerWorkspaceManifest:
        runtime = capsule_lock.runtime
        dependency_env_fingerprint = dependency_env_fingerprint or runtime.dependency_env_fingerprint
        enabled_custom_node_hash = sha256_fingerprint(capsule_lock.custom_nodes)
        launch_config_hash = sha256_fingerprint(
            {
                "engine": capsule_lock.engine.type,
                "runner": "core_comfyui",
                "phase": "phase4-runner-workspace",
            }
        )
        model_view_hash = sha256_fingerprint(capsule_lock.models)
        fingerprint = runtime.runner_fingerprint
        if dependency_env_fingerprint != runtime.dependency_env_fingerprint:
            fingerprint = runner_workspace_fingerprint(
                dependency_env_fingerprint=dependency_env_fingerprint,
                runtime_profile_id=runtime.runtime_profile_id,
                runtime_profile_manifest_hash=runtime.runtime_profile_manifest_hash,
                runtime_profile_variant_id=runtime.runtime_profile_variant_id,
                comfyui_source_hash=capsule_lock.engine.core_source_hash,
                comfyui_frontend_version=profile_selection.profile.comfyui_frontend_version
                if profile_selection is not None
                else "unknown",
                enabled_custom_node_manifest_hash=enabled_custom_node_hash,
                launch_config_hash=launch_config_hash,
                model_view_hash=model_view_hash,
            )
        return RunnerWorkspaceManifest(
            schema_version=RUNTIME_MANIFEST_SCHEMA_VERSION,
            fingerprint=fingerprint,
            runtime_profile_id=runtime.runtime_profile_id,
            runtime_profile_variant_id=runtime.runtime_profile_variant_id,
            runtime_profile_manifest_hash=runtime.runtime_profile_manifest_hash,
            runtime_profile_catalog_version=runtime.runtime_profile_catalog_version,
            fingerprint_schema_version=runtime.fingerprint_schema_version,
            dependency_env_fingerprint=dependency_env_fingerprint,
            comfyui_version=capsule_lock.engine.comfyui_version,
            comfyui_source_hash=capsule_lock.engine.core_source_hash,
            enabled_custom_node_hash=enabled_custom_node_hash,
            launch_config_hash=launch_config_hash,
            model_view_hash=model_view_hash,
            status=status,
            smoke_test_status=smoke_test_status,
        )

    def _materialize_runner_workspace(self, workspace_path: Path, workflow_id: str) -> None:
        if self.comfyui_source_dir is None:
            return
        source_dir = self.comfyui_source_dir
        if not source_dir.exists():
            raise FileNotFoundError(f"ComfyUI source directory not found: {source_dir}")
        if not (source_dir / "main.py").exists():
            raise FileNotFoundError(f"ComfyUI main.py not found in: {source_dir}")

        workspace_path.mkdir(parents=True, exist_ok=True)
        for entry in sorted(source_dir.iterdir(), key=lambda path: path.name):
            if entry.name in _WORKSPACE_OWNED_NAMES:
                continue
            target = workspace_path / entry.name
            self._link_or_copy(entry, target)

        for directory_name in ("custom_nodes", "input", "output", "temp", "user"):
            (workspace_path / directory_name).mkdir(parents=True, exist_ok=True)

        models_target = workspace_path / "models"
        if self.model_view_dir is not None:
            self.model_view_dir.mkdir(parents=True, exist_ok=True)
            self._link_or_copy(self.model_view_dir, models_target)
        else:
            models_target.mkdir(parents=True, exist_ok=True)

        self.log_store.add(
            "info",
            "Materialized runner workspace source view",
            "runtime.workspace",
            workflow_id=workflow_id,
            details={
                "workspace_path": str(workspace_path),
                "source_dir": str(source_dir),
                "model_view_dir": str(self.model_view_dir) if self.model_view_dir else None,
            },
        )

    def _validate_ready_runner_workspace(self, workspace_path: Path) -> None:
        if self.comfyui_source_dir is None:
            return
        missing = [
            name
            for name in ("main.py", "custom_nodes", "models", "input", "output", "temp", "user")
            if not (workspace_path / name).exists()
        ]
        if missing:
            raise RuntimeError(
                "Ready runner workspace is missing materialized entries: "
                + ", ".join(sorted(missing))
            )

    def _link_or_copy(self, source: Path, target: Path) -> None:
        if target.exists() or target.is_symlink():
            return
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
            return
        except (NotImplementedError, OSError):
            pass

        if source.is_dir():
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target)


_WORKSPACE_OWNED_NAMES = {
    ".git",
    "__pycache__",
    "custom_nodes",
    "input",
    "models",
    "output",
    "temp",
    "user",
}


def _safe_fingerprint(fingerprint: str) -> str:
    return fingerprint.replace("sha256:", "").replace("/", "_").replace("\\", "_").replace(":", "_")


def _uv_python_platform(os_name: str, architecture: str) -> str | None:
    if os_name == "darwin" and architecture == "arm64":
        return "aarch64-apple-darwin"
    if os_name == "darwin" and architecture == "x64":
        return "x86_64-apple-darwin"
    if os_name == "windows" and architecture == "x64":
        return "x86_64-pc-windows-msvc"
    if os_name == "linux" and architecture == "x64":
        return "x86_64-unknown-linux-gnu"
    return None
