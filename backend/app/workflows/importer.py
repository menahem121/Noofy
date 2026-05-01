from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.engine.diagnostics import LogStore
from app.workflows.package import (
    DashboardSchema,
    DashboardSection,
    RequiredModel,
    UnresolvedRuntimeInput,
    WorkflowAssetMetadata,
    WorkflowCustomNodeRecord,
    WorkflowImportMetadata,
    WorkflowMetadata,
    WorkflowPackage,
    WorkflowPackageIdentity,
)

NOOFY_ARCHIVE_SCHEMA_VERSION = "0.1.0"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_JSON_BYTES = 16 * 1024 * 1024
REQUIRED_FILES = {
    "package.json",
    "comfyui_graph.json",
    "dashboard.json",
    "capsule.lock.json",
    "export-report.json",
}
LOCAL_IMAGE_NODE_TYPES = {"LoadImage", "LoadImageMask"}


class NoofyImportError(RuntimeError):
    """Raised when a `.noofy` archive cannot be safely imported."""


class ImportedWorkflowPackageStore:
    """Stores normalized imported workflow packages under workflow-store."""

    def __init__(self, root_dir: Path, *, log_store: LogStore | None = None) -> None:
        self.root_dir = root_dir
        self.log_store = log_store or LogStore()

    def import_archive(self, data: bytes, *, original_filename: str | None = None) -> WorkflowPackage:
        transaction_dir: Path | None = None
        try:
            importer = NoofyArchiveImporter(data, original_filename=original_filename)
            package = importer.normalize()
            target_dir = self.package_dir(package)
            transaction_dir = self.root_dir / "_transactions" / f"import-{uuid.uuid4().hex}"
            source_files_dir = transaction_dir / "source-files"

            transaction_dir.mkdir(parents=True, exist_ok=False)
            importer.extract_source_files(source_files_dir)
            (transaction_dir / "source-archive.noofy").write_bytes(data)
            (transaction_dir / "package.json").write_text(
                package.model_dump_json(indent=2),
                encoding="utf-8",
            )
            (transaction_dir / "import-report.json").write_text(
                json.dumps(
                    {
                        "schema_version": NOOFY_ARCHIVE_SCHEMA_VERSION,
                        "workflow_id": package.metadata.id,
                        "identity": package.identity.model_dump() if package.identity else None,
                        "original_filename": original_filename,
                        "status": package.import_metadata.status
                        if package.import_metadata
                        else "imported",
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )

            target_dir.parent.mkdir(parents=True, exist_ok=True)
            if target_dir.exists():
                shutil.rmtree(target_dir)
            transaction_dir.replace(target_dir)
        except Exception as exc:
            if transaction_dir is not None:
                shutil.rmtree(transaction_dir, ignore_errors=True)
            self.log_store.add(
                "warning",
                "Workflow import failed",
                "workflow.import",
                details={
                    "original_filename": original_filename,
                    "error": str(exc),
                },
            )
            raise

        self.log_store.add(
            "info",
            "Imported workflow package",
            "workflow.import",
            workflow_id=package.metadata.id,
            details={
                "publisher_id": package.identity.publisher_id if package.identity else None,
                "package_id": package.identity.package_id if package.identity else None,
                "version": package.identity.version if package.identity else None,
                "custom_node_count": len(package.custom_nodes),
                "required_model_count": len(package.required_models),
                "unresolved_input_count": len(package.unresolved_runtime_inputs),
            },
        )
        return package

    def package_dir(self, package: WorkflowPackage) -> Path:
        if package.identity is None:
            raise NoofyImportError("Imported package is missing identity metadata")
        return (
            self.root_dir
            / _safe_store_segment(package.identity.publisher_id)
            / _safe_store_segment(package.identity.package_id)
            / _safe_store_segment(package.identity.version)
        )


class NoofyArchiveImporter:
    """Safely inspects a `.noofy` zip archive as data."""

    def __init__(self, data: bytes, *, original_filename: str | None = None) -> None:
        if len(data) > MAX_ARCHIVE_BYTES:
            raise NoofyImportError("Workflow package is too large to import automatically.")
        self.data = data
        self.original_filename = original_filename
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise NoofyImportError("Workflow package is not a valid .noofy archive.") from exc
        self.members = self._validated_members()

    def normalize(self) -> WorkflowPackage:
        package_json = self._read_json("package.json")
        graph = self._read_json("comfyui_graph.json")
        dashboard_json = self._read_json("dashboard.json")
        capsule_json = self._read_json("capsule.lock.json")
        export_report = self._read_json("export-report.json")

        if not isinstance(graph, dict):
            raise NoofyImportError("Workflow graph must be a JSON object.")

        publisher_id = _string_field(package_json, "publisher_id", fallback="unknown")
        package_id = _string_field(package_json, "package_id", fallback="workflow")
        version = _string_field(package_json, "version", fallback="0.1.0")
        workflow_id = imported_workflow_id(publisher_id, package_id, version)
        trust_level = _normalize_trust_level(package_json.get("trust_level"))
        display_name = _string_field(package_json, "display_name", fallback=package_id)

        models = _normalize_models(capsule_json)
        custom_nodes = _normalize_custom_nodes(capsule_json)
        unresolved_inputs = _detect_unresolved_runtime_inputs(graph)
        dashboard = _normalize_dashboard(dashboard_json, unresolved_inputs)
        observed_hardware = _observed_hardware(capsule_json, export_report)
        import_status = _import_status(unresolved_inputs)

        try:
            return WorkflowPackage(
                metadata=WorkflowMetadata(
                    id=workflow_id,
                    name=display_name,
                    version=version,
                    description=_string_field(package_json, "description", fallback=""),
                    author=publisher_id,
                ),
                identity=WorkflowPackageIdentity(
                    publisher_id=publisher_id,
                    package_id=package_id,
                    version=version,
                    trust_level=trust_level,
                    source="noofy_archive_import",
                ),
                engine="comfyui",
                required_models=models,
                comfyui_graph=graph,
                inputs=[],
                outputs=[],
                dashboard=dashboard,
                custom_nodes=custom_nodes,
                unresolved_runtime_inputs=unresolved_inputs,
                assets=WorkflowAssetMetadata(
                    thumbnail="source-files/assets/thumbnail.png"
                    if "assets/thumbnail.png" in self.members
                    else None
                ),
                export_report=export_report,
                exported_package=package_json,
                exported_capsule=capsule_json,
                observed_hardware=observed_hardware,
                import_metadata=WorkflowImportMetadata(
                    original_filename=self.original_filename,
                    imported_at=datetime.now(UTC).isoformat(),
                    source_archive_sha256=f"sha256:{hashlib.sha256(self.data).hexdigest()}",
                    status=import_status,
                    user_facing_message=_import_status_message(import_status),
                ),
            )
        except ValidationError as exc:
            raise NoofyImportError("Workflow package metadata could not be normalized.") from exc

    def extract_source_files(self, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, info in self.members.items():
            if name.endswith("/"):
                continue
            target = target_dir.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with self.archive.open(info, "r") as source, target.open("wb") as dest:
                shutil.copyfileobj(source, dest)

    def _read_json(self, name: str) -> dict[str, Any]:
        info = self.members.get(name)
        if info is None:
            raise NoofyImportError(f"Workflow package is missing {name}.")
        if info.file_size > MAX_JSON_BYTES:
            raise NoofyImportError(f"{name} is too large to import automatically.")
        try:
            payload = json.loads(self.archive.read(info).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NoofyImportError(f"{name} is not valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise NoofyImportError(f"{name} must contain a JSON object.")
        return payload

    def _validated_members(self) -> dict[str, zipfile.ZipInfo]:
        infos = self.archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise NoofyImportError("Workflow package contains too many files.")

        total_uncompressed = 0
        members: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = _safe_archive_name(info.filename)
            if _zip_member_is_symlink(info):
                raise NoofyImportError(f"Workflow package contains an unsupported symlink: {name}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise NoofyImportError("Workflow package expands to too much data.")
            members[name] = info

        missing = sorted(REQUIRED_FILES - set(members))
        if missing:
            raise NoofyImportError("Workflow package is missing required files: " + ", ".join(missing))
        return members


def imported_workflow_id(publisher_id: str, package_id: str, version: str) -> str:
    return "__".join(
        [
            _safe_store_segment(publisher_id),
            _safe_store_segment(package_id),
            _safe_store_segment(version),
        ]
    )


def _import_status(unresolved_inputs: list[UnresolvedRuntimeInput]) -> str:
    if unresolved_inputs:
        return "needs_input_setup"
    return "imported"


def _import_status_message(status: str) -> str:
    if status == "needs_input_setup":
        return "Needs input setup"
    if status == "cannot_prepare_automatically":
        return "Cannot prepare automatically"
    return "Imported"


def _safe_archive_name(name: str) -> str:
    if "\\" in name:
        raise NoofyImportError(f"Workflow package contains an unsafe path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise NoofyImportError(f"Workflow package contains an absolute path: {name}")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise NoofyImportError(f"Workflow package contains an unsafe path: {name}")
    return str(path)


def _safe_store_segment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    cleaned = cleaned.strip(".-_")
    return cleaned or "unknown"


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_IFMT(info.external_attr >> 16) == stat.S_IFLNK


def _string_field(data: dict[str, Any], key: str, *, fallback: str) -> str:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _normalize_trust_level(value: Any) -> str:
    if value == "noofy_verified":
        return "noofy_verified"
    if value == "registry_locked":
        return "registry_locked"
    if value in {"public_unverified", "quarantined_community"}:
        return "quarantined_community"
    return "unsupported"


def _normalize_models(capsule_json: dict[str, Any]) -> list[RequiredModel]:
    models = capsule_json.get("models", [])
    if not isinstance(models, list):
        return []

    normalized: list[RequiredModel] = []
    for index, model in enumerate(models):
        if not isinstance(model, dict):
            continue
        folder = _string_field(model, "comfyui_folder", fallback="")
        filename = _string_field(model, "filename", fallback="")
        if not folder or not filename:
            continue
        source_urls = [url for url in model.get("source_urls", []) if isinstance(url, str)]
        sha256 = model.get("sha256")
        checksum = None
        if isinstance(sha256, str) and sha256:
            checksum = sha256 if sha256.startswith("sha256:") else f"sha256:{sha256}"
        size_bytes_value = model.get("size_bytes")
        size_bytes = size_bytes_value if isinstance(size_bytes_value, int) else None
        model_type = model.get("model_type")
        identity_verified = model.get("identity_verified_by_exporter")
        local_file_available = model.get("local_file_available_at_export")
        bundled = model.get("bundled")
        identity_warnings = [
            item for item in model.get("identity_warnings", []) if isinstance(item, str)
        ]
        normalized.append(
            RequiredModel(
                folder=folder,
                filename=filename,
                source_url=source_urls[0] if source_urls else None,
                source_urls=source_urls,
                checksum=checksum,
                model_type=model_type if isinstance(model_type, str) else None,
                size_bytes=size_bytes,
                verification_level=_normalize_model_verification_level(
                    model,
                    checksum=checksum,
                    size_bytes=size_bytes,
                ),
                identity_verified_by_exporter=identity_verified
                if isinstance(identity_verified, bool)
                else checksum is not None and size_bytes is not None,
                local_file_available_at_export=local_file_available
                if isinstance(local_file_available, bool)
                else None,
                bundled=bundled if isinstance(bundled, bool) else False,
                asset_ownership=_normalize_asset_ownership(model.get("asset_ownership")),
                identity_warnings=identity_warnings,
            )
        )
    return normalized


def _normalize_model_verification_level(
    model: dict[str, Any],
    *,
    checksum: str | None,
    size_bytes: int | None,
) -> str:
    value = model.get("verification_level")
    if value in {item.value for item in ModelVerificationLevel}:
        return str(value)
    if checksum is not None and size_bytes is not None:
        return ModelVerificationLevel.SHA256_SIZE.value
    if size_bytes is not None:
        return ModelVerificationLevel.FILENAME_SIZE.value
    return ModelVerificationLevel.FILENAME_ONLY.value


def _normalize_asset_ownership(value: Any) -> str:
    if value in {item.value for item in AssetOwnership}:
        return str(value)
    return AssetOwnership.EXTERNAL_REFERENCE.value


def _normalize_custom_nodes(capsule_json: dict[str, Any]) -> list[WorkflowCustomNodeRecord]:
    nodes = capsule_json.get("custom_nodes", [])
    if not isinstance(nodes, list):
        return []

    normalized: list[WorkflowCustomNodeRecord] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        folder_name = _string_field(node, "folder_name", fallback=_string_field(node, "id", fallback="custom-node"))
        node_id = _string_field(node, "id", fallback=folder_name)
        requirements_files = [
            item for item in node.get("requirements_files", []) if isinstance(item, str)
        ]
        node_types = [item for item in node.get("node_types", []) if isinstance(item, str)]
        normalized.append(
            WorkflowCustomNodeRecord(
                id=node_id,
                folder_name=folder_name,
                source=_string_field(node, "source", fallback="unknown"),
                included=bool(node.get("included")),
                node_types=node_types,
                requirements_files=requirements_files,
                has_install_py=bool(node.get("has_install_py")),
                sha256_manifest=node.get("sha256_manifest")
                if isinstance(node.get("sha256_manifest"), str)
                else None,
            )
        )
    return normalized


def _normalize_dashboard(
    dashboard_json: dict[str, Any],
    unresolved_inputs: list[UnresolvedRuntimeInput],
) -> DashboardSchema:
    if "version" in dashboard_json and isinstance(dashboard_json.get("sections"), list):
        return DashboardSchema.model_validate(dashboard_json)

    title = "Input setup needed" if unresolved_inputs else "Imported workflow"
    return DashboardSchema(
        version=NOOFY_ARCHIVE_SCHEMA_VERSION,
        sections=[DashboardSection(id="import_setup", title=title, controls=[])],
    )


def _detect_unresolved_runtime_inputs(graph: dict[str, Any]) -> list[UnresolvedRuntimeInput]:
    unresolved: list[UnresolvedRuntimeInput] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        node_type = node.get("class_type")
        inputs = node.get("inputs")
        if node_type not in LOCAL_IMAGE_NODE_TYPES or not isinstance(inputs, dict):
            continue
        image_value = inputs.get("image")
        if isinstance(image_value, str) and image_value:
            unresolved.append(
                UnresolvedRuntimeInput(
                    node_id=str(node_id),
                    node_type=node_type,
                    input_name="image",
                    current_value=image_value,
                    reason="creator_local_image_not_bundled",
                )
            )
    return unresolved


def _observed_hardware(capsule_json: dict[str, Any], export_report: dict[str, Any]) -> dict[str, Any]:
    observed: dict[str, Any] = {}
    capsule_observations = capsule_json.get("hardware_observations")
    if isinstance(capsule_observations, dict):
        observed.update(capsule_observations)
    runtime = export_report.get("runtime")
    if isinstance(runtime, dict):
        observed["export_runtime"] = runtime
    test_run = export_report.get("test_run")
    if isinstance(test_run, dict):
        observed["test_run"] = test_run
    return observed
