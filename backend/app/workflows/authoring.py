"""Dashboard authoring service.

Owns the read-modify-write lifecycle for dashboard.json in the workflow store.
Never modifies package.json or comfyui_graph.json.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.workflows.import_normalization import (
    detect_unresolved_runtime_inputs,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.media_values import (
    MEDIA_LOAD_CONTROLS,
    is_gallery_media_reference,
    is_package_asset_value,
    is_uploaded_asset_value,
    media_metadata_matches_input,
    target_media_kind_for_input,
)
from app.workflows.package import (
    DashboardSchema,
    UnresolvedRuntimeInput,
    WorkflowInput,
    WorkflowOutput,
    WorkflowPackage,
)
from app.workflows.package_assets import (
    PackageAssetError,
    copy_package_asset,
    make_package_asset_reference,
    validate_package_asset_reference,
    write_package_asset_metadata,
)
from app.workflows.model_architecture import (
    ArchitectureFilterEvent,
    filter_bindable_input_nodes_for_architecture,
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
        dashboard_assets_dir: Path | None = None,
    ) -> None:
        self.workflow_store_dir = workflow_store_dir
        self.workflow_loader = workflow_loader
        self.validator = validator or WorkflowPackageValidator()
        self.log_store = log_store
        self.object_info_provider = object_info_provider
        self.dashboard_overrides_dir = dashboard_overrides_dir
        self.dashboard_assets_dir = dashboard_assets_dir

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
        nodes, filter_events = filter_bindable_input_nodes_for_architecture(package, nodes)
        self._log_architecture_filter_events(workflow_id, filter_events)
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
        missing_required = _unbound_required_runtime_inputs(candidate, parsed_inputs)
        errors = list(result.errors)
        if missing_required:
            errors.append(_format_missing_required_inputs_error(missing_required))
        return {
            "workflow_id": workflow_id,
            "valid": result.valid and not missing_required,
            "errors": errors,
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

        # A required runtime input (an unbundled creator-local file such as an
        # image or audio clip) must stay bound to a dashboard widget. Removing
        # the auto-created widget would otherwise produce a dashboard that saves
        # but still reports as needing setup, silently bouncing the user back to
        # the builder. Reject it here with a clear, actionable error instead.
        missing_required = _unbound_required_runtime_inputs(candidate, parsed_inputs)
        if missing_required:
            raise DashboardAuthoringError(
                _format_missing_required_inputs_error(missing_required)
            )

        # Determine where to write dashboard.json. Bundled package files stay
        # immutable; their editable dashboard schema lives in a user-owned
        # override directory.
        package_dir, persistence = self._dashboard_write_target(workflow_id, package)
        parsed_inputs = self._package_pinned_uploaded_defaults(
            parsed_inputs,
            package_dir=package_dir,
            workflow_id=workflow_id,
        )

        # Build the on-disk dashboard.json payload.
        schema_configured = parsed_schema.model_copy(update={"status": "configured"})
        on_disk: dict[str, Any] = schema_configured.model_dump(
            mode="json", exclude_none=True
        )
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
            if target.exists():
                shutil.rmtree(target)
                removed = True
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

    def _package_pinned_uploaded_defaults(
        self,
        inputs: list[WorkflowInput],
        *,
        package_dir: Path,
        workflow_id: str,
    ) -> list[WorkflowInput]:
        converted: list[WorkflowInput] = []
        converted_count = 0
        for workflow_input in inputs:
            if is_package_asset_value(workflow_input.default):
                try:
                    reference = validate_package_asset_reference(
                        workflow_input.default,
                        workflow_input=workflow_input,
                    )
                except PackageAssetError as exc:
                    raise DashboardAuthoringError(
                        f"Input '{workflow_input.id}' has an invalid packaged default asset."
                    ) from exc
                converted.append(workflow_input.model_copy(update={"default": reference}))
                continue
            if (
                not workflow_input.default_pinned
                or workflow_input.control not in MEDIA_LOAD_CONTROLS
                or not is_uploaded_asset_value(workflow_input.default)
            ):
                converted.append(workflow_input)
                continue
            if self.dashboard_assets_dir is None:
                raise DashboardAuthoringError("Noofy could not save the uploaded file as a packaged default.")
            source_path = self.dashboard_assets_dir / str(workflow_input.default)
            if not source_path.is_file():
                raise DashboardAuthoringError("The uploaded default file could not be found.")
            metadata = _dashboard_asset_metadata(self.dashboard_assets_dir, str(workflow_input.default))
            kind = str(metadata.get("kind") or target_media_kind_for_input(workflow_input) or "file")
            content_type = metadata.get("content_type") if isinstance(metadata.get("content_type"), str) else None
            original_filename = (
                metadata.get("original_filename")
                if isinstance(metadata.get("original_filename"), str)
                else source_path.name
            )
            extension = source_path.suffix or (metadata.get("extension") if isinstance(metadata.get("extension"), str) else None)
            if not media_metadata_matches_input(
                workflow_input,
                kind=kind,
                extension=extension,
                mime_type=content_type,
            ):
                raise DashboardAuthoringError("The uploaded default file is not compatible with this input.")
            try:
                reference, asset_id = make_package_asset_reference(
                    source_path=source_path,
                    kind=kind,
                    original_filename=original_filename,
                    content_type=content_type,
                )
                copy_package_asset(source_path, package_dir, asset_id)
                write_package_asset_metadata(package_dir, reference)
            except (OSError, PackageAssetError) as exc:
                raise DashboardAuthoringError("Noofy could not save the uploaded file as a packaged default.") from exc
            converted_count += 1
            converted.append(workflow_input.model_copy(update={"default": reference, "default_pinned": True}))
        if converted_count:
            self.log_store.add(
                "info",
                "Converted uploaded dashboard default media to packaged assets",
                "workflow.authoring",
                workflow_id=workflow_id,
                details={"converted_count": converted_count},
            )
        return converted

    def _log_architecture_filter_events(
        self,
        workflow_id: str,
        events: list[ArchitectureFilterEvent],
    ) -> None:
        for event in events:
            self.log_store.add(
                "debug",
                "Filtered bindable model options by architecture family",
                "workflow.authoring",
                workflow_id=workflow_id,
                details={
                    "node_id": event.node_id,
                    "input_name": event.input_name,
                    "category": event.category,
                    "target_family": event.target_family,
                    "hidden_count": event.hidden_count,
                },
            )


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


def _unbound_required_runtime_inputs(
    candidate: WorkflowPackage,
    parsed_inputs: list[WorkflowInput],
) -> list[UnresolvedRuntimeInput]:
    """Return required runtime inputs the saved dashboard does not bind.

    A community workflow that references an unbundled creator-local file (image,
    audio, video, 3D, or generic file) must expose that input as a dashboard
    widget so an end user can supply their own file. The original requirement is
    recomputed from the immutable graph — so an input that was bound by a prior
    save and is later removed is detected again — and unioned with any declared
    runtime inputs that are still unresolved on the loaded package.
    """
    required = _required_runtime_inputs(candidate)
    if not required:
        return []
    input_by_binding = {
        (workflow_input.binding.node_id, workflow_input.binding.input_name): workflow_input
        for workflow_input in parsed_inputs
    }
    visible_input_ids = _dashboard_visible_input_ids(candidate.dashboard)
    missing: list[UnresolvedRuntimeInput] = []
    for runtime_input in required:
        if not runtime_input.required:
            continue
        workflow_input = input_by_binding.get((runtime_input.node_id, runtime_input.input_name))
        if workflow_input is None:
            missing.append(runtime_input)
            continue
        if workflow_input.id in visible_input_ids:
            continue
        if not _hidden_runtime_input_has_usable_default(workflow_input):
            missing.append(runtime_input)
    return missing


def _dashboard_visible_input_ids(dashboard: DashboardSchema) -> set[str]:
    return {
        control.input_id
        for section in dashboard.sections
        for control in section.controls
        if control.input_id
    }


def _hidden_runtime_input_has_usable_default(workflow_input: WorkflowInput) -> bool:
    if workflow_input.control not in MEDIA_LOAD_CONTROLS:
        return workflow_input.default is not None and workflow_input.default != ""
    return (
        is_uploaded_asset_value(workflow_input.default)
        or is_gallery_media_reference(workflow_input.default)
        or is_package_asset_value(workflow_input.default)
    )


def _dashboard_asset_metadata(assets_dir: Path, asset_id: str) -> dict[str, Any]:
    meta_path = assets_dir / f"{asset_id}.meta.json"
    metadata: dict[str, Any] = {"asset_id": asset_id, "original_filename": asset_id}
    if not meta_path.exists():
        return metadata
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return metadata
    if isinstance(raw, dict):
        metadata.update(raw)
    return metadata


def _required_runtime_inputs(
    candidate: WorkflowPackage,
) -> list[UnresolvedRuntimeInput]:
    detected = detect_unresolved_runtime_inputs(candidate.comfyui_graph)
    merged: list[UnresolvedRuntimeInput] = []
    seen: set[tuple[str, str]] = set()
    for runtime_input in (*candidate.unresolved_runtime_inputs, *detected):
        key = (runtime_input.node_id, runtime_input.input_name)
        if key in seen:
            continue
        seen.add(key)
        merged.append(runtime_input)
    return merged


def _format_missing_required_inputs_error(
    missing: list[UnresolvedRuntimeInput],
) -> str:
    descriptions = [
        f"{_friendly_runtime_input_label(item)} "
        f"({item.expected_kind or 'file'} input on node {item.node_id})"
        for item in missing
    ]
    return (
        "Add a widget for the required input(s) below before saving so people "
        "can provide them when they run the workflow: "
        + "; ".join(descriptions)
        + "."
    )


def _friendly_runtime_input_label(item: UnresolvedRuntimeInput) -> str:
    name = (item.input_name or "").replace("_", " ").strip()
    if not name:
        return "Input"
    return name[:1].upper() + name[1:]


# ------------------------------------------------------------------
# Graph input classification heuristics
# ------------------------------------------------------------------

_SCALAR_INPUT_KINDS: dict[type, str] = {
    str: "string",
    int: "number",
    float: "number",
    bool: "boolean",
}

_IMAGE_NODE_TYPES = frozenset({"LoadImage", "LoadImageMask", "NoofyOptionalLoadImage"})
_IMAGE_OUTPUT_NODE_TYPES = frozenset({"PreviewImage", "SaveImage"})
_AUDIO_NODE_TYPES = frozenset({"LoadAudio", "NoofyOptionalLoadAudio"})
_AUDIO_OUTPUT_NODE_TYPES = frozenset({"PreviewAudio", "SaveAudio", "SaveAudioMP3", "SaveAudioOpus"})
_TEXT_OUTPUT_NODE_TYPES = frozenset({"PreviewAny"})
_VIDEO_NODE_TYPES = frozenset({"LoadVideo", "VHS_LoadVideo", "VHS_LoadVideoPath"})
_VIDEO_OUTPUT_NODE_TYPES = frozenset({"PreviewVideo", "SaveVideo", "SaveWEBM", "VHS_VideoCombine"})
_THREE_D_NODE_TYPES = frozenset({"Load3D", "Load3DAnimation"})
_THREE_D_OUTPUT_NODE_TYPES = frozenset({"SaveGLB", "Preview3D", "Preview3DAnimation"})
_FILE_OUTPUT_NODE_TYPES = frozenset({"SaveFile", "SaveText", "SaveJSON", "SaveCSV", "SaveDocument"})
_FILE_INPUT_NAMES = frozenset({"file", "filename", "path", "file_path", "filepath", "json", "csv", "srt", "subtitle", "subtitles", "zip", "npy", "pt"})
_THREE_D_INPUT_NAMES = frozenset({"model", "mesh", "model_file", "file", "filename", "path", "model_path", "mesh_path"})
_THREE_D_EXTENSIONS = frozenset({".glb", ".gltf", ".obj", ".stl", ".fbx", ".ply", ".usdz", ".dae"})
_SEED_INPUT_NAMES = frozenset({"seed", "noise_seed"})
_LORA_NODE_TYPES = frozenset({"LoraLoader", "LoraLoaderModelOnly"})
_NOTE_NODE_TYPES = frozenset({"Note"})


def _classify_graph_inputs(
    graph: dict[str, Any],
    *,
    object_info: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen_note_node_ids: set[str] = set()
    default_image_output_node_id = _default_image_output_node_id(graph)
    default_audio_output_node_id = _default_audio_output_node_id(graph)
    default_video_output_node_id = _default_video_output_node_id(graph)
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        node_id_str = str(node_id)
        node_type = node.get("class_type") or node.get("type") or ""
        raw_inputs = node.get("inputs")
        if node_type in _NOTE_NODE_TYPES:
            nodes.append(_classified_note_node(node_id_str, node))
            seen_note_node_ids.add(node_id_str)
            continue
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
                    "auto_select": node_id_str == default_image_output_node_id,
                }
            )
        if _is_video_output_node_type(node_type):
            scalar_inputs.append(
                {
                    "input_name": "output_video",
                    "current_value": None,
                    "kind": "video_output",
                    "suggested_widget_type": "display_video",
                    "widget_types": ["display_video"],
                    "auto_select": node_id_str == default_video_output_node_id,
                }
            )
        if _is_three_d_output_node_type(node_type):
            scalar_inputs.append(
                {
                    "input_name": "output_3d",
                    "current_value": None,
                    "kind": "three_d_output",
                    "suggested_widget_type": "display_3d",
                    "widget_types": ["display_3d"],
                    "auto_select": False,
                }
            )
        if _is_file_output_node_type(node_type):
            scalar_inputs.append(
                {
                    "input_name": "output_file",
                    "current_value": None,
                    "kind": "file_output",
                    "suggested_widget_type": "display_file",
                    "widget_types": ["display_file"],
                    "auto_select": False,
                }
            )
        if node_type in _AUDIO_OUTPUT_NODE_TYPES:
            scalar_inputs.append(
                {
                    "input_name": "output_audio",
                    "current_value": None,
                    "kind": "audio_output",
                    "suggested_widget_type": "display_audio",
                    "widget_types": ["display_audio"],
                    "auto_select": node_id_str == default_audio_output_node_id,
                }
            )
        if node_type in _TEXT_OUTPUT_NODE_TYPES:
            scalar_inputs.append(
                {
                    "input_name": "output_text",
                    "current_value": None,
                    "kind": "text_output",
                    "suggested_widget_type": "display_text",
                    "widget_types": ["display_text"],
                    "auto_select": True,
                }
            )

        for input_name, value in raw_inputs.items():
            # Skip link references (arrays like ["3", 0]).
            if isinstance(value, list):
                continue
            if _is_ignored_media_node_input(node_type, input_name):
                continue
            option_spec = _options_for_node_input(object_info, node_type, input_name)
            kind = _value_kind(input_name, value, node_type)
            if option_spec.options and kind not in {"image_input", "audio_input", "video_input", "three_d_input", "file_input", "lora"}:
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
                    "node_title": _node_title(node, fallback=str(node_type or "Unknown node")),
                    "is_image_node": node_type in _IMAGE_NODE_TYPES,
                    "is_audio_node": node_type in _AUDIO_NODE_TYPES,
                    "is_video_node": _is_video_input_node_type(node_type),
                    "is_three_d_node": _is_three_d_input_node_type(node_type),
                    "is_lora_node": node_type in _LORA_NODE_TYPES,
                    "inputs": scalar_inputs,
                }
            )

    for node_id, node in _iter_frontend_note_nodes(graph):
        if node_id in seen_note_node_ids:
            continue
        nodes.append(_classified_note_node(node_id, node))
        seen_note_node_ids.add(node_id)

    return nodes


def _classified_note_node(node_id: str, node: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": "Note",
        "node_title": _note_title(node),
        "is_image_node": False,
        "is_lora_node": False,
        "inputs": [
            {
                "input_name": "note",
                "current_value": _note_body(node),
                "kind": "note",
                "suggested_widget_type": "note",
                "widget_types": ["note"],
                "auto_select": True,
            }
        ],
    }


def _iter_frontend_note_nodes(
    payload: Mapping[str, Any],
    *,
    scope: str = "workflow",
) -> Iterator[tuple[str, Mapping[str, Any]]]:
    raw_nodes = payload.get("nodes")
    if isinstance(raw_nodes, list):
        for index, node in enumerate(raw_nodes):
            if not isinstance(node, Mapping) or node.get("type") not in _NOTE_NODE_TYPES:
                continue
            node_id = node.get("id", index)
            yield f"visual:{scope}:node:{node_id}", node

    definitions = payload.get("definitions")
    if not isinstance(definitions, Mapping):
        return
    subgraphs = definitions.get("subgraphs")
    if not isinstance(subgraphs, list):
        return
    for index, subgraph in enumerate(subgraphs):
        if not isinstance(subgraph, Mapping):
            continue
        subgraph_id = subgraph.get("id", index)
        yield from _iter_frontend_note_nodes(
            subgraph,
            scope=f"{scope}/subgraph:{subgraph_id}",
        )


def _note_title(node: Mapping[str, Any]) -> str:
    return _node_title(node, fallback="Note")


def _node_title(node: Mapping[str, Any], *, fallback: str) -> str:
    title = node.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    metadata = node.get("_meta")
    if isinstance(metadata, Mapping):
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return fallback


def _note_body(node: Mapping[str, Any]) -> str:
    inputs = node.get("inputs")
    if isinstance(inputs, Mapping):
        for key in ("text", "note", "body", "content"):
            value = inputs.get(key)
            if isinstance(value, str):
                return value
    widget_values = node.get("widgets_values")
    if isinstance(widget_values, list):
        for value in widget_values:
            if isinstance(value, str):
                return value
    return ""


def _default_image_output_node_id(graph: dict[str, Any]) -> str | None:
    # Use dependency depth as a deterministic approximation of execution order.
    # If multiple output nodes have the same depth, fall back to their original
    # graph order rather than node id, because ComfyUI ids are not chronological.
    node_order: dict[str, int] = {}
    candidate_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    known_node_ids = {str(node_id) for node_id in graph.keys()}

    for index, (node_id, node) in enumerate(graph.items()):
        if not isinstance(node, dict):
            continue
        node_id_str = str(node_id)
        node_order[node_id_str] = index
        if node.get("class_type") in _IMAGE_OUTPUT_NODE_TYPES:
            candidate_ids.add(node_id_str)
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, dict):
            dependencies[node_id_str] = set()
            continue
        dependencies[node_id_str] = {
            str(value[0])
            for value in raw_inputs.values()
            if isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[1], int)
            and str(value[0]) in known_node_ids
        }

    if not candidate_ids:
        return None

    visiting: set[str] = set()
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            return 0
        visiting.add(node_id)
        dep_depths = [depth(dep_id) for dep_id in dependencies.get(node_id, set())]
        visiting.remove(node_id)
        memo[node_id] = 1 + max(dep_depths, default=0)
        return memo[node_id]

    return max(candidate_ids, key=lambda node_id: (depth(node_id), node_order.get(node_id, -1)))


def _default_audio_output_node_id(graph: dict[str, Any]) -> str | None:
    node_order: dict[str, int] = {}
    candidate_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    known_node_ids = {str(node_id) for node_id in graph.keys()}

    for index, (node_id, node) in enumerate(graph.items()):
        if not isinstance(node, dict):
            continue
        node_id_str = str(node_id)
        node_order[node_id_str] = index
        if node.get("class_type") in _AUDIO_OUTPUT_NODE_TYPES:
            candidate_ids.add(node_id_str)
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, dict):
            dependencies[node_id_str] = set()
            continue
        dependencies[node_id_str] = {
            str(value[0])
            for value in raw_inputs.values()
            if isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[1], int)
            and str(value[0]) in known_node_ids
        }

    if not candidate_ids:
        return None

    visiting: set[str] = set()
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            return 0
        visiting.add(node_id)
        dep_depths = [depth(dep_id) for dep_id in dependencies.get(node_id, set())]
        visiting.remove(node_id)
        memo[node_id] = 1 + max(dep_depths, default=0)
        return memo[node_id]

    return max(candidate_ids, key=lambda node_id: (depth(node_id), node_order.get(node_id, -1)))


def _default_video_output_node_id(graph: dict[str, Any]) -> str | None:
    node_order: dict[str, int] = {}
    candidate_ids: set[str] = set()
    dependencies: dict[str, set[str]] = {}
    known_node_ids = {str(node_id) for node_id in graph.keys()}

    for index, (node_id, node) in enumerate(graph.items()):
        if not isinstance(node, dict):
            continue
        node_id_str = str(node_id)
        node_order[node_id_str] = index
        if _is_video_output_node_type(str(node.get("class_type") or "")):
            candidate_ids.add(node_id_str)
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, dict):
            dependencies[node_id_str] = set()
            continue
        dependencies[node_id_str] = {
            str(value[0])
            for value in raw_inputs.values()
            if isinstance(value, list)
            and len(value) >= 2
            and isinstance(value[1], int)
            and str(value[0]) in known_node_ids
        }

    if not candidate_ids:
        return None

    visiting: set[str] = set()
    memo: dict[str, int] = {}

    def depth(node_id: str) -> int:
        if node_id in memo:
            return memo[node_id]
        if node_id in visiting:
            return 0
        visiting.add(node_id)
        dep_depths = [depth(dep_id) for dep_id in dependencies.get(node_id, set())]
        visiting.remove(node_id)
        memo[node_id] = 1 + max(dep_depths, default=0)
        return memo[node_id]

    return max(candidate_ids, key=lambda node_id: (depth(node_id), node_order.get(node_id, -1)))


def _is_ignored_media_node_input(node_type: str, input_name: str) -> bool:
    if node_type in _IMAGE_NODE_TYPES:
        return input_name in {"upload", "enabled", "mode"}
    if node_type in _AUDIO_NODE_TYPES:
        return input_name in {"upload", "audioUI", "enabled", "mode"}
    return False


def _value_kind(input_name: str, value: Any, node_type: str) -> str | None:
    if node_type in _IMAGE_NODE_TYPES and input_name == "image":
        return "image_input"
    if node_type in _AUDIO_NODE_TYPES and input_name in {"audio", "file", "filename", "path", "audio_path"}:
        return "audio_input"
    if _is_video_input_node_type(node_type) and input_name in {"video", "file", "filename", "path", "video_path"}:
        return "video_input"
    if _is_three_d_input(node_type, input_name, value):
        return "three_d_input"
    if _is_file_input(node_type, input_name, value):
        return "file_input"
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
        "audio_input": ["load_audio"],
        "audio_output": ["display_audio"],
        "text_output": ["display_text"],
        "video_input": ["load_video"],
        "video_output": ["display_video"],
        "three_d_input": ["load_3d"],
        "three_d_output": ["display_3d"],
        "file_input": ["load_file"],
        "file_output": ["display_file"],
        "lora": ["lora_loader"],
        "select": ["select", "string_field"],
    }
    return mapping.get(kind, ["string_field"])


def _is_video_input_node_type(node_type: str) -> bool:
    if node_type in _VIDEO_NODE_TYPES:
        return True
    normalized = node_type.lower()
    return "video" in normalized and any(token in normalized for token in ("load", "input", "import"))


def _is_video_output_node_type(node_type: str) -> bool:
    if node_type in _VIDEO_OUTPUT_NODE_TYPES:
        return True
    normalized = node_type.lower()
    return "video" in normalized and any(token in normalized for token in ("preview", "save", "combine", "output", "export"))


def _is_three_d_input_node_type(node_type: str) -> bool:
    normalized = node_type.lower()
    return node_type in _THREE_D_NODE_TYPES or (
        any(token in normalized for token in ("3d", "mesh", "glb", "gltf"))
        and any(token in normalized for token in ("load", "input", "import"))
    )


def _is_three_d_output_node_type(node_type: str) -> bool:
    normalized = node_type.lower()
    return node_type in _THREE_D_OUTPUT_NODE_TYPES or (
        any(token in normalized for token in ("3d", "mesh", "glb"))
        and any(token in normalized for token in ("preview", "save", "output", "export"))
    )


def _is_three_d_input(node_type: str, input_name: str, value: Any) -> bool:
    normalized_input = input_name.lower()
    suffix = Path(value).suffix.lower() if isinstance(value, str) else ""
    return normalized_input in _THREE_D_INPUT_NAMES and (
        _is_three_d_input_node_type(node_type) or suffix in _THREE_D_EXTENSIONS
    )


def _is_file_input(node_type: str, input_name: str, value: Any) -> bool:
    normalized_node = node_type.lower()
    normalized_input = input_name.lower()
    if any(media in normalized_node for media in ("image", "audio", "video", "3d", "mesh", "lora")):
        return False
    if any(model_token in normalized_node for model_token in ("checkpoint", "model", "controlnet", "embedding", "vae", "unet", "clip")):
        return False
    strong_node_signal = any(token in normalized_node for token in ("file", "document", "archive", "json", "csv", "subtitle", "text"))
    strong_input_signal = normalized_input in _FILE_INPUT_NAMES or any(token in normalized_input for token in ("file", "filepath", "file_path", "document", "archive", "subtitle"))
    extension_signal = isinstance(value, str) and _looks_like_generic_file_path(value)
    return strong_input_signal and (strong_node_signal or extension_signal)


def _looks_like_generic_file_path(value: str) -> bool:
    suffix = Path(value).suffix.lower()
    return suffix in {".txt", ".json", ".csv", ".srt", ".pdf", ".zip", ".npy", ".pt", ".yaml", ".yml", ".xml"}


def _is_file_output_node_type(node_type: str) -> bool:
    if node_type in _FILE_OUTPUT_NODE_TYPES:
        return True
    normalized = node_type.lower()
    if any(media in normalized for media in ("image", "audio", "video", "3d", "mesh", "glb")):
        return False
    return any(token in normalized for token in ("file", "document", "archive", "json", "csv", "text", "subtitle")) and any(
        token in normalized for token in ("save", "write", "export", "output")
    )


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
