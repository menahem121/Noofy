"""Tests for WorkflowExporter.

Verifies:
- Export produces a valid .noofy archive.
- Export does not modify the original imported file.
- Export strips trust signatures.
- Exported archive has a separate dashboard.json.
"""

from __future__ import annotations

import io
import hashlib
import json
import struct
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.artifacts import ModelVerificationLevel
from app.diagnostics import LogStore
from app.gallery import CapturedGalleryOutput, GalleryStore
from app.runtime.node_registry import (
    CustomNodeSourceCache,
    NodeRegistryResolver,
    NodeRegistrySource,
    NodeRegistrySourceKind,
    NoofyNodeRegistry,
)
from app.runtime.profiles import load_runtime_profile_catalog
from app.workflows.assets import DashboardAssetService
from app.workflows import importer as importer_module
from app.workflows.exporter import WorkflowExportError, WorkflowExporter, stored_comfyui_graph_file
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.user_state import (
    OutputPreference,
    UserStateActionBarPosition,
    UserStateLayoutOverride,
    UserStatePresentationOverrides,
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


def _png_bytes() -> bytes:
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
        + chunk(b"IEND", b"")
    )


def _gallery_item(
    store: GalleryStore,
    *,
    kind: str = "image",
    filename: str = "gallery-current.png",
    mime_type: str = "image/png",
    data: bytes | None = None,
):
    payload = data if data is not None else _png_bytes()
    staged = store.create_staging_path()
    staged.write_bytes(payload)
    return store.save_staged_output(
        CapturedGalleryOutput(
            idempotency_key=f"export-test|{kind}|{filename}",
            created_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
            workflow_id="wf",
            workflow_title="Workflow",
            job_id="job",
            control_id="result",
            output_id=kind,
            node_id="9",
            widget_title="Result",
            kind=kind,
            staged_path=staged,
            source_filename=filename,
            source_mime_type=mime_type,
            extension=Path(filename).suffix,
            size_bytes=len(payload),
            width=None,
            height=None,
            duration_seconds=None,
            fps=None,
            generation_settings={},
        )
    )


class _FakeSourceFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.urls.append(url)
        return self.payload


class _FakeGitHubCustomNodeUrlResolver:
    def __init__(self, source: NodeRegistrySource) -> None:
        self.source = source
        self.calls: list[tuple[str, str]] = []

    def resolve(self, url: str, *, node_type: str) -> tuple[str, NodeRegistrySource]:
        self.calls.append((node_type, url))
        return "github-roundtrip-node", self.source


