from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkflowPackageIdentity(BaseModel):
    publisher_id: str
    package_id: str
    version: str
    trust_level: str = "noofy_verified"
    source: str | None = None


class WorkflowMetadata(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""


class RequiredModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    folder: str
    filename: str
    source_url: str | None = None
    checksum: str | None = None
    model_type: str | None = None
    size_bytes: int | None = None
    source_urls: list[str] = Field(default_factory=list)


class InputBinding(BaseModel):
    node_id: str
    input_name: str


class WorkflowInput(BaseModel):
    id: str
    label: str
    control: str
    binding: InputBinding
    default: Any = None
    validation: dict[str, Any] = Field(default_factory=dict)


class WorkflowOutput(BaseModel):
    id: str
    label: str
    node_id: str
    type: str


class DashboardControl(BaseModel):
    id: str
    type: str
    label: str
    input_id: str | None = None
    visible_if: dict[str, Any] | None = None
    enabled_if: dict[str, Any] | None = None


class DashboardSection(BaseModel):
    id: str
    title: str
    controls: list[DashboardControl] = Field(default_factory=list)


class DashboardSchema(BaseModel):
    version: str
    sections: list[DashboardSection] = Field(default_factory=list)


class WorkflowCustomNodeRecord(BaseModel):
    id: str
    folder_name: str
    source: str
    included: bool = False
    node_types: list[str] = Field(default_factory=list)
    requirements_files: list[str] = Field(default_factory=list)
    has_install_py: bool = False
    sha256_manifest: str | None = None


class UnresolvedRuntimeInput(BaseModel):
    node_id: str
    node_type: str
    input_name: str
    current_value: Any = None
    reason: str


class WorkflowAssetMetadata(BaseModel):
    thumbnail: str | None = None


class WorkflowImportMetadata(BaseModel):
    original_filename: str | None = None
    imported_at: str | None = None
    source_archive_sha256: str | None = None
    status: str = "imported"
    user_facing_message: str = "Imported"


class WorkflowPackage(BaseModel):
    metadata: WorkflowMetadata
    identity: WorkflowPackageIdentity | None = None
    engine: Literal["comfyui"]
    required_models: list[RequiredModel] = Field(default_factory=list)
    comfyui_graph: dict[str, Any]
    inputs: list[WorkflowInput] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)
    dashboard: DashboardSchema
    custom_nodes: list[WorkflowCustomNodeRecord] = Field(default_factory=list)
    unresolved_runtime_inputs: list[UnresolvedRuntimeInput] = Field(default_factory=list)
    assets: WorkflowAssetMetadata = Field(default_factory=WorkflowAssetMetadata)
    export_report: dict[str, Any] = Field(default_factory=dict)
    exported_package: dict[str, Any] = Field(default_factory=dict)
    exported_capsule: dict[str, Any] = Field(default_factory=dict)
    observed_hardware: dict[str, Any] = Field(default_factory=dict)
    import_metadata: WorkflowImportMetadata | None = None
