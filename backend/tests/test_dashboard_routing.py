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

from app.diagnostics import LogStore
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
    return {
        "status": status,
        "workflow_id": package.metadata.id,
        "package": package,
        "package_dir": store.package_dir(package),
    }


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


def test_legacy_dashboard_missing_status_records_normalization(tmp_path: Path) -> None:
    legacy_dashboard = {
        "version": "0.1.0",
        "inputs": [],
        "outputs": [],
        "sections": [],
    }
    result = _import_result(tmp_path, _archive(legacy_dashboard))

    assert result["status"] == "needs_input_setup"
    report = json.loads((result["package_dir"] / "import-report.json").read_text(encoding="utf-8"))
    assert report["dashboard"]["parse_status"] == "normalized"
    assert "empty_dashboard_marked_not_configured" in report["dashboard"]["normalizations"]
    assert report["dashboard"]["effective_status"] == "not_configured"


def test_configured_valid_dashboard_routes_to_imported(tmp_path: Path) -> None:
    result = _import_result(tmp_path, _archive(_CONFIGURED_DASHBOARD))
    assert result["status"] == "imported"
    report = json.loads((result["package_dir"] / "import-report.json").read_text(encoding="utf-8"))
    assert report["dashboard"]["parse_status"] == "parsed"
    assert report["dashboard"]["validation_status"] == "valid"
    assert report["dashboard"]["effective_status"] == "configured"


def test_configured_valid_dashboard_preserves_inputs_and_outputs(tmp_path: Path) -> None:
    result = _import_result(tmp_path, _archive(_CONFIGURED_DASHBOARD))
    package = result["package"]

    assert [item.id for item in package.inputs] == ["prompt"]
    assert [item.id for item in package.outputs] == ["image_out"]


def test_configured_invalid_dashboard_routes_to_needs_input_setup(tmp_path: Path) -> None:
    result = _import_result(tmp_path, _archive(_INVALID_DASHBOARD))
    assert result["status"] == "needs_input_setup"
    report = json.loads((result["package_dir"] / "import-report.json").read_text(encoding="utf-8"))
    assert report["dashboard"]["parse_status"] == "parsed"
    assert report["dashboard"]["validation_status"] == "invalid"
    assert any("MISSING_NODE" in error for error in report["dashboard"]["validation_errors"])


def test_schema_rejected_dashboard_routes_to_setup_with_diagnostics(tmp_path: Path) -> None:
    malformed_dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [],
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "type": "textarea",
                        "label": "Missing id",
                        "input_id": "prompt",
                    }
                ],
            }
        ],
    }

    result = _import_result(tmp_path, _archive(malformed_dashboard))

    assert result["status"] == "needs_input_setup"
    assert result["package"].dashboard.status == "not_configured"
    report = json.loads((result["package_dir"] / "import-report.json").read_text(encoding="utf-8"))
    assert report["dashboard"]["parse_status"] == "rejected"
    assert report["dashboard"]["source_status"] == "configured"
    assert report["dashboard"]["effective_status"] == "not_configured"
    assert report["dashboard"]["downgraded_to_setup_required"] is True
    assert report["dashboard"]["rejection_reason"]
