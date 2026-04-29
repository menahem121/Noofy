from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "completed", "failed", "canceled", "missing_models", "unknown"]
LogLevel = Literal["debug", "info", "warning", "error"]
RuntimeMode = Literal["external", "managed"]


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


class DiagnosticEvent(BaseModel):
    id: int
    timestamp: datetime
    level: LogLevel
    message: str
    source: str
    job_id: str | None = None
    workflow_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class DiagnosticLogResponse(BaseModel):
    events: list[DiagnosticEvent] = Field(default_factory=list)


class RuntimeDependencyStatus(BaseModel):
    name: str
    available: bool
    error: str | None = None


class RuntimeHardwareProfile(BaseModel):
    os_name: str
    os_version: str | None = None
    machine: str
    architecture: str
    accelerator: str
    gpu_names: list[str] = Field(default_factory=list)
    cuda_version: str | None = None
    notes: list[str] = Field(default_factory=list)


class TorchInstallPlan(BaseModel):
    accelerator: str
    packages: list[str] = Field(default_factory=list)
    index_url: str | None = None
    pip_args: list[str] = Field(default_factory=list)
    reason: str
    warnings: list[str] = Field(default_factory=list)


class RuntimeEnvironmentStatus(BaseModel):
    repo_dir: str
    main_py_exists: bool
    requirements_file: str
    requirements_file_exists: bool
    runtime_dir: str
    runtime_dir_writable: bool
    log_dir: str
    cache_dir: str
    python_executable: str
    python_exists: bool
    hardware: RuntimeHardwareProfile
    torch_install_plan: TorchInstallPlan
    dependencies: list[RuntimeDependencyStatus] = Field(default_factory=list)
    prepared: bool = False
    error: str | None = None


class RuntimeBootstrapResult(BaseModel):
    status: str
    environment: RuntimeEnvironmentStatus | None = None


class ComfyUIRuntimeStatus(BaseModel):
    mode: RuntimeMode = "external"
    reachable: bool
    base_url: str
    repo_dir: str
    managed_process_running: bool = False
    pid: int | None = None
    error: str | None = None
    environment: RuntimeEnvironmentStatus | None = None


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
    latest_error: DiagnosticEvent | None = None
