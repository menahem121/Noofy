from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import cast

from app.diagnostics import DiagnosticsStore
from app.engine.adapter import EngineAdapter
from app.engine.models import DiagnosticLogResponse, EngineOutputStream, JobProgress, LogLevel
from app.runtime.runners.supervisor import JobRunnerNotFoundError, RunnerStatus, RunnerSupervisor
from app.runs.progress_estimator import WorkflowProgressEstimator
from app.runs.queue_service import WorkflowRunQueueService, WorkflowRunQueueStatus
from app.workflows.loader import WorkflowPackageLoader

terminal_statuses = {"completed", "failed", "canceled"}


class RunJobService:
    """Thin job query layer: progress, cancellation, output fetch, and job logs.

    Result handling lives in RunResultService. Queue aliases resolve here
    before adapter routing so every REST surface follows the same contract.
    """

    def __init__(
        self,
        runner_supervisor: RunnerSupervisor,
        log_store: DiagnosticsStore,
        workflow_loader: WorkflowPackageLoader | None = None,
        workflow_run_queue_service: WorkflowRunQueueService | None = None,
        progress_estimator: WorkflowProgressEstimator | None = None,
    ) -> None:
        self.runner_supervisor = runner_supervisor
        self.log_store = log_store
        self.workflow_loader = workflow_loader
        self.workflow_run_queue_service = workflow_run_queue_service
        self.progress_estimator = progress_estimator
        self.terminal_job_progress: Callable[[str], JobProgress | None] | None = None

    async def get_progress(
        self,
        job_id: str,
        since_preview_sequence: int | None = None,
    ) -> JobProgress:
        resolved = self._resolve(job_id)
        if self.terminal_job_progress is not None:
            terminal = self.terminal_job_progress(resolved.job_id)
            if terminal is not None:
                return self._decorate_progress(
                    terminal.model_copy(update={"queue_id": resolved.queue_id}),
                    final_result_ready=True,
                )
        if self.workflow_run_queue_service is not None:
            progress = self.workflow_run_queue_service.progress(resolved.queue_id or job_id)
            if progress is not None:
                return progress
        adapter = self._adapter_for_job(resolved.job_id)
        progress = await adapter.get_progress(
            resolved.job_id,
            since_preview_sequence=since_preview_sequence,
        )
        return self._decorate_progress(progress.model_copy(update={"queue_id": resolved.queue_id}))

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.log_store.add("info", "Cancel requested", "runs.job_service", job_id=job_id)
        resolved = self._resolve(job_id)
        if self.terminal_job_progress is not None:
            terminal = self.terminal_job_progress(resolved.job_id)
            if terminal is not None:
                return terminal.model_copy(update={"queue_id": resolved.queue_id})
        if self.workflow_run_queue_service is not None and resolved.queue_id is not None:
            queued = self.workflow_run_queue_service.cancel(resolved.queue_id)
            if queued is not None and queued.status is not WorkflowRunQueueStatus.SUBMITTED:
                if queued.reservation_token is not None:
                    canceled_before_submission = (
                        self.runner_supervisor.cancel_pre_submission_reservation(
                            queued.reservation_token
                        )
                    )
                else:
                    canceled_before_submission = True
                if not canceled_before_submission:
                    self.log_store.add(
                        "info",
                        "Cancellation requested while workflow submission is in flight",
                        "runs.job_service",
                        workflow_id=queued.workflow_id,
                        details={"queue_id": queued.queue_id},
                    )
                    return JobProgress(
                        job_id=queued.queue_id,
                        queue_id=queued.queue_id,
                        status="canceled",
                        message="Workflow run cancellation requested.",
                    )
                if (
                    canceled_before_submission
                    and queued.status is not WorkflowRunQueueStatus.CANCELED
                ):
                    queued = self.workflow_run_queue_service.mark_terminal(
                        queued.queue_id,
                        status=WorkflowRunQueueStatus.CANCELED,
                        reason="canceled_before_submission",
                    )
                if queued is not None and queued.status is WorkflowRunQueueStatus.CANCELED:
                    self.log_store.add(
                        "info",
                        "Canceled queued workflow run",
                        "runs.job_service",
                        workflow_id=queued.workflow_id,
                        details={"queue_id": queued.queue_id},
                    )
                    return JobProgress(
                        job_id=queued.queue_id,
                        queue_id=queued.queue_id,
                        status="canceled",
                        message="Workflow run canceled.",
                    )
        adapter = self._adapter_for_job(resolved.job_id)
        progress = await adapter.cancel_job(resolved.job_id)
        if progress.status in {"completed", "failed", "canceled"}:
            self.mark_job_finished(resolved.job_id)
            if self.workflow_run_queue_service is not None and resolved.queue_id is not None:
                queue_status = None
                if progress.status == "canceled":
                    queue_status = WorkflowRunQueueStatus.CANCELED
                elif progress.status == "failed":
                    queue_status = WorkflowRunQueueStatus.FAILED
                self.workflow_run_queue_service.mark_terminal(
                    resolved.queue_id,
                    status=queue_status,
                    reason=progress.message,
                )
        return progress.model_copy(update={"queue_id": resolved.queue_id})

    async def cancel_workflow_active_and_queued(self, workflow_id: str) -> dict[str, int]:
        summary = {
            "canceled_active_count": 0,
            "canceled_queued_count": 0,
            "already_terminal_count": 0,
            "failed_to_cancel_count": 0,
        }
        seen_job_ids: set[str] = set()

        if self.workflow_run_queue_service is not None:
            for record in self.workflow_run_queue_service.list_records_for_workflow(workflow_id):
                if record.submitted_job_id is not None:
                    seen_job_ids.add(record.submitted_job_id)
                if (
                    self.workflow_run_queue_service.is_terminal(record.queue_id)
                    or record.status in {WorkflowRunQueueStatus.CANCELED, WorkflowRunQueueStatus.FAILED}
                ):
                    summary["already_terminal_count"] += 1
                    continue
                try:
                    progress = await self.cancel_job(record.queue_id)
                except Exception as exc:
                    self.log_store.add(
                        "warning",
                        "Workflow queued run could not be canceled",
                        "runs.job_service",
                        workflow_id=workflow_id,
                        details={"queue_id": record.queue_id, "error": str(exc)},
                    )
                    summary["failed_to_cancel_count"] += 1
                    continue
                latest_record = self.workflow_run_queue_service.get(record.queue_id)
                if latest_record is not None and latest_record.submitted_job_id is not None:
                    seen_job_ids.add(latest_record.submitted_job_id)
                submitted_record = latest_record or record
                if progress.status == "canceled":
                    if (
                        submitted_record.status is WorkflowRunQueueStatus.SUBMITTED
                        or submitted_record.submitted_job_id is not None
                    ):
                        summary["canceled_active_count"] += 1
                    else:
                        summary["canceled_queued_count"] += 1
                elif progress.status in terminal_statuses:
                    summary["already_terminal_count"] += 1
                else:
                    summary["failed_to_cancel_count"] += 1

        for runner in self.runner_supervisor.list_runners():
            job_id = runner.current_job_id
            if (
                not job_id
                or job_id in seen_job_ids
                or runner.current_workflow_id != workflow_id
                or runner.status is not RunnerStatus.RUNNING
            ):
                continue
            try:
                progress = await self.cancel_job(job_id)
            except Exception as exc:
                self.log_store.add(
                    "warning",
                    "Workflow active run could not be canceled",
                    "runs.job_service",
                    job_id=job_id,
                    workflow_id=workflow_id,
                    details={"runner_id": runner.runner_id, "error": str(exc)},
                )
                summary["failed_to_cancel_count"] += 1
                continue
            if progress.status == "canceled":
                summary["canceled_active_count"] += 1
            elif progress.status in terminal_statuses:
                summary["already_terminal_count"] += 1
            else:
                summary["failed_to_cancel_count"] += 1

        self.log_store.add(
            "info",
            "Workflow active and queued runs cancellation requested",
            "runs.job_service",
            workflow_id=workflow_id,
            details=summary,
        )
        return summary

    def workflow_active_and_queued_summary(self, workflow_id: str) -> dict[str, int]:
        active_count = 0
        queued_count = 0
        seen_job_ids: set[str] = set()

        if self.workflow_run_queue_service is not None:
            for record in self.workflow_run_queue_service.list_records_for_workflow(workflow_id):
                if (
                    self.workflow_run_queue_service.is_terminal(record.queue_id)
                    or record.status in {WorkflowRunQueueStatus.CANCELED, WorkflowRunQueueStatus.FAILED}
                ):
                    continue
                if record.submitted_job_id is not None:
                    active_count += 1
                    seen_job_ids.add(record.submitted_job_id)
                else:
                    queued_count += 1

        for runner in self.runner_supervisor.list_runners():
            job_id = runner.current_job_id
            if (
                not job_id
                or job_id in seen_job_ids
                or runner.current_workflow_id != workflow_id
                or runner.status is not RunnerStatus.RUNNING
            ):
                continue
            active_count += 1

        return {
            "active_count": active_count,
            "queued_count": queued_count,
            "total_count": active_count + queued_count,
        }

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        resolved = self._resolve(job_id)
        adapter = self._adapter_for_job(resolved.job_id)
        return await adapter.fetch_output(resolved.job_id, filename, subfolder, output_type)

    async def stream_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
        range_header: str | None = None,
    ) -> EngineOutputStream:
        resolved = self._resolve(job_id)
        try:
            runner = self.runner_supervisor.runner_for_job(resolved.job_id)
        except JobRunnerNotFoundError:
            runner = self.runner_supervisor.core_runner()
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)
        self.runner_supervisor.acquire_output_stream_lease(runner.runner_id)
        try:
            streamed = await adapter.stream_output(
                resolved.job_id,
                filename,
                subfolder,
                output_type,
                range_header,
            )
        except Exception:
            self.runner_supervisor.release_output_stream_lease(runner.runner_id)
            raise

        return EngineOutputStream(
            body=_RunnerLeasedOutputBody(
                streamed.body,
                release=lambda: self.runner_supervisor.release_output_stream_lease(runner.runner_id),
            ),
            media_type=streamed.media_type,
            status_code=streamed.status_code,
            headers=streamed.headers,
        )

    async def upload_workflow_image(
        self,
        workflow_id: str,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> dict[str, str]:
        if self.workflow_loader is None:
            raise KeyError(f"Workflow image uploads are not configured: {workflow_id}")
        package = self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)
        return await adapter.upload_workflow_image(
            package,
            filename,
            data,
            content_type,
        )

    def list_job_logs(
        self,
        job_id: str,
        *,
        level: LogLevel | None = None,
        limit: int = 200,
    ) -> DiagnosticLogResponse:
        resolved = self._resolve(job_id)
        return self.log_store.list_events(job_id=resolved.job_id, level=level, limit=limit)

    def adapter_for_job(self, job_id: str) -> EngineAdapter:
        return self._adapter_for_job(self._resolve(job_id).job_id)

    def mark_job_finished(self, job_id: str) -> None:
        job_id = self._resolve(job_id).job_id
        try:
            runner = self.runner_supervisor.runner_for_job(job_id)
        except JobRunnerNotFoundError:
            return
        self.runner_supervisor.mark_runner_job_finished(runner.runner_id, job_id)

    def _adapter_for_job(self, job_id: str) -> EngineAdapter:
        try:
            return self.runner_supervisor.adapter_for_job(job_id)
        except JobRunnerNotFoundError:
            return self._core_adapter()

    def canonical_job_id(self, handle: str) -> str:
        return self._resolve(handle).job_id

    def queue_id_for(self, handle: str) -> str | None:
        return self._resolve(handle).queue_id

    def _resolve(self, handle: str):
        if self.workflow_run_queue_service is None:
            return _ResolvedJobHandle(job_id=handle)
        return self.workflow_run_queue_service.resolve(handle)

    def _decorate_progress(
        self,
        progress: JobProgress,
        *,
        final_result_ready: bool = False,
    ) -> JobProgress:
        if self.progress_estimator is None:
            return progress
        return self.progress_estimator.decorate(
            progress,
            final_result_ready=final_result_ready,
        )

    def _core_adapter(self) -> EngineAdapter:
        descriptor = self.runner_supervisor.core_runner()
        return self.runner_supervisor.get_adapter(descriptor.runner_id)


class _RunnerLeasedOutputBody:
    def __init__(self, body: AsyncIterator[bytes], *, release: Callable[[], None]) -> None:
        self.body = body
        self.release = release
        self.released = False

    def __aiter__(self) -> "_RunnerLeasedOutputBody":
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.body.__anext__()
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        if self.released:
            return
        self.released = True
        try:
            if hasattr(self.body, "aclose"):
                try:
                    await cast(AsyncGenerator[bytes, None], self.body).aclose()
                except Exception:
                    pass
        finally:
            self.release()


class _ResolvedJobHandle:
    def __init__(self, *, job_id: str, queue_id: str | None = None) -> None:
        self.job_id = job_id
        self.queue_id = queue_id
