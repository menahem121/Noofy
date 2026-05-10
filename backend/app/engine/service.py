import asyncio
import hashlib
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.artifacts import AssetOwnership
from app.core.config import settings
from app.engine.adapter import EngineAdapter
from app.engine.diagnostics import DiagnosticsStore
from app.engine.memory_observation import (
    MemoryObservationCoordinator,
    memory_input_profile_fingerprint,
)
from app.engine.models import (
    BackendHealthReport,
    DiagnosticLogResponse,
    EngineJob,
    JobProgress,
    JobResult,
    LogLevel,
    MachineResourceSnapshot,
    ModelInfo,
    RuntimeBootstrapResult,
    WorkflowHealthSummary,
    WorkflowValidationResult,
)
from app.runtime.capsule_installer import CapsuleInstaller, CapsuleInstallError
from app.runtime.comfyui_sidecar_service import ComfyUISidecarService
from app.runtime.comfyui_updates import (
    ComfyUIRebuildRequest,
    ComfyUIUpdateRequest,
    ComfyUIUpdateService,
)
from app.runtime.install_state import (
    InstallStateStore,
    user_facing_install_message,
)
from app.runtime.isolation import (
    CapsuleLock,
    DependencyEnvManifest,
    InstallState,
    InstallStatus,
    RunnerWorkspaceManifest,
    SmokeTestStatus,
    TrustLevel,
)
from app.runtime.launch_settings import (
    ComfyUILaunchSettings,
    ComfyUILaunchSettingsResponse,
    ComfyUILaunchSettingsStore,
    ComfyUILaunchSettingsUpdateResult,
)
from app.runtime.manager import RuntimeManager
from app.runtime.memory_governor import (
    LocalMemoryLearningStore,
    MachineMemoryObserver,
    MachineMemorySnapshot,
    MemoryAdmissionRequest,
    MemoryBackend,
    MemoryDecisionAction,
    MemoryGovernorDecision,
    MemoryReleaseStatus,
    ProcessTreeMemoryObserver,
    RunnerMemoryTelemetryReader,
    RunnerMemorySnapshot,
    WorkflowMemoryEstimateRequest,
    build_workflow_memory_estimate,
    decide_memory_admission,
    likely_memory_error,
    memory_user_status_for_decision,
    record_memory_governor_decision,
    retry_after_memory_cleanup_decision,
    wait_for_memory_release,
)
from app.runtime.model_store import LocalModelRequirement
from app.runtime.resource_monitor import SystemResourceObserver, build_resource_snapshot
from app.runtime.runner_coordinator import RunnerProcessCoordinator
from app.runtime.runner_process import RunnerLaunchSpec
from app.runtime.smoke_test import SmokeExecutionFixture
from app.runtime.storage_gc import RuntimeStorageGarbageCollector, RuntimeStorageRoots
from app.runtime.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    JobRunnerNotFoundError,
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    QueuedRunnerStartKind,
    RunnerSelectionAction,
    RunnerStatus,
    RunnerSupervisor,
)
from app.source_policy import ModelSourceTrust, SourcePolicy
from app.trust import capsule_source_policy, workflow_source_policy, workflow_trust_payload
from app.workflows.authoring import DashboardAuthoringError, DashboardAuthoringService
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.exporter import WorkflowExportError, WorkflowExporter
from app.workflows.importer import ImportedWorkflowPackageStore, NoofyImportError
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import imported_workflow_id, safe_store_segment
from app.workflows.validator import WorkflowPackageValidator

