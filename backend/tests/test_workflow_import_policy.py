from __future__ import annotations

import pytest

from app.runtime.node_registry import (
    NodeRegistryResolutionError,
    NodeRegistryResolutionErrorCode,
)
from app.workflows.import_policy import (
    explicit_node_registry_source,
    graph_node_types,
    import_status,
)
from app.workflows.package import WorkflowCustomNodeRecord


def test_import_status_requires_input_setup_for_unconfigured_dashboard() -> None:
    assert import_status([], dashboard_valid=False) == "needs_input_setup"


def test_graph_node_types_ignores_builtin_workflow_nodes() -> None:
    assert graph_node_types(
        {
            "1": {"class_type": "KSampler"},
            "2": {"class_type": "Reroute"},
            "3": {"type": "workflow/Input"},
            "4": {"nodes": [{"class_type": "CustomNode"}]},
        }
    ) == {"KSampler", "CustomNode"}


def test_explicit_node_registry_source_requires_pinned_https_source() -> None:
    record = WorkflowCustomNodeRecord(
        id="custom-node",
        folder_name="custom-node",
        source="https://example.test/node.zip",
        node_types=["CustomNode"],
    )

    with pytest.raises(NodeRegistryResolutionError) as exc:
        explicit_node_registry_source(record)

    assert exc.value.code is NodeRegistryResolutionErrorCode.MISSING_PINNED_SOURCE_REF