def _custom_node_source_archive(root: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/__init__.py", "# test custom node archive\n")
        zf.writestr(f"{root}/requirements.txt", "")
    return buf.getvalue()


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


def _archive_with_package_update(
    archive_bytes: bytes,
    update: Any,
) -> bytes:
    src = io.BytesIO(archive_bytes)
    dst = io.BytesIO()
    with zipfile.ZipFile(src, "r") as source, zipfile.ZipFile(dst, "w") as target:
        package_data = json.loads(source.read("package.json"))
        update(package_data)
        for item in source.infolist():
            if item.filename == "package.json":
                target.writestr("package.json", json.dumps(package_data))
            else:
                target.writestr(item, source.read(item.filename))
    return dst.getvalue()


def _archive_with_json_updates(
    archive_bytes: bytes,
    updates: dict[str, Any],
) -> bytes:
    src = io.BytesIO(archive_bytes)
    dst = io.BytesIO()
    with zipfile.ZipFile(src, "r") as source, zipfile.ZipFile(dst, "w") as target:
        for item in source.infolist():
            if item.filename in updates:
                data = json.loads(source.read(item.filename))
                updates[item.filename](data)
                target.writestr(item.filename, json.dumps(data))
            else:
                target.writestr(item, source.read(item.filename))
    return dst.getvalue()


def _archive_with_extra_files(
    archive_bytes: bytes,
    files: dict[str, Any],
) -> bytes:
    src = io.BytesIO(archive_bytes)
    dst = io.BytesIO()
    with zipfile.ZipFile(src, "r") as source, zipfile.ZipFile(dst, "w") as target:
        for item in source.infolist():
            target.writestr(item, source.read(item.filename))
        for name, value in files.items():
            if isinstance(value, bytes):
                target.writestr(name, value)
            elif isinstance(value, str):
                target.writestr(name, value)
            else:
                target.writestr(name, json.dumps(value))
    return dst.getvalue()


def _setup_with_configured_dashboard(
    tmp_path: Path,
    *,
    user_state_service: UserStateService | None = None,
    dashboard_assets_dir: Path | None = None,
    dashboard: dict[str, Any] | None = None,
    archive_bytes: bytes | None = None,
):
    configured_dashboard = dashboard if dashboard is not None else _CONFIGURED_DASHBOARD
    archive_bytes = archive_bytes or _make_archive(
        with_signature=True,
        dashboard=configured_dashboard,
    )
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    pkg = store.import_archive(archive_bytes, original_filename="export_test.noofy")
    workflow_id = pkg.metadata.id
    (store.package_dir(pkg) / "dashboard.json").write_text(
        json.dumps(configured_dashboard),
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
        dashboard_assets_dir=dashboard_assets_dir,
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


def test_export_preserves_original_assets_and_bundled_custom_node_sources(
    tmp_path: Path,
) -> None:
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {"7": {"class_type": "ExampleBundledNode", "inputs": {}}}
            ),
            "capsule.lock.json": lambda capsule: capsule.update(
                {
                    "custom_nodes": [
                        {
                            "id": "example-bundled-node",
                            "folder_name": "ExampleBundledNode",
                            "source": "bundled_from_creator_machine",
                            "included": True,
                            "node_types": ["ExampleBundledNode"],
                            "requirements_files": ["requirements.txt"],
                            "sha256_manifest": "abc123",
                        }
                    ]
                }
            ),
        },
    )
    archive_bytes = _archive_with_extra_files(
        archive_bytes,
        {
            "assets/thumbnail.png": b"thumbnail-bytes",
            "assets/input-defaults/sample.txt": b"default text",
            "custom_nodes/ExampleBundledNode/__init__.py": "NODE_CLASS_MAPPINGS = {}\n",
            "custom_nodes/ExampleBundledNode/requirements.txt": "",
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive_bytes,
    )

    exported, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        names = zf.namelist()
        package_data = json.loads(zf.read("package.json"))
        capsule_data = json.loads(zf.read("capsule.lock.json"))
        assert zf.read("assets/thumbnail.png") == b"thumbnail-bytes"
        assert zf.read("assets/input-defaults/sample.txt") == b"default text"
        assert (
            zf.read("custom_nodes/ExampleBundledNode/__init__.py")
            == b"NODE_CLASS_MAPPINGS = {}\n"
        )
        assert zf.read("custom_nodes/ExampleBundledNode/requirements.txt") == b""

    assert len(names) == len(set(names))
    assert package_data["custom_nodes"][0]["source"] == "bundled_from_creator_machine"
    assert package_data["custom_nodes"][0]["folder_name"] == "ExampleBundledNode"
    assert capsule_data["custom_nodes"][0]["source"] == "bundled_from_creator_machine"
    assert capsule_data["custom_nodes"][0]["node_types"] == ["ExampleBundledNode"]

    clean_store = ImportedWorkflowPackageStore(tmp_path / "clean-packages", log_store=LogStore())
    reimported = clean_store.import_archive(exported, original_filename="roundtrip.noofy")
    reimported_dir = clean_store.package_dir(reimported)
    assert (
        reimported_dir
        / "source-files"
        / "custom_nodes"
        / "ExampleBundledNode"
        / "__init__.py"
    ).read_text(encoding="utf-8") == "NODE_CLASS_MAPPINGS = {}\n"
    assert (
        reimported_dir / "source-files" / "assets" / "input-defaults" / "sample.txt"
    ).read_bytes() == b"default text"


def test_export_accepts_bundled_package_root_custom_node_sources(tmp_path: Path) -> None:
    packages_dir = tmp_path / "native-packages"
    package_dir = packages_dir / "root_custom_node_workflow"
    package_dir.mkdir(parents=True)
    package_payload = {
        "metadata": {
            "id": "root_custom_node_workflow",
            "name": "Root custom node workflow",
            "version": "0.1.0",
        },
        "engine": "comfyui",
        "comfyui_graph": {
            "1": {"class_type": "RootBundledNode", "inputs": {}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        },
        "custom_nodes": [
            {
                "id": "root-bundled-node",
                "folder_name": "RootBundledNode",
                "source": "bundled_from_creator_machine",
                "included": True,
                "node_types": ["RootBundledNode"],
                "requirements_files": ["requirements.txt"],
                "sha256_manifest": "abc123",
            }
        ],
        "dashboard": _CONFIGURED_DASHBOARD,
    }
    capsule_payload = {
        "schema_version": "0.1.0",
        "custom_nodes": [
            {
                "id": "root-bundled-node",
                "folder_name": "RootBundledNode",
                "source": "bundled_from_creator_machine",
                "included": True,
                "node_types": ["RootBundledNode"],
                "requirements_files": ["requirements.txt"],
                "sha256_manifest": "abc123",
            }
        ],
        "models": [],
    }
    (package_dir / "package.json").write_text(json.dumps(package_payload), encoding="utf-8")
    (package_dir / "capsule.lock.json").write_text(json.dumps(capsule_payload), encoding="utf-8")
    custom_node_dir = package_dir / "custom_nodes" / "RootBundledNode"
    custom_node_dir.mkdir(parents=True)
    (custom_node_dir / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    (custom_node_dir / "requirements.txt").write_text("", encoding="utf-8")
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "workflow-store",
        workflow_loader=WorkflowPackageLoader(packages_dir),
    )

    exported, _ = exporter.export_archive("root_custom_node_workflow")

    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        assert (
            zf.read("custom_nodes/RootBundledNode/__init__.py")
            == b"NODE_CLASS_MAPPINGS = {}\n"
        )
        assert zf.read("custom_nodes/RootBundledNode/requirements.txt") == b""


def test_exported_archive_omits_dashboard_three_d_asset_bytes(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    asset_service = DashboardAssetService(assets_dir)
    model = asset_service.store_three_d_stream(io.BytesIO(b"glTF\x02\x00\x00\x00"), "model/gltf-binary", "mesh.glb")
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path, dashboard_assets_dir=assets_dir)

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        assert all(model["asset_id"] not in name for name in zf.namelist())


def test_export_archive_includes_packaged_default_from_dashboard_override(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    pkg = store.import_archive(_make_archive(with_signature=True), original_filename="override_asset.noofy")
    override_dir = tmp_path / "dashboard-overrides" / pkg.metadata.id
    asset_path = override_dir / "assets" / "input-defaults" / "default.png"
    asset_path.parent.mkdir(parents=True)
    asset_bytes = b"default-image"
    asset_path.write_bytes(asset_bytes)
    asset_ref = {
        "source": "package_asset",
        "asset_id": "input-defaults/default.png",
        "kind": "image",
        "filename": "default.png",
        "content_type": "image/png",
        "size_bytes": len(asset_bytes),
        "sha256": f"sha256:{hashlib.sha256(asset_bytes).hexdigest()}",
    }
    (override_dir / "dashboard.json").write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "status": "configured",
                "inputs": [
                    {
                        "id": "input-image",
                        "label": "Input image",
                        "control": "load_image",
                        "binding": {"node_id": "10", "input_name": "image"},
                        "default": asset_ref,
                        "default_pinned": True,
                        "validation": {},
                    }
                ],
                "outputs": [],
                "sections": [],
            }
        ),
        encoding="utf-8",
    )
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
        dashboard_overrides_dir=tmp_path / "dashboard-overrides",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        dashboard_overrides_dir=tmp_path / "dashboard-overrides",
    )

    exported, _ = exporter.export_archive(pkg.metadata.id)

    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        assert zf.read("assets/input-defaults/default.png") == asset_bytes
        dashboard = json.loads(zf.read("dashboard.json"))
    assert dashboard["inputs"][0]["default"] == asset_ref


def test_export_archive_packages_uploaded_user_state_media_default(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    asset_service = DashboardAssetService(assets_dir)
    uploaded = asset_service.store(_png_bytes(), "image/png", "current.png")
    user_state_service = UserStateService(tmp_path / "user-state")
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "input-image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "10", "input_name": "image"},
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
                    {"id": "input-image", "type": "load_image", "label": "Input image", "input_id": "input-image"}
                ],
            }
        ],
    }
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=dashboard),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {"10": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}
            )
        },
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    pkg = store.import_archive(archive_bytes, original_filename="media_default.noofy")
    user_state_service.save(
        WorkflowUserState(
            workflow_id=pkg.metadata.id,
            values={"input-image": uploaded["asset_id"]},
        )
    )
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        user_state_service=user_state_service,
        dashboard_assets_dir=assets_dir,
    )

    exported, _ = exporter.export_archive(pkg.metadata.id)

    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        names = set(zf.namelist())
        dashboard_data = json.loads(zf.read("dashboard.json"))
        graph_data = json.loads(zf.read("comfyui_graph.json"))
        default = dashboard_data["inputs"][0]["default"]
        archive_asset_path = f"assets/{default['asset_id']}"
        packaged_bytes = zf.read(archive_asset_path)
        archive_text = b"".join(zf.read(name) for name in names if name.endswith((".json", ".txt")))

    assert default["source"] == "package_asset"
    assert default["kind"] == "image"
    assert default["filename"] == "current.png"
    assert dashboard_data["inputs"][0]["default_pinned"] is True
    assert packaged_bytes == (assets_dir / uploaded["asset_id"]).read_bytes()
    assert graph_data["10"]["inputs"]["image"] == "original.png"
    assert uploaded["asset_id"].encode("utf-8") not in archive_text


