from __future__ import annotations

import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "noofy_exporter.py"
SPEC = importlib.util.spec_from_file_location("noofy_exporter_test_module", MODULE_PATH)
assert SPEC is not None
exporter = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = exporter
SPEC.loader.exec_module(exporter)


def test_prepare_graph_for_export_preserves_load_image_inputs_without_mutating_original() -> None:
    prompt = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": "creator-image.png"},
        },
        "2": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 4},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {"seed": 12345, "noise_seed": 67890},
        },
    }

    graph, adjustments = exporter.prepare_graph_for_export(prompt)

    assert graph["1"]["inputs"]["image"] == "creator-image.png"
    assert graph["2"]["inputs"]["batch_size"] == 1
    assert graph["3"]["inputs"]["seed"] == 12345
    assert graph["3"]["inputs"]["noise_seed"] == 67890
    assert adjustments == {"image_inputs_preserved": 1, "batch_size_inputs": 1}
    assert prompt["1"]["inputs"]["image"] == "creator-image.png"
    assert prompt["2"]["inputs"]["batch_size"] == 4


def test_build_export_filename_uses_only_package_id() -> None:
    assert exporter.build_export_filename("eraserv4.5") == "eraserv4.5.noofy"


def test_detect_model_references_hashes_existing_models(tmp_path: Path) -> None:
    model = tmp_path / "checkpoints" / "model.safetensors"
    model.parent.mkdir()
    model.write_bytes(b"fake model")

    def resolve(folder: str, filename: str) -> str | None:
        path = tmp_path / folder / filename
        return str(path) if path.exists() else None

    prompt = {
        "12": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        }
    }

    records = exporter.detect_model_references(prompt, resolve)

    assert records == [
        {
            "node_id": "12",
            "input_name": "ckpt_name",
            "node_type": "CheckpointLoaderSimple",
            "model_type": "checkpoint",
            "comfyui_folder": "checkpoints",
            "filename": "model.safetensors",
            "sha256": hashlib.sha256(b"fake model").hexdigest(),
            "size_bytes": len(b"fake model"),
            "verification_level": "sha256_size",
            "identity_verified_by_exporter": True,
            "local_file_available_at_export": True,
            "bundled": False,
            "asset_ownership": "external_reference",
            "identity_warnings": [],
            "source_urls": [],
        }
    ]


def test_detect_model_references_marks_unresolved_models_as_filename_only() -> None:
    prompt = {
        "12": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "missing.safetensors"},
        }
    }

    records = exporter.detect_model_references(prompt, lambda _folder, _filename: None)

    assert records[0]["filename"] == "missing.safetensors"
    assert records[0]["sha256"] is None
    assert records[0]["size_bytes"] is None
    assert records[0]["verification_level"] == "filename_only"
    assert records[0]["identity_verified_by_exporter"] is False
    assert records[0]["local_file_available_at_export"] is False
    assert records[0]["bundled"] is False
    assert records[0]["asset_ownership"] == "external_reference"
    assert records[0]["identity_warnings"] == [
        "ComfyUI did not resolve this model file at export time."
    ]
    assert exporter.collect_model_warnings(records) == [
        "Model missing.safetensors: ComfyUI did not resolve this model file at export time."
    ]


def test_detect_model_references_keeps_size_when_hash_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "checkpoints" / "model.safetensors"
    model.parent.mkdir()
    model.write_bytes(b"fake model")

    def fail_hash(_path: Path) -> str:
        raise OSError("locked")

    monkeypatch.setattr(exporter, "sha256_file", fail_hash)

    records = exporter.detect_model_references(
        {
            "12": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {"ckpt_name": "model.safetensors"},
            }
        },
        lambda folder, filename: str(tmp_path / folder / filename),
    )

    assert records[0]["sha256"] is None
    assert records[0]["size_bytes"] == len(b"fake model")
    assert records[0]["verification_level"] == "filename_size"
    assert records[0]["identity_verified_by_exporter"] is False
    assert records[0]["local_file_available_at_export"] is True
    assert records[0]["identity_warnings"] == [
        "Could not hash model file at export time: locked"
    ]


