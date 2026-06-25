"""Tests for DashboardAuthoringService.

Verifies:
- PUT writes only dashboard.json (package.json bytes unchanged)
- PUT transitions dashboard status to 'configured'
- PUT rejects invalid bindings
- validate does not persist anything
- bindable-inputs works without a runner
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from app.diagnostics import LogStore
from app.workflows.authoring import (
    DashboardAuthoringService,
    DashboardAuthoringError,
    _classify_graph_inputs,
)
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.library_service import WorkflowLibraryService
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_archive(
    graph: dict[str, Any] | None = None,
    dashboard: dict[str, Any] | None = None,
    extra_files: dict[str, bytes] | None = None,
    package_update: dict[str, Any] | None = None,
) -> bytes:
    """Build a minimal .noofy archive suitable for import."""
    if graph is None:
        graph = {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "hello", "clip": ["4", 0]},
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "v1.safetensors"},
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {"images": ["5", 0], "filename_prefix": "out"},
            },
        }

    package_data: dict[str, Any] = {
        "schema_version": "0.5.0",
        "engine": "comfyui",
        "metadata": {"id": "test_wf", "name": "Test Workflow", "version": "1.0.0"},
        "publisher_id": "test_publisher",
        "package_id": "test_wf",
        "version": "1.0.0",
        "required_models": [],
        "custom_nodes": [],
    }
    package_data.update(package_update or {})

    capsule_data: dict[str, Any] = {
        "schema_version": "0.5.0",
        "capsule_id": "test_wf",
        "source_policy": "quarantined_community",
        "custom_nodes": [],
        "dependency_lock": {"packages": []},
        "graph_hash": "abc123",
        "dependency_env_hash": "def456",
        "runner_workspace_hash": "ghi789",
    }

    export_report: dict[str, Any] = {
        "export_timestamp": "2024-01-01T00:00:00Z",
        "comfyui_version": "0.0.1",
    }

    stub_dashboard: dict[str, Any] = (
        dashboard
        if dashboard is not None
        else {
            "version": "0.1.0",
            "status": "not_configured",
            "inputs": [],
            "outputs": [],
            "sections": [],
        }
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("package.json", json.dumps(package_data))
        zf.writestr("comfyui_graph.json", json.dumps(graph))
        zf.writestr("capsule.lock.json", json.dumps(capsule_data))
        zf.writestr("export-report.json", json.dumps(export_report))
        zf.writestr("dashboard.json", json.dumps(stub_dashboard))
        for filename, data in (extra_files or {}).items():
            zf.writestr(filename, data)
    return buf.getvalue()


def _import_and_setup(
    tmp_path: Path,
    archive_bytes: bytes,
) -> tuple[DashboardAuthoringService, str]:
    """Import an archive and return (service, workflow_id)."""
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    package = store.import_archive(archive_bytes, original_filename="test.noofy")
    workflow_id = package.metadata.id

    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    service = DashboardAuthoringService(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        validator=WorkflowPackageValidator(),
        log_store=log_store,
    )
    return service, workflow_id


# ---------------------------------------------------------------------------
# Minimal valid dashboard payload
# ---------------------------------------------------------------------------


def _minimal_inputs_and_dashboard() -> tuple[list[dict], dict]:
    inputs = [
        {
            "id": "prompt",
            "label": "Prompt",
            "control": "textarea",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": "hello",
            "validation": {},
        }
    ]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [
            {"id": "image_out", "label": "Image", "node_id": "9", "type": "image"}
        ],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "id": "ctrl_prompt",
                        "type": "textarea",
                        "label": "Prompt",
                        "input_id": "prompt",
                    },
                    {
                        "id": "ctrl_result",
                        "type": "result_image",
                        "label": "Result",
                        "output_id": "image_out",
                    },
                ],
            }
        ],
    }
    return inputs, dashboard


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_save_dashboard_writes_only_dashboard_json(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    # Record package.json bytes before save.
    # The workflow store uses publisher/package/version dirs.
    packages_root = tmp_path / "packages"
    found: list[Path] = list(packages_root.rglob("package.json"))
    assert found, "package.json should exist after import"
    package_json_path = found[0]
    package_json_bytes_before = package_json_path.read_bytes()

    inputs, dashboard = _minimal_inputs_and_dashboard()
    result = service.save_dashboard(workflow_id, inputs, dashboard)

    assert result["status"] == "configured"
    assert result["valid"] is True
    assert result["errors"] == []

    assert package_json_path.read_bytes() == package_json_bytes_before

    # dashboard.json must now exist and carry status: configured.
    dashboard_json_path = package_json_path.parent / "dashboard.json"
    assert dashboard_json_path.exists(), "dashboard.json should be written"
    saved = json.loads(dashboard_json_path.read_text())
    assert saved["status"] == "configured"
    assert any(i["id"] == "prompt" for i in saved["inputs"])


def test_save_dashboard_transitions_status(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    # Before save the dashboard is not_configured.
    pkg = service.workflow_loader.get_package(workflow_id)
    assert pkg.dashboard.status == "not_configured"

    inputs, dashboard = _minimal_inputs_and_dashboard()
    service.save_dashboard(workflow_id, inputs, dashboard)

    # Reload the package — status should be configured.
    # Flush cache by creating a fresh loader.
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    pkg_after = loader.get_package(workflow_id)
    assert pkg_after.dashboard.status == "configured"


def test_uploaded_builder_default_is_packaged_and_resolvable_after_reload(
    tmp_path: Path,
) -> None:
    graph = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": "creator-default.png"},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0], "filename_prefix": "out"},
        },
    }
    service, workflow_id = _import_and_setup(
        tmp_path,
        _make_minimal_archive(graph=graph),
    )
    dashboard_assets_dir = tmp_path / "dashboard-assets"
    dashboard_assets_dir.mkdir()
    uploaded_asset_id = "12345678-1234-1234-1234-123456789abc.png"
    uploaded_bytes = b"\x89PNG\r\n\x1a\nbuilder-default"
    (dashboard_assets_dir / uploaded_asset_id).write_bytes(uploaded_bytes)
    (dashboard_assets_dir / f"{uploaded_asset_id}.meta.json").write_text(
        json.dumps(
            {
                "asset_id": uploaded_asset_id,
                "kind": "image",
                "content_type": "image/png",
                "original_filename": "starter.png",
                "size": len(uploaded_bytes),
            }
        ),
        encoding="utf-8",
    )
    service.dashboard_assets_dir = dashboard_assets_dir

    inputs_payload = [
        {
            "id": "image",
            "label": "Input image",
            "control": "load_image",
            "binding": {"node_id": "1", "input_name": "image"},
            "default": uploaded_asset_id,
            "default_pinned": True,
            "validation": {},
        }
    ]
    dashboard_payload = {
        "version": "0.1.0",
        "status": "configured",
        "outputs": [
            {"id": "image_out", "label": "Image", "node_id": "9", "type": "image"}
        ],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "id": "image-control",
                        "type": "load_image",
                        "label": "Input image",
                        "input_id": "image",
                    }
                ],
            }
        ],
    }
    service.save_dashboard(
        workflow_id,
        inputs_payload,
        dashboard_payload,
    )

    reloaded = service.workflow_loader.get_package(workflow_id)
    default = reloaded.inputs[0].default
    assert default["source"] == "package_asset"
    assert default["filename"] == "starter.png"
    assert default["kind"] == "image"

    library = WorkflowLibraryService(
        workflow_loader=service.workflow_loader,
        model_availability_service=object(),  # type: ignore[arg-type]
        log_store=LogStore(),
    )
    resolved_path, reference = library.workflow_default_asset(workflow_id, "image")
    assert reference == default
    assert resolved_path.read_bytes() == uploaded_bytes

    inputs_payload[0]["default"] = None
    inputs_payload[0]["default_pinned"] = False
    service.save_dashboard(workflow_id, inputs_payload, dashboard_payload)

    assert not resolved_path.exists()
    assert not resolved_path.with_name(f"{resolved_path.name}.meta.json").exists()


def test_imported_packaged_default_is_resolvable_from_archived_source_files(
    tmp_path: Path,
) -> None:
    asset_bytes = b"\x89PNG\r\n\x1a\nimported-default"
    digest = hashlib.sha256(asset_bytes).hexdigest()
    asset_id = f"input-defaults/{digest[:16]}-starter.png"
    reference = {
        "source": "package_asset",
        "asset_id": asset_id,
        "kind": "image",
        "filename": "starter.png",
        "content_type": "image/png",
        "size_bytes": len(asset_bytes),
        "sha256": f"sha256:{digest}",
    }
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "1", "input_name": "image"},
                "default": reference,
                "default_pinned": True,
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
                    {
                        "id": "image-control",
                        "type": "load_image",
                        "label": "Input image",
                        "input_id": "image",
                    }
                ],
            }
        ],
    }
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "starter.png"}},
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["1", 0], "filename_prefix": "out"},
        },
    }
    service, workflow_id = _import_and_setup(
        tmp_path,
        _make_minimal_archive(
            graph=graph,
            dashboard=dashboard,
            extra_files={f"assets/{asset_id}": asset_bytes},
        ),
    )
    library = WorkflowLibraryService(
        workflow_loader=service.workflow_loader,
        model_availability_service=object(),  # type: ignore[arg-type]
        log_store=LogStore(),
    )

    resolved_path, resolved_reference = library.workflow_default_asset(
        workflow_id,
        "image",
    )

    assert "source-files/assets/input-defaults" in resolved_path.as_posix()
    assert resolved_path.read_bytes() == asset_bytes
    assert resolved_reference == reference

    service.save_dashboard(workflow_id, dashboard["inputs"], dashboard)
    reloaded = service.workflow_loader.get_package(workflow_id)
    assert reloaded.inputs[0].default == reference

    resolved_after_save, reference_after_save = library.workflow_default_asset(
        workflow_id,
        "image",
    )
    assert resolved_after_save.read_bytes() == asset_bytes
    assert reference_after_save == reference


def test_save_dashboard_persists_action_bar_presentation(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs, dashboard = _minimal_inputs_and_dashboard()
    dashboard["presentation"] = {"action_bar": {"x": 120, "y": 28}}
    service.save_dashboard(workflow_id, inputs, dashboard)

    reloaded = service.workflow_loader.get_package(workflow_id)
    assert reloaded.dashboard.presentation is not None
    assert reloaded.dashboard.presentation.action_bar is not None
    assert reloaded.dashboard.presentation.action_bar.x == 120
    assert reloaded.dashboard.presentation.action_bar.y == 28


def test_save_dashboard_rejects_invalid_binding(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [
        {
            "id": "bad_input",
            "label": "Bad",
            "control": "textarea",
            "binding": {"node_id": "999_nonexistent", "input_name": "text"},
            "default": "",
            "validation": {},
        }
    ]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "id": "ctrl_bad",
                        "type": "textarea",
                        "label": "Bad",
                        "input_id": "bad_input",
                    }
                ],
            }
        ],
    }

    with pytest.raises(DashboardAuthoringError, match="missing node"):
        service.save_dashboard(workflow_id, inputs, dashboard)


def _graph_with_required_image_input() -> dict[str, Any]:
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["4", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1.safetensors"}},
        "10": {"class_type": "LoadImage", "inputs": {"image": "reference.png"}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "out"}},
    }


def _graph_with_required_text_path_inputs() -> dict[str, Any]:
    return {
        "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hello", "clip": ["4", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1.safetensors"}},
        "22:4": {"class_type": "LoadText", "inputs": {"image": "/creator/input-a.txt"}},
        "22:5": {"class_type": "LoadText", "inputs": {"image": "/creator/input-b.txt"}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "out"}},
    }


def _prompt_input() -> dict[str, Any]:
    return {
        "id": "prompt",
        "label": "Prompt",
        "control": "textarea",
        "binding": {"node_id": "1", "input_name": "text"},
        "default": "hi",
        "validation": {},
    }


def _image_input(default: Any = None) -> dict[str, Any]:
    return {
        "id": "img",
        "label": "Image",
        "control": "load_image",
        "binding": {"node_id": "10", "input_name": "image"},
        "default": default,
        "validation": {},
    }


def _text_path_input(
    input_id: str,
    node_id: str,
    *,
    default: Any = "",
) -> dict[str, Any]:
    return {
        "id": input_id,
        "label": "Image",
        "control": "string_field",
        "binding": {"node_id": node_id, "input_name": "image"},
        "default": default,
        "validation": {},
    }


def test_save_dashboard_rejects_removed_required_runtime_input(tmp_path: Path) -> None:
    # A LoadImage referencing an unbundled creator-local file is a required
    # runtime input. Removing its auto-created widget must not save a dashboard
    # that still reports as needing setup (which silently bounces the user back
    # to the builder); it must raise a clear, actionable validation error.
    archive = _make_minimal_archive(graph=_graph_with_required_image_input())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    pkg = service.workflow_loader.get_package(workflow_id)
    assert any(
        runtime_input.node_id == "10" for runtime_input in pkg.unresolved_runtime_inputs
    )

    # Dashboard omits the load_image input (user removed it) but keeps the
    # output display widget.
    inputs = [_prompt_input()]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "c_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                    {"id": "c_result", "type": "display_image", "label": "Result", "output_id": "image"},
                ],
            }
        ],
    }

    with pytest.raises(DashboardAuthoringError, match="required input"):
        service.save_dashboard(workflow_id, inputs, dashboard)

    # Nothing was persisted: the dashboard is still not_configured.
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    assert loader.get_package(workflow_id).dashboard.status == "not_configured"


def test_save_dashboard_allows_removing_output_when_required_input_bound(
    tmp_path: Path,
) -> None:
    # Output/display widgets are not mandatory. Removing one while the required
    # input stays bound must save successfully and report the workflow ready.
    archive = _make_minimal_archive(graph=_graph_with_required_image_input())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [_prompt_input(), _image_input("123e4567-e89b-12d3-a456-426614174000.png")]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "c_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                    {"id": "c_img", "type": "load_image", "label": "Image", "input_id": "img"},
                ],
            }
        ],
    }

    result = service.save_dashboard(workflow_id, inputs, dashboard)
    assert result["status"] == "configured"

    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    reloaded = loader.get_package(workflow_id)
    assert reloaded.dashboard.status == "configured"
    # Binding the load_image input resolves the runtime requirement, so the
    # workflow is no longer flagged as needing dashboard setup.
    assert not reloaded.unresolved_runtime_inputs


def test_save_dashboard_allows_hidden_required_runtime_input(
    tmp_path: Path,
) -> None:
    archive = _make_minimal_archive(graph=_graph_with_required_image_input())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [_prompt_input(), _image_input("123e4567-e89b-12d3-a456-426614174000.png")]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "c_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                    {"id": "c_result", "type": "display_image", "label": "Result", "output_id": "image"},
                ],
            }
        ],
    }

    result = service.save_dashboard(workflow_id, inputs, dashboard)

    assert result["status"] == "configured"
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    reloaded = loader.get_package(workflow_id)
    assert any(workflow_input.id == "img" for workflow_input in reloaded.inputs)
    assert all(control.input_id != "img" for section in reloaded.dashboard.sections for control in section.controls)


def test_save_dashboard_allows_visible_required_text_path_inputs_with_empty_defaults(
    tmp_path: Path,
) -> None:
    archive = _make_minimal_archive(graph=_graph_with_required_text_path_inputs())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [
        _prompt_input(),
        _text_path_input("text_path_a", "22:4"),
        _text_path_input("text_path_b", "22:5"),
    ]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "c_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                    {"id": "c_text_path_a", "type": "string_field", "label": "Image", "input_id": "text_path_a"},
                    {"id": "c_text_path_b", "type": "string_field", "label": "Image", "input_id": "text_path_b"},
                    {"id": "c_result", "type": "display_image", "label": "Result", "output_id": "image"},
                ],
            }
        ],
    }

    result = service.save_dashboard(workflow_id, inputs, dashboard)

    assert result["status"] == "configured"
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    reloaded = loader.get_package(workflow_id)
    assert not reloaded.unresolved_runtime_inputs
    assert [
        workflow_input.default
        for workflow_input in reloaded.inputs
        if workflow_input.id in {"text_path_a", "text_path_b"}
    ] == ["", ""]


def test_bindable_inputs_mark_required_runtime_text_path_inputs(
    tmp_path: Path,
) -> None:
    archive = _make_minimal_archive(graph=_graph_with_required_text_path_inputs())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    result = service.get_bindable_inputs(workflow_id)

    inputs_by_binding = {
        (node["node_id"], input_record["input_name"]): input_record
        for node in result["nodes"]
        for input_record in node["inputs"]
    }
    assert inputs_by_binding[("22:4", "image")]["required_runtime_input"] is True
    assert inputs_by_binding[("22:4", "image")]["required_runtime_kind"] == "text"
    assert inputs_by_binding[("22:5", "image")]["required_runtime_input"] is True
    assert inputs_by_binding[("22:5", "image")]["required_runtime_kind"] == "text"


def test_bindable_inputs_drop_legacy_multimodal_text_media_requirements(
    tmp_path: Path,
) -> None:
    graph = {
        "22:4": {
            "class_type": "TextEncodeQwenImageEdit",
            "inputs": {
                "image": "__noofy_runtime_text_input_required__",
                "prompt": "turn the dog red",
            },
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"images": ["22:4", 0], "filename_prefix": "out"},
        },
    }
    archive = _make_minimal_archive(
        graph=graph,
        package_update={
            "unresolved_runtime_inputs": [
                {
                    "node_id": "22:4",
                    "node_type": "TextEncodeQwenImageEdit",
                    "input_name": "image",
                    "current_value": "__noofy_runtime_text_input_required__",
                    "reason": "creator_local_text_not_bundled",
                    "expected_kind": "text",
                    "required": True,
                }
            ]
        },
    )
    service, workflow_id = _import_and_setup(tmp_path, archive)

    result = service.get_bindable_inputs(workflow_id)

    text_encoder = next(
        node for node in result["nodes"] if node["node_id"] == "22:4"
    )
    assert [item["input_name"] for item in text_encoder["inputs"]] == ["prompt"]
    package = service._get_package(workflow_id)
    assert package.comfyui_graph["22:4"]["inputs"] == {
        "prompt": "turn the dog red"
    }
    assert package.unresolved_runtime_inputs == []


def test_save_dashboard_rejects_hidden_required_text_path_inputs_with_empty_defaults(
    tmp_path: Path,
) -> None:
    archive = _make_minimal_archive(graph=_graph_with_required_text_path_inputs())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [
        _prompt_input(),
        _text_path_input("text_path_a", "22:4"),
        _text_path_input("text_path_b", "22:5"),
    ]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "c_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                    {"id": "c_result", "type": "display_image", "label": "Result", "output_id": "image"},
                ],
            }
        ],
    }

    with pytest.raises(DashboardAuthoringError, match="required input") as exc_info:
        service.save_dashboard(workflow_id, inputs, dashboard)

    message = str(exc_info.value)
    assert 'Text file input "Image" on node 22:4' in message
    assert 'Text file input "Image" on node 22:5' in message
    assert "Image (text input" not in message


def test_save_dashboard_rejects_hidden_required_runtime_input_without_default(
    tmp_path: Path,
) -> None:
    archive = _make_minimal_archive(graph=_graph_with_required_image_input())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [_prompt_input(), _image_input()]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "c_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                    {"id": "c_result", "type": "display_image", "label": "Result", "output_id": "image"},
                ],
            }
        ],
    }

    with pytest.raises(DashboardAuthoringError, match="required input"):
        service.save_dashboard(workflow_id, inputs, dashboard)


def test_validate_dashboard_flags_removed_required_runtime_input(tmp_path: Path) -> None:
    archive = _make_minimal_archive(graph=_graph_with_required_image_input())
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [_prompt_input()]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [{"id": "image", "label": "Image", "node_id": "9", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "c_prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                    {"id": "c_result", "type": "display_image", "label": "Result", "output_id": "image"},
                ],
            }
        ],
    }

    result = service.validate_dashboard(workflow_id, inputs, dashboard)
    assert result["valid"] is False
    assert any("required input" in error for error in result["errors"])


def test_validate_dashboard_does_not_persist(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    packages_root = tmp_path / "packages"
    dashboard_files_before = set(packages_root.rglob("dashboard.json"))

    inputs, dashboard = _minimal_inputs_and_dashboard()
    result = service.validate_dashboard(workflow_id, inputs, dashboard)

    assert result["valid"] is True

    # No new files should have been written by validate.
    dashboard_files_after = set(packages_root.rglob("dashboard.json"))
    assert (
        dashboard_files_after == dashboard_files_before
    ), "validate_dashboard must not write any files"


def test_validate_dashboard_accepts_decimal_slider_steps(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)
    inputs, dashboard = _minimal_inputs_and_dashboard()
    inputs.append(
        {
            "id": "denoise",
            "label": "Transformation level",
            "control": "slider",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": 0.3,
            "validation": {"min": 0, "max": 1, "step": 0.01},
        }
    )
    dashboard["sections"][0]["controls"].append(
        {
            "id": "denoise",
            "type": "slider",
            "label": "Transformation level",
            "input_id": "denoise",
        }
    )

    result = service.validate_dashboard(workflow_id, inputs, dashboard)

    assert result["valid"] is True


@pytest.mark.parametrize(
    ("validation", "default", "message"),
    [
        ({"min": 0, "max": 1, "step": 0}, 0.3, "validation.step greater than 0"),
        ({"min": 1, "max": 1, "step": 1}, 1, "validation.max greater than validation.min"),
        ({"min": 0, "max": 1, "step": 0.1}, 2, "default above validation.max"),
        ({"min": 0, "max": 1, "step": 0.25}, 0.3, "default that does not align"),
    ],
)
def test_validate_dashboard_rejects_invalid_slider_constraints(
    tmp_path: Path,
    validation: dict[str, int | float],
    default: int | float,
    message: str,
) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)
    inputs, dashboard = _minimal_inputs_and_dashboard()
    inputs.append(
        {
            "id": "denoise",
            "label": "Transformation level",
            "control": "slider",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": default,
            "validation": validation,
        }
    )
    dashboard["sections"][0]["controls"].append(
        {
            "id": "denoise",
            "type": "slider",
            "label": "Transformation level",
            "input_id": "denoise",
        }
    )

    result = service.validate_dashboard(workflow_id, inputs, dashboard)

    assert result["valid"] is False
    assert any(message in error for error in result["errors"])
    with pytest.raises(DashboardAuthoringError, match=message):
        service.save_dashboard(workflow_id, inputs, dashboard)


def test_save_dashboard_persists_visual_control_groups(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    inputs = [
        {
            "id": "width",
            "label": "Width",
            "control": "slider",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": 512,
            "validation": {"min": 256, "max": 1024, "step": 64},
        },
        {
            "id": "height",
            "label": "Height",
            "control": "slider",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": 512,
            "validation": {"min": 256, "max": 1024, "step": 64},
        },
    ]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "width", "type": "slider", "label": "Width", "input_id": "width"},
                    {"id": "height", "type": "slider", "label": "Height", "input_id": "height"},
                ],
                "groups": [
                    {
                        "id": "size-group",
                        "title": "Image size",
                        "description": "Output dimensions.",
                        "control_ids": ["width", "height"],
                        "layout": {"x": 0, "y": 0, "w": 12, "h": 8},
                    }
                ],
            }
        ],
    }

    service.save_dashboard(workflow_id, inputs, dashboard)

    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    pkg = loader.get_package(workflow_id)
    group = pkg.dashboard.sections[0].groups[0]
    assert group.id == "size-group"
    assert group.control_ids == ["width", "height"]
    assert group.layout is not None
    assert group.layout.w == 12


def test_save_dashboard_rejects_invalid_control_groups(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)
    inputs, dashboard = _minimal_inputs_and_dashboard()
    dashboard["sections"][0]["groups"] = [
        {"id": "broken", "title": "Broken", "control_ids": ["ctrl_prompt", "missing_control"]}
    ]

    with pytest.raises(DashboardAuthoringError, match="missing control"):
        service.save_dashboard(workflow_id, inputs, dashboard)


@pytest.mark.parametrize(
    ("layout", "message"),
    [
        ({"x": -1, "y": 0, "w": 8, "h": 4}, "layout.x must be greater than or equal to 0"),
        ({"x": 0, "y": -1, "w": 8, "h": 4}, "layout.y must be greater than or equal to 0"),
        ({"x": 0, "y": 0, "w": 0, "h": 4}, "layout.w must be greater than 0"),
        ({"x": 0, "y": 0, "w": 8, "h": -1}, "layout.h must be greater than 0"),
        ({"x": 0, "y": 0, "w": 8, "h": 4, "min_w": 9}, "layout.min_w must not be larger than layout.w"),
        ({"x": 0, "y": 0, "w": 8, "h": 4, "min_h": 5}, "layout.min_h must not be larger than layout.h"),
        ({"x": 0, "y": 0, "w": 33, "h": 4}, "layout.w must not exceed the 32-column grid"),
        ({"x": 28, "y": 0, "w": 8, "h": 4}, "extends beyond the 32-column grid"),
    ],
)
def test_validate_and_save_dashboard_reject_invalid_control_layouts(
    tmp_path: Path,
    layout: dict[str, int],
    message: str,
) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)
    inputs, dashboard = _minimal_inputs_and_dashboard()
    dashboard["sections"][0]["controls"][0]["layout"] = layout

    result = service.validate_dashboard(workflow_id, inputs, dashboard)

    assert result["valid"] is False
    assert any(message in error for error in result["errors"])
    with pytest.raises(DashboardAuthoringError, match=message):
        service.save_dashboard(workflow_id, inputs, dashboard)


def test_validate_and_save_dashboard_reject_invalid_group_layouts(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)
    inputs, dashboard = _minimal_inputs_and_dashboard()
    dashboard["sections"][0]["groups"] = [
        {
            "id": "broken-layout",
            "title": "Broken layout",
            "control_ids": ["ctrl_prompt", "ctrl_result"],
            "layout": {"x": 0, "y": 0, "w": 10, "h": 4, "min_w": 11},
        }
    ]

    result = service.validate_dashboard(workflow_id, inputs, dashboard)

    assert result["valid"] is False
    assert any("layout.min_w must not be larger than layout.w" in error for error in result["errors"])
    with pytest.raises(DashboardAuthoringError, match="layout.min_w must not be larger than layout.w"):
        service.save_dashboard(workflow_id, inputs, dashboard)


def test_validate_and_save_dashboard_reject_overlapping_top_level_layouts(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)
    inputs, dashboard = _minimal_inputs_and_dashboard()
    dashboard["sections"][0]["controls"][0]["layout"] = {"x": 0, "y": 0, "w": 12, "h": 6}
    dashboard["sections"][0]["controls"][1]["layout"] = {"x": 8, "y": 2, "w": 12, "h": 6}

    result = service.validate_dashboard(workflow_id, inputs, dashboard)

    assert result["valid"] is False
    assert any("overlaps" in error for error in result["errors"])
    with pytest.raises(DashboardAuthoringError, match="overlaps"):
        service.save_dashboard(workflow_id, inputs, dashboard)


def test_validate_and_save_dashboard_reject_group_overlap_with_top_level_control(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)
    inputs, dashboard = _minimal_inputs_and_dashboard()
    dashboard["sections"][0]["controls"].append(
        {
            "id": "creator-note",
            "type": "note",
            "label": "Note",
            "description": "Instructions",
            "layout": {"x": 4, "y": 2, "w": 10, "h": 4},
        }
    )
    dashboard["sections"][0]["groups"] = [
        {
            "id": "main-group",
            "title": "Main group",
            "control_ids": ["ctrl_prompt", "ctrl_result"],
            "layout": {"x": 0, "y": 0, "w": 12, "h": 6},
        }
    ]

    result = service.validate_dashboard(workflow_id, inputs, dashboard)

    assert result["valid"] is False
    assert any("Dashboard layout item 'creator-note' overlaps 'main-group'" in error for error in result["errors"])
    with pytest.raises(DashboardAuthoringError, match="overlaps 'main-group'"):
        service.save_dashboard(workflow_id, inputs, dashboard)


def test_dashboard_schema_accepts_api_credential_without_raw_secret(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
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
                        "provider": "comfy_org",
                        "required": True,
                        "secret_ref": "api-key:comfy_org",
                        "injection_strategy": {
                            "kind": "comfyui_extra_data",
                            "field": "api_key_comfy_org",
                        },
                    }
                ],
            }
        ],
    }

    result = service.save_dashboard(workflow_id, [], dashboard)

    assert result["valid"] is True
    saved = service.workflow_loader.get_package(workflow_id).dashboard.model_dump(mode="json")
    assert saved["sections"][0]["controls"][0]["secret_ref"] == "api-key:comfy_org"
    assert "api_key" not in saved["sections"][0]["controls"][0]


def test_get_bindable_inputs_works_without_runner(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    result = service.get_bindable_inputs(workflow_id)

    assert result["workflow_id"] == workflow_id
    assert result["enrichment"] == "heuristic"
    # The graph has CLIPTextEncode with a "text" string input.
    nodes = result["nodes"]
    assert isinstance(nodes, list)
    clip_nodes = [n for n in nodes if n["node_type"] == "CLIPTextEncode"]
    assert clip_nodes, "CLIPTextEncode should appear in bindable inputs"
    text_inputs = [
        inp for inp in clip_nodes[0]["inputs"] if inp["input_name"] == "text"
    ]
    assert text_inputs, "text input should be classified"


def test_bindable_inputs_preserve_exported_packaged_image_default(
    tmp_path: Path,
) -> None:
    asset_bytes = b"\x89PNG\r\n\x1a\nexported-default"
    digest = hashlib.sha256(asset_bytes).hexdigest()
    asset_id = f"input-defaults/{digest[:16]}-starter.png"
    reference = {
        "source": "package_asset",
        "asset_id": asset_id,
        "kind": "image",
        "filename": "starter.png",
        "content_type": "image/png",
        "size_bytes": len(asset_bytes),
        "sha256": f"sha256:{digest}",
    }
    archive = _make_minimal_archive(
        graph={
            "13": {
                "class_type": "LoadImage",
                "inputs": {"image": "__noofy_runtime_image_input_required__"},
            }
        },
        dashboard={
            "version": "0.1.0",
            "status": "not_configured",
            "inputs": [
                {
                    "id": "input-13-image",
                    "label": "Image",
                    "control": "load_image",
                    "binding": {"node_id": "13", "input_name": "image"},
                    "default": reference,
                    "default_pinned": True,
                    "validation": {},
                }
            ],
            "outputs": [],
            "sections": [],
        },
        extra_files={f"assets/{asset_id}": asset_bytes},
    )
    service, workflow_id = _import_and_setup(tmp_path, archive)

    result = service.get_bindable_inputs(workflow_id)

    image_input = result["nodes"][0]["inputs"][0]
    assert image_input["backend_input_id"] == "input-13-image"
    assert image_input["current_value"] == reference
    assert image_input["default_pinned"] is True
    assert "required_runtime_input" not in image_input


def test_get_bindable_inputs_uses_exported_widget_metadata_without_runner(
    tmp_path: Path,
) -> None:
    archive = _make_minimal_archive(
        graph={
            "12": {
                "class_type": "CustomFrontendSelector",
                "inputs": {"style": "cinematic"},
            }
        },
        package_update={
            "comfyui_widget_metadata": {
                "schema_version": "0.1.0",
                "nodes": {
                    "12": {
                        "inputs": {
                            "style": {
                                "options": ["cinematic", "illustration"],
                                "display_name": "Rendering style",
                            }
                        }
                    }
                },
            }
        },
    )
    service, workflow_id = _import_and_setup(tmp_path, archive)

    result = service.get_bindable_inputs(workflow_id)

    assert result["enrichment"] == "exported_widgets"
    assert result["nodes"][0]["inputs"][0]["options"] == ["cinematic", "illustration"]
    assert result["nodes"][0]["inputs"][0]["suggested_label"] == "Rendering style"


def test_classify_graph_inputs_labels_clip_text_from_downstream_prompt_role() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "Misleading negative title"},
                "inputs": {"text": "content is not inspected", "clip": ["10", 0]},
            },
            "2": {
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "Misleading positive title"},
                "inputs": {"text": "", "clip": ["10", 0]},
            },
            "3": {
                "class_type": "ConditioningSetArea",
                "inputs": {"conditioning": ["1", 0], "width": 512},
            },
            "4": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": ["3", 0],
                    "negative": ["2", 0],
                },
            },
        }
    )

    labels = {
        node["node_id"]: next(
            input_record["suggested_label"]
            for input_record in node["inputs"]
            if input_record["input_name"] == "text"
        )
        for node in nodes
        if node["node_type"] == "CLIPTextEncode"
    }
    assert labels == {"1": "Positive prompt", "2": "Negative prompt"}
    negative_prompt_input = next(
        input_record
        for node in nodes
        for input_record in node["inputs"]
        if node["node_id"] == "2" and input_record["input_name"] == "text"
    )
    assert negative_prompt_input["suggested_widget_type"] == "textarea"
    assert negative_prompt_input["widget_types"] == ["textarea", "string_field"]


def test_classify_graph_inputs_labels_basic_guider_conditioning_as_positive() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": "hello", "clip": ["10", 0]},
            },
            "2": {
                "class_type": "BasicGuider",
                "inputs": {"conditioning": ["1", 0], "model": ["10", 0]},
            },
        }
    )

    assert nodes[0]["inputs"][0]["suggested_label"] == "Positive prompt"


@pytest.mark.parametrize(
    "downstream_nodes",
    [
        {},
        {
            "2": {
                "class_type": "ConditioningSetArea",
                "inputs": {"conditioning": ["1", 0]},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "positive": ["2", 0],
                    "negative": ["2", 0],
                },
            },
        },
        {
            "2": {
                "class_type": "UnknownMultiOutputNode",
                "inputs": {"value": ["1", 0]},
            },
            "3": {
                "class_type": "KSampler",
                "inputs": {"positive": ["2", 1]},
            },
        },
    ],
    ids=["unclassified", "ambiguous", "unrelated-intermediate"],
)
def test_classify_graph_inputs_uses_generic_prompt_label_without_single_role(
    downstream_nodes: dict[str, Any],
) -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "CLIPTextEncode",
                "_meta": {"title": "Negative prompt"},
                "inputs": {"text": "blurry", "clip": ["10", 0]},
            },
            **downstream_nodes,
        }
    )

    assert nodes[0]["inputs"][0]["suggested_label"] == "Prompt"


def test_get_bindable_inputs_includes_image_output_widgets(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    result = service.get_bindable_inputs(workflow_id)

    output_nodes = [n for n in result["nodes"] if n["node_type"] == "SaveImage"]
    assert output_nodes, "SaveImage should appear as an output-capable dashboard node"
    output_values = [
        inp for inp in output_nodes[0]["inputs"] if inp["kind"] == "image_output"
    ]
    assert output_values == [
        {
            "input_name": "output_image",
            "current_value": None,
            "kind": "image_output",
            "suggested_widget_type": "display_image",
            "widget_types": ["display_image"],
            "auto_select": True,
        }
    ]


def test_classify_graph_inputs_includes_comfyui_meta_title_for_scalar_nodes() -> None:
    nodes = _classify_graph_inputs(
        {
            "128:116": {
                "_meta": {"title": "Float(CFG)"},
                "class_type": "PrimitiveFloat",
                "inputs": {"value": 3.5},
            }
        }
    )

    assert nodes[0]["node_id"] == "128:116"
    assert nodes[0]["node_type"] == "PrimitiveFloat"
    assert nodes[0]["node_title"] == "Float(CFG)"
    assert nodes[0]["inputs"][0]["input_name"] == "value"


def test_classify_graph_inputs_suggests_dashboard_note_for_comfyui_note_nodes() -> None:
    nodes = _classify_graph_inputs(
        {
            "11": {
                "class_type": "Note",
                "title": "Before you run",
                "inputs": {"text": "Use a square source image.\nLarge images take longer."},
            }
        }
    )

    assert nodes == [
        {
            "node_id": "11",
            "node_type": "Note",
            "node_title": "Before you run",
            "is_image_node": False,
            "is_lora_node": False,
            "inputs": [
                {
                    "input_name": "note",
                    "current_value": "Use a square source image.\nLarge images take longer.",
                    "kind": "note",
                    "suggested_widget_type": "note",
                    "widget_types": ["note"],
                    "auto_select": True,
                }
            ],
        }
    ]


def test_classify_graph_inputs_reads_frontend_style_comfyui_note_nodes() -> None:
    nodes = _classify_graph_inputs(
        {
            "nodes": [],
            "definitions": {
                "subgraphs": [
                    {
                        "id": "video-workflow",
                        "nodes": [
                            {
                                "id": 12,
                                "type": "Note",
                                "inputs": [],
                                "outputs": [],
                                "widgets_values": ["This workflow needs plenty of free VRAM."],
                            }
                        ],
                    }
                ]
            },
        }
    )

    assert nodes[0]["node_id"] == "visual:workflow/subgraph:video-workflow:node:12"
    assert nodes[0]["node_title"] == "Note"
    assert nodes[0]["inputs"][0] == {
        "input_name": "note",
        "current_value": "This workflow needs plenty of free VRAM.",
        "kind": "note",
        "suggested_widget_type": "note",
        "widget_types": ["note"],
        "auto_select": True,
    }


def test_save_dashboard_allows_dashboard_only_note_without_input_binding(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "id": "creator-note",
                        "type": "note",
                        "label": "Before you run",
                        "description": "Use a square source image.\nLarge images take longer.",
                        "layout": {"x": 0, "y": 0, "w": 6, "h": 4, "min_w": 6, "min_h": 4},
                    }
                ],
            }
        ],
    }

    result = service.save_dashboard(workflow_id, [], dashboard)

    assert result["valid"] is True
    saved = service.workflow_loader.get_package(workflow_id)
    control = saved.dashboard.sections[0].controls[0]
    assert control.type == "note"
    assert control.input_id is None
    assert control.description == "Use a square source image.\nLarge images take longer."


def test_save_dashboard_rejects_note_output_binding(tmp_path: Path) -> None:
    archive = _make_minimal_archive()
    service, workflow_id = _import_and_setup(tmp_path, archive)

    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [{"id": "image", "label": "Result", "node_id": "9", "type": "image"}],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "id": "creator-note",
                        "type": "note",
                        "label": "Before you run",
                        "description": "Read this first.",
                        "output_id": "image",
                    }
                ],
            }
        ],
    }

    with pytest.raises(DashboardAuthoringError, match="must not have output_id"):
        service.save_dashboard(workflow_id, [], dashboard)


def test_classify_graph_inputs_marks_load_image_as_image_input_only_when_unlinked() -> None:
    nodes = _classify_graph_inputs(
        {
            "10": {
                "class_type": "LoadImage",
                "inputs": {"image": "creator-local-input.png", "upload": "image"},
            },
            "11": {
                "class_type": "LoadImage",
                "inputs": {"image": ["3", 0]},
            },
            "12": {
                "class_type": "PreviewImage",
                "inputs": {"images": ["10", 0]},
            },
        }
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["10"]["inputs"][0] == {
        "input_name": "image",
        "current_value": "creator-local-input.png",
        "kind": "image_input",
        "suggested_widget_type": "load_image",
        "widget_types": ["load_image", "load_image_mask"],
    }
    assert len(input_nodes["10"]["inputs"]) == 1
    assert "11" not in input_nodes
    assert input_nodes["12"]["inputs"] == [
        {
            "input_name": "output_image",
            "current_value": None,
            "kind": "image_output",
            "suggested_widget_type": "display_image",
            "widget_types": ["display_image"],
            "auto_select": True,
        }
    ]


def test_classify_graph_inputs_uses_bundled_media_contract_without_object_info() -> None:
    nodes = _classify_graph_inputs(
        {
            "image": {"class_type": "LoadImageOutput", "inputs": {"image": ""}},
            "video": {"class_type": "LoadVideo", "inputs": {"file": ""}},
            "audio": {"class_type": "LoadAudio", "inputs": {"audio": ""}},
            "model": {"class_type": "Load3D", "inputs": {"model_file": "none"}},
        },
        widget_metadata={
            "nodes": {
                "model": {
                    "inputs": {
                        "model_file": {
                            "input_type": "COMBO",
                            "file_upload": True,
                        }
                    }
                }
            }
        },
    )

    kinds = {node["node_id"]: node["inputs"][0]["kind"] for node in nodes}
    assert kinds == {
        "image": "image_input",
        "video": "video_input",
        "audio": "audio_input",
        "model": "three_d_input",
    }


def _custom_node_archive() -> bytes:
    node_type = "ArbitraryCustomNode"
    return _make_minimal_archive(
        graph={"1": {"class_type": node_type, "inputs": {"payload": ""}}},
        package_update={
            "custom_nodes": [
                {
                    "id": "arbitrary-custom-node",
                    "folder_name": "arbitrary-custom-node",
                    "source": "https://example.invalid/arbitrary-custom-node.git",
                    "included": False,
                    "node_types": [node_type],
                }
            ]
        },
    )


def test_get_bindable_inputs_waits_for_custom_string_input_metadata(
    tmp_path: Path,
) -> None:
    service, workflow_id = _import_and_setup(tmp_path, _custom_node_archive())

    assert service.get_bindable_inputs(workflow_id) == {
        "workflow_id": workflow_id,
        "status": "controls_preparing",
        "enrichment": "pending",
        "nodes": [],
    }


def test_get_bindable_inputs_does_not_accept_partial_core_object_info_for_custom_nodes(
    tmp_path: Path,
) -> None:
    service, workflow_id = _import_and_setup(tmp_path, _custom_node_archive())

    result = service.get_bindable_inputs(
        workflow_id,
        object_info={"KSampler": {"input": {"required": {}}}},
    )

    assert result["status"] == "controls_preparing"
    assert result["nodes"] == []


def test_get_bindable_inputs_uses_graph_metadata_while_engine_verification_is_pending(
    tmp_path: Path,
) -> None:
    graph = {
        "1": {
            "class_type": "UnknownRawNode",
            "inputs": {"payload": ""},
        }
    }
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=log_store,
    )
    package = store.import_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="unknown-raw-node.json",
    )
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    raw_details = package.import_metadata.developer_details["raw_comfyui_json"]
    assert raw_details["custom_node_detection"] == {
        "status": "engine_verification_pending",
        "executable_node_types": ["UnknownRawNode"],
    }
    service = DashboardAuthoringService(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=WorkflowPackageLoader(
            Path("missing-bundled"),
            imported_packages_dir=tmp_path / "packages",
        ),
        validator=WorkflowPackageValidator(),
        log_store=log_store,
    )

    result = service.get_bindable_inputs(package.metadata.id)
    assert result["status"] == "ready"
    assert result["enrichment"] == "heuristic"
    assert result["nodes"][0]["node_type"] == "UnknownRawNode"


def test_classify_graph_inputs_includes_audio_widgets() -> None:
    nodes = _classify_graph_inputs(
        {
            "20": {
                "class_type": "LoadAudio",
                "inputs": {"audio": "creator-local-audio.wav"},
            },
            "21": {
                "class_type": "SaveAudioMP3",
                "inputs": {"audio": ["20", 0], "filename_prefix": "voice"},
            },
        }
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["20"]["inputs"][0] == {
        "input_name": "audio",
        "current_value": "creator-local-audio.wav",
        "kind": "audio_input",
        "suggested_widget_type": "load_audio",
        "widget_types": ["load_audio"],
    }
    assert input_nodes["21"]["inputs"][0] == {
        "input_name": "output_audio",
        "current_value": None,
        "kind": "audio_output",
        "suggested_widget_type": "display_audio",
        "widget_types": ["display_audio"],
        "auto_select": True,
    }


def test_classify_graph_inputs_supports_preview_any_text_output() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "TextGenerate",
                "inputs": {"model": ["8", 0]},
            },
            "4": {
                "_meta": {"title": "Preview as Text"},
                "class_type": "PreviewAny",
                "inputs": {"source": ["1", 0]},
            },
        },
        object_info={
            "PreviewAny": {"output": ["STRING"]},
            "TextGenerate": {"output": ["STRING"]},
        },
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["4"]["node_title"] == "Preview as Text"
    assert input_nodes["4"]["inputs"] == [
        {
            "input_name": "output_text",
            "current_value": None,
            "kind": "text_output",
            "suggested_widget_type": "display_text",
            "widget_types": ["display_text"],
            "auto_select": True,
        }
    ]


def test_classify_graph_inputs_infers_custom_media_uploads_from_object_info() -> None:
    nodes = _classify_graph_inputs(
        {
            "image": {
                "class_type": "ArbitraryNodeAlpha",
                "inputs": {
                    "payload_a": "",
                    "helper_a": "image",
                    "caption": "Keep this control",
                },
            },
            "audio": {
                "class_type": "ArbitraryNodeBeta",
                "inputs": {
                    "payload_b": "",
                    "audioUI": "/api/view?filename=&type=input",
                },
            },
            "video": {"class_type": "ArbitraryNodeGamma", "inputs": {"payload_c": ""}},
            "file": {"class_type": "ArbitraryNodeDelta", "inputs": {"payload_d": ""}},
        },
        object_info={
            "ArbitraryNodeAlpha": {
                "input": {
                    "required": {
                        "payload_a": [
                            ["", "creator-private.png"],
                            {"image_upload": True, "tooltip": "Choose an image."},
                        ],
                        "helper_a": ["IMAGEUPLOAD", {}],
                        "caption": ["STRING", {}],
                    }
                },
                "output": ["IMAGE"],
            },
            "ArbitraryNodeBeta": {
                "input": {
                    "required": {
                        "payload_b": [
                            ["", "creator-private.wav"],
                            {"audio_upload": True, "tooltip": "Choose an audio file."},
                        ],
                    }
                },
                "output": ["AUDIO"],
            },
            "ArbitraryNodeGamma": {
                "input": {
                    "required": {
                        "payload_c": [
                            ["", "creator-private.mp4"],
                            {"video_upload": True},
                        ],
                    }
                },
                "output": ["VIDEO"],
            },
            "ArbitraryNodeDelta": {
                "input": {
                    "required": {
                        "payload_d": [
                            ["", "creator-private.bin"],
                            {"file_upload": True},
                        ],
                    }
                },
                "output": ["CUSTOM_DATA"],
            },
        },
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["image"]["is_image_node"] is True
    assert input_nodes["image"]["inputs"] == [
        {
            "input_name": "payload_a",
            "current_value": "",
            "kind": "image_input",
            "suggested_widget_type": "load_image",
            "widget_types": ["load_image", "load_image_mask"],
            "hint": "Choose an image.",
        },
        {
            "input_name": "caption",
            "current_value": "Keep this control",
            "kind": "string",
            "suggested_widget_type": "textarea",
            "widget_types": ["textarea", "string_field"],
        },
    ]
    assert input_nodes["audio"]["is_audio_node"] is True
    assert input_nodes["audio"]["inputs"] == [
        {
            "input_name": "payload_b",
            "current_value": "",
            "kind": "audio_input",
            "suggested_widget_type": "load_audio",
            "widget_types": ["load_audio"],
            "hint": "Choose an audio file.",
        }
    ]
    assert input_nodes["video"]["inputs"][0]["kind"] == "video_input"
    assert input_nodes["video"]["inputs"][0]["suggested_widget_type"] == "load_video"
    assert input_nodes["file"]["inputs"][0]["kind"] == "file_input"
    assert input_nodes["file"]["inputs"][0]["suggested_widget_type"] == "load_file"


@pytest.mark.parametrize(
    ("node_type", "input_name"),
    [
        ("OptionalImageInput", "image"),
        ("OptionalAudioInput", "audio"),
        ("NoofyOptionalLoadImage", "image"),
        ("NoofyOptionalLoadAudio", "audio"),
    ],
)
def test_classify_graph_inputs_does_not_infer_media_from_optional_node_names(
    node_type: str,
    input_name: str,
) -> None:
    nodes = _classify_graph_inputs(
        {"1": {"class_type": node_type, "inputs": {input_name: ""}}}
    )

    assert nodes[0]["inputs"][0]["kind"] == "string"


def test_classify_graph_inputs_uses_packaged_upload_metadata_without_object_info() -> None:
    nodes = _classify_graph_inputs(
        {
            "42": {
                "class_type": "UnknownFutureNode",
                "inputs": {"payload": "", "preview_helper": ""},
            }
        },
        widget_metadata={
            "nodes": {
                "42": {
                    "inputs": {
                        "payload": {
                            "input_type": "COMBO",
                            "audio_upload": True,
                            "options": ["", "creator-private.wav"],
                        },
                        "preview_helper": {"input_type": "AUDIO_UI"},
                    }
                }
            }
        },
    )

    assert nodes[0]["node_type"] == "UnknownFutureNode"
    assert nodes[0]["is_audio_node"] is True
    assert nodes[0]["inputs"] == [
        {
            "input_name": "payload",
            "current_value": "",
            "kind": "audio_input",
            "suggested_widget_type": "load_audio",
            "widget_types": ["load_audio"],
        }
    ]


def test_classify_graph_inputs_hides_schema_hidden_custom_inputs() -> None:
    nodes = _classify_graph_inputs(
        {
            "12": {
                "class_type": "FutureNode",
                "inputs": {"source": "", "internal_token": "do-not-show"},
            }
        },
        widget_metadata={
            "nodes": {
                "12": {
                    "outputs": ["CUSTOM_DATA"],
                    "inputs": {
                        "source": {
                            "input_type": "COMBO",
                            "input_group": "required",
                            "file_upload": True,
                        },
                        "internal_token": {
                            "input_type": "STRING",
                            "input_group": "hidden",
                        },
                    },
                }
            }
        },
    )

    assert [item["input_name"] for item in nodes[0]["inputs"]] == ["source"]


def test_classify_graph_inputs_uses_packaged_output_contracts_offline() -> None:
    nodes = _classify_graph_inputs(
        {
            "source": {"class_type": "FutureGenerator", "inputs": {}},
            "preview": {
                "class_type": "PreviewAny",
                "inputs": {"source": ["source", 0]},
            },
        },
        widget_metadata={
            "nodes": {
                "source": {"outputs": ["AUDIO"]},
                "preview": {"outputs": ["ANY"]},
            }
        },
    )

    assert nodes[0]["node_id"] == "preview"
    assert nodes[0]["inputs"][0]["kind"] == "audio_output"


def test_classify_graph_inputs_recognizes_generic_3d_upload_from_output_contract() -> None:
    nodes = _classify_graph_inputs(
        {"mesh": {"class_type": "FutureAssetNode", "inputs": {"source": ""}}},
        widget_metadata={
            "nodes": {
                "mesh": {
                    "outputs": ["FILE_3D_GLB"],
                    "inputs": {
                        "source": {
                            "input_type": "COMBO",
                            "file_upload": True,
                        }
                    },
                }
            }
        },
    )

    assert nodes[0]["inputs"][0]["kind"] == "three_d_input"


def test_classify_graph_inputs_uses_preview_any_declared_string_output() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["2", 0], "vae": ["3", 0]},
            },
            "4": {
                "_meta": {"title": "Preview decoded image"},
                "class_type": "PreviewAny",
                "inputs": {"source": ["1", 0]},
            },
        },
        object_info={
            "PreviewAny": {"output": ["STRING"]},
            "VAEDecode": {"output": ["IMAGE"]},
        },
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["4"]["inputs"] == [
        {
            "input_name": "output_text",
            "current_value": None,
            "kind": "text_output",
            "suggested_widget_type": "display_text",
            "widget_types": ["display_text"],
            "auto_select": True,
        }
    ]


def test_classify_graph_inputs_infers_ambiguous_preview_any_image_output_from_source_object_info() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "VAEDecode",
                "inputs": {"samples": ["2", 0], "vae": ["3", 0]},
            },
            "4": {
                "_meta": {"title": "Preview decoded image"},
                "class_type": "PreviewAny",
                "inputs": {"source": ["1", 0]},
            },
        },
        object_info={
            "PreviewAny": {"output": ["ANY"]},
            "VAEDecode": {"output": ["IMAGE"]},
        },
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["4"]["inputs"] == [
        {
            "input_name": "output_image",
            "current_value": None,
            "kind": "image_output",
            "suggested_widget_type": "display_image",
            "widget_types": ["display_image"],
            "auto_select": True,
        }
    ]


def test_classify_graph_inputs_infers_ambiguous_preview_any_audio_output_from_source_object_info() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "AudioPreviewSource",
                "inputs": {"seed": ["2", 0]},
            },
            "4": {
                "class_type": "PreviewAny",
                "inputs": {"source": ["1", 0]},
            },
        },
        object_info={
            "AudioPreviewSource": {"output": ["AUDIO"]},
            "PreviewAny": {"output": ["ANY"]},
        },
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["4"]["inputs"] == [
        {
            "input_name": "output_audio",
            "current_value": None,
            "kind": "audio_output",
            "suggested_widget_type": "display_audio",
            "widget_types": ["display_audio"],
            "auto_select": True,
        }
    ]


def test_classify_graph_inputs_falls_back_to_ambiguous_preview_any_source_node_type() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": ["2", 0]},
            },
            "4": {
                "class_type": "PreviewAny",
                "inputs": {"source": ["1", 0]},
            },
        },
        object_info={"PreviewAny": {"output": ["ANY"]}},
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["4"]["inputs"][0]["kind"] == "image_output"
    assert input_nodes["4"]["inputs"][0]["widget_types"] == ["display_image"]


def test_classify_graph_inputs_includes_video_widgets_and_custom_binding_hint() -> None:
    nodes = _classify_graph_inputs(
        {
            "30": {
                "class_type": "LoadVideo",
                "inputs": {"file": "creator-local-video.mp4"},
            },
            "31": {
                "class_type": "VHS_LoadVideoPath",
                "inputs": {"video_path": "creator-local-video.mkv"},
            },
            "32": {
                "class_type": "SaveVideo",
                "inputs": {"video": ["30", 0], "filename_prefix": "clip"},
            },
        }
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["30"]["inputs"][0]["kind"] == "video_input"
    assert input_nodes["30"]["inputs"][0]["widget_types"] == ["load_video"]
    assert input_nodes["31"]["inputs"][0]["kind"] == "video_input"
    assert input_nodes["31"]["inputs"][0]["input_name"] == "video_path"
    assert input_nodes["32"]["inputs"][0] == {
        "input_name": "output_video",
        "current_value": None,
        "kind": "video_output",
        "suggested_widget_type": "display_video",
        "widget_types": ["display_video"],
        "auto_select": True,
    }


def test_classify_graph_inputs_includes_three_d_widgets() -> None:
    nodes = _classify_graph_inputs(
        {
            "40": {"class_type": "Load3D", "inputs": {"model_file": "creator-local-model.glb"}},
            "41": {"class_type": "SaveGLB", "inputs": {"mesh": ["40", 0], "filename_prefix": "mesh"}},
        }
    )

    input_nodes = {node["node_id"]: node for node in nodes}
    assert input_nodes["40"]["inputs"][0]["kind"] == "three_d_input"
    assert input_nodes["40"]["inputs"][0]["widget_types"] == ["load_3d"]
    assert input_nodes["41"]["inputs"][0] == {
        "input_name": "output_3d",
        "current_value": None,
        "kind": "three_d_output",
        "suggested_widget_type": "display_3d",
        "widget_types": ["display_3d"],
        "auto_select": False,
    }


def test_classify_graph_inputs_does_not_treat_generic_model_loaders_as_three_d() -> None:
    nodes = _classify_graph_inputs(
        {
            "40": {"class_type": "ModelLoader", "inputs": {"model": "checkpoint.safetensors"}},
        }
    )

    assert nodes[0]["inputs"][0]["kind"] != "three_d_input"


def test_classify_graph_inputs_auto_selects_deepest_image_output() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
            "4": {"class_type": "ImageScale", "inputs": {"image": ["2", 0]}},
        }
    )

    output_auto_select = {
        node["node_id"]: node["inputs"][0]["auto_select"]
        for node in nodes
        if node["inputs"] and node["inputs"][0]["kind"] == "image_output"
    }

    assert output_auto_select == {"1": False, "3": True}


def test_classify_graph_inputs_tie_breaks_image_output_by_graph_order() -> None:
    nodes = _classify_graph_inputs(
        {
            "save_a": {"class_type": "SaveImage", "inputs": {"images": ["decode", 0]}},
            "decode": {"class_type": "VAEDecode", "inputs": {"samples": ["latent", 0]}},
            "preview_b": {"class_type": "PreviewImage", "inputs": {"images": ["decode", 0]}},
        }
    )

    output_auto_select = {
        node["node_id"]: node["inputs"][0]["auto_select"]
        for node in nodes
        if node["inputs"] and node["inputs"][0]["kind"] == "image_output"
    }

    assert output_auto_select == {"save_a": False, "preview_b": True}


def test_classify_graph_inputs_ignores_non_link_lists_when_ordering_outputs() -> None:
    nodes = _classify_graph_inputs(
        {
            "save_a": {"class_type": "SaveImage", "inputs": {"images": ["base", 0]}},
            "preview_b": {"class_type": "PreviewImage", "inputs": {"images": ["base", 0]}},
            "base": {"class_type": "CustomNode", "inputs": {"metadata": ["preview_b", "not-a-link"]}},
        }
    )

    output_auto_select = {
        node["node_id"]: node["inputs"][0]["auto_select"]
        for node in nodes
        if node["inputs"] and node["inputs"][0]["kind"] == "image_output"
    }

    assert output_auto_select == {"save_a": False, "preview_b": True}


def test_get_bindable_inputs_keeps_load_image_as_upload_when_object_info_has_file_options() -> None:
    nodes = _classify_graph_inputs(
        {
            "10": {
                "class_type": "LoadImage",
                "inputs": {"image": "creator-local-input.png"},
            },
        },
        object_info={
            "LoadImage": {
                "input": {
                    "required": {
                        "image": [
                            ["creator-local-input.png", "other.png"],
                            {"tooltip": "Image to open."},
                        ],
                    }
                }
            }
        },
    )

    assert nodes[0]["inputs"][0] == {
        "input_name": "image",
        "current_value": "creator-local-input.png",
        "kind": "image_input",
        "suggested_widget_type": "load_image",
        "widget_types": ["load_image", "load_image_mask"],
        "hint": "Image to open.",
    }


def test_get_bindable_inputs_uses_object_info_options_for_dropdowns(tmp_path: Path) -> None:
    del tmp_path
    graph = {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 5,
                "steps": 20,
                "cfg": 8,
                "sampler_name": "euler",
                "scheduler": "normal",
                "positive": ["6", 0],
                "negative": ["7", 0],
                "model": ["4", 0],
                "latent_image": ["5", 0],
            },
        }
    }
    nodes = _classify_graph_inputs(
        graph,
        object_info={
            "KSampler": {
                "input": {
                    "required": {
                        "sampler_name": [
                            ["euler", "euler_ancestral"],
                            {"tooltip": "The algorithm used when sampling."},
                        ],
                        "scheduler": [
                            ["normal", "karras"],
                            {"tooltip": "The scheduler controls denoising."},
                        ],
                    }
                }
            }
        },
    )

    ksampler = nodes[0]
    inputs_by_name = {inp["input_name"]: inp for inp in ksampler["inputs"]}
    assert inputs_by_name["sampler_name"] == {
        "input_name": "sampler_name",
        "current_value": "euler",
        "kind": "select",
        "suggested_widget_type": "select",
        "widget_types": ["select", "string_field"],
        "options": ["euler", "euler_ancestral"],
        "hint": "The algorithm used when sampling.",
    }
    assert inputs_by_name["scheduler"]["options"] == ["normal", "karras"]


def test_get_bindable_inputs_uses_modern_combo_metadata_for_custom_node_dropdowns() -> None:
    nodes = _classify_graph_inputs(
        {
            "12": {
                "class_type": "CustomStyleSelector",
                "inputs": {
                    "style": "cinematic",
                },
            }
        },
        object_info={
            "CustomStyleSelector": {
                "input": {
                    "required": {
                        "style": [
                            "COMBO",
                            {
                                "options": ["cinematic", "illustration", "cinematic"],
                                "display_name": "Rendering style",
                                "tooltip": "Choose the custom-node rendering style.",
                            },
                        ]
                    }
                }
            }
        },
    )

    assert nodes[0]["inputs"][0] == {
        "input_name": "style",
        "current_value": "cinematic",
        "kind": "select",
        "suggested_widget_type": "select",
        "widget_types": ["select", "string_field"],
        "options": ["cinematic", "illustration"],
        "hint": "Choose the custom-node rendering style.",
        "suggested_label": "Rendering style",
    }


def test_get_bindable_inputs_uses_exported_frontend_widget_dropdown_fallback() -> None:
    nodes = _classify_graph_inputs(
        {
            "12": {
                "class_type": "CustomFrontendSelector",
                "inputs": {"style": "cinematic"},
            }
        },
        object_info={
            "CustomFrontendSelector": {
                "input": {"required": {"style": ["STRING", {"tooltip": "Live tooltip."}]}}
            }
        },
        widget_metadata={
            "schema_version": "0.1.0",
            "nodes": {
                "12": {
                    "inputs": {
                        "style": {
                            "options": ["cinematic", "illustration"],
                            "display_name": "Rendering style",
                            "tooltip": "Exported tooltip.",
                        }
                    }
                }
            },
        },
    )

    assert nodes[0]["inputs"][0] == {
        "input_name": "style",
        "current_value": "cinematic",
        "kind": "select",
        "suggested_widget_type": "select",
        "widget_types": ["select", "string_field"],
        "options": ["cinematic", "illustration"],
        "hint": "Live tooltip.",
        "suggested_label": "Rendering style",
    }


def test_get_bindable_inputs_prefers_live_dropdown_options_over_export_snapshot() -> None:
    nodes = _classify_graph_inputs(
        {
            "12": {
                "class_type": "CustomFrontendSelector",
                "inputs": {"style": "live"},
            }
        },
        object_info={
            "CustomFrontendSelector": {
                "input": {
                    "required": {
                        "style": [
                            "COMBO",
                            {"options": ["live", "new"], "display_name": "Live style"},
                        ]
                    }
                }
            }
        },
        widget_metadata={
            "nodes": {
                "12": {
                    "inputs": {
                        "style": {
                            "options": ["old", "snapshot"],
                            "display_name": "Snapshot style",
                        }
                    }
                }
            }
        },
    )

    assert nodes[0]["inputs"][0]["options"] == ["live", "new"]
    assert nodes[0]["inputs"][0]["suggested_label"] == "Live style"


def test_get_bindable_inputs_suggests_generic_file_only_for_strong_file_signals() -> None:
    nodes = _classify_graph_inputs(
        {
            "1": {
                "class_type": "DocumentLoader",
                "inputs": {"file_path": "notes.json"},
            },
            "2": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"filename": "model.safetensors"},
            },
        }
    )

    by_node = {node["node_id"]: node for node in nodes}
    assert by_node["1"]["inputs"][0]["kind"] == "file_input"
    assert by_node["1"]["inputs"][0]["suggested_widget_type"] == "load_file"
    assert by_node["2"]["inputs"][0]["kind"] == "string"
    assert by_node["2"]["inputs"][0]["suggested_widget_type"] != "load_file"


@pytest.mark.parametrize("node_type", ["LoraLoader", "LoraLoaderModelOnly"])
def test_get_bindable_inputs_keeps_comfyui_lora_nodes_as_lora_loader_when_options_exist(
    node_type: str,
) -> None:
    graph = {
        "12": {
            "class_type": node_type,
            "inputs": {
                "model": ["4", 0],
                "clip": ["4", 1],
                "lora_name": "None",
                "strength_model": 1.0,
            },
        }
    }
    nodes = _classify_graph_inputs(
        graph,
        object_info={
            node_type: {
                "input": {
                    "required": {
                        "lora_name": [
                            ["None", "cinematic.safetensors"],
                            {"tooltip": "The LoRA model to load."},
                        ]
                    }
                }
            }
        },
    )

    lora_node = nodes[0]
    inputs_by_name = {inp["input_name"]: inp for inp in lora_node["inputs"]}
    assert lora_node["node_type"] == node_type
    assert lora_node["is_lora_node"] is True
    assert inputs_by_name["lora_name"] == {
        "input_name": "lora_name",
        "current_value": "None",
        "kind": "lora",
        "suggested_widget_type": "lora_loader",
        "widget_types": ["lora_loader"],
        "options": ["None", "cinematic.safetensors"],
        "hint": "The LoRA model to load.",
    }


def test_get_bindable_inputs_filters_model_options_dynamically(tmp_path: Path) -> None:
    packages_dir = tmp_path / "packages"
    package_dir = packages_dir / "authoring_filter"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": "authoring_filter", "name": "Authoring Filter", "version": "1.0.0"},
                "engine": "comfyui",
                "required_models": [
                    {
                        "folder": "checkpoints",
                        "filename": "base-sdxl.safetensors",
                        "node_id": "4",
                        "node_type": "CheckpointLoaderSimple",
                        "input_name": "ckpt_name",
                        "model_type": "checkpoint",
                        "architecture_family": "sdxl",
                    }
                ],
                "comfyui_graph": {
                    "4": {
                        "class_type": "CheckpointLoaderSimple",
                        "inputs": {"ckpt_name": "base-sdxl.safetensors"},
                    }
                },
                "inputs": [],
                "outputs": [],
                "dashboard": {"version": "0.1.0", "status": "not_configured", "sections": []},
            }
        ),
        encoding="utf-8",
    )
    loader = WorkflowPackageLoader(packages_dir)
    service = DashboardAuthoringService(
        workflow_store_dir=packages_dir,
        workflow_loader=loader,
        log_store=LogStore(),
    )

    result = service.get_bindable_inputs(
        "authoring_filter",
        object_info={
            "CheckpointLoaderSimple": {
                "input": {
                    "required": {
                        "ckpt_name": [
                            ["base-sdxl.safetensors", "DreamshaperXL.safetensors", "FLUX.dev.safetensors"],
                            {"tooltip": "Checkpoint to load."},
                        ]
                    }
                }
            }
        },
    )

    ckpt_input = result["nodes"][0]["inputs"][0]
    assert ckpt_input["options"] == ["base-sdxl.safetensors", "DreamshaperXL.safetensors"]
    assert ckpt_input["architecture_filter"]["hidden_options"] == ["FLUX.dev.safetensors"]
    assert loader.get_package("authoring_filter").inputs == []


def test_save_dashboard_for_bundled_workflow_writes_user_override(tmp_path: Path) -> None:
    """Bundled source files stay read-only, but users can customize dashboards."""
    bundled_root = Path("app/workflows/packages")
    overrides_dir = tmp_path / "dashboard-overrides"
    loader = WorkflowPackageLoader(bundled_root, dashboard_overrides_dir=overrides_dir)
    service = DashboardAuthoringService(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        log_store=LogStore(),
        dashboard_overrides_dir=overrides_dir,
    )

    # Build a valid payload against text_to_image_v0's actual graph nodes.
    # Node "6" is CLIPTextEncode (positive prompt) in the bundled workflow.
    pkg = loader.get_package("text_to_image_v0")
    graph_node_ids = list(pkg.comfyui_graph.keys())
    first_node = graph_node_ids[0]
    first_node_data = pkg.comfyui_graph[first_node]
    scalar_inputs = [
        k
        for k, v in first_node_data.get("inputs", {}).items()
        if not isinstance(v, list)
    ]
    input_name = scalar_inputs[0] if scalar_inputs else "text"

    inputs = [
        {
            "id": "prompt",
            "label": "Prompt",
            "control": "textarea",
            "binding": {"node_id": first_node, "input_name": input_name},
            "default": "",
            "validation": {},
        }
    ]
    dashboard = {
        "version": "0.1.0",
        "status": "not_configured",
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {
                        "id": "c1",
                        "type": "textarea",
                        "label": "P",
                        "input_id": "prompt",
                    },
                ],
            }
        ],
    }

    bundled_package_dir = bundled_root / "text_to_image_v0"
    bundled_package_json_before = (bundled_package_dir / "package.json").read_bytes()
    bundled_dashboard_json_before = (bundled_package_dir / "dashboard.json").read_bytes()

    result = service.save_dashboard("text_to_image_v0", inputs, dashboard)

    assert result["status"] == "configured"
    assert (bundled_package_dir / "package.json").read_bytes() == bundled_package_json_before
    assert (bundled_package_dir / "dashboard.json").read_bytes() == bundled_dashboard_json_before

    override_file = overrides_dir / "text_to_image_v0" / "dashboard.json"
    assert override_file.exists()
    saved = json.loads(override_file.read_text(encoding="utf-8"))
    assert saved["status"] == "configured"
    assert saved["sections"][0]["controls"][0]["label"] == "P"

    reloaded = WorkflowPackageLoader(
        bundled_root,
        dashboard_overrides_dir=overrides_dir,
    ).get_package("text_to_image_v0")
    assert reloaded.dashboard.sections[0].controls[0].label == "P"

    reset = service.reset_dashboard_override("text_to_image_v0")
    assert reset["removed"] is True
    assert not override_file.exists()


def test_reset_dashboard_override_recovers_from_unreadable_override(tmp_path: Path) -> None:
    bundled_root = Path("app/workflows/packages")
    overrides_dir = tmp_path / "dashboard-overrides"
    override_dir = overrides_dir / "text_to_image_v0"
    override_dir.mkdir(parents=True)
    override_file = override_dir / "dashboard.json"
    override_file.write_text("{", encoding="utf-8")
    service = DashboardAuthoringService(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=WorkflowPackageLoader(
            bundled_root,
            dashboard_overrides_dir=overrides_dir,
        ),
        log_store=LogStore(),
        dashboard_overrides_dir=overrides_dir,
    )

    result = service.reset_dashboard_override("text_to_image_v0")

    assert result["removed"] is True
    assert not override_file.exists()
