"""Round-trip persistence tests for dashboard authoring.

Verifies:
- Load a package, mutate dashboard, save, reload — graph + identity bytes unchanged.
- dashboard.json changes are visible after reload.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from app.diagnostics import LogStore
from app.workflows.authoring import DashboardAuthoringService
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator


_GRAPH = {
    "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "hi", "clip": ["4", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "model.safetensors"}},
    "9": {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": "out"}},
}


def _make_archive() -> bytes:
    package = {
        "schema_version": "0.5.0",
        "engine": "comfyui",
        "metadata": {"id": "persist_wf", "name": "Persist Test", "version": "1.0.0"},
        "publisher_id": "persist_pub",
        "package_id": "persist_wf",
        "version": "1.0.0",
        "required_models": [],
        "custom_nodes": [],
    }
    capsule = {
        "schema_version": "0.5.0",
        "capsule_id": "persist_wf",
        "source_policy": "quarantined_community",
        "custom_nodes": [],
        "dependency_lock": {"packages": []},
        "graph_hash": "aaa",
        "dependency_env_hash": "bbb",
        "runner_workspace_hash": "ccc",
    }
    stub_dashboard = {
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
        zf.writestr("dashboard.json", json.dumps(stub_dashboard))
    return buf.getvalue()


def _setup(tmp_path: Path):
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    pkg = store.import_archive(_make_archive(), original_filename="persist.noofy")
    workflow_id = pkg.metadata.id

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
    return service, loader, workflow_id


def test_round_trip_graph_and_identity_unchanged(tmp_path: Path) -> None:
    service, loader, workflow_id = _setup(tmp_path)

    packages_root = tmp_path / "packages"
    package_json_path = next(packages_root.rglob("package.json"))
    graph_json_path = next(packages_root.rglob("comfyui_graph.json"))

    package_bytes_before = package_json_path.read_bytes()
    graph_bytes_before = graph_json_path.read_bytes()

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
                    {"id": "c1", "type": "textarea", "label": "P", "input_id": "prompt"},
                    {"id": "c2", "type": "result_image", "label": "R", "output_id": "image_out"},
                ],
            }
        ],
    }

    service.save_dashboard(workflow_id, inputs, dashboard)

    # Reload.
    loader2 = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    pkg = loader2.get_package(workflow_id)

    # comfyui_graph.json must be byte-for-byte unchanged.
    assert graph_json_path.read_bytes() == graph_bytes_before

    # package.json identity/graph fields must be structurally unchanged,
    # but import_metadata.status is promoted to "imported" after save.
    pkg_before = json.loads(package_bytes_before)
    pkg_after = json.loads(package_json_path.read_bytes())
    assert pkg_after.get("engine") == pkg_before.get("engine")
    assert pkg_after.get("identity") == pkg_before.get("identity")
    assert pkg_after.get("import_metadata", {}).get("status") == "imported"

    # Dashboard changes are visible.
    assert pkg.dashboard.status == "configured"
    assert any(i.id == "prompt" for i in pkg.inputs)


def test_round_trip_dashboard_inputs_survive_reload(tmp_path: Path) -> None:
    service, loader, workflow_id = _setup(tmp_path)

    inputs = [
        {
            "id": "seed",
            "label": "Seed",
            "control": "seed_widget",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": 42,
            "validation": {"min": 0, "max": 999999},
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
                    {"id": "c_seed", "type": "seed_widget", "label": "Seed", "input_id": "seed"},
                ],
            }
        ],
    }

    service.save_dashboard(workflow_id, inputs, dashboard)

    loader2 = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    pkg = loader2.get_package(workflow_id)
    seed_input = next((i for i in pkg.inputs if i.id == "seed"), None)
    assert seed_input is not None
    assert seed_input.default == 42
    assert seed_input.validation == {"min": 0, "max": 999999}


def test_loader_recovers_imported_dashboard_inputs_from_source_files(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "unknown" / "recovered" / "0.1.0"
    (package_dir / "source-files").mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": "unknown__recovered__0.1.0", "name": "Recovered", "version": "0.1.0"},
                "engine": "comfyui",
                "required_models": [],
                "comfyui_graph": _GRAPH,
                "custom_nodes": [],
            }
        ),
        encoding="utf-8",
    )
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [],
        "outputs": [],
        "sections": [
            {
                "id": "main",
                "title": "Controls",
                "controls": [
                    {"id": "prompt", "type": "textarea", "label": "Prompt", "input_id": "prompt"},
                ],
            }
        ],
    }
    source_dashboard = {
        **dashboard,
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
    }
    (package_dir / "dashboard.json").write_text(json.dumps(dashboard), encoding="utf-8")
    (package_dir / "source-files" / "dashboard.json").write_text(json.dumps(source_dashboard), encoding="utf-8")

    loader = WorkflowPackageLoader(Path("missing-bundled"), imported_packages_dir=tmp_path / "packages")
    package = loader.get_package("unknown__recovered__0.1.0")

    assert [item.id for item in package.inputs] == ["prompt"]
