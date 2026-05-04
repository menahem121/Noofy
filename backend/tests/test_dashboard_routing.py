"""Tests for dashboard-status-based workflow routing.

Covers all four routing-table cases:
- Missing dashboard.json -> needs_input_setup
- present + status: not_configured -> needs_input_setup
- present + status: configured + validates -> imported
- present + invalid (bad binding) -> needs_input_setup
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from app.engine.diagnostics import LogStore
from app.workflows.importer import ImportedWorkflowPackageStore


# ---------------------------------------------------------------------------
# Archive helpers
# ---------------------------------------------------------------------------


_BASE_GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["4", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1.safetensors"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "out"}},
}

_BASE_PACKAGE = {
    "schema_version": "0.5.0",
    "engine": "comfyui",
    "metadata": {"id": "routing_wf", "name": "Routing Test", "version": "1.0.0"},
    "publisher_id": "test_publisher",
    "package_id": "routing_wf",
    "version": "1.0.0",
    "required_models": [],
    "custom_nodes": [],
}

_BASE_CAPSULE = {
    "schema_version": "0.5.0",
    "capsule_id": "routing_wf",
    "source_policy": "quarantined_community",
    "custom_nodes": [],
    "dependency_lock": {"packages": []},
    "graph_hash": "abc",
    "dependency_env_hash": "def",
    "runner_workspace_hash": "ghi",
}

_CONFIGURED_DASHBOARD = {
    "version": "0.1.0",
    "status": "configured",
    "inputs": [
        {
            "id": "prompt",
            "label": "Prompt",
            "control": "textarea",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": "hello",
            "validation": {},
        }
    ],
    "outputs": [
        {"id": "image_out", "label": "Image", "node_id": "9", "type": "image"}
    ],
    "sections": [
        {
            "id": "main",
            "title": "Controls",
            "controls": [
                {"id": "ctrl_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                {"id": "ctrl_result", "type": "result_image", "label": "Result", "output_id": "image_out"},
            ],
        }
    ],
}

_NOT_CONFIGURED_DASHBOARD = {
    "version": "0.1.0",
    "status": "not_configured",
    "inputs": [],
    "outputs": [],
    "sections": [],
}

_INVALID_DASHBOARD = {
    "version": "0.1.0",
    "status": "configured",
    "inputs": [
        {
            "id": "prompt",
            "label": "Prompt",
            "control": "textarea",
            "binding": {"node_id": "MISSING_NODE", "input_name": "text"},
            "default": "",
            "validation": {},
        }
    ],
    "outputs": [],
    "sections": [
        {
            "id": "main",
            "title": "Controls",
            "controls": [
                {"id": "ctrl_p", "type": "textarea", "label": "P", "input_id": "prompt"},
            ],
        }
    ],
}


def _archive(dashboard: dict[str, Any] | None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("package.json", json.dumps(_BASE_PACKAGE))
        zf.writestr("comfyui_graph.json", json.dumps(_BASE_GRAPH))
        zf.writestr("capsule.lock.json", json.dumps(_BASE_CAPSULE))
        zf.writestr("export-report.json", "{}")
        if dashboard is not None:
            zf.writestr("dashboard.json", json.dumps(dashboard))
    return buf.getvalue()


def _import_result(tmp_path: Path, archive_bytes: bytes) -> dict[str, Any]:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    package = store.import_archive(archive_bytes, original_filename="test.noofy")
    status = package.import_metadata.status if package.import_metadata else "imported"
    return {"status": status, "workflow_id": package.metadata.id}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_empty_dashboard_routes_to_needs_input_setup(tmp_path: Path) -> None:
    """An archive with an empty/stub dashboard.json routes to needs_input_setup."""
    empty_dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "inputs": [],
        "outputs": [],
        "sections": [],
    }
    result = _import_result(tmp_path, _archive(empty_dashboard))
    assert result["status"] == "needs_input_setup"


def test_not_configured_dashboard_routes_to_needs_input_setup(tmp_path: Path) -> None:
    result = _import_result(tmp_path, _archive(_NOT_CONFIGURED_DASHBOARD))
    assert result["status"] == "needs_input_setup"


def test_configured_valid_dashboard_routes_to_imported(tmp_path: Path) -> None:
    result = _import_result(tmp_path, _archive(_CONFIGURED_DASHBOARD))
    assert result["status"] == "imported"


def test_configured_invalid_dashboard_routes_to_needs_input_setup(tmp_path: Path) -> None:
    result = _import_result(tmp_path, _archive(_INVALID_DASHBOARD))
    assert result["status"] == "needs_input_setup"