_PREPARABLE_TRUST_LEVELS = {
    TrustLevel.NOOFY_VERIFIED,
    TrustLevel.REGISTRY_LOCKED,
    TrustLevel.QUARANTINED_COMMUNITY,
}


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
        self.memory_observer = memory_observer
        self.memory_learning_store = memory_learning_store
        self.comfyui_update_service = comfyui_update_service
        self.comfyui_launch_settings_store = comfyui_launch_settings_store
        self.resource_observer = resource_observer or SystemResourceObserver()
        self.dashboard_authoring = dashboard_authoring
        self.workflow_exporter = workflow_exporter
        self.comfyui_sidecar_service = comfyui_sidecar_service or ComfyUISidecarService(
            runtime_manager=runtime_manager,
            update_service=comfyui_update_service,
            launch_settings_store=comfyui_launch_settings_store,
            on_endpoint_changed=self._reconfigure_core_runner_endpoint,
        )
        self._job_workflows: dict[str, str] = {}
        self._job_run_requests: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
        self._memory_retry_roots: dict[str, str] = {}
        self._memory_retry_attempted_roots: set[str] = set()
        self._queued_workflow_runs: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}
        self._memory_governor_metrics: dict[str, int] = {}
        self.memory_observation = MemoryObservationCoordinator(
            runner_supervisor=runner_supervisor,
            log_store=log_store,
            memory_observer=memory_observer,
            process_tree_memory_observer=process_tree_memory_observer or ProcessTreeMemoryObserver(),
            runner_memory_telemetry_reader=runner_memory_telemetry_reader or RunnerMemoryTelemetryReader(),
            memory_learning_store=memory_learning_store,
            record_metric=self._record_memory_governor_metric,
        )

    def list_workflows(self) -> list[dict[str, object]]:
        return [
            self._workflow_summary(package)
            for package in self.workflow_loader.list_packages()
        ]

    def list_runners(self) -> list[RunnerDescriptor]:
        return self.runner_supervisor.list_runners()

    def workflow_status(self, workflow_id: str) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        install = self.get_install_state(workflow_id)
        runner = self.runner_supervisor.runner_for_workflow(workflow_id)
        required_actions = _required_actions_for_workflow(package, install)
        return {
            "workflow_id": workflow_id,
            "workflow": self._workflow_summary(package),
            "install": install,
            "required_actions": required_actions,
            "compatibility_guidance": _compatibility_guidance(package),
            "runner": runner.model_dump(mode="json") if runner is not None else None,
            "runner_status": runner.status.value if runner is not None else "not_started",
            "can_prepare": install["status"] not in {InstallStatus.UNSUPPORTED.value, InstallStatus.BLOCKED_BY_POLICY.value},
            "can_cancel_preparation": False,
            "can_cancel_job": runner.current_job_id is not None if runner is not None else False,
        }

    def cancel_preparation(self, workflow_id: str) -> dict[str, object]:
        self.log_store.add(
            "info",
            "Workflow preparation cancellation requested",
            "engine.service",
            workflow_id=workflow_id,
            details={"status": "no_active_cancelable_preparation"},
        )
        return {
            "workflow_id": workflow_id,
            "status": "no_active_cancelable_preparation",
            "user_facing_message": "No preparation is currently running for this workflow.",
            "cancelable": False,
        }

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
        if self.imported_package_store is None:
            raise NoofyImportError("Workflow import is not configured.")
        package = self.imported_package_store.import_archive(
            data,
            original_filename=original_filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )
        status = package.import_metadata.status if package.import_metadata else "imported"
        message = package.import_metadata.user_facing_message if package.import_metadata else "Imported"
        return {
            "workflow_id": package.metadata.id,
            "status": status,
            "user_facing_message": message,
            "workflow": self._workflow_summary(package),
            "required_model_count": len(package.required_models),
            "custom_node_count": len(package.custom_nodes),
            "unresolved_input_count": len(package.unresolved_runtime_inputs),
        }

    # ------------------------------------------------------------------
    # Capsule install pipeline (Phase 3)
    # ------------------------------------------------------------------

    def get_install_state(self, workflow_id: str) -> dict[str, object]:
        """Return the user-facing install state for a workflow.

        Workflows that ship a Noofy Verified capsule lock surface an
        InstallState record; workflows without a lock return an
        unsupported-shaped payload so the UI can render gracefully.
        """
        if self.capsule_loader is None or self.capsule_installer is None:
            return self._unsupported_install_payload(workflow_id)
        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            return self._unsupported_install_payload(workflow_id)

        state = self.capsule_installer.get_state(capsule_lock)
        return self._install_payload(workflow_id, state, capsule_lock=capsule_lock)

    def get_install_state_developer_details(self, workflow_id: str) -> dict[str, object]:
        if self.capsule_loader is None or self.capsule_installer is None:
            return {"workflow_id": workflow_id, "developer_details": {}}
        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            return {"workflow_id": workflow_id, "developer_details": {}}
        state = self.capsule_installer.get_state(capsule_lock)
        return {
            "workflow_id": workflow_id,
            "developer_details": _install_developer_details(state),
        }

    async def prepare_workflow(self, workflow_id: str) -> dict[str, object]:
        if self.capsule_loader is None or self.capsule_installer is None:
            self.log_store.add(
                "warning",
                "Workflow prepare requested but capsule installer is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_install_payload(workflow_id)

        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            self.log_store.add(
                "warning",
                "Workflow prepare requested but no verified bundled capsule is available",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_install_payload(workflow_id)

        package = self.workflow_loader.get_package(workflow_id)
        local_model_requirements = _local_model_requirements(package, capsule_lock)
        capsule_lock = capsule_lock.model_copy(
            update={
                "source_policy": _effective_prepare_source_policy(
                    package,
                    capsule_lock,
                    local_model_requirements=local_model_requirements,
                )
            }
        )
        model_resolution_error = _unresolved_model_requirement_message(package, capsule_lock)
        if model_resolution_error is not None:
            state = self.capsule_installer.install_state_store.update(
                capsule_lock.runtime.capsule_fingerprint,
                status=InstallStatus.CANNOT_PREPARE_AUTOMATICALLY,
                last_error=model_resolution_error,
                model_references=[],
            )
            self.log_store.add(
                "warning",
                "Workflow prepare blocked by unresolved model requirements",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": model_resolution_error,
                },
            )
            return self._install_payload(workflow_id, state, capsule_lock=capsule_lock)

        try:
            state = await self.capsule_installer.prepare(
                capsule_lock,
                local_model_requirements=local_model_requirements,
                workflow_execution_smoke_allowed=not package.unresolved_runtime_inputs,
            )
        except CapsuleInstallError as exc:
            self.log_store.add(
                "error",
                "Capsule preparation failed",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                },
            )
            return self._install_payload(workflow_id, exc.state, capsule_lock=capsule_lock)
        return self._install_payload(workflow_id, state, capsule_lock=capsule_lock)

    async def start_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        """Start and bind an isolated runner for a prepared verified workflow."""
        if self.runner_process_coordinator is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but runner coordinator is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "runner_coordinator_not_configured")
        if self.capsule_installer is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but capsule installer is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "capsule_installer_not_configured")

        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but no verified bundled capsule is available",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "verified_capsule_not_available")

        install_state = self.capsule_installer.get_state(capsule_lock)
        if install_state.status is not InstallStatus.READY:
            self.log_store.add(
                "warning",
                "Workflow runner start blocked because workflow is not ready",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "install_status": install_state.status.value,
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": "install_not_ready",
                "runner": None,
                "pid": None,
                "install_status": install_state.status.value,
                "error": install_state.last_error,
            }

        try:
            spec = self._runner_launch_spec(capsule_lock, install_state)
        except ValueError as exc:
            self.log_store.add(
                "error",
                "Workflow runner start blocked by missing runtime artifacts",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runner": None,
                "pid": None,
                "install_status": install_state.status.value,
                "error": str(exc),
            }
        decision = self.runner_supervisor.runner_selection_for(
            runner_process_compatibility_key=spec.runner_process_compatibility_key or spec.fingerprint,
            memory_class=spec.memory_class,
        )
        if decision.action is RunnerSelectionAction.REUSE and decision.runner_id is not None:
            descriptor = self.runner_supervisor.bind_workflow_runner(workflow_id, decision.runner_id)
            self.log_store.add(
                "info",
                "Workflow runner reused",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "runner_id": descriptor.runner_id,
                    "runner_process_compatibility_key": descriptor.runner_process_compatibility_key,
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": descriptor.status.value,
                "runner": descriptor.model_dump(),
                "pid": descriptor.pid,
                "install_status": InstallStatus.READY.value,
                "error": None,
            }
        if decision.action is RunnerSelectionAction.QUEUE_PENDING_SWITCH:
            queued_behind = (
                self.runner_supervisor.get_runner(decision.queued_behind_runner_id)
                if decision.queued_behind_runner_id is not None
                else None
            )
            queued = self.runner_supervisor.enqueue_runner_start(
                workflow_id=workflow_id,
                kind=QueuedRunnerStartKind.PENDING_SWITCH,
                queued_behind_runner_id=decision.queued_behind_runner_id,
                reason=decision.reason,
            )
            self.log_store.add(
                "info",
                "Workflow runner start queued pending switch",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "queue_id": queued.queue_id,
                    "queued_behind_runner_id": decision.queued_behind_runner_id,
                    "reason": decision.reason,
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": RunnerStatus.QUEUED_PENDING_SWITCH.value,
                "queue_id": queued.queue_id,
                "runner": queued_behind.model_dump() if queued_behind is not None else None,
                "pid": queued_behind.pid if queued_behind is not None else None,
                "install_status": InstallStatus.READY.value,
                "error": None,
                "memory_status": {
                    "state": "waiting_for_gpu",
                    "message": "This workflow is waiting until the current GPU work finishes.",
                    "risk_level": "medium",
                    "queue_id": queued.queue_id,
                    "can_cancel": True,
                    "can_retry_after_cleanup": False,
                },
            }
        try:
            memory_decision = self._memory_governor_decision_for_runner_start(
                workflow_id=workflow_id,
                capsule_lock=capsule_lock,
                install_state=install_state,
                spec=spec,
            )
            if memory_decision is not None:
                self._record_memory_governor_metric(f"runner_start_decision_{memory_decision.action.value}")
                if memory_decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY:
                    queued_behind = (
                        self.runner_supervisor.get_runner(memory_decision.queued_behind_runner_id)
                        if memory_decision.queued_behind_runner_id is not None
                        else None
                    )
                    queued = self.runner_supervisor.enqueue_runner_start(
                        workflow_id=workflow_id,
                        kind=QueuedRunnerStartKind.PENDING_MEMORY,
                        queued_behind_runner_id=memory_decision.queued_behind_runner_id,
                        reason=memory_decision.reason_code,
                    )
                    self._record_memory_governor_metric("runner_start_queued_pending_memory")
                    return {
                        "workflow_id": workflow_id,
                        "status": RunnerStatus.QUEUED_PENDING_MEMORY.value,
                        "queue_id": queued.queue_id,
                        "runner": queued_behind.model_dump() if queued_behind is not None else None,
                        "pid": queued_behind.pid if queued_behind is not None else None,
                        "install_status": InstallStatus.READY.value,
                        "error": None,
                        "memory_decision": memory_decision.model_dump(mode="json"),
                        "memory_status": self._memory_status_payload(memory_decision, queue_id=queued.queue_id),
                    }
                if memory_decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY:
                    self._record_memory_governor_metric("runner_start_blocked_by_memory")
                    return {
                        "workflow_id": workflow_id,
                        "status": RunnerStatus.BLOCKED_BY_MEMORY.value,
                        "runner": None,
                        "pid": None,
                        "install_status": InstallStatus.READY.value,
                        "error": memory_decision.user_message,
                        "memory_decision": memory_decision.model_dump(mode="json"),
                        "memory_status": self._memory_status_payload(memory_decision),
                    }
                if memory_decision.action is MemoryDecisionAction.EVICT_THEN_START:
                    for evict_runner_id in memory_decision.evict_runner_ids:
                        stopped = await self.runner_process_coordinator.stop_runner(evict_runner_id)
                        self._record_memory_governor_metric("idle_runner_evicted_for_memory")
                        self.log_store.add(
                            "info",
                            "Evicted idle runner before Memory Governor admitted workflow runner",
                            "engine.service",
                            workflow_id=workflow_id,
                            details={
                                "evicted_runner_id": evict_runner_id,
                                "stop_status": stopped.status.value,
                                "memory_decision_id": memory_decision.decision_id,
                                "reason": memory_decision.reason_code,
                            },
                        )
                    release_check = self._wait_for_memory_release_after_cleanup(memory_decision)
                    if release_check is not None and release_check.status is not MemoryReleaseStatus.RELEASED:
                        self.log_store.add(
                            "warning",
                            "Memory cleanup did not release enough memory",
                            "engine.service",
                            workflow_id=workflow_id,
                            details={
                                "memory_decision_id": memory_decision.decision_id,
                                "release_status": release_check.status.value,
                                "reason": release_check.reason_code,
                                "required_free_vram_mb": release_check.required_free_vram_mb,
                                "required_free_ram_mb": release_check.required_free_ram_mb,
                            },
                        )
                        return {
                            "workflow_id": workflow_id,
                            "status": RunnerStatus.MEMORY_CLEANUP_FAILED.value,
                            "runner": None,
                            "pid": None,
                            "install_status": InstallStatus.READY.value,
                            "error": "Noofy freed memory, but the machine still does not have enough available memory.",
                            "memory_decision": memory_decision.model_dump(mode="json"),
                            "memory_status": {
                                **self._memory_status_payload(memory_decision),
                                "state": "memory_cleanup_failed",
                                "message": "Noofy freed memory, but the machine still does not have enough available memory.",
                            },
                            "memory_release_check": release_check.model_dump(mode="json"),
                        }
            elif decision.action is RunnerSelectionAction.SWITCH and decision.evict_runner_id is not None:
                stopped = await self.runner_process_coordinator.stop_runner(decision.evict_runner_id)
                self.log_store.add(
                    "info",
                    "Evicted idle runner before workflow runner switch",
                    "engine.service",
                    workflow_id=workflow_id,
                    details={
                        "evicted_runner_id": decision.evict_runner_id,
                        "stop_status": stopped.status.value,
                        "reason": decision.reason,
                    },
                )
            handle = await self.runner_process_coordinator.start_runner(spec, workflow_id=workflow_id)
        except Exception as exc:
            self.log_store.add(
                "error",
                "Workflow runner start failed",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "runner_id": spec.runner_id,
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runner": None,
                "pid": None,
                "install_status": InstallStatus.READY.value,
                "error": str(exc),
            }

        self.log_store.add(
            "info",
            "Workflow runner started and bound",
            "engine.service",
            workflow_id=workflow_id,
            details={
                "runner_id": handle.runner_id,
                "base_url": handle.descriptor.base_url,
                "fingerprint": handle.descriptor.fingerprint,
            },
        )
        return {
            "workflow_id": workflow_id,
            "status": handle.descriptor.status.value,
            "runner": handle.descriptor.model_dump(),
            "pid": handle.pid,
            "install_status": InstallStatus.READY.value,
            "error": None,
            "memory_decision": memory_decision.model_dump(mode="json") if memory_decision is not None else None,
            "memory_status": self._memory_status_payload(memory_decision) if memory_decision is not None else None,
        }

    async def handoff_next_queued_runner_start(
        self,
        *,
        released_runner_id: str | None = None,
    ) -> dict[str, object] | None:
        queued = self.runner_supervisor.pop_next_queued_runner_start(
            released_runner_id=released_runner_id,
        )
        if queued is None:
            return None
        self.log_store.add(
            "info",
            "Handing off queued workflow runner start",
            "engine.service",
            workflow_id=queued.workflow_id,
            details={
                "queue_id": queued.queue_id,
                "kind": queued.kind.value,
                "released_runner_id": released_runner_id,
                "queued_behind_runner_id": queued.queued_behind_runner_id,
                "reason": queued.reason,
            },
        )
        result = await self.start_workflow_runner(queued.workflow_id)
        result["started_from_queue_id"] = queued.queue_id
        return result

    def cancel_queued_runner_start(self, queue_id: str) -> dict[str, object]:
        queued = self.runner_supervisor.cancel_queued_runner_start(queue_id)
        if queued is None:
            return {
                "queue_id": queue_id,
                "status": "not_found",
                "workflow_id": None,
            }
        self.log_store.add(
            "info",
            "Canceled queued workflow runner start",
            "engine.service",
            workflow_id=queued.workflow_id,
            details={
                "queue_id": queued.queue_id,
                "kind": queued.kind.value,
                "queued_behind_runner_id": queued.queued_behind_runner_id,
            },
        )
        return queued.model_dump(mode="json")

    async def handoff_queued_workflow_run(self, queue_id: str) -> dict[str, object] | EngineJob | WorkflowValidationResult | None:
        queued = self._queued_workflow_runs.pop(queue_id, None)
        if queued is None:
            return None
        workflow_id, inputs, options = queued
        self.log_store.add(
            "info",
            "Handing off queued workflow run",
            "engine.service",
            workflow_id=workflow_id,
            details={"queue_id": queue_id},
        )
        result = await self.run_workflow(workflow_id, inputs, options)
        if isinstance(result, EngineJob):
            result = result.model_copy(update={"queue_id": result.queue_id or queue_id})
        return result

    async def stop_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        """Stop the isolated runner currently bound to a workflow."""
        if self.runner_process_coordinator is None:
            self.log_store.add(
                "warning",
                "Workflow runner stop requested but runner coordinator is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "runner_coordinator_not_configured")

        descriptor = self.runner_supervisor.runner_for_workflow(workflow_id)
        if descriptor is None:
            return {
                "workflow_id": workflow_id,
                "status": "not_running",
                "runner": None,
                "pid": None,
                "error": None,
            }
        if descriptor.kind is RunnerKind.CORE_COMFYUI:
            self.log_store.add(
                "warning",
                "Refusing to stop core runner through workflow runner endpoint",
                "engine.service",
                workflow_id=workflow_id,
                details={"runner_id": descriptor.runner_id},
            )
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runner": descriptor.model_dump(),
                "pid": None,
                "error": "workflow is bound to the core runner",
            }

        status = await self.runner_process_coordinator.stop_runner(descriptor.runner_id)
        self.runner_supervisor.unbind_workflow_runner(workflow_id)
        self.log_store.add(
            "info",
            "Workflow runner stopped and unbound",
            "engine.service",
            workflow_id=workflow_id,
            details={"runner_id": descriptor.runner_id, "status": status.status.value},
        )
        return {
            "workflow_id": workflow_id,
            "status": status.status.value,
            "runner": {
                "runner_id": status.runner_id,
                "kind": descriptor.kind.value,
                "base_url": status.base_url,
                "ws_url": status.ws_url,
                "fingerprint": descriptor.fingerprint,
                "status": status.status.value,
            },
            "pid": status.pid,
            "error": status.error,
        }

    def open_workflow_runner_lease(self, workflow_id: str) -> dict[str, object]:
        """Record that a workflow view is open and should keep its runner warm."""
        self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.runner_for_workflow(workflow_id)
        if runner is None:
            self.log_store.add(
                "info",
                "Workflow runner lease opened without a bound runner",
                "engine.service",
                workflow_id=workflow_id,
            )
            return {
                "workflow_id": workflow_id,
                "status": "no_runner",
                "lease_id": None,
                "runner": None,
            }

        lease_id = self.runner_supervisor.open_workflow_lease(workflow_id, runner.runner_id)
        updated = self.runner_supervisor.get_runner(runner.runner_id)
        self.log_store.add(
            "info",
            "Workflow runner lease opened",
            "engine.service",
            workflow_id=workflow_id,
            details={
                "runner_id": updated.runner_id,
                "lease_id": lease_id,
                "open_workflow_lease_count": updated.open_workflow_lease_count,
            },
        )
        return {
            "workflow_id": workflow_id,
            "status": updated.status.value,
            "lease_id": lease_id,
            "runner": updated.model_dump(),
        }

    def close_workflow_runner_lease(self, workflow_id: str, lease_id: str) -> dict[str, object]:
        """Record that a workflow view closed and may enter cooldown."""
        self.workflow_loader.get_package(workflow_id)
        updated = self.runner_supervisor.close_workflow_lease(lease_id)
        if updated is None:
            self.log_store.add(
                "warning",
                "Workflow runner lease close requested for an unknown lease",
                "engine.service",
                workflow_id=workflow_id,
                details={"lease_id": lease_id},
            )
            return {
                "workflow_id": workflow_id,
                "status": "lease_not_found",
                "lease_id": lease_id,
                "runner": None,
            }

        self.log_store.add(
            "info",
            "Workflow runner lease closed",
            "engine.service",
            workflow_id=workflow_id,
            details={
                "runner_id": updated.runner_id,
                "lease_id": lease_id,
                "open_workflow_lease_count": updated.open_workflow_lease_count,
                "closed_view_cooldown_expires_at": updated.closed_view_cooldown_expires_at,
            },
        )
        return {
            "workflow_id": workflow_id,
            "status": updated.status.value,
            "lease_id": lease_id,
            "runner": updated.model_dump(),
        }

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

    def export_workflow_archive(self, workflow_id: str) -> tuple[bytes, str]:
        """Return (archive_bytes, suggested_filename) for download."""
        if self.workflow_exporter is None:
            raise KeyError(f"Workflow export not configured: {workflow_id}")
        try:
            return self.workflow_exporter.export_archive(workflow_id)
        except WorkflowExportError as exc:
            raise ValueError(str(exc)) from exc

    async def upload_workflow_image(
        self, workflow_id: str, filename: str, data: bytes, content_type: str
    ) -> dict[str, str]:
        """Upload or stage an image through the workflow-selected engine adapter."""
        package = self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)
        return await adapter.upload_workflow_image(
            package,
            filename,
            data,
            content_type,
        )

    async def validate_workflow(self, workflow_id: str) -> WorkflowValidationResult:
        package = self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)
        validation = await self._validate_package(package, adapter)
        if validation.valid:
            self.log_store.add(
                "info",
                "Workflow validation passed",
                "engine.service",
                workflow_id=workflow_id,
                details={"runner_id": runner.runner_id},
            )
        else:
            self.log_store.add(
                "warning",
                "Workflow validation failed",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "runner_id": runner.runner_id,
                    "missing_models": [model.model_dump() for model in validation.missing_models],
                    "errors": validation.errors,
                },
            )
        return validation

    async def run_workflow(
        self,
        workflow_id: str,
        inputs: dict[str, Any],
        options: dict[str, Any],
        *,
        memory_retry_after_cleanup: bool = False,
    ):
        package = self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)

        validation = await self._validate_package(package, adapter)
        if not validation.valid:
            self.log_store.add(
                "warning",
                "Workflow run blocked by validation failure",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "runner_id": runner.runner_id,
                    "missing_models": [model.model_dump() for model in validation.missing_models],
                    "errors": validation.errors,
                },
            )
            return validation

        graph = self._apply_input_bindings(package, inputs)
        memory_decision = self._memory_governor_decision_for_workflow_run(
            package=package,
            workflow_id=workflow_id,
            runner=runner,
            input_profile_fingerprint=memory_input_profile_fingerprint(inputs, options),
            memory_retry_after_cleanup=memory_retry_after_cleanup,
        )
        if memory_decision is not None:
            if memory_decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY:
                queue_id = f"workflow-run-queue-{workflow_id}-{len(self._queued_workflow_runs) + 1}"
                self._queued_workflow_runs[queue_id] = (workflow_id, dict(inputs), dict(options))
                self._record_memory_governor_metric("workflow_run_queued_pending_memory")
                self.log_store.add(
                    "info",
                    "Workflow run queued pending memory",
                    "engine.service",
                    workflow_id=workflow_id,
                    details={
                        "queue_id": queue_id,
                        "runner_id": runner.runner_id,
                        "memory_decision_id": memory_decision.decision_id,
                        "reason": memory_decision.reason_code,
                    },
                )
                return EngineJob(
                    job_id=queue_id,
                    workflow_id=workflow_id,
                    engine="noofy",
                    status="queued_pending_memory",
                    queue_id=queue_id,
                    message=memory_decision.user_message,
                    memory_decision=memory_decision.model_dump(mode="json"),
                    memory_status=self._memory_status_payload(memory_decision, queue_id=queue_id),
                )
            if memory_decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY:
                self._record_memory_governor_metric("workflow_run_blocked_by_memory")
                self.log_store.add(
                    "warning",
                    "Workflow run blocked by memory policy",
                    "engine.service",
                    workflow_id=workflow_id,
                    details={
                        "runner_id": runner.runner_id,
                        "memory_decision_id": memory_decision.decision_id,
                        "reason": memory_decision.reason_code,
                    },
                )
                return EngineJob(
                    job_id=f"blocked-memory-{workflow_id}",
                    workflow_id=workflow_id,
                    engine="noofy",
                    status="blocked_by_memory",
                    message=memory_decision.user_message,
                    memory_decision=memory_decision.model_dump(mode="json"),
                    memory_status=self._memory_status_payload(memory_decision),
                )
            if memory_decision.action is MemoryDecisionAction.EVICT_THEN_START:
                cleanup_failed = await self._evict_idle_runners_for_workflow_run(memory_decision)
                if cleanup_failed is not None:
                    return cleanup_failed

        self.log_store.add(
            "info",
            "Submitting workflow run",
            "engine.service",
            workflow_id=workflow_id,
            details={"runner_id": runner.runner_id, "input_keys": sorted(inputs.keys())},
        )
        memory_sampling_started_at = datetime.now(UTC).isoformat()
        pre_submit_snapshot = self.memory_observer.snapshot() if self.memory_observer is not None else None
        job = await adapter.run_workflow(package, graph, inputs, options)
        self.runner_supervisor.register_job(job.job_id, runner.runner_id)
        self._job_workflows[job.job_id] = workflow_id
        self._job_run_requests[job.job_id] = (workflow_id, dict(inputs), dict(options))
        self._memory_retry_roots.setdefault(job.job_id, job.job_id)
        self._start_job_memory_sampling(
            job_id=job.job_id,
            workflow_id=workflow_id,
            runner_id=runner.runner_id,
            initial_snapshot=pre_submit_snapshot,
            retry_after_cleanup=memory_retry_after_cleanup,
            telemetry_observed_after=memory_sampling_started_at,
        )
        if memory_decision is not None:
            job = job.model_copy(
                update={
                    "memory_decision": memory_decision.model_dump(mode="json"),
                    "memory_status": self._memory_status_payload(memory_decision),
                }
            )
        self.log_store.add(
            "info",
            "Workflow run queued",
            "engine.service",
            job_id=job.job_id,
            workflow_id=workflow_id,
            details={"runner_id": runner.runner_id},
        )
        return job

    async def get_progress(self, job_id: str) -> JobProgress:
        adapter = self._adapter_for_job(job_id)
        return await adapter.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.log_store.add("info", "Cancel requested", "engine.service", job_id=job_id)
        adapter = self._adapter_for_job(job_id)
        return await adapter.cancel_job(job_id)

    async def get_result(self, job_id: str) -> JobResult | EngineJob:
        adapter = self._adapter_for_job(job_id)
        result = await adapter.get_result(job_id)
        await self._finish_job_memory_sampling(result.job_id)
        self._record_local_memory_observation_for_result(result)
        retry_job = await self._maybe_retry_after_memory_cleanup(result)
        return retry_job or result

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        adapter = self._adapter_for_job(job_id)
        return await adapter.fetch_output(job_id, filename, subfolder, output_type)

    async def stream_progress_events(self, job_id: str):
        while True:
            progress = await self.get_progress(job_id)
            yield f"event: progress\ndata: {progress.model_dump_json()}\n\n"

            if progress.status in {"completed", "failed", "canceled"}:
                result = await self.get_result(job_id)
                yield f"event: result\ndata: {result.model_dump_json()}\n\n"
                return

            await asyncio.sleep(1)

    async def list_available_models(self):
        adapter = self._core_adapter()
        return await adapter.list_available_models()

    def list_logs(self, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.log_store.list_events(level=level, limit=limit)

    def list_job_logs(self, job_id: str, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.log_store.list_events(job_id=job_id, level=level, limit=limit)

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
        status = package.import_metadata.status if package.import_metadata else "installed"
        user_facing_status = (
            package.import_metadata.user_facing_message
            if package.import_metadata
            else "Installed"
        )
        return {
            "id": package.metadata.id,
            "name": package.metadata.name,
            "version": package.metadata.version,
            "description": package.metadata.description,
            "publisher_id": package.identity.publisher_id if package.identity else package.metadata.author,
            "package_id": package.identity.package_id if package.identity else package.metadata.id,
            "trust_level": package.identity.trust_level if package.identity else "noofy_verified",
            "trust": workflow_trust_payload(package),
            "source_policy": (
                package.source_policy.model_dump(mode="json")
                if package.source_policy is not None
                else workflow_source_policy(package).model_dump(mode="json")
            ),
            "status": status,
            "status_label": user_facing_status,
            "unresolved_input_count": len(package.unresolved_runtime_inputs),
            "custom_node_count": len(package.custom_nodes),
            "required_model_count": len(package.required_models),
        }

    def _phase3_verified_capsule_lock(self, workflow_id: str) -> CapsuleLock | None:
        return self._preparable_capsule_lock(workflow_id)

    def _preparable_capsule_lock(self, workflow_id: str) -> CapsuleLock | None:
        if self.capsule_loader is None:
            return None
        try:
            capsule_lock = self.capsule_loader.get_bundled_capsule_lock(workflow_id)
        except KeyError:
            try:
                capsule_lock = self.capsule_loader.get_capsule_lock(workflow_id)
            except KeyError:
                return None
        if capsule_lock.workflow.package_id != workflow_id and _imported_workflow_id(capsule_lock) != workflow_id:
            return None
        if capsule_lock.workflow.trust_level not in _PREPARABLE_TRUST_LEVELS:
            return None
        if capsule_lock.trust.level not in _PREPARABLE_TRUST_LEVELS:
            return None
        return capsule_lock

    def _unsupported_install_payload(self, workflow_id: str) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "capsule_fingerprint": None,
            "status": InstallStatus.UNSUPPORTED.value,
            "user_facing_message": user_facing_install_message(InstallStatus.UNSUPPORTED),
            "installed_at": None,
            "last_used_at": None,
            "dependency_env_path": None,
            "runner_workspace_path": None,
            "smoke_test_status": "not_run",
            "smoke_test_report": {},
            "last_error": None,
        }

    def _unsupported_runner_payload(self, workflow_id: str, reason: str) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "status": "unsupported",
            "runner": None,
            "pid": None,
            "install_status": InstallStatus.UNSUPPORTED.value,
            "error": reason,
        }

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
        graph = deepcopy(package.comfyui_graph)
        for exposed_input in package.inputs:
            if exposed_input.id not in inputs:
                continue

            node_id = exposed_input.binding.node_id
            input_name = exposed_input.binding.input_name
            if node_id not in graph:
                raise ValueError(f"Input binding references unknown node: {node_id}")

            node_inputs = graph[node_id].setdefault("inputs", {})
            node_inputs[input_name] = inputs[exposed_input.id]
        return graph

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

    def _runner_launch_spec(self, capsule_lock: CapsuleLock, install_state: InstallState) -> RunnerLaunchSpec:
        dependency_env_path, runner_workspace_path = self._prepared_runtime_paths(install_state, capsule_lock)
        if install_state.smoke_test_status is not SmokeTestStatus.PASSED:
            raise ValueError(
                "Prepared runtime smoke test has not passed: "
                f"{install_state.smoke_test_status.value}"
            )
        return _workflow_runner_launch_spec(
            capsule_lock,
            dependency_env_path=dependency_env_path,
            runner_workspace_path=runner_workspace_path,
            runtime_manager=self.runtime_manager,
        )

    def _memory_governor_decision_for_runner_start(
        self,
        *,
        workflow_id: str,
        capsule_lock: CapsuleLock,
        install_state: InstallState,
        spec: RunnerLaunchSpec,
    ) -> MemoryGovernorDecision | None:
        if self.memory_observer is None:
            return None
        machine_snapshot = self.memory_observer.snapshot()
        local_evidence = (
            self.memory_learning_store.summary_for(
                workflow_id=workflow_id,
                runner_process_compatibility_key=spec.runner_process_compatibility_key or spec.fingerprint,
                machine_profile_id=machine_snapshot.machine_profile_id,
                backend=machine_snapshot.backend,
            )
            if self.memory_learning_store is not None
            else None
        )
        model_size_mb = _installed_model_size_mb(install_state)
        estimate = build_workflow_memory_estimate(
            WorkflowMemoryEstimateRequest(
                workflow_id=workflow_id,
                runner_process_compatibility_key=spec.runner_process_compatibility_key or spec.fingerprint,
                declared_memory_class=spec.memory_class,
                local_evidence=local_evidence,
                creator_observed_peak_vram_mb=capsule_lock.hardware_observations.observed_peak_vram_mb
                or capsule_lock.hardware_observations.recommended_vram_mb,
                creator_observed_peak_ram_mb=capsule_lock.hardware_observations.observed_peak_ram_mb
                or capsule_lock.hardware_observations.recommended_ram_mb,
                required_model_size_mb=model_size_mb,
            )
        )
        runner_snapshots = [
            RunnerMemorySnapshot.from_descriptor(runner)
            for runner in self.runner_supervisor.list_runners()
            if runner.kind is RunnerKind.ISOLATED_COMFYUI
        ]
        decision = decide_memory_admission(
            MemoryAdmissionRequest(
                workflow_estimate=estimate,
                machine_snapshot=machine_snapshot,
                resident_runners=runner_snapshots,
            )
        )
        record_memory_governor_decision(self.log_store, decision)
        return decision

    def _memory_governor_decision_for_workflow_run(
        self,
        *,
        package: WorkflowPackage,
        workflow_id: str,
        runner: RunnerDescriptor,
        input_profile_fingerprint: str | None = None,
        memory_retry_after_cleanup: bool = False,
    ) -> MemoryGovernorDecision | None:
        if self.memory_observer is None:
            return None
        machine_snapshot = self.memory_observer.snapshot()
        local_evidence = (
            self.memory_learning_store.summary_for(
                workflow_id=workflow_id,
                runner_process_compatibility_key=runner.runner_process_compatibility_key,
                machine_profile_id=machine_snapshot.machine_profile_id,
                backend=machine_snapshot.backend,
                input_profile_fingerprint=input_profile_fingerprint,
            )
            if self.memory_learning_store is not None
            else None
        )
        estimate = build_workflow_memory_estimate(
            WorkflowMemoryEstimateRequest(
                workflow_id=workflow_id,
                runner_process_compatibility_key=runner.runner_process_compatibility_key,
                declared_memory_class=runner.memory_class,
                input_profile_fingerprint=input_profile_fingerprint,
                local_evidence=local_evidence,
                creator_observed_peak_vram_mb=_observed_hardware_int(package, "observed_peak_vram_mb")
                or _observed_hardware_int(package, "recommended_vram_mb"),
                creator_observed_peak_ram_mb=_observed_hardware_int(package, "observed_peak_ram_mb")
                or _observed_hardware_int(package, "recommended_ram_mb"),
                declared_peak_vram_mb=runner.observed_execution_peak_vram_mb,
                declared_peak_ram_mb=runner.observed_execution_peak_ram_mb,
                required_model_size_mb=_required_model_size_mb_from_package(package),
            )
        )
        resident_runners = []
        for resident in self.runner_supervisor.list_runners():
            if resident.runner_id == runner.runner_id and resident.current_job_id is None:
                continue
            if (
                resident.kind is RunnerKind.CORE_COMFYUI
                and resident.current_job_id is None
                and resident.status
                not in {
                    RunnerStatus.RUNNING,
                    RunnerStatus.LOADING_MODEL,
                    RunnerStatus.RETRYING_AFTER_MEMORY_CLEANUP,
                }
            ):
                continue
            resident_runners.append(RunnerMemorySnapshot.from_descriptor(resident))
        decision = decide_memory_admission(
            MemoryAdmissionRequest(
                workflow_estimate=estimate,
                machine_snapshot=machine_snapshot,
                resident_runners=resident_runners,
            )
        )
        record_memory_governor_decision(self.log_store, decision)
        self._record_memory_governor_metric(f"workflow_run_decision_{decision.action.value}")
        if (
            memory_retry_after_cleanup
            and decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
            and decision.reason_code == "recent_memory_error_requires_cleanup_before_retry"
        ):
            decision = decision.model_copy(
                update={
                    "action": MemoryDecisionAction.START_CO_RESIDENT,
                    "reason_code": "retry_after_cleanup_cautious_start",
                    "user_message": "Noofy freed memory and is trying this workflow one more time.",
                    "developer_details": {
                        **decision.developer_details,
                        "recent_memory_error_allowed_for_retry_after_cleanup": True,
                    },
                }
            )
            record_memory_governor_decision(self.log_store, decision)
        return decision

    def _wait_for_memory_release_after_cleanup(
        self,
        decision: MemoryGovernorDecision,
    ):
        if self.memory_observer is None or decision.workflow_estimate is None:
            return None
        required_free_vram_mb = _required_free_after_cleanup(
            _estimated_vram_after_cleanup(decision),
            decision.required_vram_margin_mb,
        )
        required_free_ram_mb = _required_free_after_cleanup(
            _estimated_ram_after_cleanup(decision),
            decision.required_ram_margin_mb,
        )
        if required_free_vram_mb is None and required_free_ram_mb is None:
            return None
        release_check = wait_for_memory_release(
            self.memory_observer,
            required_free_vram_mb=required_free_vram_mb,
            required_free_ram_mb=required_free_ram_mb,
            max_checks=3,
            interval_seconds=0,
        )
        self.log_store.add(
            "info" if release_check.status is MemoryReleaseStatus.RELEASED else "warning",
            "Memory release check completed",
            "memory_governor",
            workflow_id=decision.workflow_id,
            details={
                "memory_decision_id": decision.decision_id,
                "status": release_check.status.value,
                "reason_code": release_check.reason_code,
                "required_free_vram_mb": release_check.required_free_vram_mb,
                "required_free_ram_mb": release_check.required_free_ram_mb,
                "checks": len(release_check.snapshots),
            },
        )
        return release_check

    async def _evict_idle_runners_for_workflow_run(
        self,
        decision: MemoryGovernorDecision,
    ) -> EngineJob | None:
        if self.runner_process_coordinator is None:
            return None
        for evict_runner_id in decision.evict_runner_ids:
            stopped = await self.runner_process_coordinator.stop_runner(evict_runner_id)
            self._record_memory_governor_metric("idle_runner_evicted_for_workflow_run")
            self.log_store.add(
                "info",
                "Evicted idle runner before workflow run",
                "memory_governor",
                workflow_id=decision.workflow_id,
                details={
                    "evicted_runner_id": evict_runner_id,
                    "stop_status": stopped.status.value,
                    "memory_decision_id": decision.decision_id,
                    "reason": decision.reason_code,
                },
            )
        release_check = self._wait_for_memory_release_after_cleanup(decision)
        if release_check is None or release_check.status is MemoryReleaseStatus.RELEASED:
            return None
        self._record_memory_governor_metric("workflow_run_memory_cleanup_failed")
        return EngineJob(
            job_id=f"blocked-memory-{decision.workflow_id}",
            workflow_id=decision.workflow_id or "unknown",
            engine="noofy",
            status="blocked_by_memory",
            message="Noofy freed memory, but the machine still does not have enough available memory.",
            memory_decision=decision.model_dump(mode="json"),
            memory_status={
                **self._memory_status_payload(decision),
                "state": "memory_cleanup_failed",
                "message": "Noofy freed memory, but the machine still does not have enough available memory.",
            },
        )

    def _start_job_memory_sampling(
        self,
        *,
        job_id: str,
        workflow_id: str,
        runner_id: str,
        initial_snapshot: MachineMemorySnapshot | None = None,
        retry_after_cleanup: bool = False,
        telemetry_observed_after: str | None = None,
    ) -> None:
        self.memory_observation.start_job_sampling(
            job_id=job_id,
            workflow_id=workflow_id,
            runner_id=runner_id,
            initial_snapshot=initial_snapshot,
            retry_after_cleanup=retry_after_cleanup,
            telemetry_observed_after=telemetry_observed_after,
        )

    async def _finish_job_memory_sampling(self, job_id: str) -> None:
        await self.memory_observation.finish_job_sampling(
            job_id,
            workflow_id=self._job_workflows.get(job_id),
        )

    async def _maybe_retry_after_memory_cleanup(self, result: JobResult) -> EngineJob | None:
        if result.status != "failed" or not likely_memory_error(result.error):
            return None
        workflow_id = self._job_workflows.get(result.job_id)
        run_request = self._job_run_requests.get(result.job_id)
        if workflow_id is None or run_request is None or self.memory_observer is None:
            return None

        root_job_id = self._memory_retry_roots.get(result.job_id, result.job_id)
        retry_already_attempted = root_job_id in self._memory_retry_attempted_roots
        machine_snapshot = self.memory_observer.snapshot()
        try:
            runner = self.runner_supervisor.runner_for_job(result.job_id)
        except JobRunnerNotFoundError:
            runner = None
        local_evidence = (
            self.memory_learning_store.summary_for(
                workflow_id=workflow_id,
                runner_process_compatibility_key=runner.runner_process_compatibility_key if runner is not None else None,
                machine_profile_id=machine_snapshot.machine_profile_id,
                backend=machine_snapshot.backend,
                input_profile_fingerprint=memory_input_profile_fingerprint(run_request[1], run_request[2]),
            )
            if self.memory_learning_store is not None
            else None
        )
        estimate = build_workflow_memory_estimate(
            WorkflowMemoryEstimateRequest(
                workflow_id=workflow_id,
                runner_process_compatibility_key=runner.runner_process_compatibility_key if runner is not None else None,
                declared_memory_class=runner.memory_class if runner is not None else RunnerMemoryClass.UNKNOWN,
                input_profile_fingerprint=memory_input_profile_fingerprint(run_request[1], run_request[2]),
                local_evidence=local_evidence,
                declared_peak_vram_mb=runner.observed_execution_peak_vram_mb if runner is not None else None,
                declared_peak_ram_mb=runner.observed_execution_peak_ram_mb if runner is not None else None,
            )
        )
        decision = retry_after_memory_cleanup_decision(
            workflow_estimate=estimate,
            machine_snapshot=machine_snapshot,
            error_message=result.error,
            retry_already_attempted=retry_already_attempted,
        )
        record_memory_governor_decision(
            self.log_store,
            decision,
            level="warning" if decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY else "info",
        )
        if decision.action is not MemoryDecisionAction.RETRY_AFTER_MEMORY_CLEANUP:
            self._record_memory_governor_metric("memory_retry_blocked")
            return None

        release_check = await self._stop_idle_runners_for_memory_retry(
            current_job_id=result.job_id,
            decision=decision,
        )
        if release_check is not None and release_check.status is not MemoryReleaseStatus.RELEASED:
            self._record_memory_governor_metric("memory_retry_cleanup_failed")
            return None

        self._memory_retry_attempted_roots.add(root_job_id)
        self._record_memory_governor_metric("memory_retry_attempted")
        retry_workflow_id, inputs, options = run_request
        retry_result = await self.run_workflow(
            retry_workflow_id,
            dict(inputs),
            dict(options),
            memory_retry_after_cleanup=True,
        )
        if not isinstance(retry_result, EngineJob):
            return None
        self._memory_retry_roots[retry_result.job_id] = root_job_id
        self.log_store.add(
            "info",
            "Retrying workflow after Memory Governor cleanup",
            "memory_governor",
            job_id=retry_result.job_id,
            workflow_id=retry_workflow_id,
            details={
                "original_job_id": result.job_id,
                "root_job_id": root_job_id,
                "memory_decision_id": decision.decision_id,
            },
        )
        return retry_result.model_copy(
            update={
                "status": "queued",
                "message": decision.user_message,
                "memory_decision": decision.model_dump(mode="json"),
                "memory_status": self._memory_status_payload(decision),
            }
        )

    async def _stop_idle_runners_for_memory_retry(
        self,
        *,
        current_job_id: str,
        decision: MemoryGovernorDecision,
    ):
        if self.runner_process_coordinator is None:
            return None
        stopped_runner_ids: list[str] = []
        for runner in self.runner_supervisor.list_runners():
            if runner.kind is not RunnerKind.ISOLATED_COMFYUI:
                continue
            if runner.current_job_id in {current_job_id}:
                continue
            if runner.current_job_id is not None or runner.status is RunnerStatus.RUNNING:
                continue
            if runner.status not in {
                RunnerStatus.READY,
                RunnerStatus.IDLE,
                RunnerStatus.IDLE_WARM,
                RunnerStatus.CO_RESIDENT,
            }:
                continue
            stopped = await self.runner_process_coordinator.stop_runner(runner.runner_id)
            stopped_runner_ids.append(runner.runner_id)
            self._record_memory_governor_metric("idle_runner_evicted_for_retry")
            self.log_store.add(
                "info",
                "Evicted idle runner before retry after memory cleanup",
                "memory_governor",
                workflow_id=decision.workflow_id,
                details={
                    "evicted_runner_id": runner.runner_id,
                    "stop_status": stopped.status.value,
                    "memory_decision_id": decision.decision_id,
                },
            )
        if not stopped_runner_ids:
            return None
        return self._wait_for_memory_release_after_cleanup(decision)

    def _record_local_memory_observation_for_result(self, result: JobResult) -> None:
        self.memory_observation.record_result_observation(
            result,
            workflow_id=self._job_workflows.get(result.job_id),
            run_request=self._job_run_requests.get(result.job_id),
        )

    def memory_governor_metrics(self) -> dict[str, int]:
        return dict(self._memory_governor_metrics)

    def _record_memory_governor_metric(self, name: str) -> None:
        self._memory_governor_metrics[name] = self._memory_governor_metrics.get(name, 0) + 1

    def _memory_status_payload(
        self,
        decision: MemoryGovernorDecision,
        *,
        queue_id: str | None = None,
    ) -> dict[str, Any]:
        return memory_user_status_for_decision(decision, queue_id=queue_id).model_dump(mode="json")

    def _prepared_runtime_paths(self, install_state: InstallState, capsule_lock: CapsuleLock) -> tuple[Path, Path]:
        if not install_state.dependency_env_path or not install_state.runner_workspace_path:
            raise ValueError("Prepared runtime artifact paths are missing; prepare the workflow again.")

        dependency_env_path = Path(install_state.dependency_env_path)
        runner_workspace_path = Path(install_state.runner_workspace_path)
        missing: list[str] = []
        if not (dependency_env_path / "manifest.json").exists():
            missing.append("dependency environment manifest")
        if not (runner_workspace_path / "manifest.json").exists():
            missing.append("runner workspace manifest")
        if not (runner_workspace_path / "main.py").exists():
            missing.append("runner workspace entrypoint")
        if missing:
            raise ValueError(f"Prepared runtime artifact is missing: {', '.join(missing)}")

        dependency_manifest = _read_dependency_manifest(dependency_env_path / "manifest.json")
        runner_manifest = _read_runner_workspace_manifest(runner_workspace_path / "manifest.json")
        not_ready: list[str] = []
        if dependency_manifest.status is not InstallStatus.READY:
            not_ready.append(f"dependency environment manifest status {dependency_manifest.status.value}")
        if runner_manifest.status is not InstallStatus.READY:
            not_ready.append(f"runner workspace manifest status {runner_manifest.status.value}")
        if dependency_manifest.fingerprint != capsule_lock.runtime.dependency_env_fingerprint:
            not_ready.append("dependency environment manifest fingerprint mismatch")
        if runner_manifest.fingerprint != capsule_lock.runtime.runner_fingerprint:
            not_ready.append("runner workspace manifest fingerprint mismatch")
        if runner_manifest.dependency_env_fingerprint != dependency_manifest.fingerprint:
            not_ready.append("runner workspace dependency environment mismatch")
        not_ready.extend(_invalid_model_references(install_state))
        if not_ready:
            raise ValueError(f"Prepared runtime artifact is not ready: {', '.join(not_ready)}")
        return dependency_env_path, runner_workspace_path

    @staticmethod
    def _runner_id_for_capsule(capsule_lock: CapsuleLock) -> str:
        raw = capsule_lock.runtime.runner_fingerprint
        safe = "".join(char if char.isalnum() else "-" for char in raw.lower()).strip("-")
        return f"workflow-{capsule_lock.workflow.package_id}-{safe}"


