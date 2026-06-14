from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.workflows.assets import DashboardAssetService
from app.workflows.library import WorkflowLibraryStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.store_paths import safe_store_segment
from app.workflows.user_state import UserStateService


@dataclass(frozen=True)
class WorkflowRemovalCleanupResult:
    removed_asset_ids: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


class WorkflowRemovalCleanupService:
    """Removes workflow-owned state while preserving shared dashboard assets."""

    def __init__(
        self,
        *,
        workflow_loader: WorkflowPackageLoader,
        user_state_service: UserStateService,
        asset_service: DashboardAssetService,
        workflow_library_store: WorkflowLibraryStore | None = None,
        dashboard_overrides_dir: Path | None = None,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.user_state_service = user_state_service
        self.asset_service = asset_service
        self.workflow_library_store = workflow_library_store
        self.dashboard_overrides_dir = dashboard_overrides_dir

    def snapshot_asset_candidates(self, workflow_id: str) -> set[str]:
        existing = self.asset_service.list_asset_ids()
        if not existing:
            return set()
        package = self.workflow_loader.get_package(workflow_id)
        user_state = self.user_state_service.get(workflow_id)
        return _referenced_asset_ids(
            [package.model_dump(mode="json"), user_state.model_dump(mode="json")],
            existing,
        )

    def cleanup_after_package_removal(
        self,
        workflow_id: str,
        asset_candidates: set[str],
    ) -> WorkflowRemovalCleanupResult:
        failures: list[dict[str, str]] = []
        for operation, cleanup in (
            ("delete_user_state", lambda: self.user_state_service.delete(workflow_id)),
            ("delete_dashboard_override", lambda: self._delete_dashboard_override(workflow_id)),
        ):
            try:
                cleanup()
            except Exception as exc:
                failures.append(_cleanup_failure(operation, exc))

        try:
            referenced = self._remaining_asset_references()
        except Exception as exc:
            failures.append(_cleanup_failure("scan_dashboard_asset_references", exc))
            return WorkflowRemovalCleanupResult(failures=failures)

        removed: list[str] = []
        for asset_id in sorted(asset_candidates - referenced):
            try:
                if self.asset_service.delete(asset_id):
                    removed.append(asset_id)
            except Exception as exc:
                failures.append(_cleanup_failure(f"delete_dashboard_asset:{asset_id}", exc))
        return WorkflowRemovalCleanupResult(
            removed_asset_ids=removed,
            failures=failures,
        )

    def _remaining_asset_references(self) -> set[str]:
        existing = self.asset_service.list_asset_ids()
        if not existing:
            return set()

        payloads: list[Any] = [
            package.model_dump(mode="json")
            for package in self.workflow_loader.list_packages()
        ]
        user_state_dir = self.user_state_service.user_state_dir
        if user_state_dir.exists():
            payloads.extend(_read_json_files_strict(user_state_dir.glob("*.json")))
        if self.workflow_library_store is not None and self.workflow_library_store.root_dir.exists():
            payloads.extend(
                _read_json_files_strict(
                    self.workflow_library_store.root_dir.rglob("*.json")
                )
            )

        referenced = _referenced_asset_ids(payloads, existing)
        # Asset metadata can retain lineage references between Noofy-owned
        # dashboard assets, such as a mask derived from an uploaded image.
        for metadata in _read_json_files_strict(self.asset_service.assets_dir.glob("*.meta.json")):
            if not isinstance(metadata, dict):
                continue
            source_asset_id = metadata.get("source_asset_id")
            if isinstance(source_asset_id, str) and source_asset_id in existing:
                referenced.add(source_asset_id)
        return referenced

    def _delete_dashboard_override(self, workflow_id: str) -> None:
        if self.dashboard_overrides_dir is None:
            return
        target = self.dashboard_overrides_dir / safe_store_segment(workflow_id)
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            return


def _read_json_files_strict(paths) -> list[Any]:
    payloads: list[Any] = []
    for path in paths:
        try:
            payloads.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Could not read reference file: {path}") from exc
    return payloads


def _cleanup_failure(operation: str, exc: Exception) -> dict[str, str]:
    return {
        "operation": operation,
        "error": str(exc),
        "error_type": type(exc).__name__,
    }


def _referenced_asset_ids(payloads: list[Any], existing: set[str]) -> set[str]:
    referenced: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
            return
        if isinstance(value, list):
            for child in value:
                visit(child)
            return
        if not isinstance(value, str):
            return
        candidate = value.removeprefix("asset:")
        if candidate in existing:
            referenced.add(candidate)

    for payload in payloads:
        visit(payload)
    return referenced