def test_export_archive_packages_current_gallery_media_default(tmp_path: Path) -> None:
    gallery_store = GalleryStore(tmp_path / "gallery")
    gallery_item = _gallery_item(gallery_store, filename="selected-gallery.png")
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "input-image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "10", "input_name": "image"},
                "default": None,
                "validation": {},
            }
        ],
        "outputs": [],
        "sections": [],
    }
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=dashboard),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {"10": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}
            )
        },
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    pkg = store.import_archive(archive_bytes, original_filename="gallery_media_default.noofy")
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
        gallery_store=gallery_store,
    )

    exported, _ = exporter.export_archive(
        pkg.metadata.id,
        input_values={
            "input-image": {
                "source": "gallery",
                "gallery_item_id": gallery_item.id,
                "kind": "image",
                "filename": gallery_item.filename,
                "extension": gallery_item.extension,
                "mime_type": gallery_item.mime_type,
                "size_bytes": gallery_item.size_bytes,
            }
        },
    )

    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        names = set(zf.namelist())
        dashboard_data = json.loads(zf.read("dashboard.json"))
        graph_data = json.loads(zf.read("comfyui_graph.json"))
        default = dashboard_data["inputs"][0]["default"]
        packaged_bytes = zf.read(f"assets/{default['asset_id']}")
        exported_json = b"".join(zf.read(name) for name in names if name.endswith(".json"))

    assert default["source"] == "package_asset"
    assert default["kind"] == "image"
    assert default["filename"] == "selected-gallery.png"
    assert dashboard_data["inputs"][0]["default_pinned"] is True
    assert packaged_bytes == gallery_store.content_path(gallery_item.id).read_bytes()
    assert graph_data["10"]["inputs"]["image"] == "original.png"
    assert gallery_item.id.encode("utf-8") not in exported_json


def test_export_archive_packages_accessible_local_media_default(tmp_path: Path) -> None:
    local_default = tmp_path / "current.png"
    local_default.write_bytes(_png_bytes())
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "input-image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "10", "input_name": "image"},
                "default": None,
                "validation": {},
            }
        ],
        "outputs": [],
        "sections": [],
    }
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=dashboard),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {"10": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}
            )
        },
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    pkg = store.import_archive(archive_bytes, original_filename="local_media_default.noofy")
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
    )

    exported, _ = exporter.export_archive(
        pkg.metadata.id,
        input_values={"input-image": str(local_default)},
    )

    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        dashboard_data = json.loads(zf.read("dashboard.json"))
        default = dashboard_data["inputs"][0]["default"]
        packaged_bytes = zf.read(f"assets/{default['asset_id']}")
        exported_json = b"".join(zf.read(name) for name in zf.namelist() if name.endswith(".json"))

    assert default["source"] == "package_asset"
    assert default["kind"] == "image"
    assert default["filename"] == "current.png"
    assert dashboard_data["inputs"][0]["default_pinned"] is True
    assert packaged_bytes == local_default.read_bytes()
    assert str(local_default).encode("utf-8") not in exported_json


def test_export_archive_warns_when_media_default_is_nonportable(tmp_path: Path) -> None:
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "input-image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "10", "input_name": "image"},
                "default": None,
                "validation": {},
            }
        ],
        "outputs": [],
        "sections": [],
    }
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=dashboard),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {"10": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}
            )
        },
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    pkg = store.import_archive(archive_bytes, original_filename="bad_media_default.noofy")
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
    )

    with pytest.raises(WorkflowExportError, match="cannot be bundled into the .noofy package"):
        exporter.export_archive(pkg.metadata.id, input_values={"input-image": "ComfyUI/input/current.png"})


def test_export_archive_warns_when_media_default_file_is_missing(tmp_path: Path) -> None:
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "input-image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "10", "input_name": "image"},
                "default": None,
                "validation": {},
            }
        ],
        "outputs": [],
        "sections": [],
    }
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=dashboard),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {"10": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}
            )
        },
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    pkg = store.import_archive(archive_bytes, original_filename="missing_media_default.noofy")
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
    )

    missing = tmp_path / "missing.png"
    with pytest.raises(WorkflowExportError, match="could not be found"):
        exporter.export_archive(pkg.metadata.id, input_values={"input-image": str(missing)})


def test_export_archive_warns_when_media_default_is_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_default = tmp_path / "current.png"
    local_default.write_bytes(_png_bytes())
    monkeypatch.setattr("app.workflows.exporter.MAX_EXPORTED_DEFAULT_ASSET_BYTES", 1)
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "input-image",
                "label": "Input image",
                "control": "load_image",
                "binding": {"node_id": "10", "input_name": "image"},
                "default": None,
                "validation": {},
            }
        ],
        "outputs": [],
        "sections": [],
    }
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=dashboard),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {"10": {"class_type": "LoadImage", "inputs": {"image": "original.png"}}}
            )
        },
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    pkg = store.import_archive(archive_bytes, original_filename="too_large_media_default.noofy")
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
    )

    with pytest.raises(WorkflowExportError, match="too large"):
        exporter.export_archive(pkg.metadata.id, input_values={"input-image": str(local_default)})


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


def test_export_backfills_dashboard_inputs_when_stored_dashboard_lost_them(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    dashboard_file = package_dir / "dashboard.json"
    dashboard_data = json.loads(dashboard_file.read_text(encoding="utf-8"))
    dashboard_data["inputs"] = []
    dashboard_data["outputs"] = []
    dashboard_file.write_text(json.dumps(dashboard_data), encoding="utf-8")

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        exported_dashboard = json.loads(zf.read("dashboard.json"))

    assert [item["id"] for item in exported_dashboard["inputs"]] == ["prompt"]


def test_exported_archive_promotes_user_state_values_and_layout_to_creator_defaults(tmp_path: Path) -> None:
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
    assert dashboard_data["inputs"][0]["default_pinned"] is True
    assert graph_data["1"]["inputs"]["text"] == "hi"
    first_control = dashboard_data["sections"][0]["controls"][0]
    assert first_control["layout"] == {"x": 2, "y": 3, "w": 10, "h": 5}
    second_control = dashboard_data["sections"][0]["controls"][1]
    assert "show_download" not in second_control


def test_exported_archive_overlays_user_control_and_group_layouts_without_mutating_store(
    tmp_path: Path,
) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
    )
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    dashboard_file = package_dir / "dashboard.json"
    source_archive_file = package_dir / "source-archive.noofy"
    dashboard_data = json.loads(dashboard_file.read_text(encoding="utf-8"))
    dashboard_data["sections"][0]["groups"] = [
        {
            "id": "main-group",
            "title": "Main group",
            "description": "Grouped controls.",
            "control_ids": ["c1", "c2"],
            "layout": {"x": 0, "y": 0, "w": 16, "h": 10},
        }
    ]
    dashboard_file.write_text(json.dumps(dashboard_data), encoding="utf-8")
    dashboard_before = dashboard_file.read_bytes()
    source_archive_before = source_archive_file.read_bytes()
    user_state_service.save(
        WorkflowUserState(
            workflow_id=workflow_id,
            dashboard_version="0.1.0",
            layout_overrides={
                "main-group": UserStateLayoutOverride(x=4, y=5, w=18, h=12),
                "c1": UserStateLayoutOverride(x=20, y=20, w=4, h=4),
            },
        )
    )

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        exported_dashboard = json.loads(zf.read("dashboard.json"))

    group = exported_dashboard["sections"][0]["groups"][0]
    assert group["layout"] == {"x": 4, "y": 5, "w": 18, "h": 12}
    assert exported_dashboard["sections"][0]["controls"][0]["layout"] == {
        "x": 20,
        "y": 20,
        "w": 4,
        "h": 4,
    }
    assert dashboard_file.read_bytes() == dashboard_before
    assert source_archive_file.read_bytes() == source_archive_before


