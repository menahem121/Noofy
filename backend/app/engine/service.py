import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.engine.adapter import EngineAdapter
from app.diagnostics import DiagnosticsStore, sanitize, sanitize_text
from app.engine.models import (
    BackendHealthReport,
    DiagnosticLogResponse,
    EngineJob,
    ImportModelDownloadJobStart,
    ImportModelDownloadJobStatus,
    JobProgress,
    JobResult,
    LogLevel,
    MachineResourceSnapshot,
    ModelInfo,
    RequiredModelSummary,
    RuntimeBootstrapResult,
    StagedWorkflowImportResponse,
    WorkflowHealthSummary,
    WorkflowValidationResult,
)
from app.gallery import (
    GalleryCaptureService,
    RunSubmissionSnapshot,
)
from app.history import HistoryService
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.install_state import user_facing_install_message
from app.trust import capsule_source_policy
from app.runtime.comfyui.comfyui_sidecar_service import ComfyUISidecarService
from app.runtime.comfyui.comfyui_updates import (
    ComfyUIRebuildRequest,
    ComfyUIUpdateRequest,
    ComfyUIUpdateService,
)
from app.runtime.dependencies.isolation import (
    CapsuleLock,
    InstallStatus,
    InstallState,
)
from app.runtime.comfyui.launch_settings import (
    ComfyUILaunchSettings,
    ComfyUILaunchSettingsResponse,
    ComfyUILaunchSettingsStore,
    ComfyUILaunchSettingsUpdateResult,
)
from app.runtime.manager import RuntimeManager
from app.runtime.memory.memory_governor import (
    LocalMemoryLearningStore,
    MachineMemoryObserver,
    MachineMemorySnapshot,
    MemoryBackend,
    ProcessTreeMemoryObserver,
    RunnerMemoryTelemetryReader,
)
from app.runtime.memory.service import MemoryGovernorService
from app.runtime.memory.resource_monitor import SystemResourceObserver, build_resource_snapshot
from app.runtime.runners.runner_coordinator import RunnerProcessCoordinator
from app.runtime.runners.lifecycle_service import (
    WorkflowRunnerLifecycleService,
    _smoke_execution_fixture_for_capsule,  # re-exported for factory.py / tests
    _workflow_runner_launch_spec,          # re-exported for factory.py
    _workflow_source_files_dir,            # re-exported for factory.py
)
from app.runtime.storage.storage_gc import RuntimeStorageGarbageCollector, RuntimeStorageRoots
from app.runtime.runners.supervisor import (
    CORE_RUNNER_ID,
    JobRunnerNotFoundError,
    RunnerDescriptor,
    RunnerStatus,
    RunnerSupervisor,
)
from app.runs.job_service import RunJobService
from app.runs.orchestrator import RunOrchestrator
from app.runs.result_service import RunResultService
from app.workflows.authoring import DashboardAuthoringError, DashboardAuthoringService
from app.workflows.bindings import apply_input_bindings
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.exporter import WorkflowExportError, WorkflowExporter
from app.workflows.importer import ImportedWorkflowPackageStore, NoofyImportError
from app.workflows.library import WorkflowLibraryStore, WorkflowMetadataUpdate
from app.workflows.import_orchestrator import (
    IMPORT_SESSION_TTL,
    ImportSessionExpiredError,
    WorkflowImportOrchestrator,
    _ImportModelDownloadJob,  # temporary migration re-export for tests
    _PendingWorkflowImport,  # temporary migration re-export for tests
)
from app.workflows.library_service import WorkflowLibraryService
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.model_availability import ModelAvailabilityService
from app.workflows.package import WorkflowPackage
from app.workflows.validator import WorkflowPackageValidator


