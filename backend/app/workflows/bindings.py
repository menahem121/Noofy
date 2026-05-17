from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.workflows.package import WorkflowPackage


def apply_input_bindings(package: WorkflowPackage, inputs: Mapping[str, Any]) -> dict[str, Any]:
    graph = deepcopy(package.comfyui_graph)
    for exposed_input in package.inputs:
        if exposed_input.id not in inputs:
            continue

        node_id = exposed_input.binding.node_id
        input_name = exposed_input.binding.input_name
        if node_id not in graph:
            raise ValueError(f"Input binding references unknown node: {node_id}")

        node_inputs = graph[node_id].setdefault("inputs", {})
        node_inputs[input_name] = inputs[exposed_input.id]
    return graph
