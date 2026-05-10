from __future__ import annotations

import hashlib
import io
import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from app.engine.diagnostics import DiagnosticsSink
from app.runtime.isolation import CapsuleLock, HardwareObservations, ModelLock, TrustLevel
from app.runtime.node_registry import (
    CustomNodeSourceCache,
    CustomNodeSourceResolutionRequest,
    NodeRegistryResolutionError,
    NodeRegistryResolver,
    NodeRegistrySource,
)
from app.trust import (
    TrustVerificationResult,
    TrustVerificationStatus,
    TrustVerifier,
    imported_archive_trust_payload,
)
from app.workflows.package import (
    DashboardSchema,
    RequiredModel,
    UnresolvedRuntimeInput,
    WorkflowAssetMetadata,
    WorkflowCustomNodeRecord,
    WorkflowImportMetadata,
    WorkflowInput,
    WorkflowMetadata,
    WorkflowOutput,
    WorkflowPackage,
    WorkflowPackageIdentity,
    SignedRegistryMetadata,
    WorkflowPackageSignature,
    WorkflowSmokeTests,
)
from app.workflows.archive_validation import (
    ArchiveValidationError,
    ignored_archive_member,
    safe_archive_name,
    single_wrapper_root,
    strip_wrapper_root,
    validate_archive_members,
    zip_member_is_symlink,
)
from app.workflows.package_persistence import write_imported_package_transaction
from app.workflows.import_runtime_profile import (
    RuntimeProfileSelectionError,
    current_architecture,
    current_os_name,
    has_nvidia_gpu,
    preferred_gpu_backend,
    select_import_runtime_profile,
)
from app.workflows.import_policy import (
    explicit_node_registry_source,
    graph_node_types,
    import_status,
    import_status_message,
    is_resolvable_workflow_node_type,
    non_bundled_required_custom_node_records,
    package_with_import_resolution_status,
    package_with_source_policy,
    package_with_trust_verification,
    source_policy_status_for_import,
    trust_level_from_string,
)
from app.workflows.import_normalization import (
    ImportNormalizationError,
    detect_unresolved_runtime_inputs,
    has_nonempty_launch_option,
    normalize_asset_ownership,
    normalize_custom_nodes,
    normalize_dashboard,
    normalize_model_verification_level,
    normalize_models,
    normalize_signed_registry_metadata,
    normalize_signatures,
    normalize_trust_level,
    normalized_display_name,
    observed_hardware,
    optional_string_field,
    reject_unsupported_exported_launch_options,
    string_field,
)
from app.workflows.import_capsule_lock import (
    ImportCapsuleLockError,
    hardware_observations_from_package,
    imported_package_capsule_lock as build_imported_package_capsule_lock,
    launch_config_hash,
    model_id,
    model_locks_from_package,
)
from app.workflows.store_paths import (
    imported_workflow_id,
    mutable_package_dir,
    safe_store_segment,
)

NOOFY_ARCHIVE_SCHEMA_VERSION = "0.1.0"
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_JSON_BYTES = 16 * 1024 * 1024
REQUIRED_FILES = {
    "package.json",
    "comfyui_graph.json",
    "capsule.lock.json",
    "export-report.json",
}
class NoofyImportError(RuntimeError):
    """Raised when a `.noofy` archive cannot be safely imported."""


