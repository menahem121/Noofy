from typing import Any, Literal

from pydantic import BaseModel, Field


class WorkflowMetadata(BaseModel):
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""


class RequiredModel(BaseModel):
    folder: str
    filename: str
    source_url: str | None = None
    checksum: str | None = None


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


class WorkflowPackage(BaseModel):
    metadata: WorkflowMetadata
    engine: Literal["comfyui"]
    required_models: list[RequiredModel] = Field(default_factory=list)
    comfyui_graph: dict[str, Any]
    inputs: list[WorkflowInput] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)
    dashboard: DashboardSchema
