from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from app.diagnostics import DiagnosticsSink
from app.engine.adapter import EngineAdapter
from app.engine.errors import EngineUserFixableValidationError
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
from app.runtime.memory.input_features import (
    build_memory_signature_set,
    extract_model_selection_features,
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
from app.workflows.run_input_validation import (
    validate_run_inputs,
    validation_result_for_user_errors,
)
from app.runs.media_staging import (
    MediaInputStagingError,
    MediaInputStagingResolver,
    cleanup_staged_media_files,
)
from app.runs.queue_service import (
    WorkflowRunQueueRecord,
    WorkflowRunQueueService,
    WorkflowRunQueueStatus,
)

ValidatePackage = Callable[[WorkflowPackage, EngineAdapter], Awaitable[WorkflowValidationResult]]
UnavailablePackageReason = Callable[[WorkflowPackage], str | None]
ApplyInputBindings = Callable[[WorkflowPackage, dict[str, Any]], dict[str, Any]]
EnsureWorkflowRunner = Callable[[WorkflowPackage], Awaitable[str | dict[str, Any] | None]]
WorkflowRunMemoryDecision = Callable[..., MemoryGovernorDecision | None]
EvictIdleRunners = Callable[[MemoryGovernorDecision], Awaitable[EngineJob | None]]
MemoryStatusPayload = Callable[..., dict[str, Any]]
RecordMemoryMetric = Callable[[str], None]
StartMemorySampling = Callable[..., None]
ApiNodesUnavailableReason = Callable[[WorkflowPackage, EngineAdapter], Awaitable[str | None]]


class RunOrchestrator:
    """Validate and submit user workflow runs.

    Memory admission and retry state are supplied by runtime/memory callbacks.
    Workflow-run queue records and public-handle aliases stay in runs/.
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
        job_memory_profile_fingerprints: dict[str, str],
        job_memory_signatures: dict[str, dict[str, Any]],
        job_run_snapshots: dict[str, RunSubmissionSnapshot],
        memory_retry_roots: dict[str, str],
        workflow_run_queue_service: WorkflowRunQueueService,
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
        media_staging_resolver: MediaInputStagingResolver | None = None,
        request_run_dispatch: Callable[[str], None] | None = None,
        submitted_job_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.runner_supervisor = runner_supervisor
        self.log_store = log_store
        self.memory_observer = memory_observer
        self.job_workflows = job_workflows
        self.job_started_at = job_started_at
        self.job_run_requests = job_run_requests
        self.job_memory_profile_fingerprints = job_memory_profile_fingerprints
        self.job_memory_signatures = job_memory_signatures
        self.job_run_snapshots = job_run_snapshots
        self.memory_retry_roots = memory_retry_roots
        self.workflow_run_queue_service = workflow_run_queue_service
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
        self.media_staging_resolver = media_staging_resolver
        self.request_run_dispatch = request_run_dispatch
        self.submitted_job_callback = submitted_job_callback

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
        queue_id: str | None = None,
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
        user_errors = validate_run_inputs(package, inputs)
        if user_errors:
            primary_error = user_errors[0]
            self._record_run_blocked(package, primary_error.user_message)
            self.log_store.add(
                "warning",
                "Workflow run blocked by dashboard input validation",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"user_errors": [error.model_dump(mode="json") for error in user_errors]},
            )
            return validation_result_for_user_errors(package, user_errors)
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
            if isinstance(runner_unavailable, dict):
                runner_start_queue_id = runner_unavailable.get("queue_id")
                queued = self.workflow_run_queue_service.enqueue(
                    workflow_id=workflow_id,
                    inputs=runtime_inputs,
                    options=options,
                    run_submission_snapshot=run_submission_snapshot,
                    reason=str(runner_unavailable.get("status") or "waiting_for_runner_start"),
                    prerequisite_runner_start_queue_id=(
                        str(runner_start_queue_id)
                        if runner_start_queue_id is not None
                        else None
                    ),
                    queue_id=queue_id,
                )
                return EngineJob(
                    job_id=queued.queue_id,
                    queue_id=queued.queue_id,
                    workflow_id=workflow_id,
                    engine="noofy",
                    status="queued_pending_memory",
                    message="This workflow is waiting for its isolated runner to become ready.",
                    memory_status={
                        "state": "waiting_for_active_workflow",
                        "message": "This workflow is waiting for its isolated runner to become ready.",
                        "runner_start_queue_id": queued.prerequisite_runner_start_queue_id,
                    },
                )
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

        if self._queue_cancel_requested(queue_id):
            return self._canceled_queue_job(workflow_id, queue_id)
        reservation = self.runner_supervisor.reserve_runner_for_submission(
            runner.runner_id,
            workflow_id=workflow_id,
        )
        if reservation is None:
            return self._runner_reservation_wait_job(
                workflow_id=workflow_id,
                inputs=runtime_inputs,
                options=options,
                runner=runner,
                run_submission_snapshot=run_submission_snapshot,
                queue_id=queue_id,
            )
        if queue_id is not None:
            self.workflow_run_queue_service.set_reservation(queue_id, reservation.token)
        input_profile_fingerprint = memory_input_profile_fingerprint(
            runtime_inputs,
            options,
            package=package,
        )
        try:
            memory_decision = self.workflow_run_memory_decision(
                package=runtime_package,
                workflow_id=workflow_id,
                runner=runner,
                inputs=runtime_inputs,
                options=options,
                input_profile_fingerprint=input_profile_fingerprint,
                memory_retry_after_cleanup=memory_retry_after_cleanup,
                queued_model_residency_payloads=self._queued_model_residency_payloads(
                    exclude_queue_id=queue_id
                ),
            )
            memory_result = await self._handle_memory_decision(
                workflow_id=workflow_id,
                inputs=runtime_inputs,
                options=options,
                runner=runner,
                memory_decision=memory_decision,
                run_submission_snapshot=run_submission_snapshot,
                queue_id=queue_id,
                reservation_token=reservation.token,
            )
        except Exception:
            self.runner_supervisor.rollback_runner_reservation(reservation.token)
            raise
        if memory_result is not None:
            return memory_result
        if (
            memory_decision is not None
            and memory_decision.action is MemoryDecisionAction.EVICT_THEN_START
        ):
            reservation = self.runner_supervisor.reserve_runner_for_submission(
                runner.runner_id,
                workflow_id=workflow_id,
            )
            if reservation is None:
                return self._runner_reservation_wait_job(
                    workflow_id=workflow_id,
                    inputs=runtime_inputs,
                    options=options,
                    runner=runner,
                    run_submission_snapshot=run_submission_snapshot,
                    queue_id=queue_id,
                )
            if queue_id is not None:
                self.workflow_run_queue_service.set_reservation(queue_id, reservation.token)

        return await self._submit_run(
            package=package,
            workflow_id=workflow_id,
            inputs=runtime_inputs,
            options=options,
            adapter_options=options_with_credential_plan(options, credential_plan),
            runner=runner,
            adapter=adapter,
            memory_decision=memory_decision,
            memory_retry_after_cleanup=memory_retry_after_cleanup,
            run_submission_snapshot=run_submission_snapshot,
            input_profile_fingerprint=input_profile_fingerprint,
            queue_id=queue_id,
            reservation_token=reservation.token,
        )

    async def handoff_queued_run(self, record: WorkflowRunQueueRecord):
        return await self.run_workflow(
            record.workflow_id,
            dict(record.inputs),
            dict(record.options),
            validated_before_queue=True,
            run_submission_snapshot=record.run_submission_snapshot.model_copy(deep=True),
            queue_id=record.queue_id,
        )

    def _queued_model_residency_payloads(
        self,
        *,
        exclude_queue_id: str | None,
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for record in self.workflow_run_queue_service.list_records():
            if record.queue_id == exclude_queue_id or record.cancel_requested:
                continue
            if record.status not in {
                WorkflowRunQueueStatus.QUEUED,
                WorkflowRunQueueStatus.REQUEUED,
                WorkflowRunQueueStatus.HANDING_OFF,
            }:
                continue
            try:
                package = self.workflow_loader.get_package(record.workflow_id)
                runtime_package = package_for_input_bindings(package, dict(record.inputs))
                model_selections = extract_model_selection_features(
                    runtime_package,
                    dict(record.inputs),
                )
                signatures = build_memory_signature_set(
                    runner_process_compatibility_key=None,
                    model_selections=model_selections,
                    execution_profile={},
                )
            except Exception as exc:
                self.log_store.add(
                    "warning",
                    "Queued workflow model-residency demand could not be summarized",
                    "runs.orchestrator",
                    workflow_id=record.workflow_id,
                    details={"queue_id": record.queue_id, "error": str(exc)},
                )
                continue
            if signatures.model_residency_payload:
                payloads.append(signatures.model_residency_payload)
        return payloads

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
        queue_id: str | None,
        reservation_token: str,
    ) -> EngineJob | None:
        if memory_decision is None:
            return None
        if memory_decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY:
            self.runner_supervisor.rollback_runner_reservation(
                reservation_token,
                notify_state_change=False,
            )
            queued = self.workflow_run_queue_service.enqueue(
                workflow_id=workflow_id,
                inputs=inputs,
                options=options,
                run_submission_snapshot=run_submission_snapshot,
                reason=memory_decision.reason_code,
                queue_id=queue_id,
            )
            self.record_memory_metric("workflow_run_queued_pending_memory")
            self.log_store.add(
                "info",
                "Workflow run queued pending memory",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={
                    "queue_id": queued.queue_id,
                    "runner_id": runner.runner_id,
                    "memory_decision_id": memory_decision.decision_id,
                    "reason": memory_decision.reason_code,
                },
            )
            return EngineJob(
                job_id=queued.queue_id,
                workflow_id=workflow_id,
                engine="noofy",
                status="queued_pending_memory",
                queue_id=queued.queue_id,
                message=memory_decision.user_message,
                memory_decision=memory_decision.model_dump(mode="json"),
                memory_status=self.memory_status_payload(memory_decision, queue_id=queued.queue_id),
            )
        if memory_decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY:
            self.runner_supervisor.rollback_runner_reservation(
                reservation_token,
                notify_state_change=False,
            )
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
            self.runner_supervisor.rollback_runner_reservation(
                reservation_token,
                notify_state_change=False,
            )
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
        inputs: dict[str, Any],
        options: dict[str, Any],
        adapter_options: dict[str, Any],
        runner: RunnerDescriptor,
        adapter: EngineAdapter,
        memory_decision: MemoryGovernorDecision | None,
        memory_retry_after_cleanup: bool,
        run_submission_snapshot: RunSubmissionSnapshot,
        input_profile_fingerprint: str,
        queue_id: str | None,
        reservation_token: str,
    ) -> EngineJob | WorkflowValidationResult:
        if self._queue_cancel_requested(queue_id):
            self.runner_supervisor.rollback_runner_reservation(reservation_token)
            return self._canceled_queue_job(workflow_id, queue_id)
        job_id = str(uuid4())
        try:
            media_staging = (
                self.media_staging_resolver.stage_media_inputs(
                    package=package,
                    inputs=inputs,
                    runner=runner,
                    adapter=adapter,
                    job_id=job_id,
                )
                if self.media_staging_resolver is not None
                else None
            )
        except MediaInputStagingError as exc:
            self.runner_supervisor.rollback_runner_reservation(reservation_token)
            self._record_run_blocked(package, str(exc))
            self.log_store.add(
                "warning",
                "Workflow run blocked by media input staging",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"error": str(exc)},
            )
            return WorkflowValidationResult(
                workflow_id=workflow_id,
                valid=False,
                errors=[str(exc)],
            )
        resolved_inputs = media_staging.inputs if media_staging is not None else inputs
        adapter_options = {
            **adapter_options,
            "job_id": job_id,
            "_noofy_staged_files": [str(path) for path in (media_staging.staged_files if media_staging else [])],
        }
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
        try:
            graph = self.apply_input_bindings(package, resolved_inputs)
            self.runner_supervisor.mark_runner_submitting(reservation_token)
            if self._queue_cancel_requested(queue_id):
                self.runner_supervisor.rollback_runner_reservation(reservation_token)
                cleanup_staged_media_files(media_staging.staged_files if media_staging else [])
                return self._canceled_queue_job(workflow_id, queue_id)
            job = await adapter.run_workflow(package, graph, resolved_inputs, adapter_options)
        except EngineUserFixableValidationError as exc:
            self.runner_supervisor.rollback_runner_reservation(reservation_token)
            cleanup_staged_media_files(media_staging.staged_files if media_staging else [])
            self._record_run_blocked(package, exc.user_error.user_message)
            self.log_store.add(
                "warning",
                "Workflow run blocked by engine input validation",
                "runs.orchestrator",
                workflow_id=workflow_id,
                details={"user_error": exc.user_error.model_dump(mode="json")},
            )
            return validation_result_for_user_errors(package, [exc.user_error])
        except Exception:
            self.runner_supervisor.rollback_runner_reservation(reservation_token)
            cleanup_staged_media_files(media_staging.staged_files if media_staging else [])
            raise
        memory_signatures = (
            (memory_decision.developer_details or {}).get("memory_signatures")
            if memory_decision is not None
            else None
        )
        model_residency_signature = (
            memory_signatures.get("model_residency_signature")
            if isinstance(memory_signatures, dict)
            and isinstance(memory_signatures.get("model_residency_signature"), str)
            else None
        )
        model_residency_payload = _memory_signature_payload(
            memory_signatures,
            "model_residency",
        )
        execution_profile_signature = (
            memory_signatures.get("execution_profile_signature")
            if isinstance(memory_signatures, dict)
            and isinstance(memory_signatures.get("execution_profile_signature"), str)
            else None
        )
        self.runner_supervisor.commit_runner_submission(
            reservation_token,
            job_id=job.job_id,
            workflow_id=workflow_id,
            model_residency_signature=model_residency_signature,
            model_residency_payload=model_residency_payload,
            execution_profile_signature=execution_profile_signature,
            memory_signatures_known=isinstance(memory_signatures, dict),
        )
        if queue_id is not None:
            self.workflow_run_queue_service.mark_submitted(queue_id, job_id=job.job_id)
        self.job_workflows[job.job_id] = workflow_id
        self.job_started_at[job.job_id] = datetime.now(UTC)
        self.job_run_requests[job.job_id] = (workflow_id, dict(inputs), safe_options_for_storage(options))
        self.job_memory_profile_fingerprints[job.job_id] = input_profile_fingerprint
        if isinstance(memory_signatures, dict):
            self.job_memory_signatures[job.job_id] = memory_signatures
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
        if queue_id is not None:
            job = job.model_copy(update={"queue_id": queue_id})
            queued = self.workflow_run_queue_service.get(queue_id)
            if queued is not None and queued.cancel_requested:
                await adapter.cancel_job(job.job_id)
        if self.submitted_job_callback is not None:
            self.submitted_job_callback(job.job_id)
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

    def _runner_reservation_wait_job(
        self,
        *,
        workflow_id: str,
        inputs: dict[str, Any],
        options: dict[str, Any],
        runner: RunnerDescriptor,
        run_submission_snapshot: RunSubmissionSnapshot,
        queue_id: str | None,
    ) -> EngineJob:
        queued = self.workflow_run_queue_service.enqueue(
            workflow_id=workflow_id,
            inputs=inputs,
            options=options,
            run_submission_snapshot=run_submission_snapshot,
            reason="runner_submission_reservation_unavailable",
            queue_id=queue_id,
        )
        self.log_store.add(
            "info",
            "Workflow run queued because runner submission reservation is busy",
            "runs.orchestrator",
            workflow_id=workflow_id,
            details={"queue_id": queued.queue_id, "runner_id": runner.runner_id},
        )
        if self.request_run_dispatch is not None and queue_id is None:
            self.request_run_dispatch("submission_reservation_busy")
        return EngineJob(
            job_id=queued.queue_id,
            queue_id=queued.queue_id,
            workflow_id=workflow_id,
            engine="noofy",
            status="queued_pending_memory",
            message="This workflow is waiting for the current runner handoff to finish.",
            memory_status={
                "state": "waiting_for_active_workflow",
                "message": "This workflow is waiting for the current runner handoff to finish.",
            },
        )

    def _queue_cancel_requested(self, queue_id: str | None) -> bool:
        if queue_id is None:
            return False
        queued = self.workflow_run_queue_service.get(queue_id)
        return queued is not None and queued.cancel_requested

    @staticmethod
    def _canceled_queue_job(workflow_id: str, queue_id: str | None) -> EngineJob:
        assert queue_id is not None
        return EngineJob(
            job_id=queue_id,
            queue_id=queue_id,
            workflow_id=workflow_id,
            engine="noofy",
            status="canceled",
            message="Workflow run canceled.",
        )


def _dashboard_unavailable_reason(package: WorkflowPackage) -> str | None:
    if package.unresolved_runtime_inputs:
        return "Dashboard setup is incomplete because the workflow has unresolved runtime inputs."
    if package.dashboard.status != "configured":
        return "Dashboard setup is incomplete. Configure the dashboard before running this workflow."
    if not any(section.controls for section in package.dashboard.sections):
        return "Dashboard setup is incomplete. Add at least one dashboard widget before running this workflow."
    return None


def _memory_signature_payload(
    memory_signatures: Any,
    payload_name: str,
) -> dict[str, Any]:
    if not isinstance(memory_signatures, dict):
        return {}
    payloads = memory_signatures.get("payloads")
    if not isinstance(payloads, dict):
        return {}
    payload = payloads.get(payload_name)
    return dict(payload) if isinstance(payload, dict) else {}