class EngineService:
    def __init__(
        self,
        workflow_loader: WorkflowPackageLoader,
        workflow_validator: WorkflowPackageValidator,
        runner_supervisor: RunnerSupervisor,
        runtime_manager: RuntimeManager,
        log_store: DiagnosticsStore,
        capsule_loader: CapsuleLockLoader | None = None,
        capsule_installer: CapsuleInstaller | None = None,
        runner_process_coordinator: RunnerProcessCoordinator | None = None,
        imported_package_store: ImportedWorkflowPackageStore | None = None,
        memory_observer: MachineMemoryObserver | None = None,
        process_tree_memory_observer: ProcessTreeMemoryObserver | None = None,
        runner_memory_telemetry_reader: RunnerMemoryTelemetryReader | None = None,
        memory_learning_store: LocalMemoryLearningStore | None = None,
        comfyui_update_service: ComfyUIUpdateService | None = None,
        comfyui_launch_settings_store: ComfyUILaunchSettingsStore | None = None,
        comfyui_sidecar_service: ComfyUISidecarService | None = None,
        resource_observer: SystemResourceObserver | None = None,
        dashboard_authoring: DashboardAuthoringService | None = None,
        workflow_exporter: WorkflowExporter | None = None,
        model_roots_ref: list[Path] | None = None,
        model_availability_service: ModelAvailabilityService | None = None,
        gallery_capture_service: GalleryCaptureService | None = None,
        workflow_library_store: WorkflowLibraryStore | None = None,
        workflow_library_service: WorkflowLibraryService | None = None,
        workflow_import_orchestrator: WorkflowImportOrchestrator | None = None,
        workflow_runner_lifecycle_service: WorkflowRunnerLifecycleService | None = None,
        run_job_service: RunJobService | None = None,
        run_orchestrator: RunOrchestrator | None = None,
        run_result_service: RunResultService | None = None,
        history_service: HistoryService | None = None,
        credential_resolver=None,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.workflow_validator = workflow_validator
        self.runner_supervisor = runner_supervisor
        self.runtime_manager = runtime_manager
        self.log_store = log_store
        self.capsule_loader = capsule_loader
        self.capsule_installer = capsule_installer
        self.runner_process_coordinator = runner_process_coordinator
        self.imported_package_store = imported_package_store
        self._memory_observer = memory_observer
        self.memory_learning_store = memory_learning_store
        self.comfyui_update_service = comfyui_update_service
        self.comfyui_launch_settings_store = comfyui_launch_settings_store
        self.resource_observer = resource_observer or SystemResourceObserver()
        self.dashboard_authoring = dashboard_authoring
        self.workflow_exporter = workflow_exporter
        self.model_roots_ref = model_roots_ref
        model_roots = model_roots_ref or [settings.comfyui_models_dir]
        self.model_availability_service = model_availability_service or ModelAvailabilityService(
            model_roots=model_roots,
            noofy_models_dir=model_roots[0],
            log_store=log_store,
        )
        self.gallery_capture_service = gallery_capture_service
        self.workflow_library_store = workflow_library_store
        self.history_service = history_service
        self.model_ownership_store = None
        if self.dashboard_authoring is not None:
            self.dashboard_authoring.object_info_provider = self._fetch_core_object_info_for_authoring
        self.workflow_library_service: WorkflowLibraryService = workflow_library_service or WorkflowLibraryService(
            workflow_loader=self.workflow_loader,
            model_availability_service=self.model_availability_service,
            log_store=self.log_store,
            workflow_library_store=self.workflow_library_store,
            imported_package_store=self.imported_package_store,
            history_service=self.history_service,
        )
        if self.imported_package_store is not None:
            self.workflow_import_orchestrator: WorkflowImportOrchestrator | None = (
                workflow_import_orchestrator
                or WorkflowImportOrchestrator(
                    imported_package_store=self.imported_package_store,
                    workflow_library_service=self.workflow_library_service,
                    model_availability_service=self.model_availability_service,
                    log_store=self.log_store,
                    model_ownership_store=self.model_ownership_store,
                    history_service=self.history_service,
                )
            )
        else:
            self.workflow_import_orchestrator = workflow_import_orchestrator
        self.run_job_service: RunJobService = run_job_service or RunJobService(
            runner_supervisor=runner_supervisor,
            log_store=log_store,
            workflow_loader=self.workflow_loader,
        )
        self.model_availability_service.cleanup_interrupted_downloads()
        self.comfyui_sidecar_service = comfyui_sidecar_service or ComfyUISidecarService(
            runtime_manager=runtime_manager,
            update_service=comfyui_update_service,
            launch_settings_store=comfyui_launch_settings_store,
            on_endpoint_changed=self._reconfigure_core_runner_endpoint,
        )

        # Shared state dicts — passed by reference to sub-services so they
        # all see the same live data without coordinator coupling.
        self._job_workflows: dict[str, str] = {}
        self._job_started_at: dict[str, datetime] = {}
        self._job_run_requests: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
        self._job_run_snapshots: dict[str, RunSubmissionSnapshot] = {}

        # Memory-governor stateful service — owns retry roots, queued run
        # queue, sampling, and learning-store updates.
        self.memory_service = MemoryGovernorService(
            runner_supervisor=runner_supervisor,
            runner_process_coordinator=runner_process_coordinator,
            log_store=log_store,
            memory_observer=memory_observer,
            process_tree_memory_observer=process_tree_memory_observer or ProcessTreeMemoryObserver(),
            runner_memory_telemetry_reader=runner_memory_telemetry_reader or RunnerMemoryTelemetryReader(),
            memory_learning_store=memory_learning_store,
            job_workflows=self._job_workflows,
            job_run_requests=self._job_run_requests,
            job_run_snapshots=self._job_run_snapshots,
        )
        self.run_job_service.queued_workflow_run_progress = self.memory_service.queued_workflow_run_progress
        self.run_job_service.cancel_queued_workflow_run = self.memory_service.cancel_queued_workflow_run
        # Temporary migration alias used by diagnostics and tests.
        self.memory_observation = self.memory_service.memory_observation

        self.run_result_service: RunResultService = run_result_service or RunResultService(
            job_service=self.run_job_service,
            log_store=log_store,
            job_workflows=self._job_workflows,
            job_started_at=self._job_started_at,
            job_run_snapshots=self._job_run_snapshots,
            finish_memory_sampling=self.memory_service.finish_job_sampling,
            record_memory_observation=self.memory_service.record_local_memory_observation,
            maybe_retry_after_memory_cleanup=self.memory_service.maybe_retry_after_memory_cleanup,
            gallery_capture_service=self.gallery_capture_service,
            workflow_library_store=self.workflow_library_store,
            history_service=self.history_service,
        )
        self.run_orchestrator: RunOrchestrator = run_orchestrator or RunOrchestrator(
            workflow_loader=self.workflow_loader,
            runner_supervisor=self.runner_supervisor,
            log_store=self.log_store,
            memory_observer=self.memory_observer,
            job_workflows=self._job_workflows,
            job_started_at=self._job_started_at,
            job_run_requests=self._job_run_requests,
            job_run_snapshots=self._job_run_snapshots,
            memory_retry_roots=self.memory_service._memory_retry_roots,
            queued_workflow_runs=self.memory_service.queued_workflow_runs,
            validate_package=self._validate_package,
            unavailable_package_reason=self._imported_workflow_without_preparable_capsule,
            apply_input_bindings=self._apply_input_bindings,
            ensure_workflow_runner=self._ensure_workflow_runner_for_run,
            workflow_run_memory_decision=self.memory_service.decision_for_workflow_run,
            evict_idle_runners=self.memory_service.evict_idle_runners_for_workflow_run,
            memory_status_payload=self.memory_service.memory_status_payload,
            record_memory_metric=self.memory_service.record_metric,
            start_memory_sampling=self.memory_service.start_job_sampling,
            history_service=self.history_service,
            credential_resolver=credential_resolver,
            api_nodes_unavailable_reason=self._api_nodes_unavailable_reason,
        )
        # Wire the retry callback now that RunOrchestrator exists.
        self.memory_service.run_workflow = self.run_orchestrator.run_workflow

        self.workflow_runner_lifecycle_service: WorkflowRunnerLifecycleService = (
            workflow_runner_lifecycle_service
            or WorkflowRunnerLifecycleService(
                workflow_loader=self.workflow_loader,
                runner_supervisor=self.runner_supervisor,
                log_store=self.log_store,
                capsule_loader=self.capsule_loader,
                capsule_installer=self.capsule_installer,
                runner_process_coordinator=self.runner_process_coordinator,
                runtime_manager=self.runtime_manager,
                memory_service=self.memory_service,
                imported_package_store=self.imported_package_store,
                workflow_summary=self.workflow_library_service.workflow_summary,
            )
        )

    @property
    def memory_observer(self) -> MachineMemoryObserver | None:
        memory_service = getattr(self, "memory_service", None)
        if memory_service is not None:
            return memory_service.memory_observer
        return self._memory_observer

    @memory_observer.setter
    def memory_observer(self, value: MachineMemoryObserver | None) -> None:
        self._memory_observer = value
        memory_service = getattr(self, "memory_service", None)
        if memory_service is not None:
            memory_service.memory_observer = value

    # Temporary migration proxies while tests move to WorkflowImportOrchestrator.
    @property
    def _pending_workflow_imports(self) -> dict:
        if self.workflow_import_orchestrator is None:
            return {}
        return self.workflow_import_orchestrator._pending_workflow_imports

    @property
    def _import_model_download_jobs(self) -> dict:
        if self.workflow_import_orchestrator is None:
            return {}
        return self.workflow_import_orchestrator._import_model_download_jobs

    def _pending_import_or_raise(self, import_session_id: str) -> _PendingWorkflowImport:
        if self.workflow_import_orchestrator is None:
            raise KeyError("Workflow import is not configured.")
        return self.workflow_import_orchestrator._pending_import_or_raise(import_session_id)

    def list_workflows(self) -> list[dict[str, object]]:
        return self.workflow_library_service.list_workflows()

    def workflow_details(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_library_service.workflow_details(workflow_id)

    def update_workflow_metadata(
        self,
        workflow_id: str,
        update: WorkflowMetadataUpdate,
    ) -> dict[str, object]:
        return self.workflow_library_service.update_workflow_metadata(workflow_id, update)

    def remove_workflow(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_library_service.remove_workflow(workflow_id)

    def export_workflow_comfyui_graph(
        self,
        workflow_id: str,
        input_values: dict[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        if self.workflow_exporter is not None:
            try:
                if input_values is None:
                    return self.workflow_exporter.export_comfyui_graph(workflow_id)
                return self.workflow_exporter.export_comfyui_graph(
                    workflow_id,
                    input_values=input_values,
                )
            except WorkflowExportError as exc:
                raise ValueError(str(exc)) from exc
        return self.workflow_library_service.export_workflow_comfyui_graph(workflow_id)

    def list_runners(self) -> list[RunnerDescriptor]:
        return self.runner_supervisor.list_runners()

    def apply_model_folder_settings(
        self,
        noofy_models_dir: Path,
        external_comfyui_models_dir: Path | None = None,
        *,
        extra_model_paths_config: Path | None = None,
    ) -> None:
        model_roots = [noofy_models_dir]
        if external_comfyui_models_dir is not None:
            model_roots.append(external_comfyui_models_dir)
        if self.model_roots_ref is not None:
            self.model_roots_ref[:] = model_roots
        if self.capsule_installer is not None:
            model_store = getattr(self.capsule_installer, "model_store", None)
            if model_store is not None and hasattr(model_store, "owned_model_root"):
                model_store.owned_model_root = noofy_models_dir
            if model_store is not None and hasattr(model_store, "local_model_roots"):
                model_store.local_model_roots = model_roots
        if self.model_availability_service is not None:
            self.model_availability_service.configure_model_roots(
                model_roots=model_roots,
                noofy_models_dir=noofy_models_dir,
            )
        adapter = self.runner_supervisor.get_adapter(CORE_RUNNER_ID)
        configure_model_roots = getattr(adapter, "configure_model_roots", None)
        if callable(configure_model_roots):
            configure_model_roots(model_roots)
        if extra_model_paths_config is not None:
            self.runtime_manager.set_managed_extra_model_paths_config(extra_model_paths_config)
        self.runtime_manager.set_managed_model_roots(model_roots)
        self.log_store.add(
            "info",
            "Model folder settings applied",
            "engine.service",
            details={
                "noofy_models_dir": str(noofy_models_dir),
                "external_comfyui_models_dir": str(external_comfyui_models_dir)
                if external_comfyui_models_dir
                else None,
            },
        )

    def workflow_status(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service.workflow_status(workflow_id)

    def cancel_preparation(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service.cancel_preparation(workflow_id)

    def diagnostics_payload(
        self,
        *,
        workflow_id: str | None = None,
        include_developer_details: bool = False,
        limit: int = 200,
    ) -> dict[str, object]:
        events = self.log_store.list_events(limit=limit).events
        if workflow_id is not None:
            events = [event for event in events if event.workflow_id == workflow_id]
        return {
            "events": [
                _diagnostic_event_payload(event, include_developer_details=include_developer_details)
                for event in events[-limit:]
            ]
        }

    def storage_diagnostics_payload(self) -> dict[str, object]:
        if self.capsule_installer is None:
            states: list[InstallState] = []
        else:
            states = self.capsule_installer.install_state_store.list_states()
        collector = RuntimeStorageGarbageCollector(
            roots=RuntimeStorageRoots.from_paths(settings.paths),
            install_states=states,
            runner_descriptors=self.runner_supervisor.list_runners(),
            log_store=self.log_store,
        )
        return collector.build_reference_index().to_diagnostics()

    def trust_policy_payload(self) -> dict[str, object]:
        verifier_payload: dict[str, object]
        if self.imported_package_store is None:
            verifier_payload = {
                "schema_version": "0.1.0",
                "signature_payload_schema_version": "0.1.0",
                "trusted_key_count": 0,
                "trusted_keys": [],
                "trust_levels": {},
            }
        else:
            verifier_payload = self.imported_package_store.trust_verifier.policy_payload()
        return {
            **verifier_payload,
            "imported_trusted_claims_require_verified_evidence": True,
            "secrets_exposed": False,
        }

    def get_workflow_package(self, workflow_id: str) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        return package.model_dump()

    def import_workflow_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ) -> dict[str, object]:
        if self.workflow_import_orchestrator is None:
            raise NoofyImportError("Workflow import is not configured.")
        return self.workflow_import_orchestrator.import_workflow_archive(
            data,
            original_filename=original_filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )

    def preview_workflow_import(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ) -> StagedWorkflowImportResponse:
        if self.workflow_import_orchestrator is None:
            raise NoofyImportError("Workflow import is not configured.")
        return self.workflow_import_orchestrator.preview_workflow_import(
            data,
            original_filename=original_filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )

    def start_missing_model_download_for_import(
        self, import_session_id: str
    ) -> ImportModelDownloadJobStart:
        if self.workflow_import_orchestrator is None:
            raise KeyError("Workflow import is not configured.")
        return self.workflow_import_orchestrator.start_missing_model_download_for_import(import_session_id)

    def import_model_download_status(
        self, import_session_id: str, job_id: str
    ) -> ImportModelDownloadJobStatus:
        if self.workflow_import_orchestrator is None:
            raise KeyError("Workflow import is not configured.")
        return self.workflow_import_orchestrator.import_model_download_status(import_session_id, job_id)

    def cancel_import_model_download_job(
        self, import_session_id: str, job_id: str
    ) -> ImportModelDownloadJobStatus:
        if self.workflow_import_orchestrator is None:
            raise KeyError("Workflow import is not configured.")
        return self.workflow_import_orchestrator.cancel_import_model_download_job(import_session_id, job_id)

    def commit_workflow_import(
        self,
        import_session_id: str,
        *,
        duplicate_action: str | None = None,
    ) -> StagedWorkflowImportResponse:
        if self.workflow_import_orchestrator is None:
            raise NoofyImportError("Workflow import is not configured.")
        return self.workflow_import_orchestrator.commit_workflow_import(
            import_session_id,
            duplicate_action=duplicate_action,
        )

    def cancel_workflow_import(self, import_session_id: str) -> dict[str, object]:
        if self.workflow_import_orchestrator is None:
            return {"import_session_id": import_session_id, "status": "not_found"}
        return self.workflow_import_orchestrator.cancel_workflow_import(import_session_id)

    def model_availability_summary(self, workflow_id: str) -> RequiredModelSummary:
        return self.workflow_library_service.model_availability_summary(workflow_id)

    def model_availability_summary_for_package(
        self, package: WorkflowPackage
    ) -> RequiredModelSummary:
        return self.workflow_library_service.model_availability_summary_for_package(package)

    # ------------------------------------------------------------------
    # Capsule install pipeline (Phase 3)
    # ------------------------------------------------------------------

    def get_install_state(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service.get_install_state(workflow_id)

    def get_install_state_developer_details(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service.get_install_state_developer_details(workflow_id)

    async def prepare_workflow(self, workflow_id: str) -> dict[str, object]:
        return await self.workflow_runner_lifecycle_service.prepare_workflow(workflow_id)

    async def start_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        return await self.workflow_runner_lifecycle_service.start_workflow_runner(workflow_id)

    async def _ensure_workflow_runner_for_run(
        self, package: WorkflowPackage
    ) -> str | None:
        """Prepare and bind a workflow runner before running custom-node capsules."""
        workflow_id = package.metadata.id
        capsule_lock = self.workflow_runner_lifecycle_service.preparable_capsule_lock(
            workflow_id
        )
        if capsule_lock is None or not capsule_lock.custom_nodes:
            return None

        runner = self.runner_supervisor.runner_for_workflow(workflow_id)
        if runner is not None and runner.status in {
            RunnerStatus.READY,
            RunnerStatus.IDLE,
            RunnerStatus.IDLE_WARM,
        }:
            return None

        install = self.workflow_runner_lifecycle_service.get_install_state(workflow_id)
        if install.get("status") != InstallStatus.READY.value:
            self.log_store.add(
                "info",
                "Preparing workflow runner before run",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "install_status": install.get("status"),
                },
            )
            install = await self.workflow_runner_lifecycle_service.prepare_workflow(
                workflow_id
            )

        if install.get("status") != InstallStatus.READY.value:
            return _workflow_runner_unavailable_message(install)

        start = await self.workflow_runner_lifecycle_service.start_workflow_runner(
            workflow_id
        )
        if _workflow_runner_start_needs_reprepare(start):
            self.log_store.add(
                "info",
                "Re-preparing workflow runner after stale runtime artifacts",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": start.get("error"),
                },
            )
            install = await self.workflow_runner_lifecycle_service.prepare_workflow(
                workflow_id
            )
            if install.get("status") != InstallStatus.READY.value:
                return _workflow_runner_unavailable_message(install)
            start = await self.workflow_runner_lifecycle_service.start_workflow_runner(
                workflow_id
            )
        if start.get("status") not in {
            RunnerStatus.READY.value,
            RunnerStatus.IDLE.value,
            RunnerStatus.IDLE_WARM.value,
        }:
            return _workflow_runner_unavailable_message(start)
        return None

    async def handoff_next_queued_runner_start(
        self,
        *,
        released_runner_id: str | None = None,
    ) -> dict[str, object] | None:
        return await self.workflow_runner_lifecycle_service.handoff_next_queued_runner_start(
            released_runner_id=released_runner_id,
        )

    def cancel_queued_runner_start(self, queue_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service.cancel_queued_runner_start(queue_id)

    async def handoff_queued_workflow_run(self, queue_id: str) -> dict[str, object] | EngineJob | WorkflowValidationResult | None:
        return await self.memory_service.handoff_queued_workflow_run(queue_id)

    async def stop_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        return await self.workflow_runner_lifecycle_service.stop_workflow_runner(workflow_id)

    def open_workflow_runner_lease(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service.open_workflow_runner_lease(workflow_id)

    def close_workflow_runner_lease(self, workflow_id: str, lease_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service.close_workflow_runner_lease(
            workflow_id,
            lease_id,
        )

    # ------------------------------------------------------------------
    # Dashboard authoring (M2)
    # ------------------------------------------------------------------

    def get_bindable_inputs(self, workflow_id: str) -> dict[str, Any]:
        if self.dashboard_authoring is None:
            raise KeyError(f"Dashboard authoring not configured: {workflow_id}")
        try:
            return self.dashboard_authoring.get_bindable_inputs(
                workflow_id,
                object_info=self._fetch_core_object_info_for_authoring(workflow_id),
            )
        except DashboardAuthoringError as exc:
            raise KeyError(str(exc)) from exc

    def get_unresolved_inputs(self, workflow_id: str) -> dict[str, Any]:
        if self.dashboard_authoring is None:
            raise KeyError(f"Dashboard authoring not configured: {workflow_id}")
        try:
            return self.dashboard_authoring.get_unresolved_inputs(workflow_id)
        except DashboardAuthoringError as exc:
            raise KeyError(str(exc)) from exc

    def validate_dashboard(self, workflow_id: str, inputs: list, dashboard: dict) -> dict[str, Any]:
        if self.dashboard_authoring is None:
            raise KeyError(f"Dashboard authoring not configured: {workflow_id}")
        try:
            return self.dashboard_authoring.validate_dashboard(workflow_id, inputs, dashboard)
        except DashboardAuthoringError as exc:
            raise ValueError(str(exc)) from exc

    def save_dashboard(self, workflow_id: str, inputs: list, dashboard: dict) -> dict[str, Any]:
        if self.dashboard_authoring is None:
            raise KeyError(f"Dashboard authoring not configured: {workflow_id}")
        try:
            return self.dashboard_authoring.save_dashboard(workflow_id, inputs, dashboard)
        except DashboardAuthoringError as exc:
            raise ValueError(str(exc)) from exc

    def export_workflow_archive(
        self,
        workflow_id: str,
        input_values: dict[str, Any] | None = None,
        export_metadata: dict[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        """Return (archive_bytes, suggested_filename) for download."""
        if self.workflow_exporter is None:
            raise KeyError(f"Workflow export not configured: {workflow_id}")
        try:
            if input_values is None and export_metadata is None:
                return self.workflow_exporter.export_archive(workflow_id)
            return self.workflow_exporter.export_archive(
                workflow_id,
                input_values=input_values,
                export_metadata=export_metadata,
            )
        except WorkflowExportError as exc:
            raise ValueError(str(exc)) from exc

    async def upload_workflow_image(
        self, workflow_id: str, filename: str, data: bytes, content_type: str
    ) -> dict[str, str]:
        """Upload or stage an image through the workflow-selected engine adapter."""
        return await self.run_job_service.upload_workflow_image(
            workflow_id,
            filename,
            data,
            content_type,
        )

    async def validate_workflow(self, workflow_id: str) -> WorkflowValidationResult:
        return await self.run_orchestrator.validate_workflow(workflow_id)

    async def run_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        options: dict[str, Any],
        *,
        memory_retry_after_cleanup: bool = False,
        output_preferences_snapshot: dict[str, dict[str, Any]] | None = None,
        run_submission_snapshot: RunSubmissionSnapshot | None = None,
    ):
        return await self.run_orchestrator.run_workflow(
            workflow_id,
            inputs,
            options,
            memory_retry_after_cleanup=memory_retry_after_cleanup,
            output_preferences_snapshot=output_preferences_snapshot,
            run_submission_snapshot=run_submission_snapshot,
        )

    async def get_progress(self, job_id: str) -> JobProgress:
        return await self.run_job_service.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        return await self.run_job_service.cancel_job(job_id)

    async def get_result(self, job_id: str) -> JobResult | EngineJob:
        self.run_result_service.gallery_capture_service = self.gallery_capture_service
        self.run_result_service.workflow_library_store = self.workflow_library_store
        return await self.run_result_service.get_result(job_id)

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        return await self.run_job_service.fetch_output(job_id, filename, subfolder, output_type)

    async def stream_progress_events(self, job_id: str):
        async for event in self.run_result_service.stream_progress_events(job_id):
            yield event

    async def list_available_models(self):
        adapter = self._core_adapter()
        return await adapter.list_available_models()

    def list_logs(self, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.log_store.list_events(level=level, limit=limit)

    def list_job_logs(self, job_id: str, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.run_job_service.list_job_logs(job_id, level=level, limit=limit)

    async def health(self) -> BackendHealthReport:
        packages = self.workflow_loader.list_packages()
        workflow_summaries: list[WorkflowHealthSummary] = []

        for package in packages:
            runner = self.runner_supervisor.acquire_runner(package)
            adapter = self.runner_supervisor.get_adapter(runner.runner_id)
            validation = await self._validate_package(package, adapter)
            workflow_summaries.append(
                WorkflowHealthSummary(
                    workflow_id=package.metadata.id,
                    valid=validation.valid,
                    missing_model_count=len(validation.missing_models),
                    error_count=len(validation.errors),
                )
            )

        comfyui_status = await self.runtime_manager.status()
        status = "ok" if comfyui_status.reachable and all(item.valid for item in workflow_summaries) else "degraded"

        return BackendHealthReport(
            status=status,
            comfyui=comfyui_status,
            workflow_package_count=len(packages),
            workflows=workflow_summaries,
            latest_error=self.log_store.latest_error(),
        )

    async def runtime_status(self):
        return await self.runtime_manager.status()

    def resource_snapshot(self) -> MachineResourceSnapshot:
        cpu_metric = self.resource_observer.cpu_metric()
        memory_snapshot = (
            self.memory_observer.snapshot()
            if self.memory_observer is not None
            else MachineMemorySnapshot(
                available=False,
                backend=MemoryBackend.UNKNOWN,
                error="memory_observer_unavailable",
            )
        )
        return build_resource_snapshot(memory_snapshot, cpu_metric=cpu_metric)

    def comfyui_launch_settings(self) -> ComfyUILaunchSettingsResponse:
        return self.comfyui_sidecar_service.launch_settings()

    async def update_comfyui_launch_settings(
        self,
        request: ComfyUILaunchSettings,
    ) -> ComfyUILaunchSettingsUpdateResult:
        return await self.comfyui_sidecar_service.update_launch_settings(request)

    async def start_comfyui(self):
        return await self.comfyui_sidecar_service.start()

    async def stop_comfyui(self):
        return await self.comfyui_sidecar_service.stop()

    async def bootstrap_comfyui_runtime(self) -> RuntimeBootstrapResult:
        return await self.comfyui_sidecar_service.bootstrap_runtime()

    async def comfyui_versions(self, *, check_upstream: bool = False):
        return await self.comfyui_sidecar_service.versions(check_upstream=check_upstream)

    async def update_comfyui(self, request: ComfyUIUpdateRequest):
        return await self.comfyui_sidecar_service.update(request)

    async def rebuild_comfyui(self, request: ComfyUIRebuildRequest):
        return await self.comfyui_sidecar_service.rebuild(request)

    def comfyui_update_status(self):
        return self.comfyui_sidecar_service.update_status()

    async def shutdown(self) -> None:
        if self.runner_process_coordinator is not None:
            await self.runner_process_coordinator.stop_all_runners()
        await self.comfyui_sidecar_service.shutdown()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _validate_package(
        self,
        package: WorkflowPackage,
        adapter: EngineAdapter,
    ) -> WorkflowValidationResult:
        structure_result = self.workflow_validator.validate_structure(package)
        if not structure_result.valid:
            return structure_result

        available_models = self._available_model_keys(await adapter.list_available_models())
        missing_models = self.workflow_validator.validate_models(package, available_models)
        if package.import_metadata is not None and package.required_models:
            availability = self.model_availability_summary_for_package(package)
            verified_keys = {
                (model.folder, model.filename)
                for model in availability.models
                if model.status == "available"
            }
            missing_models = self.workflow_validator.validate_models(package, verified_keys)
        return self.workflow_validator.combine(package, structure_result, missing_models)

    def _install_payload(
        self,
        workflow_id: str,
        state: InstallState,
        *,
        capsule_lock: CapsuleLock | None = None,
    ) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "capsule_fingerprint": state.capsule_fingerprint,
            "status": state.status.value,
            "user_facing_message": user_facing_install_message(state.status),
            "installed_at": state.installed_at,
            "last_used_at": state.last_used_at,
            "dependency_env_path": state.dependency_env_path,
            "runner_workspace_path": state.runner_workspace_path,
            "smoke_test_status": state.smoke_test_status.value,
            "smoke_test_report": state.smoke_test_report.model_dump(mode="json"),
            "last_error": state.last_error,
            "developer_details_available": state.last_error is not None or bool(state.smoke_test_report.model_dump(mode="json")),
            "source_policy": capsule_source_policy(capsule_lock).model_dump(mode="json")
            if capsule_lock is not None
            else None,
        }

    def _workflow_summary(self, package: WorkflowPackage) -> dict[str, object]:
        return self.workflow_library_service.workflow_summary(package)

    def _phase3_verified_capsule_lock(self, workflow_id: str) -> CapsuleLock | None:
        return self.workflow_runner_lifecycle_service._preparable_capsule_lock(workflow_id)

    def _preparable_capsule_lock(self, workflow_id: str) -> CapsuleLock | None:
        return self.workflow_runner_lifecycle_service._preparable_capsule_lock(workflow_id)

    def _imported_workflow_without_preparable_capsule(
        self, package: WorkflowPackage
    ) -> str | None:
        return self.workflow_runner_lifecycle_service.imported_workflow_without_preparable_capsule(package)

    def _unsupported_install_payload(self, workflow_id: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service._unsupported_install_payload(workflow_id)

    def _unsupported_runner_payload(self, workflow_id: str, reason: str) -> dict[str, object]:
        return self.workflow_runner_lifecycle_service._unsupported_runner_payload(workflow_id, reason)

    def _adapter_for_job(self, job_id: str) -> EngineAdapter:
        try:
            return self.runner_supervisor.adapter_for_job(job_id)
        except JobRunnerNotFoundError:
            # The job was either submitted before the registry existed or the
            # registry was reset. Fall back to the core runner so existing API
            # responses keep working while later phases tighten this contract.
            return self._core_adapter()

    def _core_adapter(self) -> EngineAdapter:
        descriptor = self.runner_supervisor.core_runner()
        return self.runner_supervisor.get_adapter(descriptor.runner_id)

    def _available_model_keys(self, models: list[ModelInfo]) -> set[tuple[str, str]]:
        return {(model.folder, model.filename) for model in models}

    def _fetch_core_object_info_for_authoring(self, workflow_id: str) -> dict[str, Any] | None:
        try:
            adapter = self._core_adapter()
        except Exception as exc:
            self.log_store.add(
                "debug",
                "ComfyUI object info unavailable for dashboard authoring",
                "workflow.authoring",
                workflow_id=workflow_id,
                details={"reason": str(exc)},
            )
            return None

        base_url = getattr(adapter, "base_url", None)
        if not isinstance(base_url, str) or not base_url:
            return None

        try:
            with httpx.Client(timeout=2.0) as client:
                response = client.get(f"{base_url.rstrip('/')}/object_info")
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            self.log_store.add(
                "debug",
                "Failed to enrich bindable inputs from ComfyUI object info",
                "workflow.authoring",
                workflow_id=workflow_id,
                details={"base_url": base_url, "error": str(exc)},
            )
            return None

        if not isinstance(payload, dict):
            self.log_store.add(
                "warning",
                "ComfyUI object info returned an unexpected payload",
                "workflow.authoring",
                workflow_id=workflow_id,
                details={"payload_type": type(payload).__name__},
            )
            return None

        self.log_store.add(
            "debug",
            "Enriched bindable inputs from ComfyUI object info",
            "workflow.authoring",
            workflow_id=workflow_id,
            details={"node_definition_count": len(payload)},
        )
        return payload

    def _apply_input_bindings(self, package: WorkflowPackage, inputs: dict[str, Any]) -> dict[str, Any]:
        return apply_input_bindings(package, inputs)

    async def _api_nodes_unavailable_reason(
        self,
        package: WorkflowPackage,
        adapter: EngineAdapter,
    ) -> str | None:
        runtime_manager = getattr(self, "runtime_manager", None)
        if runtime_manager is not None:
            disabled = bool(getattr(runtime_manager, "api_nodes_disabled", False))
            extra_args = getattr(runtime_manager, "managed_extra_args", [])
            if "--disable-api-nodes" in extra_args:
                disabled = True
            if disabled:
                return "ComfyUI API nodes are disabled for the active runtime."

        base_url = getattr(adapter, "base_url", None)
        if not isinstance(base_url, str) or not base_url:
            return None
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{base_url.rstrip('/')}/object_info")
                response.raise_for_status()
                object_info = response.json()
        except Exception:
            return None
        if not isinstance(object_info, dict):
            return None

        missing_node_types = sorted(
            {
                str(node.get("class_type"))
                for node in package.comfyui_graph.values()
                if isinstance(node, dict)
                and isinstance(node.get("class_type"), str)
                and node.get("class_type") not in object_info
            }
        )
        if missing_node_types:
            return (
                "ComfyUI Partner/API node support is unavailable for this workflow. "
                f"Missing node types: {', '.join(missing_node_types[:5])}."
            )
        return None

    def _reconfigure_core_runner_endpoint(self) -> None:
        try:
            descriptor = self.runner_supervisor.core_runner()
        except LookupError:
            return
        self.runner_supervisor.update_runner_endpoint(
            descriptor.runner_id,
            self.runtime_manager.base_url,
            self.runtime_manager.ws_url,
        )

    def memory_governor_metrics(self) -> dict[str, int]:
        return self.memory_service.memory_governor_metrics()

    def _record_memory_governor_metric(self, name: str) -> None:
        self.memory_service.record_metric(name)

    def _memory_status_payload(
        self,
        decision: Any,
        *,
        queue_id: str | None = None,
    ) -> dict[str, Any]:
        return self.memory_service.memory_status_payload(decision, queue_id=queue_id)


def _install_developer_details(state: InstallState) -> dict[str, object]:
    details: dict[str, object] = {}
    if state.last_error is not None:
        details["last_error"] = state.last_error
    details["smoke_test_report"] = state.smoke_test_report.model_dump(mode="json")
    details["dependency_env_path"] = state.dependency_env_path
    details["runner_workspace_path"] = state.runner_workspace_path
    return _redact_diagnostic_details(details)


def _diagnostic_event_payload(event, *, include_developer_details: bool) -> dict[str, object]:
    details = _redact_diagnostic_details(event.details)
    payload: dict[str, object] = {
        "id": event.id,
        "timestamp": event.timestamp.isoformat(),
        "level": event.level,
        "message": event.message if include_developer_details else _redact_user_private_paths(event.message),
        "source": event.source,
        "workflow_id": event.workflow_id,
        "job_id": event.job_id,
        "correlation_ids": _diagnostic_correlation_ids(event, details),
    }
    if include_developer_details:
        payload["developer_details"] = details
    return payload


def _diagnostic_correlation_ids(event, details: dict[str, object]) -> dict[str, object]:
    correlations: dict[str, object] = {}
    if event.workflow_id is not None:
        correlations["workflow_id"] = event.workflow_id
    if event.job_id is not None:
        correlations["job_id"] = event.job_id
    for key in (
        "runner_id",
        "install_transaction_id",
        "transaction_id",
        "queue_id",
        "memory_decision_id",
    ):
        value = details.get(key)
        if value is not None:
            correlations[key] = value
    return correlations


def _workflow_runner_unavailable_message(payload: dict[str, object]) -> str:
    for key in ("last_error", "error", "user_facing_message"):
        message = payload.get(key)
        if isinstance(message, str) and message.strip():
            return message
    status = payload.get("status") or payload.get("install_status")
    if isinstance(status, str) and status.strip():
        return f"Workflow runner is not ready: {status}."
    return "Workflow runner is not ready."


def _workflow_runner_start_needs_reprepare(payload: dict[str, object]) -> bool:
    error = payload.get("error")
    if not isinstance(error, str):
        return False
    return error.startswith("Prepared runtime artifact ")


def _redact_diagnostic_details(value):
    value = sanitize(value)
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _is_secret_key(key) else _redact_diagnostic_details(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_diagnostic_details(item) for item in value]
    if isinstance(value, str) and _looks_sensitive(value):
        return "[redacted]"
    if isinstance(value, str):
        return _redact_user_private_paths(sanitize_text(value))
    return value


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in ("token", "secret", "authorization", "api_key", "signed_url"))


def _looks_sensitive(value: str) -> bool:
    lowered = value.lower()
    return (
        "authorization:" in lowered
        or "token=" in lowered
        or "x-amz-signature=" in lowered
        or "x-goog-signature=" in lowered
    )


_LOCAL_PATH_PATTERNS = (
    re.compile(r"/Users/[^/\s]+(?:/[^\s,;)]+)*"),
    re.compile(r"/home/[^/\s]+(?:/[^\s,;)]+)*"),
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s]+(?:\\[^\s,;)]+)*"),
)


def _redact_user_private_paths(value: str) -> str:
    redacted = value
    for pattern in _LOCAL_PATH_PATTERNS:
        redacted = pattern.sub("[local-path-redacted]", redacted)
    return redacted


def create_default_engine_service() -> EngineService:
    from app.engine.factory import create_default_engine_service as _factory

    return _factory()
