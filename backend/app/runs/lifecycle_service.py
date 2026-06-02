"""Backend-owned workflow-run dispatch and terminal watchers."""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.engine.models import EngineJob, JobResult, WorkflowValidationResult
from app.runs.queue_service import (
    WorkflowRunQueueRecord,
    WorkflowRunQueueService,
    WorkflowRunQueueStatus,
)

SubmitQueuedRun = Callable[[WorkflowRunQueueRecord], Awaitable[Any]]
FinalizeJob = Callable[[str], Awaitable[JobResult | EngineJob]]
GetProgress = Callable[[str], Awaitable[Any]]


class RunLifecycleService:
    """Own workflow dispatch wakes, queue loop guards, and job watchers."""

    max_records_per_wake = 8

    def __init__(
        self,
        *,
        queue_service: WorkflowRunQueueService,
        log_store: DiagnosticsSink,
    ) -> None:
        self.queue_service = queue_service
        self.log_store = log_store
        self.submit_queued_run: SubmitQueuedRun | None = None
        self.finalize_job: FinalizeJob | None = None
        self.get_progress: GetProgress | None = None
        self.drain_runner_starts: Callable[[], Awaitable[Any]] | None = None
        self._dispatch_epoch = 0
        self._dispatch_task: asyncio.Task[None] | None = None
        self._dispatch_requested = False
        self._delayed_dispatch_tasks: set[asyncio.Task[None]] = set()
        self._watchers: dict[str, asyncio.Task[None]] = {}
        self._terminal_wakes: dict[str, asyncio.Event] = {}
        self._dispatch_lock = asyncio.Lock()
        self._shutting_down = False

    def request_dispatch(self, reason: str = "state_changed") -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._shutting_down:
            return
        if self._dispatch_task is not None and not self._dispatch_task.done():
            self._dispatch_requested = True
            return
        self._dispatch_task = loop.create_task(self._drain(reason))

    async def handoff(self, queue_id: str) -> Any:
        self._dispatch_epoch += 1
        record = self.queue_service.claim_next(dispatch_epoch=self._dispatch_epoch, queue_id=queue_id)
        if record is None:
            return None
        return await self._handoff_record(record)

    async def _drain(self, reason: str) -> None:
        try:
            async with self._dispatch_lock:
                self._dispatch_epoch += 1
                epoch = self._dispatch_epoch
                self.log_store.add(
                    "debug",
                    "Workflow run dispatcher woke",
                    "runs.lifecycle_service",
                    details={"reason": reason, "dispatch_epoch": epoch},
                )
                for _ in range(self.max_records_per_wake):
                    record = self.queue_service.claim_next(dispatch_epoch=epoch)
                    if record is None:
                        break
                    await self._handoff_record(record)
                if self.drain_runner_starts is not None:
                    await self.drain_runner_starts()
        finally:
            if self._shutting_down:
                self._dispatch_requested = False
                if self._dispatch_task is asyncio.current_task():
                    self._dispatch_task = None
            elif self._dispatch_requested:
                self._dispatch_requested = False
                self._dispatch_task = None
                asyncio.get_running_loop().call_soon(self.request_dispatch, "coalesced_state_change")
            elif self._dispatch_task is asyncio.current_task():
                self._dispatch_task = None

    async def _handoff_record(self, record: WorkflowRunQueueRecord) -> Any:
        if self.submit_queued_run is None:
            return self._requeue(record, reason="dispatcher_not_wired", transient=True)
        self.log_store.add(
            "info",
            "Handing off queued workflow run",
            "runs.lifecycle_service",
            workflow_id=record.workflow_id,
            details={"queue_id": record.queue_id, "attempt": record.attempt_count},
        )
        try:
            result = await self.submit_queued_run(record)
        except Exception as exc:
            return self._requeue(record, reason=f"submission_error:{exc}", transient=True)
        latest = self.queue_service.get(record.queue_id)
        if latest is not None and latest.cancel_requested and isinstance(result, EngineJob):
            return result
        if isinstance(result, EngineJob):
            if result.status == "canceled":
                self.queue_service.mark_terminal(
                    record.queue_id,
                    status=WorkflowRunQueueStatus.CANCELED,
                    reason="canceled_before_submission",
                )
                return result
            if result.status == "queued_pending_memory":
                return self._requeue(
                    record,
                    reason=(result.memory_status or {}).get("state", "waiting_for_memory"),
                    transient=False,
                    wait_for_state_change=True,
                )
            if result.status == "blocked_by_memory":
                self.queue_service.mark_terminal(
                    record.queue_id,
                    status=WorkflowRunQueueStatus.FAILED,
                    reason=(result.memory_status or {}).get("state", result.message),
                )
                return result
            submitted = self.queue_service.mark_submitted(record.queue_id, job_id=result.job_id)
            return result.model_copy(update={"queue_id": submitted.queue_id if submitted is not None else record.queue_id})
        if isinstance(result, WorkflowValidationResult):
            self.queue_service.mark_terminal(
                record.queue_id,
                status=WorkflowRunQueueStatus.FAILED,
                reason="; ".join(result.errors) or "workflow_validation_failed",
            )
        return result

    def _requeue(
        self,
        record: WorkflowRunQueueRecord,
        *,
        reason: str,
        transient: bool,
        wait_for_state_change: bool = False,
    ) -> WorkflowRunQueueRecord | None:
        updated = self.queue_service.requeue(
            record.queue_id,
            reason=reason,
            transient=transient,
            wait_for_state_change=wait_for_state_change,
        )
        self.log_store.add(
            "warning" if transient else "info",
            "Workflow run requeued",
            "runs.lifecycle_service",
            workflow_id=record.workflow_id,
            details={
                "queue_id": record.queue_id,
                "attempt": updated.attempt_count if updated is not None else record.attempt_count,
                "blocking_reason": reason,
                "reservation_state": updated.reservation_token if updated is not None else None,
                "next_eligible_at": updated.next_eligible_at if updated is not None else None,
            },
        )
        if updated is not None and updated.next_eligible_at is not None:
            self._schedule_delayed_dispatch(updated.next_eligible_at)
        return updated

    def track_submitted_job(self, job_id: str) -> None:
        if self._shutting_down:
            return
        if job_id in self._watchers and not self._watchers[job_id].done():
            return
        try:
            self._terminal_wakes[job_id] = asyncio.Event()
            self._watchers[job_id] = asyncio.create_task(self._watch_job(job_id))
        except RuntimeError:
            return

    def notify_terminal_hint(self, job_id: str) -> None:
        wake = self._terminal_wakes.get(job_id)
        if wake is not None:
            wake.set()

    async def _watch_job(self, job_id: str) -> None:
        if self.get_progress is None or self.finalize_job is None:
            return
        wake = self._terminal_wakes[job_id]
        try:
            while True:
                progress = await self.get_progress(job_id)
                if progress.status in {"completed", "failed", "canceled"}:
                    await self.finalize_job(job_id)
                    self.request_dispatch("job_terminal")
                    return
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), timeout=1)
                except TimeoutError:
                    pass
        finally:
            self._terminal_wakes.pop(job_id, None)
            self._watchers.pop(job_id, None)

    def _schedule_delayed_dispatch(self, next_eligible_at: str) -> None:
        if self._shutting_down:
            return

        async def _later() -> None:
            eligible_at = datetime.fromisoformat(next_eligible_at)
            if eligible_at.tzinfo is None:
                eligible_at = eligible_at.replace(tzinfo=UTC)
            await asyncio.sleep(max(0, (eligible_at - datetime.now(UTC)).total_seconds()))
            self.request_dispatch("handoff_backoff_elapsed")

        task = asyncio.create_task(_later())
        self._delayed_dispatch_tasks.add(task)
        task.add_done_callback(self._delayed_dispatch_tasks.discard)

    async def shutdown(self) -> None:
        self._shutting_down = True
        self._dispatch_requested = False
        tasks = [
            *(task for task in [self._dispatch_task] if task is not None),
            *self._delayed_dispatch_tasks,
            *self._watchers.values(),
        ]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._dispatch_task = None
        self._delayed_dispatch_tasks.clear()
        self._watchers.clear()
        self._terminal_wakes.clear()
