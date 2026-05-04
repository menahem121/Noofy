import json
from pathlib import Path
from typing import Any

from app.workflows.package import DashboardSchema, WorkflowInput, WorkflowOutput, WorkflowPackage

_STUB_DASHBOARD_VERSION = "0.1.0"


def _load_dashboard_from_dir(package_dir: Path) -> tuple[list[WorkflowInput], list[WorkflowOutput], DashboardSchema]:
    """Load dashboard.json from a package directory and split into inputs/outputs/schema."""
    dashboard_file = package_dir / "dashboard.json"
    if not dashboard_file.exists():
        return [], [], DashboardSchema(version=_STUB_DASHBOARD_VERSION, status="not_configured")

    with dashboard_file.open("r", encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)

    inputs: list[WorkflowInput] = []
    outputs: list[WorkflowOutput] = []

    raw_inputs = raw.pop("inputs", None)
    raw_outputs = raw.pop("outputs", None)

    if isinstance(raw_inputs, list):
        for item in raw_inputs:
            try:
                inputs.append(WorkflowInput.model_validate(item))
            except Exception:
                pass

    if isinstance(raw_outputs, list):
        for item in raw_outputs:
            try:
                outputs.append(WorkflowOutput.model_validate(item))
            except Exception:
                pass

    try:
        schema = DashboardSchema.model_validate(raw)
    except Exception:
        schema = DashboardSchema(version=_STUB_DASHBOARD_VERSION, status="not_configured")

    return inputs, outputs, schema


class WorkflowPackageLoader:
    """Load workflow packages from bundled and user directories.

    Bundled workflows are read-only starter packages shipped with the app.
    User workflows live in the app-data directory. Product behavior must not
    silently let a user package shadow a bundled workflow by matching
    ``metadata.id``. Development tooling can opt into overrides explicitly.
    """

    def __init__(
        self,
        packages_dir: Path,
        user_packages_dir: Path | None = None,
        imported_packages_dir: Path | None = None,
        allow_user_overrides: bool = False,
    ) -> None:
        self.packages_dir = packages_dir
        self.user_packages_dir = user_packages_dir
        self.imported_packages_dir = imported_packages_dir
        self.allow_user_overrides = allow_user_overrides

    def list_packages(self) -> list[WorkflowPackage]:
        by_id: dict[str, WorkflowPackage] = {}

        # Bundled (lower priority)
        for package in self._load_from(self.packages_dir):
            by_id[package.metadata.id] = package

        # User packages cannot shadow bundled packages unless a development
        # caller opts into that behavior explicitly.
        for user_dir in self._user_search_dirs():
            for package in self._load_from(user_dir):
                if package.metadata.id in by_id and not self.allow_user_overrides:
                    continue
                by_id[package.metadata.id] = package

        return sorted(by_id.values(), key=lambda p: p.metadata.id)

    def get_package(self, workflow_id: str) -> WorkflowPackage:
        for package in self.list_packages():
            if package.metadata.id == workflow_id:
                return package
        raise KeyError(f"Unknown workflow package: {workflow_id}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_from(self, directory: Path) -> list[WorkflowPackage]:
        packages: list[WorkflowPackage] = []
        if not directory.exists():
            return packages
        package_files = {
            *directory.glob("*/package.json"),
            *directory.glob("*/*/*/package.json"),
        }
        for package_file in sorted(package_files):
            packages.append(self._load_file(package_file))
        return packages

    def _user_search_dirs(self) -> list[Path]:
        directories: list[Path] = []
        if self.user_packages_dir is not None:
            directories.append(self.user_packages_dir)
        if self.imported_packages_dir is not None and self.imported_packages_dir not in directories:
            directories.append(self.imported_packages_dir)
        return directories

    def _load_file(self, package_file: Path) -> WorkflowPackage:
        with package_file.open("r", encoding="utf-8") as file:
            data: dict[str, Any] = json.load(file)

        package_dir = package_file.parent
        inputs, outputs, dashboard = _load_dashboard_from_dir(package_dir)

        # Inline inputs/outputs in package.json take lower priority than dashboard.json.
        # If dashboard.json provided them, use those; otherwise fall back to inline.
        if not inputs and "inputs" in data:
            raw_inputs = data.get("inputs") or []
            try:
                inputs = [WorkflowInput.model_validate(i) for i in raw_inputs if isinstance(i, dict)]
            except Exception:
                inputs = []

        if not outputs and "outputs" in data:
            raw_outputs = data.get("outputs") or []
            try:
                outputs = [WorkflowOutput.model_validate(o) for o in raw_outputs if isinstance(o, dict)]
            except Exception:
                outputs = []

        # Inline dashboard in package.json takes lower priority than dashboard.json.
        # Only use it if dashboard.json was absent or not configured.
        if dashboard.status == "not_configured" and "dashboard" in data:
            raw_dash = data.get("dashboard")
            if isinstance(raw_dash, dict):
                try:
                    inline_dashboard = DashboardSchema.model_validate(raw_dash)
                    if inline_dashboard.sections:
                        dashboard = inline_dashboard
                        # Inline configured dashboards get promoted to configured status
                        # if they have at least one section with controls.
                        has_controls = any(s.controls for s in inline_dashboard.sections)
                        if has_controls and dashboard.status == "not_configured":
                            dashboard = dashboard.model_copy(update={"status": "configured"})
                except Exception:
                    pass

        # Strip inputs/outputs from data before model_validate to avoid conflicts
        data_clean = {k: v for k, v in data.items() if k not in ("inputs", "outputs", "dashboard")}
        data_clean["inputs"] = [i.model_dump() for i in inputs]
        data_clean["outputs"] = [o.model_dump() for o in outputs]
        data_clean["dashboard"] = dashboard.model_dump()

        return WorkflowPackage.model_validate(data_clean)
