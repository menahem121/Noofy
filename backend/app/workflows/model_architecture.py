from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.workflows.package import RequiredModel, WorkflowInput, WorkflowPackage


ARCHITECTURE_SENSITIVE_MODEL_FOLDERS = frozenset(
    {"checkpoints", "loras", "embeddings", "hypernetworks", "diffusion_models"}
)

_BASE_MODEL_FOLDERS = frozenset({"checkpoints", "diffusion_models", "unet"})
_OPTIONLESS_VALUES = frozenset({"", "none"})

_INPUT_FOLDER_HINTS = {
    "ckpt_name": "checkpoints",
    "checkpoint": "checkpoints",
    "checkpoint_name": "checkpoints",
    "lora_name": "loras",
    "embedding_name": "embeddings",
    "embedding": "embeddings",
    "hypernetwork_name": "hypernetworks",
    "hypernetwork": "hypernetworks",
    "unet_name": "diffusion_models",
    "diffusion_model_name": "diffusion_models",
}

_EXPLICIT_FAMILY_ALIASES = {
    "sdxl": "sdxl",
    "stable_diffusion_xl": "sdxl",
    "stable-diffusion-xl": "sdxl",
    "sd_xl": "sdxl",
    "pony": "pony_sdxl",
    "ponyxl": "pony_sdxl",
    "pony_xl": "pony_sdxl",
    "pony_sdxl": "pony_sdxl",
    "sd15": "sd15",
    "sd1": "sd15",
    "sd1_5": "sd15",
    "sd1.5": "sd15",
    "stable_diffusion_1_5": "sd15",
    "stable-diffusion-1-5": "sd15",
    "flux": "flux",
    "flux1": "flux",
    "flux_1": "flux",
    "flux.1": "flux",
    "sd3": "sd3",
    "sd35": "sd3",
    "sd3_5": "sd3",
    "qwen_image": "qwen_image",
    "qwen-image": "qwen_image",
    "wan": "wan",
    "hunyuan": "hunyuan",
    "hunyuan_video": "hunyuan",
}


@dataclass(frozen=True)
class ArchitectureDetection:
    family: str | None
    confidence: str = "unknown"
    source: str | None = None


@dataclass(frozen=True)
class ArchitectureFilterEvent:
    input_id: str | None
    node_id: str
    input_name: str
    category: str
    target_family: str
    hidden_count: int


def filter_workflow_inputs_for_architecture(
    package: WorkflowPackage,
    inputs: list[WorkflowInput],
) -> tuple[list[WorkflowInput], list[ArchitectureFilterEvent]]:
    filtered: list[WorkflowInput] = []
    events: list[ArchitectureFilterEvent] = []
    for workflow_input in inputs:
        result = _filtered_options_for_binding(
            package,
            node_id=workflow_input.binding.node_id,
            input_name=workflow_input.binding.input_name,
            input_id=workflow_input.id,
            raw_options=_string_options(workflow_input.validation.get("options")),
        )
        if result is None:
            filtered.append(workflow_input)
            continue
        options, metadata, event = result
        validation = dict(workflow_input.validation)
        validation["options"] = options
        validation["architecture_filter"] = metadata
        filtered.append(workflow_input.model_copy(update={"validation": validation}))
        if event is not None:
            events.append(event)
    return filtered, events


def filter_bindable_input_nodes_for_architecture(
    package: WorkflowPackage,
    nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[ArchitectureFilterEvent]]:
    filtered_nodes: list[dict[str, Any]] = []
    events: list[ArchitectureFilterEvent] = []
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        raw_inputs = node.get("inputs")
        if not isinstance(raw_inputs, list):
            filtered_nodes.append(node)
            continue
        changed = False
        filtered_inputs: list[dict[str, Any]] = []
        for input_record in raw_inputs:
            if not isinstance(input_record, dict):
                filtered_inputs.append(input_record)
                continue
            input_name = str(input_record.get("input_name") or "")
            result = _filtered_options_for_binding(
                package,
                node_id=node_id,
                input_name=input_name,
                input_id=None,
                raw_options=_string_options(input_record.get("options")),
            )
            if result is None:
                filtered_inputs.append(input_record)
                continue
            options, metadata, event = result
            updated = dict(input_record)
            updated["options"] = options
            updated["architecture_filter"] = metadata
            filtered_inputs.append(updated)
            changed = True
            if event is not None:
                events.append(event)
        if changed:
            updated_node = dict(node)
            updated_node["inputs"] = filtered_inputs
            filtered_nodes.append(updated_node)
        else:
            filtered_nodes.append(node)
    return filtered_nodes, events