def _workflow_runner_launch_spec(
    capsule_lock: CapsuleLock,
    *,
    dependency_env_path: Path,
    runner_workspace_path: Path,
    runtime_manager: RuntimeManager,
    runner_id_suffix: str | None = None,
) -> RunnerLaunchSpec:
    runner_id = EngineService._runner_id_for_capsule(capsule_lock)
    if runner_id_suffix:
        runner_id = f"{runner_id}-{runner_id_suffix}"
    telemetry_path = runner_workspace_path / ".noofy" / "memory" / f"{runner_id}.jsonl"
    extra_args = [
        "--base-directory",
        str(runner_workspace_path),
        "--disable-auto-launch",
    ]
    if not capsule_lock.custom_nodes:
        extra_args.append("--disable-all-custom-nodes")
    return RunnerLaunchSpec(
        runner_id=runner_id,
        kind=RunnerKind.ISOLATED_COMFYUI,
        fingerprint=capsule_lock.runtime.runner_fingerprint,
        python_executable=_runtime_python_executable(runtime_manager),
        working_dir=runner_workspace_path,
        dependency_env_path=dependency_env_path,
        runner_workspace_path=runner_workspace_path,
        runner_workspace_fingerprint=capsule_lock.runtime.runner_fingerprint,
        dependency_env_fingerprint=capsule_lock.runtime.dependency_env_fingerprint,
        runner_process_compatibility_key=(
            capsule_lock.runtime.runner_process_compatibility_key
            or capsule_lock.runtime.runner_fingerprint
        ),
        runtime_profile_id=capsule_lock.runtime.runtime_profile_id,
        runtime_profile_variant_id=capsule_lock.runtime.runtime_profile_variant_id,
        memory_class=_memory_class_for_runtime_backend(capsule_lock.runtime.gpu_backend),
        host=runtime_manager.managed_host,
        extra_args=extra_args,
        memory_telemetry_path=telemetry_path,
        env={
            "NOOFY_CAPSULE_FINGERPRINT": capsule_lock.runtime.capsule_fingerprint,
            "NOOFY_DEPENDENCY_ENV_PATH": str(dependency_env_path),
            "NOOFY_RUNNER_WORKSPACE_PATH": str(runner_workspace_path),
            "NOOFY_WORKFLOW_ID": capsule_lock.workflow.package_id,
        },
    )


