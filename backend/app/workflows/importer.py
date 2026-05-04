from __future__ import annotations

import hashlib
import io
import json
import platform
import shutil
import stat
import subprocess
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.engine.diagnostics import LogStore
from app.runtime.dependency_lock import DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION
from app.runtime.fingerprints import (
    FINGERPRINT_SCHEMA_VERSION,
    capsule_fingerprint,
    dependency_env_fingerprint,
    runner_workspace_fingerprint,
    sha256_fingerprint,
)
from app.runtime.isolation import (
    CapsuleLock,
    CustomNodeLock,
    HardwareObservations,
    ModelLock,
    TrustMetadata,
    TrustLevel,
)
from app.runtime.node_registry import (
    CustomNodeSourceCache,
    CustomNodeSourceResolutionRequest,
    NodeRegistryResolutionError,
    NodeRegistryResolutionErrorCode,
    NodeRegistryResolver,
    NodeRegistrySource,
    NodeRegistrySourceKind,
)
from app.runtime.profiles import (
    DEFAULT_RUNTIME_PROFILE_CATALOG_PATH,
    RuntimeProfile,
    RuntimeProfileVariant,
    load_runtime_profile_catalog,
)
from app.trust import (
    TrustVerificationResult,
    TrustVerificationStatus,
    TrustVerifier,
    imported_archive_trust_payload,
)
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
    SignedRegistryMetadata,
    WorkflowPackageSignature,
    WorkflowSmokeTests,
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
UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS = {
    "comfyui_launch_options",
    "launch_config",
    "launch_options",
    "runner_launch_options",
}


class NoofyImportError(RuntimeError):
    """Raised when a `.noofy` archive cannot be safely imported."""


