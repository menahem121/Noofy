from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.engine.adapter import EngineAdapter
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.engine.diagnostics import DiagnosticsSink, LogStore
from app.engine.service import (
    EngineService,
    _smoke_execution_fixture_for_capsule,
    _workflow_runner_launch_spec,
    _workflow_source_files_dir,
)
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.comfyui_updates import (
    ComfyUIUpdateService,
    resolve_active_runtime_selection,
)
from app.runtime.custom_nodes import CustomNodeWorkspaceMaterializer
from app.runtime.dependency_env import UvDependencyEnvironmentInstaller
from app.runtime.dependency_lock import core_dependency_lock_from_capsule
from app.runtime.dependency_lock_store import ResolvedDependencyLockStore
from app.runtime.dependency_resolver import UvDependencyLockResolver
from app.runtime.environment import RuntimeEnvironment
from app.runtime.install_state import InstallStateStore
from app.runtime.install_transactions import InstallTransactionStore
from app.runtime.launch_settings import ComfyUILaunchSettingsStore
from app.runtime.manager import RuntimeManager
from app.runtime.memory_governor import (
    LocalMemoryLearningStore,
    default_memory_observer,
)
from app.runtime.model_store import ModelStore, http_streaming_downloader
from app.runtime.node_registry import (
    CustomNodeSourceCache,
    NodeRegistryResolver,
    NoofyNodeRegistry,
)
from app.runtime.profiles import (
    DEFAULT_RUNTIME_PROFILE_CATALOG_PATH,
    load_runtime_profile_catalog,
)
from app.runtime.runner_coordinator import AdapterFactory, RunnerProcessCoordinator
from app.runtime.runner_process import RunnerProcessSupervisor
from app.runtime.smoke_test import RunnerSmokeTester
from app.runtime.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerStatus,
    RunnerSupervisor,
)
from app.runtime.uv_executable import resolve_noofy_uv_executable
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)
from app.trust import load_trust_verifier
from app.workflows.authoring import DashboardAuthoringService
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.exporter import WorkflowExporter
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator


def comfyui_adapter_factory(
    *,
    models_dir: Path,
    log_store: DiagnosticsSink,
) -> AdapterFactory:
    def factory(descriptor: RunnerDescriptor) -> EngineAdapter:
        return ComfyUIEngineAdapter(
            descriptor.base_url,
            models_dir,
            descriptor.ws_url,
            log_store=log_store,
        )

    return factory


