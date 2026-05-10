from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.runtime.isolation import TrustLevel
from app.runtime.node_registry import (
    NodeRegistryResolutionError,
    NodeRegistryResolutionErrorCode,
    NodeRegistrySource,
    NodeRegistrySourceKind,
)
from app.trust import TrustVerificationResult, workflow_source_policy
from app.workflows.package import (
    DashboardSchema,
    UnresolvedRuntimeInput,
    WorkflowCustomNodeRecord,
    WorkflowImportMetadata,
    WorkflowPackage,
)


def import_status(
    unresolved_inputs: list[UnresolvedRuntimeInput],
    dashboard: DashboardSchema | None = None,
    dashboard_valid: bool = True,
) -> str:
    if unresolved_inputs:
        return "needs_input_setup"
    if dashboard is not None and dashboard.status != "configured":
        return "needs_input_setup"
    if not dashboard_valid:
        return "needs_input_setup"
    return "imported"


def import_status_message(status: str) -> str:
    if status == "needs_input_setup":
        return "Needs input setup"
    if status == "blocked_by_policy":
        return "Needs permission to prepare community workflow"
    if status == "unsupported":
        return "Unsupported workflow"
    if status == "cannot_prepare_automatically":
        return "Cannot prepare automatically"
    return "Imported"


def package_with_import_resolution_status(
    package: WorkflowPackage,
    *,
    status: str,
    message: str,
    source_resolution: dict[str, object],
) -> WorkflowPackage:
    import_metadata = package.import_metadata or WorkflowImportMetadata(
        imported_at=datetime.now(UTC).isoformat(),
    )
    developer_details = dict(import_metadata.developer_details)
    developer_details["source_resolution"] = source_resolution
    updated_identity = package.identity
    if status in {"blocked_by_policy", "unsupported"} and package.identity is not None:
        updated_identity = package.identity.model_copy(
            update={"trust_level": TrustLevel.UNSUPPORTED.value}
        )
    updated = package.model_copy(
        update={
            "identity": updated_identity,
            "import_metadata": import_metadata.model_copy(
                update={
                    "status": status,
                    "user_facing_message": message,
                    "developer_details": developer_details,
                }
            ),
        }
    )
    return package_with_source_policy(
        updated,
        community_preparation_opted_in=(
            package.source_policy.community_preparation_opted_in
            if package.source_policy is not None
            else False
        ),
        policy_status=(
            status if status in {"blocked_by_policy", "unsupported"} else "active"
        ),
    )


def package_with_trust_verification(
    package: WorkflowPackage,
    verification: TrustVerificationResult,
) -> WorkflowPackage:
    import_metadata = package.import_metadata or WorkflowImportMetadata(
        imported_at=datetime.now(UTC).isoformat(),
    )
    developer_details = dict(import_metadata.developer_details)
    developer_details["trust_verification"] = verification.model_dump(mode="json")
    return package.model_copy(
        update={
            "import_metadata": import_metadata.model_copy(
                update={"developer_details": developer_details}
            ),
        }
    )


def package_with_source_policy(
    package: WorkflowPackage,
    *,
    community_preparation_opted_in: bool,
    policy_status: str = "active",
) -> WorkflowPackage:
    return package.model_copy(
        update={
            "source_policy": workflow_source_policy(
                package,
                community_preparation_opted_in=community_preparation_opted_in,
                policy_status=policy_status,
            )
        }
    )


def source_policy_status_for_import(package: WorkflowPackage) -> str:
    status = (
        package.import_metadata.status
        if package.import_metadata is not None
        else "active"
    )
    if status in {"blocked_by_policy", "unsupported"}:
        return status
    return "active"


def non_bundled_required_custom_node_records(
    package: WorkflowPackage,
) -> list[WorkflowCustomNodeRecord]:
    graph_types = graph_node_types(package.comfyui_graph)
    required: list[WorkflowCustomNodeRecord] = []
    for record in package.custom_nodes:
        if record.included:
            continue
        if record.node_types and not any(
            node_type in graph_types for node_type in record.node_types
        ):
            continue
        required.append(record)
    return required


def graph_node_types(graph: dict[str, Any]) -> set[str]:
    node_types: set[str] = set()

    def visit_node(node: Any) -> None:
        if not isinstance(node, dict):
            return
        class_type = node.get("class_type") or node.get("type")
        if isinstance(class_type, str) and is_resolvable_workflow_node_type(
            class_type
        ):
            node_types.add(class_type)
        group_nodes = node.get("nodes") or node.get("groupNodes")
        if isinstance(group_nodes, list):
            for group_node in group_nodes:
                visit_node(group_node)
        elif isinstance(group_nodes, dict):
            for group_node in group_nodes.values():
                visit_node(group_node)

    for node in graph.values():
        visit_node(node)
    return node_types


def is_resolvable_workflow_node_type(node_type: str) -> bool:
    if node_type in {"Reroute", "Note"}:
        return False
    return not (node_type.startswith("workflow/") or node_type.startswith("workflow>"))


def explicit_node_registry_source(
    record: WorkflowCustomNodeRecord,
) -> NodeRegistrySource | None:
    if not record.source.startswith("https://"):
        return None
    if record.source_ref is None:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.MISSING_PINNED_SOURCE_REF,
            "Noofy cannot prepare a workflow extension without a pinned source version.",
            developer_details={"package_id": record.id, "source_url": record.source},
        )
    if record.source_content_hash is None:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.MISSING_SOURCE_CONTENT_HASH,
            "Noofy cannot prepare a workflow extension without a verified source hash.",
            developer_details={"package_id": record.id, "source_url": record.source},
        )
    try:
        return NodeRegistrySource(
            source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
            source_url=record.source,
            source_ref=record.source_ref,
            source_content_hash=record.source_content_hash,
            archive_subdir=record.source_archive_subdir,
        )
    except ValidationError as exc:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.UNPINNED_SOURCE_REF,
            "Noofy cannot prepare a workflow extension without pinned and verified source facts.",
            developer_details={
                "package_id": record.id,
                "source_url": record.source,
                "validation_error": str(exc),
            },
        ) from exc


def trust_level_from_string(value: str) -> TrustLevel:
    if value in {item.value for item in TrustLevel}:
        return TrustLevel(value)
    return TrustLevel.UNSUPPORTED
