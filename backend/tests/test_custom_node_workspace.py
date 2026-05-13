import json
from pathlib import Path

import pytest

from app.runtime.dependencies.custom_nodes import (
    CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME,
    CoreNodeManifest,
    CoreNodeManifestCatalog,
    CustomNodeMaterializationError,
    CustomNodeMaterializationErrorCode,
    CustomNodeWorkspaceMaterializer,
    validate_custom_node_source_relative_paths,
)
from app.runtime.dependencies.isolation import CapsuleLock


RUNTIME_PROFILE_HASH = "sha256:" + ("9" * 64)


def _materializer(*, max_files: int = 20_000, max_bytes: int = 512 * 1024 * 1024) -> CustomNodeWorkspaceMaterializer:
    return CustomNodeWorkspaceMaterializer(
        core_node_manifest_catalog=CoreNodeManifestCatalog(
            manifests=[
                CoreNodeManifest(
                    runtime_profile_id="noofy-comfyui-v1-default",
                    runtime_profile_variant_id="darwin-arm64-mps-dev",
                    runtime_profile_manifest_hash=RUNTIME_PROFILE_HASH,
                    node_types=["KSampler", "LoadImage", "SaveImage"],
                )
            ]
        ),
        max_files=max_files,
        max_bytes=max_bytes,
    )


def test_materializer_recognizes_core_nodes_and_materializes_required_bundled_nodes(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["LoadImage", "KSampler", "CustomRequired"])
    _write_custom_node(source_files, "RequiredNode", {"requirements.txt": "pillow==10.0.0\n", "node.py": "x = 1\n"})
    _write_custom_node(source_files, "UnusedNode", {"node.py": "x = 2\n"})
    capsule = _capsule(
        [
            {"package_id": "requirednode", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]},
            {"package_id": "unusednode", "source": "bundled_from_creator_machine", "node_types": ["CustomUnused"]},
        ]
    )
    workspace = tmp_path / "runner-workspace"

    manifest = _materializer().build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    _materializer().materialize(manifest=manifest, source_files_dir=source_files, runner_workspace_dir=workspace)

    assert manifest.manifest_hash is not None
    assert [entry.custom_node_package_id for entry in manifest.entries] == ["requirednode"]
    assert manifest.entries[0].materialized_relative_path == "custom_nodes/RequiredNode"
    assert "requirements.txt" in manifest.entries[0].dependency_marker_hashes
    assert (workspace / "custom_nodes" / "RequiredNode" / "node.py").exists()
    assert not (workspace / "custom_nodes" / "UnusedNode").exists()
    stored_manifest = json.loads((workspace / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert stored_manifest["manifest_hash"] == manifest.manifest_hash


def test_materializer_does_not_mutate_trusted_core_runtime_files(tmp_path: Path) -> None:
    trusted_core = tmp_path / "trusted-core"
    trusted_custom_nodes = trusted_core / "custom_nodes"
    trusted_custom_nodes.mkdir(parents=True)
    trusted_file = trusted_custom_nodes / "trusted.py"
    trusted_file.write_text("trusted runtime\n", encoding="utf-8")
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "custom-node", {"node.py": "x = 1\n"})
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()

    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    materializer.materialize(
        manifest=manifest,
        source_files_dir=source_files,
        runner_workspace_dir=tmp_path / "runner-workspace",
    )

    assert trusted_file.read_text(encoding="utf-8") == "trusted runtime\n"


def test_custom_node_manifest_is_deterministic_across_filesystem_order(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    for source_files, file_order in (
        (left, [("b.py", "b"), ("a.py", "a")]),
        (right, [("a.py", "a"), ("b.py", "b")]),
    ):
        _write_graph(source_files, ["CustomRequired"])
        _write_custom_node(source_files, "custom-node", dict(file_order))
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()

    left_manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=left)
    right_manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=right)

    assert left_manifest.manifest_hash == right_manifest.manifest_hash


def test_materializer_rejects_unknown_non_core_node_type(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["NotCoreOrBundled"])
    capsule = _capsule([])

    with pytest.raises(CustomNodeMaterializationError) as error:
        _materializer().build_manifest(capsule_lock=capsule, source_files_dir=source_files)

    assert error.value.code is CustomNodeMaterializationErrorCode.UNKNOWN_NODE_TYPE


