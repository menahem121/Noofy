"""Dashboard authoring service.

Owns the read-modify-write lifecycle for dashboard.json in the workflow store.
Never modifies package.json or comfyui_graph.json.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import (
    DashboardSchema,
    WorkflowInput,
    WorkflowOutput,
    WorkflowPackage,
)
from app.workflows.store_paths import assert_path_within, mutable_package_dir, safe_store_segment
from app.workflows.validator import WorkflowPackageValidator


class DashboardAuthoringError(ValueError):
    pass


class DashboardAuthoringService:
    """Write dashboard.json for mutable packages or user-owned dashboard overrides."""

    def __init__(
        self,
        workflow_store_dir: Path,
        workflow_loader: WorkflowPackageLoader,
        *,
        log_store: DiagnosticsSink,
        validator: WorkflowPackageValidator | None = None,
        object_info_provider: Callable[[str], Mapping[str, Any] | None] | None = None,
        dashboard_overrides_dir: Path | None = None,
    ) -> None:
        self.workflow_store_dir = workflow_store_dir
        self.workflow_loader = workflow_loader
        self.validator = validator or WorkflowPackageValidator()
        self.log_store = log_store
        self.object_info_provider = object_info_provider
        self.dashboard_overrides_dir = dashboard_overrides_dir

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_bindable_inputs(
        self,
        workflow_id: str,
        *,
        object_info: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a list of bindable graph inputs derived from the graph and node definitions."""
        package = self._get_package(workflow_id)
        if object_info is None and self.object_info_provider is not None:
            object_info = self.object_info_provider(workflow_id)
        nodes = _classify_graph_inputs(package.comfyui_graph, object_info=object_info)
        return {
            "workflow_id": workflow_id,
            "enrichment": "object_info" if object_info is not None else "heuristic",
            "nodes": nodes,
        }

    def get_unresolved_inputs(self, workflow_id: str) -> dict[str, Any]:
        package = self._get_package(workflow_id)
        return {
            "workflow_id": workflow_id,
            "unresolved_inputs": [
                u.model_dump() for u in package.unresolved_runtime_inputs
            ],
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
        parsed_inputs, parsed_outputs, parsed_schema = _parse_dashboard_payload(
            inputs, dashboard
        )
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
        parsed_inputs, parsed_outputs, parsed_schema = _parse_dashboard_payload(
            inputs, dashboard
        )
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

        # Determine where to write dashboard.json. Bundled package files stay
        # immutable; their editable dashboard schema lives in a user-owned
        # override directory.
        package_dir, persistence = self._dashboard_write_target(workflow_id, package)

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
            details={
                "workflow_id": workflow_id,
                "input_count": len(parsed_inputs),
                "persistence": persistence,
            },
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

    def _dashboard_write_target(
        self,
        workflow_id: str,
        package: WorkflowPackage,
    ) -> tuple[Path, str]:
        candidate = mutable_package_dir(self.workflow_store_dir, package)
        if candidate is not None and candidate.exists():
            return candidate, "package"
        if self.dashboard_overrides_dir is None:
            raise DashboardAuthoringError(
                f"Workflow '{workflow_id}' is not in the mutable workflow store "
                "and no dashboard override store is configured."
            )
        target = self.dashboard_overrides_dir / safe_store_segment(workflow_id)
        assert_path_within(
            self.dashboard_overrides_dir,
            target,
            purpose="write dashboard override",
        )
        target.mkdir(parents=True, exist_ok=True)
        return target, "dashboard_override"

    def reset_dashboard_override(self, workflow_id: str) -> dict[str, Any]:
        removed = False
        if self.dashboard_overrides_dir is not None:
            target = self.dashboard_overrides_dir / safe_store_segment(workflow_id)
            assert_path_within(
                self.dashboard_overrides_dir,
                target,
                purpose="reset dashboard override",
            )
            dashboard_file = target / "dashboard.json"
            if dashboard_file.exists():
                dashboard_file.unlink()
                removed = True
            try:
                target.rmdir()
            except OSError:
                pass
        if not removed:
            self._get_package(workflow_id)
        self.log_store.add(
            "info",
            "Dashboard override reset",
            "workflow.authoring",
            details={"workflow_id": workflow_id, "removed": removed},
        )
        return {
            "workflow_id": workflow_id,
            "removed": removed,
        }


# ------------------------------------------------------------------
# Payload helpers
# ------------------------------------------------------------------


def _parse_dashboard_payload(
    inputs_raw: list[dict[str, Any]],
    dashboard_raw: dict[str, Any],
) -> tuple[list[WorkflowInput], list[WorkflowOutput], DashboardSchema]:
    from pydantic import ValidationError

    dashboard_payload = dict(dashboard_raw)

    inputs: list[WorkflowInput] = []
    for item in inputs_raw:
        try:
            inputs.append(WorkflowInput.model_validate(item))
        except ValidationError as exc:
            raise DashboardAuthoringError(f"Invalid input record: {exc}") from exc

    outputs_raw: list[dict[str, Any]] = dashboard_payload.pop("outputs", []) or []
    outputs: list[WorkflowOutput] = []
    for item in outputs_raw:
        try:
            outputs.append(WorkflowOutput.model_validate(item))
        except ValidationError as exc:
            raise DashboardAuthoringError(f"Invalid output record: {exc}") from exc

    try:
        schema = DashboardSchema.model_validate(dashboard_payload)
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
_IMAGE_OUTPUT_NODE_TYPES = frozenset({"PreviewImage", "SaveImage"})
_SEED_INPUT_NAMES = frozenset({"seed", "noise_seed"})
_LORA_NODE_TYPES = frozenset({"LoraLoader", "LoraLoaderModelOnly"})


def _classify_graph_inputs(
    graph: dict[str, Any],
    *,
    object_info: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        node_type = node.get("class_type", "")
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, dict):
            continue

        scalar_inputs: list[dict[str, Any]] = []
        if node_type in _IMAGE_OUTPUT_NODE_TYPES:
            scalar_inputs.append(
                {
                    "input_name": "output_image",
                    "current_value": None,
                    "kind": "image_output",
                    "suggested_widget_type": "display_image",
                    "widget_types": ["display_image"],
                }
            )

        for input_name, value in raw_inputs.items():
            # Skip link references (arrays like ["3", 0]).
            if isinstance(value, list):
                continue
            if _is_ignored_image_node_input(node_type, input_name):
                continue
            option_spec = _options_for_node_input(object_info, node_type, input_name)
            kind = _value_kind(input_name, value, node_type)
            if option_spec.options and kind not in {"image_input", "lora"}:
                kind = "select"
            if kind is None:
                continue
            widget_types = _widget_types_for_kind(kind)
            input_record = {
                "input_name": input_name,
                "current_value": value,
                "kind": kind,
                "suggested_widget_type": (
                    widget_types[0] if widget_types else "string_field"
                ),
                "widget_types": widget_types,
            }
            if option_spec.options and kind != "image_input":
                input_record["options"] = option_spec.options
            if option_spec.tooltip:
                input_record["hint"] = option_spec.tooltip
            scalar_inputs.append(input_record)

        if scalar_inputs:
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


def _is_ignored_image_node_input(node_type: str, input_name: str) -> bool:
    return node_type in _IMAGE_NODE_TYPES and input_name == "upload"


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
        "select": ["select", "string_field"],
    }
    return mapping.get(kind, ["string_field"])


