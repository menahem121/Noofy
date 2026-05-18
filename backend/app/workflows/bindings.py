from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.workflows.package import RequiredModel, WorkflowPackage, WorkflowInput

LORA_NONE_OPTION = "None"
_LORA_LOADER_OUTPUT_INPUTS = {
    "LoraLoader": {0: "model", 1: "clip"},
    "LoraLoaderModelOnly": {0: "model"},
}


def package_for_input_bindings(package: WorkflowPackage, inputs: Mapping[str, Any]) -> WorkflowPackage:
    bypassed_lora_nodes = _bypassed_lora_node_ids(package, inputs)
    if not bypassed_lora_nodes:
        return package

    required_models = [
        model
        for model in package.required_models
        if not _is_bypassed_lora_model(model, bypassed_lora_nodes)
    ]
    if len(required_models) == len(package.required_models):
        return package
    return package.model_copy(update={"required_models": required_models})


def apply_input_bindings(package: WorkflowPackage, inputs: Mapping[str, Any]) -> dict[str, Any]:
    graph = deepcopy(package.comfyui_graph)
    bypassed_lora_nodes = _bypassed_lora_node_ids(package, inputs)
    for exposed_input in package.inputs:
        if exposed_input.id not in inputs:
            continue

        node_id = exposed_input.binding.node_id
        input_name = exposed_input.binding.input_name
        if node_id not in graph:
            raise ValueError(f"Input binding references unknown node: {node_id}")

        node_inputs = graph[node_id].setdefault("inputs", {})
        node_inputs[input_name] = inputs[exposed_input.id]
    _bypass_lora_loader_nodes(graph, bypassed_lora_nodes)
    return graph


def _bypassed_lora_node_ids(package: WorkflowPackage, inputs: Mapping[str, Any]) -> set[str]:
    node_ids: set[str] = set()
    for exposed_input in package.inputs:
        if exposed_input.id not in inputs:
            continue
        if not _is_lora_none_value(inputs[exposed_input.id]):
            continue
        if _is_lora_loader_input(package, exposed_input):
            node_ids.add(exposed_input.binding.node_id)
    return node_ids


def _is_lora_loader_input(package: WorkflowPackage, exposed_input: WorkflowInput) -> bool:
    if exposed_input.binding.input_name != "lora_name":
        return False
    node = package.comfyui_graph.get(exposed_input.binding.node_id)
    if not isinstance(node, dict):
        return False
    return node.get("class_type") in _LORA_LOADER_OUTPUT_INPUTS


def _is_lora_none_value(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() == LORA_NONE_OPTION.casefold()


def _is_bypassed_lora_model(model: RequiredModel, bypassed_lora_nodes: set[str]) -> bool:
    if model.node_id not in bypassed_lora_nodes:
        return False
    if model.input_name and model.input_name != "lora_name":
        return False
    model_type = (model.model_type or "").casefold()
    return model.folder == "loras" or model_type == "lora"


def _bypass_lora_loader_nodes(graph: dict[str, Any], node_ids: set[str]) -> None:
    for node_id in node_ids:
        node = graph.get(node_id)
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        output_input_names = _LORA_LOADER_OUTPUT_INPUTS.get(str(class_type))
        if output_input_names is None:
            continue
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict):
            continue

        replacement_by_output = {
            output_index: node_inputs.get(input_name)
            for output_index, input_name in output_input_names.items()
        }
        unsupported_links = False
        for downstream_node_id, downstream_node in graph.items():
            if downstream_node_id == node_id or not isinstance(downstream_node, dict):
                continue
            downstream_inputs = downstream_node.get("inputs")
            if not isinstance(downstream_inputs, dict):
                continue
            for input_name, value in list(downstream_inputs.items()):
                output_index = _linked_output_index(value, node_id)
                if output_index is None:
                    continue
                replacement = replacement_by_output.get(output_index)
                if replacement is None:
                    unsupported_links = True
                    continue
                downstream_inputs[input_name] = replacement
        if not unsupported_links:
            del graph[node_id]


def _linked_output_index(value: Any, node_id: str) -> int | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    if str(value[0]) != node_id:
        return None
    try:
        return int(value[1])
    except (TypeError, ValueError):
        return None
