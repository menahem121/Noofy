"""Durable in-process workflow-run queue and public-handle aliases."""
from __future__ import annotations

import threading
import uuid
from collections import deque
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.engine.models import EngineJob, JobProgress, MemoryFailureCode
from app.gallery import RunSubmissionSnapshot


class WorkflowRunQueueStatus(StrEnum):
    QUEUED = "queued"
    HANDING_OFF = "handing_off"
    SUBMITTED = "submitted"
    REQUEUED = "requeued"
    FAILED = "failed"
    CANCELED = "canceled"


class WorkflowRunQueueRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    queue_id: str = Field(min_length=1)
    workflow_id: str = Field(min_length=1)
    inputs: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    run_submission_snapshot: RunSubmissionSnapshot
    validated_before_queue: bool = False
    status: WorkflowRunQueueStatus = WorkflowRunQueueStatus.QUEUED
    prerequisite_runner_start_queue_id: str | None = None
    reservation_token: str | None = None
    submitted_job_id: str | None = None
    cancel_requested: bool = False
    created_at: str
    updated_at: str
    attempt_count: int = Field(default=0, ge=0)
    transient_failure_count: int = Field(default=0, ge=0)
    last_reason: str | None = None
    message: str | None = None
    error_code: MemoryFailureCode | None = None
    memory_status: dict[str, Any] | None = None
    memory_requirement: dict[str, Any] | None = None
    memory_decision: dict[str, Any] | None = None
    next_eligible_at: str | None = None
    last_dispatch_epoch: int | None = None


class WorkflowRunHandle(BaseModel):
    queue_id: str | None = None
    job_id: str
    record: WorkflowRunQueueRecord | None = None


