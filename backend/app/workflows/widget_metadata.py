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
        raw_inputs = raw_node.get("inputs")
        if not isinstance(raw_inputs, Mapping):
            continue
        inputs: dict[str, Any] = {}
        for raw_input_name, raw_input in raw_inputs.items():
            if (
                not isinstance(raw_input_name, str)
                or not raw_input_name
                or not isinstance(raw_input, Mapping)
            ):
                continue
            if _is_private_file_picker_binding(graph, str(raw_node_id), raw_input_name):
                continue
            raw_options = raw_input.get("options")
            if not isinstance(raw_options, list):
                continue
            options = _dedupe_preserving_order(
                [
                    str(option)
                    for option in raw_options
                    if isinstance(option, (str, int, float, bool))
                ]
            )
            if not options:
                continue
            record: dict[str, Any] = {"options": options}
            for key in ("display_name", "tooltip"):
                value = raw_input.get(key)
                if isinstance(value, str) and value.strip():
                    record[key] = value.strip()
            inputs[raw_input_name] = record
        if inputs:
            nodes[str(raw_node_id)] = {"inputs": inputs}
    if not nodes:
        return {}
    return {"schema_version": SCHEMA_VERSION, "nodes": nodes}


def comfyui_widget_input_metadata(
    metadata: Mapping[str, Any] | None,
    node_id: str,
    input_name: str,
) -> Mapping[str, Any] | None:
    if not isinstance(metadata, Mapping):
        return None
    nodes = metadata.get("nodes")
    if not isinstance(nodes, Mapping):
        return None
    node = nodes.get(str(node_id))
    if not isinstance(node, Mapping):
        return None
    inputs = node.get("inputs")
    if not isinstance(inputs, Mapping):
        return None
    value = inputs.get(input_name)
    return value if isinstance(value, Mapping) else None


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
) -> bool:
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
        "noofyoptionalloadimage",
        "loadaudio",
        "noofyoptionalloadaudio",
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
