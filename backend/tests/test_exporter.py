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

from app.diagnostics import LogStore
from app.workflows.exporter import WorkflowExporter, stored_comfyui_graph_file
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.user_state import (
    OutputPreference,
    UserStateLayoutOverride,
    UserStateService,
    WorkflowUserState,
)


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


def _setup_with_configured_dashboard(
    tmp_path: Path,
    *,
    user_state_service: UserStateService | None = None,
):
    archive_bytes = _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD)
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    pkg = store.import_archive(archive_bytes, original_filename="export_test.noofy")
    workflow_id = pkg.metadata.id
    (store.package_dir(pkg) / "dashboard.json").write_text(
        json.dumps(_CONFIGURED_DASHBOARD),
        encoding="utf-8",
    )

    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        user_state_service=user_state_service,
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


def test_exported_archive_bakes_current_user_dashboard_preferences(tmp_path: Path) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
    )
    user_state_service.save(
        WorkflowUserState(
            workflow_id=workflow_id,
            dashboard_version="0.1.0",
            values={"prompt": "latest prompt"},
            layout_overrides={"c1": UserStateLayoutOverride(x=2, y=3, w=10, h=5)},
            output_preferences={"c2": OutputPreference(auto_save=True)},
        )
    )

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        dashboard_data = json.loads(zf.read("dashboard.json"))
        graph_data = json.loads(zf.read("comfyui_graph.json"))

    assert dashboard_data["inputs"][0]["default"] == "latest prompt"
    assert graph_data["1"]["inputs"]["text"] == "latest prompt"
    first_control = dashboard_data["sections"][0]["controls"][0]
    assert first_control["layout"] == {"x": 2, "y": 3, "w": 10, "h": 5}
    second_control = dashboard_data["sections"][0]["controls"][1]
    assert second_control["show_download"] is True


def test_exported_archive_applies_explicit_dashboard_values_without_mutating_store(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    graph_file = stored_comfyui_graph_file(package_dir)
    before = json.loads(graph_file.read_text(encoding="utf-8"))

    archive_bytes, _ = exporter.export_archive(
        workflow_id,
        input_values={"prompt": "visible dashboard prompt"},
    )

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        graph_data = json.loads(zf.read("comfyui_graph.json"))
        dashboard_data = json.loads(zf.read("dashboard.json"))

    assert graph_data["1"]["inputs"]["text"] == "visible dashboard prompt"
    assert dashboard_data["inputs"][0]["default"] == "visible dashboard prompt"
    assert json.loads(graph_file.read_text(encoding="utf-8")) == before


def test_comfyui_json_export_applies_explicit_dashboard_values(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)

    graph_bytes, filename = exporter.export_comfyui_graph(
        workflow_id,
        input_values={"prompt": "json export prompt"},
    )

    assert filename.endswith(".comfyui.json")
    assert json.loads(graph_bytes)["1"]["inputs"]["text"] == "json export prompt"


def test_exported_archive_strips_api_credential_status_and_raw_values(tmp_path: Path) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "comfy_account_key",
                "label": "ComfyUI Account API Key",
                "control": "api_credential",
                "binding": {"node_id": "1", "input_name": "text"},
                "default": None,
                "validation": {},
            }
        ],
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "id": "comfy_account_key",
                        "type": "api_credential",
                        "label": "ComfyUI Account API Key",
                        "input_id": "comfy_account_key",
                        "provider": "comfy_org",
                        "required": True,
                        "secret_ref": "api-key:comfy_org",
                        "configured": True,
                        "last_four": "1234",
                        "value": "raw-secret-should-not-export",
                        "injection_strategy": {
                            "kind": "comfyui_extra_data",
                            "field": "api_key_comfy_org",
                        },
                    }
                ],
            }
        ],
    }
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
    )
    (exporter._find_package_dir(workflow_id) / "dashboard.json").write_text(
        json.dumps(dashboard),
        encoding="utf-8",
    )
    user_state_service.save(
        WorkflowUserState(
            workflow_id=workflow_id,
            values={
                "comfy_account_key": {
                    "kind": "api_key_ref",
                    "provider": "comfy_org",
                    "secret_ref": "api-key:comfy_org",
                    "configured": True,
                    "last_four": "9999",
                    "raw": "raw-secret-should-not-export",
                }
            },
        )
    )

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        for name in zf.namelist():
            assert "raw-secret-should-not-export" not in zf.read(name).decode(
                "utf-8",
                errors="ignore",
            )
        dashboard_data = json.loads(zf.read("dashboard.json"))
    control = dashboard_data["sections"][0]["controls"][0]
    assert control["secret_ref"] == "api-key:comfy_org"
    assert "configured" not in control
    assert "last_four" not in control
    assert "value" not in control
    assert dashboard_data["inputs"][0]["default"] == {
        "kind": "api_key_ref",
        "provider": "comfy_org",
        "secret_ref": "api-key:comfy_org",
    }


def test_export_supports_bundled_workflow_with_user_preferences(tmp_path: Path) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    loader = WorkflowPackageLoader(Path(__file__).resolve().parents[1] / "app/workflows/packages")
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        user_state_service=user_state_service,
    )
    user_state_service.save(
        WorkflowUserState(
            workflow_id="text_to_image_v0",
            dashboard_version="0.1.0",
            values={"prompt": "native export prompt"},
            layout_overrides={"prompt": UserStateLayoutOverride(x=1, y=2, w=20, h=5)},
        )
    )

    archive_bytes, filename = exporter.export_archive("text_to_image_v0")

    assert filename == "text_to_image_v0.noofy"
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = set(zf.namelist())
        package_data = json.loads(zf.read("package.json"))
        dashboard_data = json.loads(zf.read("dashboard.json"))
        graph_data = json.loads(zf.read("comfyui_graph.json"))

    assert {"package.json", "dashboard.json", "comfyui_graph.json", "capsule.lock.json", "export-report.json"} <= names
    assert package_data["publisher_id"] == "noofy"
    assert package_data["package_id"] == "text_to_image_v0"
    assert "dashboard" not in package_data
    assert dashboard_data["inputs"][0]["default"] == "native export prompt"
    assert dashboard_data["sections"][0]["controls"][0]["layout"] == {"x": 1, "y": 2, "w": 20, "h": 5}
    assert graph_data["6"]["inputs"]["text"] == "native export prompt"