class ImportedWorkflowPackageStore:
    """Stores normalized imported workflow packages under workflow-store."""

    def __init__(
        self,
        root_dir: Path,
        *,
        log_store: DiagnosticsSink,
        node_registry_resolver: NodeRegistryResolver | None = None,
        custom_node_source_cache: CustomNodeSourceCache | None = None,
        trust_verifier: TrustVerifier | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.log_store = log_store
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
        try:
            importer = NoofyArchiveImporter(data, original_filename=original_filename)
            package = importer.normalize()
            package = self._with_verified_import_trust(
                package, importer.trust_payload()
            )
            package = _package_with_source_policy(
                package,
                community_preparation_opted_in=allow_unverified_community_preparation,
                policy_status=_source_policy_status_for_import(package),
            )
            package = self._with_resolved_community_sources(
                package,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
            )
            target_dir = self.package_dir(package)
            app_capsule_lock = imported_package_capsule_lock(package)
            write_imported_package_transaction(
                root_dir=self.root_dir,
                target_dir=target_dir,
                package=package,
                app_capsule_lock=app_capsule_lock,
                archive_data=data,
                original_filename=original_filename,
                schema_version=NOOFY_ARCHIVE_SCHEMA_VERSION,
                extract_source_files=importer.extract_source_files,
            )
        except Exception as exc:
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
                "publisher_id": (
                    package.identity.publisher_id if package.identity else None
                ),
                "package_id": package.identity.package_id if package.identity else None,
                "version": package.identity.version if package.identity else None,
                "custom_node_count": len(package.custom_nodes),
                "required_model_count": len(package.required_models),
                "unresolved_input_count": len(package.unresolved_runtime_inputs),
            },
        )
        return package

    def package_dir(self, package: WorkflowPackage) -> Path:
        package_dir = mutable_package_dir(self.root_dir, package)
        if package_dir is None:
            raise NoofyImportError("Imported package is missing identity metadata")
        return package_dir

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
        if (
            trust_level is TrustLevel.QUARANTINED_COMMUNITY
            and not allow_unverified_community_preparation
        ):
            return _package_with_import_resolution_status(
                package,
                status="blocked_by_policy",
                message="Needs permission to prepare community workflow",
                source_resolution={
                    "status": "blocked_by_policy",
                    "reason": "community_opt_in_required",
                    "unresolved_custom_nodes": [
                        record.id for record in required_records
                    ],
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
                    "unresolved_custom_nodes": [
                        record.id for record in required_records
                    ],
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
                        source_policy=package.source_policy,
                    )
                )
                cached = self.custom_node_source_cache.materialize(
                    resolved.source,
                    source_policy=package.source_policy,
                    source_origins=resolved.source_policy_origins(),
                )
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

        updated_package = package.model_copy(
            update={"custom_nodes": list(records_by_id.values())}
        )
        _status = _import_status(
            updated_package.unresolved_runtime_inputs, updated_package.dashboard
        )
        return _package_with_import_resolution_status(
            updated_package,
            status=_status,
            message=_import_status_message(_status),
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
            raise NoofyImportError(
                "Workflow package is too large to import automatically."
            )
        self.data = data
        self.original_filename = original_filename
        try:
            self.archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise NoofyImportError(
                "Workflow package is not a valid .noofy archive."
            ) from exc
        self.members = self._validated_members()

    def normalize(self) -> WorkflowPackage:
        package_json = self._read_json("package.json")
        graph = self._read_json("comfyui_graph.json")
        dashboard_json = (
            self._read_json("dashboard.json")
            if "dashboard.json" in self.members
            else {}
        )
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
        dashboard = _normalize_dashboard(dashboard_json)
        dashboard_inputs = [
            WorkflowInput.model_validate(i)
            for i in (dashboard_json.get("inputs") or [])
            if isinstance(i, dict)
        ]
        dashboard_outputs = [
            WorkflowOutput.model_validate(o)
            for o in (dashboard_json.get("outputs") or [])
            if isinstance(o, dict)
        ]
        observed_hardware = _observed_hardware(capsule_json, export_report)

        # Validate configured dashboards — an invalid one routes to needs_input_setup.
        dashboard_valid = True
        if dashboard.status == "configured":
            from app.workflows.validator import (
                WorkflowPackageValidator,
            )  # local to avoid circular import

            _validator = WorkflowPackageValidator()
            _candidate = WorkflowPackage(
                metadata=WorkflowMetadata(
                    id=workflow_id,
                    name=display_name,
                    version=version,
                    description="",
                    author=publisher_id,
                ),
                identity=None,
                engine="comfyui",
                required_models=[],
                comfyui_graph=graph,
                inputs=dashboard_inputs,
                outputs=dashboard_outputs,
                dashboard=dashboard,
                custom_nodes=[],
                unresolved_runtime_inputs=[],
                smoke_tests=WorkflowSmokeTests(),
            )
            _result = _validator.validate_structure(_candidate)
            dashboard_valid = _result.valid

        import_status = _import_status(
            unresolved_inputs, dashboard, dashboard_valid=dashboard_valid
        )

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
                    thumbnail=(
                        "source-files/assets/thumbnail.png"
                        if "assets/thumbnail.png" in self.members
                        else None
                    )
                ),
                export_report=export_report,
                exported_package=package_json,
                exported_capsule=capsule_json,
                observed_hardware=observed_hardware,
                smoke_tests=WorkflowSmokeTests.model_validate(
                    package_json.get("smoke_tests") or {}
                ),
                import_metadata=WorkflowImportMetadata(
                    original_filename=self.original_filename,
                    imported_at=datetime.now(UTC).isoformat(),
                    source_archive_sha256=f"sha256:{hashlib.sha256(self.data).hexdigest()}",
                    status=import_status,
                    user_facing_message=_import_status_message(import_status),
                ),
            )
        except ValidationError as exc:
            raise NoofyImportError(
                "Workflow package metadata could not be normalized."
            ) from exc

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
        try:
            return validate_archive_members(
                self.archive,
                required_files=REQUIRED_FILES,
                max_files=MAX_ARCHIVE_FILES,
                max_total_uncompressed_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES,
            )
        except ArchiveValidationError as exc:
            raise NoofyImportError(str(exc)) from exc