class ImportedWorkflowPackageStore:
    """Stores normalized imported workflow packages under workflow-store."""

    def __init__(
        self,
        root_dir: Path,
        *,
        log_store: LogStore | None = None,
        node_registry_resolver: NodeRegistryResolver | None = None,
        custom_node_source_cache: CustomNodeSourceCache | None = None,
        trust_verifier: TrustVerifier | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.log_store = log_store or LogStore()
        self.node_registry_resolver = node_registry_resolver
        self.custom_node_source_cache = custom_node_source_cache
        self.trust_verifier = trust_verifier or TrustVerifier()

    def import_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ) -> WorkflowPackage:
        transaction_dir: Path | None = None
        try:
            importer = NoofyArchiveImporter(data, original_filename=original_filename)
            package = importer.normalize()
            package = self._with_verified_import_trust(package, importer.trust_payload())
            package = self._with_resolved_community_sources(
                package,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
            )
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
            app_capsule_lock = imported_package_capsule_lock(package)
            (transaction_dir / "capsule.lock.json").write_text(
                app_capsule_lock.model_dump_json(indent=2),
                encoding="utf-8",
            )
            (transaction_dir / "exported-capsule.lock.json").write_text(
                json.dumps(package.exported_capsule, indent=2, sort_keys=True),
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
                        "runtime_resolution": {
                            "runtime_profile_id": app_capsule_lock.runtime.runtime_profile_id,
                            "runtime_profile_variant_id": app_capsule_lock.runtime.runtime_profile_variant_id,
                            "runtime_profile_manifest_hash": app_capsule_lock.runtime.runtime_profile_manifest_hash,
                            "selection_stage": "import_time_phase5c",
                        },
                        "source_resolution": package.import_metadata.developer_details.get("source_resolution", {})
                        if package.import_metadata
                        else {},
                        "trust_verification": package.import_metadata.developer_details.get("trust_verification", {})
                        if package.import_metadata
                        else {},
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

    def _with_resolved_community_sources(
        self,
        package: WorkflowPackage,
        *,
        allow_unverified_community_preparation: bool,
    ) -> WorkflowPackage:
        required_records = _non_bundled_required_custom_node_records(package)
        if not required_records:
            return package

        if package.identity is None:
            return _package_with_import_resolution_status(
                package,
                status="unsupported",
                message="Unsupported workflow",
                source_resolution={
                    "status": "failed",
                    "reason": "missing_identity",
                },
            )
        trust_level = _trust_level_from_string(package.identity.trust_level)
        if trust_level is TrustLevel.UNSUPPORTED:
            return package
        if trust_level is TrustLevel.QUARANTINED_COMMUNITY and not allow_unverified_community_preparation:
            return _package_with_import_resolution_status(
                package,
                status="blocked_by_policy",
                message="Needs permission to prepare community workflow",
                source_resolution={
                    "status": "blocked_by_policy",
                    "reason": "community_opt_in_required",
                    "unresolved_custom_nodes": [record.id for record in required_records],
                },
            )
        if self.node_registry_resolver is None or self.custom_node_source_cache is None:
            return _package_with_import_resolution_status(
                package,
                status="unsupported",
                message="Unsupported workflow",
                source_resolution={
                    "status": "failed",
                    "reason": "source_resolution_not_configured",
                    "unresolved_custom_nodes": [record.id for record in required_records],
                },
            )

        records_by_id = {record.id: record for record in package.custom_nodes}
        resolved_reports: list[dict[str, object]] = []
        for record in required_records:
            try:
                explicit_source = _explicit_node_registry_source(record)
                resolved = self.node_registry_resolver.resolve(
                    CustomNodeSourceResolutionRequest(
                        package_id=record.id,
                        node_types=record.node_types,
                        trust_level=trust_level,
                        allow_unverified_community_preparation=allow_unverified_community_preparation,
                        explicit_source=explicit_source,
                    )
                )
                cached = self.custom_node_source_cache.materialize(resolved.source)
            except NodeRegistryResolutionError as exc:
                self.log_store.add(
                    "warning",
                    "Imported workflow custom-node source resolution failed",
                    "workflow.import",
                    workflow_id=package.metadata.id,
                    details={
                        "package_id": record.id,
                        "code": exc.code.value,
                        **exc.developer_details,
                    },
                )
                return _package_with_import_resolution_status(
                    package,
                    status="unsupported",
                    message="Unsupported workflow",
                    source_resolution={
                        "status": "failed",
                        "package_id": record.id,
                        "code": exc.code.value,
                        "developer_details": exc.developer_details,
                    },
                )
            records_by_id[record.id] = record.model_copy(
                update={
                    "included": True,
                    "source": resolved.source.source_url,
                    "source_ref": resolved.source.source_ref,
                    "source_content_hash": resolved.source.source_content_hash,
                    "source_cache_ref": cached.source_cache_ref,
                    "source_archive_subdir": resolved.source.archive_subdir,
                    "resolution_method": resolved.resolution_method,
                }
            )
            resolved_reports.append(
                {
                    "package_id": resolved.package_id,
                    "resolution_method": resolved.resolution_method,
                    "source_ref": resolved.source.source_ref,
                    "source_cache_ref": cached.source_cache_ref,
                    "source_archive_subdir": resolved.source.archive_subdir,
                }
            )

        updated_package = package.model_copy(update={"custom_nodes": list(records_by_id.values())})
        return _package_with_import_resolution_status(
            updated_package,
            status=_import_status(updated_package.unresolved_runtime_inputs),
            message=_import_status_message(_import_status(updated_package.unresolved_runtime_inputs)),
            source_resolution={
                "status": "resolved",
                "resolved_custom_nodes": resolved_reports,
            },
        )

    def _with_verified_import_trust(
        self,
        package: WorkflowPackage,
        trust_payload: dict[str, Any],
    ) -> WorkflowPackage:
        if package.identity is None:
            return package
        requested_trust_level = _trust_level_from_string(package.identity.trust_level)
        verification = self.trust_verifier.verify_imported_package(
            requested_trust_level=requested_trust_level,
            payload=trust_payload,
            signatures=package.identity.signatures,
            signed_registry_metadata=package.identity.signed_registry_metadata,
        )
        package = _package_with_trust_verification(package, verification)
        if verification.status in {
            TrustVerificationStatus.NOT_REQUIRED,
            TrustVerificationStatus.VERIFIED,
        }:
            return package
        self.log_store.add(
            "warning",
            "Imported workflow trust verification failed",
            "workflow.import",
            workflow_id=package.metadata.id,
            details=verification.model_dump(mode="json"),
        )
        return _package_with_import_resolution_status(
            package,
            status="unsupported",
            message="Unsupported workflow",
            source_resolution={
                "status": "failed",
                "reason": "trust_verification_failed",
                "trust_verification": verification.model_dump(mode="json"),
            },
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
        display_name = _normalized_display_name(package_json, fallback=package_id)

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
                    signature=_optional_string_field(package_json, "signature"),
                    signatures=_normalize_signatures(package_json.get("signatures")),
                    signed_registry_metadata=_normalize_signed_registry_metadata(
                        package_json.get("signed_registry_metadata")
                    ),
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
                smoke_tests=WorkflowSmokeTests.model_validate(package_json.get("smoke_tests") or {}),
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

    def trust_payload(self) -> dict[str, Any]:
        return imported_archive_trust_payload(
            package_json=self._read_json("package.json"),
            comfyui_graph=self._read_json("comfyui_graph.json"),
            dashboard_json=self._read_json("dashboard.json"),
            capsule_json=self._read_json("capsule.lock.json"),
            export_report=self._read_json("export-report.json"),
        )

    def extract_source_files(self, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, info in self.members.items():
            if info.is_dir():
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
        raw_members: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            name = _safe_archive_name(info.filename)
            if _zip_member_is_symlink(info):
                raise NoofyImportError(f"Workflow package contains an unsupported symlink: {name}")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                raise NoofyImportError("Workflow package expands to too much data.")
            if info.is_dir():
                continue
            if _ignored_archive_member(name):
                continue
            if name in raw_members:
                raise NoofyImportError(f"Workflow package contains duplicate file path: {name}")
            raw_members[name] = info

        root_prefix = _single_wrapper_root(raw_members)
        members: dict[str, zipfile.ZipInfo] = {}
        for name, info in raw_members.items():
            normalized_name = _strip_wrapper_root(name, root_prefix)
            if normalized_name is None or _ignored_archive_member(normalized_name):
                continue
            if normalized_name in members:
                raise NoofyImportError(f"Workflow package contains duplicate file path: {normalized_name}")
            members[normalized_name] = info

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
    if status == "blocked_by_policy":
        return "Needs permission to prepare community workflow"
    if status == "unsupported":
        return "Unsupported workflow"
    if status == "cannot_prepare_automatically":
        return "Cannot prepare automatically"
    return "Imported"


def _package_with_import_resolution_status(
    package: WorkflowPackage,
    *,
    status: str,
    message: str,
    source_resolution: dict[str, object],
) -> WorkflowPackage:
    import_metadata = package.import_metadata or WorkflowImportMetadata(
        imported_at=datetime.now(UTC).isoformat(),
    )
    developer_details = dict(import_metadata.developer_details)
    developer_details["source_resolution"] = source_resolution
    updated_identity = package.identity
    if status in {"blocked_by_policy", "unsupported"} and package.identity is not None:
        updated_identity = package.identity.model_copy(update={"trust_level": TrustLevel.UNSUPPORTED.value})
    return package.model_copy(
        update={
            "identity": updated_identity,
            "import_metadata": import_metadata.model_copy(
                update={
                    "status": status,
                    "user_facing_message": message,
                    "developer_details": developer_details,
                }
            ),
        }
    )


def _package_with_trust_verification(
    package: WorkflowPackage,
    verification: TrustVerificationResult,
) -> WorkflowPackage:
    import_metadata = package.import_metadata or WorkflowImportMetadata(
        imported_at=datetime.now(UTC).isoformat(),
    )
    developer_details = dict(import_metadata.developer_details)
    developer_details["trust_verification"] = verification.model_dump(mode="json")
    return package.model_copy(
        update={
            "import_metadata": import_metadata.model_copy(
                update={"developer_details": developer_details}
            ),
        }
    )


def _non_bundled_required_custom_node_records(package: WorkflowPackage) -> list[WorkflowCustomNodeRecord]:
    graph_node_types = _graph_node_types(package.comfyui_graph)
    required: list[WorkflowCustomNodeRecord] = []
    for record in package.custom_nodes:
        if record.included:
            continue
        if record.node_types and not any(node_type in graph_node_types for node_type in record.node_types):
            continue
        required.append(record)
    return required


def _graph_node_types(graph: dict[str, Any]) -> set[str]:
    node_types: set[str] = set()

    def visit_node(node: Any) -> None:
        if not isinstance(node, dict):
            return
        class_type = node.get("class_type") or node.get("type")
        if isinstance(class_type, str) and _is_resolvable_workflow_node_type(class_type):
            node_types.add(class_type)
        group_nodes = node.get("nodes") or node.get("groupNodes")
        if isinstance(group_nodes, list):
            for group_node in group_nodes:
                visit_node(group_node)
        elif isinstance(group_nodes, dict):
            for group_node in group_nodes.values():
                visit_node(group_node)

    for node in graph.values():
        visit_node(node)
    return node_types


def _is_resolvable_workflow_node_type(node_type: str) -> bool:
    if node_type in {"Reroute", "Note"}:
        return False
    return not (node_type.startswith("workflow/") or node_type.startswith("workflow>"))


def _explicit_node_registry_source(record: WorkflowCustomNodeRecord) -> NodeRegistrySource | None:
    if not record.source.startswith("https://"):
        return None
    if record.source_ref is None:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.MISSING_PINNED_SOURCE_REF,
            "Noofy cannot prepare a workflow extension without a pinned source version.",
            developer_details={"package_id": record.id, "source_url": record.source},
        )
    if record.source_content_hash is None:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.MISSING_SOURCE_CONTENT_HASH,
            "Noofy cannot prepare a workflow extension without a verified source hash.",
            developer_details={"package_id": record.id, "source_url": record.source},
        )
    try:
        return NodeRegistrySource(
            source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
            source_url=record.source,
            source_ref=record.source_ref,
            source_content_hash=record.source_content_hash,
            archive_subdir=record.source_archive_subdir,
        )
    except ValidationError as exc:
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.UNPINNED_SOURCE_REF,
            "Noofy cannot prepare a workflow extension without pinned and verified source facts.",
            developer_details={"package_id": record.id, "source_url": record.source, "validation_error": str(exc)},
        ) from exc


