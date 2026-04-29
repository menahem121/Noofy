from typing import Any, Protocol

from app.engine.models import EngineJob, JobProgress, JobResult, ModelInfo
from app.workflows.package import WorkflowPackage


class EngineAdapter(Protocol):
    async def run_workflow(
        self,
        workflow_package: WorkflowPackage,
        graph: dict[str, Any],
        inputs: dict[str, Any],
        options: dict[str, Any],
    ) -> EngineJob:
        """Submit a workflow graph and return the app-owned job record."""

    async def get_progress(self, job_id: str) -> JobProgress:
        """Return the latest known progress for a job."""

    async def cancel_job(self, job_id: str) -> JobProgress:
        """Cancel a queued or running job."""

    async def get_result(self, job_id: str) -> JobResult:
        """Return the current or final result for a job."""

    async def list_available_models(self) -> list[ModelInfo]:
        """Return models visible to this engine implementation."""