def test_materializer_rejects_path_traversal_source_ref(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_archive:../evil", "node_types": ["CustomRequired"]}]
    )

    with pytest.raises(CustomNodeMaterializationError) as error:
        _materializer().build_manifest(capsule_lock=capsule, source_files_dir=source_files)

    assert error.value.code is CustomNodeMaterializationErrorCode.PATH_TRAVERSAL


def test_materializer_rejects_case_insensitive_path_collision(tmp_path: Path) -> None:
    with pytest.raises(CustomNodeMaterializationError) as error:
        validate_custom_node_source_relative_paths(["Node.py", "node.py"])

    assert error.value.code is CustomNodeMaterializationErrorCode.CASE_INSENSITIVE_PATH_COLLISION


def test_materializer_rejects_symlink_escape(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    node_dir = source_files / "custom_nodes" / "custom-node"
    node_dir.mkdir(parents=True)
    (node_dir / "escape.py").symlink_to(tmp_path / "outside.py")
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )

    with pytest.raises(CustomNodeMaterializationError) as error:
        _materializer().build_manifest(capsule_lock=capsule, source_files_dir=source_files)

    assert error.value.code is CustomNodeMaterializationErrorCode.SYMLINK_ESCAPE


def test_materializer_rejects_oversized_source(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "custom-node", {"big.bin": "123456"})
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )

    with pytest.raises(CustomNodeMaterializationError) as error:
        _materializer(max_bytes=4).build_manifest(capsule_lock=capsule, source_files_dir=source_files)

    assert error.value.code is CustomNodeMaterializationErrorCode.OVERSIZED_SOURCE


def test_materializer_rejects_protected_custom_node_folder_name(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "models", {"node.py": "x = 1\n"})
    capsule = _capsule(
        [{"package_id": "models", "source": "bundled_archive:models", "node_types": ["CustomRequired"]}]
    )

    with pytest.raises(CustomNodeMaterializationError) as error:
        _materializer().build_manifest(capsule_lock=capsule, source_files_dir=source_files)

    assert error.value.code is CustomNodeMaterializationErrorCode.PROTECTED_PATH_SHADOWING


def _write_graph(source_files: Path, node_types: list[str]) -> None:
    source_files.mkdir(parents=True, exist_ok=True)
    graph = {str(index): {"class_type": node_type, "inputs": {}} for index, node_type in enumerate(node_types)}
    (source_files / "comfyui_graph.json").write_text(json.dumps(graph), encoding="utf-8")


def _write_custom_node(source_files: Path, folder_name: str, files: dict[str, str]) -> None:
    node_dir = source_files / "custom_nodes" / folder_name
    for relative_path, contents in files.items():
        path = node_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")


def _capsule(custom_nodes: list[dict]) -> CapsuleLock:
    return CapsuleLock.model_validate(
        {
            "schema_version": "0.1.0",
            "workflow": {
                "publisher_id": "publisher",
                "package_id": "workflow",
                "version": "0.1.0",
                "trust_level": "quarantined_community",
                "source": "noofy_archive_import",
            },
            "engine": {
                "type": "comfyui",
                "comfyui_version": "v0.20.1",
                "core_source_hash": "sha256:" + ("a" * 64),
            },
            "runtime": {
                "runtime_profile_id": "noofy-comfyui-v1-default",
                "runtime_profile_variant_id": "darwin-arm64-mps-dev",
                "runtime_profile_manifest_hash": RUNTIME_PROFILE_HASH,
                "runtime_profile_catalog_version": "0.1.0",
                "fingerprint_schema_version": "0.1.0",
                "dependency_env_fingerprint": "sha256:" + ("b" * 64),
                "runner_fingerprint": "sha256:" + ("c" * 64),
                "capsule_fingerprint": "sha256:" + ("d" * 64),
                "os": "darwin",
                "architecture": "arm64",
                "python_version": "3.13",
                "python_build_id": "cpython-3.13-noofy-dev",
                "gpu_backend": "mps",
                "dependency_lock_hash": "sha256:" + ("e" * 64),
                "runner_workspace_hash": "sha256:" + ("f" * 64),
            },
            "custom_nodes": [
                {
                    "package_id": node["package_id"],
                    "source": node["source"],
                    "trust_level": "quarantined_community",
                    "node_types": node["node_types"],
                }
                for node in custom_nodes
            ],
            "dependencies": {"lock_file": "dependency-lock.json", "install_policy": "quarantined-community-v1"},
            "models": [],
            "trust": {"level": "quarantined_community"},
        }
    )
