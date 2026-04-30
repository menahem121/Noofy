"""Layered runtime fingerprint helpers.

Phase 4 introduces stable identities for dependency environments, runner
workspaces, and resolved workflow capsules. These helpers intentionally only
compute identities; they do not create environments, launch runners, or switch
adapters.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel

from app.runtime.isolation import CapsuleLock, CustomNodeLock, ModelLock, TrustMetadata

FINGERPRINT_SCHEMA_VERSION = "0.1.0"


def canonical_json_bytes(payload: Any) -> bytes:
    """Return deterministic JSON bytes for hashing."""
    return json.dumps(
        _to_jsonable(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def sha256_fingerprint(payload: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def dependency_env_fingerprint(
    *,
    os_name: str,
    architecture: str,
    python_build_id: str,
    torch_backend: str,
    dependency_lock_hash: str,
    native_dependency_constraints: dict[str, Any] | None = None,
    install_policy_version: str,
) -> str:
    return sha256_fingerprint(
        {
            "schema_version": FINGERPRINT_SCHEMA_VERSION,
            "kind": "dependency_env",
            "os": os_name,
            "architecture": architecture,
            "python_build_id": python_build_id,
            "torch_backend": torch_backend,
            "dependency_lock_hash": dependency_lock_hash,
            "native_dependency_constraints": native_dependency_constraints or {},
            "install_policy_version": install_policy_version,
        }
    )


def runner_workspace_fingerprint(
    *,
    dependency_env_fingerprint: str,
    comfyui_source_hash: str,
    enabled_custom_node_manifest_hash: str,
    launch_config_hash: str,
    model_view_hash: str | None = None,
) -> str:
    return sha256_fingerprint(
        {
            "schema_version": FINGERPRINT_SCHEMA_VERSION,
            "kind": "runner_workspace",
            "dependency_env_fingerprint": dependency_env_fingerprint,
            "comfyui_source_hash": comfyui_source_hash,
            "enabled_custom_node_manifest_hash": enabled_custom_node_manifest_hash,
            "launch_config_hash": launch_config_hash,
            "model_view_hash": model_view_hash,
        }
    )


def capsule_fingerprint(
    *,
    workflow_package_hash: str,
    graph_hash: str,
    dashboard_schema_hash: str,
    model_requirements: list[ModelLock],
    custom_nodes: list[CustomNodeLock],
    trust: TrustMetadata,
    runner_fingerprint: str,
) -> str:
    return sha256_fingerprint(
        {
            "schema_version": FINGERPRINT_SCHEMA_VERSION,
            "kind": "capsule",
            "workflow_package_hash": workflow_package_hash,
            "graph_hash": graph_hash,
            "dashboard_schema_hash": dashboard_schema_hash,
            "model_requirements": _sorted_models(model_requirements),
            "custom_nodes": _sorted_custom_nodes(custom_nodes),
            "trust": trust,
            "runner_fingerprint": runner_fingerprint,
        }
    )


def capsule_lock_content_hash(capsule_lock: CapsuleLock) -> str:
    """Hash a full immutable capsule lock as stored on disk."""
    return sha256_fingerprint(
        {
            "schema_version": FINGERPRINT_SCHEMA_VERSION,
            "kind": "capsule_lock",
            "capsule_lock": capsule_lock,
        }
    )


def _sorted_models(models: list[ModelLock]) -> list[dict[str, Any]]:
    return sorted(
        (_to_jsonable(model) for model in models),
        key=lambda item: (item["id"], item["sha256"], item["comfyui_folder"], item["filename"]),
    )


def _sorted_custom_nodes(nodes: list[CustomNodeLock]) -> list[dict[str, Any]]:
    return sorted(
        (_to_jsonable(node) for node in nodes),
        key=lambda item: (item["package_id"], item.get("commit") or "", item.get("version") or ""),
    )


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    return value
