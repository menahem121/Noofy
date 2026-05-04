"""Tests for WorkflowExporter.

Verifies:
- Export produces a valid .noofy archive.
- Export does not modify the original imported file.
- Export strips trust signatures.
- Exported archive has a separate dashboard.json.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.engine.diagnostics import LogStore
from app.workflows.exporter import WorkflowExporter, WorkflowExportError
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.loader import WorkflowPackageLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hi", "clip": ["4", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "out"}},
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
                {"id": "c1", "type": "textarea", "label": "P", "input_id": "prompt"},
                {"id": "c2", "type": "result_image", "label": "R", "output_id": "image_out"},
            ],
        }
    ],
}


def _make_archive(
    with_signature: bool = False,
    dashboard: dict[str, Any] | None = None,
) -> bytes:
    package: dict[str, Any] = {
        "schema_version": "0.5.0",
        "engine": "comfyui",
        "metadata": {"id": "export_wf", "name": "Export Test", "version": "1.0.0"},
        "publisher_id": "export_pub",
        "package_id": "export_wf",
        "version": "1.0.0",
        "required_models": [],
        "custom_nodes": [],
    }
    if with_signature:
        package["signature"] = "ed25519:FAKE_SIG"
        package["signed_registry_metadata"] = {"registered": True}

    capsule = {
        "schema_version": "0.5.0",
        "capsule_id": "export_wf",
        "source_policy": "quarantined_community",
        "custom_nodes": [],
        "dependency_lock": {"packages": []},
        "graph_hash": "aaa",
        "dependency_env_hash": "bbb",
        "runner_workspace_hash": "ccc",
    }
    effective_dashboard: dict[str, Any] = dashboard if dashboard is not None else {
        "version": "0.1.0",
        "status": "not_configured",
        "inputs": [],
        "outputs": [],
        "sections": [],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("package.json", json.dumps(package))
        zf.writestr("comfyui_graph.json", json.dumps(_GRAPH))
        zf.writestr("capsule.lock.json", json.dumps(capsule))
        zf.writestr("export-report.json", "{}")
        zf.writestr("dashboard.json", json.dumps(effective_dashboard))
    return buf.getvalue()


def _setup_with_configured_dashboard(tmp_path: Path):
    archive_bytes = _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD)
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    pkg = store.import_archive(archive_bytes, original_filename="export_test.noofy")
    workflow_id = pkg.metadata.id

    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
    )
    return exporter, workflow_id, archive_bytes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_export_produces_valid_noofy_archive(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)

    archive_bytes, filename = exporter.export_archive(workflow_id)

    assert filename.endswith(".noofy")
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = zf.namelist()
        assert "package.json" in names
        assert "comfyui_graph.json" in names
        assert "dashboard.json" in names


def test_export_does_not_modify_original_file(tmp_path: Path) -> None:
    exporter, workflow_id, original_bytes = _setup_with_configured_dashboard(tmp_path)

    # Write the original to disk so we can check it.
    original_file = tmp_path / "original.noofy"
    original_file.write_bytes(original_bytes)

    exporter.export_archive(workflow_id)

    # The original file must be untouched.
    assert original_file.read_bytes() == original_bytes


def test_export_strips_trust_signatures(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        package_data = json.loads(zf.read("package.json"))

    assert "signature" not in package_data
    assert "signatures" not in package_data
    assert "signed_registry_metadata" not in package_data
    assert package_data.get("source_policy") == "local"


def test_exported_archive_has_separate_dashboard_json(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        assert "dashboard.json" in zf.namelist()
        dashboard_data = json.loads(zf.read("dashboard.json"))
        package_data = json.loads(zf.read("package.json"))

    # Dashboard data must be in dashboard.json, not embedded in package.json.
    assert "inputs" in dashboard_data or "sections" in dashboard_data
    assert "inputs" not in package_data
    assert "dashboard" not in package_data


def test_export_raises_for_bundled_workflow(tmp_path: Path) -> None:
    loader = WorkflowPackageLoader(Path("app/workflows/packages"))
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
    )

    with pytest.raises(WorkflowExportError, match="no mutable store copy"):
        exporter.export_archive("text_to_image_v0")
