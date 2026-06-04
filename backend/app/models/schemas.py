from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.engine.models import ImportModelDownloadProgressItem

MODEL_INVENTORY_SCHEMA_VERSION = "2026-05-13"

ModelInventoryStatus = Literal["ready", "missing", "needs_attention", "never_used"]
ModelInventorySource = Literal[
    "noofy",
    "external_comfyui",
    "engine_visible",
    "required_by_workflow",
]
ModelOwnership = Literal[
    "noofy_downloaded",
    "noofy_imported",
    "noofy_local",
    "external_reference",
    "engine_reference",
    "workflow_requirement",
]


class ModelTag(BaseModel):
    id: str
    name: str
    color: str


class ModelTagCreateRequest(BaseModel):
    name: str
    color: str = "#60a5fa"


class ModelTagAssignmentRequest(BaseModel):
    tag_ids: list[str] = Field(default_factory=list)


class ModelWorkflowReference(BaseModel):
    workflow_id: str
    workflow_name: str
    requirement_id: str
    status: str
    status_label: str


class ModelDownloadReference(BaseModel):
    workflow_id: str
    workflow_name: str
    requirement_id: str


class ModelInventoryEntry(BaseModel):
    model_key: str
    filename: str
    folder: str
    model_type: str
    size_bytes: int | None = None
    status: ModelInventoryStatus
    status_label: str
    source: ModelInventorySource
    source_label: str
    ownership: ModelOwnership
    ownership_label: str
    can_delete: bool = False
    delete_unavailable_reason: str | None = None
    path: str | None = None
    matched_root: str | None = None
    verification_level: str | None = None
    matched_sha256: str | None = None
    source_availability: str | None = None
    message: str | None = None
    workflow_usage: list[ModelWorkflowReference] = Field(default_factory=list)
    downloadable_references: list[ModelDownloadReference] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)


class ModelInventorySummary(BaseModel):
    total_count: int
    noofy_count: int
    external_comfyui_count: int
    missing_count: int
    total_known_size_bytes: int
    disk_free_bytes: int | None = None


class ModelInventoryFolders(BaseModel):
    noofy_models_dir: str
    external_comfyui_models_dir: str | None
    categories: list[str]


class ModelInventoryResponse(BaseModel):
    schema_version: str = MODEL_INVENTORY_SCHEMA_VERSION
    summary: ModelInventorySummary
    folders: ModelInventoryFolders
    tags: list[ModelTag]
    models: list[ModelInventoryEntry]


class ModelImportRequest(BaseModel):
    source_paths: list[str]
    folder: str
    overwrite: bool = False


class ModelImportItemResult(BaseModel):
    source_path: str
    filename: str | None = None
    target_path: str | None = None
    status: Literal["imported", "already_in_place", "failed"]
    message: str | None = None


class ModelImportResponse(BaseModel):
    status: Literal["completed", "completed_with_errors"]
    imported_count: int
    failed_count: int
    models: list[ModelImportItemResult]


class ModelDeleteResponse(BaseModel):
    model_key: str
    deleted: bool
    message: str


class ModelDownloadSelection(BaseModel):
    workflow_id: str
    requirement_id: str


class ModelDownloadStartRequest(BaseModel):
    selections: list[ModelDownloadSelection]


class ModelDownloadJobStart(BaseModel):
    job_id: str
    status: str
    user_facing_message: str


class ModelDownloadJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "completed_with_errors", "failed", "canceled"] | str
    user_facing_message: str
    current_model_filename: str | None = None
    current_model_index: int | None = None
    total_models: int
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    percent: float | None = None
    speed_bytes_per_second: float | None = None
    models: list[ImportModelDownloadProgressItem] = Field(default_factory=list)


class ModelDownloadActiveResponse(BaseModel):
    job: ModelDownloadJobStatus | None = None
