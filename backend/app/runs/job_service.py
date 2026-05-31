from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import cast

from app.diagnostics import DiagnosticsStore
from app.engine.adapter import EngineAdapter
from app.engine.models import DiagnosticLogResponse, EngineOutputStream, JobProgress, LogLevel
from app.runtime.runners.supervisor import JobRunnerNotFoundError, RunnerSupervisor
from app.workflows.loader import WorkflowPackageLoader


class RunJobService:
    """Thin job query layer: progress, cancellation, output fetch, and job logs.

    Result handling lives in RunResultService. Memory-governor retry state
    remains injected from EngineService until that state becomes a service.
    """

    def __init__(
        self,
        runner_supervisor: RunnerSupervisor,
        log_store: DiagnosticsStore,
        workflow_loader: WorkflowPackageLoader | None = None,
    ) -> None:
        self.runner_supervisor = runner_supervisor
        self.log_store = log_store
        self.workflow_loader = workflow_loader
        self.queued_workflow_run_progress: Callable[[str], JobProgress | None] | None = None
        self.cancel_queued_workflow_run: Callable[[str], JobProgress | None] | None = None

    async def get_progress(self, job_id: str) -> JobProgress:
        if self.queued_workflow_run_progress is not None:
            progress = self.queued_workflow_run_progress(job_id)
            if progress is not None:
                return progress
        adapter = self._adapter_for_job(job_id)
        return await adapter.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.log_store.add("info", "Cancel requested", "runs.job_service", job_id=job_id)
        if self.cancel_queued_workflow_run is not None:
            canceled = self.cancel_queued_workflow_run(job_id)
            if canceled is not None:
                return canceled
        adapter = self._adapter_for_job(job_id)
        progress = await adapter.cancel_job(job_id)
        if progress.status in {"completed", "failed", "canceled"}:
            self.mark_job_finished(job_id)
        return progress

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        adapter = self._adapter_for_job(job_id)
        return await adapter.fetch_output(job_id, filename, subfolder, output_type)

    async def stream_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
        range_header: str | None = None,
    ) -> EngineOutputStream:
        try:
            runner = self.runner_supervisor.runner_for_job(job_id)
        except JobRunnerNotFoundError:
            runner = self.runner_supervisor.core_runner()
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)
        self.runner_supervisor.acquire_output_stream_lease(runner.runner_id)
        try:
            streamed = await adapter.stream_output(
                job_id,
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
        return self.log_store.list_events(job_id=job_id, level=level, limit=limit)

    def adapter_for_job(self, job_id: str) -> EngineAdapter:
        return self._adapter_for_job(job_id)

    def mark_job_finished(self, job_id: str) -> None:
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
