from __future__ import annotations

from pathlib import Path

from app.core.config import settings
from app.engine.adapter import EngineAdapter
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.diagnostics import DiagnosticsSink, LogStore
from app.engine.service import EngineService
from app.runtime.runners.lifecycle_service import (
    _smoke_execution_fixture_for_capsule,
    _workflow_runner_launch_spec,
    _workflow_source_files_dir,
)
from app.gallery import GalleryStore
from app.history import ActivityLogStore, HistoryService
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.comfyui.comfyui_sidecar_service import ComfyUISidecarService
from app.runtime.comfyui.comfyui_updates import (
    ComfyUIUpdateService,
    resolve_active_runtime_selection,
)
from app.runtime.comfyui.comfyui_update_records import (
    read_active_record,
)
from app.runtime.dependencies.custom_nodes import CustomNodeWorkspaceMaterializer
from app.runtime.dependencies.dependency_env import UvDependencyEnvironmentInstaller
from app.runtime.dependencies.dependency_lock import core_dependency_lock_from_capsule
from app.runtime.dependencies.dependency_lock_store import ResolvedDependencyLockStore
from app.runtime.dependencies.dependency_resolver import UvDependencyLockResolver
from app.runtime.environment import RuntimeEnvironment
from app.runtime.install_state import InstallStateStore
from app.runtime.install_transactions import InstallTransactionStore
from app.runtime.comfyui.launch_settings import ComfyUILaunchSettingsStore
from app.runtime.manager import RuntimeManager
from app.runtime.memory.memory_governor import (
    LocalMemoryLearningStore,
    default_memory_observer,
)
from app.runs.progress_estimator import ProgressTimingStore, WorkflowProgressEstimator
from app.runtime.models.model_store import ModelStore, http_streaming_downloader
from app.runtime.node_registry import (
    CustomNodeSourceCache,
    NodeRegistryResolver,
    load_node_type_mapping_catalog,
    load_noofy_node_registry,
)
from app.runtime.profiles import (
    ActiveRuntimeProfileState,
    DEFAULT_RUNTIME_PROFILE_CATALOG_PATH,
    load_runtime_profile_catalog,
)
from app.runtime.runners.runner_coordinator import AdapterFactory, RunnerProcessCoordinator
from app.runtime.runners.runner_process import RunnerProcessSupervisor
from app.runtime.runners.runtime_activation import WorkflowRuntimeActivationCoordinator
from app.runtime.smoke_test import RunnerSmokeTester
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerStatus,
    RunnerSupervisor,
)
from app.runtime.uv_executable import resolve_noofy_uv_executable
from app.runtime.storage.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.storage.maintenance import RuntimeStorageMaintenanceService
from app.runtime.storage.storage_gc import RuntimeStorageRoots
from app.runtime.storage.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)
from app.models.folders import (
    ModelFolderSettingsStore,
    default_noofy_models_dir,
    ensure_model_subfolders,
    repair_accidental_default_models_folder,
    write_extra_model_paths_config,
)
from app.models.source_auth import provider_auth_headers_for_url
from app.settings.api_keys import (
    ApiKeyMetadataStore,
    ApiKeySettingsService,
    create_credential_store,
)
from app.trust import load_trust_verifier
from app.workflows.authoring import DashboardAuthoringService
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.exporter import WorkflowExporter
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.import_runtime_profile import (
    RuntimeProfileSelectionError,
    select_import_runtime_profile,
)
from app.workflows.library import WorkflowLibraryStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.model_availability import ModelAvailabilityService
from app.workflows.model_identity_store import LocalModelIdentityStore
from app.workflows.user_state import UserStateService
from app.workflows.validator import WorkflowPackageValidator


def comfyui_adapter_factory(
    *,
    model_roots: list[Path] | None = None,
    models_dir: Path | None = None,
    dashboard_assets_dir: Path | None = None,
    log_store: DiagnosticsSink,
) -> AdapterFactory:
    roots = model_roots or ([models_dir] if models_dir is not None else None)
    if not roots:
        raise ValueError("A ComfyUI adapter factory requires at least one model root.")

    def factory(descriptor: RunnerDescriptor) -> EngineAdapter:
        comfyui_input_dir = (
            Path(descriptor.runner_workspace_path) / "input"
            if descriptor.runner_workspace_path
            else None
        )
        return ComfyUIEngineAdapter(
            descriptor.base_url,
            roots[0],
            descriptor.ws_url,
            log_store=log_store,
            dashboard_assets_dir=dashboard_assets_dir,
            comfyui_input_dir=comfyui_input_dir,
            model_roots=roots,
        )

    return factory


