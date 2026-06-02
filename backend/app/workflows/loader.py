import json
from pathlib import Path
from typing import Any, Literal

from app.artifacts import ModelVerificationLevel
from app.workflows.import_normalization import (
    filter_resolved_runtime_inputs,
    normalize_custom_nodes,
)
from app.workflows.package import (
    DashboardSchema,
    UnresolvedRuntimeInput,
    WorkflowInput,
    WorkflowOutput,
    WorkflowPackage,
)
from app.workflows.store_paths import safe_store_segment

_STUB_DASHBOARD_VERSION = "0.1.0"
_CAPSULE_LOCK_FILENAME = "capsule.lock.json"
WorkflowPackageSource = Literal["bundled", "user", "imported"]


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

    if (not inputs or not outputs) and any(section.get("controls") for section in raw.get("sections") or [] if isinstance(section, dict)):
        source_dashboard_file = package_dir / "source-files" / "dashboard.json"
        if source_dashboard_file.exists():
            try:
                source_raw = json.loads(source_dashboard_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                source_raw = {}
            if not inputs:
                for item in source_raw.get("inputs") or []:
                    if not isinstance(item, dict):
                        continue
                    try:
                        inputs.append(WorkflowInput.model_validate(item))
                    except Exception:
                        pass
            if not outputs:
                for item in source_raw.get("outputs") or []:
                    if not isinstance(item, dict):
                        continue
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
        dashboard_overrides_dir: Path | None = None,
        allow_user_overrides: bool = False,
    ) -> None:
        self.packages_dir = packages_dir
        self.user_packages_dir = user_packages_dir
        self.imported_packages_dir = imported_packages_dir
        self.dashboard_overrides_dir = dashboard_overrides_dir
        self.allow_user_overrides = allow_user_overrides

    def list_packages(self) -> list[WorkflowPackage]:
        return [package for package, _source in self.list_packages_with_sources()]

    def list_packages_with_sources(self) -> list[tuple[WorkflowPackage, WorkflowPackageSource]]:
        by_id = self._load_packages_by_id()
        return sorted(by_id.values(), key=lambda record: record[0].metadata.id)

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

    def _load_packages_by_id(self) -> dict[str, tuple[WorkflowPackage, WorkflowPackageSource]]:
        by_id: dict[str, tuple[WorkflowPackage, WorkflowPackageSource]] = {}

        # Bundled (lower priority)
        for package in self._load_from(self.packages_dir):
            by_id[package.metadata.id] = (package, "bundled")

        # User packages cannot shadow bundled packages unless a development
        # caller opts into that behavior explicitly.
        for user_dir in self._user_search_dirs():
            source: WorkflowPackageSource = (
                "imported"
                if self.imported_packages_dir is not None and user_dir == self.imported_packages_dir
                else "user"
            )
            for package in self._load_from(user_dir):
                if package.metadata.id in by_id and not self.allow_user_overrides:
                    continue
                by_id[package.metadata.id] = (package, source)

        return by_id

    def _load_file(self, package_file: Path) -> WorkflowPackage:
        with package_file.open("r", encoding="utf-8") as file:
            data: dict[str, Any] = json.load(file)

        package_dir = package_file.parent
        dashboard_dir = self._dashboard_dir_for_package(package_dir, data)
        inputs, outputs, dashboard = _load_dashboard_from_dir(dashboard_dir)

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
        _enrich_required_models_from_capsule_lock(data_clean, package_dir / _CAPSULE_LOCK_FILENAME)
        _repair_imported_custom_nodes_from_source_files(data_clean, package_dir)
        raw_unresolved_inputs = data_clean.get("unresolved_runtime_inputs")
        if isinstance(raw_unresolved_inputs, list):
            package_unresolved: list[UnresolvedRuntimeInput] = []
            for item in raw_unresolved_inputs:
                if not isinstance(item, dict):
                    continue
                try:
                    package_unresolved.append(UnresolvedRuntimeInput.model_validate(item))
                except Exception:
                    continue
            data_clean["unresolved_runtime_inputs"] = [
                item.model_dump()
                for item in filter_resolved_runtime_inputs(package_unresolved, inputs)
            ]
        data_clean["inputs"] = [i.model_dump() for i in inputs]
        data_clean["outputs"] = [o.model_dump() for o in outputs]
        data_clean["dashboard"] = dashboard.model_dump()

        return WorkflowPackage.model_validate(data_clean)

    def _dashboard_dir_for_package(self, package_dir: Path, data: dict[str, Any]) -> Path:
        workflow_id = _package_id_from_data(data)
        if workflow_id is None or self.dashboard_overrides_dir is None:
            return package_dir
        override_dir = self.dashboard_overrides_dir / safe_store_segment(workflow_id)
        if (override_dir / "dashboard.json").exists():
            return override_dir
        return package_dir