def _runtime_python_executable(runtime_manager: RuntimeManager) -> str:
    environment = getattr(runtime_manager, "environment", None)
    if environment is not None:
        return environment.python_executable
    return runtime_manager.python_executable


def _required_actions_for_workflow(package: WorkflowPackage, install: dict[str, object]) -> list[dict[str, object]]:
    actions: list[dict[str, object]] = []
    if package.unresolved_runtime_inputs:
        actions.append(
            {
                "kind": "input_setup",
                "status": "required",
                "user_facing_message": "Choose the missing input files before running this workflow.",
                "count": len(package.unresolved_runtime_inputs),
            }
        )
    status = install.get("status")
    if status in {InstallStatus.PENDING.value, InstallStatus.IMPORTED.value, InstallStatus.NEEDS_INPUT_SETUP.value}:
        actions.append(
            {
                "kind": "prepare_workflow",
                "status": "available",
                "user_facing_message": "Prepare this workflow before running it.",
            }
        )
    if status in {
        InstallStatus.CANNOT_PREPARE_AUTOMATICALLY.value,
        InstallStatus.BLOCKED_BY_POLICY.value,
        InstallStatus.UNSUPPORTED_RUNTIME_PROFILE.value,
        InstallStatus.FAILED.value,
        InstallStatus.UNSUPPORTED.value,
    }:
        actions.append(
            {
                "kind": "review_preparation_issue",
                "status": "required",
                "user_facing_message": install.get("user_facing_message") or "This workflow needs attention.",
            }
        )
    return actions


