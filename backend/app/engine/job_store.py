from app.engine.models import EngineJob, JobProgress, JobResult
from app.diagnostics import sanitize_text


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, EngineJob] = {}
        self._progress: dict[str, JobProgress] = {}
        self._results: dict[str, JobResult] = {}

    def add_job(self, job: EngineJob) -> None:
        self._jobs[job.job_id] = job
        self._progress[job.job_id] = JobProgress(job_id=job.job_id, status=job.status)
        self._results[job.job_id] = JobResult(job_id=job.job_id, status=job.status)

    def get_progress(self, job_id: str) -> JobProgress:
        return self._progress.get(job_id, JobProgress(job_id=job_id, status="unknown"))

    def set_progress(self, progress: JobProgress) -> None:
        self._progress[progress.job_id] = progress.model_copy(
            update={"message": sanitize_text(progress.message) if progress.message else None}
        )

    def get_result(self, job_id: str) -> JobResult:
        return self._results.get(job_id, JobResult(job_id=job_id, status="unknown"))

    def set_result(self, result: JobResult) -> None:
        self._results[result.job_id] = result.model_copy(
            update={"error": sanitize_text(result.error) if result.error else None}
        )