def test_exported_archive_overlays_user_action_bar_position(tmp_path: Path) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
    )
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    dashboard_file = package_dir / "dashboard.json"
    dashboard_data = json.loads(dashboard_file.read_text(encoding="utf-8"))
    dashboard_data["presentation"] = {"action_bar": {"x": 32, "y": 24}}
    dashboard_file.write_text(json.dumps(dashboard_data), encoding="utf-8")
    user_state_service.save(
        WorkflowUserState(
            workflow_id=workflow_id,
            dashboard_version="0.1.0",
            presentation_overrides=UserStatePresentationOverrides(
                action_bar=UserStateActionBarPosition(x=300, y=90),
            ),
        )
    )

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        exported_dashboard = json.loads(zf.read("dashboard.json"))

    assert exported_dashboard["presentation"]["action_bar"] == {"x": 300, "y": 90}


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

    assert graph_data["1"]["inputs"]["text"] == "hi"
    assert dashboard_data["inputs"][0]["default"] == "visible dashboard prompt"
    assert dashboard_data["inputs"][0]["default_pinned"] is True
    assert json.loads(graph_file.read_text(encoding="utf-8")) == before


def test_reexport_does_not_restore_legacy_multimodal_text_media_sentinel(
    tmp_path: Path,
) -> None:
    archive = _archive_with_json_updates(
        _make_archive(dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {
                    "22:4": {
                        "class_type": "TextEncodeQwenImageEdit",
                        "inputs": {
                            "image": "__noofy_runtime_text_input_required__",
                            "prompt": "turn the dog red",
                        },
                    }
                }
            )
        },
    )
    archive = _archive_with_package_update(
        archive,
        lambda package: package.update(
            {
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
            }
        ),
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive,
    )

    exported_archive, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(exported_archive)) as zf:
        graph = json.loads(zf.read("comfyui_graph.json"))
    assert graph["22:4"]["inputs"] == {"prompt": "turn the dog red"}