def _compatibility_guidance(package: WorkflowPackage) -> dict[str, object] | None:
    observed = package.observed_hardware or {}
    if not observed:
        return None
    return {
        "kind": "observed_creator_hardware",
        "user_facing_message": "This hardware information is guidance from previous runs, not a guaranteed requirement.",
        "observed_hardware": observed,
    }


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


def _redact_diagnostic_details(value):
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
        return _redact_user_private_paths(value)
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


def _read_dependency_manifest(path: Path) -> DependencyEnvManifest:
    try:
        return DependencyEnvManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is unreadable: {path}") from exc
    except ValidationError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is invalid: {path}") from exc


def _read_runner_workspace_manifest(path: Path) -> RunnerWorkspaceManifest:
    try:
        return RunnerWorkspaceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is unreadable: {path}") from exc
    except ValidationError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is invalid: {path}") from exc


def _invalid_model_references(install_state: InstallState) -> list[str]:
    invalid: list[str] = []
    for ref in install_state.model_references:
        if ref.asset_ownership is AssetOwnership.USER_LOCAL:
            if not ref.source_path:
                invalid.append(f"local model reference missing source path for {ref.requirement_id}")
                continue
            source_path = Path(ref.source_path)
            if not source_path.exists():
                invalid.append(f"local model source missing for {ref.requirement_id}")
                continue
        else:
            if not ref.blob_path:
                invalid.append(f"model reference missing blob path for {ref.requirement_id}")
                continue
            blob_path = Path(ref.blob_path)
            if not blob_path.exists():
                invalid.append(f"model blob missing for {ref.requirement_id}")
                continue
        if not ref.materialized_path:
            invalid.append(f"model reference missing materialized path for {ref.requirement_id}")
            continue
        materialized_path = Path(ref.materialized_path)
        if not materialized_path.exists():
            invalid.append(f"model view file missing for {ref.requirement_id}")
            continue
        if ref.size_bytes is not None and materialized_path.stat().st_size != ref.size_bytes:
            invalid.append(f"model view file size mismatch for {ref.requirement_id}")
            continue
        if ref.sha256 is not None:
            expected = ref.sha256.removeprefix("sha256:")
            if _sha256_file(materialized_path) != expected:
                invalid.append(f"model view file hash mismatch for {ref.requirement_id}")
    return invalid