class WorkflowRunQueueService:
    """Own queue records and queue-id to submitted-job aliases.

    Active records are retained until terminal completion. Terminal records are
    bounded so aliases remain useful for recent REST, SSE, log, and output
    lookups without allowing an unbounded in-memory ledger.
    """

    terminal_record_limit = 256
    max_transient_handoff_failures = 8
    initial_backoff_seconds = 0.25
    max_backoff_seconds = 2.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, WorkflowRunQueueRecord] = {}
        self._job_to_queue: dict[str, str] = {}
        self._terminal_queue_ids: deque[str] = deque()
        self._terminal_queue_id_set: set[str] = set()

    def enqueue(
        self,
        *,
        workflow_id: str,
        inputs: dict[str, Any],
        options: dict[str, Any],
        run_submission_snapshot: RunSubmissionSnapshot,
        reason: str | None = None,
        message: str | None = None,
        validated_before_queue: bool = False,
        memory_status: dict[str, Any] | None = None,
        prerequisite_runner_start_queue_id: str | None = None,
        queue_id: str | None = None,
    ) -> WorkflowRunQueueRecord:
        now = _now_iso()
        with self._lock:
            if queue_id is not None and queue_id in self._records:
                record = self._records[queue_id]
                if record.status in {
                    WorkflowRunQueueStatus.CANCELED,
                    WorkflowRunQueueStatus.FAILED,
                    WorkflowRunQueueStatus.SUBMITTED,
                }:
                    return record
                updated = record.model_copy(
                    update={
                        "status": WorkflowRunQueueStatus.REQUEUED,
                        "inputs": dict(inputs),
                        "options": dict(options),
                        "run_submission_snapshot": run_submission_snapshot.model_copy(deep=True),
                        "last_reason": reason,
                        "message": message or record.message,
                        "validated_before_queue": (
                            record.validated_before_queue or validated_before_queue
                        ),
                        "memory_status": (
                            dict(memory_status)
                            if memory_status is not None
                            else record.memory_status
                        ),
                        "prerequisite_runner_start_queue_id": (
                            prerequisite_runner_start_queue_id
                            or record.prerequisite_runner_start_queue_id
                        ),
                        "updated_at": now,
                    }
                )
                self._records[queue_id] = updated
                return updated
            created = WorkflowRunQueueRecord(
                queue_id=queue_id or f"workflow-run-queue-{uuid.uuid4().hex}",
                workflow_id=workflow_id,
                inputs=dict(inputs),
                options=dict(options),
                run_submission_snapshot=run_submission_snapshot.model_copy(deep=True),
                validated_before_queue=validated_before_queue,
                prerequisite_runner_start_queue_id=prerequisite_runner_start_queue_id,
                last_reason=reason,
                message=message,
                memory_status=dict(memory_status) if memory_status is not None else None,
                created_at=now,
                updated_at=now,
            )
            self._records[created.queue_id] = created
            return created

    def get(self, queue_id: str) -> WorkflowRunQueueRecord | None:
        with self._lock:
            return self._records.get(queue_id)

    def list_records(self) -> list[WorkflowRunQueueRecord]:
        with self._lock:
            return sorted(self._records.values(), key=lambda item: (item.created_at, item.queue_id))

    def list_records_for_workflow(self, workflow_id: str) -> list[WorkflowRunQueueRecord]:
        with self._lock:
            return sorted(
                (
                    record
                    for record in self._records.values()
                    if record.workflow_id == workflow_id
                ),
                key=lambda item: (item.created_at, item.queue_id),
            )

    def claim_next(
        self,
        *,
        dispatch_epoch: int,
        queue_id: str | None = None,
    ) -> WorkflowRunQueueRecord | None:
        now = datetime.now(UTC)
        with self._lock:
            candidates = sorted(self._records.values(), key=lambda item: (item.created_at, item.queue_id))
            for record in candidates:
                if queue_id is not None and record.queue_id != queue_id:
                    continue
                if record.status not in {
                    WorkflowRunQueueStatus.QUEUED,
                    WorkflowRunQueueStatus.REQUEUED,
                }:
                    continue
                if record.cancel_requested or record.last_dispatch_epoch == dispatch_epoch:
                    continue
                if record.next_eligible_at is not None and _parse_iso(record.next_eligible_at) > now:
                    continue
                updated = record.model_copy(
                    update={
                        "status": WorkflowRunQueueStatus.HANDING_OFF,
                        "attempt_count": record.attempt_count + 1,
                        "last_dispatch_epoch": dispatch_epoch,
                        "updated_at": _now_iso(),
                    }
                )
                self._records[record.queue_id] = updated
                return updated
        return None

    def requeue(
        self,
        queue_id: str,
        *,
        reason: str,
        transient: bool,
        wait_for_state_change: bool = False,
        message: str | None = None,
        memory_status: dict[str, Any] | None = None,
        memory_requirement: dict[str, Any] | None = None,
        memory_decision: dict[str, Any] | None = None,
    ) -> WorkflowRunQueueRecord | None:
        with self._lock:
            record = self._records.get(queue_id)
            if record is None or record.status in {
                WorkflowRunQueueStatus.CANCELED,
                WorkflowRunQueueStatus.FAILED,
                WorkflowRunQueueStatus.SUBMITTED,
            }:
                return record
            failure_count = record.transient_failure_count + (1 if transient else 0)
            if transient and failure_count >= self.max_transient_handoff_failures:
                return self._finish_locked(
                    record.model_copy(update={"transient_failure_count": failure_count}),
                    status=WorkflowRunQueueStatus.FAILED,
                    reason=reason,
                )
            backoff = None
            if transient and not wait_for_state_change:
                seconds = min(
                    self.max_backoff_seconds,
                    self.initial_backoff_seconds * (2 ** max(0, failure_count - 1)),
                )
                backoff = _iso(datetime.now(UTC) + timedelta(seconds=seconds))
            updated = record.model_copy(
                update={
                    "status": WorkflowRunQueueStatus.REQUEUED,
                    "last_reason": reason,
                    "message": message or record.message,
                    "memory_status": (
                        dict(memory_status)
                        if memory_status is not None
                        else record.memory_status
                    ),
                    "memory_requirement": (
                        dict(memory_requirement)
                        if memory_requirement is not None
                        else record.memory_requirement
                    ),
                    "memory_decision": (
                        dict(memory_decision)
                        if memory_decision is not None
                        else record.memory_decision
                    ),
                    "transient_failure_count": failure_count,
                    "next_eligible_at": backoff,
                    "reservation_token": None,
                    "updated_at": _now_iso(),
                }
            )
            self._records[queue_id] = updated
            return updated

    def update_progress(
        self,
        queue_id: str,
        *,
        reason: str,
        message: str,
        memory_status: dict[str, Any] | None = None,
        memory_requirement: dict[str, Any] | None = None,
        memory_decision: dict[str, Any] | None = None,
    ) -> WorkflowRunQueueRecord | None:
        with self._lock:
            record = self._records.get(queue_id)
            if record is None or record.status in {
                WorkflowRunQueueStatus.CANCELED,
                WorkflowRunQueueStatus.FAILED,
                WorkflowRunQueueStatus.SUBMITTED,
            }:
                return record
            updated = record.model_copy(
                update={
                    "last_reason": reason,
                    "message": message,
                    "memory_status": (
                        dict(memory_status)
                        if memory_status is not None
                        else record.memory_status
                    ),
                    "memory_requirement": (
                        dict(memory_requirement)
                        if memory_requirement is not None
                        else record.memory_requirement
                    ),
                    "memory_decision": (
                        dict(memory_decision)
                        if memory_decision is not None
                        else record.memory_decision
                    ),
                    "updated_at": _now_iso(),
                }
            )
            self._records[queue_id] = updated
            return updated

    def mark_submitted(
        self,
        queue_id: str,
        *,
        job_id: str,
    ) -> WorkflowRunQueueRecord | None:
        with self._lock:
            record = self._records.get(queue_id)
            if record is None:
                return None
            updated = record.model_copy(
                update={
                    "status": WorkflowRunQueueStatus.SUBMITTED,
                    "submitted_job_id": job_id,
                    "reservation_token": None,
                    "updated_at": _now_iso(),
                }
            )
            self._records[queue_id] = updated
            self._job_to_queue[job_id] = queue_id
            return updated

    def set_reservation(self, queue_id: str, token: str | None) -> WorkflowRunQueueRecord | None:
        with self._lock:
            record = self._records.get(queue_id)
            if record is None:
                return None
            updated = record.model_copy(
                update={"reservation_token": token, "updated_at": _now_iso()}
            )
            self._records[queue_id] = updated
            return updated

    def mark_terminal(
        self,
        handle: str,
        *,
        status: WorkflowRunQueueStatus | None = None,
        reason: str | None = None,
        message: str | None = None,
        error_code: MemoryFailureCode | None = None,
        memory_status: dict[str, Any] | None = None,
        memory_requirement: dict[str, Any] | None = None,
        memory_decision: dict[str, Any] | None = None,
    ) -> WorkflowRunQueueRecord | None:
        with self._lock:
            queue_id = self._queue_id_locked(handle)
            record = self._records.get(queue_id) if queue_id is not None else None
            if record is None:
                return None
            return self._finish_locked(
                record,
                status=status or record.status,
                reason=reason,
                message=message,
                error_code=error_code,
                memory_status=memory_status,
                memory_requirement=memory_requirement,
                memory_decision=memory_decision,
            )

    def cancel(self, handle: str) -> WorkflowRunQueueRecord | None:
        with self._lock:
            queue_id = self._queue_id_locked(handle)
            record = self._records.get(queue_id) if queue_id is not None else None
            if record is None:
                return None
            if record.status in {WorkflowRunQueueStatus.FAILED, WorkflowRunQueueStatus.CANCELED}:
                return record
            if record.status is WorkflowRunQueueStatus.SUBMITTED:
                updated = record.model_copy(update={"cancel_requested": True, "updated_at": _now_iso()})
                self._records[queue_id] = updated
                return updated
            if record.status is WorkflowRunQueueStatus.HANDING_OFF:
                updated = record.model_copy(update={"cancel_requested": True, "updated_at": _now_iso()})
                self._records[queue_id] = updated
                return updated
            return self._finish_locked(record, status=WorkflowRunQueueStatus.CANCELED, reason="canceled")

    def resolve(self, handle: str) -> WorkflowRunHandle:
        with self._lock:
            queue_id = self._queue_id_locked(handle)
            record = self._records.get(queue_id) if queue_id is not None else None
            job_id = record.submitted_job_id if record is not None and record.submitted_job_id else handle
            return WorkflowRunHandle(queue_id=queue_id, job_id=job_id, record=record)

    def is_terminal(self, handle: str) -> bool:
        with self._lock:
            queue_id = self._queue_id_locked(handle)
            return queue_id in self._terminal_queue_id_set if queue_id is not None else False

    def progress(self, handle: str) -> JobProgress | None:
        resolved = self.resolve(handle)
        record = resolved.record
        if record is None or record.status is WorkflowRunQueueStatus.SUBMITTED:
            return None
        if record.cancel_requested and record.status is not WorkflowRunQueueStatus.SUBMITTED:
            status = "canceled"
            message = "Workflow run cancellation requested."
        elif record.status is WorkflowRunQueueStatus.CANCELED:
            status = "canceled"
            message = "Workflow run canceled."
        elif record.status is WorkflowRunQueueStatus.FAILED:
            status = "failed"
            message = record.message or record.last_reason or "Workflow run could not be started."
        else:
            status = "queued_pending_memory"
            message = record.message or _queued_wait_message(record.last_reason)
        developer_details = {
            key: value
            for key, value in {
                "memory_status": record.memory_status,
                "memory_decision": record.memory_decision,
            }.items()
            if value is not None
        }
        return JobProgress(
            job_id=record.queue_id,
            queue_id=record.queue_id,
            status=status,
            message=message,
            error_code=record.error_code,
            memory_requirement=record.memory_requirement,
            memory_status=record.memory_status,
            developer_details=developer_details,
        )

    def terminal_job(self, handle: str) -> EngineJob | None:
        resolved = self.resolve(handle)
        record = resolved.record
        if record is None or record.status not in {
            WorkflowRunQueueStatus.FAILED,
            WorkflowRunQueueStatus.CANCELED,
        }:
            return None
        if record.status is WorkflowRunQueueStatus.CANCELED:
            status = "canceled"
        elif record.error_code == "insufficient_memory":
            status = "blocked_by_memory"
        else:
            status = "failed"
        return EngineJob(
            job_id=record.queue_id,
            queue_id=record.queue_id,
            workflow_id=record.workflow_id,
            engine="noofy",
            status=status,
            message=record.message or record.last_reason,
            error_code=record.error_code,
            memory_status=record.memory_status,
            memory_requirement=record.memory_requirement,
            memory_decision=record.memory_decision,
        )

    def _queue_id_locked(self, handle: str) -> str | None:
        if handle in self._records:
            return handle
        return self._job_to_queue.get(handle)

    def _finish_locked(
        self,
        record: WorkflowRunQueueRecord,
        *,
        status: WorkflowRunQueueStatus,
        reason: str | None,
        message: str | None = None,
        error_code: MemoryFailureCode | None = None,
        memory_status: dict[str, Any] | None = None,
        memory_requirement: dict[str, Any] | None = None,
        memory_decision: dict[str, Any] | None = None,
    ) -> WorkflowRunQueueRecord:
        updated = record.model_copy(
            update={
                "status": status,
                "last_reason": reason or record.last_reason,
                "message": message or record.message,
                "error_code": error_code or record.error_code,
                "memory_status": (
                    dict(memory_status)
                    if memory_status is not None
                    else record.memory_status
                ),
                "memory_requirement": (
                    dict(memory_requirement)
                    if memory_requirement is not None
                    else record.memory_requirement
                ),
                "memory_decision": (
                    dict(memory_decision)
                    if memory_decision is not None
                    else record.memory_decision
                ),
                "reservation_token": None,
                "updated_at": _now_iso(),
            }
        )
        self._records[record.queue_id] = updated
        if record.queue_id not in self._terminal_queue_id_set:
            self._terminal_queue_ids.append(record.queue_id)
            self._terminal_queue_id_set.add(record.queue_id)
        self._prune_terminal_locked()
        return updated

    def _prune_terminal_locked(self) -> None:
        while len(self._terminal_queue_ids) > self.terminal_record_limit:
            queue_id = self._terminal_queue_ids.popleft()
            self._terminal_queue_id_set.discard(queue_id)
            record = self._records.get(queue_id)
            if record is None:
                continue
            self._records.pop(queue_id, None)
            if record.submitted_job_id is not None:
                self._job_to_queue.pop(record.submitted_job_id, None)


def _queued_wait_message(reason: str | None) -> str:
    """User-facing wait copy for a queued run that has not been submitted yet.

    Runs queued behind the workflow's own active run (or its starting runner)
    are normal queue behavior and must not read like a memory problem.
    """
    if reason in {"runner_submission_reservation_unavailable", "queued_behind_active_run"}:
        return "This run is queued and will start when the current run finishes."
    if reason == "waiting_for_active_workflow":
        return "Noofy will start this workflow after the active run finishes."
    if reason == "starting":
        return "This run will start when the workflow's runner is ready."
    if reason == "preparing_run":
        return "Preparing this workflow to run."
    if reason == "unloading_previous_workflow":
        return "Noofy is unloading the previous workflow before starting this one."
    if reason in {"freeing_previous_models", "freeing_memory"}:
        return "Noofy is freeing memory before starting this workflow."
    if reason == "waiting_for_memory_release":
        return "Noofy is waiting for the previous workflow's memory to be released."
    return "Waiting for enough memory to start this workflow."


def _now_iso() -> str:
    return _iso(datetime.now(UTC))


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