def test_detect_custom_nodes_resolves_file_based_custom_node(tmp_path: Path) -> None:
    custom_nodes_dir = tmp_path / "custom_nodes"
    custom_nodes_dir.mkdir()
    custom_node_file = custom_nodes_dir / "single_file_node.py"
    custom_node_file.write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")

    class SingleFileNode:
        RELATIVE_PYTHON_MODULE = "custom_nodes.single_file_node"

    class NodesModule:
        NODE_CLASS_MAPPINGS = {"SingleFileNode": SingleFileNode}
        LOADED_MODULE_DIRS = {str(custom_node_file.with_suffix("")): str(custom_nodes_dir)}

    records = exporter.detect_custom_nodes(
        {"1": {"class_type": "SingleFileNode", "inputs": {}}},
        NodesModule,
    )

    assert len(records) == 1
    assert records[0].folder_name == "single_file_node"
    assert records[0].source_path == str(custom_node_file.resolve())
    assert records[0].included is True


def test_custom_node_manifest_and_zip_exclude_runtime_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "custom_nodes" / "my_node_pack"
    (source / ".git").mkdir(parents=True)
    (source / "__pycache__").mkdir()
    (source / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    (source / "requirements.txt").write_text("numpy\n", encoding="utf-8")
    (source / "install.py").write_text("print('do not run')\n", encoding="utf-8")
    (source / ".git" / "config").write_text("ignored\n", encoding="utf-8")
    (source / "__pycache__" / "mod.pyc").write_bytes(b"ignored")
    (source / "large.safetensors").write_bytes(b"ignored")

    record = exporter.CustomNodeRecord(
        id="my-node-pack",
        folder_name="my_node_pack",
        source_path=str(source),
        node_types=["CustomNode"],
    )
    exporter.collect_custom_node_manifest(record)

    included_paths = {item["path"] for item in record.file_manifest}
    assert included_paths == {"__init__.py", "install.py", "requirements.txt"}
    assert record.requirements_files == ["requirements.txt"]
    assert record.has_install_py is True
    assert record.included is True
    assert record.excluded_count == 1

    graph = {"1": {"class_type": "CustomNode", "inputs": {}}}
    runtime = exporter.RuntimeMetadata(
        comfyui_version="1.2.3",
        python_version="3.12.0",
        platform_name="darwin",
        gpu_backend="mps",
        gpu_name="mps",
    )
    hardware = exporter.MemoryObservation(
        observed_peak_vram_mb=None,
        observed_peak_ram_mb=2048,
        gpu_name="mps",
        backend="mps",
    )
    documents = exporter.build_package_documents(
        graph=graph,
        workflow_name="Test Workflow",
        runtime=runtime,
        custom_nodes=[record],
        models=[],
        hardware=hardware,
        started_at="2026-04-30T00:00:00Z",
        finished_at="2026-04-30T00:01:00Z",
        duration_seconds=60,
        graph_adjustments={"image_inputs_preserved": 0, "batch_size_inputs": 0},
        warnings=exporter.flatten_warnings([record], []),
    )

    target = tmp_path / "workflow.noofy"
    exporter.write_noofy_package(
        target_path=target,
        graph=graph,
        documents=documents,
        custom_nodes=[record],
        thumbnail_bytes=b"thumbnail",
    )

    with zipfile.ZipFile(target) as package:
        names = set(package.namelist())

    assert "package.json" in names
    assert "comfyui_graph.json" in names
    assert "dashboard.json" in names
    assert "capsule.lock.json" in names
    assert "export-report.json" in names
    assert "assets/thumbnail.png" in names
    assert "custom_nodes/my_node_pack/__init__.py" in names
    assert "custom_nodes/my_node_pack/requirements.txt" in names
    assert "custom_nodes/my_node_pack/install.py" in names
    assert "custom_nodes/my_node_pack/large.safetensors" not in names