def _installed_model_size_mb(install_state: InstallState) -> int | None:
    total_size_bytes = sum(ref.size_bytes or 0 for ref in install_state.model_references)
    if total_size_bytes <= 0:
        return None
    return max(1, total_size_bytes // (1024 * 1024))


def _required_model_size_mb_from_package(package: WorkflowPackage) -> int | None:
    total_size_bytes = sum(model.size_bytes or 0 for model in package.required_models)
    if total_size_bytes <= 0:
        return None
    return max(1, total_size_bytes // (1024 * 1024))


def _observed_hardware_int(package: WorkflowPackage, key: str) -> int | None:
    value = package.observed_hardware.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _required_free_after_cleanup(estimated_peak_mb: int | None, margin_mb: int | None) -> int | None:
    if estimated_peak_mb is None:
        return None
    return estimated_peak_mb + (margin_mb or 0)


def _estimated_vram_after_cleanup(decision: MemoryGovernorDecision) -> int | None:
    if decision.machine_snapshot is not None and decision.machine_snapshot.backend in {
        MemoryBackend.MPS,
        MemoryBackend.CPU,
    }:
        return None
    if decision.workflow_estimate is None:
        return None
    return decision.workflow_estimate.estimated_peak_vram_mb


def _estimated_ram_after_cleanup(decision: MemoryGovernorDecision) -> int | None:
    if decision.workflow_estimate is None:
        return None
    if decision.workflow_estimate.estimated_peak_ram_mb is not None:
        return decision.workflow_estimate.estimated_peak_ram_mb
    if decision.machine_snapshot is not None and decision.machine_snapshot.backend in {
        MemoryBackend.MPS,
        MemoryBackend.CPU,
    }:
        return decision.workflow_estimate.estimated_peak_vram_mb
    return None


def _memory_class_for_runtime_backend(gpu_backend: str) -> RunnerMemoryClass:
    return RunnerMemoryClass.CPU_ONLY if gpu_backend.lower() == "cpu" else RunnerMemoryClass.GPU_HEAVY


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _unresolved_model_requirement_message(
    package: WorkflowPackage,
    capsule_lock: CapsuleLock,
) -> str | None:
    locked_targets = {
        (model.comfyui_folder.casefold(), model.filename.casefold())
        for model in capsule_lock.models
    }
    unresolved: list[str] = []
    for model in package.required_models:
        target = (model.folder.casefold(), model.filename.casefold())
        if target in locked_targets:
            continue
        if model.checksum is not None and model.size_bytes is not None:
            unresolved.append(f"{model.folder}/{model.filename} is missing from the capsule model lock")
        elif model.checksum is not None:
            unresolved.append(
                f"{model.folder}/{model.filename} has hash identity but no byte size; complete model identity is required"
            )
        elif model.size_bytes is not None:
            continue
        else:
            unresolved.append(
                f"{model.folder}/{model.filename} has only filename identity; filename-only model matches are not trusted"
            )
    if not unresolved:
        return None
    return (
        "Cannot prepare workflow automatically because model requirements are unresolved: "
        + "; ".join(unresolved)
    )


def _local_model_requirements(
    package: WorkflowPackage,
    capsule_lock: CapsuleLock,
) -> list[LocalModelRequirement]:
    locked_targets = {
        (model.comfyui_folder.casefold(), model.filename.casefold())
        for model in capsule_lock.models
    }
    requirements: list[LocalModelRequirement] = []
    for model in package.required_models:
        target = (model.folder.casefold(), model.filename.casefold())
        if target in locked_targets:
            continue
        if model.checksum is None and model.size_bytes is not None:
            requirements.append(
                LocalModelRequirement(
                    requirement_id=f"{model.folder}/{model.filename}",
                    comfyui_folder=model.folder,
                    filename=model.filename,
                    size_bytes=model.size_bytes,
                )
            )
    return requirements


def _effective_prepare_source_policy(
    package: WorkflowPackage,
    capsule_lock: CapsuleLock,
    *,
    local_model_requirements: list[LocalModelRequirement],
) -> SourcePolicy:
    policy = package.source_policy or workflow_source_policy(package)
    if capsule_lock.models and local_model_requirements:
        model_source_trust = ModelSourceTrust.MIXED
    elif capsule_lock.models:
        model_source_trust = ModelSourceTrust.HASHED
    elif local_model_requirements:
        model_source_trust = ModelSourceTrust.FILENAME_SIZE
    else:
        model_source_trust = policy.model_source_trust
    return policy.model_copy(update={"model_source_trust": model_source_trust})


def _workflow_source_files_dir(
    workflow_id: str,
    *,
    workflow_loader: WorkflowPackageLoader,
    imported_package_store: ImportedWorkflowPackageStore,
) -> Path | None:
    try:
        package = workflow_loader.get_package(workflow_id)
    except KeyError:
        return None
    if package.import_metadata is None:
        return None
    try:
        package_dir = imported_package_store.package_dir(package)
    except NoofyImportError:
        return None
    source_files_dir = package_dir / "source-files"
    return source_files_dir if source_files_dir.exists() else None


def _smoke_execution_fixture_for_capsule(
    capsule_lock: CapsuleLock,
    *,
    workflow_loader: WorkflowPackageLoader,
) -> SmokeExecutionFixture | None:
    package = None
    for workflow_id in (capsule_lock.workflow.package_id, _imported_workflow_id(capsule_lock)):
        try:
            package = workflow_loader.get_package(workflow_id)
            break
        except KeyError:
            continue
    if package is None:
        return None
    fixture = package.smoke_tests.workflow_execution
    if fixture is None:
        if capsule_lock.custom_nodes:
            return None
        return _default_core_smoke_execution_fixture()
    return SmokeExecutionFixture(
        name=fixture.name,
        prompt=fixture.prompt,
        required_node_types=fixture.required_node_types,
        expected_output_node_count=fixture.expected_output_node_count,
        expected_output_node_ids=fixture.expected_output_node_ids,
        timeout_seconds=fixture.timeout_seconds,
    )


def _default_core_smoke_execution_fixture() -> SmokeExecutionFixture:
    return SmokeExecutionFixture(
        name="default-core-empty-image",
        prompt={
            "1": {
                "class_type": "EmptyImage",
                "inputs": {
                    "width": 64,
                    "height": 64,
                    "batch_size": 1,
                    "color": 0x335577,
                },
            },
            "2": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["1", 0],
                    "filename_prefix": "noofy_smoke",
                },
            },
        },
        required_node_types=("EmptyImage", "SaveImage"),
        expected_output_node_count=1,
        expected_output_node_ids=("2",),
        timeout_seconds=30,
    )


def _imported_workflow_id(capsule_lock: CapsuleLock) -> str:
    return imported_workflow_id(
        capsule_lock.workflow.publisher_id,
        capsule_lock.workflow.package_id,
        capsule_lock.workflow.version,
    )


def _safe_store_segment(value: str) -> str:
    return safe_store_segment(value)


def create_default_engine_service() -> EngineService:
    from app.engine.factory import create_default_engine_service as _factory

    return _factory()
