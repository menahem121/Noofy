"""Workflow package exporter.

Packs the internal workflow-store copy into a portable .noofy archive.
Never modifies the original imported file or any file in the store.
Strips trust signatures — the exported package is local/user-authored.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from app.workflows.bindings import apply_input_bindings
from app.workflows.library import WorkflowLibraryMetadata, WorkflowLibraryStore
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import mutable_package_dir
from app.workflows.user_state import UserStateService


class WorkflowExportError(Exception):
    pass


class WorkflowExporter:
    def __init__(
        self,
        workflow_store_dir: Path,
        workflow_loader: WorkflowPackageLoader,
        user_state_service: UserStateService | None = None,
        workflow_library_store: WorkflowLibraryStore | None = None,
    ) -> None:
        self.workflow_store_dir = workflow_store_dir
        self.workflow_loader = workflow_loader
        self.user_state_service = user_state_service
        self.workflow_library_store = workflow_library_store

    def export_archive(
        self,
        workflow_id: str,
        input_values: dict[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        """Return (archive_bytes, suggested_filename).

        Raises WorkflowExportError if the workflow is unknown.
        """
        package = self._get_package(workflow_id)
        package_dir = self._find_package_dir(workflow_id)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # package.json — identity/models only, stripped of dashboard data and trust sigs.
            metadata = (
                self.workflow_library_store.metadata(package.metadata.id)
                if self.workflow_library_store is not None
                else None
            )
            package_json = _build_export_package_json(package_dir, package, metadata)
            zf.writestr("package.json", json.dumps(package_json, indent=2, sort_keys=True))

            # comfyui_graph.json — export-time snapshot with current dashboard values applied.
            bound_graph = self._bound_comfyui_graph(
                package,
                package_dir=package_dir,
                input_values=input_values,
            )
            zf.writestr(
                "comfyui_graph.json",
                json.dumps(bound_graph, indent=2, sort_keys=True),
            )

            # dashboard.json — the configured dashboard (inputs, outputs, sections, status).
            dashboard_file = package_dir / "dashboard.json" if package_dir is not None else None
            if dashboard_file is not None and dashboard_file.exists():
                dashboard_data = json.loads(dashboard_file.read_text(encoding="utf-8"))
            else:
                # Fall back to generating from in-memory model.
                dashboard_data: dict[str, Any] = package.dashboard.model_dump(mode="json")
                dashboard_data["inputs"] = [i.model_dump(mode="json") for i in package.inputs]
                dashboard_data["outputs"] = [o.model_dump(mode="json") for o in package.outputs]
            dashboard_data = self._dashboard_with_user_state(
                package.metadata.id,
                dashboard_data,
                input_values=input_values,
            )
            zf.writestr("dashboard.json", json.dumps(dashboard_data, indent=2, sort_keys=True))

            # capsule.lock.json — if present.
            capsule_file = package_dir / "capsule.lock.json" if package_dir is not None else None
            if capsule_file is not None and capsule_file.exists():
                zf.write(capsule_file, "capsule.lock.json")

            # export-report.json — stub so importers that require it don't fail.
            export_report_file = package_dir / "export-report.json" if package_dir is not None else None
            if export_report_file is not None and export_report_file.exists():
                zf.write(export_report_file, "export-report.json")
            else:
                zf.writestr("export-report.json", json.dumps({}))

        filename = f"{_safe_filename(workflow_id)}.noofy"
        return buf.getvalue(), filename

    def export_comfyui_graph(
        self,
        workflow_id: str,
        input_values: dict[str, Any] | None = None,
    ) -> tuple[bytes, str]:
        """Return a bound comfyui_graph.json snapshot for download."""
        package = self._get_package(workflow_id)
        package_dir = self._find_package_dir(workflow_id)
        graph = self._bound_comfyui_graph(
            package,
            package_dir=package_dir,
            input_values=input_values,
        )
        payload = json.dumps(graph, indent=2, sort_keys=True).encode("utf-8")
        return payload, f"{_safe_filename(workflow_id)}.comfyui.json"

    def _dashboard_with_user_state(
        self,
        workflow_id: str,
        dashboard_data: dict[str, Any],
        *,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        exported = json.loads(json.dumps(dashboard_data))

        values: dict[str, Any] = {}
        if self.user_state_service is not None:
            user_state = self.user_state_service.get(workflow_id)
            values.update(user_state.values or {})
        if input_values is not None:
            values.update(input_values)
        credential_input_ids = _credential_input_ids(exported)
        if values:
            for item in exported.get("inputs") or []:
                if not isinstance(item, dict):
                    continue
                input_id = item.get("id")
                if isinstance(input_id, str) and input_id in values:
                    item["default"] = _export_safe_user_value(
                        values[input_id],
                        credential=bool(input_id in credential_input_ids),
                    )

        layout_overrides = {}
        output_preferences = {}
        if self.user_state_service is not None:
            user_state = self.user_state_service.get(workflow_id)
            layout_overrides = user_state.layout_overrides or {}
            output_preferences = user_state.output_preferences or {}
        for section in exported.get("sections") or []:
            if not isinstance(section, dict):
                continue
            for control in section.get("controls") or []:
                if not isinstance(control, dict):
                    continue
                control_id = control.get("id")
                if not isinstance(control_id, str):
                    continue
                layout = layout_overrides.get(control_id)
                if layout is not None:
                    existing = control.get("layout") if isinstance(control.get("layout"), dict) else {}
                    control["layout"] = {
                        **existing,
                        "x": layout.x,
                        "y": layout.y,
                        "w": layout.w,
                        "h": layout.h,
                    }
                preference = output_preferences.get(control_id)
                if preference is not None:
                    control["show_download"] = preference.auto_save
                if control.get("type") == "api_credential":
                    control.pop("configured", None)
                    control.pop("last_four", None)
                    control.pop("value", None)

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

    def _stored_comfyui_graph(self, package_dir: Path | None) -> dict[str, Any] | None:
        if package_dir is None:
            return None
        graph_file = stored_comfyui_graph_file(package_dir)
        if not graph_file.exists():
            return None
        return json.loads(graph_file.read_text(encoding="utf-8"))

    def _export_input_values(
        self,
        package: WorkflowPackage,
        input_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        values = {item.id: item.default for item in package.inputs}
        if self.user_state_service is not None:
            user_state = self.user_state_service.get(package.metadata.id)
            values.update(user_state.values or {})
        if input_values is not None:
            values.update(input_values)

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
        if candidate is not None and candidate.exists():
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
                if isinstance(metadata, dict) and metadata.get("id") == workflow_id:
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
) -> dict[str, Any]:
    """Build the package.json for the exported archive.

    Strips trust signatures. Sets source_policy to 'local'.
    Never embeds dashboard data.
    """
    # Try to read the stored package.json as the base (it was written during import).
    stored_file = package_dir / "package.json" if package_dir is not None else None
    if stored_file is not None and stored_file.exists():
        with stored_file.open("r", encoding="utf-8") as f:
            base: dict[str, Any] = json.load(f)
    else:
        base = package.model_dump(mode="json", exclude_none=True)

    # Remove trust artefacts so the recipient knows this is not verified.
    base.pop("signature", None)
    base.pop("signatures", None)
    base.pop("signed_registry_metadata", None)

    # Mark as local/user-authored.
    base["source_policy"] = "local"

    # Ensure dashboard data is not embedded.
    base.pop("inputs", None)
    base.pop("outputs", None)
    base.pop("dashboard", None)
    base.pop("comfyui_graph", None)

    # Ensure publisher and package identifiers are present.
    if package.identity:
        base.setdefault("publisher_id", package.identity.publisher_id)
        base.setdefault("package_id", package.identity.package_id)
        base.setdefault("version", package.identity.version)
    else:
        base.setdefault("publisher_id", "noofy")
        base.setdefault("package_id", package.metadata.id)
        base.setdefault("version", package.metadata.version)

    if metadata is not None:
        _apply_library_metadata(base, metadata)

    return base


def stored_comfyui_graph_file(package_dir: Path) -> Path:
    top_level = package_dir / "comfyui_graph.json"
    if top_level.exists():
        return top_level
    return package_dir / "source-files" / "comfyui_graph.json"


def _apply_library_metadata(base: dict[str, Any], metadata: WorkflowLibraryMetadata) -> None:
    patch = metadata.model_dump(mode="json", exclude_none=True)
    patch.pop("updated_at", None)
    if not patch:
        return
    package_metadata = base.get("metadata")
    if not isinstance(package_metadata, dict):
        package_metadata = {}
    for key, value in patch.items():
        package_metadata[key] = value
        if key in {"description", "author", "website", "category", "tags", "icon"}:
            base[key] = value
    base["metadata"] = package_metadata


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


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in {"-", "_", "."} else "-" for c in name).strip("-_.")
