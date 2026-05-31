from __future__ import annotations

import hashlib
import importlib.util
import json
import os
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


def test_redact_local_inputs_for_package_removes_creator_image_references() -> None:
    graph = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": "creator-portrait.png", "upload": "image"},
        },
        "2": {
            "class_type": "LoadImageMask",
            "inputs": {"image": "private-mask.png", "channel": "alpha"},
        },
        "3": {
            "class_type": "PreviewImage",
            "inputs": {"images": ["1", 0]},
        },
    }

    package_graph, adjustments, unresolved = exporter.redact_local_inputs_for_package(graph)

    assert package_graph["1"]["inputs"]["image"] == exporter.REDACTED_IMAGE_INPUT_VALUE
    assert package_graph["1"]["inputs"]["upload"] == "image"
    assert package_graph["2"]["inputs"]["image"] == exporter.REDACTED_IMAGE_INPUT_VALUE
    assert package_graph["2"]["inputs"]["channel"] == "alpha"
    assert package_graph["3"]["inputs"]["images"] == ["1", 0]
    assert adjustments["image_inputs_redacted"] == 2
    assert [item["expected_kind"] for item in unresolved] == ["image", "image"]
    assert graph["1"]["inputs"]["image"] == "creator-portrait.png"
    assert graph["2"]["inputs"]["image"] == "private-mask.png"


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


def test_detect_model_references_resolves_latent_upscale_model_folder(tmp_path: Path) -> None:
    model = tmp_path / "latent_upscale_models" / "ltx-spatial-upscaler.safetensors"
    model.parent.mkdir()
    model.write_bytes(b"latent upscale model")

    def resolve(folder: str, filename: str) -> str | None:
        path = tmp_path / folder / filename
        return str(path) if path.exists() else None

    records = exporter.detect_model_references(
        {
            "12": {
                "class_type": "LatentUpscaleModelLoader",
                "inputs": {"model_name": model.name},
            }
        },
        resolve,
    )

    assert len(records) == 1
    assert records[0]["comfyui_folder"] == "latent_upscale_models"
    assert records[0]["sha256"] == hashlib.sha256(b"latent upscale model").hexdigest()
    assert records[0]["size_bytes"] == len(b"latent upscale model")
    assert records[0]["verification_level"] == "sha256_size"
    assert records[0]["identity_verified_by_exporter"] is True
    assert records[0]["local_file_available_at_export"] is True


def test_detect_model_references_reuses_cached_hash_for_same_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = tmp_path / "checkpoints" / "model.safetensors"
    model.parent.mkdir()
    model.write_bytes(b"fake model")
    prompt = {
        "12": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        }
    }

    def resolve(folder: str, filename: str) -> str | None:
        return str(tmp_path / folder / filename)

    cache_path = tmp_path / "cache" / "model_hash_cache.json"
    first_cache = exporter.ModelHashCache(cache_path)
    expected_hash = hashlib.sha256(b"fake model").hexdigest()

    first_records = exporter.detect_model_references(prompt, resolve, hash_cache=first_cache)
    assert first_records[0]["sha256"] == expected_hash

    def fail_hash(_path: Path) -> str:
        raise AssertionError("cached model hash was not reused")

    monkeypatch.setattr(exporter, "sha256_file", fail_hash)
    second_cache = exporter.ModelHashCache(cache_path)
    second_records = exporter.detect_model_references(prompt, resolve, hash_cache=second_cache)

    assert second_records[0]["sha256"] == expected_hash
    assert second_records[0]["verification_level"] == "sha256_size"
    assert second_records[0]["identity_verified_by_exporter"] is True