def model_architecture_detection(model: RequiredModel | None, *, filename: str | None = None) -> ArchitectureDetection:
    if model is not None:
        explicit = _normalize_family(model.architecture_family)
        if explicit is not None:
            return ArchitectureDetection(
                family=explicit,
                confidence=model.architecture_family_confidence or "high",
                source=model.architecture_family_source or "package_metadata",
            )
        filename = model.filename
    return _filename_architecture_detection(filename)


def _filtered_options_for_binding(
    package: WorkflowPackage,
    *,
    node_id: str,
    input_name: str,
    input_id: str | None,
    raw_options: list[str],
) -> tuple[list[str], dict[str, Any], ArchitectureFilterEvent | None] | None:
    if not raw_options:
        return None
    category = _sensitive_category_for_binding(package, node_id, input_name)
    if category is None:
        return None
    target = _target_architecture_for_binding(package, node_id, input_name, category)
    if target.family is None:
        return None

    visible: list[str] = []
    hidden: list[str] = []
    for option in raw_options:
        option_family = _option_architecture(package, category, option)
        if option_family.family is not None and not _families_are_selector_compatible(target.family, option_family.family):
            hidden.append(option)
            continue
        visible.append(option)

    metadata = {
        "category": category,
        "target_family": target.family,
        "target_confidence": target.confidence,
        "target_source": target.source,
        "hidden_options": hidden,
    }
    event = (
        ArchitectureFilterEvent(
            input_id=input_id,
            node_id=node_id,
            input_name=input_name,
            category=category,
            target_family=target.family,
            hidden_count=len(hidden),
        )
        if hidden
        else None
    )
    return visible, metadata, event


def _target_architecture_for_binding(
    package: WorkflowPackage,
    node_id: str,
    input_name: str,
    category: str,
) -> ArchitectureDetection:
    matching_model = _required_model_for_binding(package, node_id, input_name)
    if category == "loras":
        lora_family = model_architecture_detection(matching_model)
        if lora_family.family is not None:
            return lora_family
        return _upstream_base_architecture(package, node_id)
    direct = model_architecture_detection(matching_model)
    if direct.family is not None:
        return direct
    return _graph_input_architecture(package, node_id, input_name)


def _option_architecture(package: WorkflowPackage, category: str, option: str) -> ArchitectureDetection:
    if option.strip().casefold() in _OPTIONLESS_VALUES:
        return ArchitectureDetection(family=None)
    matching = _required_model_for_option(package, category, option)
    return model_architecture_detection(matching, filename=option)


def _sensitive_category_for_binding(package: WorkflowPackage, node_id: str, input_name: str) -> str | None:
    model = _required_model_for_binding(package, node_id, input_name)
    if model is not None:
        return _sensitive_folder(model.folder)
    return _sensitive_folder(_INPUT_FOLDER_HINTS.get(input_name))


def _sensitive_folder(folder: str | None) -> str | None:
    if folder == "unet":
        return "diffusion_models"
    if folder in ARCHITECTURE_SENSITIVE_MODEL_FOLDERS:
        return folder
    return None


def _required_model_for_binding(package: WorkflowPackage, node_id: str, input_name: str) -> RequiredModel | None:
    for model in package.required_models:
        if model.node_id == node_id and model.input_name == input_name:
            return model
    return None


def _required_model_for_option(package: WorkflowPackage, category: str, option: str) -> RequiredModel | None:
    normalized_option = option.casefold()
    for model in package.required_models:
        if _sensitive_folder(model.folder) != category:
            continue
        if model.filename.casefold() == normalized_option:
            return model
    return None