class _ComfyInputOptionSpec:
    def __init__(self, options: list[str] | None = None, tooltip: str | None = None) -> None:
        self.options = options or []
        self.tooltip = tooltip


def _options_for_node_input(
    object_info: Mapping[str, Any] | None,
    node_type: str,
    input_name: str,
) -> _ComfyInputOptionSpec:
    if object_info is None:
        return _ComfyInputOptionSpec()

    node_info = object_info.get(node_type)
    if not isinstance(node_info, Mapping):
        return _ComfyInputOptionSpec()

    input_groups = node_info.get("input")
    if not isinstance(input_groups, Mapping):
        return _ComfyInputOptionSpec()

    for group_name in ("required", "optional"):
        group = input_groups.get(group_name)
        if not isinstance(group, Mapping) or input_name not in group:
            continue
        return _options_from_input_spec(group[input_name])

    return _ComfyInputOptionSpec()


def _options_from_input_spec(input_spec: Any) -> _ComfyInputOptionSpec:
    if not isinstance(input_spec, (list, tuple)) or not input_spec:
        return _ComfyInputOptionSpec()

    raw_options = input_spec[0]
    if not isinstance(raw_options, (list, tuple)):
        return _ComfyInputOptionSpec(tooltip=_tooltip_from_input_spec(input_spec))

    options = [
        str(option)
        for option in raw_options
        if isinstance(option, (str, int, float, bool))
    ]
    if not options:
        return _ComfyInputOptionSpec(tooltip=_tooltip_from_input_spec(input_spec))

    return _ComfyInputOptionSpec(
        options=_dedupe_preserving_order(options),
        tooltip=_tooltip_from_input_spec(input_spec),
    )


def _tooltip_from_input_spec(input_spec: Any) -> str | None:
    if not isinstance(input_spec, (list, tuple)) or len(input_spec) < 2:
        return None
    metadata = input_spec[1]
    if not isinstance(metadata, Mapping):
        return None
    tooltip = metadata.get("tooltip")
    return tooltip if isinstance(tooltip, str) and tooltip.strip() else None


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


# ------------------------------------------------------------------
# Atomic file write
# ------------------------------------------------------------------


def _promote_import_status(package_file: Path) -> None:
    """Update import_metadata.status in package.json from needs_input_setup to imported."""
    try:
        with package_file.open("r", encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except Exception:
        return
    meta = data.get("import_metadata")
    if not isinstance(meta, dict) or meta.get("status") != "needs_input_setup":
        return
    meta["status"] = "imported"
    meta["user_facing_message"] = "Imported"
    _atomic_write_json(package_file, data)


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
