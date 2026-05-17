"""Tests for DashboardAuthoringService.

Verifies:
- PUT writes only dashboard.json (package.json bytes unchanged)
- PUT transitions dashboard status to 'configured'
- PUT rejects invalid bindings
- validate does not persist anything
- bindable-inputs works without a runner
"""

from __future__ import annotations

import json
import zipfile
import io
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
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_archive(
    graph: dict[str, Any] | None = None,
    dashboard: dict[str, Any] | None = None,
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

    # package.json import_metadata.status must be promoted to "imported".
    package_after = json.loads(package_json_path.read_text())
    assert (
        package_after.get("import_metadata", {}).get("status") == "imported"
    ), "save_dashboard must promote import_metadata.status to 'imported'"
    # All other package.json fields must remain structurally unchanged.
    before = json.loads(package_json_bytes_before)
    assert package_after.get("engine") == before.get("engine")
    assert package_after.get("comfyui_graph") == before.get("comfyui_graph")
    assert package_after.get("identity") == before.get("identity")

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
        }
    ]


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


def test_save_dashboard_rejects_bundled_workflow(tmp_path: Path) -> None:
    """Bundled workflows are read-only — save must raise DashboardAuthoringError."""
    loader = WorkflowPackageLoader(Path("app/workflows/packages"))
    service = DashboardAuthoringService(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        log_store=LogStore(),
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

    with pytest.raises(DashboardAuthoringError, match="read-only"):
        service.save_dashboard("text_to_image_v0", inputs, dashboard)