def _package_id_from_data(data: dict[str, Any]) -> str | None:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        return None
    workflow_id = metadata.get("id")
    return workflow_id if isinstance(workflow_id, str) and workflow_id else None


def _enrich_required_models_from_capsule_lock(package_data: dict[str, Any], capsule_path: Path) -> None:
    """Fill weak package model identities from an adjacent capsule lock.

    The package is the app contract used by model availability and exports,
    while the capsule lock is the immutable runtime lock. When both are
    present, package metadata must not be weaker than lock metadata.
    """
    raw_models = package_data.get("required_models")
    if not isinstance(raw_models, list) or not capsule_path.exists():
        return
    try:
        capsule_data = json.loads(capsule_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    raw_locked_models = capsule_data.get("models") if isinstance(capsule_data, dict) else None
    if not isinstance(raw_locked_models, list):
        return

    locked_by_path: dict[tuple[str, str], dict[str, Any]] = {}
    for locked in raw_locked_models:
        if not isinstance(locked, dict):
            continue
        folder = locked.get("comfyui_folder")
        filename = locked.get("filename")
        if isinstance(folder, str) and isinstance(filename, str):
            locked_by_path[(folder, filename)] = locked

    for model in raw_models:
        if not isinstance(model, dict):
            continue
        folder = model.get("folder")
        filename = model.get("filename")
        if not isinstance(folder, str) or not isinstance(filename, str):
            continue
        locked = locked_by_path.get((folder, filename))
        if locked is None:
            continue
        _fill_required_model_identity_from_lock(model, locked)


def _repair_imported_custom_nodes_from_source_files(
    package_data: dict[str, Any], package_dir: Path
) -> None:
    raw_nodes = package_data.get("custom_nodes")
    if isinstance(raw_nodes, list) and any(
        isinstance(node, dict) and node.get("included") for node in raw_nodes
    ):
        return
    source_package_path = package_dir / "source-files" / "package.json"
    source_capsule_path = package_dir / "source-files" / _CAPSULE_LOCK_FILENAME
    if not source_package_path.exists() and not source_capsule_path.exists():
        return
    try:
        source_package = (
            json.loads(source_package_path.read_text(encoding="utf-8"))
            if source_package_path.exists()
            else {}
        )
        source_capsule = (
            json.loads(source_capsule_path.read_text(encoding="utf-8"))
            if source_capsule_path.exists()
            else {}
        )
    except (OSError, json.JSONDecodeError):
        return
    repaired = normalize_custom_nodes(source_capsule, source_package)
    if repaired:
        package_data["custom_nodes"] = [
            node.model_dump(mode="json") for node in repaired
        ]


def _fill_required_model_identity_from_lock(model: dict[str, Any], locked: dict[str, Any]) -> None:
    sha256 = locked.get("sha256")
    size_bytes = locked.get("size_bytes")
    source_urls = locked.get("source_urls")
    architecture_family = locked.get("architecture_family")
    architecture_family_confidence = locked.get("architecture_family_confidence")
    architecture_family_source = locked.get("architecture_family_source")

    if isinstance(sha256, str) and not model.get("checksum"):
        model["checksum"] = sha256 if sha256.startswith("sha256:") else f"sha256:{sha256}"
    if isinstance(size_bytes, int) and size_bytes > 0 and not isinstance(model.get("size_bytes"), int):
        model["size_bytes"] = size_bytes
    if isinstance(source_urls, list) and not model.get("source_urls"):
        urls = [url for url in source_urls if isinstance(url, str)]
        if urls:
            model["source_urls"] = urls
            model.setdefault("source_url", urls[0])
    if isinstance(architecture_family, str) and architecture_family.strip() and not model.get("architecture_family"):
        model["architecture_family"] = architecture_family
    if (
        isinstance(architecture_family_confidence, str)
        and architecture_family_confidence.strip()
        and not model.get("architecture_family_confidence")
    ):
        model["architecture_family_confidence"] = architecture_family_confidence
    if (
        isinstance(architecture_family_source, str)
        and architecture_family_source.strip()
        and not model.get("architecture_family_source")
    ):
        model["architecture_family_source"] = architecture_family_source

    has_checksum = isinstance(model.get("checksum"), str) and bool(str(model.get("checksum")).strip())
    has_size = isinstance(model.get("size_bytes"), int) and model.get("size_bytes") > 0
    if has_checksum and has_size:
        model["verification_level"] = ModelVerificationLevel.SHA256_SIZE.value
    elif has_size and model.get("verification_level") not in {item.value for item in ModelVerificationLevel}:
        model["verification_level"] = ModelVerificationLevel.FILENAME_SIZE.value