def test_detect_model_references_does_not_reuse_cache_for_same_filename_at_different_path(
    tmp_path: Path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_model = first_root / "checkpoints" / "model.safetensors"
    second_model = second_root / "checkpoints" / "model.safetensors"
    first_model.parent.mkdir(parents=True)
    second_model.parent.mkdir(parents=True)
    first_model.write_bytes(b"first model")
    second_model.write_bytes(b"second model")
    prompt = {
        "12": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        }
    }
    cache = exporter.ModelHashCache(tmp_path / "cache" / "model_hash_cache.json")

    def resolve_first(folder: str, filename: str) -> str | None:
        return str(first_root / folder / filename)

    def resolve_second(folder: str, filename: str) -> str | None:
        return str(second_root / folder / filename)

    first_records = exporter.detect_model_references(prompt, resolve_first, hash_cache=cache)
    second_records = exporter.detect_model_references(prompt, resolve_second, hash_cache=cache)

    assert first_records[0]["filename"] == second_records[0]["filename"]
    assert first_records[0]["sha256"] == hashlib.sha256(b"first model").hexdigest()
    assert second_records[0]["sha256"] == hashlib.sha256(b"second model").hexdigest()


def test_parallel_model_hashing_preserves_order_and_matches_serial_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "checkpoints" / "first.safetensors"
    second = tmp_path / "checkpoints" / "second.safetensors"
    first.parent.mkdir()
    first.write_bytes(b"first model")
    second.write_bytes(b"second model")
    prompt = {
        "20": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "second.safetensors"}},
        "10": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "first.safetensors"}},
    }

    def resolve(folder: str, filename: str) -> str | None:
        return str(tmp_path / folder / filename)

    monkeypatch.setenv(exporter.MODEL_HASH_CONCURRENCY_ENV, "4")
    monkeypatch.setattr(exporter, "verification_filesystem_downgrade_reason", lambda _paths: None)
    parallel_metrics = exporter.VerifyHashMetrics()
    parallel_records = exporter.detect_model_references(prompt, resolve, metrics=parallel_metrics)

    monkeypatch.setenv(exporter.MODEL_HASH_CONCURRENCY_ENV, "1")
    serial_records = exporter.detect_model_references(prompt, resolve)

    assert [record["filename"] for record in parallel_records] == [
        "first.safetensors",
        "second.safetensors",
    ]
    assert [record["sha256"] for record in parallel_records] == [
        hashlib.sha256(b"first model").hexdigest(),
        hashlib.sha256(b"second model").hexdigest(),
    ]
    assert [record["sha256"] for record in parallel_records] == [
        record["sha256"] for record in serial_records
    ]
    assert parallel_metrics.cache_misses == 2
    assert parallel_metrics.bytes_hashed == len(b"first model") + len(b"second model")


def test_detect_model_references_invalidates_stale_cache_when_file_changes(
    tmp_path: Path,
) -> None:
    model = tmp_path / "checkpoints" / "model.safetensors"
    model.parent.mkdir()
    model.write_bytes(b"first model")
    prompt = {
        "12": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        }
    }
    cache = exporter.ModelHashCache(tmp_path / "cache" / "model_hash_cache.json")

    def resolve(folder: str, filename: str) -> str | None:
        return str(tmp_path / folder / filename)

    first_records = exporter.detect_model_references(prompt, resolve, hash_cache=cache)
    model.write_bytes(b"second model with different size")
    second_records = exporter.detect_model_references(prompt, resolve, hash_cache=cache)

    assert first_records[0]["sha256"] == hashlib.sha256(b"first model").hexdigest()
    assert second_records[0]["sha256"] == hashlib.sha256(
        b"second model with different size"
    ).hexdigest()


