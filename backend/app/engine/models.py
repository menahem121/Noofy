from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.artifacts import AssetOwnership, ModelVerificationLevel

JobStatus = Literal[
    "queued",
    "queued_pending_memory",
    "blocked_by_memory",
    "running",
    "completed",
    "failed",
    "canceled",
    "missing_models",
    "unknown",
]
LogLevel = Literal["debug", "info", "warning", "error"]
RuntimeMode = Literal["external", "managed"]


class WorkflowRunRequest(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    output_preferences_snapshot: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    folder: str
    filename: str
    path: str | None = None


class MissingModel(BaseModel):
    folder: str
    filename: str
    source_url: str | None = None
    checksum: str | None = None
    model_type: str | None = None
    verification_level: ModelVerificationLevel | None = None
    size_bytes: int | None = None
    source_urls: list[str] = Field(default_factory=list)


class RequiredModelAvailability(BaseModel):
    requirement_id: str
    node_id: str | None = None
    node_type: str | None = None
    input_name: str | None = None
    filename: str
    model_type: str | None = None
    folder: str
    verification_level: ModelVerificationLevel
    size_bytes: int | None = None
    source_urls: list[str] = Field(default_factory=list)
    source_availability: Literal["known", "resolvable", "unknown"] = "unknown"
    status: Literal[
        "available",
        "possible_match",
        "missing",
        "checking",
        "needs_manual_download",
        "download_failed",
        "authentication_required",
        "access_denied",
        "rate_limited",
        "hash_mismatch",
        "not_enough_disk_space",
        "canceled",
    ]
    status_label: str
    asset_ownership: AssetOwnership
    source_path: str | None = None
    matched_root: str | None = None
    matched_sha256: str | None = None
    matched_size_bytes: int | None = None
    message: str | None = None


class RequiredModelSummary(BaseModel):
    workflow_id: str
    total_count: int
    available_count: int
    possible_match_count: int
    missing_count: int
    needs_manual_download_count: int
    ready_to_run: bool
    models: list[RequiredModelAvailability] = Field(default_factory=list)


class StagedWorkflowImportResponse(BaseModel):
    import_session_id: str | None = None
    workflow_id: str
    status: str
    user_facing_message: str
    workflow: dict[str, object]
    required_model_count: int
    custom_node_count: int
    unresolved_input_count: int
    model_summary: RequiredModelSummary | None = None
    duplicate_identity: dict[str, object] | None = None


class ModelDownloadSummary(BaseModel):
    workflow_id: str
    status: str
    user_facing_message: str
    downloaded_count: int = 0
    failed_count: int = 0
    model_summary: RequiredModelSummary


class ImportModelDownloadJobStart(BaseModel):
    job_id: str
    import_session_id: str
    workflow_id: str
    status: str
    user_facing_message: str


class ImportModelDownloadProgressItem(BaseModel):
    requirement_id: str
    filename: str
    status: Literal[
        "queued",
        "downloading",
        "verifying",
        "completed",
        "failed",
        "canceled",
    ]
    status_label: str
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    message: str | None = None


class ImportModelDownloadJobStatus(BaseModel):
    job_id: str
    import_session_id: str
    workflow_id: str
    status: Literal["queued", "running", "completed", "failed", "canceled"]
    user_facing_message: str
    current_model_filename: str | None = None
    current_model_index: int | None = None
    total_models: int
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    percent: float | None = None
    speed_bytes_per_second: float | None = None
    models: list[ImportModelDownloadProgressItem] = Field(default_factory=list)
    model_summary: RequiredModelSummary | None = None


class ImportModelVerificationJobStatus(BaseModel):
    job_id: str
    import_session_id: str
    workflow_id: str
    status: Literal["queued", "running", "completed", "failed"]
    user_facing_message: str
    current_model_filename: str | None = None
    current_model_index: int | None = None
    total_models: int
    verified_models: int
    percent: float | None = None
    models: list[RequiredModelAvailability] = Field(default_factory=list)
    model_summary: RequiredModelSummary | None = None


class WorkflowValidationResult(BaseModel):
    workflow_id: str
    valid: bool
    missing_models: list[MissingModel] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EngineJob(BaseModel):
    job_id: str
    workflow_id: str
    engine: str
    status: JobStatus
    queue_id: str | None = None
    message: str | None = None
    memory_decision: dict[str, Any] | None = None
    memory_status: dict[str, Any] | None = None


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


class ResourceMetric(BaseModel):
    available: bool = False
    percent: float | None = Field(default=None, ge=0, le=100)
    used_mb: int | None = Field(default=None, ge=0)
    total_mb: int | None = Field(default=None, ge=0)
    free_mb: int | None = Field(default=None, ge=0)
    source: str | None = None
    error: str | None = None


class MachineResourceSnapshot(BaseModel):
    observed_at: str
    cpu: ResourceMetric
    ram: ResourceMetric
    vram: ResourceMetric
    backend: str = "unknown"
    device_name: str | None = None
    memory_pressure: str = "unknown"


class ComfyUIVersionMetadata(BaseModel):
    active_tag: str | None = None
    source_hash: str | None = None
    source_kind: str = "external"
    local_validation_status: str | None = None


class ComfyUIRuntimeStatus(BaseModel):
    mode: RuntimeMode = "external"
    reachable: bool
    base_url: str
    repo_dir: str
    managed_process_running: bool = False
    sidecar_starting: bool = False
    pid: int | None = None
    error: str | None = None
    environment: RuntimeEnvironmentStatus | None = None
    crash_count: int = 0
    restart_attempt: int = 0
    max_restart_attempts: int = 0
    uptime_seconds: float | None = None
    last_crash_at: str | None = None
    version: ComfyUIVersionMetadata | None = None
    managed_vram_mode: str = "normal"
    model_paths: dict[str, str | None] = Field(default_factory=dict)


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