def test_exported_archive_applies_export_only_metadata_without_mutating_store(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    package_file = package_dir / "package.json"
    before = json.loads(package_file.read_text(encoding="utf-8"))

    archive_bytes, _ = exporter.export_archive(
        workflow_id,
        export_metadata={
            "name": "Reviewed Export",
            "description": "Export-ready description",
            "author": "Noofy User",
            "website": "https://example.test",
            "category": "Portrait",
            "tags": ["portrait", " cleanup ", "portrait"],
            "icon": "image",
        },
    )

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        package_data = json.loads(zf.read("package.json"))

    assert package_data["trust_level"] == "quarantined_community"
    assert package_data["metadata"]["name"] == "Reviewed Export"
    assert package_data["metadata"]["display_name"] == "Reviewed Export"
    assert package_data["display_name"] == "Reviewed Export"
    assert package_data["metadata"]["description"] == "Export-ready description"
    assert package_data["metadata"]["author"] == "Noofy User"
    assert package_data["metadata"]["website"] == "https://example.test"
    assert package_data["metadata"]["category"] == "Portrait"
    assert package_data["metadata"]["tags"] == ["portrait", "cleanup"]
    assert package_data["metadata"]["icon"] == "image"
    assert json.loads(package_file.read_text(encoding="utf-8")) == before


def test_exported_archive_keeps_existing_discovery_metadata_when_review_fields_are_blank(tmp_path: Path) -> None:
    archive = _archive_with_package_update(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        lambda package: package["metadata"].update(
            {
                "description": "Creator description",
                "author": "Package Author",
                "website": "https://package.example",
                "category": "Restoration",
                "tags": ["restoration", "starter"],
                "icon": "maximize",
            }
        ),
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive,
    )

    archive_bytes, _ = exporter.export_archive(
        workflow_id,
        export_metadata={
            "name": "Reviewed Export",
            "description": " ",
            "author": "",
            "website": " ",
            "category": "",
            "tags": [],
            "icon": "",
        },
    )

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        package_data = json.loads(zf.read("package.json"))

    assert package_data["metadata"]["name"] == "Reviewed Export"
    assert package_data["metadata"]["display_name"] == "Reviewed Export"
    assert package_data["metadata"]["description"] == "Creator description"
    assert package_data["metadata"]["author"] == "Package Author"
    assert package_data["metadata"]["website"] == "https://package.example"
    assert package_data["metadata"]["category"] == "Restoration"
    assert package_data["metadata"]["tags"] == ["restoration", "starter"]
    assert package_data["metadata"]["icon"] == "maximize"
    for key in ("description", "author", "website", "category", "tags", "icon"):
        assert package_data[key] == package_data["metadata"][key]


def test_exported_archive_infers_category_when_metadata_has_no_category(tmp_path: Path) -> None:
    dashboard = {
        "version": "0.1.0",
        "status": "configured",
        "inputs": [
            {
                "id": "image",
                "label": "Image",
                "control": "load_image",
                "binding": {"node_id": "1", "input_name": "image"},
                "default": None,
                "validation": {},
            }
        ],
        "outputs": [
            {
                "id": "video",
                "label": "Video",
                "node_id": "2",
                "type": "video",
                "kind": "video",
            }
        ],
        "sections": [],
    }
    archive = _archive_with_package_update(
        _make_archive(with_signature=True, dashboard=dashboard),
        lambda package: package.update(
            {
                "inputs": dashboard["inputs"],
                "outputs": dashboard["outputs"],
                "comfyui_graph": {
                    "1": {"class_type": "LoadImage", "inputs": {}},
                    "2": {"class_type": "SaveVideo", "inputs": {}},
                },
            }
        ),
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        dashboard=dashboard,
        archive_bytes=archive,
    )

    archive_bytes, _ = exporter.export_archive(workflow_id, export_metadata={"category": ""})

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        package_data = json.loads(zf.read("package.json"))

    assert package_data["metadata"]["category"] == "img2vid"
    assert package_data["category"] == "img2vid"


def test_exported_package_json_excludes_internal_import_state(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    package_file = package_dir / "package.json"
    package_data = json.loads(package_file.read_text(encoding="utf-8"))
    package_data.update(
        {
            "import_metadata": {"original_filename": "/Users/me/private/source.noofy"},
            "exported_package": {"private": "/Users/me/private/package.json"},
            "exported_capsule": {"private": "/Users/me/private/capsule.lock.json"},
            "export_report": {"private": "/Users/me/private/report.json"},
            "observed_hardware": {"gpu_name": "Local GPU"},
            "custom_nodes": [
                {
                    "id": "example-node",
                    "folder_name": "example-node",
                    "source": "https://example.test/example-node.zip",
                    "included": True,
                    "node_types": ["ExampleNode"],
                    "source_ref": "a" * 40,
                    "source_content_hash": "sha256:" + ("b" * 64),
                    "source_cache_ref": "local-cache/source",
                }
            ],
        }
    )
    package_file.write_text(json.dumps(package_data), encoding="utf-8")

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        exported_package = json.loads(zf.read("package.json"))
    exported_text = json.dumps(exported_package)
    for key in (
        "import_metadata",
        "exported_package",
        "exported_capsule",
        "export_report",
        "observed_hardware",
    ):
        assert key not in exported_package
    assert "/Users/me/private" not in exported_text
    assert "source_cache_ref" not in exported_package["custom_nodes"][0]


def test_exported_package_json_preserves_comfyui_widget_metadata(tmp_path: Path) -> None:
    archive = _archive_with_package_update(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        lambda package: package.update(
            {
                "comfyui_widget_metadata": {
                    "schema_version": "0.1.0",
                    "nodes": {
                        "1": {
                            "inputs": {
                                "text": {
                                    "options": ["short", "detailed"],
                                    "display_name": "Prompt preset",
                                }
                            }
                        }
                    },
                }
            }
        ),
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive,
    )

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        package_data = json.loads(zf.read("package.json"))
    assert package_data["comfyui_widget_metadata"]["nodes"]["1"]["inputs"]["text"] == {
        "options": ["short", "detailed"],
        "display_name": "Prompt preset",
    }


def test_exported_archive_includes_selected_custom_icon_asset(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    asset_service = DashboardAssetService(assets_dir)
    icon = asset_service.store_workflow_icon(_png_bytes(), "image/png", "custom-icon.png")
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        dashboard_assets_dir=assets_dir,
    )

    archive_bytes, _ = exporter.export_archive(
        workflow_id,
        export_metadata={"icon": icon["id"]},
    )

    asset_id = icon["asset_id"]
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        package_data = json.loads(zf.read("package.json"))
        assert package_data["metadata"]["icon"] == icon["id"]
        assert zf.read(f"assets/workflow-icons/{asset_id}") == (assets_dir / asset_id).read_bytes()
        assert f"assets/workflow-icons/{asset_id}.meta.json" in zf.namelist()

    roundtrip_assets_dir = tmp_path / "roundtrip-assets"
    store = ImportedWorkflowPackageStore(
        tmp_path / "roundtrip-packages",
        log_store=LogStore(),
        dashboard_assets_dir=roundtrip_assets_dir,
    )
    imported = store.import_archive(archive_bytes, original_filename="roundtrip.noofy")

    assert imported.identity is not None
    assert imported.identity.trust_level == "quarantined_community"
    assert imported.metadata.icon == icon["id"]
    assert (roundtrip_assets_dir / asset_id).read_bytes() == (assets_dir / asset_id).read_bytes()
    assert json.loads((roundtrip_assets_dir / f"{asset_id}.meta.json").read_text(encoding="utf-8"))["kind"] == "workflow_icon"


def test_import_treats_legacy_local_noofy_export_as_community(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)
    archive_bytes, _ = exporter.export_archive(workflow_id)
    archive_bytes = _archive_with_package_update(archive_bytes, lambda package: package.pop("trust_level", None))

    store = ImportedWorkflowPackageStore(tmp_path / "legacy-roundtrip", log_store=LogStore())
    imported = store.import_archive(archive_bytes, original_filename="legacy-local.noofy")

    assert imported.identity is not None
    assert imported.identity.trust_level == "quarantined_community"
    assert imported.import_metadata is not None
    assert imported.import_metadata.status == "imported"


def test_comfyui_json_export_applies_explicit_dashboard_values(tmp_path: Path) -> None:
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path)

    graph_bytes, filename = exporter.export_comfyui_graph(
        workflow_id,
        input_values={"prompt": "json export prompt"},
    )

    assert filename.endswith(".comfyui.json")
    assert json.loads(graph_bytes)["1"]["inputs"]["text"] == "json export prompt"


def test_comfyui_json_export_prefers_editable_workflow_and_applies_widget_values(
    tmp_path: Path,
) -> None:
    editable_workflow = {
        "last_node_id": 9,
        "last_link_id": 0,
        "nodes": [
            {
                "id": 1,
                "type": "CLIPTextEncode",
                "widgets_values": ["hello"],
            }
        ],
        "links": [],
        "groups": [],
        "config": {},
        "extra": {},
        "version": 0.4,
    }
    widget_bindings = {
        "schema_version": "0.1.0",
        "nodes": {"1": {"text": 0}},
    }
    archive_bytes = _archive_with_extra_files(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_workflow.json": editable_workflow,
            "comfyui_workflow_bindings.json": widget_bindings,
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive_bytes,
    )

    graph_bytes, _ = exporter.export_comfyui_graph(
        workflow_id,
        input_values={"prompt": "current dashboard prompt"},
    )

    exported = json.loads(graph_bytes)
    assert exported["nodes"][0]["widgets_values"] == ["current dashboard prompt"]
    assert "1" not in exported


def test_comfyui_json_export_falls_back_when_editable_workflow_bindings_are_missing(
    tmp_path: Path,
) -> None:
    archive_bytes = _archive_with_extra_files(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_workflow.json": {
                "last_node_id": 1,
                "nodes": [
                    {
                        "id": 1,
                        "type": "CLIPTextEncode",
                        "widgets_values": ["original prompt"],
                    }
                ],
                "links": [],
                "version": 0.4,
            },
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive_bytes,
    )

    graph_bytes, _ = exporter.export_comfyui_graph(
        workflow_id,
        input_values={"prompt": "current prompt"},
    )
    reexported_archive, _ = exporter.export_archive(workflow_id)

    assert json.loads(graph_bytes)["1"]["inputs"]["text"] == "current prompt"
    with zipfile.ZipFile(io.BytesIO(reexported_archive)) as zf:
        assert "comfyui_workflow.json" not in zf.namelist()
        assert "comfyui_workflow_bindings.json" not in zf.namelist()


