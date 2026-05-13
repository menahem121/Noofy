from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.diagnostics import DiagnosticsSink
from app.engine.models import EngineJob, JobResult
from app.gallery import GalleryCaptureService, GalleryItem, RunSubmissionSnapshot
from app.history import HistoryService
from app.runs.job_service import RunJobService
from app.workflows.library import WorkflowLibraryStore

FinishMemorySampling = Callable[[str], Awaitable[None]]
RecordMemoryObservation = Callable[[JobResult], None]
MaybeRetryAfterMemoryCleanup = Callable[[JobResult], Awaitable[EngineJob | None]]


class RunResultService:
    """Run result, SSE, gallery capture, and run-history coordination.

    Memory retry decisions remain injected callbacks for now. That keeps the
    current memory-governor state together while moving result handling into
    the runs domain.
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

    async def get_result(self, job_id: str) -> JobResult | EngineJob:
        adapter = self.job_service.adapter_for_job(job_id)
        result = await adapter.get_result(job_id)
        await self.finish_memory_sampling(result.job_id)
        self.record_memory_observation(result)
        retry_job = await self.maybe_retry_after_memory_cleanup(result)
        if retry_job is not None:
            return retry_job
        gallery_items = await self._capture_gallery_outputs(result)
        self._record_run_history_and_activity(result, gallery_items)
        return result

    async def stream_progress_events(self, job_id: str):
        while True:
            progress = await self.job_service.get_progress(job_id)
            yield f"event: progress\ndata: {progress.model_dump_json()}\n\n"

            if progress.status in {"completed", "failed", "canceled"}:
                result = await self.get_result(job_id)
                yield f"event: result\ndata: {result.model_dump_json()}\n\n"
                return

            await asyncio.sleep(1)

    async def _capture_gallery_outputs(self, result: JobResult) -> list[GalleryItem]:
        if self.gallery_capture_service is None:
            return []
        try:
            return await self.gallery_capture_service.save_completed_job_outputs(
                result=result,
                snapshot=self.job_run_snapshots.get(result.job_id),
                fetch_output=self.job_service.fetch_output,
            )
        except Exception as exc:
            self.log_store.add(
                "error",
                "Gallery capture failed after workflow completion",
                "runs.result_service",
                job_id=result.job_id,
                workflow_id=self.job_workflows.get(result.job_id),
                details={"error": str(exc)},
            )
            return []

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