def create_default_engine_service() -> EngineService:
    paths = settings.paths
    paths.ensure_directories()
    log_store = LogStore()
    sweep_report = InstallTransactionStore(
        paths.install_transactions_dir, log_store=log_store
    ).sweep_startup()
    if (
        sweep_report.stale_transactions_quarantined
        or sweep_report.expired_quarantines_removed
        or sweep_report.stale_tmp_files_removed
        or sweep_report.stale_lock_files_removed
    ):
        log_store.add(
            "info",
            "Runtime install startup sweep completed",
            "runtime.install_transaction",
            details={
                "stale_transactions_quarantined": sweep_report.stale_transactions_quarantined,
                "expired_quarantines_removed": sweep_report.expired_quarantines_removed,
                "stale_tmp_files_removed": sweep_report.stale_tmp_files_removed,
                "stale_lock_files_removed": sweep_report.stale_lock_files_removed,
            },
        )
    loader = WorkflowPackageLoader(
        settings.workflows_dir,
        user_packages_dir=paths.user_workflows_dir,
        imported_packages_dir=paths.workflow_packages_store_dir,
    )
    validator = WorkflowPackageValidator()
    developer_runtime_override = (
        settings.comfyui_repo_dir_override_active
        or settings.comfyui_python_executable_override_active
    )
    active_runtime = resolve_active_runtime_selection(
        paths,
        fallback_repo_dir=settings.comfyui_repo_dir,
        fallback_python_executable=settings.comfyui_python_executable,
        mode=settings.comfyui_runtime_mode,
        developer_override=developer_runtime_override,
    )
    launch_settings_store = ComfyUILaunchSettingsStore(
        paths.runtime_store_dir / "settings" / "comfyui-launch.json"
    )
    launch_settings = launch_settings_store.read()
    runtime_environment = RuntimeEnvironment(
        repo_dir=active_runtime.repo_dir,
        runtime_dir=settings.runtime_dir,
        bootstrap_python_executable=settings.comfyui_bootstrap_python_executable,
        python_executable_override=active_runtime.python_executable,
        torch_cuda_index_url=settings.comfyui_torch_cuda_index_url,
        torch_cpu_index_url=settings.comfyui_torch_cpu_index_url,
        log_store=log_store,
        logs_dir=paths.logs_dir,
        cache_dir=paths.cache_dir,
        venv_dir_override=active_runtime.venv_dir,
    )
    runtime_manager = RuntimeManager(
        mode=settings.comfyui_runtime_mode,
        external_base_url=settings.comfyui_base_url,
        external_ws_url=settings.comfyui_ws_url,
        repo_dir=active_runtime.repo_dir,
        python_executable=runtime_environment.python_executable,
        managed_host=settings.comfyui_managed_host,
        managed_port=settings.comfyui_managed_port,
        startup_timeout_seconds=settings.comfyui_startup_timeout_seconds,
        health_poll_interval_seconds=settings.comfyui_health_poll_interval_seconds,
        max_restart_attempts=settings.comfyui_max_restart_attempts,
        restart_backoff_base_seconds=settings.comfyui_restart_backoff_base,
        log_store=log_store,
        environment=runtime_environment,
        pid_dir=paths.runtime_dir,
        managed_base_directory=paths.data_dir,
        managed_output_directory=paths.outputs_dir,
        managed_input_directory=paths.input_dir,
        managed_temp_directory=paths.data_dir,
        managed_user_directory=paths.comfyui_user_dir,
        managed_database_url=f"sqlite:///{paths.comfyui_database_file.as_posix()}",
        python_cache_dir=paths.python_cache_dir,
        version_metadata=active_runtime.version_metadata,
        managed_vram_mode=launch_settings.vram_mode,
    )
    runtime_manager._cleanup_stale_pid()
    adapter = ComfyUIEngineAdapter(
        runtime_manager.base_url,
        settings.comfyui_models_dir,
        runtime_manager.ws_url,
        log_store=log_store,
        dashboard_assets_dir=paths.dashboard_assets_dir,
        comfyui_input_dir=paths.input_dir,
    )
    trust_verifier = load_trust_verifier(settings.trust_keys_file, log_store=log_store)
    imported_package_store = ImportedWorkflowPackageStore(
        paths.workflow_packages_store_dir,
        log_store=log_store,
        trust_verifier=trust_verifier,
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="noofy-empty-local-registry"),
            log_store=log_store,
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=paths.custom_node_cache_dir,
            log_store=log_store,
        ),
    )

    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url=runtime_manager.base_url,
            ws_url=runtime_manager.ws_url,
            fingerprint=CORE_RUNNER_FINGERPRINT,
            status=RunnerStatus.UNKNOWN,
        ),
        adapter,
    )

    capsule_loader = CapsuleLockLoader(
        settings.workflows_dir,
        user_packages_dir=paths.user_workflows_dir,
        imported_packages_dir=paths.workflow_packages_store_dir,
    )
    dependency_lock_store = ResolvedDependencyLockStore(paths.dependency_locks_dir)
    preseed_capsule_loader = CapsuleLockLoader(
        settings.workflows_dir,
        user_packages_dir=paths.user_workflows_dir,
    )
    for capsule_lock in preseed_capsule_loader.list_capsule_locks():
        if not capsule_lock.custom_nodes:
            dependency_lock_store.write(core_dependency_lock_from_capsule(capsule_lock))
    install_state_store = InstallStateStore(paths.workflow_store_dir / "install-state")
    stale_install_state_temps = install_state_store.remove_stale_temp_files()
    if stale_install_state_temps:
        log_store.add(
            "info",
            "Removed stale install-state temp files",
            "runtime.install_state",
            details={"removed_count": stale_install_state_temps},
        )
    model_store = ModelStore(
        blobs_dir=paths.model_blobs_dir,
        refs_dir=paths.model_refs_dir,
        materialized_dir=paths.model_materialized_dir,
        transactions_dir=paths.install_transactions_dir,
        log_store=log_store,
        downloader=http_streaming_downloader,
        local_model_roots=[settings.comfyui_models_dir],
    )
    orphan_model_links_removed = model_store.sweep_orphan_materialized_links()
    if orphan_model_links_removed:
        log_store.add(
            "info",
            "Removed orphan materialized model links",
            "model.store",
            details={"removed_count": orphan_model_links_removed},
        )
    runner_process_supervisor = RunnerProcessSupervisor(
        log_store=log_store,
        startup_timeout_seconds=settings.comfyui_startup_timeout_seconds,
        health_poll_interval_seconds=settings.comfyui_health_poll_interval_seconds,
        pid_dir=paths.runtime_store_dir / "runners",
    )
    stale_runner_pids_cleaned = runner_process_supervisor.cleanup_stale_pid_files()
    if stale_runner_pids_cleaned:
        log_store.add(
            "info",
            "Cleaned stale workflow runner PID files",
            "runtime.runner_process",
            details={"cleaned_count": stale_runner_pids_cleaned},
        )
    runner_smoke_tester = RunnerSmokeTester(
        process_supervisor=runner_process_supervisor,
        launch_spec_factory=lambda capsule_lock, prepared_workspace: _workflow_runner_launch_spec(
            capsule_lock,
            dependency_env_path=prepared_workspace.dependency_env_path,
            runner_workspace_path=prepared_workspace.runner_workspace_path,
            runtime_manager=runtime_manager,
            runner_id_suffix="smoke",
        ),
        execution_fixture_resolver=lambda capsule_lock, prepared_workspace: _smoke_execution_fixture_for_capsule(
            capsule_lock,
            workflow_loader=loader,
        ),
        log_store=log_store,
    )
    capsule_installer = CapsuleInstaller(
        install_state_store=install_state_store,
        model_store=model_store,
        workspace_preparer=RuntimeWorkspacePreparer(
            dependency_env_store=DependencyEnvManifestStore(paths.dependency_envs_dir),
            runner_workspace_store=RunnerWorkspaceManifestStore(
                paths.runner_workspaces_dir
            ),
            comfyui_source_dir=active_runtime.repo_dir,
            model_view_dir=paths.model_materialized_dir,
            runtime_profile_catalog=load_runtime_profile_catalog(
                DEFAULT_RUNTIME_PROFILE_CATALOG_PATH
            ),
            dependency_env_installer=UvDependencyEnvironmentInstaller(
                wheel_cache_dir=paths.wheel_cache_dir,
                uv_cache_dir=paths.cache_dir / "uv",
                uv_executable=resolve_noofy_uv_executable(),
                log_store=log_store,
            ),
            dependency_lock_store=dependency_lock_store,
            dependency_lock_resolver=UvDependencyLockResolver(
                wheel_cache_dir=paths.wheel_cache_dir,
                work_dir=paths.install_transactions_dir,
                uv_cache_dir=paths.cache_dir / "uv",
                uv_executable=resolve_noofy_uv_executable(),
                log_store=log_store,
            ),
            custom_node_materializer=CustomNodeWorkspaceMaterializer(),
            custom_node_source_files_dir_resolver=lambda workflow_id: _workflow_source_files_dir(
                workflow_id,
                workflow_loader=loader,
                imported_package_store=imported_package_store,
            ),
            custom_node_source_cache_dir=paths.custom_node_cache_dir,
            dependency_transactions_dir=paths.install_transactions_dir,
            log_store=log_store,
        ),
        workspace_smoke_test=runner_smoke_tester.run,
        log_store=log_store,
    )
    runner_process_coordinator = RunnerProcessCoordinator(
        runner_supervisor=supervisor,
        process_supervisor=runner_process_supervisor,
        adapter_factory=comfyui_adapter_factory(
            models_dir=settings.comfyui_models_dir,
            log_store=log_store,
        ),
        log_store=log_store,
    )

    # Wire the on_restart callback so the supervisor and adapter learn
    # the new URL after a crash-restart that picked a new port.
    def _reconfigure_adapter() -> None:
        supervisor.update_runner_endpoint(
            CORE_RUNNER_ID,
            runtime_manager.base_url,
            runtime_manager.ws_url,
        )

    runtime_manager._on_restart = _reconfigure_adapter
    comfyui_update_service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=runtime_manager,
        mode=settings.comfyui_runtime_mode,
        developer_override=developer_runtime_override,
        bootstrap_python_executable=settings.comfyui_bootstrap_python_executable,
        torch_cuda_index_url=settings.comfyui_torch_cuda_index_url,
        torch_cpu_index_url=settings.comfyui_torch_cpu_index_url,
        bundled_repo_dir=settings.comfyui_repo_dir,
        bundled_python_executable=RuntimeEnvironment(
            repo_dir=settings.comfyui_repo_dir,
            runtime_dir=settings.runtime_dir,
            bootstrap_python_executable=settings.comfyui_bootstrap_python_executable,
            torch_cuda_index_url=settings.comfyui_torch_cuda_index_url,
            torch_cpu_index_url=settings.comfyui_torch_cpu_index_url,
            log_store=log_store,
            logs_dir=paths.logs_dir,
            cache_dir=paths.cache_dir,
        ).python_executable,
        log_store=log_store,
    )

    log_store.add(
        "info",
        "Backend engine service initialized",
        "engine.service",
        details={
            "runtime_mode": runtime_manager.mode,
            "data_dir": str(paths.data_dir),
            "core_runner_id": CORE_RUNNER_ID,
        },
    )
    service = EngineService(
        loader,
        validator,
        supervisor,
        runtime_manager,
        log_store,
        capsule_loader=capsule_loader,
        capsule_installer=capsule_installer,
        runner_process_coordinator=runner_process_coordinator,
        imported_package_store=imported_package_store,
        memory_observer=default_memory_observer(),
        memory_learning_store=LocalMemoryLearningStore(
            paths.user_state_dir / "memory-learning"
        ),
        comfyui_update_service=comfyui_update_service,
        comfyui_launch_settings_store=launch_settings_store,
        dashboard_authoring=DashboardAuthoringService(
            workflow_store_dir=paths.workflow_packages_store_dir,
            workflow_loader=loader,
            validator=validator,
            log_store=log_store,
        ),
        workflow_exporter=WorkflowExporter(
            workflow_store_dir=paths.workflow_packages_store_dir,
            workflow_loader=loader,
        ),
    )
    return service