def test_comfyui_json_export_applies_saved_noofy_defaults_to_editable_workflow(
    tmp_path: Path,
) -> None:
    saved_dashboard = json.loads(json.dumps(_CONFIGURED_DASHBOARD))
    saved_dashboard["inputs"] = [
        {
            "id": "prompt",
            "label": "Prompt",
            "control": "textarea",
            "binding": {"node_id": "1", "input_name": "text"},
            "default": "saved Noofy prompt",
            "default_pinned": True,
            "validation": {},
        },
        {
            "id": "sampler",
            "label": "Sampler",
            "control": "select",
            "binding": {"node_id": "6", "input_name": "sampler_name"},
            "default": "dpmpp_2m",
            "default_pinned": True,
            "validation": {"options": ["euler", "dpmpp_2m"]},
        },
        {
            "id": "seed",
            "label": "Seed",
            "control": "seed_widget",
            "binding": {"node_id": "7", "input_name": "seed"},
            "default": 987654321,
            "default_pinned": True,
            "validation": {},
        },
        {
            "id": "strength",
            "label": "Strength",
            "control": "slider",
            "binding": {"node_id": "8", "input_name": "denoise"},
            "default": 0.42,
            "default_pinned": True,
            "validation": {"min": 0, "max": 1, "step": 0.01},
        },
    ]
    saved_dashboard["sections"][0]["controls"] = [
        {
            "id": item["id"],
            "type": item["control"],
            "label": item["label"],
            "input_id": item["id"],
        }
        for item in saved_dashboard["inputs"]
    ]
    original_dashboard = json.loads(json.dumps(saved_dashboard))
    for item, original_default in zip(
        original_dashboard["inputs"],
        ["original prompt", "euler", 1234, 0.8],
        strict=True,
    ):
        item["default"] = original_default
    editable_workflow = {
        "last_node_id": 9,
        "last_link_id": 4,
        "nodes": [
            {"id": 1, "type": "CLIPTextEncode", "widgets_values": ["original prompt"]},
            {"id": 6, "type": "KSamplerSelect", "widgets_values": ["euler"]},
            {"id": 7, "type": "RandomNoise", "widgets_values": [1234, "randomize"]},
            {"id": 8, "type": "KSampler", "widgets_values": [0.8]},
        ],
        "links": [[4, 1, 0, 8, 0, "CONDITIONING"]],
        "groups": [{"title": "Original structure"}],
        "config": {},
        "extra": {"keep": "structural metadata"},
        "version": 0.4,
    }
    widget_bindings = {
        "schema_version": "0.1.0",
        "nodes": {
            "1": {"text": 0},
            "6": {"sampler_name": 0},
            "7": {"seed": 0},
            "8": {"denoise": 0},
        },
    }
    archive_bytes = _archive_with_json_updates(
        _make_archive(with_signature=True, dashboard=original_dashboard),
        {
            "comfyui_graph.json": lambda graph: graph.update(
                {
                    "6": {
                        "class_type": "KSamplerSelect",
                        "inputs": {"sampler_name": "euler"},
                    },
                    "7": {
                        "class_type": "RandomNoise",
                        "inputs": {"seed": 1234},
                    },
                    "8": {
                        "class_type": "KSampler",
                        "inputs": {"denoise": 0.8},
                    },
                }
            ),
        },
    )
    archive_bytes = _archive_with_extra_files(
        archive_bytes,
        {
            "comfyui_workflow.json": editable_workflow,
            "comfyui_workflow_bindings.json": widget_bindings,
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        dashboard=original_dashboard,
        archive_bytes=archive_bytes,
    )
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    (package_dir / "dashboard.json").write_text(
        json.dumps(saved_dashboard),
        encoding="utf-8",
    )

    graph_bytes, _ = exporter.export_comfyui_graph(workflow_id)

    exported = json.loads(graph_bytes)
    widgets_by_id = {
        str(node["id"]): node["widgets_values"]
        for node in exported["nodes"]
    }
    assert widgets_by_id == {
        "1": ["saved Noofy prompt"],
        "6": ["dpmpp_2m"],
        "7": [987654321, "randomize"],
        "8": [0.42],
    }
    assert exported["links"] == editable_workflow["links"]
    assert exported["groups"] == editable_workflow["groups"]
    assert exported["extra"] == editable_workflow["extra"]


def test_comfyui_json_export_current_value_overrides_saved_state_and_dashboard_default(
    tmp_path: Path,
) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    archive_bytes = _archive_with_extra_files(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_workflow.json": {
                "last_node_id": 1,
                "nodes": [{"id": 1, "type": "CLIPTextEncode", "widgets_values": ["original"]}],
                "links": [],
                "version": 0.4,
            },
            "comfyui_workflow_bindings.json": {
                "schema_version": "0.1.0",
                "nodes": {"1": {"text": 0}},
            },
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
        archive_bytes=archive_bytes,
    )
    user_state_service.save(
        WorkflowUserState(
            workflow_id=workflow_id,
            values={"prompt": "saved Run-page value"},
        )
    )

    graph_bytes, _ = exporter.export_comfyui_graph(
        workflow_id,
        input_values={"prompt": "currently visible Run-page value"},
    )

    exported = json.loads(graph_bytes)
    assert exported["nodes"][0]["widgets_values"] == [
        "currently visible Run-page value"
    ]


def test_comfyui_json_export_updates_only_top_level_bound_workflow_node(
    tmp_path: Path,
) -> None:
    editable_workflow = {
        "last_node_id": 1,
        "nodes": [
            {
                "id": 1,
                "type": "CLIPTextEncode",
                "widgets_values": ["top-level original"],
            }
        ],
        "definitions": {
            "subgraphs": [
                {
                    "id": "nested",
                    "nodes": [
                        {
                            "id": 1,
                            "type": "CLIPTextEncode",
                            "widgets_values": ["nested original"],
                        }
                    ],
                }
            ]
        },
        "links": [],
        "version": 0.4,
    }
    archive_bytes = _archive_with_extra_files(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_workflow.json": editable_workflow,
            "comfyui_workflow_bindings.json": {
                "schema_version": "0.1.0",
                "nodes": {"1": {"text": 0}},
            },
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive_bytes,
    )

    graph_bytes, _ = exporter.export_comfyui_graph(
        workflow_id,
        input_values={"prompt": "current prompt"},
    )

    exported = json.loads(graph_bytes)
    assert exported["nodes"][0]["widgets_values"] == ["current prompt"]
    nested_node = exported["definitions"]["subgraphs"][0]["nodes"][0]
    assert nested_node["widgets_values"] == ["nested original"]


def test_comfyui_json_export_saved_run_page_value_overrides_dashboard_default(
    tmp_path: Path,
) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    archive_bytes = _archive_with_extra_files(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_workflow.json": {
                "last_node_id": 1,
                "nodes": [{"id": 1, "type": "CLIPTextEncode", "widgets_values": ["original"]}],
                "links": [],
                "version": 0.4,
            },
            "comfyui_workflow_bindings.json": {
                "schema_version": "0.1.0",
                "nodes": {"1": {"text": 0}},
            },
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
        archive_bytes=archive_bytes,
    )
    user_state_service.save(
        WorkflowUserState(
            workflow_id=workflow_id,
            values={"prompt": "saved Run-page value"},
        )
    )

    graph_bytes, _ = exporter.export_comfyui_graph(workflow_id)

    exported = json.loads(graph_bytes)
    assert exported["nodes"][0]["widgets_values"] == ["saved Run-page value"]