def test_detect_model_references_invalidates_cache_for_same_path_same_size_and_mtime(
    tmp_path: Path,
) -> None:
    model = tmp_path / "checkpoints" / "model.safetensors"
    model.parent.mkdir()
    first_content = b"fake model v1"
    second_content = b"fake model v2"
    model.write_bytes(first_content)
    original_stat = model.stat()
    prompt = {
        "12": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "model.safetensors"},
        }
    }
    cache = exporter.ModelHashCache(tmp_path / "cache" / "model_hash_cache.json")

    def resolve(folder: str, filename: str) -> str | None:
        return str(tmp_path / folder / filename)

    first_records = exporter.detect_model_references(prompt, resolve, hash_cache=cache)
    model.write_bytes(second_content)
    os.utime(model, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second_records = exporter.detect_model_references(prompt, resolve, hash_cache=cache)

    assert len(first_content) == len(second_content)
    assert first_records[0]["sha256"] == hashlib.sha256(first_content).hexdigest()
    assert second_records[0]["sha256"] == hashlib.sha256(second_content).hexdigest()


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


def test_noofy_package_writes_redacted_load_image_values(tmp_path: Path) -> None:
    test_graph = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": "creator-family-photo.png", "upload": "image"},
        }
    }
    package_graph, privacy_adjustments, unresolved = exporter.redact_local_inputs_for_package(test_graph)
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
        graph=package_graph,
        workflow_name="Private Image Workflow",
        runtime=runtime,
        custom_nodes=[],
        models=[],
        hardware=hardware,
        started_at="2026-04-30T00:00:00Z",
        finished_at="2026-04-30T00:01:00Z",
        duration_seconds=60,
        graph_adjustments={
            "image_inputs_preserved": 1,
            "batch_size_inputs": 0,
            **privacy_adjustments,
        },
        warnings=[],
        unresolved_runtime_inputs=unresolved,
    )

    target = tmp_path / "workflow.noofy"
    exporter.write_noofy_package(
        target_path=target,
        graph=package_graph,
        documents=documents,
        custom_nodes=[],
        thumbnail_bytes=b"placeholder-thumbnail",
    )

    with zipfile.ZipFile(target) as package:
        graph = package.read("comfyui_graph.json").decode("utf-8")
        report = package.read("export-report.json").decode("utf-8")
        package_blob = b"".join(
            name.encode("utf-8") + b"\n" + package.read(name)
            for name in package.namelist()
        )

    assert "creator-family-photo.png" not in graph
    assert exporter.REDACTED_IMAGE_INPUT_VALUE in graph
    assert "creator-family-photo.png" not in report
    assert b"creator-family-photo.png" not in package_blob
    assert '"image_inputs_redacted": 1' in report


