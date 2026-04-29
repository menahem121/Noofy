from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "completed", "failed", "canceled", "missing_models", "unknown"]


class WorkflowRunRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    folder: str
    filename: str
    path: str | None = None


class MissingModel(BaseModel):
    folder: str
    filename: str
    source_url: str | None = None
    checksum: str | None = None


class WorkflowValidationResult(BaseModel):
    workflow_id: str
    valid: bool
    missing_models: list[MissingModel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EngineJob(BaseModel):
    job_id: str
    workflow_id: str
    engine: str
    status: JobStatus


class JobProgress(BaseModel):
    job_id: str
    status: JobStatus
    value: int | None = None
    max: int | None = None
    current_node: str | None = None
    message: str | None = None


class JobResult(BaseModel):
    job_id: str
    status: JobStatus
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class ComfyUIRuntimeStatus(BaseModel):
    reachable: bool
    base_url: str
    repo_dir: str
    managed_process_running: bool = False
    pid: int | None = None
    error: str | None = None


class ProcessActionResult(BaseModel):
    status: str
    comfyui: ComfyUIRuntimeStatus


class WorkflowHealthSummary(BaseModel):
    workflow_id: str
    valid: bool
    missing_model_count: int = 0
    error_count: int = 0


class BackendHealthReport(BaseModel):
    status: str
    comfyui: ComfyUIRuntimeStatus
    workflow_package_count: int
    workflows: list[WorkflowHealthSummary] = Field(default_factory=list)