def _upstream_base_architecture(package: WorkflowPackage, node_id: str) -> ArchitectureDetection:
    graph = package.comfyui_graph
    visited: set[str] = set()
    queue: list[str] = []
    start = graph.get(node_id)
    if isinstance(start, Mapping):
        raw_inputs = start.get("inputs")
        if isinstance(raw_inputs, Mapping):
            for name in ("model", "clip", "unet", "checkpoint"):
                linked = _linked_node_id(raw_inputs.get(name))
                if linked is not None:
                    queue.append(linked)

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        model = _base_model_for_node(package, current)
        detected = model_architecture_detection(model)
        if detected.family is not None:
            return detected
        current_node = graph.get(current)
        if not isinstance(current_node, Mapping):
            continue
        raw_inputs = current_node.get("inputs")
        if not isinstance(raw_inputs, Mapping):
            continue
        for input_name, value in raw_inputs.items():
            graph_detected = _graph_input_architecture(package, current, str(input_name))
            if graph_detected.family is not None:
                return graph_detected
            linked = _linked_node_id(value)
            if linked is not None and linked not in visited:
                queue.append(linked)
    return ArchitectureDetection(family=None)


def _base_model_for_node(package: WorkflowPackage, node_id: str) -> RequiredModel | None:
    for model in package.required_models:
        if model.node_id == node_id and model.folder in _BASE_MODEL_FOLDERS:
            return model
    return None


def _graph_input_architecture(package: WorkflowPackage, node_id: str, input_name: str) -> ArchitectureDetection:
    node = package.comfyui_graph.get(node_id)
    if not isinstance(node, Mapping):
        return ArchitectureDetection(family=None)
    raw_inputs = node.get("inputs")
    if not isinstance(raw_inputs, Mapping):
        return ArchitectureDetection(family=None)
    value = raw_inputs.get(input_name)
    return _filename_architecture_detection(value if isinstance(value, str) else None)


def _linked_node_id(value: Any) -> str | None:
    if not isinstance(value, list) or len(value) < 2:
        return None
    if not isinstance(value[1], int):
        return None
    return str(value[0])


def _filename_architecture_detection(filename: str | None) -> ArchitectureDetection:
    if not isinstance(filename, str) or not filename.strip():
        return ArchitectureDetection(family=None)
    normalized = _normalize_filename(filename)
    tokens = set(normalized.split())
    compact = normalized.replace(" ", "")

    if "pony" in tokens or "ponyxl" in compact or "ponysdxl" in compact:
        return ArchitectureDetection("pony_sdxl", "medium", "filename_hint")
    if "flux" in tokens or compact.startswith("flux") or "fluxdev" in compact or "flux1" in compact:
        return ArchitectureDetection("flux", "medium", "filename_hint")
    if "sdxl" in tokens or "stable diffusion xl" in normalized or _known_sdxl_name(compact):
        return ArchitectureDetection("sdxl", "medium", "filename_hint")
    if "sd15" in compact or "sd1 5" in normalized or "v1 5" in normalized or "stable diffusion 1 5" in normalized:
        return ArchitectureDetection("sd15", "medium", "filename_hint")
    if "sd3" in tokens or "sd35" in compact or "stable diffusion 3" in normalized:
        return ArchitectureDetection("sd3", "medium", "filename_hint")
    if "qwen" in tokens and "image" in tokens:
        return ArchitectureDetection("qwen_image", "medium", "filename_hint")
    if "wan" in tokens or compact.startswith("wan2"):
        return ArchitectureDetection("wan", "medium", "filename_hint")
    if "hunyuan" in tokens:
        return ArchitectureDetection("hunyuan", "medium", "filename_hint")
    return ArchitectureDetection(family=None)


def _known_sdxl_name(compact: str) -> bool:
    return any(
        marker in compact
        for marker in (
            "dreamshaperxl",
            "juggernautxl",
            "realvisxl",
            "animaginexl",
            "illustriousxl",
            "wai-nsfg-xl",
            "waiinswxl",
        )
    )


def _normalize_family(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    key = value.strip().casefold()
    if key in _EXPLICIT_FAMILY_ALIASES:
        return _EXPLICIT_FAMILY_ALIASES[key]
    normalized = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return _EXPLICIT_FAMILY_ALIASES.get(normalized, normalized or None)


def _normalize_filename(value: str) -> str:
    stem = value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem = re.sub(r"\.[a-z0-9]{2,12}$", "", stem.casefold())
    return re.sub(r"[^a-z0-9]+", " ", stem).strip()


def _families_are_selector_compatible(target: str, option: str) -> bool:
    return _selector_family_group(target) == _selector_family_group(option)


def _selector_family_group(family: str) -> str:
    if family == "pony_sdxl":
        return "sdxl"
    return family


def _string_options(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]
