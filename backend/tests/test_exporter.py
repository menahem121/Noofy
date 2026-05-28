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
import struct
import zipfile
from pathlib import Path
from typing import Any

from app.diagnostics import LogStore
from app.workflows.assets import DashboardAssetService
from app.workflows.exporter import WorkflowExporter, stored_comfyui_graph_file
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


def _setup_with_configured_dashboard(
    tmp_path: Path,
    *,
    user_state_service: UserStateService | None = None,
    dashboard_assets_dir: Path | None = None,
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


def test_exported_archive_does_not_leak_user_state(tmp_path: Path) -> None:
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

    assert dashboard_data["inputs"][0]["default"] == "hello"
    assert graph_data["1"]["inputs"]["text"] == "hi"
    first_control = dashboard_data["sections"][0]["controls"][0]
    assert "layout" not in first_control
    second_control = dashboard_data["sections"][0]["controls"][1]
    assert "show_download" not in second_control


def test_exported_archive_keeps_creator_group_layouts_not_user_overrides(tmp_path: Path) -> None:
    user_state_service = UserStateService(tmp_path / "user-state")
    exporter, workflow_id, _ = _setup_with_configured_dashboard(
        tmp_path,
        user_state_service=user_state_service,
    )
    package_dir = exporter._find_package_dir(workflow_id)
    assert package_dir is not None
    dashboard_file = package_dir / "dashboard.json"
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
    assert group["layout"] == {"x": 0, "y": 0, "w": 16, "h": 10}
    assert "layout" not in exported_dashboard["sections"][0]["controls"][0]


def test_exported_archive_keeps_creator_action_bar_position_not_user_override(tmp_path: Path) -> None:
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

    assert exported_dashboard["presentation"]["action_bar"] == {"x": 32, "y": 24}


def test_exported_archive_ignores_explicit_dashboard_values_without_mutating_store(tmp_path: Path) -> None:
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
    assert dashboard_data["inputs"][0]["default"] == "hello"
    assert json.loads(graph_file.read_text(encoding="utf-8")) == before


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
    assert package_data["display_name"] == "Reviewed Export"
    assert package_data["metadata"]["description"] == "Export-ready description"
    assert package_data["metadata"]["author"] == "Noofy User"
    assert package_data["metadata"]["website"] == "https://example.test"
    assert package_data["metadata"]["category"] == "Portrait"
    assert package_data["metadata"]["tags"] == ["portrait", "cleanup"]
    assert package_data["metadata"]["icon"] == "image"
    assert json.loads(package_file.read_text(encoding="utf-8")) == before


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


def test_export_supports_bundled_workflow_without_user_preferences(tmp_path: Path) -> None:
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
    assert package_data["required_models"][0]["size_bytes"] == 2132696762
    assert package_data["required_models"][0]["verification_level"] == "sha256_size"
    assert dashboard_data["inputs"][0]["default"] == "a cinematic photo of a mountain lake"
    assert dashboard_data["sections"][0]["controls"][0]["layout"] == {"x": 0, "y": 0, "w": 32, "h": 6}
    assert graph_data["6"]["inputs"]["text"] == "a cinematic photo of a mountain lake"