def _safe_archive_name(name: str) -> str:
    if "\\" in name:
        raise NoofyImportError(f"Workflow package contains an unsafe path: {name}")
    path = PurePosixPath(name)
    if path.is_absolute():
        raise NoofyImportError(f"Workflow package contains an absolute path: {name}")
    if not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise NoofyImportError(f"Workflow package contains an unsafe path: {name}")
    return str(path)


def _ignored_archive_member(name: str) -> bool:
    parts = PurePosixPath(name).parts
    return (
        not parts
        or parts[0] == "__MACOSX"
        or parts[-1] == ".DS_Store"
        or parts[-1].startswith("._")
    )


def _single_wrapper_root(members: dict[str, zipfile.ZipInfo]) -> str | None:
    if REQUIRED_FILES <= set(members):
        return None

    roots: set[str] = set()
    for name in members:
        parts = PurePosixPath(name).parts
        if len(parts) > 1:
            roots.add(parts[0])

    for root in sorted(roots):
        root_files = {
            str(PurePosixPath(*PurePosixPath(name).parts[1:]))
            for name in members
            if PurePosixPath(name).parts[:1] == (root,) and len(PurePosixPath(name).parts) > 1
        }
        if REQUIRED_FILES <= root_files:
            return root
    return None


def _strip_wrapper_root(name: str, root_prefix: str | None) -> str | None:
    if root_prefix is None:
        return name
    parts = PurePosixPath(name).parts
    if parts[:1] != (root_prefix,):
        return None
    if len(parts) == 1:
        return None
    return str(PurePosixPath(*parts[1:]))


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


