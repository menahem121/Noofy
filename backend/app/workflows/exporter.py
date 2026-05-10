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

from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import mutable_package_dir


class WorkflowExportError(Exception):
    pass


class WorkflowExporter:
    def __init__(
        self,
        workflow_store_dir: Path,
        workflow_loader: WorkflowPackageLoader,
    ) -> None:
        self.workflow_store_dir = workflow_store_dir
        self.workflow_loader = workflow_loader

    def export_archive(self, workflow_id: str) -> tuple[bytes, str]:
        """Return (archive_bytes, suggested_filename).

        Raises WorkflowExportError if the workflow cannot be exported
        (e.g. it is a bundled read-only workflow with no mutable store copy).
        """
        package = self._get_package(workflow_id)
        package_dir = self._find_package_dir(workflow_id)
        if package_dir is None:
            raise WorkflowExportError(
                f"Workflow '{workflow_id}' has no mutable store copy and cannot be exported. "
                "Only imported or user-created workflows can be exported."
            )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            # package.json — identity/models only, stripped of dashboard data and trust sigs.
            package_json = _build_export_package_json(package_dir, package)
            zf.writestr("package.json", json.dumps(package_json, indent=2, sort_keys=True))

            # comfyui_graph.json — unchanged from store.
            graph_file = package_dir / "comfyui_graph.json"
            if graph_file.exists():
                zf.write(graph_file, "comfyui_graph.json")
            else:
                # Fall back to in-memory graph if file is absent.
                zf.writestr(
                    "comfyui_graph.json",
                    json.dumps(package.comfyui_graph, indent=2, sort_keys=True),
                )

            # dashboard.json — the configured dashboard (inputs, outputs, sections, status).
            dashboard_file = package_dir / "dashboard.json"
            if dashboard_file.exists():
                zf.write(dashboard_file, "dashboard.json")
            else:
                # Fall back to generating from in-memory model.
                dashboard_data: dict[str, Any] = package.dashboard.model_dump(mode="json")
                dashboard_data["inputs"] = [i.model_dump(mode="json") for i in package.inputs]
                dashboard_data["outputs"] = [o.model_dump(mode="json") for o in package.outputs]
                zf.writestr("dashboard.json", json.dumps(dashboard_data, indent=2, sort_keys=True))

            # capsule.lock.json — if present.
            capsule_file = package_dir / "capsule.lock.json"
            if capsule_file.exists():
                zf.write(capsule_file, "capsule.lock.json")

            # export-report.json — stub so importers that require it don't fail.
            export_report_file = package_dir / "export-report.json"
            if export_report_file.exists():
                zf.write(export_report_file, "export-report.json")
            else:
                zf.writestr("export-report.json", json.dumps({}))

        filename = f"{_safe_filename(workflow_id)}.noofy"
        return buf.getvalue(), filename

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_package(self, workflow_id: str) -> WorkflowPackage:
        try:
            return self.workflow_loader.get_package(workflow_id)
        except KeyError as exc:
            raise WorkflowExportError(f"Unknown workflow: {workflow_id}") from exc

    def _find_package_dir(self, workflow_id: str) -> Path | None:
        package = self._get_package(workflow_id)
        candidate = mutable_package_dir(self.workflow_store_dir, package)
        if candidate is None:
            return None
        return candidate if candidate.exists() else None


def _build_export_package_json(package_dir: Path, package: WorkflowPackage) -> dict[str, Any]:
    """Build the package.json for the exported archive.

    Strips trust signatures. Sets source_policy to 'local'.
    Never embeds dashboard data.
    """
    # Try to read the stored package.json as the base (it was written during import).
    stored_file = package_dir / "package.json"
    if stored_file.exists():
        with stored_file.open("r", encoding="utf-8") as f:
            base: dict[str, Any] = json.load(f)
    else:
        base = {}

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

    # Ensure publisher and package identifiers are present.
    if package.identity:
        base.setdefault("publisher_id", package.identity.publisher_id)
        base.setdefault("package_id", package.identity.package_id)
        base.setdefault("version", package.identity.version)

    return base


def _safe_filename(name: str) -> str:
    return "".join(c if c.isalnum() or c in {"-", "_", "."} else "-" for c in name).strip("-_.")
