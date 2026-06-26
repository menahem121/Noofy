"""Workflow-bound runner lifecycle: leases, queued starts, prepare, start, stop.

This service owns the full runner lifecycle for isolated community-workflow
runners.  The memory-governor decisions for runner start are delegated to
MemoryGovernorService, which is injected as an optional dependency so that
environments without memory observation still work.
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from app.core.config import settings
from app.diagnostics import DiagnosticsSink, sanitize
from app.runtime.capsule_installer import CapsuleInstaller, CapsuleInstallError
from app.runtime.install_state import user_facing_install_message
from app.runtime.dependencies.isolation import (
    CapsuleLock,
    DependencyEnvManifest,
    InstallState,
    InstallStatus,
    InstalledModelReference,
    RunnerWorkspaceManifest,
    SmokeStageStatus,
    SmokeTestStatus,
    TrustLevel,
)
from app.runtime.manager import RuntimeManager
from app.runtime.comfyui.launch_settings import (
    comfyui_attention_args,
    comfyui_precision_args,
    comfyui_preview_args,
    comfyui_vram_args,
)
from app.runtime.memory.memory_governor import (
    MemoryDecisionAction,
    MemoryReleaseStatus,
)
from app.runtime.models.model_store import LocalModelRequirement
from app.runtime.runners.runner_coordinator import (
    RunnerProcessCoordinator,
    RunnerRuntimeActivationInProgressError,
)
from app.runtime.runners.runner_process import RunnerLaunchSpec
from app.runtime.python_abi import detect_python_major_minor
from app.runtime.smoke_test import SmokeExecutionFixture
from app.runtime.runners.supervisor import (
    QueuedRunnerStartKind,
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    QueuedRunnerStartStatus,
    RunnerSelectionAction,
    RunnerStatus,
    RunnerSupervisor,
)
from app.source_policy import ModelSourceTrust, SourcePolicy
from app.trust import capsule_source_policy, workflow_source_policy
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.importer import ImportedWorkflowPackageStore, NoofyImportError
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.model_grouping import unique_required_models
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import imported_workflow_id

_PREPARABLE_TRUST_LEVELS = {
    TrustLevel.NOOFY_VERIFIED,
    TrustLevel.REGISTRY_LOCKED,
    TrustLevel.QUARANTINED_COMMUNITY,
}
_SLOW_RUNNER_START_STAGE_SECONDS = 1.0


class PreparedRuntimeArtifactError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        start_status: str = "needs_reprepare",
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.start_status = start_status


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
        workflow_summary: Callable[[WorkflowPackage], dict[str, object]] | None = None,
        has_pending_workflow_runs: Callable[[str], bool] | None = None,
        closed_view_auto_release_enabled: bool | None = None,
        closed_view_release_retry_seconds: float = 10.0,
        workflow_lease_ttl_seconds: float | None = None,
        workflow_lease_sweep_interval_seconds: float | None = None,
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
        self.workflow_summary = workflow_summary
        self.has_pending_workflow_runs = has_pending_workflow_runs
        self._closed_view_auto_release_enabled = closed_view_auto_release_enabled
        self._closed_view_release_retry_seconds = max(
            0.01, closed_view_release_retry_seconds
        )
        self._closed_view_release_tasks: dict[str, asyncio.Task[None]] = {}
        self._closed_view_release_last_outcomes: dict[
            str, tuple[str, str, str | None]
        ] = {}
        self._workflow_lease_ttl_seconds = max(
            0.01,
            workflow_lease_ttl_seconds
            if workflow_lease_ttl_seconds is not None
            else settings.workflow_lease_ttl_seconds,
        )
        self._workflow_lease_sweep_interval_seconds = max(
            0.01,
            workflow_lease_sweep_interval_seconds
            if workflow_lease_sweep_interval_seconds is not None
            else settings.workflow_lease_sweep_interval_seconds,
        )
        self._workflow_lease_sweeper_task: asyncio.Task[None] | None = None
        self._workflow_preparation_tasks: dict[str, asyncio.Task[dict[str, object]]] = {}

    # ------------------------------------------------------------------
    # Queued start handoff and cancellation (existing)
    # ------------------------------------------------------------------

    async def handoff_next_queued_runner_start(
        self,
        *,
        released_runner_id: str | None = None,
    ) -> dict[str, object] | None:
        queued = self.runner_supervisor.claim_next_queued_runner_start(
            released_runner_id=released_runner_id,
        )
        if queued is None:
            return None
        if self._runner_start_canceled(queued.queue_id):
            return self._canceled_runner_start_payload(queued)
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
        if self._runner_start_canceled(queued.queue_id):
            if result.get("status") in {
                RunnerStatus.READY.value,
                RunnerStatus.IDLE.value,
                RunnerStatus.IDLE_WARM.value,
            }:
                await self.stop_workflow_runner(queued.workflow_id)
            return self._canceled_runner_start_payload(queued)
        if result.get("status") in {
            RunnerStatus.READY.value,
            RunnerStatus.IDLE.value,
            RunnerStatus.IDLE_WARM.value,
        }:
            queue_status = QueuedRunnerStartStatus.SUBMITTED
        elif result.get("status") in {
            RunnerStatus.QUEUED_PENDING_MEMORY.value,
            RunnerStatus.QUEUED_PENDING_SWITCH.value,
        }:
            queue_status = QueuedRunnerStartStatus.REQUEUED
        else:
            queue_status = QueuedRunnerStartStatus.FAILED
        self.runner_supervisor.finish_queued_runner_start(
            queued.queue_id,
            status=queue_status,
            reason=str(result.get("error") or result.get("status")),
        )
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
            return {
                "workflow_id": workflow_id,
                "status": "no_runner",
                "lease_id": None,
                "runner": None,
            }

        lease_id = self.runner_supervisor.open_workflow_lease(workflow_id, runner.runner_id)
        self._ensure_workflow_lease_sweeper()
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

    def heartbeat_workflow_runner_lease(
        self, workflow_id: str, lease_id: str
    ) -> dict[str, object]:
        self.workflow_loader.get_package(workflow_id)
        updated = self.runner_supervisor.heartbeat_workflow_lease(
            lease_id, workflow_id=workflow_id
        )
        if updated is None:
            return {
                "workflow_id": workflow_id,
                "status": "lease_not_found",
                "lease_id": lease_id,
                "runner": None,
            }
        return {
            "workflow_id": workflow_id,
            "status": "active",
            "lease_id": lease_id,
            "runner": updated.model_dump(),
        }

    def close_workflow_runner_lease(self, workflow_id: str, lease_id: str) -> dict[str, object]:
        self.workflow_loader.get_package(workflow_id)
        updated = self.runner_supervisor.close_workflow_lease(
            lease_id, workflow_id=workflow_id
        )
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
        self._maybe_schedule_closed_view_release(updated)
        return {
            "workflow_id": workflow_id,
            "status": updated.status.value,
            "lease_id": lease_id,
            "runner": updated.model_dump(),
        }

    def _ensure_workflow_lease_sweeper(self) -> None:
        existing = self._workflow_lease_sweeper_task
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._sweep_stale_workflow_leases())
        self._workflow_lease_sweeper_task = task

        def remove_finished_task(done: asyncio.Task[None]) -> None:
            if self._workflow_lease_sweeper_task is done:
                self._workflow_lease_sweeper_task = None
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                self.log_store.add(
                    "warning",
                    "Workflow view lease sweeper failed",
                    "runtime.runners.lifecycle_service",
                    details={
                        "error": str(error),
                        "error_type": type(error).__name__,
                    },
                )

        task.add_done_callback(remove_finished_task)

    async def _sweep_stale_workflow_leases(self) -> None:
        # Stay alive once started. Exiting when the lease set becomes empty can
        # race with a new lease opening while this task is finishing, leaving
        # the new lease without TTL enforcement.
        while True:
            await asyncio.sleep(self._workflow_lease_sweep_interval_seconds)
            expired = self.runner_supervisor.expire_stale_workflow_leases(
                self._workflow_lease_ttl_seconds
            )
            for item in expired:
                self.log_store.add(
                    "info",
                    "Workflow view lease expired without heartbeat",
                    "runtime.runners.lifecycle_service",
                    workflow_id=item.workflow_id,
                    details={
                        "runner_id": item.runner.runner_id,
                        "lease_id": item.lease_id,
                        "ttl_seconds": self._workflow_lease_ttl_seconds,
                        "open_workflow_lease_count": item.runner.open_workflow_lease_count,
                        "closed_view_cooldown_expires_at": item.runner.closed_view_cooldown_expires_at,
                    },
                )
                self._maybe_schedule_closed_view_release(item.runner)

    # ------------------------------------------------------------------
    # Closed-view cooldown release for isolated runners
    # ------------------------------------------------------------------
    #
    # Closing the last workflow view starts the supervisor's closed-view
    # cooldown. The frontend only ever closes its lease; deciding whether the
    # bound isolated runner can actually be released stays here, after the
    # cooldown, with live re-checks. The core runner is never released through
    # this path, and the Memory Governor may still evict a zero-lease idle
    # runner earlier when admission needs the memory.

    _CLOSED_VIEW_RELEASABLE_STATUSES = frozenset(
        {
            RunnerStatus.READY,
            RunnerStatus.IDLE,
            RunnerStatus.IDLE_WARM,
            RunnerStatus.CO_RESIDENT,
            RunnerStatus.RELEASE_FAILED,
        }
    )

    # Skip reasons the deferred release keeps retrying because the blocker is
    # expected to clear (work finishes, streams close, transient states pass).
    # Anything else — released elsewhere, view reopened, or a runner state the
    # eviction reservation can never accept (failed, unreachable, unknown) —
    # ends the retry task instead of polling forever.
    _RETRYABLE_CLOSED_VIEW_SKIP_REASONS = frozenset(
        {
            "active_job",
            "output_stream_active",
            "runner_reserved",
            "runner_reservation_unavailable",
            "queued_runner_start_pending",
            "queued_workflow_run_pending",
            "runtime_activation_in_progress",
        }
        | {
            f"runner_status_{status.value}"
            for status in (
                RunnerStatus.STARTING,
                RunnerStatus.PREPARING,
                RunnerStatus.RUNNING,
                RunnerStatus.QUEUED,
                RunnerStatus.QUEUED_PENDING_SWITCH,
                RunnerStatus.QUEUED_PENDING_MEMORY,
                RunnerStatus.RESERVING,
                RunnerStatus.SUBMITTING,
                RunnerStatus.STOPPING,
                RunnerStatus.SWITCHING,
                RunnerStatus.LOADING_MODEL,
                RunnerStatus.RETRYING_AFTER_MEMORY_CLEANUP,
                RunnerStatus.WAITING_FOR_MEMORY_RELEASE,
                RunnerStatus.EVICTING_RUNNER,
            )
        }
    )

    @property
    def closed_view_auto_release_enabled(self) -> bool:
        if self._closed_view_auto_release_enabled is not None:
            return self._closed_view_auto_release_enabled
        return settings.closed_view_auto_release_enabled

    def _maybe_schedule_closed_view_release(self, descriptor: RunnerDescriptor) -> None:
        if not self.closed_view_auto_release_enabled:
            return
        if self.runner_process_coordinator is None:
            return
        if descriptor.kind is not RunnerKind.ISOLATED_COMFYUI:
            return
        if descriptor.open_workflow_lease_count > 0:
            return
        if descriptor.closed_view_cooldown_expires_at is None:
            return
        runner_id = descriptor.runner_id
        self._closed_view_release_last_outcomes.pop(runner_id, None)
        existing = self._closed_view_release_tasks.get(runner_id)
        if existing is not None and not existing.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Without a running loop the deferred release cannot be scheduled;
            # the Memory Governor's opportunistic eviction remains the fallback.
            return
        task = loop.create_task(self._release_closed_view_runner_when_safe(runner_id))
        self._closed_view_release_tasks[runner_id] = task

        def remove_finished_task(done: asyncio.Task[None]) -> None:
            if self._closed_view_release_tasks.get(runner_id) is done:
                self._closed_view_release_tasks.pop(runner_id, None)
            try:
                error = done.exception()
            except asyncio.CancelledError:
                return
            if error is not None:
                self.log_store.add(
                    "warning",
                    "Closed-view runner release task failed",
                    "runtime.runners.lifecycle_service",
                    details={
                        "runner_id": runner_id,
                        "error": str(error),
                        "error_type": type(error).__name__,
                    },
                )

        task.add_done_callback(remove_finished_task)

    async def _release_closed_view_runner_when_safe(self, runner_id: str) -> None:
        """Keep checking an expired closed view until its runner is safe to stop."""
        while True:
            descriptor = self.runner_supervisor.get_runner(runner_id)
            if (
                descriptor.kind is not RunnerKind.ISOLATED_COMFYUI
                or descriptor.open_workflow_lease_count > 0
                or descriptor.closed_view_cooldown_expires_at is None
            ):
                return
            delay = self.runner_supervisor.closed_view_cooldown_remaining_seconds(
                runner_id
            )
            if delay is None:
                return
            if delay > 0:
                # Small grace so the next check observes an expired timestamp.
                await asyncio.sleep(delay + 0.05)
                continue
            if self.runner_supervisor.runtime_activation_in_progress():
                outcome = self._closed_view_release_outcome(
                    descriptor,
                    status="skipped",
                    reason="runtime_activation_in_progress",
                )
            else:
                outcome = await self._try_release_closed_view_runner(descriptor)
            if outcome["status"] == "released":
                return
            if (
                outcome["status"] != "failed"
                and outcome["reason"] not in self._RETRYABLE_CLOSED_VIEW_SKIP_REASONS
            ):
                return
            # Failed stops leave the runner release_failed with its cooldown
            # intact, so they retry alongside the transient skip reasons.
            await asyncio.sleep(self._closed_view_release_retry_seconds)

    async def release_closed_view_runners(self) -> list[dict[str, object]]:
        """Stop isolated runners whose closed-view cooldown expired, when safe."""
        results: list[dict[str, object]] = []
        if not self.closed_view_auto_release_enabled:
            return results
        if self.runner_process_coordinator is None:
            return results
        if self.runner_supervisor.runtime_activation_in_progress():
            # A ComfyUI runtime switch already owns runner teardown.
            return results
        for descriptor in self.runner_supervisor.expired_closed_view_runners():
            results.append(await self._try_release_closed_view_runner(descriptor))
        return results

    async def _try_release_closed_view_runner(
        self, descriptor: RunnerDescriptor
    ) -> dict[str, object]:
        runner_id = descriptor.runner_id
        skip_reason = self._closed_view_release_skip_reason(descriptor)
        if skip_reason is not None:
            return self._closed_view_release_outcome(
                descriptor, status="skipped", reason=skip_reason
            )
        reservation = self.runner_supervisor.reserve_runner_for_eviction(runner_id)
        if reservation is None:
            return self._closed_view_release_outcome(
                descriptor, status="skipped", reason="runner_reservation_unavailable"
            )
        # Re-check every safety condition from live state after atomically
        # reserving. New submissions cannot claim the runner while this
        # eviction reservation is held.
        current = self.runner_supervisor.get_runner(runner_id)
        live_skip_reason = self._closed_view_release_skip_reason(
            current,
            expected_reservation_token=reservation.token,
        )
        if live_skip_reason is not None:
            self.runner_supervisor.rollback_runner_reservation(reservation.token)
            return self._closed_view_release_outcome(
                current, status="skipped", reason=live_skip_reason
            )
        try:
            stopped = await self.runner_process_coordinator.stop_runner(runner_id)
        except Exception as exc:
            self.runner_supervisor.fail_runner_memory_release(reservation.token)
            return self._closed_view_release_outcome(
                descriptor,
                status="failed",
                reason="runner_stop_error",
                error=str(exc),
            )
        if stopped.status is not RunnerStatus.STOPPED:
            self.runner_supervisor.fail_runner_memory_release(reservation.token)
            return self._closed_view_release_outcome(
                descriptor,
                status="failed",
                reason=f"runner_stop_status_{stopped.status.value}",
            )
        self.runner_supervisor.confirm_runner_memory_released(reservation.token)
        self.runner_supervisor.update_runner_status(
            runner_id, RunnerStatus.EVICTED_AFTER_COOLDOWN
        )
        record_metric = getattr(self.memory_service, "record_metric", None)
        if callable(record_metric):
            record_metric("idle_runner_released_after_closed_view_cooldown")
        outcome = self._closed_view_release_outcome(
            descriptor, status="released", reason="closed_view_cooldown_expired"
        )
        # The freed memory may unblock a queued runner start.
        try:
            await self.handoff_next_queued_runner_start(released_runner_id=runner_id)
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Queued runner start handoff failed after closed-view release",
                "runtime.runners.lifecycle_service",
                details={
                    "runner_id": runner_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
        return outcome

    def _closed_view_release_skip_reason(
        self,
        descriptor: RunnerDescriptor,
        *,
        expected_reservation_token: str | None = None,
    ) -> str | None:
        if descriptor.kind is not RunnerKind.ISOLATED_COMFYUI:
            return "not_isolated_runner"
        if descriptor.open_workflow_lease_count > 0:
            return "workflow_view_reopened"
        if descriptor.closed_view_cooldown_expires_at is None:
            return "workflow_view_reopened"
        if descriptor.current_job_id is not None:
            return "active_job"
        if descriptor.output_stream_lease_count > 0:
            return "output_stream_active"
        if descriptor.reservation_token not in {
            None,
            expected_reservation_token,
        }:
            return "runner_reserved"
        allowed_statuses = self._CLOSED_VIEW_RELEASABLE_STATUSES
        if expected_reservation_token is not None:
            allowed_statuses = allowed_statuses | {RunnerStatus.EVICTING_RUNNER}
        if descriptor.status not in allowed_statuses:
            return f"runner_status_{descriptor.status.value}"
        for bound_workflow_id in self.runner_supervisor.workflows_bound_to_runner(
            descriptor.runner_id
        ):
            if (
                self.runner_supervisor.queued_runner_start_for_workflow(bound_workflow_id)
                is not None
            ):
                return "queued_runner_start_pending"
            if self.has_pending_workflow_runs is not None and self.has_pending_workflow_runs(
                bound_workflow_id
            ):
                return "queued_workflow_run_pending"
        return None

    def _closed_view_release_outcome(
        self,
        descriptor: RunnerDescriptor,
        *,
        status: str,
        reason: str,
        error: str | None = None,
    ) -> dict[str, object]:
        outcome: dict[str, object] = {
            "runner_id": descriptor.runner_id,
            "status": status,
            "reason": reason,
        }
        if error is not None:
            outcome["error"] = error
        outcome_key = (status, reason, error)
        if self._closed_view_release_last_outcomes.get(descriptor.runner_id) == outcome_key:
            return outcome
        self._closed_view_release_last_outcomes[descriptor.runner_id] = outcome_key
        if status == "released":
            message = "Released isolated workflow runner after closed-view cooldown"
            level = "info"
        elif status == "failed":
            message = "Isolated workflow runner closed-view release failed"
            level = "warning"
        else:
            message = "Isolated workflow runner kept after closed-view cooldown"
            level = "info"
        self.log_store.add(
            level,
            message,
            "runtime.runners.lifecycle_service",
            workflow_id=descriptor.last_workflow_id or descriptor.current_workflow_id,
            details={
                **outcome,
                "open_workflow_lease_count": descriptor.open_workflow_lease_count,
                "closed_view_cooldown_expires_at": descriptor.closed_view_cooldown_expires_at,
            },
        )
        return outcome

    async def shutdown(self) -> None:
        """Cancel pending closed-view release tasks during backend shutdown."""
        sweeper = self._workflow_lease_sweeper_task
        if sweeper is not None:
            sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sweeper
            self._workflow_lease_sweeper_task = None
        tasks = list(self._closed_view_release_tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._closed_view_release_tasks.clear()
        self._closed_view_release_last_outcomes.clear()
        prepare_tasks = list(self._workflow_preparation_tasks.values())
        for task in prepare_tasks:
            task.cancel()
        for task in prepare_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._workflow_preparation_tasks.clear()

    # ------------------------------------------------------------------
    # Install state queries
    # ------------------------------------------------------------------

    def get_install_state(self, workflow_id: str) -> dict[str, object]:
        if self.capsule_loader is None or self.capsule_installer is None:
            return self._unsupported_install_payload(workflow_id)
        try:
            package = self.workflow_loader.get_package(workflow_id)
        except KeyError:
            return self._unsupported_install_payload(workflow_id)
        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            return self._unsupported_install_payload(workflow_id)
        state = self.capsule_installer.get_state(capsule_lock)
        return self._install_payload(
            workflow_id,
            state,
            capsule_lock=capsule_lock,
            package=package,
            requires_preparation=_workflow_requires_isolated_preparation(
                package,
                capsule_lock,
            ),
        )

    def get_install_state_developer_details(self, workflow_id: str) -> dict[str, object]:
        if self.capsule_loader is None or self.capsule_installer is None:
            return {"workflow_id": workflow_id, "developer_details": {}}
        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            return {"workflow_id": workflow_id, "developer_details": {}}
        state = self.capsule_installer.get_state(capsule_lock)
        details = _install_developer_details(state)
        details.update(self.capsule_installer.developer_details(state))
        return {
            "workflow_id": workflow_id,
            "developer_details": sanitize(details),
        }

    def workflow_status(self, workflow_id: str) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        install = self.get_install_state(workflow_id)
        runner = self.runner_supervisor.runner_for_workflow(workflow_id)
        return {
            "workflow_id": workflow_id,
            "workflow": self._workflow_summary(package),
            "install": install,
            "required_actions": _required_actions_for_workflow(package, install),
            "compatibility_guidance": _compatibility_guidance(package),
            "runner": runner.model_dump(mode="json") if runner is not None else None,
            "runner_status": runner.status.value if runner is not None else "not_started",
            "can_prepare": _install_status_can_prepare(install.get("status")),
            "can_cancel_preparation": False,
            "can_cancel_job": runner.current_job_id is not None if runner is not None else False,
        }

    def cancel_preparation(self, workflow_id: str) -> dict[str, object]:
        self.workflow_loader.get_package(workflow_id)
        self.log_store.add(
            "info",
            "Workflow preparation cancellation requested",
            "runtime.runners.lifecycle_service",
            workflow_id=workflow_id,
            details={"status": "no_active_cancelable_preparation"},
        )
        return {
            "workflow_id": workflow_id,
            "status": "no_active_cancelable_preparation",
            "user_facing_message": "No preparation is currently running for this workflow.",
            "cancelable": False,
        }

    async def resolve_workflow_engine_nodes_from_urls(
        self,
        workflow_id: str,
        *,
        urls_by_node_type: dict[str, str],
    ) -> dict[str, object]:
        if self.imported_package_store is None:
            raise RuntimeError("Imported workflow remediation is not configured.")
        package = self.workflow_loader.get_package(workflow_id)
        resolved = self.imported_package_store.resolve_custom_nodes_from_github_urls(
            package,
            urls_by_node_type=urls_by_node_type,
            allow_unverified_community_preparation=_community_preparation_opted_in(
                package
            ),
        )
        self.imported_package_store.persist_custom_node_resolution(resolved)
        self.log_store.add(
            "info",
            "Committed workflow node remediation source provided",
            "runtime.runners.lifecycle_service",
            workflow_id=workflow_id,
            details={
                "node_types": sorted(urls_by_node_type),
                "source_resolution": _workflow_source_resolution(resolved),
            },
        )
        return await self.prepare_workflow(workflow_id)

    def _workflow_summary(self, package: WorkflowPackage) -> dict[str, object]:
        if self.workflow_summary is not None:
            return self.workflow_summary(package)
        return {
            "id": package.metadata.id,
            "name": package.metadata.name,
            "version": package.metadata.version,
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
        cached = self._cached_ready_prepare_payload(workflow_id)
        if cached is not None:
            return cached
        existing = self._workflow_preparation_tasks.get(workflow_id)
        if existing is not None and not existing.done():
            self.log_store.add(
                "info",
                "Joining existing workflow preparation",
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
            )
            return await existing
        if existing is not None and existing.done():
            self._workflow_preparation_tasks.pop(workflow_id, None)
        task = asyncio.create_task(self._prepare_workflow_with_reservation(workflow_id))
        self._workflow_preparation_tasks[workflow_id] = task
        try:
            return await task
        finally:
            if self._workflow_preparation_tasks.get(workflow_id) is task:
                self._workflow_preparation_tasks.pop(workflow_id, None)

    async def _prepare_workflow_with_reservation(self, workflow_id: str) -> dict[str, object]:
        if not self.runner_supervisor.begin_workflow_preparation(workflow_id):
            return {
                "workflow_id": workflow_id,
                "status": RunnerStatus.QUEUED_PENDING_SWITCH.value,
                "user_facing_message": "Waiting for the ComfyUI update to finish.",
                "cancelable": False,
                "error": "ComfyUI runtime activation is in progress.",
            }
        try:
            return await self._prepare_workflow(workflow_id)
        finally:
            self.runner_supervisor.end_workflow_preparation(workflow_id)

    def _cached_ready_prepare_payload(self, workflow_id: str) -> dict[str, object] | None:
        if self.capsule_loader is None or self.capsule_installer is None:
            return None
        capsule_lock = self._preparable_capsule_lock(workflow_id)
        if capsule_lock is None:
            return None
        state = self.capsule_installer.get_state(capsule_lock)
        if state.status not in {
            InstallStatus.READY,
            InstallStatus.PREPARED_NEEDS_INPUT_SETUP,
        }:
            return None
        package = self.workflow_loader.get_package(workflow_id)
        payload = self._install_payload(
            workflow_id,
            state,
            capsule_lock=capsule_lock,
            package=package,
            requires_preparation=_workflow_requires_isolated_preparation(
                package,
                capsule_lock,
            ),
        )
        payload["reused_cached_preparation"] = True
        self.log_store.add(
            "info",
            "Reusing cached workflow preparation",
            "runtime.runners.lifecycle_service",
            workflow_id=workflow_id,
            details={
                "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                "install_status": state.status.value,
            },
        )
        return payload

    async def _prepare_workflow(self, workflow_id: str) -> dict[str, object]:
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
            return self._install_payload(
                workflow_id,
                state,
                capsule_lock=capsule_lock,
                package=package,
            )

        try:
            state = await self.capsule_installer.prepare(
                capsule_lock,
                workflow_id=workflow_id,
                local_model_requirements=local_model_requirements,
                workflow_execution_smoke_allowed=not package.unresolved_runtime_inputs,
            )
        except CapsuleInstallError as exc:
            remediated = await self._try_engine_missing_node_remediation(
                workflow_id=workflow_id,
                package=package,
                capsule_lock=capsule_lock,
                local_model_requirements=local_model_requirements,
                workflow_execution_smoke_allowed=not package.unresolved_runtime_inputs,
                failure=exc,
            )
            if remediated is not None:
                return remediated
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
            return self._install_payload(
                workflow_id,
                exc.state,
                capsule_lock=capsule_lock,
                package=package,
            )
        return self._install_payload(
            workflow_id,
            state,
            capsule_lock=capsule_lock,
            package=package,
        )

    async def _try_engine_missing_node_remediation(
        self,
        *,
        workflow_id: str,
        package: WorkflowPackage,
        capsule_lock: CapsuleLock,
        local_model_requirements: list[LocalModelRequirement],
        workflow_execution_smoke_allowed: bool,
        failure: CapsuleInstallError,
    ) -> dict[str, object] | None:
        missing_node_types = _engine_unrecognized_missing_node_types(failure.state)
        if not missing_node_types:
            return None
        if self.imported_package_store is None or package.import_metadata is None:
            return None
        self.log_store.add(
            "info",
            "Workflow preparation found engine-unrecognized nodes; attempting automatic resolution",
            "runtime.runners.lifecycle_service",
            workflow_id=workflow_id,
            details={
                "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                "missing_node_types": missing_node_types,
            },
        )
        remediated_package = self.imported_package_store.resolve_missing_engine_nodes_automatically(
            package,
            missing_node_types=missing_node_types,
            allow_unverified_community_preparation=_community_preparation_opted_in(package),
        )
        self.imported_package_store.persist_custom_node_resolution(remediated_package)
        if not _engine_auto_resolution_attempted(remediated_package):
            self.log_store.add(
                "warning",
                "Workflow preparation needs user node resolution",
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
                details={
                    "missing_node_types": missing_node_types,
                    "source_resolution": _workflow_source_resolution(remediated_package),
                },
            )
            return self._install_payload(
                workflow_id,
                failure.state,
                capsule_lock=capsule_lock,
                package=remediated_package,
            )

        refreshed_capsule_lock = self._preparable_capsule_lock(workflow_id)
        if refreshed_capsule_lock is None:
            return self._install_payload(
                workflow_id,
                failure.state,
                capsule_lock=capsule_lock,
                package=remediated_package,
            )
        local_model_requirements = _local_model_requirements(
            remediated_package,
            refreshed_capsule_lock,
        )
        refreshed_capsule_lock = refreshed_capsule_lock.model_copy(
            update={
                "source_policy": _effective_prepare_source_policy(
                    remediated_package,
                    refreshed_capsule_lock,
                    local_model_requirements=local_model_requirements,
                )
            }
        )
        try:
            retry_state = await self.capsule_installer.prepare(
                refreshed_capsule_lock,
                workflow_id=workflow_id,
                local_model_requirements=local_model_requirements,
                workflow_execution_smoke_allowed=workflow_execution_smoke_allowed,
            )
        except CapsuleInstallError as retry_exc:
            retry_missing_node_types = _engine_unrecognized_missing_node_types(
                retry_exc.state
            )
            if retry_missing_node_types:
                fallback_package = self.imported_package_store.with_engine_unrecognized_nodes(
                    remediated_package,
                    missing_node_types=retry_missing_node_types,
                    reason="automatic_resolution_retry_still_missing",
                    automatic_resolution_failures=[
                        "Noofy staged a candidate automatically, but the current engine still does not recognize these nodes."
                    ],
                )
                self.imported_package_store.persist_custom_node_resolution(
                    fallback_package
                )
                return self._install_payload(
                    workflow_id,
                    retry_exc.state,
                    capsule_lock=refreshed_capsule_lock,
                    package=fallback_package,
                )
            return self._install_payload(
                workflow_id,
                retry_exc.state,
                capsule_lock=refreshed_capsule_lock,
                package=remediated_package,
            )
        self.log_store.add(
            "info",
            "Workflow preparation succeeded after automatic node resolution",
            "runtime.runners.lifecycle_service",
            workflow_id=workflow_id,
            details={
                "missing_node_types": missing_node_types,
                "capsule_fingerprint": refreshed_capsule_lock.runtime.capsule_fingerprint,
            },
        )
        return self._install_payload(
            workflow_id,
            retry_state,
            capsule_lock=refreshed_capsule_lock,
            package=remediated_package,
        )

    async def start_workflow_runner(
        self,
        workflow_id: str,
        *,
        memory_status_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, object]:
        """Start and bind an isolated runner for a prepared verified workflow."""
        if self.runner_supervisor.runtime_activation_in_progress():
            return {
                "workflow_id": workflow_id,
                "status": RunnerStatus.QUEUED_PENDING_SWITCH.value,
                "runner": None,
                "pid": None,
                "install_status": None,
                "error": "ComfyUI runtime activation is in progress.",
            }
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

        capsule_lookup_started_at = time.monotonic()
        capsule_lock = self._preparable_capsule_lock(workflow_id)
        self._log_slow_runner_start_stage(
            workflow_id,
            "capsule_lock_lookup",
            time.monotonic() - capsule_lookup_started_at,
        )
        if capsule_lock is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but no verified bundled capsule is available",
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "verified_capsule_not_available")

        install_state_lookup_started_at = time.monotonic()
        install_state = self.capsule_installer.get_state(capsule_lock)
        self._log_slow_runner_start_stage(
            workflow_id,
            "install_state_lookup",
            time.monotonic() - install_state_lookup_started_at,
            install_status=install_state.status.value,
        )
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

        reused = self._reuse_resident_runner_from_ready_install_state(
            workflow_id,
            capsule_lock,
            install_state,
        )
        if reused is not None:
            return reused

        try:
            launch_spec_started_at = time.monotonic()
            spec = self._runner_launch_spec(capsule_lock, install_state)
            self._log_slow_runner_start_stage(
                workflow_id,
                "runner_launch_spec",
                time.monotonic() - launch_spec_started_at,
            )
        except PreparedRuntimeArtifactError as exc:
            self.log_store.add(
                "warning",
                "Workflow runner start needs workflow reprepare",
                "runtime.runners.lifecycle_service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "reason_code": exc.reason_code,
                    "error": str(exc),
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": exc.start_status,
                "runner": None,
                "pid": None,
                "install_status": install_state.status.value,
                "reason_code": exc.reason_code,
                "error": str(exc),
            }
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
            queued = self._enqueue_runner_start_once(
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
                memory_decision_started_at = time.monotonic()
                memory_decision = self.memory_service.decision_for_runner_start(
                    workflow_id=workflow_id,
                    capsule_lock=capsule_lock,
                    install_state=install_state,
                    spec=spec,
                )
                self._log_slow_runner_start_stage(
                    workflow_id,
                    "memory_decision",
                    time.monotonic() - memory_decision_started_at,
                    memory_action=(
                        memory_decision.action.value
                        if memory_decision is not None
                        else None
                    ),
                )
                if memory_decision is not None:
                    self.memory_service.record_metric(f"runner_start_decision_{memory_decision.action.value}")
                    if memory_status_callback is not None:
                        memory_status_callback(
                            self.memory_service.memory_status_payload(memory_decision)
                        )
                    if memory_decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY:
                        queued_behind = (
                            self.runner_supervisor.get_runner(memory_decision.queued_behind_runner_id)
                            if memory_decision.queued_behind_runner_id is not None
                            else None
                        )
                        queued = self._enqueue_runner_start_once(
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
                        cleaned_up = await self.memory_service.cleanup_idle_runners_for_memory_decision(
                            memory_decision,
                            metric_name="idle_runner_evicted_for_memory",
                            log_source="runtime.runners.lifecycle_service",
                            log_message="Released idle runner memory before Memory Governor admitted workflow runner",
                        )
                        if not cleaned_up:
                            return {
                                "workflow_id": workflow_id,
                                "status": RunnerStatus.MEMORY_CLEANUP_FAILED.value,
                                "runner": None,
                                "pid": None,
                                "install_status": InstallStatus.READY.value,
                                "error": "Noofy could not release the idle runner memory needed for this workflow.",
                                "memory_decision": memory_decision.model_dump(mode="json"),
                                "memory_status": {
                                    **self.memory_service.memory_status_payload(memory_decision),
                                    "state": "memory_cleanup_failed",
                                    "message": "Noofy could not release the idle runner memory needed for this workflow.",
                                },
                            }
                        if memory_status_callback is not None:
                            memory_status_callback(
                                {
                                    **self.memory_service.memory_status_payload(memory_decision),
                                    "state": "waiting_for_memory_release",
                                    "message": "Noofy is waiting for the previous workflow's memory to be released.",
                                }
                            )
                        release_check = await self.memory_service.wait_for_memory_release_after_cleanup(memory_decision)
                        capacity_sufficient = (
                            release_check.status
                            is MemoryReleaseStatus.CAPACITY_SUFFICIENT
                        )
                        if release_check.status is not MemoryReleaseStatus.RELEASED:
                            additional_release = await self.memory_service.cleanup_remaining_idle_runners_for_memory_decision(
                                memory_decision
                            )
                            if additional_release is not None:
                                release_check = additional_release
                        if not capacity_sufficient and release_check.status not in {
                            MemoryReleaseStatus.RELEASED,
                            MemoryReleaseStatus.CAPACITY_SUFFICIENT,
                        }:
                            cleanup_succeeded = (
                                release_check.status
                                is MemoryReleaseStatus.RELEASED_INSUFFICIENT_MEMORY
                            )
                            blocked_status = (
                                RunnerStatus.BLOCKED_BY_MEMORY
                                if cleanup_succeeded
                                else RunnerStatus.MEMORY_CLEANUP_FAILED
                            )
                            blocked_state = (
                                "blocked_by_memory"
                                if cleanup_succeeded
                                else "memory_cleanup_failed"
                            )
                            blocked_message = (
                                "Noofy freed memory, but the machine still does not have enough available memory."
                                if cleanup_succeeded
                                else "Noofy could not confirm that enough memory was released for this workflow."
                            )
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
                                    "baseline_free_vram_mb": release_check.baseline_free_vram_mb,
                                    "baseline_free_ram_mb": release_check.baseline_free_ram_mb,
                                    "final_free_vram_mb": release_check.final_free_vram_mb,
                                    "final_free_ram_mb": release_check.final_free_ram_mb,
                                    "blocking_constraints": list(release_check.blocking_constraints),
                                },
                            )
                            return {
                                "workflow_id": workflow_id,
                                "status": blocked_status.value,
                                "runner": None,
                                "pid": None,
                                "install_status": InstallStatus.READY.value,
                                "error": blocked_message,
                                "memory_decision": memory_decision.model_dump(mode="json"),
                                "memory_status": {
                                    **self.memory_service.memory_status_payload(memory_decision),
                                    "state": blocked_state,
                                    "message": blocked_message,
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
            process_start_started_at = time.monotonic()
            handle = await self.runner_process_coordinator.start_runner(spec, workflow_id=workflow_id)
            self._log_slow_runner_start_stage(
                workflow_id,
                "runner_process_start",
                time.monotonic() - process_start_started_at,
                runner_id=handle.runner_id,
            )
        except RunnerRuntimeActivationInProgressError as exc:
            return {
                "workflow_id": workflow_id,
                "status": RunnerStatus.QUEUED_PENDING_SWITCH.value,
                "runner": None,
                "pid": None,
                "install_status": InstallStatus.READY.value,
                "error": str(exc),
            }
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

    def _runner_start_canceled(self, queue_id: str) -> bool:
        queued = self.runner_supervisor.get_queued_runner_start(queue_id)
        return queued is not None and (
            queued.cancel_requested
            or queued.status is QueuedRunnerStartStatus.CANCELED
        )

    @staticmethod
    def _canceled_runner_start_payload(queued) -> dict[str, object]:
        return {
            "workflow_id": queued.workflow_id,
            "status": QueuedRunnerStartStatus.CANCELED.value,
            "queue_id": queued.queue_id,
            "runner": None,
            "pid": None,
            "error": None,
            "started_from_queue_id": queued.queue_id,
        }

    def _enqueue_runner_start_once(
        self,
        *,
        workflow_id: str,
        kind: QueuedRunnerStartKind,
        queued_behind_runner_id: str | None,
        reason: str | None,
    ):
        queued = self.runner_supervisor.queued_runner_start_for_workflow(workflow_id)
        if queued is not None:
            return queued
        return self.runner_supervisor.enqueue_runner_start(
            workflow_id=workflow_id,
            kind=kind,
            queued_behind_runner_id=queued_behind_runner_id,
            reason=reason,
        )

    def _preparable_capsule_lock(self, workflow_id: str) -> CapsuleLock | None:
        if self.capsule_loader is None:
            return None
        try:
            capsule_lock = self.capsule_loader.get_bundled_capsule_lock(workflow_id)
        except KeyError:
            capsule_lock = self._refreshed_imported_capsule_lock(workflow_id)
            if capsule_lock is None:
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

    def _refreshed_imported_capsule_lock(
        self,
        workflow_id: str,
    ) -> CapsuleLock | None:
        if self.imported_package_store is None:
            return None
        try:
            package = self.workflow_loader.get_package(workflow_id)
        except KeyError:
            return None
        if not self.imported_package_store.has_package_identity(package):
            return None
        try:
            return self.imported_package_store.refresh_capsule_lock(package)
        except NoofyImportError as exc:
            self.log_store.add(
                "warning",
                "Imported workflow runtime capsule could not be refreshed",
                "workflow.runtime",
                workflow_id=workflow_id,
                details={"error": str(exc)},
            )
            return None

    def _log_slow_runner_start_stage(
        self,
        workflow_id: str,
        stage: str,
        duration_seconds: float,
        **details: object,
    ) -> None:
        if duration_seconds < _SLOW_RUNNER_START_STAGE_SECONDS:
            return
        self.log_store.add(
            "info",
            "Workflow runner start stage was slow",
            "runtime.runners.lifecycle_service",
            workflow_id=workflow_id,
            details={
                "stage": stage,
                "duration_seconds": round(duration_seconds, 3),
                **{key: value for key, value in details.items() if value is not None},
            },
        )

    def _reuse_resident_runner_from_ready_install_state(
        self,
        workflow_id: str,
        capsule_lock: CapsuleLock,
        install_state: InstallState,
    ) -> dict[str, object] | None:
        if install_state.smoke_test_status is not SmokeTestStatus.PASSED:
            return None
        compatibility_key = _ready_install_runner_process_compatibility_key(
            capsule_lock,
            install_state,
        )
        if not compatibility_key:
            return None
        decision = self.runner_supervisor.runner_selection_for(
            runner_process_compatibility_key=compatibility_key,
            memory_class=_memory_class_for_runtime_backend(capsule_lock.runtime.gpu_backend),
        )
        if decision.action is not RunnerSelectionAction.REUSE or decision.runner_id is None:
            return None
        descriptor = self.runner_supervisor.bind_workflow_runner(workflow_id, decision.runner_id)
        self.log_store.add(
            "info",
            "Workflow runner reused",
            "runtime.runners.lifecycle_service",
            workflow_id=workflow_id,
            details={
                "runner_id": descriptor.runner_id,
                "runner_process_compatibility_key": (
                    descriptor.runner_process_compatibility_key
                ),
                "reuse_source": "ready_install_state",
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

    def _runner_launch_spec(self, capsule_lock: CapsuleLock, install_state: InstallState) -> RunnerLaunchSpec:
        assert self.runtime_manager is not None, "runtime_manager required for runner launch"
        assert self.capsule_installer is not None, "capsule_installer required for runner launch"
        if install_state.smoke_test_status is not SmokeTestStatus.PASSED:
            raise ValueError(
                "Prepared runtime smoke test has not passed: "
                f"{install_state.smoke_test_status.value}"
            )
        (
            dependency_env_path,
            runner_workspace_path,
            dependency_env_fingerprint,
            runner_workspace_fingerprint,
        ) = _prepared_runtime_paths(
            install_state,
            capsule_lock,
            expected_python_version=capsule_lock.runtime.python_version,
            model_reference_validator=(
                self.capsule_installer.model_store.validate_installed_model_references_for_launch
            ),
        )
        self._validate_runtime_python_abi(capsule_lock)
        spec = _workflow_runner_launch_spec(
            capsule_lock,
            dependency_env_path=dependency_env_path,
            runner_workspace_path=runner_workspace_path,
            dependency_env_fingerprint=dependency_env_fingerprint,
            runner_workspace_fingerprint=runner_workspace_fingerprint,
            runtime_manager=self.runtime_manager,
        )
        self.log_store.add(
            "info",
            "Effective workflow runner launch configuration",
            "workflow.runtime",
            workflow_id=capsule_lock.workflow.package_id,
            details=_effective_launch_diagnostics(capsule_lock, spec),
        )
        return spec

    def _install_payload(
        self,
        workflow_id: str,
        state: InstallState,
        *,
        capsule_lock: CapsuleLock | None = None,
        package: WorkflowPackage | None = None,
        requires_preparation: bool = True,
    ) -> dict[str, object]:
        # Workflows with nothing to prepare (no custom nodes, so they run on
        # the core runner) would otherwise sit in "pending" forever because the
        # run path never prepares them. Report them as ready so the UI does not
        # keep offering or announcing a preparation that will never happen.
        status = state.status
        if not requires_preparation and status in {
            InstallStatus.PENDING,
            InstallStatus.IMPORTED,
        }:
            status = InstallStatus.READY
        payload = {
            "workflow_id": workflow_id,
            "capsule_fingerprint": state.capsule_fingerprint,
            "status": status.value,
            "user_facing_message": user_facing_install_message(status),
            "requires_preparation": requires_preparation,
            "installed_at": state.installed_at,
            "last_used_at": state.last_used_at,
            "dependency_env_path": state.dependency_env_path,
            "runner_workspace_path": state.runner_workspace_path,
            "smoke_test_status": state.smoke_test_status.value,
            "smoke_test_report": state.smoke_test_report.model_dump(mode="json"),
            "last_error": state.last_error,
            "last_error_code": state.last_error_code,
            "developer_details_available": state.last_error is not None or bool(state.smoke_test_report.model_dump(mode="json")),
            "source_policy": capsule_source_policy(capsule_lock).model_dump(mode="json")
            if capsule_lock is not None
            else None,
        }
        if package is not None:
            node_resolution = _workflow_node_resolution_payload(package)
            if node_resolution is not None:
                payload["custom_node_resolution"] = node_resolution
        return sanitize(payload)

    def _unsupported_install_payload(self, workflow_id: str) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "capsule_fingerprint": None,
            "status": InstallStatus.UNSUPPORTED.value,
            "user_facing_message": user_facing_install_message(InstallStatus.UNSUPPORTED),
            "requires_preparation": True,
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

    def _validate_runtime_python_abi(self, capsule_lock: CapsuleLock) -> None:
        assert self.runtime_manager is not None, "runtime_manager required for runner launch"
        executable = _runtime_python_executable(self.runtime_manager)
        active_python_version = _runtime_python_version(self.runtime_manager)
        expected_python_version = capsule_lock.runtime.python_version
        if active_python_version == expected_python_version:
            return
        if active_python_version is None and not Path(executable).exists():
            return
        if active_python_version is None:
            raise ValueError(
                "Managed runner Python ABI could not be inspected; "
                f"runtime profile requires Python {expected_python_version}."
            )
        raise ValueError(
            "Managed runner Python ABI mismatch: runtime profile requires "
            f"Python {expected_python_version}, but the active managed runner "
            f"uses Python {active_python_version}."
        )


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
    dependency_env_fingerprint: str | None = None,
    runner_workspace_fingerprint: str | None = None,
    runtime_manager: RuntimeManager,
    runner_id_suffix: str | None = None,
) -> RunnerLaunchSpec:
    runner_id = WorkflowRunnerLifecycleService._runner_id_for_capsule(capsule_lock)
    if runner_id_suffix:
        runner_id = f"{runner_id}-{runner_id_suffix}"
    runner_workspace_fingerprint = (
        runner_workspace_fingerprint or capsule_lock.runtime.runner_fingerprint
    )
    dependency_env_fingerprint = (
        dependency_env_fingerprint or capsule_lock.runtime.dependency_env_fingerprint
    )
    telemetry_path = runner_workspace_path / ".noofy" / "memory" / f"{runner_id}.jsonl"
    extra_args = [
        "--base-directory",
        str(runner_workspace_path),
        "--disable-auto-launch",
    ]
    extra_args.extend(
        comfyui_preview_args(
            capsule_lock.runtime.preview_method,
            capsule_lock.runtime.preview_size,
        )
    )
    extra_args.extend(comfyui_vram_args(capsule_lock.runtime.vram_mode))
    extra_args.extend(comfyui_attention_args(capsule_lock.runtime.attention_backend))
    extra_args.extend(comfyui_precision_args(capsule_lock.runtime.precision_policy))
    if capsule_lock.runtime.gpu_backend.lower() == "cpu" and "--cpu" not in extra_args:
        extra_args.append("--cpu")
    if not capsule_lock.custom_nodes:
        extra_args.append("--disable-all-custom-nodes")
    return RunnerLaunchSpec(
        runner_id=runner_id,
        kind=RunnerKind.ISOLATED_COMFYUI,
        fingerprint=runner_workspace_fingerprint,
        python_executable=_runtime_python_executable(runtime_manager),
        working_dir=runner_workspace_path,
        dependency_env_path=dependency_env_path,
        runner_workspace_path=runner_workspace_path,
        runner_workspace_fingerprint=runner_workspace_fingerprint,
        dependency_env_fingerprint=dependency_env_fingerprint,
        runner_process_compatibility_key=(
            capsule_lock.runtime.runner_process_compatibility_key
            or runner_workspace_fingerprint
        ),
        runtime_profile_id=capsule_lock.runtime.runtime_profile_id,
        runtime_profile_variant_id=capsule_lock.runtime.runtime_profile_variant_id,
        memory_class=_memory_class_for_runtime_backend(capsule_lock.runtime.gpu_backend),
        host=runtime_manager.managed_host,
        extra_args=extra_args,
        memory_telemetry_path=telemetry_path,
        env={
            # Profile-authored environment first; Noofy identity keys win.
            **capsule_lock.runtime.noofy_environment,
            "NOOFY_CAPSULE_FINGERPRINT": capsule_lock.runtime.capsule_fingerprint,
            "NOOFY_DEPENDENCY_ENV_PATH": str(dependency_env_path),
            "NOOFY_RUNNER_WORKSPACE_PATH": str(runner_workspace_path),
            "NOOFY_WORKFLOW_ID": capsule_lock.workflow.package_id,
        },
    )


def _effective_launch_diagnostics(
    capsule_lock: CapsuleLock,
    spec: RunnerLaunchSpec,
) -> dict[str, object]:
    """Developer diagnostics for debugging speed, output, and dependency issues."""
    runtime = capsule_lock.runtime
    return {
        "runner_id": spec.runner_id,
        "runtime_profile_id": runtime.runtime_profile_id,
        "runtime_profile_variant_id": runtime.runtime_profile_variant_id,
        "runtime_profile_manifest_hash": runtime.runtime_profile_manifest_hash,
        "comfyui_version": capsule_lock.engine.comfyui_version,
        "comfyui_core_source_hash": capsule_lock.engine.core_source_hash,
        "python_version": runtime.python_version,
        "python_build_id": runtime.python_build_id,
        "gpu_backend": runtime.gpu_backend,
        # Torch version/build are pinned per profile variant; the dependency
        # env fingerprint covers the exact torch wheel build tag.
        "dependency_env_fingerprint": runtime.dependency_env_fingerprint,
        "launch_args": list(spec.extra_args),
        "attention_backend": runtime.attention_backend,
        "effective_attention": (
            "comfyui_auto"
            if runtime.attention_backend == "auto"
            else runtime.attention_backend
        ),
        "vram_mode": runtime.vram_mode,
        "precision_policy": runtime.precision_policy,
        "noofy_environment": dict(runtime.noofy_environment),
        "memory_class": spec.memory_class.value,
    }


def _runtime_python_executable(runtime_manager: RuntimeManager) -> str:
    environment = getattr(runtime_manager, "environment", None)
    if environment is not None:
        return environment.python_executable
    return runtime_manager.python_executable


def _runtime_python_version(runtime_manager: RuntimeManager) -> str | None:
    return detect_python_major_minor(_runtime_python_executable(runtime_manager))


def _memory_class_for_runtime_backend(gpu_backend: str) -> RunnerMemoryClass:
    return RunnerMemoryClass.CPU_ONLY if gpu_backend.lower() == "cpu" else RunnerMemoryClass.GPU_HEAVY


def _ready_install_runner_process_compatibility_key(
    capsule_lock: CapsuleLock,
    install_state: InstallState,
) -> str | None:
    return (
        install_state.runner_process_compatibility_key
        or install_state.runner_workspace_fingerprint
        or capsule_lock.runtime.runner_process_compatibility_key
        or capsule_lock.runtime.runner_fingerprint
    )


def _prepared_runtime_paths(
    install_state: InstallState,
    capsule_lock: CapsuleLock,
    *,
    expected_python_version: str | None = None,
    model_reference_validator: Callable[[list[InstalledModelReference]], list[str]],
) -> tuple[Path, Path, str, str]:
    if not install_state.dependency_env_path or not install_state.runner_workspace_path:
        raise PreparedRuntimeArtifactError(
            "Prepared runtime artifact paths are missing; prepare the workflow again.",
            reason_code="missing_runtime_artifact_paths",
        )

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
        raise PreparedRuntimeArtifactError(
            f"Prepared runtime artifact is missing: {', '.join(missing)}",
            reason_code="missing_runtime_artifact",
        )

    try:
        dependency_manifest = _read_dependency_manifest(dependency_env_path / "manifest.json")
        runner_manifest = _read_runner_workspace_manifest(runner_workspace_path / "manifest.json")
    except ValueError as exc:
        raise PreparedRuntimeArtifactError(
            str(exc),
            reason_code="invalid_runtime_artifact_manifest",
        ) from exc
    expected_dependency_fingerprint = (
        install_state.dependency_env_fingerprint
        or capsule_lock.runtime.dependency_env_fingerprint
    )
    expected_runner_fingerprint = (
        install_state.runner_workspace_fingerprint
        or capsule_lock.runtime.runner_fingerprint
    )
    not_ready: list[str] = []
    fatal_not_ready: list[str] = []
    if dependency_manifest.status is not InstallStatus.READY:
        not_ready.append(f"dependency environment manifest status {dependency_manifest.status.value}")
    if runner_manifest.status is not InstallStatus.READY:
        not_ready.append(f"runner workspace manifest status {runner_manifest.status.value}")
    if dependency_manifest.fingerprint != expected_dependency_fingerprint:
        not_ready.append("dependency environment manifest fingerprint mismatch")
    if (
        expected_python_version is not None
        and dependency_manifest.python_version != expected_python_version
    ):
        fatal_not_ready.append(
            "dependency environment Python ABI mismatch"
        )
    if runner_manifest.fingerprint != expected_runner_fingerprint:
        not_ready.append("runner workspace manifest fingerprint mismatch")
    if runner_manifest.dependency_env_fingerprint != dependency_manifest.fingerprint:
        not_ready.append("runner workspace dependency environment mismatch")
    invalid_model_references = model_reference_validator(install_state.model_references)
    not_ready.extend(invalid_model_references)
    if fatal_not_ready:
        details = fatal_not_ready + not_ready
        raise ValueError(f"Prepared runtime artifact is not ready: {', '.join(details)}")
    if not_ready:
        reason_code = (
            "stale_model_view"
            if invalid_model_references
            else "stale_runtime_artifact"
        )
        raise PreparedRuntimeArtifactError(
            f"Prepared runtime artifact is not ready: {', '.join(not_ready)}",
            reason_code=reason_code,
        )
    return (
        dependency_env_path,
        runner_workspace_path,
        dependency_manifest.fingerprint,
        runner_manifest.fingerprint,
    )


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


def _unresolved_model_requirement_message(
    package: WorkflowPackage,
    capsule_lock: CapsuleLock,
) -> str | None:
    locked_targets = {
        (model.comfyui_folder.casefold(), model.filename.casefold())
        for model in capsule_lock.models
    }
    unresolved: list[str] = []
    for model in unique_required_models(package.required_models):
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
    for model in unique_required_models(package.required_models):
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


def _workflow_requires_isolated_preparation(
    package: WorkflowPackage,
    capsule_lock: CapsuleLock,
) -> bool:
    return package.import_metadata is not None or bool(capsule_lock.custom_nodes)


def _engine_unrecognized_missing_node_types(state: InstallState) -> list[str]:
    workflow_execution = state.smoke_test_report.workflow_execution
    if workflow_execution.status is not SmokeStageStatus.FAILED:
        return []
    if workflow_execution.details.get("reason") != "engine_unrecognized_node_types":
        return []
    missing = workflow_execution.details.get("missing_node_types")
    if not isinstance(missing, list):
        return []
    return sorted({item for item in missing if isinstance(item, str) and item})


def _workflow_source_resolution(package: WorkflowPackage) -> dict[str, object]:
    details = (
        package.import_metadata.developer_details.get("source_resolution")
        if package.import_metadata is not None
        else None
    )
    return details if isinstance(details, dict) else {}


def _engine_auto_resolution_attempted(package: WorkflowPackage) -> bool:
    if package.import_metadata is None:
        return False
    attempt = package.import_metadata.developer_details.get(
        "engine_node_auto_resolution"
    )
    if not isinstance(attempt, dict) or attempt.get("status") != "attempted":
        return False
    source_resolution = _workflow_source_resolution(package)
    return source_resolution.get("status") == "resolved" or any(
        record.included for record in package.custom_nodes
    )


def _community_preparation_opted_in(package: WorkflowPackage) -> bool:
    if package.source_policy is None:
        return True
    return bool(package.source_policy.community_preparation_opted_in)


def _workflow_node_resolution_payload(
    package: WorkflowPackage,
) -> dict[str, object] | None:
    if package.import_metadata is None:
        return None
    source_resolution = _workflow_source_resolution(package)
    status = package.import_metadata.status
    resolution_status = source_resolution.get("status")
    if status not in {
        "engine_unrecognized_nodes",
        "missing_custom_nodes",
        "needs_comfyui_update",
    } and resolution_status not in {
        "engine_unrecognized_nodes",
        "failed",
        "missing_custom_nodes",
        "needs_comfyui_update",
    }:
        return None
    unresolved_node_types = [
        item
        for item in source_resolution.get("unresolved_node_types", [])
        if isinstance(item, str)
    ]
    ambiguous_node_types = [
        item
        for item in source_resolution.get("ambiguous_node_types", [])
        if isinstance(item, dict)
    ]
    unresolved_from_ambiguous = [
        item["node_type"]
        for item in ambiguous_node_types
        if isinstance(item.get("node_type"), str)
    ]
    fields = [
        {"node_type": node_type, "label": node_type}
        for node_type in sorted(set(unresolved_node_types + unresolved_from_ambiguous))
    ]
    candidate = source_resolution.get("candidate")
    public_candidate = None
    if isinstance(candidate, dict):
        public_candidate = {
            key: value for key, value in candidate.items() if key != "source"
        }
    developer_details = dict(source_resolution)
    if public_candidate is not None:
        developer_details["candidate"] = public_candidate
    public_status = (
        resolution_status
        if resolution_status == "engine_unrecognized_nodes"
        else status or resolution_status or "engine_unrecognized_nodes"
    )
    return {
        "status": public_status,
        "mode": source_resolution.get("mode") or "manual_url",
        "user_facing_message": package.import_metadata.user_facing_message,
        "missing_custom_node": source_resolution.get("missing_custom_node"),
        "package_id": source_resolution.get("package_id"),
        "unresolved_node_types": unresolved_node_types,
        "ambiguous_node_types": ambiguous_node_types,
        "automatic_resolution_failures": source_resolution.get(
            "automatic_resolution_failures", []
        ),
        "failed_custom_nodes": source_resolution.get("failed_custom_nodes", []),
        "candidate": public_candidate,
        "github_url_fields": fields,
        "can_provide_github_urls": bool(fields),
        "can_mark_no_custom_nodes": False,
        "update_guidance": source_resolution.get("update_guidance")
        or (
            "This can also happen if your managed ComfyUI engine is too old. "
            "You can update the engine in Settings, then retry."
        ),
        "developer_details": developer_details,
    }


def _required_actions_for_workflow(
    package: WorkflowPackage,
    install: dict[str, object],
) -> list[dict[str, object]]:
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
    if status in {
        InstallStatus.PENDING.value,
        InstallStatus.IMPORTED.value,
        InstallStatus.NEEDS_INPUT_SETUP.value,
        InstallStatus.CANNOT_PREPARE_AUTOMATICALLY.value,
        InstallStatus.BLOCKED_BY_POLICY.value,
        InstallStatus.UNSUPPORTED_RUNTIME_PROFILE.value,
        InstallStatus.FAILED.value,
    }:
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
                "user_facing_message": install.get("user_facing_message")
                or "This workflow needs attention.",
            }
        )
    return actions


def _install_status_can_prepare(status: object) -> bool:
    return status != InstallStatus.UNSUPPORTED.value


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
    details["last_error_code"] = state.last_error_code
    details["install_transaction_id"] = state.last_install_transaction_id
    details["diagnostic_log_names"] = state.diagnostic_log_names
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