def test_noofy_export_preserves_editable_comfyui_workflow_files(tmp_path: Path) -> None:
    editable_workflow = {
        "last_node_id": 1,
        "nodes": [{"id": 1, "type": "CLIPTextEncode", "widgets_values": ["hello"]}],
        "links": [],
        "version": 0.4,
    }
    widget_bindings = {
        "schema_version": "0.1.0",
        "nodes": {"1": {"text": 0}},
    }
    archive_bytes = _archive_with_extra_files(
        _make_archive(with_signature=True, dashboard=_CONFIGURED_DASHBOARD),
        {
            "comfyui_workflow.json": editable_workflow,
            "comfyui_workflow_bindings.json": widget_bindings,
        },
    )
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        archive_bytes=archive_bytes,
    )

    archive_bytes, _ = exporter.export_archive(workflow_id)

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        assert json.loads(zf.read("comfyui_workflow.json")) == editable_workflow
        assert json.loads(zf.read("comfyui_workflow_bindings.json")) == widget_bindings


def test_comfyui_json_export_applies_saved_user_state_values(tmp_path: Path) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
    )
    user_state_service.save(
        WorkflowUserState(
            workflow_id=workflow_id,
            values={"prompt": "saved json export prompt"},
        )
    )

    graph_bytes, _ = exporter.export_comfyui_graph(workflow_id)

    assert json.loads(graph_bytes)["1"]["inputs"]["text"] == "saved json export prompt"


def test_comfyui_json_export_uses_original_graph_when_no_default_exists(tmp_path: Path) -> None:
    dashboard = json.loads(json.dumps(_CONFIGURED_DASHBOARD))
    dashboard["inputs"][0]["default"] = None
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path, dashboard=dashboard)

    graph_bytes, _ = exporter.export_comfyui_graph(workflow_id)

    assert json.loads(graph_bytes)["1"]["inputs"]["text"] == "hi"


def test_comfyui_json_export_ignores_explicit_null_when_no_default_exists(tmp_path: Path) -> None:
    dashboard = json.loads(json.dumps(_CONFIGURED_DASHBOARD))
    dashboard["inputs"][0]["default"] = None
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path, dashboard=dashboard)

    graph_bytes, _ = exporter.export_comfyui_graph(
        workflow_id,
        input_values={"prompt": None},
    )

    assert json.loads(graph_bytes)["1"]["inputs"]["text"] == "hi"


def test_exported_archive_ignores_explicit_null_when_no_default_exists(tmp_path: Path) -> None:
    dashboard = json.loads(json.dumps(_CONFIGURED_DASHBOARD))
    dashboard["inputs"][0]["default"] = None
    exporter, workflow_id, _ = _setup_with_configured_dashboard(tmp_path, dashboard=dashboard)

    archive_bytes, _ = exporter.export_archive(
        workflow_id,
        input_values={"prompt": None},
    )

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        dashboard_data = json.loads(zf.read("dashboard.json"))
        graph_data = json.loads(zf.read("comfyui_graph.json"))

    assert dashboard_data["inputs"][0]["default"] is None
    assert dashboard_data["inputs"][0].get("default_pinned") is not True
    assert graph_data["1"]["inputs"]["text"] == "hi"


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
    assert dashboard_data["inputs"][0]["default"] is None


def test_exported_archive_strips_local_model_state_and_source_url_secrets(tmp_path: Path) -> None:
    archive_bytes = _archive_with_json_updates(
        _make_archive(),
        {
            "capsule.lock.json": lambda capsule: capsule.update(
                {
                    "models": [
                        {
                            "comfyui_folder": "checkpoints",
                            "filename": "private.safetensors",
                            "source_urls": [
                                "https://example.test/private.safetensors?token=secret-token",
                                "https://example.test/private.safetensors?api_key=secret-key",
                                "https://example.test/public.safetensors",
                            ],
                            "sha256": "sha256:" + ("a" * 64),
                            "size_bytes": 10,
                            "verification_level": "sha256_size",
                            "local_file_available_at_export": True,
                            "asset_ownership": "noofy_downloaded",
                        }
                    ]
                }
            ),
            "package.json": lambda package: package.update(
                {
                    "required_models": [
                        {
                            "folder": "checkpoints",
                            "filename": "private.safetensors",
                            "source_url": "https://example.test/private.safetensors?token=secret-token",
                            "source_urls": [
                                "https://example.test/private.safetensors?api_key=secret-key",
                                "https://example.test/public.safetensors",
                            ],
                            "sha256": "sha256:" + ("a" * 64),
                            "size_bytes": 10,
                            "verification_level": "sha256_size",
                            "local_file_available_at_export": True,
                            "asset_ownership": "noofy_downloaded",
                        }
                    ]
                }
            ),
        },
    )
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    pkg = store.import_archive(archive_bytes, original_filename="model_state.noofy")
    loader = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    )
    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=loader,
    )

    exported, _ = exporter.export_archive(pkg.metadata.id)

    with zipfile.ZipFile(io.BytesIO(exported)) as zf:
        package_data = json.loads(zf.read("package.json"))
    exposed = json.dumps(package_data)
    model = package_data["required_models"][0]
    assert "local_file_available_at_export" not in model
    assert "asset_ownership" not in model
    assert "secret-token" not in exposed
    assert "secret-key" not in exposed
    assert model["source_url"].endswith("token=%5Bredacted%5D")
    assert model["source_urls"][0].endswith("token=%5Bredacted%5D")
    assert model["source_urls"][1].endswith("api_key=%5Bredacted%5D")
    assert model["source_urls"][2] == "https://example.test/public.safetensors"


