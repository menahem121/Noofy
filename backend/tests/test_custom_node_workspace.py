import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.runtime.dependencies.custom_nodes import (
    CUSTOM_NODE_WORKSPACE_MANIFEST_SCHEMA_VERSION,
    CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME,
    CustomNodeMaterializationError,
    CustomNodeMaterializationErrorCode,
    CustomNodeSourceBoundary,
    CustomNodeSourceKind,
    CustomNodeWorkspaceEntry,
    CustomNodeWorkspaceManifest,
    CustomNodeWorkspaceMaterializer,
    validate_custom_node_source_relative_paths,
)
from app.runtime.dependencies.isolation import CapsuleLock, TrustLevel
from app.runtime.profiles import (
    load_runtime_profile_catalog,
)


RUNTIME_PROFILE_HASH = "sha256:" + ("9" * 64)


def _materializer(*, max_files: int = 20_000, max_bytes: int = 512 * 1024 * 1024) -> CustomNodeWorkspaceMaterializer:
    return CustomNodeWorkspaceMaterializer(
        max_files=max_files,
        max_bytes=max_bytes,
    )


def test_materializer_records_graph_nodes_and_materializes_declared_bundled_nodes(tmp_path: Path) -> None:
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
    assert [entry.custom_node_package_id for entry in manifest.entries] == [
        "requirednode",
        "unusednode",
    ]
    assert manifest.graph_node_types == ["CustomRequired", "KSampler", "LoadImage"]
    assert manifest.entries[0].materialized_relative_path == "custom_nodes/RequiredNode"
    assert "requirements.txt" in manifest.entries[0].dependency_marker_hashes
    assert (workspace / "custom_nodes" / "RequiredNode" / "node.py").exists()
    assert (workspace / "custom_nodes" / "UnusedNode" / "node.py").exists()
    stored_manifest = json.loads((workspace / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert stored_manifest["manifest_hash"] == manifest.manifest_hash
    assert stored_manifest["schema_version"] == "0.2.0"
    assert (
        CUSTOM_NODE_WORKSPACE_MANIFEST_SCHEMA_VERSION
        == stored_manifest["schema_version"]
    )


def test_materializer_lets_engine_verify_unclassified_flux_loader_utility_nodes(
    tmp_path: Path,
) -> None:
    runtime_catalog = load_runtime_profile_catalog(
        Path("app/runtime/profile_catalog.json")
    )
    profile = runtime_catalog.profiles[0]
    variant = next(
        item
        for item in profile.variants
        if item.runtime_profile_variant_id == "linux-x64-cuda130"
    )
    source_files = tmp_path / "source-files"
    _write_graph(
        source_files,
        [
            "BiRefNetRMBG",
            "CLIPLoader",
            "CLIPTextEncode",
            "ConditioningZeroOut",
            "FluxGuidance",
            "GetImageSize",
            "GrowMaskWithBlur",
            "ImageCompositeMasked",
            "ImageScaleToTotalPixels",
            "KSampler",
            "LoadImage",
            "ReferenceLatent",
            "ResizeMask",
            "SaveImage",
            "SetLatentNoiseMask",
            "UNETLoader",
            "VAEDecode",
            "VAEEncode",
            "VAELoader",
        ],
    )
    _write_custom_node(source_files, "ComfyUI-KJNodes", {"node.py": "x = 1\n"})
    _write_custom_node(source_files, "comfyui-rmbg", {"node.py": "x = 2\n"})
    capsule = _capsule_for_runtime(
        [
            {
                "package_id": "comfyui-kjnodes",
                "source": "bundled_from_creator_machine",
                "node_types": ["GrowMaskWithBlur", "ResizeMask"],
            },
            {
                "package_id": "comfyui-rmbg",
                "source": "bundled_from_creator_machine",
                "node_types": ["BiRefNetRMBG"],
            },
        ],
        profile=profile,
        variant=variant,
    )
    materializer = CustomNodeWorkspaceMaterializer()

    manifest = materializer.build_manifest(
        capsule_lock=capsule,
        source_files_dir=source_files,
    )

    assert [entry.custom_node_package_id for entry in manifest.entries] == [
        "comfyui-kjnodes",
        "comfyui-rmbg",
    ]
    assert all("CLIPLoader" not in entry.node_types for entry in manifest.entries)


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


def test_materializer_leaves_unknown_node_types_to_runner_when_no_custom_nodes(
    tmp_path: Path,
) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["NotCoreOrBundled"])
    capsule = _capsule([])

    manifest = _materializer().build_manifest(
        capsule_lock=capsule,
        source_files_dir=source_files,
    )

    assert manifest.entries == []
    assert manifest.graph_node_types == ["NotCoreOrBundled"]


def test_materializer_conservatively_copies_supported_packages_for_unclassified_nodes(
    tmp_path: Path,
) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["KnownCustomA", "UnclassifiedNode"])
    _write_custom_node(source_files, "CustomNodeA", {"node.py": "x = 1\n"})
    _write_custom_node(source_files, "CustomNodeB", {"node.py": "x = 2\n"})
    capsule = _capsule(
        [
            {
                "package_id": "customnodea",
                "source": "bundled_from_creator_machine",
                "node_types": ["KnownCustomA"],
            },
            {
                "package_id": "customnodeb",
                "source": "bundled_from_creator_machine",
                "node_types": ["KnownCustomB"],
            },
        ]
    )

    manifest = _materializer().build_manifest(
        capsule_lock=capsule,
        source_files_dir=source_files,
    )

    assert [entry.custom_node_package_id for entry in manifest.entries] == [
        "customnodea",
        "customnodeb",
    ]