def _import_status(
    unresolved_inputs: list[UnresolvedRuntimeInput],
    dashboard: DashboardSchema | None = None,
    dashboard_valid: bool = True,
) -> str:
    return import_status(unresolved_inputs, dashboard, dashboard_valid)


def _import_status_message(status: str) -> str:
    return import_status_message(status)


def _package_with_import_resolution_status(
    package: WorkflowPackage,
    *,
    status: str,
    message: str,
    source_resolution: dict[str, object],
) -> WorkflowPackage:
    return package_with_import_resolution_status(
        package,
        status=status,
        message=message,
        source_resolution=source_resolution,
    )


def _package_with_trust_verification(
    package: WorkflowPackage,
    verification: TrustVerificationResult,
) -> WorkflowPackage:
    return package_with_trust_verification(package, verification)


def _package_with_source_policy(
    package: WorkflowPackage,
    *,
    community_preparation_opted_in: bool,
    policy_status: str = "active",
) -> WorkflowPackage:
    return package_with_source_policy(
        package,
        community_preparation_opted_in=community_preparation_opted_in,
        policy_status=policy_status,
    )


def _source_policy_status_for_import(package: WorkflowPackage) -> str:
    return source_policy_status_for_import(package)


def _non_bundled_required_custom_node_records(
    package: WorkflowPackage,
) -> list[WorkflowCustomNodeRecord]:
    return non_bundled_required_custom_node_records(package)


def _graph_node_types(graph: dict[str, Any]) -> set[str]:
    return graph_node_types(graph)


def _is_resolvable_workflow_node_type(node_type: str) -> bool:
    return is_resolvable_workflow_node_type(node_type)


def _explicit_node_registry_source(
    record: WorkflowCustomNodeRecord,
) -> NodeRegistrySource | None:
    return explicit_node_registry_source(record)


def _safe_archive_name(name: str) -> str:
    try:
        return safe_archive_name(name)
    except ArchiveValidationError as exc:
        raise NoofyImportError(str(exc)) from exc


def _ignored_archive_member(name: str) -> bool:
    return ignored_archive_member(name)


def _single_wrapper_root(members: dict[str, zipfile.ZipInfo]) -> str | None:
    return single_wrapper_root(members, required_files=REQUIRED_FILES)


def _strip_wrapper_root(name: str, root_prefix: str | None) -> str | None:
    return strip_wrapper_root(name, root_prefix)


