from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.source_policy import SourcePolicy


class WorkflowPackageIdentity(BaseModel):
    publisher_id: str
    package_id: str
    version: str
    trust_level: str = "noofy_verified"
    source: str | None = None
    signature: str | None = None
    signatures: list[WorkflowPackageSignature] = Field(default_factory=list)
    signed_registry_metadata: SignedRegistryMetadata | None = None


class WorkflowPackageSignature(BaseModel):
    key_id: str
    algorithm: str
    value: str


class SignedRegistryMetadata(BaseModel):
    registry_id: str
    snapshot_hash: str
    signature: str
    key_id: str | None = None
    algorithm: str | None = None


class WorkflowMetadata(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    website: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    icon: str = ""


class RequiredModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    folder: str
    filename: str
    node_id: str | None = None
    node_type: str | None = None
    input_name: str | None = None
    source_url: str | None = None
    checksum: str | None = None
    model_type: str | None = None
    size_bytes: int | None = None
    source_urls: list[str] = Field(default_factory=list)
    verification_level: ModelVerificationLevel = ModelVerificationLevel.FILENAME_ONLY
    identity_verified_by_exporter: bool | None = None
    local_file_available_at_export: bool | None = None
    bundled: bool = False
    asset_ownership: AssetOwnership = AssetOwnership.EXTERNAL_REFERENCE
    identity_warnings: list[str] = Field(default_factory=list)


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


DASHBOARD_CONTROL_TYPES = frozenset(
    {
        "slider",
        "int_field",
        "string_field",
        "textarea",
        "toggle",
        "load_image",
        "load_image_mask",
        "display_image",
        "seed_widget",
        "lora_loader",
        "select",
        "result_image",
        "api_credential",
    }
)


class ControlLayout(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 11
    h: int = 4
    min_w: int | None = None
    min_h: int | None = None


class DashboardActionBarPosition(BaseModel):
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)


class DashboardPresentation(BaseModel):
    action_bar: DashboardActionBarPosition | None = None


class CredentialInjectionStrategy(BaseModel):
    kind: Literal["comfyui_extra_data", "runner_env", "config_file", "node_input"]
    field: str | None = None


class DashboardControl(BaseModel):
    id: str
    type: str
    label: str
    input_id: str | None = None
    output_id: str | None = None
    description: str = ""
    layout: ControlLayout | None = None
    visible_if: dict[str, Any] | None = None
    enabled_if: dict[str, Any] | None = None
    provider: str | None = None
    required: bool = False
    secret_ref: str | None = None
    injection_strategy: CredentialInjectionStrategy | None = None


class DashboardControlGroup(BaseModel):
    id: str
    title: str
    description: str = ""
    control_ids: list[str] = Field(default_factory=list)
    layout: ControlLayout | None = None


class DashboardSection(BaseModel):
    id: str
    title: str
    controls: list[DashboardControl] = Field(default_factory=list)
    groups: list[DashboardControlGroup] = Field(default_factory=list)


class DashboardSchema(BaseModel):
    version: str
    status: Literal["configured", "not_configured", "invalid"] = "not_configured"
    presentation: DashboardPresentation | None = None
    inputs: list[WorkflowInput] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)
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
    source_ref: str | None = None
    source_content_hash: str | None = None
    source_cache_ref: str | None = None
    source_archive_subdir: str | None = None
    resolution_method: str | None = None


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
    developer_details: dict[str, Any] = Field(default_factory=dict)


class WorkflowExecutionSmokeFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    prompt: dict[str, Any]
    required_node_types: list[str] = Field(default_factory=list)
    expected_output_node_count: int | None = Field(default=None, ge=0)
    expected_output_node_ids: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=30, gt=0, le=300)


class WorkflowSmokeTests(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_execution: WorkflowExecutionSmokeFixture | None = None


class WorkflowPackage(BaseModel):
    metadata: WorkflowMetadata
    identity: WorkflowPackageIdentity | None = None
    engine: Literal["comfyui"]
    required_models: list[RequiredModel] = Field(default_factory=list)
    comfyui_graph: dict[str, Any]
    inputs: list[WorkflowInput] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)
    dashboard: DashboardSchema = Field(
        default_factory=lambda: DashboardSchema(version="0.1.0", status="not_configured")
    )
    custom_nodes: list[WorkflowCustomNodeRecord] = Field(default_factory=list)
    unresolved_runtime_inputs: list[UnresolvedRuntimeInput] = Field(default_factory=list)
    assets: WorkflowAssetMetadata = Field(default_factory=WorkflowAssetMetadata)
    export_report: dict[str, Any] = Field(default_factory=dict)
    exported_package: dict[str, Any] = Field(default_factory=dict)
    exported_capsule: dict[str, Any] = Field(default_factory=dict)
    observed_hardware: dict[str, Any] = Field(default_factory=dict)
    smoke_tests: WorkflowSmokeTests = Field(default_factory=WorkflowSmokeTests)
    import_metadata: WorkflowImportMetadata | None = None
    source_policy: SourcePolicy | None = None