def test_thumbnail_defaults_to_placeholder_without_opening_flac(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "ComfyUI_temp_secret_00001_.flac"
    source.write_bytes(b"fLaC\x00\x00\x00")

    from PIL import Image

    def fail_open(_source: object) -> object:
        raise AssertionError("Pillow should not open generated audio outputs")

    monkeypatch.setattr(Image, "open", fail_open)

    assert exporter.create_thumbnail_bytes(source) == exporter.create_placeholder_thumbnail_bytes()


def test_history_output_declarations_use_kinds_without_runtime_file_identity() -> None:
    graph = {
        "8": {"class_type": "SaveAudio", "inputs": {}},
        "9": {"class_type": "SaveImage", "inputs": {}},
        "10": {"class_type": "Export3D", "inputs": {}},
        "11": {"class_type": "TextOutput", "inputs": {}},
    }
    history = {
        "prompt-id": {
            "outputs": {
                "8": {"audio": [{"filename": "ComfyUI_temp_secret_00001_.flac", "subfolder": "private", "type": "temp"}]},
                "9": {"images": [{"filename": "ComfyUI_00002_.png", "subfolder": "creator", "type": "output"}]},
                "10": {"files": [{"filename": "scan.glb", "subfolder": "meshes", "type": "output"}]},
                "11": {"text": ["hello"]},
            }
        }
    }

    outputs = exporter.collect_history_output_declarations(history, graph)

    assert [output.to_dict() for output in outputs] == [
        {"id": "audio-8", "label": "Audio Output", "node_id": "8", "node_type": "SaveAudio", "type": "audio", "kind": "audio"},
        {"id": "image-9", "label": "Image Output", "node_id": "9", "node_type": "SaveImage", "type": "image", "kind": "image"},
        {"id": "3d-10", "label": "3D Output", "node_id": "10", "node_type": "Export3D", "type": "3d", "kind": "3d"},
        {"id": "text-11", "label": "Text Output", "node_id": "11", "node_type": "TextOutput", "type": "text", "kind": "text"},
    ]
    dashboard_json = json.dumps([output.to_dict() for output in outputs])
    assert "ComfyUI_temp_secret" not in dashboard_json
    assert "private" not in dashboard_json
    assert "output" not in dashboard_json


def test_redact_local_media_and_file_inputs_retains_safe_setup_metadata() -> None:
    graph = {
        "1": {"class_type": "LoadAudio", "inputs": {"audio": "/home/creator/private-song.flac"}},
        "2": {"class_type": "VHS_LoadVideo", "inputs": {"video": "/home/creator/private-video.mp4"}},
        "3": {"class_type": "Load3D", "inputs": {"model_file": "/home/creator/scan.glb"}},
        "4": {"class_type": "LoadFile", "inputs": {"file_path": "/home/creator/notes.json"}},
        "5": {"class_type": "KSampler", "inputs": {"model": ["4", 0]}},
    }

    package_graph, adjustments, unresolved = exporter.redact_local_inputs_for_package(graph)

    assert package_graph["1"]["inputs"]["audio"] == "__noofy_runtime_audio_input_required__"
    assert package_graph["2"]["inputs"]["video"] == "__noofy_runtime_video_input_required__"
    assert package_graph["3"]["inputs"]["model_file"] == "__noofy_runtime_three_d_input_required__"
    assert package_graph["4"]["inputs"]["file_path"] == "__noofy_runtime_text_input_required__"
    assert package_graph["5"]["inputs"]["model"] == ["4", 0]
    assert adjustments["audio_inputs_redacted"] == 1
    assert adjustments["video_inputs_redacted"] == 1
    assert adjustments["three_d_inputs_redacted"] == 1
    assert adjustments["text_inputs_redacted"] == 1
    assert [item["expected_kind"] for item in unresolved] == ["audio", "video", "3d", "text"]
    assert [item["extension_hint"] for item in unresolved] == [".flac", ".mp4", ".glb", ".json"]
    unresolved_blob = json.dumps(unresolved)
    assert "/home/creator" not in unresolved_blob
    assert "private-song.flac" not in unresolved_blob
    assert "private-video.mp4" not in unresolved_blob
    assert "scan.glb" not in unresolved_blob
    assert "notes.json" not in unresolved_blob


def test_noofy_package_omits_generated_output_identity_and_media_bytes(tmp_path: Path) -> None:
    generated_audio_name = "ComfyUI_temp_secret_00001_.flac"
    generated_image_name = "ComfyUI_00002_.png"
    graph = {"8": {"class_type": "SaveAudio", "inputs": {}}, "9": {"class_type": "SaveImage", "inputs": {}}}
    history = {
        "prompt-id": {
            "outputs": {
                "8": {"audio": [{"filename": generated_audio_name, "subfolder": "temp/private", "type": "temp"}]},
                "9": {"images": [{"filename": generated_image_name, "subfolder": "output/private", "type": "output"}]},
            }
        }
    }
    runtime = exporter.RuntimeMetadata(
        comfyui_version="1.2.3",
        python_version="3.12.0",
        platform_name="linux",
        gpu_backend="cuda",
        gpu_name="GPU",
    )
    hardware = exporter.MemoryObservation(
        observed_peak_vram_mb=1024,
        observed_peak_ram_mb=2048,
        gpu_name="GPU",
        backend="cuda",
    )
    documents = exporter.build_package_documents(
        graph=graph,
        workflow_name="Audio First",
        runtime=runtime,
        custom_nodes=[],
        models=[],
        outputs=exporter.collect_history_output_declarations(history, graph),
        unresolved_runtime_inputs=[],
        hardware=hardware,
        started_at="2026-04-30T00:00:00Z",
        finished_at="2026-04-30T00:01:00Z",
        duration_seconds=60,
        graph_adjustments={},
        warnings=[],
    )

    target = tmp_path / "workflow.noofy"
    exporter.write_noofy_package(
        target_path=target,
        graph=graph,
        documents=documents,
        custom_nodes=[],
        thumbnail_bytes=exporter.create_placeholder_thumbnail_bytes(),
    )

    with zipfile.ZipFile(target) as package:
        blob = b"".join(package.read(name) for name in package.namelist())
        dashboard = json.loads(package.read("dashboard.json"))

    assert [output["kind"] for output in dashboard["outputs"]] == ["audio", "image"]
    assert generated_audio_name.encode("utf-8") not in blob
    assert generated_image_name.encode("utf-8") not in blob
    assert b"temp/private" not in blob
    assert b"output/private" not in blob
