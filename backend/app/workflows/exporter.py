"""Workflow package exporter.

Packs the internal workflow-store copy into a portable .noofy archive.
Never modifies the original imported file or any file in the store.
Strips trust signatures — the exported package is local/user-authored.
"""

from __future__ import annotations

import io
import json
import mimetypes
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.archive_safety import PathSafetyError, safe_relative_posix_path
from app.artifacts import ModelVerificationLevel
from app.gallery import GalleryStore
from app.workflows.assets import workflow_icon_asset_id
from app.workflows.bindings import apply_input_bindings
from app.workflows.import_capsule_lock import (
    ImportCapsuleLockError,
    imported_package_capsule_lock,
    model_locks_from_package,
)
from app.workflows.import_policy import trust_level_from_string
from app.workflows.import_normalization import (
    normalize_unresolved_runtime_inputs,
    repair_misclassified_multimodal_text_inputs,
)
from app.workflows.library import (
    WorkflowLibraryMetadata,
    WorkflowLibraryStore,
    workflow_package_display_name,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.media_values import (
    MEDIA_LOAD_CONTROLS,
    is_empty_media_value,
    is_gallery_media_reference,
    is_package_asset_value,
    is_uploaded_asset_value,
    media_metadata_matches_input,
    target_media_kind_for_input,
)
from app.workflows.metadata_inference import infer_workflow_category
from app.workflows.package import (
    RequiredModel,
    WorkflowCustomNodeRecord,
    WorkflowInput,
    WorkflowPackage,
)
from app.workflows.package_assets import (
    PackageAssetError,
    make_package_asset_reference,
    package_asset_archive_path,
    package_asset_source_candidates,
    safe_package_asset_id,
    validate_package_asset_file,
    validate_package_asset_reference,
)
from app.workflows.store_paths import mutable_package_dir, path_is_within, safe_store_segment
from app.workflows.user_state import UserStateService
from app.workflows.widget_metadata import normalize_comfyui_widget_metadata

MAX_EXPORTED_DEFAULT_ASSET_BYTES = 512 * 1024 * 1024
MAX_COMFYUI_WORKFLOW_JSON_BYTES = 16 * 1024 * 1024
COMFYUI_WORKFLOW_FILENAME = "comfyui_workflow.json"
COMFYUI_WORKFLOW_BINDINGS_FILENAME = "comfyui_workflow_bindings.json"
_SKIP_WORKFLOW_WIDGET_VALUE = object()


class WorkflowExportError(Exception):
    pass


class WorkflowExporter:
    def __init__(
        self,
        workflow_store_dir: Path,
        workflow_loader: WorkflowPackageLoader,
        user_state_service: UserStateService | None = None,
        workflow_library_store: WorkflowLibraryStore | None = None,
        dashboard_assets_dir: Path | None = None,
        dashboard_overrides_dir: Path | None = None,
        gallery_store: GalleryStore | None = None,
    ) -> None:
        self.workflow_store_dir = workflow_store_dir
        self.workflow_loader = workflow_loader
        self.user_state_service = user_state_service
        self.workflow_library_store = workflow_library_store
        self.dashboard_assets_dir = dashboard_assets_dir
        self.dashboard_overrides_dir = dashboard_overrides_dir
        self.gallery_store = gallery_store

    def export_archive(
        self,
        workflow_id: str,
        input_values: dict[str, Any] | None = None,
        export_metadata: dict[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        """Return (archive_bytes, suggested_filename).

        Raises WorkflowExportError if the workflow is unknown.
        """
        package = self._get_package(workflow_id)
        package_dir = self._find_package_dir(workflow_id)
        export_package = _portable_export_package(package, package_dir)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # package.json — identity/models only, stripped of dashboard data and trust sigs.
            metadata = (
                self.workflow_library_store.metadata(package.metadata.id)
                if self.workflow_library_store is not None
                else None
            )
            package_json = _build_export_package_json(
                package_dir,
                export_package,
                metadata,
                export_metadata=export_metadata,
            )
            zf.writestr("package.json", json.dumps(package_json, indent=2, sort_keys=True))
            self._write_workflow_icon_asset(zf, package_json)

            # comfyui_graph.json — the stored opaque execution graph. Current
            # export defaults are written to dashboard.json so media values can
            # remain safe package assets instead of graph-local paths.
            bound_graph = self._portable_comfyui_graph(export_package, package_dir=package_dir)
            zf.writestr(
                "comfyui_graph.json",
                json.dumps(bound_graph, indent=2, sort_keys=True),
            )
            editable_workflow = self._stored_comfyui_workflow(package_dir)
            workflow_bindings = self._stored_comfyui_workflow_bindings(package_dir)
            if editable_workflow is not None and workflow_bindings is not None:
                zf.writestr(
                    COMFYUI_WORKFLOW_FILENAME,
                    json.dumps(editable_workflow, indent=2, sort_keys=True),
                )
                zf.writestr(
                    COMFYUI_WORKFLOW_BINDINGS_FILENAME,
                    json.dumps(workflow_bindings, indent=2, sort_keys=True),
                )

            # dashboard.json — the configured dashboard (inputs, outputs, sections, status).
            dashboard_data: dict[str, Any] = export_package.dashboard.model_dump(
                mode="json",
                exclude_none=True,
            )
            dashboard_data["inputs"] = [i.model_dump(mode="json") for i in export_package.inputs]
            dashboard_data["outputs"] = [o.model_dump(mode="json") for o in export_package.outputs]
            dashboard_data = self._dashboard_with_export_defaults(
                dashboard_data,
                export_package,
                input_values=input_values,
            )
            dashboard_data = self._dashboard_with_user_setup(
                dashboard_data,
                export_package,
            )
            dashboard_data = self._portable_dashboard(dashboard_data)
            self._write_dashboard_package_assets(
                zf,
                dashboard_data,
                package=export_package,
                package_dir=package_dir,
            )
            zf.writestr("dashboard.json", json.dumps(dashboard_data, indent=2, sort_keys=True))

            # capsule.lock.json — rebuilt/sanitized so local cache refs never
            # become part of a portable workflow archive.
            capsule_json = _build_export_capsule_lock_json(
                export_package,
                package_dir,
            )
            if capsule_json is not None:
                zf.writestr(
                    "capsule.lock.json",
                    json.dumps(capsule_json, indent=2, sort_keys=True),
                )

            # export-report.json — stub so importers that require it don't fail.
            export_report_file = package_dir / "export-report.json" if package_dir is not None else None
            if export_report_file is not None and export_report_file.exists():
                zf.write(export_report_file, "export-report.json")
            else:
                zf.writestr("export-report.json", json.dumps({}))
            self._write_original_package_payload_files(zf, package_dir)

        filename = f"{_safe_filename(workflow_package_display_name(package, metadata))}.noofy"
        return buf.getvalue(), filename

    def export_comfyui_graph(
        self,
        workflow_id: str,
        input_values: dict[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        """Return an editable ComfyUI workflow when available, else the API prompt."""
        package = self._get_package(workflow_id)
        package_dir = self._find_package_dir(workflow_id)
        graph = self._bound_comfyui_graph(
            package,
            package_dir=package_dir,
            input_values=input_values,
        )
        editable_workflow = self._stored_comfyui_workflow(package_dir)
        workflow_bindings = self._stored_comfyui_workflow_bindings(package_dir)
        if editable_workflow is not None and workflow_bindings is not None:
            exported = _workflow_with_bound_inputs(
                editable_workflow,
                workflow_bindings,
                package=package,
                bound_graph=graph,
            )
        else:
            exported = graph
        payload = json.dumps(exported, indent=2, sort_keys=True).encode("utf-8")
        return payload, f"{_safe_filename(workflow_id)}.comfyui.json"

    def _portable_dashboard(self, dashboard_data: dict[str, Any]) -> dict[str, Any]:
        exported = json.loads(json.dumps(dashboard_data))
        for section in exported.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for control in section.get("controls") or []:
                if not isinstance(control, dict):
                    continue
                if control.get("type") == "api_credential":
                    control.pop("configured", None)
                    control.pop("last_four", None)
                    control.pop("value", None)

        credential_input_ids = _credential_input_ids(exported)
        for item in exported.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            input_id = item.get("id")
            if isinstance(input_id, str) and input_id in credential_input_ids:
                item["default"] = None
        return exported

    def _dashboard_with_export_defaults(
        self,
        dashboard_data: dict[str, Any],
        package: WorkflowPackage,
        *,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        exported = json.loads(json.dumps(dashboard_data))
        values = self._export_input_values(package, input_values=input_values)
        package_inputs_by_id = {workflow_input.id: workflow_input for workflow_input in package.inputs}
        for item in exported.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            workflow_input = _workflow_input_for_dashboard_item(item, package_inputs_by_id)
            if workflow_input is None:
                continue
            if workflow_input.id not in values:
                continue
            if workflow_input.control == "api_credential":
                item["default"] = None
                continue
            value = _export_safe_user_value(values[workflow_input.id])
            item["default"] = value
            item["default_pinned"] = True
        return exported

    def _dashboard_with_user_setup(
        self,
        dashboard_data: dict[str, Any],
        package: WorkflowPackage,
    ) -> dict[str, Any]:
        if self.user_state_service is None:
            return dashboard_data

        user_state = self.user_state_service.get(package.metadata.id)
        if not user_state.layout_overrides and user_state.presentation_overrides.action_bar is None:
            return dashboard_data

        exported = json.loads(json.dumps(dashboard_data))
        layout_overrides = {
            control_id: override.model_dump(mode="json")
            for control_id, override in user_state.layout_overrides.items()
        }
        for section in exported.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for control in section.get("controls") or []:
                if not isinstance(control, dict):
                    continue
                control_id = control.get("id")
                if isinstance(control_id, str) and control_id in layout_overrides:
                    control["layout"] = _portable_layout_override(
                        layout_overrides[control_id],
                        existing=control.get("layout"),
                    )
            for group in section.get("groups") or []:
                if not isinstance(group, dict):
                    continue
                group_id = group.get("id")
                if isinstance(group_id, str) and group_id in layout_overrides:
                    group["layout"] = _portable_layout_override(
                        layout_overrides[group_id],
                        existing=group.get("layout"),
                    )

        action_bar = user_state.presentation_overrides.action_bar
        if action_bar is not None:
            presentation = exported.get("presentation")
            if not isinstance(presentation, dict):
                presentation = {}
                exported["presentation"] = presentation
            presentation["action_bar"] = action_bar.model_dump(mode="json")
        return exported

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _bound_comfyui_graph(
        self,
        package: WorkflowPackage,
        *,
        package_dir: Path | None,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = self._export_input_values(package, input_values=input_values)
        stored_graph = self._stored_comfyui_graph(package_dir)
        if stored_graph is not None:
            package = package.model_copy(update={"comfyui_graph": stored_graph})
        return apply_input_bindings(package, values)

    def _portable_comfyui_graph(
        self,
        package: WorkflowPackage,
        *,
        package_dir: Path | None,
    ) -> dict[str, Any]:
        stored_graph = self._stored_comfyui_graph(package_dir)
        if stored_graph is not None:
            return stored_graph
        return json.loads(json.dumps(package.comfyui_graph))

    def _write_workflow_icon_asset(
        self,
        zf: zipfile.ZipFile,
        package_json: dict[str, Any],
    ) -> None:
        if self.dashboard_assets_dir is None:
            return
        icon_id = _package_metadata_icon(package_json)
        if not icon_id:
            return
        try:
            asset_id = workflow_icon_asset_id(icon_id)
        except ValueError:
            return
        asset_path = self.dashboard_assets_dir / asset_id
        if not asset_path.exists():
            return
        zf.write(asset_path, f"assets/workflow-icons/{asset_id}")
        meta_path = self.dashboard_assets_dir / f"{asset_id}.meta.json"
        if meta_path.exists():
            zf.write(meta_path, f"assets/workflow-icons/{asset_id}.meta.json")

    def _write_dashboard_package_assets(
        self,
        zf: zipfile.ZipFile,
        dashboard_data: dict[str, Any],
        *,
        package: WorkflowPackage,
        package_dir: Path | None,
    ) -> None:
        written: set[str] = set()
        package_inputs_by_id = {workflow_input.id: workflow_input for workflow_input in package.inputs}
        for item in dashboard_data.get("inputs") or []:
            if not isinstance(item, dict):
                continue
            workflow_input = _workflow_input_for_dashboard_item(item, package_inputs_by_id)
            if workflow_input is None or workflow_input.control not in MEDIA_LOAD_CONTROLS:
                continue
            default = item.get("default")
            if is_package_asset_value(default):
                try:
                    reference = validate_package_asset_reference(default, workflow_input=workflow_input)
                    asset_id = safe_package_asset_id(reference["asset_id"])
                except PackageAssetError:
                    raise WorkflowExportError(f"Workflow input '{workflow_input.id}' has an invalid packaged default asset.")
                source_path = self._find_package_asset_file(package_dir, asset_id, workflow_id=package.metadata.id)
                if source_path is None:
                    raise WorkflowExportError(f"Workflow input '{workflow_input.id}' references a missing packaged default asset.")
                try:
                    validate_package_asset_file(source_path, reference)
                except PackageAssetError as exc:
                    raise WorkflowExportError(f"Workflow input '{workflow_input.id}' has a packaged default asset that failed integrity validation.") from exc
                item["default"] = reference
                self._write_package_asset_file(zf, source_path, reference, written)
                continue
            if is_empty_media_value(default):
                continue
            if is_uploaded_asset_value(default):
                if self.dashboard_assets_dir is None:
                    raise WorkflowExportError("Noofy could not package an uploaded creator default because dashboard assets are unavailable.")
                source_path = self.dashboard_assets_dir / str(default)
                if not source_path.is_file():
                    raise WorkflowExportError("Noofy could not package an uploaded creator default because the source file is missing.")
                metadata = self._dashboard_asset_metadata(str(default))
                self._write_export_media_default(
                    zf,
                    item,
                    workflow_input,
                    source_path,
                    written,
                    kind=str(metadata.get("kind") or target_media_kind_for_input(workflow_input) or "file"),
                    content_type=metadata.get("content_type") if isinstance(metadata.get("content_type"), str) else None,
                    original_filename=(
                        metadata.get("original_filename")
                        if isinstance(metadata.get("original_filename"), str)
                        else source_path.name
                    ),
                )
                continue
            if is_gallery_media_reference(default):
                if self.gallery_store is None:
                    raise WorkflowExportError("Noofy could not package a Gallery item because the Gallery is unavailable.")
                gallery_item_id = str(default["gallery_item_id"])
                gallery_item = self.gallery_store.get_item(gallery_item_id)
                source_path = self.gallery_store.content_path(gallery_item_id)
                if gallery_item is None or source_path is None:
                    raise WorkflowExportError(f"Workflow input '{workflow_input.id}' references a Gallery item that could not be found.")
                if gallery_item.file_state != "available" or not source_path.is_file():
                    raise WorkflowExportError(f"Workflow input '{workflow_input.id}' references a Gallery item file that is unavailable.")
                if not media_metadata_matches_input(
                    workflow_input,
                    kind=gallery_item.kind,
                    extension=gallery_item.extension,
                    mime_type=gallery_item.mime_type,
                ):
                    raise WorkflowExportError(f"Workflow input '{workflow_input.id}' references a Gallery item that is not compatible with this input.")
                self._write_export_media_default(
                    zf,
                    item,
                    workflow_input,
                    source_path,
                    written,
                    kind=target_media_kind_for_input(workflow_input) if workflow_input.control == "load_file" else gallery_item.kind,
                    content_type=gallery_item.mime_type,
                    original_filename=gallery_item.filename,
                )
                continue
            local_source_path = _local_media_default_path(default)
            if local_source_path is not None:
                self._write_export_media_default(
                    zf,
                    item,
                    workflow_input,
                    local_source_path,
                    written,
                    kind=target_media_kind_for_input(workflow_input) or "file",
                    content_type=mimetypes.guess_type(local_source_path.name)[0],
                    original_filename=local_source_path.name,
                )
                continue
            raise WorkflowExportError(_nonportable_media_default_message(workflow_input, default))

    def _write_export_media_default(
        self,
        zf: zipfile.ZipFile,
        item: dict[str, Any],
        workflow_input: WorkflowInput,
        source_path: Path,
        written: set[str],
        *,
        kind: str,
        content_type: str | None,
        original_filename: str,
    ) -> None:
        try:
            stat = source_path.stat()
        except OSError as exc:
            raise WorkflowExportError(f"Workflow input '{workflow_input.id}' has a media default that could not be read.") from exc
        if not source_path.is_file():
            raise WorkflowExportError(f"Workflow input '{workflow_input.id}' has a media default that is not a file.")
        if stat.st_size > MAX_EXPORTED_DEFAULT_ASSET_BYTES:
            raise WorkflowExportError(
                f"Workflow input '{workflow_input.id}' has a media default that is too large to bundle in a .noofy package."
            )
        if not media_metadata_matches_input(
            workflow_input,
            kind=kind,
            extension=source_path.suffix,
            mime_type=content_type,
        ):
            raise WorkflowExportError(
                f"Workflow input '{workflow_input.id}' has a media default that is not compatible with this input."
            )
        try:
            reference, _asset_id = make_package_asset_reference(
                source_path=source_path,
                kind=kind,
                original_filename=original_filename,
                content_type=content_type,
            )
            reference = validate_package_asset_reference(reference, workflow_input=workflow_input)
        except (OSError, PackageAssetError) as exc:
            raise WorkflowExportError(f"Workflow input '{workflow_input.id}' has a media default that could not be packaged.") from exc
        item["default"] = reference
        item["default_pinned"] = True
        self._write_package_asset_file(zf, source_path, reference, written)

    def _write_package_asset_file(
        self,
        zf: zipfile.ZipFile,
        source_path: Path,
        reference: dict[str, Any],
        written: set[str],
    ) -> None:
        archive_path = package_asset_archive_path(reference["asset_id"])
        if archive_path not in written:
            zf.write(source_path, archive_path)
            written.add(archive_path)
        meta_path = f"{archive_path}.meta.json"
        if meta_path not in written:
            zf.writestr(meta_path, json.dumps(reference, indent=2, sort_keys=True))
            written.add(meta_path)

    def _find_package_asset_file(
        self,
        package_dir: Path | None,
        asset_id: str,
        *,
        workflow_id: str,
    ) -> Path | None:
        if self.dashboard_overrides_dir is not None:
            override_dir = self.dashboard_overrides_dir / safe_store_segment(workflow_id)
            for candidate in package_asset_source_candidates(override_dir, asset_id):
                if candidate.is_file():
                    return candidate
        if package_dir is None:
            return None
        for candidate in package_asset_source_candidates(package_dir, asset_id):
            if candidate.is_file():
                return candidate
        return None

    def _dashboard_asset_metadata(self, asset_id: str) -> dict[str, Any]:
        metadata: dict[str, Any] = {"asset_id": asset_id, "original_filename": asset_id}
        if self.dashboard_assets_dir is None:
            return metadata
        meta_path = self.dashboard_assets_dir / f"{asset_id}.meta.json"
        if not meta_path.exists():
            return metadata
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return metadata
        if isinstance(raw, dict):
            metadata.update(raw)
        return metadata

    def _write_original_package_payload_files(
        self,
        zf: zipfile.ZipFile,
        package_dir: Path | None,
    ) -> None:
        if package_dir is None:
            return
        source_files_dir = package_dir / "source-files"
        if not source_files_dir.is_dir():
            return
        written = set(zf.namelist())
        for root_name in ("assets", "custom_nodes"):
            root = source_files_dir / root_name
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
                if path.is_dir():
                    continue
                if path.is_symlink():
                    raise WorkflowExportError(
                        "This workflow cannot be exported because its original package "
                        "payload contains a symlink."
                    )
                if not path.is_file():
                    continue
                try:
                    archive_path = path.relative_to(source_files_dir).as_posix()
                    archive_path = safe_relative_posix_path(archive_path, allow_nested=True)
                except (PathSafetyError, ValueError) as exc:
                    raise WorkflowExportError(
                        "This workflow cannot be exported because its original package "
                        "payload contains an unsafe path."
                    ) from exc
                if archive_path in written:
                    continue
                zf.write(path, archive_path)
                written.add(archive_path)

    def _stored_comfyui_graph(self, package_dir: Path | None) -> dict[str, Any] | None:
        if package_dir is None:
            return None
        graph_file = stored_comfyui_graph_file(package_dir)
        if not graph_file.exists():
            return None
        graph = json.loads(graph_file.read_text(encoding="utf-8"))
        stored_package = _read_stored_package_json(package_dir / "package.json")
        unresolved_inputs = normalize_unresolved_runtime_inputs(
            stored_package.get("unresolved_runtime_inputs")
        )
        repaired_graph, _ = repair_misclassified_multimodal_text_inputs(
            graph,
            unresolved_inputs,
        )
        return repaired_graph

    def _stored_comfyui_workflow(
        self,
        package_dir: Path | None,
    ) -> dict[str, Any] | None:
        return _read_optional_json_object(
            stored_comfyui_workflow_file(package_dir) if package_dir is not None else None
        )

    def _stored_comfyui_workflow_bindings(
        self,
        package_dir: Path | None,
    ) -> dict[str, Any] | None:
        value = _read_optional_json_object(
            stored_comfyui_workflow_bindings_file(package_dir) if package_dir is not None else None
        )
        return _valid_comfyui_workflow_bindings(value)

    def _export_input_values(
        self,
        package: WorkflowPackage,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Lowest to highest precedence: dashboard defaults, saved per-user
        # Run-page state, then values submitted by the currently open Run page.
        values = {item.id: item.default for item in package.inputs if item.default is not None}
        package_defaults = {item.id: item.default for item in package.inputs}
        saved_value_ids: set[str] = set()
        if self.user_state_service is not None:
            user_state = self.user_state_service.get(package.metadata.id)
            saved_value_ids = set(user_state.values or {})
            values.update(user_state.values or {})
        if input_values is not None:
            for input_id, value in input_values.items():
                if value is None and package_defaults.get(input_id) is None and input_id not in saved_value_ids:
                    continue
                values[input_id] = value

        credential_input_ids = {
            item.id for item in package.inputs if item.control == "api_credential"
        }
        for input_id in credential_input_ids:
            if input_id in values:
                values[input_id] = _export_safe_user_value(
                    values[input_id],
                    credential=True,
                )
        return values

    def _get_package(self, workflow_id: str) -> WorkflowPackage:
        try:
            return self.workflow_loader.get_package(workflow_id)
        except KeyError as exc:
            raise WorkflowExportError(f"Unknown workflow: {workflow_id}") from exc

    def _find_package_dir(self, workflow_id: str) -> Path | None:
        package = self._get_package(workflow_id)
        candidate = mutable_package_dir(self.workflow_store_dir, package)
        if (
            candidate is not None
            and candidate.exists()
            and path_is_within(self.workflow_store_dir, candidate)
        ):
            return candidate

        for root in self._package_search_roots():
            if not root.exists():
                continue
            package_files = {
                *root.glob("*/package.json"),
                *root.glob("*/*/*/package.json"),
            }
            for package_file in sorted(package_files):
                try:
                    data = json.loads(package_file.read_text(encoding="utf-8"))
                except Exception:
                    continue
                metadata = data.get("metadata")
                if (
                    isinstance(metadata, dict)
                    and metadata.get("id") == workflow_id
                    and path_is_within(root, package_file)
                ):
                    return package_file.parent
        return None

    def _package_search_roots(self) -> list[Path]:
        roots = [self.workflow_loader.packages_dir]
        if self.workflow_loader.user_packages_dir is not None:
            roots.append(self.workflow_loader.user_packages_dir)
        if (
            self.workflow_loader.imported_packages_dir is not None
            and self.workflow_loader.imported_packages_dir not in roots
        ):
            roots.append(self.workflow_loader.imported_packages_dir)
        return roots


def _build_export_package_json(
    package_dir: Path | None,
    package: WorkflowPackage,
    metadata: WorkflowLibraryMetadata | None = None,
    export_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the package.json for the exported archive.

    Strips trust signatures. Sets source_policy to 'local'.
    Never embeds dashboard data.
    """
    stored_file = package_dir / "package.json" if package_dir is not None else None
    stored = _read_stored_package_json(stored_file)
    package_metadata = package.metadata.model_dump(mode="json")
    display_name = workflow_package_display_name(package, metadata)
    package_metadata["name"] = display_name
    package_metadata["display_name"] = display_name
    if package.identity:
        publisher_id = package.identity.publisher_id
        package_id = package.identity.package_id
        version = package.identity.version
    else:
        publisher_id = "noofy"
        package_id = package.metadata.id
        version = package.metadata.version

    base: dict[str, Any] = {
        "schema_version": _stored_string(stored, "schema_version", "0.1.0"),
        "engine": package.engine,
        "metadata": package_metadata,
        "display_name": display_name,
        "description": package_metadata.get("description", ""),
        "author": package_metadata.get("author", ""),
        "website": package_metadata.get("website", ""),
        "category": package_metadata.get("category", ""),
        "tags": package_metadata.get("tags", []),
        "icon": package_metadata.get("icon", ""),
        "publisher_id": publisher_id,
        "package_id": package_id,
        "version": version,
        "required_models": _export_safe_required_models(
            [
                model.model_dump(mode="json", exclude_none=True)
                for model in package.required_models
            ]
        ),
        "custom_nodes": _export_safe_custom_nodes(package),
        "source_policy": "local",
        "trust_level": "quarantined_community",
    }
    smoke_tests = package.smoke_tests.model_dump(mode="json", exclude_none=True)
    if smoke_tests:
        base["smoke_tests"] = smoke_tests
    comfyui_widget_metadata = normalize_comfyui_widget_metadata(
        package.comfyui_widget_metadata,
        graph=package.comfyui_graph,
    )
    if comfyui_widget_metadata:
        base["comfyui_widget_metadata"] = comfyui_widget_metadata

    if metadata is not None:
        _apply_library_metadata(base, metadata)
    if export_metadata is not None:
        _apply_export_metadata(base, export_metadata)
    _apply_inferred_metadata_defaults(base, package)

    return base


def _portable_export_package(
    package: WorkflowPackage,
    package_dir: Path | None,
) -> WorkflowPackage:
    package = _package_with_capsule_model_facts(package, package_dir)
    return package.model_copy(
        update={
            "required_models": [
                _portable_required_model(model)
                for model in package.required_models
            ],
            "custom_nodes": [_portable_custom_node(node) for node in package.custom_nodes],
        }
    )


def _package_with_capsule_model_facts(
    package: WorkflowPackage,
    package_dir: Path | None,
) -> WorkflowPackage:
    capsule_models = _stored_capsule_model_facts(package_dir)
    if not capsule_models:
        return package
    updated_models: list[RequiredModel] = []
    changed = False
    for model in package.required_models:
        facts = capsule_models.get((model.folder, model.filename))
        if facts is None:
            updated_models.append(model)
            continue
        source_urls = model.source_urls or [
            url for url in facts.get("source_urls", []) if isinstance(url, str)
        ]
        source_url = model.source_url
        if source_url is None and source_urls:
            source_url = source_urls[0]
        update: dict[str, Any] = {
            "source_url": source_url,
            "source_urls": source_urls,
        }
        checksum = model.checksum
        fact_checksum = facts.get("sha256")
        if checksum is None and isinstance(fact_checksum, str):
            checksum = fact_checksum
            update["checksum"] = checksum
        size_bytes = model.size_bytes
        fact_size = facts.get("size_bytes")
        if size_bytes is None and isinstance(fact_size, int) and fact_size > 0:
            size_bytes = fact_size
            update["size_bytes"] = size_bytes
        if checksum is not None and size_bytes is not None:
            update["verification_level"] = ModelVerificationLevel.SHA256_SIZE
        updated = model.model_copy(update=update)
        changed = changed or updated != model
        updated_models.append(updated)
    if not changed:
        return package
    return package.model_copy(update={"required_models": updated_models})


def _stored_capsule_model_facts(
    package_dir: Path | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    capsule = _read_optional_json_object(
        package_dir / "capsule.lock.json" if package_dir is not None else None
    )
    if not isinstance(capsule, dict):
        return {}
    models = capsule.get("models")
    if not isinstance(models, list):
        return {}
    facts: dict[tuple[str, str], dict[str, Any]] = {}
    for model in models:
        if not isinstance(model, dict):
            continue
        folder = model.get("comfyui_folder")
        filename = model.get("filename")
        if isinstance(folder, str) and isinstance(filename, str):
            facts[(folder, filename)] = model
    return facts


def _portable_required_model(model: RequiredModel) -> RequiredModel:
    source_url = _portable_source_url(model.source_url)
    source_urls = [
        url
        for url in (_portable_source_url(url) for url in model.source_urls)
        if url is not None
    ]
    if source_url is None and source_urls:
        source_url = source_urls[0]
    return model.model_copy(
        update={
            "source_url": source_url,
            "source_urls": source_urls,
            "identity_verified_by_exporter": None,
            "local_file_available_at_export": None,
            "identity_warnings": [],
        }
    )


def _portable_custom_node(node: WorkflowCustomNodeRecord) -> WorkflowCustomNodeRecord:
    return node.model_copy(update={"source_cache_ref": None})


def _build_export_capsule_lock_json(
    package: WorkflowPackage,
    package_dir: Path | None,
) -> dict[str, Any] | None:
    _validate_portable_custom_node_sources(package, package_dir)
    try:
        capsule = imported_package_capsule_lock(package)
        payload = capsule.model_dump(mode="json", exclude_none=True)
    except ImportCapsuleLockError as exc:
        capsule_file = package_dir / "capsule.lock.json" if package_dir is not None else None
        if capsule_file is None or not capsule_file.exists():
            if any(node.included for node in package.custom_nodes) or any(
                model.checksum and model.size_bytes for model in package.required_models
            ):
                raise WorkflowExportError(
                    "This workflow cannot be exported because Noofy could not "
                    "rebuild its portable preparation lock."
                ) from exc
            return None
        payload = _read_optional_json_object(capsule_file)
        if not isinstance(payload, dict):
            raise WorkflowExportError(
                "This workflow cannot be exported because its preparation lock is invalid."
            ) from exc
        payload = dict(payload)
        payload["custom_nodes"] = _portable_custom_node_locks(package)
        model_locks = _portable_model_locks(package)
        if model_locks:
            payload["models"] = model_locks
    return _sanitize_export_capsule_lock_payload(payload, package)


def _sanitize_export_capsule_lock_payload(
    payload: dict[str, Any],
    package: WorkflowPackage,
) -> dict[str, Any]:
    exported = json.loads(json.dumps(payload))
    if any(node.included for node in package.custom_nodes):
        exported["custom_nodes"] = _portable_custom_node_locks(package)
    else:
        custom_nodes = exported.get("custom_nodes")
        if isinstance(custom_nodes, list):
            for item in custom_nodes:
                if isinstance(item, dict):
                    item.pop("source_cache_ref", None)
    model_locks = _portable_model_locks(package)
    exported["models"] = (
        model_locks
        if model_locks
        else _sanitize_existing_capsule_model_locks(exported.get("models"))
    )
    return exported


def _portable_model_locks(package: WorkflowPackage) -> list[dict[str, Any]]:
    return [
        model.model_dump(mode="json", exclude_none=True)
        for model in model_locks_from_package(package)
    ]


def _sanitize_existing_capsule_model_locks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    models: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        exported = dict(item)
        source_urls = exported.get("source_urls")
        if isinstance(source_urls, list):
            exported["source_urls"] = [
                sanitized
                for url in source_urls
                if isinstance(url, str)
                for sanitized in [_portable_source_url(url)]
                if sanitized is not None
            ]
        models.append(exported)
    return models


def _portable_custom_node_locks(package: WorkflowPackage) -> list[dict[str, Any]]:
    trust_level = (
        trust_level_from_string(package.identity.trust_level).value
        if package.identity is not None
        else "quarantined_community"
    )
    nodes: list[dict[str, Any]] = []
    for node in package.custom_nodes:
        if not node.included:
            continue
        item: dict[str, Any] = {
            "package_id": node.id,
            "source": node.source,
            "trust_level": trust_level,
            "node_types": node.node_types,
        }
        if node.source_ref:
            item["source_ref"] = node.source_ref
        if node.source_content_hash:
            item["source_content_hash"] = node.source_content_hash
        if node.source_archive_subdir:
            item["source_archive_subdir"] = node.source_archive_subdir
        nodes.append(item)
    return nodes


def _validate_portable_custom_node_sources(
    package: WorkflowPackage,
    package_dir: Path | None,
) -> None:
    missing: list[str] = []
    for node in package.custom_nodes:
        if not node.included:
            continue
        if _is_bundled_custom_node_source(node.source):
            if not _bundled_custom_node_source_exists(package_dir, node):
                missing.append(
                    f"{node.id} ({', '.join(node.node_types) or 'unknown node types'})"
                )
            continue
        if (
            not node.source.startswith("https://")
            or not node.source_ref
            or not node.source_content_hash
        ):
            missing.append(
                f"{node.id} ({', '.join(node.node_types) or 'unknown node types'})"
            )
    if missing:
        raise WorkflowExportError(
            "This workflow cannot be exported because these custom nodes do not "
            "have portable pinned source facts: "
            + "; ".join(missing)
        )


def _is_bundled_custom_node_source(source: str) -> bool:
    return (
        source == "bundled_archive"
        or source == "bundled_from_creator_machine"
        or source.startswith("bundled_archive:")
    )


def _bundled_custom_node_source_exists(
    package_dir: Path | None,
    node: WorkflowCustomNodeRecord,
) -> bool:
    if package_dir is None:
        return False
    custom_nodes_dir = package_dir / "source-files" / "custom_nodes"
    if not custom_nodes_dir.is_dir():
        return False
    explicit_folder = _bundled_custom_node_source_folder(node.source)
    for folder_name in (explicit_folder, node.folder_name, node.id):
        if not folder_name:
            continue
        if (custom_nodes_dir / folder_name).is_dir():
            return True
    wanted = _normalized_custom_node_folder_name(node.folder_name or node.id)
    for candidate in custom_nodes_dir.iterdir():
        if (
            candidate.is_dir()
            and _normalized_custom_node_folder_name(candidate.name) == wanted
        ):
            return True
    return False


def _bundled_custom_node_source_folder(source: str) -> str | None:
    if not source.startswith("bundled_archive:"):
        return None
    try:
        return safe_relative_posix_path(source.split(":", 1)[1], allow_nested=False)
    except PathSafetyError:
        return None


def _normalized_custom_node_folder_name(value: str) -> str:
    return value.replace("_", "-").casefold()


def _read_stored_package_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _workflow_input_for_dashboard_item(
    item: dict[str, Any],
    package_inputs_by_id: dict[str, WorkflowInput],
) -> WorkflowInput | None:
    input_id = item.get("id")
    if isinstance(input_id, str) and input_id in package_inputs_by_id:
        base = package_inputs_by_id[input_id]
        try:
            return base.model_copy(
                update={
                    "default": item.get("default"),
                    "default_pinned": bool(item.get("default_pinned")),
                    "validation": item.get("validation") if isinstance(item.get("validation"), dict) else base.validation,
                    "control": item.get("control") if isinstance(item.get("control"), str) else base.control,
                }
            )
        except Exception:
            return base
    try:
        return WorkflowInput.model_validate(item)
    except Exception:
        return None


def _stored_string(data: dict[str, Any], key: str, fallback: str) -> str:
    value = data.get(key)
    return value if isinstance(value, str) and value.strip() else fallback


def stored_comfyui_graph_file(package_dir: Path) -> Path:
    top_level = package_dir / "comfyui_graph.json"
    if top_level.exists():
        return top_level
    return package_dir / "source-files" / "comfyui_graph.json"


def stored_comfyui_workflow_file(package_dir: Path) -> Path:
    top_level = package_dir / COMFYUI_WORKFLOW_FILENAME
    if top_level.exists():
        return top_level
    return package_dir / "source-files" / COMFYUI_WORKFLOW_FILENAME


def stored_comfyui_workflow_bindings_file(package_dir: Path) -> Path:
    top_level = package_dir / COMFYUI_WORKFLOW_BINDINGS_FILENAME
    if top_level.exists():
        return top_level
    return package_dir / "source-files" / COMFYUI_WORKFLOW_BINDINGS_FILENAME


def _read_optional_json_object(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_COMFYUI_WORKFLOW_JSON_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _valid_comfyui_workflow_bindings(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), dict):
        return None
    return value


def _workflow_with_bound_inputs(
    workflow: dict[str, Any],
    bindings: dict[str, Any] | None,
    *,
    package: WorkflowPackage,
    bound_graph: dict[str, Any],
) -> dict[str, Any]:
    exported = json.loads(json.dumps(workflow))
    if not isinstance(bindings, dict):
        return exported
    binding_nodes = bindings.get("nodes")
    if not isinstance(binding_nodes, dict):
        return exported

    workflow_nodes = _top_level_comfyui_workflow_nodes_by_id(exported)
    for workflow_input in package.inputs:
        node_id = workflow_input.binding.node_id
        node = workflow_nodes.get(str(node_id))
        widget_indexes = binding_nodes.get(str(node_id))
        graph_node = bound_graph.get(node_id)
        if (
            not isinstance(node, dict)
            or not isinstance(widget_indexes, dict)
            or not isinstance(graph_node, dict)
        ):
            continue
        widget_index = widget_indexes.get(workflow_input.binding.input_name)
        widgets_values = node.get("widgets_values")
        graph_inputs = graph_node.get("inputs")
        if (
            not isinstance(widget_index, int)
            or widget_index < 0
            or not isinstance(widgets_values, list)
            or widget_index >= len(widgets_values)
            or not isinstance(graph_inputs, dict)
            or workflow_input.binding.input_name not in graph_inputs
        ):
            continue
        value = _portable_workflow_widget_value(
            graph_inputs[workflow_input.binding.input_name],
            workflow_input,
            graph=bound_graph,
        )
        if value is _SKIP_WORKFLOW_WIDGET_VALUE:
            continue
        widgets_values[widget_index] = value
    return exported


def _top_level_comfyui_workflow_nodes_by_id(
    workflow: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        return {}
    return {
        str(node["id"]): node
        for node in nodes
        if isinstance(node, dict) and "id" in node
    }


def _portable_workflow_widget_value(
    value: Any,
    workflow_input: WorkflowInput,
    *,
    graph: dict[str, Any],
) -> Any:
    if workflow_input.control == "api_credential":
        return _SKIP_WORKFLOW_WIDGET_VALUE
    if _is_comfyui_graph_link(value, graph):
        return _SKIP_WORKFLOW_WIDGET_VALUE
    if workflow_input.control in MEDIA_LOAD_CONTROLS and not (
        value is None or isinstance(value, str)
    ):
        return _SKIP_WORKFLOW_WIDGET_VALUE
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return _SKIP_WORKFLOW_WIDGET_VALUE


def _is_comfyui_graph_link(value: Any, graph: dict[str, Any]) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and str(value[0]) in graph
        and isinstance(value[1], int)
    )


def _apply_library_metadata(base: dict[str, Any], metadata: WorkflowLibraryMetadata) -> None:
    patch = metadata.model_dump(mode="json", exclude_none=True)
    patch.pop("updated_at", None)
    if not patch:
        return
    package_metadata = base.get("metadata")
    if not isinstance(package_metadata, dict):
        package_metadata = {}
    for key, value in patch.items():
        if key == "display_name":
            package_metadata["display_name"] = value
            package_metadata["name"] = value
            base["display_name"] = value
            continue
        package_metadata[key] = value
        if key in {"description", "author", "website", "category", "tags", "icon"}:
            base[key] = value
    base["metadata"] = package_metadata


def _apply_export_metadata(base: dict[str, Any], metadata: dict[str, Any]) -> None:
    package_metadata = base.get("metadata")
    if not isinstance(package_metadata, dict):
        package_metadata = {}

    for key in ("name", "description", "author", "website", "category", "icon"):
        value = metadata.get(key)
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        package_metadata[key] = cleaned
        if key == "name":
            package_metadata["display_name"] = cleaned
            base["display_name"] = cleaned
        else:
            base[key] = cleaned

    tags = metadata.get("tags")
    if isinstance(tags, list):
        cleaned_tags = []
        seen = set()
        for tag in tags:
            if not isinstance(tag, str):
                continue
            cleaned = tag.strip()
            if not cleaned or cleaned.casefold() in seen:
                continue
            seen.add(cleaned.casefold())
            cleaned_tags.append(cleaned)
        if cleaned_tags:
            package_metadata["tags"] = cleaned_tags
            base["tags"] = cleaned_tags

    base["metadata"] = package_metadata


def _apply_inferred_metadata_defaults(base: dict[str, Any], package: WorkflowPackage) -> None:
    package_metadata = base.get("metadata")
    if not isinstance(package_metadata, dict):
        package_metadata = {}
    if _blank_metadata_value(package_metadata.get("category")):
        category = infer_workflow_category(package)
        package_metadata["category"] = category
        base["category"] = category
    base["metadata"] = package_metadata


def _blank_metadata_value(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    return not value.strip()


def _export_safe_required_models(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    models: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        exported = dict(item)
        exported.pop("local_file_available_at_export", None)
        exported.pop("identity_verified_by_exporter", None)
        exported.pop("asset_ownership", None)
        exported.pop("identity_warnings", None)
        source_url = exported.get("source_url")
        if isinstance(source_url, str):
            sanitized = _portable_source_url(source_url)
            if sanitized is not None:
                exported["source_url"] = sanitized
            else:
                exported.pop("source_url", None)
        source_urls = exported.get("source_urls")
        if isinstance(source_urls, list):
            exported["source_urls"] = [
                sanitized
                for url in source_urls
                if isinstance(url, str)
                for sanitized in [_portable_source_url(url)]
                if sanitized is not None
            ]
        models.append(exported)
    return models


def _export_safe_custom_nodes(package: WorkflowPackage) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node in package.custom_nodes:
        exported = node.model_dump(mode="json", exclude_none=True)
        exported.pop("source_cache_ref", None)
        nodes.append(exported)
    return nodes


def _redact_url_secret(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    redacted = [
        (key, "[redacted]" if _is_secret_query_key(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(redacted, doseq=True),
            parts.fragment,
        )
    )


def _portable_source_url(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    if parts.username or parts.password:
        return None
    return _redact_url_secret(url)


def _is_secret_query_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return normalized in {
        "api_key",
        "apikey",
        "access_token",
        "token",
        "auth",
        "authorization",
        "secret",
        "password",
        "signature",
        "x_amz_signature",
        "x_goog_signature",
    }


def _package_metadata_icon(package_json: dict[str, Any]) -> str | None:
    metadata = package_json.get("metadata")
    if isinstance(metadata, dict):
        icon = metadata.get("icon")
        if isinstance(icon, str) and icon.strip():
            return icon.strip()
    icon = package_json.get("icon")
    if isinstance(icon, str) and icon.strip():
        return icon.strip()
    return None


def _credential_input_ids(dashboard_data: dict[str, Any]) -> set[str]:
    input_ids: set[str] = set()
    for section in dashboard_data.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for control in section.get("controls") or []:
            if not isinstance(control, dict):
                continue
            if control.get("type") != "api_credential":
                continue
            input_id = control.get("input_id")
            if isinstance(input_id, str):
                input_ids.add(input_id)
    return input_ids


def _export_safe_user_value(value: Any, *, credential: bool = False) -> Any:
    if credential and not (isinstance(value, dict) and value.get("kind") == "api_key_ref"):
        return None
    if isinstance(value, dict) and value.get("kind") == "api_key_ref":
        return {
            key: item
            for key, item in value.items()
            if key in {"kind", "provider", "secret_ref"}
            and isinstance(item, str)
        }
    return value


def _portable_layout_override(
    override: dict[str, Any],
    *,
    existing: Any,
) -> dict[str, Any]:
    layout = {
        "x": override["x"],
        "y": override["y"],
        "w": override["w"],
        "h": override["h"],
    }
    if isinstance(existing, dict):
        for key in ("min_w", "min_h"):
            value = existing.get(key)
            if isinstance(value, int):
                layout[key] = value
    return layout


def _local_media_default_path(value: Any) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        path = Path(value).expanduser()
    except (OSError, ValueError):
        return None
    if not path.is_absolute():
        return None
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    return path


def _nonportable_media_default_message(workflow_input: WorkflowInput, value: Any) -> str:
    label = workflow_input.label or workflow_input.id
    if isinstance(value, dict) and value.get("source") == "gallery":
        return (
            f"Workflow input '{label}' uses a Gallery item as its current default. "
            "Choose or upload a Noofy asset for this input before exporting the .noofy package."
        )
    if isinstance(value, str) and value.strip():
        try:
            path = Path(value).expanduser()
        except (OSError, ValueError):
            path = None
        if path is not None and path.is_absolute():
            try:
                if not path.exists():
                    return f"Workflow input '{label}' has a media default file that could not be found."
                if not path.is_file():
                    return f"Workflow input '{label}' has a media default path that is not a file."
            except OSError:
                return f"Workflow input '{label}' has a media default file that could not be read."
        return (
            f"Workflow input '{label}' has a media default that cannot be bundled into the .noofy package. "
            "Use a Noofy upload, an existing package asset, or a readable absolute local file before exporting."
        )
    return (
        f"Workflow input '{label}' has a media default that cannot be bundled into the .noofy package."
    )


def _safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in {"-", "_", "."} else "-" for c in name).strip("-_.")
    return cleaned or "workflow"
