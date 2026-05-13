from __future__ import annotations

from app.diagnostics.store import LogStore
from app.engine.adapter import EngineAdapter
from app.engine.models import DiagnosticLogResponse, JobProgress, LogLevel
from app.runtime.runners.supervisor import JobRunnerNotFoundError, RunnerSupervisor


class RunJobService:
    """Thin job query layer: progress, cancellation, output fetch, and job logs.

    Result handling lives in RunResultService. Memory-governor retry state
    remains injected from EngineService until that state becomes a service.
    """

    def __init__(self, runner_supervisor: RunnerSupervisor, log_store: LogStore) -> None:
        self.runner_supervisor = runner_supervisor
        self.log_store = log_store

    async def get_progress(self, job_id: str) -> JobProgress:
        adapter = self._adapter_for_job(job_id)
        return await adapter.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.log_store.add("info", "Cancel requested", "runs.job_service", job_id=job_id)
        adapter = self._adapter_for_job(job_id)
        return await adapter.cancel_job(job_id)

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        adapter = self._adapter_for_job(job_id)
        return await adapter.fetch_output(job_id, filename, subfolder, output_type)

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

    def _adapter_for_job(self, job_id: str) -> EngineAdapter:
        try:
            return self.runner_supervisor.adapter_for_job(job_id)
        except JobRunnerNotFoundError:
            return self._core_adapter()

    def _core_adapter(self) -> EngineAdapter:
        descriptor = self.runner_supervisor.core_runner()
        return self.runner_supervisor.get_adapter(descriptor.runner_id)
