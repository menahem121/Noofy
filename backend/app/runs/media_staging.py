from __future__ import annotations

import contextlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.gallery import GalleryStore
from app.runtime.runners.supervisor import RunnerDescriptor
from app.workflows.media_values import (
    MEDIA_LOAD_CONTROLS,
    is_empty_media_value,
    is_gallery_media_reference,
    is_uploaded_asset_value,
    media_metadata_matches_input,
    target_media_kind_for_input,
)
from app.workflows.package import WorkflowPackage
from app.workflows.package_assets import (
    PackageAssetError,
    is_package_asset_value,
    package_asset_source_candidates,
    safe_package_asset_id,
    validate_package_asset_file,
    validate_package_asset_reference,
)
from app.workflows.store_paths import mutable_package_dir, safe_store_segment


class MediaInputStagingError(ValueError):
    pass


@dataclass
class MediaInputStagingResult:
    inputs: dict[str, Any]
    staged_files: list[Path] = field(default_factory=list)


class MediaInputStagingResolver:
    """Materialize persisted media references for a runner-bound submission."""

    def __init__(
        self,
        *,
        dashboard_assets_dir: Path | None,
        gallery_store: GalleryStore | None,
        log_store: DiagnosticsSink | None = None,
        workflow_store_dir: Path | None = None,
        dashboard_overrides_dir: Path | None = None,
        package_search_roots: list[Path] | None = None,
    ) -> None:
        self.dashboard_assets_dir = dashboard_assets_dir
        self.gallery_store = gallery_store
        self.log_store = log_store
        self.workflow_store_dir = workflow_store_dir
        self.dashboard_overrides_dir = dashboard_overrides_dir
        self.package_search_roots = package_search_roots or []

    def stage_media_inputs(
        self,
        *,
        package: WorkflowPackage,
        inputs: dict[str, Any],
        runner: RunnerDescriptor,
        adapter: object,
        job_id: str,
    ) -> MediaInputStagingResult:
        input_dir = self._input_dir_for_runner(runner, adapter)
        staging_dir = input_dir / "staging" if input_dir is not None else None
        resolved_inputs: dict[str, Any] | None = None
        staged_files: list[Path] = []
        staged_by_key: dict[str, Path] = {}

        try:
            for workflow_input in package.inputs:
                if workflow_input.control not in MEDIA_LOAD_CONTROLS or workflow_input.id not in inputs:
                    continue
                value = inputs[workflow_input.id]
                if is_empty_media_value(value):
                    continue
                if is_uploaded_asset_value(value):
                    if self.dashboard_assets_dir is None:
                        raise MediaInputStagingError("Noofy could not prepare the uploaded media for this workflow run.")
                    source_path = self.dashboard_assets_dir / str(value)
                    if not source_path.is_file():
                        self._log(
                            "warning",
                            "Dashboard asset not found for media input staging",
                            package.metadata.id,
                            job_id,
                            {"input_id": workflow_input.id, "asset_id": str(value)},
                        )
                        raise MediaInputStagingError("The uploaded media for this input could not be found.")
                    staged_path = self._stage_once(
                        staging_dir,
                        source_path,
                        key=f"asset:{value}",
                        staged_name=f"{job_id}_{value}",
                        staged_by_key=staged_by_key,
                        staged_files=staged_files,
                    )
                    resolved_inputs = dict(inputs) if resolved_inputs is None else resolved_inputs
                    resolved_inputs[workflow_input.id] = f"staging/{staged_path.name}"
                    self._log(
                        "debug",
                        "Staged uploaded dashboard media input",
                        package.metadata.id,
                        job_id,
                        {"input_id": workflow_input.id, "asset_id": str(value), "control": workflow_input.control},
                    )
                    continue
                if is_package_asset_value(value):
                    source_path = self._package_asset_path(package, value)
                    asset_id = safe_package_asset_id(str(value["asset_id"]))
                    kind = value.get("kind") if isinstance(value.get("kind"), str) else None
                    extension = Path(asset_id).suffix
                    mime_type = value.get("content_type") if isinstance(value.get("content_type"), str) else None
                    if not media_metadata_matches_input(
                        workflow_input,
                        kind=kind,
                        extension=extension,
                        mime_type=mime_type,
                    ):
                        self._log(
                            "warning",
                            "Packaged media default is not compatible with the workflow input",
                            package.metadata.id,
                            job_id,
                            {"input_id": workflow_input.id, "asset_id": asset_id, "control": workflow_input.control},
                        )
                        raise MediaInputStagingError("The packaged default media is not compatible with this input.")
                    staged_path = self._stage_once(
                        staging_dir,
                        source_path,
                        key=f"package_asset:{package.metadata.id}:{asset_id}",
                        staged_name=f"{job_id}_package_{Path(asset_id).name}",
                        staged_by_key=staged_by_key,
                        staged_files=staged_files,
                    )
                    resolved_inputs = dict(inputs) if resolved_inputs is None else resolved_inputs
                    resolved_inputs[workflow_input.id] = f"staging/{staged_path.name}"
                    self._log(
                        "debug",
                        "Staged packaged media default",
                        package.metadata.id,
                        job_id,
                        {"input_id": workflow_input.id, "asset_id": asset_id, "control": workflow_input.control},
                    )
                    continue
                if not is_gallery_media_reference(value):
                    if isinstance(value, dict) and value.get("source") == "gallery":
                        raise MediaInputStagingError("The selected Gallery item reference is invalid.")
                    continue

                item_id = str(value["gallery_item_id"])
                if self.gallery_store is None:
                    raise MediaInputStagingError("Gallery is not available for this workflow run.")
                item = self.gallery_store.get_item(item_id)
                if item is None:
                    self._log_gallery_failure(package.metadata.id, job_id, workflow_input.id, "not_found", item_id)
                    raise MediaInputStagingError("The selected Gallery item no longer exists.")
                if item.file_state == "missing":
                    self._log_gallery_failure(package.metadata.id, job_id, workflow_input.id, "file_missing", item_id)
                    raise MediaInputStagingError("The selected Gallery item file is missing.")
                expected_kind = target_media_kind_for_input(workflow_input)
                if expected_kind is not None and item.kind != expected_kind:
                    self._log_gallery_failure(
                        package.metadata.id,
                        job_id,
                        workflow_input.id,
                        "kind_mismatch",
                        item_id,
                        {"expected_kind": expected_kind, "actual_kind": item.kind},
                    )
                    raise MediaInputStagingError("The selected Gallery item is not compatible with this input.")
                if not media_metadata_matches_input(
                    workflow_input,
                    kind=item.kind,
                    extension=item.extension,
                    mime_type=item.mime_type,
                ):
                    self._log_gallery_failure(package.metadata.id, job_id, workflow_input.id, "media_type_mismatch", item_id)
                    raise MediaInputStagingError("The selected Gallery item type is not supported by this input.")
                source_path = self.gallery_store.content_path(item_id)
                if source_path is None or not source_path.is_file():
                    self._log_gallery_failure(package.metadata.id, job_id, workflow_input.id, "file_missing", item_id)
                    raise MediaInputStagingError("The selected Gallery item file is missing.")
                suffix = source_path.suffix or item.extension or ".bin"
                staged_path = self._stage_once(
                    staging_dir,
                    source_path,
                    key=f"gallery:{item_id}",
                    staged_name=f"{job_id}_gallery_{item_id}{suffix}",
                    staged_by_key=staged_by_key,
                    staged_files=staged_files,
                )
                resolved_inputs = dict(inputs) if resolved_inputs is None else resolved_inputs
                resolved_inputs[workflow_input.id] = f"staging/{staged_path.name}"
                self._log(
                    "info",
                    "Staged Gallery media input",
                    package.metadata.id,
                    job_id,
                    {
                        "input_id": workflow_input.id,
                        "gallery_item_id": item_id,
                        "kind": item.kind,
                        "control": workflow_input.control,
                    },
                )
        except OSError as exc:
            cleanup_staged_media_files(staged_files)
            self._log(
                "error",
                "Media input staging failed",
                package.metadata.id,
                job_id,
                {"error": str(exc)},
            )
            raise MediaInputStagingError("Noofy could not prepare the selected media for this workflow run.") from exc
        except Exception:
            cleanup_staged_media_files(staged_files)
            raise

        return MediaInputStagingResult(inputs=resolved_inputs or inputs, staged_files=staged_files)

    def _package_asset_path(self, package: WorkflowPackage, value: dict[str, Any]) -> Path:
        try:
            asset_id = safe_package_asset_id(str(value.get("asset_id", "")))
        except PackageAssetError as exc:
            raise MediaInputStagingError("The packaged default media reference is invalid.") from exc

        for package_dir in self._package_asset_dirs(package):
            for candidate in package_asset_source_candidates(package_dir, asset_id):
                if candidate.is_file():
                    try:
                        reference = validate_package_asset_reference(value)
                        validate_package_asset_file(candidate, reference)
                    except PackageAssetError as exc:
                        raise MediaInputStagingError("The packaged default media failed integrity validation.") from exc
                    return candidate
        self._log(
            "warning",
            "Packaged media default file was not found",
            package.metadata.id,
            "",
            {"asset_id": asset_id},
        )
        raise MediaInputStagingError("The packaged default media for this input could not be found.")

    def _package_asset_dirs(self, package: WorkflowPackage) -> list[Path]:
        dirs: list[Path] = []
        if self.dashboard_overrides_dir is not None:
            override = self.dashboard_overrides_dir / safe_store_segment(package.metadata.id)
            if override.exists():
                dirs.append(override)
        if self.workflow_store_dir is not None:
            candidate = mutable_package_dir(self.workflow_store_dir, package)
            if candidate is not None and candidate.exists():
                dirs.append(candidate)
        for root in self.package_search_roots:
            if not root.exists():
                continue
            for package_file in sorted({*root.glob("*/package.json"), *root.glob("*/*/*/package.json")}):
                try:
                    data = json.loads(package_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                metadata = data.get("metadata")
                if isinstance(metadata, dict) and metadata.get("id") == package.metadata.id:
                    dirs.append(package_file.parent)
        seen: set[Path] = set()
        unique: list[Path] = []
        for path in dirs:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            unique.append(path)
        return unique

    def _input_dir_for_runner(self, runner: RunnerDescriptor, adapter: object) -> Path | None:
        adapter_input_dir = getattr(adapter, "comfyui_input_dir", None)
        if isinstance(adapter_input_dir, Path):
            return adapter_input_dir
        if runner.runner_workspace_path:
            return Path(runner.runner_workspace_path) / "input"
        return None

    def _stage_once(
        self,
        staging_dir: Path | None,
        source_path: Path,
        *,
        key: str,
        staged_name: str,
        staged_by_key: dict[str, Path],
        staged_files: list[Path],
    ) -> Path:
        if staging_dir is None:
            raise MediaInputStagingError("Noofy could not prepare the selected media for this workflow run.")
        staged = staged_by_key.get(key)
        if staged is not None:
            return staged
        staging_dir.mkdir(parents=True, exist_ok=True)
        staged_path = staging_dir / _safe_staged_name(staged_name)
        _stage_media_file(source_path, staged_path)
        staged_by_key[key] = staged_path
        staged_files.append(staged_path)
        return staged_path

    def _log_gallery_failure(
        self,
        workflow_id: str,
        job_id: str,
        input_id: str,
        reason: str,
        gallery_item_id: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        details = {"input_id": input_id, "gallery_item_id": gallery_item_id, "reason": reason}
        if extra:
            details.update(extra)
        self._log("warning", "Gallery media input could not be staged", workflow_id, job_id, details)

    def _log(
        self,
        level: str,
        message: str,
        workflow_id: str,
        job_id: str,
        details: dict[str, Any],
    ) -> None:
        if self.log_store is not None:
            self.log_store.add(level, message, "runs.media_staging", workflow_id=workflow_id, job_id=job_id, details=details)  # type: ignore[arg-type]


def _stage_media_file(source: Path, target: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def cleanup_staged_media_files(paths: list[Path]) -> None:
    for path in paths:
        with contextlib.suppress(OSError):
            path.unlink()


def _safe_staged_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in name)[:220] or "media-input.bin"
