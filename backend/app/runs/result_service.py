from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.diagnostics import DiagnosticsSink
from app.engine.models import EngineJob, JobProgress, JobResult
from app.gallery import GalleryCaptureService, GalleryItem, RunSubmissionSnapshot
from app.history import HistoryService
from app.runs.job_service import RunJobService
from app.runs.progress_estimator import WorkflowProgressEstimator, progress_ready_for_result_fetch
from app.runs.queue_service import WorkflowRunQueueService
from app.workflows.library import WorkflowLibraryStore

FinishMemorySampling = Callable[[str], Awaitable[None]]
RecordMemoryObservation = Callable[[JobResult], None]
MaybeRetryAfterMemoryCleanup = Callable[[JobResult], Awaitable[EngineJob | None]]


class RunResultService:
    """Run result, SSE, gallery capture, and run-history coordination.

    Memory retry decisions remain injected runtime/memory callbacks. Terminal
    side effects are guarded here so REST reads, SSE, watchers, and terminal
    hints converge on one finalize-once path.
    """

    def __init__(
        self,
        *,
        job_service: RunJobService,
        log_store: DiagnosticsSink,
        job_workflows: dict[str, str],
        job_started_at: dict[str, datetime],
        job_run_snapshots: dict[str, RunSubmissionSnapshot],
        finish_memory_sampling: FinishMemorySampling,
        record_memory_observation: RecordMemoryObservation,
        maybe_retry_after_memory_cleanup: MaybeRetryAfterMemoryCleanup,
        gallery_capture_service: GalleryCaptureService | None = None,
        workflow_library_store: WorkflowLibraryStore | None = None,
        history_service: HistoryService | None = None,
        workflow_run_queue_service: WorkflowRunQueueService | None = None,
        request_run_dispatch: Callable[[str], None] | None = None,
        progress_estimator: WorkflowProgressEstimator | None = None,
    ) -> None:
        self.job_service = job_service
        self.log_store = log_store
        self.job_workflows = job_workflows
        self.job_started_at = job_started_at
        self.job_run_snapshots = job_run_snapshots
        self.finish_memory_sampling = finish_memory_sampling
        self.record_memory_observation = record_memory_observation
        self.maybe_retry_after_memory_cleanup = maybe_retry_after_memory_cleanup
        self.gallery_capture_service = gallery_capture_service
        self.workflow_library_store = workflow_library_store
        self.history_service = history_service
        self.workflow_run_queue_service = workflow_run_queue_service
        self.request_run_dispatch = request_run_dispatch
        self.progress_estimator = progress_estimator
        self._terminal_locks: dict[str, asyncio.Lock] = {}
        self._terminal_outcomes: dict[str, JobResult | EngineJob] = {}

    async def get_result(self, job_id: str) -> JobResult | EngineJob:
        canonical_job_id = self._canonical_job_id(job_id)
        cached = self._terminal_outcomes.get(canonical_job_id)
        if cached is not None:
            return self._decorate_queue_id(cached, job_id)
        adapter = self.job_service.adapter_for_job(canonical_job_id)
        result = await adapter.get_result(canonical_job_id)
        if result.status in {"completed", "failed", "canceled"}:
            return self._decorate_queue_id(await self._finalize_once(result), job_id)
        return self._decorate_queue_id(result, job_id)

    async def _finalize_once(self, result: JobResult) -> JobResult | EngineJob:
        lock = self._terminal_locks.setdefault(result.job_id, asyncio.Lock())
        async with lock:
            cached = self._terminal_outcomes.get(result.job_id)
            if cached is not None:
                return cached
            mark_job_finished = getattr(self.job_service, "mark_job_finished", None)
            if callable(mark_job_finished):
                mark_job_finished(result.job_id)
            await self.finish_memory_sampling(result.job_id)
            self.record_memory_observation(result)
            retry_job = await self.maybe_retry_after_memory_cleanup(result)
            outcome: JobResult | EngineJob = retry_job or result
            if self.progress_estimator is not None:
                if retry_job is None:
                    self.progress_estimator.finalize_result(result)
                else:
                    self.progress_estimator.discard_job(result.job_id)
            if retry_job is None:
                self._register_gallery_run(result)
                self._record_run_history_and_activity(result, [])
                self._schedule_gallery_auto_saves(result)
            self._terminal_outcomes[result.job_id] = outcome
            if self.workflow_run_queue_service is not None:
                self.workflow_run_queue_service.mark_terminal(result.job_id)
            if self.request_run_dispatch is not None:
                self.request_run_dispatch("job_finalized")
            return outcome

    def _decorate_queue_id(self, result: JobResult | EngineJob, handle: str):
        queue_id = self._queue_id_for(handle)
        if queue_id is None:
            queue_id = self._queue_id_for(result.job_id)
        if queue_id is None:
            return result
        return result.model_copy(update={"queue_id": queue_id})

    def _canonical_job_id(self, handle: str) -> str:
        canonical_job_id = getattr(self.job_service, "canonical_job_id", None)
        return canonical_job_id(handle) if callable(canonical_job_id) else handle

    def _queue_id_for(self, handle: str) -> str | None:
        queue_id_for = getattr(self.job_service, "queue_id_for", None)
        return queue_id_for(handle) if callable(queue_id_for) else None

    def terminal_progress(self, handle: str) -> JobProgress | None:
        canonical_job_id = self._canonical_job_id(handle)
        outcome = self._terminal_outcomes.get(canonical_job_id)
        if not isinstance(outcome, JobResult):
            return None
        return self._progress_from_terminal_result(outcome)

    @staticmethod
    def _progress_from_terminal_result(result: JobResult) -> JobProgress:
        return JobProgress(
            job_id=result.job_id,
            queue_id=result.queue_id,
            status=result.status,
            message=result.error,
        )

    async def stream_progress_events(self, job_id: str):
        while True:
            progress = await self.job_service.get_progress(job_id)
            yield f"event: progress\ndata: {progress.model_dump_json()}\n\n"

            if progress_ready_for_result_fetch(progress):
                result = await self.get_result(job_id)
                yield f"event: result\ndata: {result.model_dump_json()}\n\n"
                return

            await asyncio.sleep(1)

    def _register_gallery_run(self, result: JobResult) -> None:
        if self.gallery_capture_service is None:
            return
        try:
            self.gallery_capture_service.register_completed_run(
                result, self.job_run_snapshots.get(result.job_id)
            )
        except Exception as exc:
            self.log_store.add(
                "error",
                "Gallery run manifest could not be stored",
                "runs.result_service",
                job_id=result.job_id,
                workflow_id=self.job_workflows.get(result.job_id),
                details={"error": str(exc)},
            )

    def _schedule_gallery_auto_saves(self, result: JobResult) -> None:
        if self.gallery_capture_service is None:
            return
        try:
            self.gallery_capture_service.schedule_auto_saves(
                result, self.job_run_snapshots.get(result.job_id)
            )
        except Exception as exc:
            self.log_store.add(
                "error",
                "Gallery Auto Save could not be scheduled",
                "runs.result_service",
                job_id=result.job_id,
                workflow_id=self.job_workflows.get(result.job_id),
                details={"error": str(exc)},
            )

    def _record_run_history_and_activity(self, result: JobResult, gallery_items: list[GalleryItem]) -> None:
        if result.status not in {"completed", "failed", "canceled"}:
            return
        workflow_id = self.job_workflows.get(result.job_id)
        started_at = self.job_started_at.pop(result.job_id, None)
        if workflow_id is None or started_at is None:
            return
        completed_at = datetime.now(UTC)
        if self.workflow_library_store is not None:
            self.workflow_library_store.record_run_result(
                workflow_id=workflow_id,
                job_id=result.job_id,
                status=result.status,
                started_at=started_at,
                finished_at=completed_at,
                error=result.error,
            )
        if self.history_service is not None:
            snapshot = self.job_run_snapshots.get(result.job_id)
            self.history_service.record_run_finished(
                job_id=result.job_id,
                workflow_id=workflow_id,
                workflow_name=snapshot.workflow_title if snapshot is not None else workflow_id,
                status=result.status,
                started_at=started_at,
                completed_at=completed_at,
                error=result.error,
                snapshot=snapshot,
                gallery_items=gallery_items,
            )