def test_materializer_uses_single_bundled_package_when_exported_node_types_are_incomplete(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomDeclared", "CustomOmitted"])
    _write_custom_node(source_files, "SingleNodePack", {"node.py": "x = 1\n"})
    capsule = _capsule(
        [
            {
                "package_id": "singlenodepack",
                "source": "bundled_from_creator_machine",
                "node_types": ["CustomDeclared"],
            }
        ]
    )

    manifest = _materializer().build_manifest(
        capsule_lock=capsule,
        source_files_dir=source_files,
    )

    assert [entry.custom_node_package_id for entry in manifest.entries] == [
        "singlenodepack"
    ]


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


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows filesystems cannot materialize same-directory names that differ only by case.",
)
def test_materializer_rejects_nested_case_insensitive_path_collision(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(
        source_files,
        "custom-node",
        {
            "models/Foo.py": "x = 1\n",
            "models/foo.py": "x = 2\n",
        },
    )
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )

    with pytest.raises(CustomNodeMaterializationError) as error:
        _materializer().build_manifest(capsule_lock=capsule, source_files_dir=source_files)

    assert error.value.code is CustomNodeMaterializationErrorCode.CASE_INSENSITIVE_PATH_COLLISION
    assert error.value.boundary is CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH
    assert error.value.relative_path == "models/foo.py"


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
    assert error.value.boundary is CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH


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
    assert error.value.boundary is CustomNodeSourceBoundary.CUSTOM_NODE_PACKAGE_FOLDER
    assert error.value.developer_details["reason"] == "protected_package_folder"


