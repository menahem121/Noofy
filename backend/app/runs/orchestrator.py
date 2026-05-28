from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.engine.adapter import EngineAdapter
from app.engine.memory_observation import memory_input_profile_fingerprint
from app.engine.models import EngineJob, WorkflowValidationResult
from app.gallery import OutputPreference, RunSubmissionSnapshot, build_run_submission_snapshot
from app.history import HistoryService, workflow_display_name
from app.runtime.memory.memory_governor import (
    MachineMemoryObserver,
    MachineMemorySnapshot,
    MemoryDecisionAction,
    MemoryGovernorDecision,
)
from app.runtime.runners.supervisor import RunnerDescriptor, RunnerSupervisor
from app.runs.credentials import (
    CredentialResolver,
    CredentialRequirementError,
    build_credential_injection_plan,
    options_with_credential_plan,
    package_requires_credential_injection,
    safe_options_for_storage,
    strip_credential_inputs,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.bindings import package_for_input_bindings

ValidatePackage = Callable[[WorkflowPackage, EngineAdapter], Awaitable[WorkflowValidationResult]]
UnavailablePackageReason = Callable[[WorkflowPackage], str | None]
ApplyInputBindings = Callable[[WorkflowPackage, dict[str, Any]], dict[str, Any]]
EnsureWorkflowRunner = Callable[[WorkflowPackage], Awaitable[str | None]]
WorkflowRunMemoryDecision = Callable[..., MemoryGovernorDecision | None]
EvictIdleRunners = Callable[[MemoryGovernorDecision], Awaitable[EngineJob | None]]
MemoryStatusPayload = Callable[..., dict[str, Any]]
RecordMemoryMetric = Callable[[str], None]
StartMemorySampling = Callable[..., None]
ApiNodesUnavailableReason = Callable[[WorkflowPackage, EngineAdapter], Awaitable[str | None]]


class RunOrchestrator:
    """Validate and submit user workflow runs.

    Memory admission and retry state are still supplied by EngineService
    callbacks. This keeps the current memory-governor behavior intact while
    moving the user-facing run path into the runs domain.
    """

    def __init__(
        self,
        *,
        workflow_loader: WorkflowPackageLoader,
        runner_supervisor: RunnerSupervisor,
        log_store: DiagnosticsSink,
        memory_observer: MachineMemoryObserver | None,
        job_workflows: dict[str, str],
        job_started_at: dict[str, datetime],
        job_run_requests: dict[str, tuple[str, dict[str, Any], dict[str, Any]]],
        job_run_snapshots: dict[str, RunSubmissionSnapshot],
        memory_retry_roots: dict[str, str],
        queued_workflow_runs: dict[str, tuple[str, dict[str, Any], dict[str, Any], RunSubmissionSnapshot]],
        validate_package: ValidatePackage,
        unavailable_package_reason: UnavailablePackageReason,
        apply_input_bindings: ApplyInputBindings,
        ensure_workflow_runner: EnsureWorkflowRunner | None,
        workflow_run_memory_decision: WorkflowRunMemoryDecision,
        evict_idle_runners: EvictIdleRunners,
        memory_status_payload: MemoryStatusPayload,
        record_memory_metric: RecordMemoryMetric,
        start_memory_sampling: StartMemorySampling,
        history_service: HistoryService | None = None,
        credential_resolver: CredentialResolver | None = None,
        api_nodes_unavailable_reason: ApiNodesUnavailableReason | None = None,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.runner_supervisor = runner_supervisor
        self.log_store = log_store
        self.memory_observer = memory_observer
        self.job_workflows = job_workflows
        self.job_started_at = job_started_at
        self.job_run_requests = job_run_requests
        self.job_run_snapshots = job_run_snapshots
        self.memory_retry_roots = memory_retry_roots
        self.queued_workflow_runs = queued_workflow_runs
        self.validate_package = validate_package
        self.unavailable_package_reason = unavailable_package_reason
        self.apply_input_bindings = apply_input_bindings
        self.ensure_workflow_runner = ensure_workflow_runner
        self.workflow_run_memory_decision = workflow_run_memory_decision
        self.evict_idle_runners = evict_idle_runners
        self.memory_status_payload = memory_status_payload
        self.record_memory_metric = record_memory_metric
        self.start_memory_sampling = start_memory_sampling
        self.history_service = history_service
        self.credential_resolver = credential_resolver
        self.api_nodes_unavailable_reason = api_nodes_unavailable_reason

    async def validate_workflow(self, workflow_id: str) -> WorkflowValidationResult:
        package = self.workflow_loader.get_package(workflow_id)
        unavailable = self.unavailable_package_reason(package)
        if unavailable is not None:
            self.log_store.add(
                "warning",
                "Workflow validation blocked because no preparable capsule is available",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"error": unavailable},
            )
            return WorkflowValidationResult(
                workflow_id=workflow_id,
                valid=False,
                errors=[unavailable],
            )
        dashboard_unavailable = _dashboard_unavailable_reason(package)
        if dashboard_unavailable is not None:
            self.log_store.add(
                "warning",
                "Workflow validation blocked because dashboard setup is incomplete",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"error": dashboard_unavailable},
            )
            return WorkflowValidationResult(
                workflow_id=workflow_id,
                valid=False,
                errors=[dashboard_unavailable],
            )
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)
        validation = await self.validate_package(package, adapter)
        if validation.valid:
            self.log_store.add(
                "info",
                "Workflow validation passed",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"runner_id": runner.runner_id},
            )
        else:
            self.log_store.add(
                "warning",
                "Workflow validation failed",
                "runs.orchestrator",
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
        validated_before_queue: bool = False,
        output_preferences_snapshot: dict[str, dict[str, Any]] | None = None,
        run_submission_snapshot: RunSubmissionSnapshot | None = None,
    ):
        package = self.workflow_loader.get_package(workflow_id)
        unavailable = self.unavailable_package_reason(package)
        if unavailable is not None:
            self._record_run_blocked(package, unavailable)
            self.log_store.add(
                "warning",
                "Workflow run blocked because no preparable capsule is available",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"error": unavailable},
            )
            return WorkflowValidationResult(
                workflow_id=workflow_id,
                valid=False,
                errors=[unavailable],
            )
        dashboard_unavailable = _dashboard_unavailable_reason(package)
        if dashboard_unavailable is not None:
            self._record_run_blocked(package, dashboard_unavailable)
            self.log_store.add(
                "warning",
                "Workflow run blocked because dashboard setup is incomplete",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"error": dashboard_unavailable},
            )
            return WorkflowValidationResult(
                workflow_id=workflow_id,
                valid=False,
                errors=[dashboard_unavailable],
            )
        try:
            credential_plan = build_credential_injection_plan(
                package=package,
                submitted_inputs=inputs,
                credential_resolver=self.credential_resolver,
            )
            runtime_inputs = strip_credential_inputs(package, inputs)
        except CredentialRequirementError as exc:
            self._record_run_blocked(package, str(exc))
            self.log_store.add(
                "warning",
                "Workflow run blocked by credential requirements",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"error": str(exc), "input_keys": sorted(inputs.keys())},
            )
            return WorkflowValidationResult(
                workflow_id=workflow_id,
                valid=False,
                errors=[str(exc)],
            )

        run_submission_snapshot = self._run_submission_snapshot(
            package=package,
            inputs=runtime_inputs,
            output_preferences_snapshot=output_preferences_snapshot,
            run_submission_snapshot=run_submission_snapshot,
        )
        runtime_package = package_for_input_bindings(package, runtime_inputs)
        if self.ensure_workflow_runner is not None:
            runner_unavailable = await self.ensure_workflow_runner(package)
            if runner_unavailable is not None:
                self._record_run_blocked(package, runner_unavailable)
                self.log_store.add(
                    "warning",
                    "Workflow run blocked because the workflow runner is unavailable",
                    "runs.orchestrator",
                    workflow_id=workflow_id,
                    details={"error": runner_unavailable},
                )
                return WorkflowValidationResult(
                    workflow_id=workflow_id,
                    valid=False,
                    errors=[runner_unavailable],
                )
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)

        if not validated_before_queue:
            validation = await self.validate_package(runtime_package, adapter)
            if not validation.valid:
                self._record_run_blocked(package, "; ".join(validation.errors) or "Workflow validation failed")
                self.log_store.add(
                    "warning",
                    "Workflow run blocked by validation failure",
                    "runs.orchestrator",
                    workflow_id=workflow_id,
                    details={
                        "runner_id": runner.runner_id,
                        "missing_models": [model.model_dump() for model in validation.missing_models],
                        "errors": validation.errors,
                    },
                )
                return validation

        graph = self.apply_input_bindings(package, runtime_inputs)

        if package_requires_credential_injection(package) and self.api_nodes_unavailable_reason is not None:
            unavailable_reason = await self.api_nodes_unavailable_reason(package, adapter)
            if unavailable_reason is not None:
                self._record_run_blocked(package, unavailable_reason)
                self.log_store.add(
                    "warning",
                    "Workflow run blocked because ComfyUI API nodes are unavailable",
                    "runs.orchestrator",
                    workflow_id=workflow_id,
                    details={"error": unavailable_reason},
                )
                return WorkflowValidationResult(
                    workflow_id=workflow_id,
                    valid=False,
                    errors=[unavailable_reason],
                )

        memory_decision = self.workflow_run_memory_decision(
            package=package,
            workflow_id=workflow_id,
            runner=runner,
            input_profile_fingerprint=memory_input_profile_fingerprint(runtime_inputs, options),
            memory_retry_after_cleanup=memory_retry_after_cleanup,
        )
        memory_result = await self._handle_memory_decision(
            workflow_id=workflow_id,
            inputs=runtime_inputs,
            options=options,
            runner=runner,
            memory_decision=memory_decision,
            run_submission_snapshot=run_submission_snapshot,
        )
        if memory_result is not None:
            return memory_result

        return await self._submit_run(
            package=package,
            workflow_id=workflow_id,
            graph=graph,
            inputs=runtime_inputs,
            options=options,
            adapter_options=options_with_credential_plan(options, credential_plan),
            runner=runner,
            adapter=adapter,
            memory_decision=memory_decision,
            memory_retry_after_cleanup=memory_retry_after_cleanup,
            run_submission_snapshot=run_submission_snapshot,
        )

    def _run_submission_snapshot(
        self,
        *,
        package: WorkflowPackage,
        inputs: dict[str, Any],
        output_preferences_snapshot: dict[str, dict[str, Any]] | None,
        run_submission_snapshot: RunSubmissionSnapshot | None,
    ) -> RunSubmissionSnapshot:
        if run_submission_snapshot is not None:
            return run_submission_snapshot
        preferences: dict[str, OutputPreference] = {}
        for control_id, raw_preference in (output_preferences_snapshot or {}).items():
            if isinstance(raw_preference, OutputPreference):
                preferences[control_id] = raw_preference
                continue
            if isinstance(raw_preference, dict):
                preferences[control_id] = OutputPreference.model_validate(raw_preference)
        return build_run_submission_snapshot(
            package=package,
            inputs=inputs,
            output_preferences_snapshot=preferences,
        )

    async def _handle_memory_decision(
        self,
        *,
        workflow_id: str,
        inputs: dict[str, Any],
        options: dict[str, Any],
        runner: RunnerDescriptor,
        memory_decision: MemoryGovernorDecision | None,
        run_submission_snapshot: RunSubmissionSnapshot,
    ) -> EngineJob | None:
        if memory_decision is None:
            return None
        if memory_decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY:
            queue_id = f"workflow-run-queue-{workflow_id}-{len(self.queued_workflow_runs) + 1}"
            self.queued_workflow_runs[queue_id] = (
                workflow_id,
                dict(inputs),
                dict(options),
                run_submission_snapshot.model_copy(deep=True),
            )
            self.record_memory_metric("workflow_run_queued_pending_memory")
            self.log_store.add(
                "info",
                "Workflow run queued pending memory",
                "runs.orchestrator",
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
                memory_status=self.memory_status_payload(memory_decision, queue_id=queue_id),
            )
        if memory_decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY:
            self.record_memory_metric("workflow_run_blocked_by_memory")
            self._record_run_blocked_by_workflow_id(workflow_id, memory_decision.user_message)
            self.log_store.add(
                "warning",
                "Workflow run blocked by memory policy",
                "runs.orchestrator",
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
                memory_status=self.memory_status_payload(memory_decision),
            )
        if memory_decision.action is MemoryDecisionAction.EVICT_THEN_START:
            return await self.evict_idle_runners(memory_decision)
        return None

    def _record_run_blocked(self, package: WorkflowPackage, reason: str) -> None:
        if self.history_service is None:
            return
        self.history_service.record_run_blocked(
            workflow_id=package.metadata.id,
            workflow_name=workflow_display_name(package),
            reason=reason,
        )

    def _record_run_blocked_by_workflow_id(self, workflow_id: str, reason: str | None) -> None:
        if self.history_service is None:
            return
        try:
            package = self.workflow_loader.get_package(workflow_id)
            workflow_name = workflow_display_name(package)
        except Exception:
            workflow_name = workflow_id
        self.history_service.record_run_blocked(
            workflow_id=workflow_id,
            workflow_name=workflow_name,
            reason=reason or "Workflow run blocked",
        )

    async def _submit_run(
        self,
        *,
        package: WorkflowPackage,
        workflow_id: str,
        graph: dict[str, Any],
        inputs: dict[str, Any],
        options: dict[str, Any],
        adapter_options: dict[str, Any],
        runner: RunnerDescriptor,
        adapter: EngineAdapter,
        memory_decision: MemoryGovernorDecision | None,
        memory_retry_after_cleanup: bool,
        run_submission_snapshot: RunSubmissionSnapshot,
    ) -> EngineJob:
        self.log_store.add(
            "info",
            "Submitting workflow run",
            "runs.orchestrator",
            workflow_id=workflow_id,
            details={"runner_id": runner.runner_id, "input_keys": sorted(inputs.keys())},
        )
        memory_sampling_started_at = datetime.now(UTC).isoformat()
        pre_submit_snapshot: MachineMemorySnapshot | None = (
            self.memory_observer.snapshot() if self.memory_observer is not None else None
        )
        job = await adapter.run_workflow(package, graph, inputs, adapter_options)
        self.runner_supervisor.register_job(job.job_id, runner.runner_id)
        self.runner_supervisor.mark_runner_job_started(runner.runner_id, job.job_id)
        self.job_workflows[job.job_id] = workflow_id
        self.job_started_at[job.job_id] = datetime.now(UTC)
        self.job_run_requests[job.job_id] = (workflow_id, dict(inputs), safe_options_for_storage(options))
        self.job_run_snapshots[job.job_id] = run_submission_snapshot
        self.memory_retry_roots.setdefault(job.job_id, job.job_id)
        self.start_memory_sampling(
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
                    "memory_status": self.memory_status_payload(memory_decision),
                }
            )
        self.log_store.add(
            "info",
            "Workflow run queued",
            "runs.orchestrator",
            job_id=job.job_id,
            workflow_id=workflow_id,
            details={"runner_id": runner.runner_id},
        )
        return job


def _dashboard_unavailable_reason(package: WorkflowPackage) -> str | None:
    if package.unresolved_runtime_inputs:
        return "Dashboard setup is incomplete because the workflow has unresolved runtime inputs."
    if package.dashboard.status != "configured":
        return "Dashboard setup is incomplete. Configure the dashboard before running this workflow."
    if not any(section.controls for section in package.dashboard.sections):
        return "Dashboard setup is incomplete. Add at least one dashboard widget before running this workflow."
    return None