def _optional_string_field(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _normalized_display_name(data: dict[str, Any], *, fallback: str) -> str:
    value = _string_field(data, "display_name", fallback=fallback)
    # Some exported fixtures mark the selected workflow title with a leading
    # asterisk. That marker is exporter UI state, not part of the app name.
    cleaned = value.lstrip("*").strip()
    return cleaned or fallback


def _normalize_trust_level(value: Any) -> str:
    if value == "noofy_verified":
        return "noofy_verified"
    if value == "registry_locked":
        return "registry_locked"
    if value in {"public_unverified", "quarantined_community"}:
        return "quarantined_community"
    return "unsupported"


def _normalize_signatures(value: Any) -> list[WorkflowPackageSignature]:
    if not isinstance(value, list):
        return []
    signatures: list[WorkflowPackageSignature] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key_id = item.get("key_id")
        algorithm = item.get("algorithm")
        signature_value = item.get("value")
        if not all(isinstance(field, str) and field.strip() for field in (key_id, algorithm, signature_value)):
            continue
        signatures.append(
            WorkflowPackageSignature(
                key_id=key_id.strip(),
                algorithm=algorithm.strip(),
                value=signature_value.strip(),
            )
        )
    return signatures


def _normalize_signed_registry_metadata(value: Any) -> SignedRegistryMetadata | None:
    if not isinstance(value, dict):
        return None
    registry_id = value.get("registry_id")
    snapshot_hash = value.get("snapshot_hash")
    signature = value.get("signature")
    if not all(isinstance(field, str) and field.strip() for field in (registry_id, snapshot_hash, signature)):
        return None
    return SignedRegistryMetadata(
        registry_id=registry_id.strip(),
        snapshot_hash=snapshot_hash.strip(),
        signature=signature.strip(),
        key_id=_optional_string_field(value, "key_id"),
        algorithm=_optional_string_field(value, "algorithm"),
    )


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
                source_ref=node.get("source_ref") if isinstance(node.get("source_ref"), str) else None,
                source_content_hash=node.get("source_content_hash")
                if isinstance(node.get("source_content_hash"), str)
                else None,
                source_cache_ref=node.get("source_cache_ref")
                if isinstance(node.get("source_cache_ref"), str)
                else None,
                source_archive_subdir=_optional_string_field(node, "source_archive_subdir")
                or _optional_string_field(node, "archive_subdir"),
                resolution_method=node.get("resolution_method")
                if isinstance(node.get("resolution_method"), str)
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


def imported_package_capsule_lock(package: WorkflowPackage) -> CapsuleLock:
    if package.identity is None:
        raise NoofyImportError("Imported package is missing identity metadata.")
    _reject_unsupported_exported_launch_options(package.exported_capsule)
    _reject_unsupported_exported_launch_options(package.exported_package)
    catalog = load_runtime_profile_catalog(DEFAULT_RUNTIME_PROFILE_CATALOG_PATH)
    profile, variant = _select_import_runtime_profile(catalog.profiles)
    trust_level = _trust_level_from_string(package.identity.trust_level)
    signature_values = [signature.value for signature in package.identity.signatures]
    if package.identity.signature:
        signature_values.append(package.identity.signature)
    if package.identity.signed_registry_metadata is not None:
        signature_values.append(package.identity.signed_registry_metadata.signature)
    trust = TrustMetadata(
        level=trust_level,
        publisher=package.identity.publisher_id,
        signatures=signature_values,
    )
    custom_nodes = [
        CustomNodeLock(
            package_id=node.id,
            source=node.source,
            source_ref=node.source_ref,
            source_content_hash=node.source_content_hash,
            source_cache_ref=node.source_cache_ref,
            trust_level=trust_level,
            node_types=node.node_types,
        )
        for node in package.custom_nodes
        if node.included
    ]
    models = _model_locks_from_package(package)
    dependency_lock_hash = variant.core_dependency_lock_hash
    dependency_fingerprint = dependency_env_fingerprint(
        runtime_profile_id=profile.runtime_profile_id,
        runtime_profile_manifest_hash=profile.runtime_profile_manifest_hash,
        runtime_profile_variant_id=variant.runtime_profile_variant_id,
        os_name=variant.os,
        architecture=variant.architecture,
        python_build_id=variant.python_build_id,
        torch_wheel_build_tag=variant.torch_wheel_build_tag,
        torch_backend=variant.gpu_backend_profile,
        dependency_lock_hash=dependency_lock_hash,
        native_dependency_constraints={},
        install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    )
    runner_fingerprint = runner_workspace_fingerprint(
        dependency_env_fingerprint=dependency_fingerprint,
        runtime_profile_id=profile.runtime_profile_id,
        runtime_profile_manifest_hash=profile.runtime_profile_manifest_hash,
        runtime_profile_variant_id=variant.runtime_profile_variant_id,
        comfyui_source_hash=profile.comfyui_core_source_hash,
        comfyui_frontend_version=profile.comfyui_frontend_version,
        enabled_custom_node_manifest_hash=sha256_fingerprint(custom_nodes),
        launch_config_hash=_launch_config_hash(package.engine, variant, sha256_fingerprint(custom_nodes)),
        model_view_hash=sha256_fingerprint(models),
    )
    package_json = package.model_dump(mode="json", exclude_none=True)
    capsule_hash = capsule_fingerprint(
        workflow_package_hash=sha256_fingerprint(package_json),
        graph_hash=sha256_fingerprint(package.comfyui_graph),
        dashboard_schema_hash=sha256_fingerprint(package.dashboard),
        model_requirements=models,
        custom_nodes=custom_nodes,
        trust=trust,
        runner_fingerprint=runner_fingerprint,
    )
    return CapsuleLock.model_validate(
        {
            "schema_version": NOOFY_ARCHIVE_SCHEMA_VERSION,
            "workflow": {
                "publisher_id": package.identity.publisher_id,
                "package_id": package.identity.package_id,
                "version": package.identity.version,
                "trust_level": trust_level.value,
                "source": package.identity.source,
                "signature": package.identity.signature,
            },
            "engine": {
                "type": package.engine,
                "comfyui_version": profile.comfyui_core_version,
                "core_source_hash": profile.comfyui_core_source_hash,
            },
            "runtime": {
                "runtime_profile_id": profile.runtime_profile_id,
                "runtime_profile_variant_id": variant.runtime_profile_variant_id,
                "runtime_profile_manifest_hash": profile.runtime_profile_manifest_hash,
                "runtime_profile_catalog_version": catalog.schema_version,
                "fingerprint_schema_version": FINGERPRINT_SCHEMA_VERSION,
                "dependency_env_fingerprint": dependency_fingerprint,
                "runner_fingerprint": runner_fingerprint,
                "runner_process_compatibility_key": None,
                "capsule_fingerprint": capsule_hash,
                "os": variant.os,
                "architecture": variant.architecture,
                "python_version": variant.python_version,
                "python_build_id": variant.python_build_id,
                "gpu_backend": variant.gpu_backend_profile,
                "dependency_lock_hash": dependency_lock_hash,
                "runner_workspace_hash": runner_fingerprint,
            },
            "custom_nodes": [node.model_dump(mode="json", exclude_none=True) for node in custom_nodes],
            "dependencies": {
                "lock_file": "community-runtime.lock",
                "install_policy": DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            },
            "models": [model.model_dump(mode="json", exclude_none=True) for model in models],
            "hardware_observations": _hardware_observations_from_package(package).model_dump(mode="json", exclude_none=True),
            "trust": trust.model_dump(mode="json", exclude_none=True),
        }
    )


def _select_import_runtime_profile(profiles: list[RuntimeProfile]) -> tuple[RuntimeProfile, RuntimeProfileVariant]:
    """Select the local v1 runtime variant for imported package preparation.

    This is a Phase 5c bridge: imported app-owned capsule locks need concrete
    runtime facts before Phase 5d/5e can prepare artifacts. Later phases can
    replace this with an adapter-aware resolution pass before install.
    """
    if not profiles:
        raise NoofyImportError("Runtime profile catalog is empty.")
    profile = profiles[0]
    os_name = _current_os_name()
    architecture = _current_architecture()
    preferred_gpu = _preferred_gpu_backend(os_name, architecture)
    for variant in profile.variants:
        if variant.os == os_name and variant.architecture == architecture and variant.gpu_backend_profile == preferred_gpu:
            return profile, variant
    for variant in profile.variants:
        if variant.os == os_name and variant.architecture == architecture and variant.gpu_backend_profile == "cpu":
            return profile, variant
    return profile, profile.variants[0]


def _reject_unsupported_exported_launch_options(data: dict[str, Any]) -> None:
    launch_keys = sorted(key for key in UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS if _has_nonempty_launch_option(data, key))
    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        launch_keys.extend(
            f"runtime.{key}"
            for key in sorted(UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS)
            if _has_nonempty_launch_option(runtime, key)
        )
    if launch_keys:
        raise NoofyImportError(
            "Workflow package declares unsupported launch options: "
            + ", ".join(launch_keys)
        )


def _has_nonempty_launch_option(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    return value not in (None, {}, [], "")


def _current_os_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "darwin"
    if system == "windows":
        return "windows"
    if system == "linux":
        return "linux"
    return system or "unknown"


def _current_architecture() -> str:
    machine = (platform.machine() or platform.processor() or "").lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64", "x64"}:
        return "x64"
    return machine or "unknown"


def _preferred_gpu_backend(os_name: str, architecture: str) -> str:
    if os_name == "darwin" and architecture == "arm64":
        return "mps"
    if os_name in {"linux", "windows"} and architecture == "x64" and _has_nvidia_gpu():
        return "cuda"
    return "cpu"


def _has_nvidia_gpu() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _trust_level_from_string(value: str) -> TrustLevel:
    if value in {item.value for item in TrustLevel}:
        return TrustLevel(value)
    return TrustLevel.UNSUPPORTED


def _model_locks_from_package(package: WorkflowPackage) -> list[ModelLock]:
    models: list[ModelLock] = []
    for model in package.required_models:
        if model.checksum is None or model.size_bytes is None:
            continue
        models.append(
            ModelLock(
                id=_model_id(model),
                sha256=model.checksum,
                size_bytes=model.size_bytes,
                source_urls=model.source_urls or ([model.source_url] if model.source_url else []),
                comfyui_folder=model.folder,
                filename=model.filename,
            )
        )
    return models


def _model_id(model: RequiredModel) -> str:
    if model.source_urls:
        return model.source_urls[0]
    if model.source_url:
        return model.source_url
    return f"{model.folder}/{model.filename}"


def _hardware_observations_from_package(package: WorkflowPackage) -> HardwareObservations:
    observed = package.observed_hardware
    return HardwareObservations(
        observed_peak_vram_mb=observed.get("observed_peak_vram_mb")
        if isinstance(observed.get("observed_peak_vram_mb"), int)
        else None,
        observed_peak_ram_mb=observed.get("observed_peak_ram_mb")
        if isinstance(observed.get("observed_peak_ram_mb"), int)
        else None,
        tested_resolution=observed.get("tested_resolution")
        if isinstance(observed.get("tested_resolution"), str)
        else None,
        tested_batch_size=observed.get("tested_batch_size")
        if isinstance(observed.get("tested_batch_size"), int)
        else None,
        gpu_name=observed.get("gpu_name") if isinstance(observed.get("gpu_name"), str) else None,
        os=observed.get("os") if isinstance(observed.get("os"), str) else None,
        backend=observed.get("backend") if isinstance(observed.get("backend"), str) else None,
        precision=observed.get("precision") if isinstance(observed.get("precision"), str) else None,
        recommended_vram_mb=observed.get("recommended_vram_mb")
        if isinstance(observed.get("recommended_vram_mb"), int)
        else None,
        recommended_ram_mb=observed.get("recommended_ram_mb")
        if isinstance(observed.get("recommended_ram_mb"), int)
        else None,
    )


def _launch_config_hash(engine: str, variant: RuntimeProfileVariant, enabled_custom_node_hash: str) -> str:
    launch_defaults = variant.launch_defaults
    return sha256_fingerprint(
        {
            "kind": "runner_launch_config",
            "engine": engine,
            "preview_method": launch_defaults.preview_method,
            "vram_mode": launch_defaults.vram_mode,
            "attention_backend": launch_defaults.attention_backend,
            "precision_policy": launch_defaults.precision_policy,
            "enabled_custom_node_set": enabled_custom_node_hash,
            "extra_model_paths_mode": launch_defaults.extra_model_paths_mode,
            "noofy_environment": launch_defaults.noofy_environment,
        }
    )