def create_default_engine_service() -> EngineService:
    paths = settings.paths
    paths.ensure_directories()
    log_store = LogStore()
    base_runtime_profile_catalog = load_runtime_profile_catalog(
        DEFAULT_RUNTIME_PROFILE_CATALOG_PATH
    )
    runtime_profile_state = ActiveRuntimeProfileState(
        base_catalog=base_runtime_profile_catalog,
        source_dir=settings.comfyui_repo_dir,
    )
    selected_runtime_profile_variant = None
    try:
        _, selected_runtime_profile_variant = select_import_runtime_profile(
            base_runtime_profile_catalog.profiles
        )
    except RuntimeProfileSelectionError as exc:
        log_store.add(
            "warning",
            "No supported managed runtime profile is available for this machine",
            "runtime.profiles",
            details={"error": str(exc)},
        )
    model_folder_store = ModelFolderSettingsStore(
        paths.settings_dir / "model-folders.json"
    )
    model_folder_settings = model_folder_store.read(
        default_noofy_models_dir=default_noofy_models_dir(paths.data_dir)
    )
    model_folder_settings = repair_accidental_default_models_folder(
        model_folder_settings,
        default_noofy_models_dir=default_noofy_models_dir(paths.data_dir),
        store=model_folder_store,
        log_store=log_store,
    )
    noofy_models_dir = Path(model_folder_settings.noofy_models_dir)
    external_comfyui_models_dir = (
        Path(model_folder_settings.external_comfyui_models_dir)
        if model_folder_settings.external_comfyui_models_dir
        else None
    )
    ensure_model_subfolders(noofy_models_dir)
    model_roots = [noofy_models_dir]
    if external_comfyui_models_dir is not None:
        model_roots.append(external_comfyui_models_dir)
    extra_model_paths_config = paths.runtime_store_dir / "settings" / "extra-model-paths.yaml"
    write_extra_model_paths_config(
        extra_model_paths_config,
        noofy_models_dir=noofy_models_dir,
        external_comfyui_models_dir=external_comfyui_models_dir,
    )
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
        dashboard_overrides_dir=paths.workflow_dashboard_overrides_dir,
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
    if active_runtime.version_metadata.source_kind == "installed":
        active_record = read_active_record(paths)
        if (
            active_record is not None
            and active_record.source_hash
            and active_record.source_path
        ):
            try:
                runtime_profile_state.activate(
                    runtime_profile_state.prepare_local_activation(
                        comfyui_core_version=active_record.tag,
                        comfyui_core_source_hash=active_record.source_hash,
                        source_reference=active_record.archive_url or active_record.tag,
                        source_dir=Path(active_record.source_path),
                    )
                )
            except (OSError, ValueError) as exc:
                log_store.add(
                    "warning",
                    "Active managed ComfyUI could not initialize the workflow runtime profile",
                    "runtime.profiles",
                    details={"tag": active_record.tag, "error": str(exc)},
                )
    runtime_profile_catalog = runtime_profile_state.catalog()
    launch_settings_store = ComfyUILaunchSettingsStore(
        paths.runtime_store_dir / "settings" / "comfyui-launch.json"
    )
    launch_settings = launch_settings_store.read()
    runtime_environment = RuntimeEnvironment(
        repo_dir=active_runtime.repo_dir,
        runtime_dir=settings.runtime_dir,
        bootstrap_python_executable=settings.comfyui_bootstrap_python_executable,
        python_executable_override=(
            active_runtime.python_executable
            if active_runtime.venv_dir is None
            else None
        ),
        expected_python_version=(
            selected_runtime_profile_variant.python_version
            if selected_runtime_profile_variant is not None
            else None
        ),
        packaged_runtime=settings.packaged_runtime_active and not developer_runtime_override,
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
        managed_extra_model_paths_config=extra_model_paths_config,
        managed_model_roots=model_roots,
        version_metadata=active_runtime.version_metadata,
        managed_vram_mode=launch_settings.vram_mode,
        managed_preview_method=(
            selected_runtime_profile_variant.launch_defaults.preview_method
            if selected_runtime_profile_variant is not None
            else "auto"
        ),
        managed_preview_size=(
            selected_runtime_profile_variant.launch_defaults.preview_size
            if selected_runtime_profile_variant is not None
            else 512
        ),
    )
    runtime_manager._cleanup_stale_pid()
    adapter = ComfyUIEngineAdapter(
        runtime_manager.base_url,
        noofy_models_dir,
        runtime_manager.ws_url,
        log_store=log_store,
        dashboard_assets_dir=paths.dashboard_assets_dir,
        comfyui_input_dir=paths.input_dir,
        model_roots=model_roots,
        default_prompt_preview_method=(
            "auto" if runtime_manager.mode == "external" else None
        ),
    )
    trust_verifier = load_trust_verifier(settings.trust_keys_file, log_store=log_store)
    imported_package_store = ImportedWorkflowPackageStore(
        paths.workflow_packages_store_dir,
        log_store=log_store,
        trust_verifier=trust_verifier,
        node_registry_resolver=NodeRegistryResolver(
            registry=load_noofy_node_registry(),
            mappings=load_node_type_mapping_catalog(),
            log_store=log_store,
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=paths.custom_node_cache_dir,
            log_store=log_store,
        ),
        runtime_profile_catalog_provider=runtime_profile_state.catalog,
    )

    supervisor = RunnerSupervisor(
        closed_view_cooldown_seconds=settings.closed_view_cooldown_seconds,
    )
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url=runtime_manager.base_url,
            ws_url=runtime_manager.ws_url,
            fingerprint=CORE_RUNNER_FINGERPRINT,
            status=RunnerStatus.UNKNOWN,
            pid=runtime_manager.managed_process_pid(),
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
    api_key_service = ApiKeySettingsService(
        metadata_store=ApiKeyMetadataStore(paths.settings_dir / "api-keys.json"),
        credential_store=create_credential_store(
            data_dir=paths.data_dir,
            settings_dir=paths.settings_dir,
        ),
        log_store=log_store,
    )
    try:
        model_identity_store = LocalModelIdentityStore(
            paths.model_store_dir / "identity" / "local-model-identities.db",
            log_store=log_store,
        )
    except Exception as exc:
        model_identity_store = None
        log_store.add(
            "warning",
            "Local model hash cache could not be opened; model verification will continue without cache",
            "workflow.models.cache",
            details={"error": str(exc)},
        )
    model_store = ModelStore(
        blobs_dir=paths.model_blobs_dir,
        refs_dir=paths.model_refs_dir,
        materialized_dir=paths.model_materialized_dir,
        transactions_dir=paths.install_transactions_dir,
        log_store=log_store,
        downloader=http_streaming_downloader,
        download_headers_resolver=lambda url: provider_auth_headers_for_url(
            url,
            api_key_service.get_key,
        ),
        local_model_roots=model_roots,
        owned_model_root=noofy_models_dir,
        local_model_identity_store=model_identity_store,
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
            runtime_profile_catalog=runtime_profile_catalog,
            active_runtime_profile_provider=runtime_profile_state.snapshot,
            dependency_env_installer=UvDependencyEnvironmentInstaller(
                wheel_cache_dir=paths.wheel_cache_dir,
                uv_executable=resolve_noofy_uv_executable(),
                log_store=log_store,
            ),
            dependency_lock_store=dependency_lock_store,
            dependency_lock_resolver=UvDependencyLockResolver(
                wheel_cache_dir=paths.wheel_cache_dir,
                work_dir=paths.install_transactions_dir,
                uv_executable=resolve_noofy_uv_executable(),
                log_store=log_store,
            ),
            custom_node_materializer=CustomNodeWorkspaceMaterializer(
                runtime_profile_catalog_provider=runtime_profile_state.catalog,
            ),
            custom_node_source_files_dir_resolver=lambda workflow_id: _workflow_source_files_dir(
                workflow_id,
                workflow_loader=loader,
            ),
            custom_node_source_cache_dir=paths.custom_node_cache_dir,
            dependency_transactions_dir=paths.install_transactions_dir,
            dependency_python_executable_provider=lambda: (
                runtime_manager.environment.python_executable
                if runtime_manager.environment is not None
                else runtime_manager.python_executable
            ),
            log_store=log_store,
        ),
        workspace_smoke_test=runner_smoke_tester.run,
        log_store=log_store,
    )
    runner_process_coordinator = RunnerProcessCoordinator(
        runner_supervisor=supervisor,
        process_supervisor=runner_process_supervisor,
        adapter_factory=comfyui_adapter_factory(
            model_roots=model_roots,
            dashboard_assets_dir=paths.dashboard_assets_dir,
            log_store=log_store,
        ),
        log_store=log_store,
    )
    runtime_storage_maintenance_service = RuntimeStorageMaintenanceService(
        roots=RuntimeStorageRoots.from_paths(paths),
        install_state_store=install_state_store,
        runner_descriptors=supervisor.list_runners,
        log_store=log_store,
        model_reference_validator=model_store.validate_installed_model_references_for_launch,
    )

    # Wire the on_restart callback so the supervisor and adapter learn
    # the new URL after a crash-restart that picked a new port.
    def _reconfigure_adapter() -> None:
        supervisor.update_runner_endpoint(
            CORE_RUNNER_ID,
            runtime_manager.base_url,
            runtime_manager.ws_url,
        )
        supervisor.update_runner_process(
            CORE_RUNNER_ID,
            pid=runtime_manager.managed_process_pid(),
        )

    runtime_manager._on_restart = _reconfigure_adapter
    workflow_runtime_activation = WorkflowRuntimeActivationCoordinator(
        runtime_profile_state=runtime_profile_state,
        runner_supervisor=supervisor,
        runner_process_coordinator=runner_process_coordinator,
        log_store=log_store,
    )

    comfyui_update_service = ComfyUIUpdateService(
        paths=paths,
        runtime_manager=runtime_manager,
        mode=settings.comfyui_runtime_mode,
        developer_override=developer_runtime_override,
        bootstrap_python_executable=settings.comfyui_bootstrap_python_executable,
        torch_cuda_index_url=settings.comfyui_torch_cuda_index_url,
        torch_cpu_index_url=settings.comfyui_torch_cpu_index_url,
        expected_python_version=(
            selected_runtime_profile_variant.python_version
            if selected_runtime_profile_variant is not None
            else None
        ),
        packaged_runtime=settings.packaged_runtime_active and not developer_runtime_override,
        bundled_repo_dir=settings.comfyui_repo_dir,
        bundled_python_executable=RuntimeEnvironment(
            repo_dir=settings.comfyui_repo_dir,
            runtime_dir=settings.runtime_dir,
            bootstrap_python_executable=settings.comfyui_bootstrap_python_executable,
            expected_python_version=(
                selected_runtime_profile_variant.python_version
                if selected_runtime_profile_variant is not None
                else None
            ),
            packaged_runtime=settings.packaged_runtime_active and not developer_runtime_override,
            torch_cuda_index_url=settings.comfyui_torch_cuda_index_url,
            torch_cpu_index_url=settings.comfyui_torch_cpu_index_url,
            log_store=log_store,
            logs_dir=paths.logs_dir,
            cache_dir=paths.cache_dir,
        ).python_executable,
        prepare_runtime_activation=workflow_runtime_activation.prepare,
        commit_runtime_activation=workflow_runtime_activation.commit,
        abort_runtime_activation=workflow_runtime_activation.abort,
        log_store=log_store,
    )
    comfyui_sidecar_service = ComfyUISidecarService(
        runtime_manager=runtime_manager,
        update_service=comfyui_update_service,
        launch_settings_store=launch_settings_store,
        on_endpoint_changed=_reconfigure_adapter,
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
    user_state_service = UserStateService(paths.user_state_dir)
    workflow_library_store = WorkflowLibraryStore(paths.workflow_store_dir / "library")
    history_service = HistoryService(
        store=ActivityLogStore(paths.data_dir / "history" / "activity.db", log_store=log_store),
        workflow_library_store=workflow_library_store,
        workflow_loader=loader,
        log_store=log_store,
    )
    model_availability_service = ModelAvailabilityService(
        model_roots=model_roots,
        noofy_models_dir=noofy_models_dir,
        log_store=log_store,
        local_model_identity_store=model_identity_store,
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
        progress_estimator=WorkflowProgressEstimator(
            timing_store=ProgressTimingStore(
                paths.user_state_dir / "progress-timing",
                log_store=log_store,
            ),
            log_store=log_store,
        ),
        comfyui_update_service=comfyui_update_service,
        comfyui_launch_settings_store=launch_settings_store,
        comfyui_sidecar_service=comfyui_sidecar_service,
        dashboard_authoring=DashboardAuthoringService(
            workflow_store_dir=paths.workflow_packages_store_dir,
            workflow_loader=loader,
            validator=validator,
            log_store=log_store,
            dashboard_overrides_dir=paths.workflow_dashboard_overrides_dir,
            dashboard_assets_dir=paths.dashboard_assets_dir,
        ),
        workflow_exporter=WorkflowExporter(
            workflow_store_dir=paths.workflow_packages_store_dir,
            workflow_loader=loader,
            user_state_service=user_state_service,
            workflow_library_store=workflow_library_store,
            dashboard_assets_dir=paths.dashboard_assets_dir,
            dashboard_overrides_dir=paths.workflow_dashboard_overrides_dir,
            gallery_store=GalleryStore(paths.gallery_outputs_dir, log_store=log_store),
        ),
        model_roots_ref=model_roots,
        model_availability_service=model_availability_service,
        workflow_library_store=workflow_library_store,
        history_service=history_service,
        runtime_storage_maintenance_service=runtime_storage_maintenance_service,
    )
    service.run_runtime_storage_maintenance(reason="startup")
    return service
