from __future__ import annotations

import json
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path

from app.runtime.dependencies.isolation import CapsuleLock
from app.workflows.assets import workflow_icon_asset_id
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import assert_path_within


def write_imported_package_transaction(
    *,
    root_dir: Path,
    target_dir: Path,
    package: WorkflowPackage,
    app_capsule_lock: CapsuleLock | None,
    runtime_resolution_unavailable: dict[str, object] | None = None,
    archive_data: bytes,
    original_filename: str | None,
    schema_version: str,
    extract_source_files: Callable[[Path], None],
    dashboard_assets_dir: Path | None = None,
    replace_existing: bool = False,
) -> None:
    transaction_dir = root_dir / "_transactions" / f"import-{uuid.uuid4().hex}"
    assert_path_within(root_dir, transaction_dir, purpose="write import transaction")
    assert_path_within(root_dir, target_dir, purpose="write imported package")
    try:
        source_files_dir = transaction_dir / "source-files"
        assert_path_within(root_dir, source_files_dir, purpose="extract imported package")
        transaction_dir.mkdir(parents=True, exist_ok=False)
        extract_source_files(source_files_dir)
        if dashboard_assets_dir is not None:
            _restore_packaged_workflow_icon(
                source_files_dir=source_files_dir,
                package=package,
                dashboard_assets_dir=dashboard_assets_dir,
            )
        (transaction_dir / "source-archive.noofy").write_bytes(archive_data)
        _write_dashboard(transaction_dir, package)
        _write_package_metadata(transaction_dir, package)
        if app_capsule_lock is not None:
            (transaction_dir / "capsule.lock.json").write_text(
                app_capsule_lock.model_dump_json(indent=2),
                encoding="utf-8",
            )
        (transaction_dir / "exported-capsule.lock.json").write_text(
            json.dumps(package.exported_capsule, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        _write_import_report(
            transaction_dir,
            package=package,
            app_capsule_lock=app_capsule_lock,
            runtime_resolution_unavailable=runtime_resolution_unavailable,
            original_filename=original_filename,
            schema_version=schema_version,
        )
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        if target_dir.exists():
            if not replace_existing:
                raise FileExistsError(
                    "A workflow package with this identity already exists. "
                    "Remove it before importing a replacement."
                )
            if target_dir.is_symlink() or not target_dir.is_dir():
                raise ValueError("Existing workflow package path is not a directory.")
            shutil.rmtree(target_dir)
        transaction_dir.replace(target_dir)
    except Exception:
        shutil.rmtree(transaction_dir, ignore_errors=True)
        raise


def _restore_packaged_workflow_icon(
    *,
    source_files_dir: Path,
    package: WorkflowPackage,
    dashboard_assets_dir: Path,
) -> None:
    icon_id = package.metadata.icon
    if not icon_id:
        return
    try:
        asset_id = workflow_icon_asset_id(icon_id)
    except ValueError:
        return
    source_asset = source_files_dir / "assets" / "workflow-icons" / asset_id
    if not source_asset.exists():
        return
    dashboard_assets_dir.mkdir(parents=True, exist_ok=True)
    target_asset = dashboard_assets_dir / asset_id
    if not target_asset.exists():
        shutil.copyfile(source_asset, target_asset)
    source_meta = source_files_dir / "assets" / "workflow-icons" / f"{asset_id}.meta.json"
    target_meta = dashboard_assets_dir / f"{asset_id}.meta.json"
    if source_meta.exists() and not target_meta.exists():
        shutil.copyfile(source_meta, target_meta)
    elif not target_meta.exists():
        target_meta.write_text(
            json.dumps(
                {
                    "asset_id": asset_id,
                    "kind": "workflow_icon",
                    "original_filename": asset_id,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )


def _write_dashboard(transaction_dir: Path, package: WorkflowPackage) -> None:
    dashboard_payload = package.dashboard.model_dump(mode="json", exclude_none=True)
    dashboard_payload["inputs"] = [
        workflow_input.model_dump(mode="json") for workflow_input in package.inputs
    ]
    dashboard_payload["outputs"] = [
        workflow_output.model_dump(mode="json") for workflow_output in package.outputs
    ]
    (transaction_dir / "dashboard.json").write_text(
        json.dumps(dashboard_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_package_metadata(transaction_dir: Path, package: WorkflowPackage) -> None:
    package_data = package.model_dump(mode="json", exclude_none=True)
    package_data.pop("inputs", None)
    package_data.pop("outputs", None)
    package_data.pop("dashboard", None)
    (transaction_dir / "package.json").write_text(
        json.dumps(package_data, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _write_import_report(
    transaction_dir: Path,
    *,
    package: WorkflowPackage,
    app_capsule_lock: CapsuleLock | None,
    runtime_resolution_unavailable: dict[str, object] | None,
    original_filename: str | None,
    schema_version: str,
) -> None:
    (transaction_dir / "import-report.json").write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "workflow_id": package.metadata.id,
                "identity": package.identity.model_dump() if package.identity else None,
                "original_filename": original_filename,
                "status": (
                    package.import_metadata.status
                    if package.import_metadata
                    else "imported"
                ),
                "runtime_resolution": (
                    {
                        "runtime_profile_id": app_capsule_lock.runtime.runtime_profile_id,
                        "runtime_profile_variant_id": app_capsule_lock.runtime.runtime_profile_variant_id,
                        "runtime_profile_manifest_hash": app_capsule_lock.runtime.runtime_profile_manifest_hash,
                        "selection_stage": "import_time_phase5c",
                    }
                    if app_capsule_lock is not None
                    else (
                        runtime_resolution_unavailable
                        or {
                            "selection_stage": "unavailable",
                            "reason": "runtime_profile_unavailable",
                        }
                    )
                ),
                "source_resolution": (
                    package.import_metadata.developer_details.get(
                        "source_resolution", {}
                    )
                    if package.import_metadata
                    else {}
                ),
                "trust_verification": (
                    package.import_metadata.developer_details.get(
                        "trust_verification", {}
                    )
                    if package.import_metadata
                    else {}
                ),
                "dashboard": (
                    package.import_metadata.developer_details.get("dashboard", {})
                    if package.import_metadata
                    else {}
                ),
                "source_policy": (
                    package.source_policy.model_dump(mode="json")
                    if package.source_policy
                    else None
                ),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
