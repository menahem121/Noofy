"""Stateful memory admission, retry, and sampling orchestration.

Owns: memory admission, cleanup, release polling, retry roots, learning-store
update coordination, and all Memory Governor decision logic. RunOrchestrator
and RunResultService receive bound callbacks, keeping the memory boundary
explicit while the workflow-run queue remains in runs/.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.diagnostics import DiagnosticsSink
from app.engine.memory_observation import (
    MemoryObservationCoordinator,
    memory_input_profile_fingerprint,
)
from app.engine.models import EngineJob, JobResult
from app.gallery import RunSubmissionSnapshot
from app.runtime.memory.memory_governor import (
    LocalMemoryEvidenceSummary,
    LocalMemoryLearningStore,
    MachineMemoryObserver,
    MachineMemorySnapshot,
    MemoryAdmissionRequest,
    MemoryBackend,
    MemoryDecisionAction,
    MemoryGovernorDecision,
    MemoryReleaseCheckResult,
    MemoryReleaseStatus,
    ProcessTreeMemoryObserver,
    RunnerMemorySnapshot,
    RunnerMemoryTelemetryReader,
    WorkflowMemoryEstimateRequest,
    build_workflow_memory_estimate,
    decide_memory_admission,
    likely_memory_error,
    memory_user_status_for_decision,
    record_memory_governor_decision,
    retry_after_memory_cleanup_decision,
    wait_for_memory_release_async,
)
from app.runtime.memory.input_features import (
    ModelSelectionFeatures,
    extract_model_selection_features,
)
from app.runtime.dependencies.isolation import CapsuleLock, InstallState
from app.runtime.runners.runner_coordinator import RunnerProcessCoordinator
from app.runtime.runners.supervisor import (
    JobRunnerNotFoundError,
    RunnerKind,
    RunnerMemoryClass,
    RunnerStatus,
    RunnerSupervisor,
)
from app.workflows.model_grouping import total_required_model_size_bytes
from app.workflows.package import WorkflowPackage

RunWorkflow = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class _RunEstimateFeatures:
    resolution_width: int | None = None
    resolution_height: int | None = None
    batch_size: int | None = None
    frame_count: int | None = None
    workflow_type: str | None = None
    precision: str | None = None
    vram_mode: str | None = None
    model_selections: ModelSelectionFeatures = field(
        default_factory=ModelSelectionFeatures
    )
    sources: dict[str, str] = field(default_factory=dict)

    @property
    def effective_batch_size(self) -> int:
        batch_size = self.batch_size or 1
        if self.frame_count is None or self.frame_count <= 1:
            return batch_size
        return max(1, batch_size * self.frame_count)

    @property
    def empty(self) -> bool:
        return (
            not self.sources
            and self.workflow_type is None
            and self.model_selections.empty
        )

    def diagnostic_details(self) -> dict[str, Any]:
        return {
            "resolution_width": self.resolution_width,
            "resolution_height": self.resolution_height,
            "batch_size": self.batch_size,
            "frame_count": self.frame_count,
            "effective_batch_size": self.effective_batch_size,
            "workflow_type": self.workflow_type,
            "precision": self.precision,
            "vram_mode": self.vram_mode,
            **self.model_selections.diagnostic_details(),
            "sources": self.sources,
        }


@dataclass(frozen=True)
class _CustomNodeMemoryUncertainty:
    count: int = 0
    node_types: list[str] = field(default_factory=list)
    custom_node_ids: list[str] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return self.count > 0

    def diagnostic_details(self) -> dict[str, Any]:
        return {
            "custom_node_count": self.count,
            "custom_node_types": self.node_types,
            "custom_node_ids": self.custom_node_ids,
            "reason": "custom_node_memory_uncertain",
        }


@dataclass(frozen=True)
class _PendingMemoryRelease:
    reservation_tokens: list[str] = field(default_factory=list)
    baseline_snapshot: MachineMemorySnapshot | None = None
    require_observed_drop: bool = False


class MemoryGovernorService:
    """Stateful memory admission, retry, and sampling orchestration.

    RunOrchestrator and RunResultService receive bound methods as callbacks.
    The ``run_workflow`` attribute must be set after construction (once
    RunOrchestrator is wired up) because the dependency is circular at build
    time.
    """

    def __init__(
        self,
        *,
        runner_supervisor: RunnerSupervisor,
        runner_process_coordinator: RunnerProcessCoordinator | None,
        log_store: DiagnosticsSink,
        memory_observer: MachineMemoryObserver | None,
        process_tree_memory_observer: ProcessTreeMemoryObserver,
        runner_memory_telemetry_reader: RunnerMemoryTelemetryReader,
        memory_learning_store: LocalMemoryLearningStore | None,
        job_workflows: dict[str, str],
        job_run_requests: dict[str, tuple[str, dict[str, Any], dict[str, Any]]],
        job_memory_profile_fingerprints: dict[str, str],
        job_run_snapshots: dict[str, RunSubmissionSnapshot],
    ) -> None:
        self.runner_supervisor = runner_supervisor
        self.runner_process_coordinator = runner_process_coordinator
        self.log_store = log_store
        self.memory_observer = memory_observer
        self.memory_learning_store = memory_learning_store
        self.job_workflows = job_workflows
        self.job_run_requests = job_run_requests
        self.job_memory_profile_fingerprints = job_memory_profile_fingerprints
        self.job_run_snapshots = job_run_snapshots

        self._memory_retry_roots: dict[str, str] = {}
        self._memory_retry_attempted_roots: set[str] = set()
        self._memory_governor_metrics: dict[str, int] = {}
        self._pending_memory_releases: dict[str, _PendingMemoryRelease] = {}
        self.memory_observation = MemoryObservationCoordinator(
            runner_supervisor=runner_supervisor,
            log_store=log_store,
            memory_observer=memory_observer,
            process_tree_memory_observer=process_tree_memory_observer,
            runner_memory_telemetry_reader=runner_memory_telemetry_reader,
            memory_learning_store=memory_learning_store,
            record_metric=self.record_metric,
        )

        # Wired after RunOrchestrator is created to avoid circular dependency.
        self.run_workflow: RunWorkflow | None = None

    # ------------------------------------------------------------------
    # Public metrics
    # ------------------------------------------------------------------

    def memory_governor_metrics(self) -> dict[str, int]:
        return dict(self._memory_governor_metrics)

    def record_metric(self, name: str) -> None:
        self._memory_governor_metrics[name] = self._memory_governor_metrics.get(name, 0) + 1

    def memory_status_payload(
        self,
        decision: MemoryGovernorDecision,
        *,
        queue_id: str | None = None,
    ) -> dict[str, Any]:
        return memory_user_status_for_decision(decision, queue_id=queue_id).model_dump(mode="json")

    def _workflow_run_local_evidence(
        self,
        *,
        workflow_id: str,
        runner_process_compatibility_key: str | None,
        machine_snapshot: MachineMemorySnapshot,
        input_profile_fingerprint: str | None,
    ) -> LocalMemoryEvidenceSummary | None:
        if self.memory_learning_store is None:
            return None
        exact = self.memory_learning_store.summary_for(
            workflow_id=workflow_id,
            runner_process_compatibility_key=runner_process_compatibility_key,
            machine_profile_id=machine_snapshot.machine_profile_id,
            backend=machine_snapshot.backend,
            input_profile_fingerprint=input_profile_fingerprint,
        )
        if exact is not None:
            return exact
        return _best_compatible_success_evidence(
            self.memory_learning_store.list_summaries(),
            workflow_id=workflow_id,
            runner_process_compatibility_key=runner_process_compatibility_key,
            machine_profile_id=machine_snapshot.machine_profile_id,
            backend=machine_snapshot.backend,
            input_profile_fingerprint=input_profile_fingerprint,
        )

    # ------------------------------------------------------------------
    # Admission decisions
    # ------------------------------------------------------------------

    def decision_for_runner_start(
        self,
        *,
        workflow_id: str,
        capsule_lock: CapsuleLock,
        install_state: InstallState,
        spec: Any,
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
        custom_node_memory = _custom_node_memory_uncertainty(capsule_lock.custom_nodes)
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
                precision=_runtime_option_string(
                    capsule_lock.hardware_observations.precision,
                    feature="precision",
                ),
                custom_node_count=custom_node_memory.count,
                custom_node_types=custom_node_memory.node_types,
            )
        )
        runner_snapshots = [
            RunnerMemorySnapshot.from_descriptor(runner)
            for runner in self.runner_supervisor.list_runners()
            if _runner_may_hold_reclaimable_memory(runner)
        ]
        decision = decide_memory_admission(
            MemoryAdmissionRequest(
                workflow_estimate=estimate,
                machine_snapshot=machine_snapshot,
                resident_runners=runner_snapshots,
            )
        )
        if custom_node_memory.present:
            decision = decision.model_copy(
                update={
                    "developer_details": {
                        **decision.developer_details,
                        "custom_node_memory_uncertainty": custom_node_memory.diagnostic_details(),
                    }
                }
            )
        record_memory_governor_decision(self.log_store, decision)
        return decision

    def decision_for_workflow_run(
        self,
        *,
        package: WorkflowPackage,
        workflow_id: str,
        runner: Any,
        inputs: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        input_profile_fingerprint: str | None = None,
        memory_retry_after_cleanup: bool = False,
    ) -> MemoryGovernorDecision | None:
        if self.memory_observer is None:
            return None
        machine_snapshot = self.memory_observer.snapshot()
        local_evidence = self._workflow_run_local_evidence(
            workflow_id=workflow_id,
            runner_process_compatibility_key=runner.runner_process_compatibility_key,
            machine_snapshot=machine_snapshot,
            input_profile_fingerprint=input_profile_fingerprint,
        )
        estimate_features = _run_estimate_features(package, inputs or {}, options or {})
        custom_node_memory = _custom_node_memory_uncertainty(package.custom_nodes)
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
                resolution_width=estimate_features.resolution_width,
                resolution_height=estimate_features.resolution_height,
                batch_size=estimate_features.effective_batch_size,
                workflow_type=estimate_features.workflow_type,
                precision=estimate_features.precision,
                vram_mode=estimate_features.vram_mode,
                selected_model_count=estimate_features.model_selections.selected_model_count,
                selected_model_kinds=estimate_features.model_selections.selected_model_kinds,
                lora_count=estimate_features.model_selections.lora_count,
                lora_strength_total=estimate_features.model_selections.lora_strength_total,
                custom_node_count=custom_node_memory.count,
                custom_node_types=custom_node_memory.node_types,
            )
        )
        resident_runners = []
        for resident in self.runner_supervisor.list_runners():
            if (
                resident.runner_id == runner.runner_id
                and resident.kind is RunnerKind.ISOLATED_COMFYUI
                and resident.current_job_id is None
            ):
                continue
            if not _runner_may_hold_reclaimable_memory(resident):
                continue
            resident_runners.append(RunnerMemorySnapshot.from_descriptor(resident))
        decision = decide_memory_admission(
            MemoryAdmissionRequest(
                workflow_estimate=estimate,
                machine_snapshot=machine_snapshot,
                selected_runner=RunnerMemorySnapshot.from_descriptor(runner),
                resident_runners=resident_runners,
            )
        )
        developer_details = decision.developer_details
        if not estimate_features.empty:
            developer_details = {
                **developer_details,
                "runtime_estimate_features": estimate_features.diagnostic_details(),
            }
        if custom_node_memory.present:
            developer_details = {
                **developer_details,
                "custom_node_memory_uncertainty": custom_node_memory.diagnostic_details(),
            }
        if developer_details != decision.developer_details:
            decision = decision.model_copy(
                update={"developer_details": developer_details}
            )
        record_memory_governor_decision(self.log_store, decision)
        self.record_metric(f"workflow_run_decision_{decision.action.value}")
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

    # ------------------------------------------------------------------
    # Eviction and memory-release helpers
    # ------------------------------------------------------------------

    async def wait_for_memory_release_after_cleanup(
        self,
        decision: MemoryGovernorDecision,
    ):
        pending_release = self._pending_memory_releases.pop(
            decision.decision_id,
            _PendingMemoryRelease(),
        )
        reservation_tokens = pending_release.reservation_tokens
        if self.memory_observer is None:
            release_check = MemoryReleaseCheckResult(
                status=MemoryReleaseStatus.UNAVAILABLE,
                reason_code="memory_observer_unavailable",
                timeline=[{"state": "observer_unavailable"}],
            )
            self._finalize_release_reservations(reservation_tokens, released=False)
            return release_check
        if decision.workflow_estimate is None:
            self._finalize_release_reservations(reservation_tokens, released=False)
            return MemoryReleaseCheckResult(
                status=MemoryReleaseStatus.UNAVAILABLE,
                reason_code="memory_estimate_unavailable",
                timeline=[{"state": "observer_unavailable", "error": "memory_estimate_unavailable"}],
            )
        required_free_vram_mb = _required_free_after_cleanup(
            _estimated_vram_after_cleanup(decision),
            decision.required_vram_margin_mb,
        )
        required_free_ram_mb = _required_free_after_cleanup(
            _estimated_ram_after_cleanup(decision),
            decision.required_ram_margin_mb,
        )
        release_check = await wait_for_memory_release_async(
            self.memory_observer,
            required_free_vram_mb=required_free_vram_mb,
            required_free_ram_mb=required_free_ram_mb,
            baseline_snapshot=pending_release.baseline_snapshot,
            require_observed_drop=pending_release.require_observed_drop,
            timeout_seconds=settings.memory_release_timeout_seconds,
            initial_poll_interval_seconds=settings.memory_release_initial_poll_interval_seconds,
            max_poll_interval_seconds=settings.memory_release_max_poll_interval_seconds,
        )
        self._finalize_release_reservations(
            reservation_tokens,
            released=release_check.status is MemoryReleaseStatus.RELEASED,
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

    def _finalize_release_reservations(self, reservation_tokens: list[str], *, released: bool) -> None:
        for token in reservation_tokens:
            if released:
                self.runner_supervisor.confirm_runner_memory_released(token)
            else:
                self.runner_supervisor.fail_runner_memory_release(token)

    async def evict_idle_runners_for_workflow_run(
        self,
        decision: MemoryGovernorDecision,
    ) -> EngineJob | None:
        cleaned_up = await self.cleanup_idle_runners_for_memory_decision(
            decision,
            metric_name="idle_runner_evicted_for_workflow_run",
            log_source="memory_governor",
            log_message="Released idle runner memory before workflow run",
        )
        if not cleaned_up:
            self.record_metric("workflow_run_memory_cleanup_failed")
            return _memory_cleanup_failed_job(
                decision,
                reason_code="memory_cleanup_unavailable",
            )
        release_check = await self.wait_for_memory_release_after_cleanup(decision)
        if release_check.status is MemoryReleaseStatus.RELEASED:
            return None
        self.record_metric("workflow_run_memory_cleanup_failed")
        return _memory_cleanup_failed_job(
            decision,
            reason_code=release_check.reason_code,
            release_check=release_check,
        )

    async def cleanup_idle_runners_for_memory_decision(
        self,
        decision: MemoryGovernorDecision,
        *,
        metric_name: str,
        log_source: str,
        log_message: str,
        runner_ids: list[str] | None = None,
    ) -> bool:
        """Release idle Noofy-owned memory without terminating active work."""
        reservation_tokens: list[str] = []
        baseline_snapshot = (
            self.memory_observer.snapshot()
            if self.memory_observer is not None
            else None
        )
        require_observed_drop = False
        cleanup_runner_ids = list(
            runner_ids if runner_ids is not None else decision.evict_runner_ids
        )
        # Stop isolated processes first. If core cleanup is also requested,
        # refresh its baseline after those releases so `/free` must prove its
        # own additional drop rather than borrowing an isolated-runner delta.
        cleanup_runner_ids.sort(
            key=lambda runner_id: (
                self.runner_supervisor.get_runner(runner_id).kind
                is RunnerKind.CORE_COMFYUI
            )
        )
        for runner_id in cleanup_runner_ids:
            runner = self.runner_supervisor.get_runner(runner_id)
            if _runner_is_active(runner):
                self.log_store.add(
                    "warning",
                    "Memory cleanup skipped because a runner became active",
                    log_source,
                    workflow_id=decision.workflow_id,
                    details={
                        "runner_id": runner_id,
                        "memory_decision_id": decision.decision_id,
                    },
                )
                self._finalize_release_reservations(reservation_tokens, released=False)
                return False
            reservation = self.runner_supervisor.reserve_runner_for_eviction(runner_id)
            if reservation is None:
                self.log_store.add(
                    "warning",
                    "Memory cleanup skipped because runner reservation was lost",
                    log_source,
                    workflow_id=decision.workflow_id,
                    details={"runner_id": runner_id, "memory_decision_id": decision.decision_id},
                )
                self._finalize_release_reservations(reservation_tokens, released=False)
                return False
            if runner.kind is RunnerKind.CORE_COMFYUI:
                require_observed_drop = True
                baseline_snapshot = (
                    self.memory_observer.snapshot()
                    if self.memory_observer is not None
                    else None
                )
                adapter = self.runner_supervisor.get_adapter(runner_id)
                release_memory = getattr(adapter, "release_memory", None)
                if not callable(release_memory):
                    self.log_store.add(
                        "warning",
                        "Core runner cannot release idle memory through its adapter",
                        log_source,
                        workflow_id=decision.workflow_id,
                        details={
                            "runner_id": runner_id,
                            "memory_decision_id": decision.decision_id,
                        },
                    )
                    self.runner_supervisor.rollback_runner_reservation(reservation.token)
                    self._finalize_release_reservations(reservation_tokens, released=False)
                    return False
                try:
                    await release_memory()
                except Exception as exc:
                    self.log_store.add(
                        "warning",
                        "Core runner memory release failed",
                        log_source,
                        workflow_id=decision.workflow_id,
                        details={
                            "runner_id": runner_id,
                            "memory_decision_id": decision.decision_id,
                            "error": str(exc),
                        },
                    )
                    self.runner_supervisor.rollback_runner_reservation(reservation.token)
                    self._finalize_release_reservations(reservation_tokens, released=False)
                    return False
                self.runner_supervisor.mark_runner_waiting_for_memory_release(reservation.token)
                cleanup_status = "models_and_cache_release_requested"
            else:
                if self.runner_process_coordinator is None:
                    self.log_store.add(
                        "warning",
                        "Isolated runner memory cleanup requires a runner coordinator",
                        log_source,
                        workflow_id=decision.workflow_id,
                        details={
                            "runner_id": runner_id,
                            "memory_decision_id": decision.decision_id,
                        },
                    )
                    self.runner_supervisor.rollback_runner_reservation(reservation.token)
                    self._finalize_release_reservations(reservation_tokens, released=False)
                    return False
                try:
                    stopped = await self.runner_process_coordinator.stop_runner(runner_id)
                except Exception as exc:
                    self.log_store.add(
                        "warning",
                        "Isolated runner memory release failed",
                        log_source,
                        workflow_id=decision.workflow_id,
                        details={
                            "runner_id": runner_id,
                            "memory_decision_id": decision.decision_id,
                            "error": str(exc),
                        },
                    )
                    self.runner_supervisor.rollback_runner_reservation(reservation.token)
                    self._finalize_release_reservations(reservation_tokens, released=False)
                    return False
                if stopped.status is not RunnerStatus.STOPPED:
                    self.runner_supervisor.fail_runner_memory_release(reservation.token)
                    self._finalize_release_reservations(reservation_tokens, released=False)
                    return False
                self.runner_supervisor.mark_runner_waiting_for_memory_release(reservation.token)
                cleanup_status = stopped.status.value
            reservation_tokens.append(reservation.token)
            self.record_metric(metric_name)
            self.log_store.add(
                "info",
                log_message,
                log_source,
                workflow_id=decision.workflow_id,
                details={
                    "runner_id": runner_id,
                    "runner_kind": runner.kind.value,
                    "cleanup_status": cleanup_status,
                    "memory_decision_id": decision.decision_id,
                    "reason": decision.reason_code,
                },
            )
        self._pending_memory_releases[decision.decision_id] = _PendingMemoryRelease(
            reservation_tokens=reservation_tokens,
            baseline_snapshot=baseline_snapshot,
            require_observed_drop=require_observed_drop,
        )
        return True

    async def _stop_idle_runners_for_memory_retry(
        self,
        *,
        current_job_id: str,
        decision: MemoryGovernorDecision,
    ):
        cleanup_runner_ids: list[str] = []
        for runner in self.runner_supervisor.list_runners():
            if runner.current_job_id in {current_job_id}:
                continue
            if _runner_is_active(runner):
                continue
            if not _runner_may_hold_reclaimable_memory(runner):
                continue
            cleanup_runner_ids.append(runner.runner_id)
        if not cleanup_runner_ids:
            return None
        cleaned_up = await self.cleanup_idle_runners_for_memory_decision(
            decision,
            metric_name="idle_runner_evicted_for_retry",
            log_source="memory_governor",
            log_message="Released idle runner memory before retry after cleanup",
            runner_ids=cleanup_runner_ids,
        )
        if not cleaned_up:
            return MemoryReleaseCheckResult(
                status=MemoryReleaseStatus.UNAVAILABLE,
                reason_code="memory_cleanup_unavailable",
            )
        return await self.wait_for_memory_release_after_cleanup(decision)

    # ------------------------------------------------------------------
    # Job memory sampling
    # ------------------------------------------------------------------

    def start_job_sampling(
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

    async def finish_job_sampling(self, job_id: str) -> None:
        await self.memory_observation.finish_job_sampling(
            job_id,
            workflow_id=self.job_workflows.get(job_id),
        )

    def record_local_memory_observation(self, result: JobResult) -> None:
        self.memory_observation.record_result_observation(
            result,
            workflow_id=self.job_workflows.get(result.job_id),
            run_request=self.job_run_requests.get(result.job_id),
            input_profile_fingerprint=self.job_memory_profile_fingerprints.get(result.job_id),
        )

    # ------------------------------------------------------------------
    # Retry after memory cleanup
    # ------------------------------------------------------------------

    async def maybe_retry_after_memory_cleanup(self, result: JobResult) -> EngineJob | None:
        if result.status != "failed" or not likely_memory_error(result.error):
            return None
        workflow_id = self.job_workflows.get(result.job_id)
        run_request = self.job_run_requests.get(result.job_id)
        if workflow_id is None or run_request is None or self.memory_observer is None:
            return None

        root_job_id = self._memory_retry_roots.get(result.job_id, result.job_id)
        retry_already_attempted = root_job_id in self._memory_retry_attempted_roots
        machine_snapshot = self.memory_observer.snapshot()
        try:
            runner = self.runner_supervisor.runner_for_job(result.job_id)
        except JobRunnerNotFoundError:
            runner = None
        input_profile_fingerprint = self.job_memory_profile_fingerprints.get(
            result.job_id
        ) or memory_input_profile_fingerprint(run_request[1], run_request[2])
        local_evidence = self._workflow_run_local_evidence(
            workflow_id=workflow_id,
            runner_process_compatibility_key=runner.runner_process_compatibility_key if runner is not None else None,
            machine_snapshot=machine_snapshot,
            input_profile_fingerprint=input_profile_fingerprint,
        )
        estimate = build_workflow_memory_estimate(
            WorkflowMemoryEstimateRequest(
                workflow_id=workflow_id,
                runner_process_compatibility_key=runner.runner_process_compatibility_key if runner is not None else None,
                declared_memory_class=runner.memory_class if runner is not None else RunnerMemoryClass.UNKNOWN,
                input_profile_fingerprint=input_profile_fingerprint,
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
            self.record_metric("memory_retry_blocked")
            return None

        release_check = await self._stop_idle_runners_for_memory_retry(
            current_job_id=result.job_id,
            decision=decision,
        )
        if release_check is not None and release_check.status is not MemoryReleaseStatus.RELEASED:
            self.record_metric("memory_retry_cleanup_failed")
            return None

        self._memory_retry_attempted_roots.add(root_job_id)
        self.record_metric("memory_retry_attempted")
        retry_workflow_id, inputs, options = run_request
        assert self.run_workflow is not None, "run_workflow callback must be set before retry"
        retry_result = await self.run_workflow(
            retry_workflow_id,
            dict(inputs),
            dict(options),
            memory_retry_after_cleanup=True,
            run_submission_snapshot=self.job_run_snapshots.get(result.job_id),
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
                "memory_status": self.memory_status_payload(decision),
            }
        )

# ------------------------------------------------------------------
# Module-level helpers used by admission decisions
# ------------------------------------------------------------------

def _memory_cleanup_failed_job(
    decision: MemoryGovernorDecision,
    *,
    reason_code: str,
    release_check: MemoryReleaseCheckResult | None = None,
) -> EngineJob:
    memory_decision = decision.model_dump(mode="json")
    memory_decision["developer_details"] = {
        **memory_decision["developer_details"],
        "memory_cleanup_failure": {
            "reason_code": reason_code,
            "release_check": release_check.model_dump(mode="json")
            if release_check is not None
            else None,
        },
    }
    return EngineJob(
        job_id=f"blocked-memory-{decision.workflow_id}",
        workflow_id=decision.workflow_id or "unknown",
        engine="noofy",
        status="blocked_by_memory",
        message="Noofy could not confirm that enough memory was released for this workflow.",
        memory_decision=memory_decision,
        memory_status={
            **memory_user_status_for_decision(decision).model_dump(mode="json"),
            "state": "memory_cleanup_failed",
            "message": "Noofy could not confirm that enough memory was released for this workflow.",
        },
    )


def _runner_is_active(runner: Any) -> bool:
    return (
        runner.current_job_id is not None
        or runner.output_stream_lease_count > 0
        or runner.status
        in {
            RunnerStatus.RUNNING,
            RunnerStatus.LOADING_MODEL,
            RunnerStatus.RETRYING_AFTER_MEMORY_CLEANUP,
        }
    )


def _runner_may_hold_reclaimable_memory(runner: Any) -> bool:
    if _runner_is_active(runner):
        return True
    if runner.kind is RunnerKind.CORE_COMFYUI:
        return runner.last_workflow_id is not None or runner.observed_idle_vram_mb is not None
    return runner.status in {
        RunnerStatus.READY,
        RunnerStatus.IDLE,
        RunnerStatus.IDLE_WARM,
        RunnerStatus.CO_RESIDENT,
    }


def _installed_model_size_mb(install_state: InstallState) -> int | None:
    total_size_bytes = sum(ref.size_bytes or 0 for ref in install_state.model_references)
    if total_size_bytes <= 0:
        return None
    return max(1, total_size_bytes // (1024 * 1024))


def _required_model_size_mb_from_package(package: WorkflowPackage) -> int | None:
    total_size_bytes = total_required_model_size_bytes(package.required_models)
    if total_size_bytes <= 0:
        return None
    return max(1, total_size_bytes // (1024 * 1024))


def _custom_node_memory_uncertainty(custom_nodes: Any) -> _CustomNodeMemoryUncertainty:
    if not isinstance(custom_nodes, list):
        custom_nodes = list(custom_nodes or [])
    node_types: set[str] = set()
    custom_node_ids: set[str] = set()
    for custom_node in custom_nodes:
        for attr_name in ("id", "package_id", "folder_name"):
            raw_id = getattr(custom_node, attr_name, None)
            if isinstance(raw_id, str) and raw_id.strip():
                custom_node_ids.add(raw_id.strip())
                break
        raw_node_types = getattr(custom_node, "node_types", [])
        if not isinstance(raw_node_types, list):
            continue
        for node_type in raw_node_types:
            if isinstance(node_type, str) and node_type.strip():
                node_types.add(node_type.strip())
    return _CustomNodeMemoryUncertainty(
        count=len(custom_nodes),
        node_types=sorted(node_types)[:32],
        custom_node_ids=sorted(custom_node_ids)[:32],
    )


def _run_estimate_features(
    package: WorkflowPackage,
    inputs: dict[str, Any],
    options: dict[str, Any],
) -> _RunEstimateFeatures:
    values = list(_iter_runtime_feature_values(package, inputs, options))
    width = _first_int_feature(values, feature="width")
    height = _first_int_feature(values, feature="height")
    batch_size = _first_int_feature(values, feature="batch")
    frame_count = _first_int_feature(values, feature="frames")
    precision = _first_string_feature(values, feature="precision")
    vram_mode = _first_string_feature(values, feature="vram_mode")
    model_selections = extract_model_selection_features(package, inputs)
    workflow_type = _infer_workflow_type(package)
    sources: dict[str, str] = {}
    for name, feature in [
        ("resolution_width", width),
        ("resolution_height", height),
        ("batch_size", batch_size),
        ("frame_count", frame_count),
        ("precision", precision),
        ("vram_mode", vram_mode),
    ]:
        if feature is not None:
            sources[name] = feature[1]
    if workflow_type is not None:
        sources["workflow_type"] = "package_metadata_or_graph"
    return _RunEstimateFeatures(
        resolution_width=width[0] if width is not None else None,
        resolution_height=height[0] if height is not None else None,
        batch_size=batch_size[0] if batch_size is not None else None,
        frame_count=frame_count[0] if frame_count is not None else None,
        workflow_type=workflow_type,
        precision=precision[0] if precision is not None else None,
        vram_mode=vram_mode[0] if vram_mode is not None else None,
        model_selections=model_selections,
        sources=sources,
    )


def _iter_runtime_feature_values(
    package: WorkflowPackage,
    inputs: dict[str, Any],
    options: dict[str, Any],
):
    for workflow_input in package.inputs:
        value, source = _workflow_input_value(package, workflow_input, inputs)
        if value is None:
            continue
        node = package.comfyui_graph.get(workflow_input.binding.node_id)
        class_type = node.get("class_type") if isinstance(node, dict) else None
        yield (
            {
                workflow_input.id,
                workflow_input.binding.input_name,
                workflow_input.control,
                str(class_type or ""),
            },
            value,
            source,
        )
    for option_name, value in options.items():
        yield ({str(option_name)}, value, f"option:{option_name}")
    for node_id, node in package.comfyui_graph.items():
        if not isinstance(node, dict):
            continue
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict):
            continue
        class_type = str(node.get("class_type") or "")
        for input_name, value in node_inputs.items():
            yield ({str(input_name), class_type}, value, f"graph:{node_id}.{input_name}")


def _workflow_input_value(
    package: WorkflowPackage,
    workflow_input: Any,
    inputs: dict[str, Any],
) -> tuple[Any, str]:
    if workflow_input.id in inputs:
        return inputs[workflow_input.id], f"input:{workflow_input.id}"
    if workflow_input.default is not None:
        return workflow_input.default, f"default:{workflow_input.id}"
    node = package.comfyui_graph.get(workflow_input.binding.node_id)
    node_inputs = node.get("inputs") if isinstance(node, dict) else None
    if isinstance(node_inputs, dict) and workflow_input.binding.input_name in node_inputs:
        return (
            node_inputs[workflow_input.binding.input_name],
            f"graph:{workflow_input.binding.node_id}.{workflow_input.binding.input_name}",
        )
    return None, f"input:{workflow_input.id}"


def _first_int_feature(values, *, feature: str) -> tuple[int, str] | None:
    for raw_names, value, source in values:
        names = {_normalize_feature_name(name) for name in raw_names if name}
        if not _names_match_feature(names, feature):
            continue
        parsed = _positive_int(value)
        if parsed is not None:
            return parsed, source
    return None


def _first_string_feature(values, *, feature: str) -> tuple[str, str] | None:
    for raw_names, value, source in values:
        names = {_normalize_feature_name(name) for name in raw_names if name}
        if not _names_match_feature(names, feature):
            continue
        parsed = _runtime_option_string(value, feature=feature)
        if parsed is not None:
            return parsed, source
    return None


def _names_match_feature(names: set[str], feature: str) -> bool:
    if feature == "width":
        return any(name == "width" or name.endswith("_width") for name in names)
    if feature == "height":
        return any(name == "height" or name.endswith("_height") for name in names)
    if feature == "batch":
        return any(
            name in {"batch", "batch_size", "num_images", "image_count", "latent_count"}
            or name.endswith("_batch_size")
            for name in names
        )
    if feature == "frames":
        return any(
            name in {"frames", "frame_count", "num_frames", "video_frames"}
            or name.endswith("_frame_count")
            or name.endswith("_frames")
            for name in names
        )
    if feature == "precision":
        return any(
            name
            in {
                "precision",
                "precision_policy",
                "dtype",
                "torch_dtype",
                "weight_dtype",
                "model_precision",
                "runtime_precision",
            }
            or name.endswith("_precision")
            or name.endswith("_dtype")
            for name in names
        )
    if feature == "vram_mode":
        return any(
            name
            in {
                "vram_mode",
                "memory_mode",
                "gpu_memory_mode",
                "comfyui_vram_mode",
                "launch_vram_mode",
                "runtime_vram_mode",
            }
            or name.endswith("_vram_mode")
            or name.endswith("_memory_mode")
            for name in names
        )
    return False


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _runtime_option_string(value: Any, *, feature: str) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _normalize_feature_name(value)
    if not normalized:
        return None
    if feature == "precision":
        return _normalize_runtime_precision(normalized)
    if feature == "vram_mode":
        return _normalize_runtime_vram_mode(normalized)
    return normalized


def _normalize_runtime_precision(value: str) -> str | None:
    if value in {"auto", "default"}:
        return "auto"
    if value in {"fp32", "float32", "full", "full_precision", "no_half"}:
        return "fp32"
    if value in {"fp16", "float16", "half", "half_precision"}:
        return "fp16"
    if value in {"bf16", "bfloat16"}:
        return "bf16"
    if value in {"fp8", "float8"}:
        return "fp8"
    if value in {"int8", "8bit", "q8"}:
        return "int8"
    if value in {"int4", "4bit", "q4"}:
        return "int4"
    if value in {"quantized", "quantization"}:
        return "quantized"
    return value


def _normalize_runtime_vram_mode(value: str) -> str | None:
    if value in {"auto", "default"}:
        return "auto"
    if value in {"normal", "normalvram", "normal_vram"}:
        return "normal"
    if value in {"high", "highvram", "high_vram"}:
        return "highvram"
    if value in {"low", "lowvram", "low_vram"}:
        return "lowvram"
    if value in {"none", "no", "novram", "no_vram"}:
        return "novram"
    if value in {"cpu", "cpu_only"}:
        return "cpu"
    return value


def _infer_workflow_type(package: WorkflowPackage) -> str | None:
    tokens = [
        package.metadata.id,
        package.metadata.name,
        package.metadata.display_name or "",
        package.metadata.category,
        *package.metadata.tags,
    ]
    graph_types = [
        str(node.get("class_type") or "")
        for node in package.comfyui_graph.values()
        if isinstance(node, dict)
    ]
    normalized = " ".join(_normalize_feature_name(value) for value in [*tokens, *graph_types])
    if "controlnet" in normalized:
        return "controlnet"
    if "upscale" in normalized or "upscaler" in normalized:
        return "upscale"
    if any(token in normalized for token in ["video", "animate", "svd", "ltxv"]):
        return "video"
    if "img2img" in normalized or "loadimage" in normalized or "vaeencode" in normalized:
        return "img2img"
    if "emptylatentimage" in normalized and "ksampler" in normalized:
        return "txt2img"
    return None


def _normalize_feature_name(value: Any) -> str:
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in normalized if ch.isalnum() or ch == "_")


def _observed_hardware_int(package: WorkflowPackage, key: str) -> int | None:
    value = package.observed_hardware.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _best_compatible_success_evidence(
    summaries: list[LocalMemoryEvidenceSummary],
    *,
    workflow_id: str,
    runner_process_compatibility_key: str | None,
    machine_profile_id: str | None,
    backend: MemoryBackend,
    input_profile_fingerprint: str | None,
) -> LocalMemoryEvidenceSummary | None:
    candidates = [
        summary
        for summary in summaries
        if summary.workflow_id == workflow_id
        and summary.runner_process_compatibility_key
        == runner_process_compatibility_key
        and summary.machine_profile_id == machine_profile_id
        and summary.backend is backend
        and summary.input_profile_fingerprint != input_profile_fingerprint
        and summary.successful_runs > 0
        and summary.memory_error_runs == 0
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda summary: (
            summary.has_repeated_success,
            summary.successful_runs,
            summary.observed_peak_vram_mb or -1,
            summary.observed_peak_ram_mb or -1,
            summary.last_success_at or "",
        ),
        reverse=True,
    )[0]


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