def test_raw_json_round_trip_exports_portable_resolved_models_and_custom_nodes(
    tmp_path: Path,
) -> None:
    runtime_catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    source_root = "custom-node-" + ("a" * 40)
    source_archive = _custom_node_source_archive(source_root)
    source_hash = hashlib.sha256(source_archive).hexdigest()
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url="https://codeload.github.com/example/custom-node/zip/" + ("a" * 40),
        source_ref="a" * 40,
        source_content_hash=f"sha256:{source_hash}",
        archive_subdir=source_root,
    )
    first_fetcher = _FakeSourceFetcher(source_archive)
    first_github_resolver = _FakeGitHubCustomNodeUrlResolver(source)
    first_store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        runtime_profile_catalog_provider=lambda: runtime_catalog,
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=first_fetcher,
            log_store=LogStore(),
        ),
        custom_node_github_resolver=first_github_resolver,
    )
    raw_graph = {
        "1": {"class_type": "RoundTripCustomNode", "inputs": {}},
        "2": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "roundtrip_vae.safetensors"},
        },
    }
    raw_bytes = json.dumps(raw_graph).encode("utf-8")
    preview = first_store.preview_archive(
        raw_bytes,
        original_filename="roundtrip.json",
        allow_unverified_community_preparation=True,
    )
    preview = first_store.with_engine_unrecognized_nodes(
        preview,
        missing_node_types=["RoundTripCustomNode"],
        reason="engine_unrecognized_node_types",
    )
    resolved_custom_nodes = first_store.resolve_custom_nodes_from_github_urls(
        preview,
        urls_by_node_type={
            "RoundTripCustomNode": "https://github.com/example/custom-node"
        },
        allow_unverified_community_preparation=True,
    )
    assert first_github_resolver.calls == [
        ("RoundTripCustomNode", "https://github.com/example/custom-node")
    ]

    learned_model_url = (
        "https://huggingface.co/example/roundtrip/resolve/main/"
        "roundtrip_vae.safetensors?token=creator-secret"
    )
    learned_models = [
        model.model_copy(
            update={
                "checksum": "sha256:" + ("b" * 64),
                "size_bytes": 123456,
                "source_url": learned_model_url,
                "source_urls": [learned_model_url],
                "verification_level": ModelVerificationLevel.SHA256_SIZE,
                "identity_verified_by_exporter": True,
                "local_file_available_at_export": True,
            }
        )
        if model.filename == "roundtrip_vae.safetensors"
        else model
        for model in resolved_custom_nodes.required_models
    ]
    prepared_package = resolved_custom_nodes.model_copy(
        update={"required_models": learned_models}
    )
    imported = first_store.import_prepared_archive(
        raw_bytes,
        package=prepared_package,
        original_filename="roundtrip.json",
        allow_unverified_community_preparation=True,
    )

    exporter = WorkflowExporter(
        workflow_store_dir=tmp_path / "packages",
        workflow_loader=WorkflowPackageLoader(
            Path("missing-bundled"),
            imported_packages_dir=tmp_path / "packages",
        ),
    )
    exported_bytes, _ = exporter.export_archive(imported.metadata.id)

    with zipfile.ZipFile(io.BytesIO(exported_bytes)) as zf:
        exported_package = json.loads(zf.read("package.json"))
        exported_capsule = json.loads(zf.read("capsule.lock.json"))
    exported_text = json.dumps(
        {"package": exported_package, "capsule": exported_capsule},
        sort_keys=True,
    )
    assert "source_cache_ref" not in exported_text
    assert str(tmp_path) not in exported_text
    assert "creator-secret" not in exported_text
    assert "local_file_available_at_export" not in exported_text
    assert "identity_verified_by_exporter" not in exported_text
    assert "asset_ownership" not in exported_text

    exported_node = exported_package["custom_nodes"][0]
    assert exported_node["source"] == source.source_url
    assert exported_node["source_ref"] == source.source_ref
    assert exported_node["source_content_hash"] == source.source_content_hash
    assert exported_node["source_archive_subdir"] == source_root
    assert exported_node["node_types"] == ["RoundTripCustomNode"]
    exported_capsule_node = exported_capsule["custom_nodes"][0]
    assert exported_capsule_node["source"] == source.source_url
    assert exported_capsule_node["source_ref"] == source.source_ref
    assert exported_capsule_node["source_content_hash"] == source.source_content_hash
    assert exported_capsule_node["source_archive_subdir"] == source_root
    assert exported_capsule_node["node_types"] == ["RoundTripCustomNode"]

    exported_model = exported_package["required_models"][0]
    assert exported_model["folder"] == "vae"
    assert exported_model["filename"] == "roundtrip_vae.safetensors"
    assert exported_model["checksum"] == "sha256:" + ("b" * 64)
    assert exported_model["size_bytes"] == 123456
    assert exported_model["source_url"].endswith("token=%5Bredacted%5D")
    exported_capsule_model = exported_capsule["models"][0]
    assert exported_capsule_model["comfyui_folder"] == "vae"
    assert exported_capsule_model["filename"] == "roundtrip_vae.safetensors"
    assert exported_capsule_model["sha256"] == "sha256:" + ("b" * 64)
    assert exported_capsule_model["size_bytes"] == 123456
    assert exported_capsule_model["source_urls"][0].endswith("token=%5Bredacted%5D")

    second_fetcher = _FakeSourceFetcher(source_archive)
    second_github_resolver = _FakeGitHubCustomNodeUrlResolver(source)
    second_store = ImportedWorkflowPackageStore(
        tmp_path / "clean-packages",
        log_store=LogStore(),
        runtime_profile_catalog_provider=lambda: runtime_catalog,
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "clean-custom-node-cache",
            fetcher=second_fetcher,
            log_store=LogStore(),
        ),
        custom_node_github_resolver=second_github_resolver,
    )
    reimported = second_store.import_archive(
        exported_bytes,
        original_filename="roundtrip.noofy",
        allow_unverified_community_preparation=True,
    )

    assert second_github_resolver.calls == []
    assert second_fetcher.urls == [source.source_url]
    reimported_node = reimported.custom_nodes[0]
    assert reimported_node.source == source.source_url
    assert reimported_node.source_ref == source.source_ref
    assert reimported_node.source_content_hash == source.source_content_hash
    assert reimported_node.source_archive_subdir == source_root
    assert reimported_node.node_types == ["RoundTripCustomNode"]
    assert reimported_node.source_cache_ref is not None
    reimported_model = reimported.required_models[0]
    assert reimported_model.checksum == "sha256:" + ("b" * 64)
    assert reimported_model.size_bytes == 123456
    assert reimported_model.source_urls[0].endswith("token=%5Bredacted%5D")


def test_export_supports_bundled_workflow_with_user_setup(tmp_path: Path) -> None:
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

    assert filename == "Text-to-Image.noofy"
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        names = set(zf.namelist())
        package_data = json.loads(zf.read("package.json"))
        dashboard_data = json.loads(zf.read("dashboard.json"))
        graph_data = json.loads(zf.read("comfyui_graph.json"))

    assert {"package.json", "dashboard.json", "comfyui_graph.json", "capsule.lock.json", "export-report.json"} <= names
    assert package_data["publisher_id"] == "noofy"
    assert package_data["package_id"] == "text_to_image_v0"
    assert "dashboard" not in package_data
    assert package_data["required_models"][0]["size_bytes"] == 2132696762
    assert package_data["required_models"][0]["verification_level"] == "sha256_size"
    assert dashboard_data["inputs"][0]["default"] == "native export prompt"
    assert dashboard_data["inputs"][0]["default_pinned"] is True
    assert dashboard_data["sections"][0]["controls"][0]["layout"] == {"x": 1, "y": 2, "w": 20, "h": 5}
    assert graph_data["6"]["inputs"]["text"] == "a cinematic photo of a mountain lake"
