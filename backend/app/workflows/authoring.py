"""Dashboard authoring service.

Owns the read-modify-write lifecycle for dashboard.json in the workflow store.
Never modifies package.json or comfyui_graph.json.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.engine.diagnostics import LogStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import (
    DashboardSchema,
    WorkflowInput,
    WorkflowOutput,
    WorkflowPackage,
)
from app.workflows.validator import WorkflowPackageValidator


class DashboardAuthoringError(Exception):
    pass


class DashboardAuthoringService:
    """Write dashboard.json for a workflow package — and only dashboard.json."""

    def __init__(
        self,
        workflow_store_dir: Path,
        workflow_loader: WorkflowPackageLoader,
        validator: WorkflowPackageValidator | None = None,
        log_store: LogStore | None = None,
    ) -> None:
        self.workflow_store_dir = workflow_store_dir
        self.workflow_loader = workflow_loader
        self.validator = validator or WorkflowPackageValidator()
        self.log_store = log_store or LogStore()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_bindable_inputs(self, workflow_id: str) -> dict[str, Any]:
        """Return a list of bindable graph inputs derived heuristically from the graph."""
        package = self._get_package(workflow_id)
        nodes = _classify_graph_inputs(package.comfyui_graph)
        return {
            "workflow_id": workflow_id,
            "enrichment": "heuristic",
            "nodes": nodes,
        }

    def get_unresolved_inputs(self, workflow_id: str) -> dict[str, Any]:
        package = self._get_package(workflow_id)
        return {
            "workflow_id": workflow_id,
            "unresolved_inputs": [u.model_dump() for u in package.unresolved_runtime_inputs],
        }

    # ------------------------------------------------------------------
    # Validate (no persistence)
    # ------------------------------------------------------------------

    def validate_dashboard(
        self,
        workflow_id: str,
        inputs: list[dict[str, Any]],
        dashboard: dict[str, Any],
    ) -> dict[str, Any]:
        package = self._get_package(workflow_id)
        parsed_inputs, parsed_outputs, parsed_schema = _parse_dashboard_payload(inputs, dashboard)
        candidate = package.model_copy(
            update={
                "inputs": parsed_inputs,
                "outputs": parsed_outputs,
                "dashboard": parsed_schema,
            }
        )
        result = self.validator.validate_structure(candidate)
        return {
            "workflow_id": workflow_id,
            "valid": result.valid,
            "errors": result.errors,
            "warnings": result.warnings,
        }

    # ------------------------------------------------------------------
    # Save (writes only dashboard.json)
    # ------------------------------------------------------------------

    def save_dashboard(
        self,
        workflow_id: str,
        inputs: list[dict[str, Any]],
        dashboard: dict[str, Any],
    ) -> dict[str, Any]:
        package = self._get_package(workflow_id)
        parsed_inputs, parsed_outputs, parsed_schema = _parse_dashboard_payload(inputs, dashboard)
        candidate = package.model_copy(
            update={
                "inputs": parsed_inputs,
                "outputs": parsed_outputs,
                "dashboard": parsed_schema,
            }
        )
        result = self.validator.validate_structure(candidate)
        if not result.valid:
            raise DashboardAuthoringError(
                f"Dashboard validation failed: {'; '.join(result.errors)}"
            )

        # Determine where to write dashboard.json.
        package_dir = self._find_package_dir(workflow_id)
        if package_dir is None:
            raise DashboardAuthoringError(
                f"Workflow '{workflow_id}' is not in the mutable workflow store "
                "and cannot be edited. Bundled starter workflows are read-only."
            )

        # Build the on-disk dashboard.json payload.
        schema_configured = parsed_schema.model_copy(update={"status": "configured"})
        on_disk: dict[str, Any] = schema_configured.model_dump(mode="json")
        on_disk["inputs"] = [i.model_dump(mode="json") for i in parsed_inputs]
        on_disk["outputs"] = [o.model_dump(mode="json") for o in parsed_outputs]

        # Atomic write: write to a temp file in the same dir, then rename.
        dashboard_file = package_dir / "dashboard.json"
        _atomic_write_json(dashboard_file, on_disk)

        self.log_store.add(
            "info",
            "Dashboard saved",
            "workflow.authoring",
            details={"workflow_id": workflow_id, "input_count": len(parsed_inputs)},
        )

        return {
            "workflow_id": workflow_id,
            "status": "configured",
            "valid": True,
            "errors": [],
            "warnings": result.warnings,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_package(self, workflow_id: str) -> WorkflowPackage:
        try:
            return self.workflow_loader.get_package(workflow_id)
        except KeyError as exc:
            raise DashboardAuthoringError(f"Unknown workflow: {workflow_id}") from exc

    def _find_package_dir(self, workflow_id: str) -> Path | None:
        """Return the mutable package directory for a workflow, or None if bundled-only."""
        package = self._get_package(workflow_id)
        if package.identity is None:
            return None
        from app.workflows.importer import _safe_store_segment  # local import to avoid circular deps

        candidate = (
            self.workflow_store_dir
            / _safe_store_segment(package.identity.publisher_id)
            / _safe_store_segment(package.identity.package_id)
            / _safe_store_segment(package.identity.version)
        )
        if not candidate.exists():
            return None
        return candidate


# ------------------------------------------------------------------
# Payload helpers
# ------------------------------------------------------------------


def _parse_dashboard_payload(
    inputs_raw: list[dict[str, Any]],
    dashboard_raw: dict[str, Any],
) -> tuple[list[WorkflowInput], list[WorkflowOutput], DashboardSchema]:
    from pydantic import ValidationError

    inputs: list[WorkflowInput] = []
    for item in inputs_raw:
        try:
            inputs.append(WorkflowInput.model_validate(item))
        except ValidationError as exc:
            raise DashboardAuthoringError(f"Invalid input record: {exc}") from exc

    outputs_raw: list[dict[str, Any]] = dashboard_raw.pop("outputs", []) or []
    outputs: list[WorkflowOutput] = []
    for item in outputs_raw:
        try:
            outputs.append(WorkflowOutput.model_validate(item))
        except ValidationError as exc:
            raise DashboardAuthoringError(f"Invalid output record: {exc}") from exc

    try:
        schema = DashboardSchema.model_validate(dashboard_raw)
    except ValidationError as exc:
        raise DashboardAuthoringError(f"Invalid dashboard schema: {exc}") from exc

    return inputs, outputs, schema


# ------------------------------------------------------------------
# Graph input classification heuristics
# ------------------------------------------------------------------

_SCALAR_INPUT_KINDS: dict[type, str] = {
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
}

_IMAGE_NODE_TYPES = frozenset({"LoadImage", "LoadImageMask"})
_SEED_INPUT_NAMES = frozenset({"seed", "noise_seed"})
_LORA_NODE_TYPES = frozenset({"LoraLoader", "LoraLoaderModelOnly"})


def _classify_graph_inputs(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        node_type = node.get("class_type", "")
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, dict):
            continue

        scalar_inputs: list[dict[str, Any]] = []
        for input_name, value in raw_inputs.items():
            # Skip link references (arrays like ["3", 0]).
            if isinstance(value, list):
                continue
            kind = _value_kind(input_name, value, node_type)
            if kind is None:
                continue
            widget_types = _widget_types_for_kind(kind)
            scalar_inputs.append(
                {
                    "input_name": input_name,
                    "current_value": value,
                    "kind": kind,
                    "suggested_widget_type": widget_types[0] if widget_types else "string_field",
                    "widget_types": widget_types,
                }
            )

        if scalar_inputs or node_type in _IMAGE_NODE_TYPES:
            nodes.append(
                {
                    "node_id": str(node_id),
                    "node_type": node_type,
                    "is_image_node": node_type in _IMAGE_NODE_TYPES,
                    "is_lora_node": node_type in _LORA_NODE_TYPES,
                    "inputs": scalar_inputs,
                }
            )

    return nodes


def _value_kind(input_name: str, value: Any, node_type: str) -> str | None:
    if node_type in _IMAGE_NODE_TYPES and input_name == "image":
        return "image_input"
    if node_type in _LORA_NODE_TYPES and input_name in ("lora_name",):
        return "lora"
    if input_name in _SEED_INPUT_NAMES and isinstance(value, (int, float)):
        return "seed"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, (int, float)):
        return "number"
    return None


def _widget_types_for_kind(kind: str) -> list[str]:
    mapping: dict[str, list[str]] = {
        "string": ["textarea", "string_field"],
        "number": ["slider", "int_field"],
        "boolean": ["toggle"],
        "seed": ["seed_widget", "int_field"],
        "image_input": ["load_image", "load_image_mask"],
        "lora": ["lora_loader"],
        "select": ["select"],
    }
    return mapping.get(kind, ["string_field"])


# ------------------------------------------------------------------
# Atomic file write
# ------------------------------------------------------------------


def _atomic_write_json(target: Path, data: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
