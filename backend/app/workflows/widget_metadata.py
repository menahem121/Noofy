from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "0.1.0"


def normalize_comfyui_widget_metadata(
    raw: Any,
    *,
    graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    raw_nodes = raw.get("nodes")
    if not isinstance(raw_nodes, Mapping):
        return {}

    nodes: dict[str, Any] = {}
    for raw_node_id, raw_node in raw_nodes.items():
        if not isinstance(raw_node, Mapping):
            continue
        node_record: dict[str, Any] = {}
        raw_outputs = raw_node.get("outputs")
        if isinstance(raw_outputs, (list, tuple)):
            outputs = [
                value.strip()
                for value in raw_outputs
                if isinstance(value, str) and value.strip()
            ]
            if outputs:
                node_record["outputs"] = outputs
        raw_inputs = raw_node.get("inputs")
        inputs: dict[str, Any] = {}
        for raw_input_name, raw_input in (
            raw_inputs.items() if isinstance(raw_inputs, Mapping) else ()
        ):
            if (
                not isinstance(raw_input_name, str)
                or not raw_input_name
                or not isinstance(raw_input, Mapping)
            ):
                continue
            private_file_picker = _is_private_file_picker_binding(
                graph,
                str(raw_node_id),
                raw_input_name,
                raw_input,
            )
            raw_options = raw_input.get("options")
            options = (
                _dedupe_preserving_order(
                    [
                        str(option)
                        for option in raw_options
                        if isinstance(option, (str, int, float, bool))
                    ]
                )
                if isinstance(raw_options, list) and not private_file_picker
                else []
            )
            record: dict[str, Any] = {}
            if options:
                record["options"] = options
            for key in ("display_name", "tooltip", "input_type"):
                value = raw_input.get(key)
                if isinstance(value, str) and value.strip():
                    record[key] = value.strip()
            input_group = raw_input.get("input_group")
            if input_group in {"required", "optional", "hidden"}:
                record["input_group"] = input_group
            for key in ("image_upload", "audio_upload", "video_upload", "file_upload"):
                if raw_input.get(key) is True:
                    record[key] = True
            if not record:
                continue
            inputs[raw_input_name] = record
        if inputs:
            node_record["inputs"] = inputs
        if node_record:
            nodes[str(raw_node_id)] = node_record
    if not nodes:
        return {}
    return {"schema_version": SCHEMA_VERSION, "nodes": nodes}


def comfyui_widget_metadata_from_object_info(
    graph: Mapping[str, Any],
    object_info: Mapping[str, Any],
) -> dict[str, Any]:
    raw_nodes: dict[str, Any] = {}
    for raw_node_id, graph_node in graph.items():
        if not isinstance(graph_node, Mapping):
            continue
        node_type = graph_node.get("class_type") or graph_node.get("type")
        node_info = object_info.get(node_type) if isinstance(node_type, str) else None
        if not isinstance(node_info, Mapping):
            continue
        graph_inputs = graph_node.get("inputs")
        input_groups = node_info.get("input")
        if not isinstance(graph_inputs, Mapping):
            continue
        if not isinstance(input_groups, Mapping):
            input_groups = {}
        node_record: dict[str, Any] = {}
        outputs = node_info.get("output")
        if isinstance(outputs, (list, tuple)):
            portable_outputs = [
                value.strip()
                for value in outputs
                if isinstance(value, str) and value.strip()
            ]
            if portable_outputs:
                node_record["outputs"] = portable_outputs
        inputs: dict[str, Any] = {}
        for input_name, value in graph_inputs.items():
            if not isinstance(input_name, str) or _is_graph_link(value):
                continue
            input_group, input_spec = _object_info_input_spec(input_groups, input_name)
            record = _portable_input_spec_metadata(
                input_spec,
                input_group=input_group,
            )
            if record:
                inputs[input_name] = record
        if inputs:
            node_record["inputs"] = inputs
        if node_record:
            raw_nodes[str(raw_node_id)] = node_record
    return normalize_comfyui_widget_metadata(
        {"schema_version": SCHEMA_VERSION, "nodes": raw_nodes},
        graph=graph,
    )


def merge_comfyui_widget_metadata(
    existing: Mapping[str, Any] | None,
    discovered: Mapping[str, Any] | None,
    *,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    return normalize_comfyui_widget_metadata(
        _merge_metadata_mappings(existing, discovered),
        graph=graph,
    )


def _merge_metadata_mappings(*records: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        for key, value in record.items():
            previous = merged.get(key)
            merged[key] = (
                _merge_metadata_mappings(previous, value)
                if isinstance(value, Mapping)
                else value
            )
    return merged


def comfyui_widget_input_metadata(
    metadata: Mapping[str, Any] | None,
    node_id: str,
    input_name: str,
) -> Mapping[str, Any] | None:
    node = _widget_node_metadata(metadata, node_id)
    if node is None:
        return None
    inputs = node.get("inputs")
    if not isinstance(inputs, Mapping):
        return None
    value = inputs.get(input_name)
    return value if isinstance(value, Mapping) else None


def comfyui_widget_output_type(
    metadata: Mapping[str, Any] | None,
    node_id: str,
    output_index: int,
) -> str | None:
    node = _widget_node_metadata(metadata, node_id)
    if node is None or output_index < 0:
        return None
    outputs = node.get("outputs")
    if not isinstance(outputs, (list, tuple)) or output_index >= len(outputs):
        return None
    value = outputs[output_index]
    return value if isinstance(value, str) and value else None


def _widget_node_metadata(
    metadata: Mapping[str, Any] | None,
    node_id: str,
) -> Mapping[str, Any] | None:
    nodes = metadata.get("nodes") if isinstance(metadata, Mapping) else None
    node = nodes.get(str(node_id)) if isinstance(nodes, Mapping) else None
    return node if isinstance(node, Mapping) else None


def _object_info_input_spec(
    input_groups: Mapping[str, Any],
    input_name: str,
) -> tuple[str | None, Any]:
    for group_name in ("required", "optional", "hidden"):
        group = input_groups.get(group_name)
        if isinstance(group, Mapping) and input_name in group:
            return group_name, group[input_name]
    return None, None


def _portable_input_spec_metadata(
    input_spec: Any,
    *,
    input_group: str | None = None,
) -> dict[str, Any]:
    if not isinstance(input_spec, (list, tuple)) or not input_spec:
        return {}
    raw_type = input_spec[0]
    options = (
        input_spec[1]
        if len(input_spec) > 1 and isinstance(input_spec[1], Mapping)
        else {}
    )
    record: dict[str, Any] = {}
    if input_group in {"required", "optional", "hidden"}:
        record["input_group"] = input_group
    if isinstance(raw_type, str) and raw_type.strip():
        record["input_type"] = raw_type.strip().upper()
    elif isinstance(raw_type, (list, tuple)):
        record["input_type"] = "COMBO"
        record["options"] = list(raw_type)
    raw_options = options.get("options")
    if isinstance(raw_options, (list, tuple)):
        record["options"] = list(raw_options)
    for key in ("display_name", "tooltip"):
        value = options.get(key)
        if isinstance(value, str) and value.strip():
            record[key] = value.strip()
    for key in ("image_upload", "audio_upload", "video_upload", "file_upload"):
        if options.get(key) is True:
            record[key] = True
    return record


def _is_graph_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def _dedupe_preserving_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _is_private_file_picker_binding(
    graph: Mapping[str, Any] | None,
    node_id: str,
    input_name: str,
    input_metadata: Mapping[str, Any] | None = None,
) -> bool:
    if isinstance(input_metadata, Mapping) and any(
        input_metadata.get(flag) is True
        for flag in ("image_upload", "audio_upload", "video_upload", "file_upload")
    ):
        return True
    if not isinstance(graph, Mapping):
        return False
    node = graph.get(node_id)
    if not isinstance(node, Mapping):
        return False
    node_type = str(node.get("class_type") or node.get("type") or "").lower()
    normalized_input = input_name.lower()
    known_media_loaders = {
        "loadimage",
        "loadimagemask",
        "loadaudio",
        "loadvideo",
        "vhs_loadvideo",
        "vhs_loadvideopath",
        "load3d",
        "load3danimation",
    }
    media_loader = node_type in known_media_loaders or (
        any(token in node_type for token in ("image", "audio", "video", "3d", "mesh"))
        and any(token in node_type for token in ("load", "input", "import", "open"))
    )
    if media_loader:
        return normalized_input in {
            "image",
            "audio",
            "video",
            "model",
            "mesh",
            "model_file",
            "file",
            "filename",
            "path",
            "file_path",
            "filepath",
        }
    if any(token in node_type for token in ("checkpoint", "lora", "model", "controlnet", "vae", "unet", "clip")):
        return False
    return normalized_input in {
        "file",
        "filename",
        "path",
        "file_path",
        "filepath",
        "document",
        "archive",
        "subtitle",
    }
