from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from app.engine.models import (
    EngineJob,
    EngineOutputStream,
    JobProgress,
    JobResult,
    ModelInfo,
)
from app.workflows.package import WorkflowPackage


class EngineMemoryCleanupMode(StrEnum):
    PER_LORA_UNLOAD = "per_lora_unload"
    PER_MODEL_UNLOAD = "per_model_unload"
    RUNNER_FREE = "runner_free"
    ISOLATED_EVICTION = "isolated_eviction"
    CLEANUP_UNSUPPORTED = "cleanup_unsupported"


@dataclass(frozen=True)
class EngineMemoryCleanupCapabilities:
    modes: frozenset[EngineMemoryCleanupMode] = frozenset()
    observed_release_confirmation: bool = True
    notes: tuple[str, ...] = ()

    def supports(self, mode: EngineMemoryCleanupMode) -> bool:
        return mode in self.modes

    def diagnostic_details(self) -> dict[str, Any]:
        return {
            "modes": sorted(mode.value for mode in self.modes),
            "observed_release_confirmation": self.observed_release_confirmation,
            "notes": list(self.notes),
        }


class EngineAdapter(Protocol):
    async def run_workflow(
        self,
        workflow_package: WorkflowPackage,
        graph: dict[str, Any],
        inputs: dict[str, Any],
        options: dict[str, Any],
    ) -> EngineJob:
        """Submit a workflow graph and return the app-owned job record."""

    async def get_progress(
        self,
        job_id: str,
        since_preview_sequence: int | None = None,
    ) -> JobProgress:
        """Return the latest known progress for a job."""

    async def cancel_job(self, job_id: str) -> JobProgress:
        """Cancel a queued or running job."""

    async def get_result(self, job_id: str) -> JobResult:
        """Return the current or final result for a job."""

    async def list_available_models(self) -> list[ModelInfo]:
        """Return models visible to this engine implementation."""

    async def upload_workflow_image(
        self,
        workflow_package: WorkflowPackage,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> dict[str, str]:
        """Stage or upload an image input for a workflow."""

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        """Fetch a generated output file for an app-owned job."""

    async def stream_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
        range_header: str | None = None,
    ) -> EngineOutputStream:
        """Stream generated output media for an app-owned job."""

    async def release_memory(self) -> None:
        """Release idle engine-owned model and allocator memory."""

    def memory_cleanup_capabilities(self) -> EngineMemoryCleanupCapabilities:
        """Return safe cleanup modes exposed by this adapter/runtime."""

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        """Update this adapter's active engine endpoint."""