def _safe_store_segment(value: str) -> str:
    return safe_store_segment(value)


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return zip_member_is_symlink(info)


def _string_field(data: dict[str, Any], key: str, *, fallback: str) -> str:
    return string_field(data, key, fallback=fallback)


def _optional_string_field(data: dict[str, Any], key: str) -> str | None:
    return optional_string_field(data, key)


def _normalized_display_name(data: dict[str, Any], *, fallback: str) -> str:
    return normalized_display_name(data, fallback=fallback)


def _normalize_trust_level(value: Any) -> str:
    return normalize_trust_level(value)


def _normalize_signatures(value: Any) -> list[WorkflowPackageSignature]:
    return normalize_signatures(value)


def _normalize_signed_registry_metadata(value: Any) -> SignedRegistryMetadata | None:
    return normalize_signed_registry_metadata(value)


def _normalize_models(capsule_json: dict[str, Any]) -> list[RequiredModel]:
    return normalize_models(capsule_json)


def _normalize_model_verification_level(
    model: dict[str, Any],
    *,
    checksum: str | None,
    size_bytes: int | None,
) -> str:
    return normalize_model_verification_level(
        model,
        checksum=checksum,
        size_bytes=size_bytes,
    )


def _normalize_asset_ownership(value: Any) -> str:
    return normalize_asset_ownership(value)


def _normalize_custom_nodes(
    capsule_json: dict[str, Any],
) -> list[WorkflowCustomNodeRecord]:
    return normalize_custom_nodes(capsule_json)


def _normalize_dashboard(
    dashboard_json: dict[str, Any],
) -> DashboardSchema:
    return normalize_dashboard(dashboard_json)


def _detect_unresolved_runtime_inputs(
    graph: dict[str, Any],
) -> list[UnresolvedRuntimeInput]:
    return detect_unresolved_runtime_inputs(graph)


def _observed_hardware(
    capsule_json: dict[str, Any], export_report: dict[str, Any]
) -> dict[str, Any]:
    return observed_hardware(capsule_json, export_report)


def imported_package_capsule_lock(package: WorkflowPackage) -> CapsuleLock:
    try:
        return build_imported_package_capsule_lock(package)
    except ImportCapsuleLockError as exc:
        raise NoofyImportError(str(exc)) from exc


def _select_import_runtime_profile(
    profiles,
):
    """Select the local v1 runtime variant for imported package preparation.

    This is a Phase 5c bridge: imported app-owned capsule locks need concrete
    runtime facts before Phase 5d/5e can prepare artifacts. Later phases can
    replace this with an adapter-aware resolution pass before install.
    """
    try:
        return select_import_runtime_profile(profiles)
    except RuntimeProfileSelectionError as exc:
        raise NoofyImportError(str(exc)) from exc


def _reject_unsupported_exported_launch_options(data: dict[str, Any]) -> None:
    try:
        reject_unsupported_exported_launch_options(data)
    except ImportNormalizationError as exc:
        raise NoofyImportError(str(exc)) from exc


def _has_nonempty_launch_option(data: dict[str, Any], key: str) -> bool:
    return has_nonempty_launch_option(data, key)


def _current_os_name() -> str:
    return current_os_name()


def _current_architecture() -> str:
    return current_architecture()


def _preferred_gpu_backend(os_name: str, architecture: str) -> str:
    return preferred_gpu_backend(os_name, architecture)


def _has_nvidia_gpu() -> bool:
    return has_nvidia_gpu()


def _trust_level_from_string(value: str) -> TrustLevel:
    return trust_level_from_string(value)


def _model_locks_from_package(package: WorkflowPackage) -> list[ModelLock]:
    return model_locks_from_package(package)


def _model_id(model: RequiredModel) -> str:
    return model_id(model)


def _hardware_observations_from_package(
    package: WorkflowPackage,
) -> HardwareObservations:
    return hardware_observations_from_package(package)


def _launch_config_hash(
    engine: str, variant, enabled_custom_node_hash: str
) -> str:
    return launch_config_hash(engine, variant, enabled_custom_node_hash)