def test_materializer_allows_nested_models_directory_inside_custom_node(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(
        source_files,
        "comfyui-rmbg",
        {
            "__init__.py": "NODE_CLASS_MAPPINGS = {}\n",
            "nodes.py": "x = 1\n",
            "models/__init__.py": "",
            "models/birefnet.py": "MODEL = 'BiRefNet'\n",
        },
    )
    capsule = _capsule(
        [{"package_id": "comfyui-rmbg", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    workspace = tmp_path / "runner-workspace"
    materializer = _materializer()

    manifest = materializer.build_manifest(
        capsule_lock=capsule,
        source_files_dir=source_files,
    )
    materializer.materialize(
        manifest=manifest,
        source_files_dir=source_files,
        runner_workspace_dir=workspace,
    )

    assert manifest.entries[0].materialized_relative_path == "custom_nodes/comfyui-rmbg"
    assert manifest.entries[0].source_folder_name == "comfyui-rmbg"
    assert manifest.entries[0].policy_flags["folder_name_remapped"] is False
    assert (
        workspace / "custom_nodes" / "comfyui-rmbg" / "models" / "birefnet.py"
    ).read_text(encoding="utf-8") == "MODEL = 'BiRefNet'\n"


def test_materializer_allows_common_internal_runtime_like_folder_names(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    internal_names = [
        "models",
        "input",
        "output",
        "temp",
        "user",
        "assets",
        "configs",
        "weights",
    ]
    _write_custom_node(
        source_files,
        "custom-node",
        {f"{name}/marker.txt": name for name in internal_names},
    )
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    workspace = tmp_path / "runner-workspace"
    materializer = _materializer()

    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    materializer.materialize(
        manifest=manifest,
        source_files_dir=source_files,
        runner_workspace_dir=workspace,
    )

    for name in internal_names:
        assert (
            workspace / "custom_nodes" / "custom-node" / name / "marker.txt"
        ).read_text(encoding="utf-8") == name


def test_materializer_remaps_protected_source_folder_to_safe_package_id(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "models", {"node.py": "x = 1\n"})
    capsule = _capsule(
        [{"package_id": "comfyui-rmbg", "source": "bundled_archive:models", "node_types": ["CustomRequired"]}]
    )
    workspace = tmp_path / "runner-workspace"
    materializer = _materializer()

    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    materializer.materialize(
        manifest=manifest,
        source_files_dir=source_files,
        runner_workspace_dir=workspace,
    )

    entry = manifest.entries[0]
    assert entry.materialized_relative_path == "custom_nodes/comfyui-rmbg"
    assert entry.source_folder_name == "models"
    assert entry.policy_flags["folder_name_remapped"] is True
    assert (workspace / "custom_nodes" / "comfyui-rmbg" / "node.py").exists()
    assert not (workspace / "custom_nodes" / "models").exists()


def test_remapped_package_keeps_explicit_source_and_target_names_for_import_smoke(
    tmp_path: Path,
) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "models", {"node.py": "x = 1\n"})
    capsule = _capsule(
        [{"package_id": "safe-package", "source": "bundled_archive:models", "node_types": ["CustomRequired"]}]
    )

    manifest = _materializer().build_manifest(
        capsule_lock=capsule,
        source_files_dir=source_files,
    )

    entry = manifest.entries[0]
    assert entry.source_folder_name == "models"
    assert entry.materialized_relative_path == "custom_nodes/safe-package"
    assert entry.policy_flags == {
        "has_install_py": False,
        "folder_name_remapped": True,
    }
    assert entry.node_types == ["CustomRequired"]


@pytest.mark.parametrize(
    "materialized_relative_path",
    [
        "custom_nodes/foo/bar",
        "custom_nodes/models",
        "models/foo",
        "../custom_nodes/foo",
        "custom_nodes",
    ],
)
def test_workspace_entry_rejects_arbitrary_nested_materialized_path(
    materialized_relative_path: str,
) -> None:
    with pytest.raises(ValidationError):
        _workspace_entry(materialized_relative_path)


def test_workspace_manifest_rejects_pre_boundary_schema_version() -> None:
    with pytest.raises(ValidationError):
        CustomNodeWorkspaceManifest(
            schema_version="0.1.0",
            runtime_profile_id="profile",
            runtime_profile_variant_id="variant",
            runtime_profile_manifest_hash="sha256:" + ("1" * 64),
        )


def test_internal_source_path_still_rejects_traversal() -> None:
    with pytest.raises(CustomNodeMaterializationError) as error:
        validate_custom_node_source_relative_paths(["models/../../escape.py"])

    assert error.value.code is CustomNodeMaterializationErrorCode.PATH_TRAVERSAL
    assert error.value.boundary is CustomNodeSourceBoundary.ARCHIVE_MEMBER_PATH
    assert error.value.developer_details["relative_path"] == "models/../../escape.py"


@pytest.mark.parametrize(
    "relative_path",
    [
        "/absolute.py",
        "models//birefnet.py",
        "models/./birefnet.py",
        "models\\birefnet.py",
    ],
)
def test_internal_source_path_rejects_invalid_path_shape(relative_path: str) -> None:
    with pytest.raises(CustomNodeMaterializationError) as error:
        validate_custom_node_source_relative_paths([relative_path])

    assert error.value.code is CustomNodeMaterializationErrorCode.PATH_TRAVERSAL
    assert error.value.boundary is CustomNodeSourceBoundary.ARCHIVE_MEMBER_PATH


def test_materializer_never_writes_outside_runner_custom_nodes_package(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "custom-node", {"node.py": "x = 1\n"})
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()
    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    workspace = tmp_path / "runner-workspace"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("trusted\n", encoding="utf-8")
    (workspace / "custom_nodes").mkdir(parents=True)
    (workspace / "custom_nodes" / "custom-node").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CustomNodeMaterializationError) as error:
        materializer.materialize(
            manifest=manifest,
            source_files_dir=source_files,
            runner_workspace_dir=workspace,
        )

    assert error.value.boundary is CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION
    assert error.value.developer_details["reason"] == "destination_escape"
    assert sentinel.read_text(encoding="utf-8") == "trusted\n"
    assert not (outside / "node.py").exists()


def test_materializer_rejects_existing_package_symlink_inside_custom_nodes(
    tmp_path: Path,
) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "custom-node", {"node.py": "x = 1\n"})
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()
    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    workspace = tmp_path / "runner-workspace"
    other_package = workspace / "custom_nodes" / "other-package"
    other_package.mkdir(parents=True)
    sentinel = other_package / "sentinel.txt"
    sentinel.write_text("trusted\n", encoding="utf-8")
    (workspace / "custom_nodes" / "custom-node").symlink_to(
        other_package,
        target_is_directory=True,
    )

    with pytest.raises(CustomNodeMaterializationError) as error:
        materializer.materialize(
            manifest=manifest,
            source_files_dir=source_files,
            runner_workspace_dir=workspace,
        )

    assert error.value.boundary is CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION
    assert error.value.developer_details["reason"] == "destination_symlink"
    assert sentinel.read_text(encoding="utf-8") == "trusted\n"
    assert not (other_package / "node.py").exists()


def test_failed_staging_preserves_existing_materialized_package(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "custom-node", {"node.py": "new\n"})
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()
    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    workspace = tmp_path / "runner-workspace"
    ready_package = workspace / "custom_nodes" / "custom-node"
    ready_package.mkdir(parents=True)
    (ready_package / "node.py").write_text("ready\n", encoding="utf-8")
    (source_files / "custom_nodes" / "custom-node" / "node.py").write_text(
        "changed after manifest\n",
        encoding="utf-8",
    )

    with pytest.raises(CustomNodeMaterializationError) as error:
        materializer.materialize(
            manifest=manifest,
            source_files_dir=source_files,
            runner_workspace_dir=workspace,
        )

    assert error.value.developer_details["reason"] == "source_content_hash_mismatch"
    assert (ready_package / "node.py").read_text(encoding="utf-8") == "ready\n"
    assert not list((workspace / "custom_nodes").glob(".noofy-materialize-*"))


def test_failed_atomic_promotion_restores_existing_materialized_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "custom-node", {"node.py": "new\n"})
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()
    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    workspace = tmp_path / "runner-workspace"
    ready_package = workspace / "custom_nodes" / "custom-node"
    ready_package.mkdir(parents=True)
    (ready_package / "node.py").write_text("ready\n", encoding="utf-8")
    original_replace = Path.replace

    def fail_staged_package_promotion(path: Path, target: Path) -> Path:
        if path.parent.name == "p" and path.name == "0":
            raise OSError("injected promotion failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_staged_package_promotion)

    with pytest.raises(CustomNodeMaterializationError) as error:
        materializer.materialize(
            manifest=manifest,
            source_files_dir=source_files,
            runner_workspace_dir=workspace,
        )

    assert (
        error.value.code
        is CustomNodeMaterializationErrorCode.WORKSPACE_PROMOTION_FAILED
    )
    assert (ready_package / "node.py").read_text(encoding="utf-8") == "ready\n"
    assert not list((workspace / "custom_nodes").glob(".n*"))


def test_failed_manifest_promotion_restores_package_and_previous_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "custom-node", {"node.py": "new\n"})
    capsule = _capsule(
        [{"package_id": "custom-node", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()
    manifest = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    workspace = tmp_path / "runner-workspace"
    ready_package = workspace / "custom_nodes" / "custom-node"
    ready_package.mkdir(parents=True)
    (ready_package / "node.py").write_text("ready\n", encoding="utf-8")
    manifest_path = workspace / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME
    manifest_path.write_text("previous manifest\n", encoding="utf-8")
    original_replace = Path.replace

    def fail_staged_manifest_promotion(path: Path, target: Path) -> Path:
        if path.name == "m.json" and path.parent.name.startswith(".n"):
            raise OSError("injected manifest promotion failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_staged_manifest_promotion)

    with pytest.raises(CustomNodeMaterializationError) as error:
        materializer.materialize(
            manifest=manifest,
            source_files_dir=source_files,
            runner_workspace_dir=workspace,
        )

    assert (
        error.value.code
        is CustomNodeMaterializationErrorCode.WORKSPACE_PROMOTION_FAILED
    )
    assert (ready_package / "node.py").read_text(encoding="utf-8") == "ready\n"
    assert manifest_path.read_text(encoding="utf-8") == "previous manifest\n"
    assert not list((workspace / "custom_nodes").glob(".n*"))


def test_nested_source_content_hash_changes_when_nested_file_changes(tmp_path: Path) -> None:
    source_files = tmp_path / "source-files"
    _write_graph(source_files, ["CustomRequired"])
    _write_custom_node(source_files, "comfyui-rmbg", {"models/birefnet.py": "version = 1\n"})
    capsule = _capsule(
        [{"package_id": "comfyui-rmbg", "source": "bundled_from_creator_machine", "node_types": ["CustomRequired"]}]
    )
    materializer = _materializer()

    first = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)
    (source_files / "custom_nodes" / "comfyui-rmbg" / "models" / "birefnet.py").write_text(
        "version = 2\n",
        encoding="utf-8",
    )
    second = materializer.build_manifest(capsule_lock=capsule, source_files_dir=source_files)

    assert first.entries[0].source_content_hash != second.entries[0].source_content_hash
    assert first.manifest_hash != second.manifest_hash


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


def _workspace_entry(materialized_relative_path: str) -> CustomNodeWorkspaceEntry:
    return CustomNodeWorkspaceEntry(
        custom_node_package_id="custom-node",
        source_kind=CustomNodeSourceKind.BUNDLED_ARCHIVE,
        source_ref="bundled_from_creator_machine",
        source_content_hash="sha256:" + ("1" * 64),
        materialized_relative_path=materialized_relative_path,
        source_folder_name="custom-node",
        import_order_index=0,
        package_trust_level=TrustLevel.QUARANTINED_COMMUNITY,
    )


def _capsule_for_runtime(custom_nodes: list[dict], *, profile, variant) -> CapsuleLock:
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
                "comfyui_version": profile.comfyui_core_version,
                "core_source_hash": profile.comfyui_core_source_hash,
            },
            "runtime": {
                "runtime_profile_id": profile.runtime_profile_id,
                "runtime_profile_variant_id": variant.runtime_profile_variant_id,
                "runtime_profile_manifest_hash": profile.runtime_profile_manifest_hash,
                "runtime_profile_catalog_version": "0.1.0",
                "fingerprint_schema_version": "0.1.0",
                "dependency_env_fingerprint": "sha256:" + ("b" * 64),
                "runner_fingerprint": "sha256:" + ("c" * 64),
                "capsule_fingerprint": "sha256:" + ("d" * 64),
                "os": variant.os,
                "architecture": variant.architecture,
                "python_version": variant.python_version,
                "python_build_id": variant.python_build_id,
                "gpu_backend": variant.gpu_backend_profile,
                "dependency_lock_hash": variant.core_dependency_lock_hash,
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
            "dependencies": {
                "lock_file": "dependency-lock.json",
                "install_policy": "quarantined-community-v1",
            },
            "models": [],
            "trust": {"level": "quarantined_community"},
        }
    )


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
