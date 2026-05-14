"""Workflow-bound runner lifecycle: leases, queued starts, prepare, start, stop.

This service owns the full runner lifecycle for isolated community-workflow
runners.  The memory-governor decisions for runner start are delegated to
MemoryGovernorService, which is injected as an optional dependency so that
environments without memory observation still work.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import ValidationError

from app.diagnostics import DiagnosticsSink, sanitize
from app.runtime.capsule_installer import CapsuleInstaller, CapsuleInstallError
from app.runtime.install_state import user_facing_install_message
from app.runtime.dependencies.isolation import (
    CapsuleLock,
    DependencyEnvManifest,
    InstallState,
    InstallStatus,
    RunnerWorkspaceManifest,
    SmokeTestStatus,
    TrustLevel,
)
from app.runtime.manager import RuntimeManager
from app.runtime.memory.memory_governor import (
    MemoryDecisionAction,
    MemoryReleaseStatus,
)
from app.runtime.models.model_store import LocalModelRequirement
from app.runtime.runners.runner_coordinator import RunnerProcessCoordinator
from app.runtime.runners.runner_process import RunnerLaunchSpec
from app.runtime.smoke_test import SmokeExecutionFixture
from app.runtime.runners.supervisor import (
    QueuedRunnerStartKind,
    RunnerKind,
    RunnerMemoryClass,
    RunnerSelectionAction,
    RunnerStatus,
    RunnerSupervisor,
)
from app.source_policy import ModelSourceTrust, SourcePolicy
from app.trust import capsule_source_policy, workflow_source_policy
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.importer import ImportedWorkflowPackageStore, NoofyImportError
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import imported_workflow_id

_PREPARABLE_TRUST_LEVELS = {
    TrustLevel.NOOFY_VERIFIED,
    TrustLevel.REGISTRY_LOCKED,
    TrustLevel.QUARANTINED_COMMUNITY,
}


class WorkflowRunnerLifecycleService:
    """Workflow-bound runner leases, queued-start lifecycle, and runner operations."""

    def __init__(
        self,
        *,
        workflow_loader: WorkflowPackageLoader,
        runner_supervisor: RunnerSupervisor,
        log_store: DiagnosticsSink,
        capsule_loader: CapsuleLockLoader | None = None,
        capsule_installer: CapsuleInstaller | None = None,
        runner_process_coordinator: RunnerProcessCoordinator | None = None,
        runtime_manager: RuntimeManager | None = None,
        memory_service: object | None = None,
        imported_package_store: ImportedWorkflowPackageStore | None = None,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.runner_supervisor = runner_supervisor
        self.log_store = log_store
        self.capsule_loader = capsule_loader
        self.capsule_installer = capsule_installer
        self.runner_process_coordinator = runner_process_coordinator
        self.runtime_manager = runtime_manager
        self.memory_service = memory_service
        self.imported_package_store = imported_package_store

    # ------------------------------------------------------------------
    # Queued start handoff and cancellation (existing)
    # ------------------------------------------------------------------

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
            "runtime.runners.lifecycle_service",
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
            "runtime.runners.lifecycle_service",
            workflow_id=queued.workflow_id,
            details={
                "queue_id": queued.queue_id,
                "kind": queued.kind.value,
                "queued_behind_runner_id": queued.queued_behind_runner_id,
            },
        )
        return queued.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Runner leases (existing)
    # ------------------------------------------------------------------

    def open_workflow_runner_lease(self, workflow_id: str) -> dict[str, object]:
        self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.runner_for_workflow(workflow_id)
        if runner is None:
            self.log_store.add(
                "info",
                "Workflow runner lease opened without a bound runner",
                "runtime.runners.lifecycle_service",
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
            "runtime.runners.lifecycle_service",
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
        self.workflow_loader.get_package(workflow_id)
        updated = self.runner_supervisor.close_workflow_lease(lease_id)
        if updated is None:
            self.log_store.add(
                "warning",
                "Workflow runner lease close requested for an unknown lease",
                "runtime.runners.lifecycle_service",
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
            "runtime.runners.lifecycle_service",
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
    # Install state queries
    # ------------------------------------------------------------------

    def get_install_state(self, workflow_id: str) -> dict[str, object]:
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

    def preparable_capsule_lock(self, workflow_id: str) -> CapsuleLock | None:
        """Accessor used by the temporary EngineService facade and RunOrchestrator."""
        return self._preparable_capsule_lock(workflow_id)

    def imported_workflow_without_preparable_capsule(
        self, package: WorkflowPackage
    ) -> str | None:
        if package.import_metadata is None:
            return None
        if self._preparable_capsule_lock(package.metadata.id) is not None:
            return None
        return (
            "This imported workflow cannot run on this machine because Noofy could "
            "not resolve a supported managed runtime profile for it."
        )

    # ------------------------------------------------------------------
    # Prepare, start, and stop runner
    # ------------------------------------------------------------------

    async def prepare_workflow(self, workflow_id: str) -> dict[str, object]:
        if self.capsule_loader is None or self.capsule_installer is None:
            self.log_store.add(
                "warning",
                "Workflow prepare requested but capsule installer is not configured",
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
            )
            return self._unsupported_install_payload(workflow_id)

        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            self.log_store.add(
                "warning",
                "Workflow prepare requested but no verified bundled capsule is available",
                "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "runner_coordinator_not_configured")
        if self.capsule_installer is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but capsule installer is not configured",
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "capsule_installer_not_configured")

        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but no verified bundled capsule is available",
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "verified_capsule_not_available")

        install_state = self.capsule_installer.get_state(capsule_lock)
        if install_state.status is not InstallStatus.READY:
            self.log_store.add(
                "warning",
                "Workflow runner start blocked because workflow is not ready",
                "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
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
        memory_decision = None
        try:
            if self.memory_service is not None:
                memory_decision = self.memory_service.decision_for_runner_start(
                    workflow_id=workflow_id,
                    capsule_lock=capsule_lock,
                    install_state=install_state,
                    spec=spec,
                )
                if memory_decision is not None:
                    self.memory_service.record_metric(f"runner_start_decision_{memory_decision.action.value}")
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
                        self.memory_service.record_metric("runner_start_queued_pending_memory")
                        return {
                            "workflow_id": workflow_id,
                            "status": RunnerStatus.QUEUED_PENDING_MEMORY.value,
                            "queue_id": queued.queue_id,
                            "runner": queued_behind.model_dump() if queued_behind is not None else None,
                            "pid": queued_behind.pid if queued_behind is not None else None,
                            "install_status": InstallStatus.READY.value,
                            "error": None,
                            "memory_decision": memory_decision.model_dump(mode="json"),
                            "memory_status": self.memory_service.memory_status_payload(memory_decision, queue_id=queued.queue_id),
                        }
                    if memory_decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY:
                        self.memory_service.record_metric("runner_start_blocked_by_memory")
                        return {
                            "workflow_id": workflow_id,
                            "status": RunnerStatus.BLOCKED_BY_MEMORY.value,
                            "runner": None,
                            "pid": None,
                            "install_status": InstallStatus.READY.value,
                            "error": memory_decision.user_message,
                            "memory_decision": memory_decision.model_dump(mode="json"),
                            "memory_status": self.memory_service.memory_status_payload(memory_decision),
                        }
                    if memory_decision.action is MemoryDecisionAction.EVICT_THEN_START:
                        for evict_runner_id in memory_decision.evict_runner_ids:
                            stopped = await self.runner_process_coordinator.stop_runner(evict_runner_id)
                            self.memory_service.record_metric("idle_runner_evicted_for_memory")
                            self.log_store.add(
                                "info",
                                "Evicted idle runner before Memory Governor admitted workflow runner",
                                "runtime.runners.lifecycle_service",
                                workflow_id=workflow_id,
                                details={
                                    "evicted_runner_id": evict_runner_id,
                                    "stop_status": stopped.status.value,
                                    "memory_decision_id": memory_decision.decision_id,
                                    "reason": memory_decision.reason_code,
                                },
                            )
                        release_check = self.memory_service.wait_for_memory_release_after_cleanup(memory_decision)
                        if release_check is not None and release_check.status is not MemoryReleaseStatus.RELEASED:
                            self.log_store.add(
                                "warning",
                                "Memory cleanup did not release enough memory",
                                "runtime.runners.lifecycle_service",
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
                                    **self.memory_service.memory_status_payload(memory_decision),
                                    "state": "memory_cleanup_failed",
                                    "message": "Noofy freed memory, but the machine still does not have enough available memory.",
                                },
                                "memory_release_check": release_check.model_dump(mode="json"),
                            }
            if memory_decision is None and decision.action is RunnerSelectionAction.SWITCH and decision.evict_runner_id is not None:
                stopped = await self.runner_process_coordinator.stop_runner(decision.evict_runner_id)
                self.log_store.add(
                    "info",
                    "Evicted idle runner before workflow runner switch",
                    "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
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
            "runtime.runners.lifecycle_service",
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
            "memory_status": self.memory_service.memory_status_payload(memory_decision) if (self.memory_service is not None and memory_decision is not None) else None,
        }

    async def stop_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        """Stop the isolated runner currently bound to a workflow."""
        if self.runner_process_coordinator is None:
            self.log_store.add(
                "warning",
                "Workflow runner stop requested but runner coordinator is not configured",
                "runtime.runners.lifecycle_service",
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
                "runtime.runners.lifecycle_service",
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
            "runtime.runners.lifecycle_service",
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        if capsule_lock.workflow.package_id != workflow_id and _imported_workflow_id_str(capsule_lock) != workflow_id:
            return None
        if capsule_lock.workflow.trust_level not in _PREPARABLE_TRUST_LEVELS:
            return None
        if capsule_lock.trust.level not in _PREPARABLE_TRUST_LEVELS:
            return None
        return capsule_lock

    def _runner_launch_spec(self, capsule_lock: CapsuleLock, install_state: InstallState) -> RunnerLaunchSpec:
        assert self.runtime_manager is not None, "runtime_manager required for runner launch"
        dependency_env_path, runner_workspace_path = _prepared_runtime_paths(install_state, capsule_lock)
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

    def _install_payload(
        self,
        workflow_id: str,
        state: InstallState,
        *,
        capsule_lock: CapsuleLock | None = None,
    ) -> dict[str, object]:
        return sanitize({
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
        })

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

    @staticmethod
    def _runner_id_for_capsule(capsule_lock: CapsuleLock) -> str:
        raw = capsule_lock.runtime.runner_fingerprint
        safe = "".join(char if char.isalnum() else "-" for char in raw.lower()).strip("-")
        return f"workflow-{capsule_lock.workflow.package_id}-{safe}"


# ------------------------------------------------------------------
# Module-level helpers used directly by factory.py and tests.
# ------------------------------------------------------------------

def _imported_workflow_id_str(capsule_lock: CapsuleLock) -> str:
    return imported_workflow_id(
        capsule_lock.workflow.publisher_id,
        capsule_lock.workflow.package_id,
        capsule_lock.workflow.version,
    )


def _workflow_runner_launch_spec(
    capsule_lock: CapsuleLock,
    *,
    dependency_env_path: Path,
    runner_workspace_path: Path,
    runtime_manager: RuntimeManager,
    runner_id_suffix: str | None = None,
) -> RunnerLaunchSpec:
    runner_id = WorkflowRunnerLifecycleService._runner_id_for_capsule(capsule_lock)
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


def _memory_class_for_runtime_backend(gpu_backend: str) -> RunnerMemoryClass:
    return RunnerMemoryClass.CPU_ONLY if gpu_backend.lower() == "cpu" else RunnerMemoryClass.GPU_HEAVY


def _prepared_runtime_paths(install_state: InstallState, capsule_lock: CapsuleLock) -> tuple[Path, Path]:
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
    from app.artifacts import AssetOwnership
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


def _install_developer_details(state: InstallState) -> dict[str, object]:
    details: dict[str, object] = {}
    if state.last_error is not None:
        details["last_error"] = state.last_error
    details["smoke_test_report"] = state.smoke_test_report.model_dump(mode="json")
    details["dependency_env_path"] = state.dependency_env_path
    details["runner_workspace_path"] = state.runner_workspace_path
    return sanitize(details)


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
    for workflow_id in (capsule_lock.workflow.package_id, _imported_workflow_id_str(capsule_lock)):
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
