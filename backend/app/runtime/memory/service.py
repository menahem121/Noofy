"""Stateful memory admission, retry, and sampling orchestration.

Owns: memory retry roots, queued workflow run queue, learning-store update
coordination, and all Memory Governor decision logic that EngineService used to
inline.  RunOrchestrator and RunResultService receive bound methods from this
service as callbacks, keeping the memory boundary explicit.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.engine.memory_observation import (
    MemoryObservationCoordinator,
    memory_input_profile_fingerprint,
)
from app.engine.models import EngineJob, JobResult
from app.gallery import RunSubmissionSnapshot
from app.runtime.memory.memory_governor import (
    LocalMemoryLearningStore,
    MachineMemoryObserver,
    MachineMemorySnapshot,
    MemoryAdmissionRequest,
    MemoryBackend,
    MemoryDecisionAction,
    MemoryGovernorDecision,
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
    wait_for_memory_release,
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
from app.workflows.package import WorkflowPackage

RunWorkflow = Callable[..., Awaitable[Any]]


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
        job_run_snapshots: dict[str, RunSubmissionSnapshot],
    ) -> None:
        self.runner_supervisor = runner_supervisor
        self.runner_process_coordinator = runner_process_coordinator
        self.log_store = log_store
        self.memory_observer = memory_observer
        self.memory_learning_store = memory_learning_store
        self.job_workflows = job_workflows
        self.job_run_requests = job_run_requests
        self.job_run_snapshots = job_run_snapshots

        self._memory_retry_roots: dict[str, str] = {}
        self._memory_retry_attempted_roots: set[str] = set()
        self._memory_governor_metrics: dict[str, int] = {}
        self.queued_workflow_runs: dict[
            str,
            tuple[str, dict[str, Any], dict[str, Any], RunSubmissionSnapshot],
        ] = {}

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

    def decision_for_workflow_run(
        self,
        *,
        package: WorkflowPackage,
        workflow_id: str,
        runner: Any,
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

    def wait_for_memory_release_after_cleanup(
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

    async def evict_idle_runners_for_workflow_run(
        self,
        decision: MemoryGovernorDecision,
    ) -> EngineJob | None:
        if self.runner_process_coordinator is None:
            return None
        for evict_runner_id in decision.evict_runner_ids:
            stopped = await self.runner_process_coordinator.stop_runner(evict_runner_id)
            self.record_metric("idle_runner_evicted_for_workflow_run")
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
        release_check = self.wait_for_memory_release_after_cleanup(decision)
        if release_check is None or release_check.status is MemoryReleaseStatus.RELEASED:
            return None
        self.record_metric("workflow_run_memory_cleanup_failed")
        return EngineJob(
            job_id=f"blocked-memory-{decision.workflow_id}",
            workflow_id=decision.workflow_id or "unknown",
            engine="noofy",
            status="blocked_by_memory",
            message="Noofy freed memory, but the machine still does not have enough available memory.",
            memory_decision=decision.model_dump(mode="json"),
            memory_status={
                **self.memory_status_payload(decision),
                "state": "memory_cleanup_failed",
                "message": "Noofy freed memory, but the machine still does not have enough available memory.",
            },
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
            self.record_metric("idle_runner_evicted_for_retry")
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
        return self.wait_for_memory_release_after_cleanup(decision)

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
    # Queued workflow run handoff
    # ------------------------------------------------------------------

    async def handoff_queued_workflow_run(
        self, queue_id: str
    ) -> Any:
        queued = self.queued_workflow_runs.pop(queue_id, None)
        if queued is None:
            return None
        workflow_id, inputs, options, run_submission_snapshot = queued
        self.log_store.add(
            "info",
            "Handing off queued workflow run",
            "memory_governor",
            workflow_id=workflow_id,
            details={"queue_id": queue_id},
        )
        assert self.run_workflow is not None, "run_workflow callback must be set"
        result = await self.run_workflow(
            workflow_id,
            inputs,
            options,
            run_submission_snapshot=run_submission_snapshot,
        )
        if isinstance(result, EngineJob):
            result = result.model_copy(update={"queue_id": result.queue_id or queue_id})
        return result


# ------------------------------------------------------------------
# Module-level helpers used by admission decisions
# ------------------------------------------------------------------

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
