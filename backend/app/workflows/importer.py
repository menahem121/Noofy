from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError

from app.archive_safety import (
    PathSafetyError,
    StreamLimitError,
    contained_destination,
    copy_stream_limited,
)
from app.core.config import settings
from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.isolation import CapsuleLock, HardwareObservations, ModelLock, TrustLevel
from app.runtime.profiles import RuntimeProfileCatalog
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
from app.workflows.package_assets import (
    PackageAssetError,
    is_package_asset_value,
    package_asset_archive_path,
    validate_package_asset_reference,
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
from app.workflows.model_grouping import unique_required_models
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
    filter_resolved_runtime_inputs,
    has_nonempty_launch_option,
    normalize_asset_ownership,
    normalize_custom_nodes,
    normalize_dashboard,
    normalize_model_verification_level,
    normalize_models,
    required_models_from_comfyui_workflow,
    normalize_unresolved_runtime_inputs,
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
from app.workflows.library import workflow_package_display_name
from app.workflows.store_paths import (
    assert_path_within,
    imported_workflow_id,
    mutable_package_dir,
    package_identity_dir,
    safe_store_segment,
)
from app.workflows.widget_metadata import normalize_comfyui_widget_metadata

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
        dashboard_assets_dir: Path | None = None,
        node_registry_resolver: NodeRegistryResolver | None = None,
        custom_node_source_cache: CustomNodeSourceCache | None = None,
        trust_verifier: TrustVerifier | None = None,
        runtime_profile_catalog_provider: Callable[[], RuntimeProfileCatalog] | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.log_store = log_store
        self.dashboard_assets_dir = dashboard_assets_dir or settings.paths.dashboard_assets_dir
        self.node_registry_resolver = node_registry_resolver
        self.custom_node_source_cache = custom_node_source_cache
        self.trust_verifier = trust_verifier or TrustVerifier()
        self.runtime_profile_catalog_provider = runtime_profile_catalog_provider

    def preview_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ) -> WorkflowPackage:
        importer = NoofyArchiveImporter(data, original_filename=original_filename)
        package = importer.normalize()
        package = self._with_verified_import_trust(package, importer.trust_payload())
        return _package_with_source_policy(
            package,
            community_preparation_opted_in=allow_unverified_community_preparation,
            policy_status=_source_policy_status_for_import(package),
        )

    def import_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
        duplicate_action: str | None = None,
    ) -> WorkflowPackage:
        try:
            importer = NoofyArchiveImporter(data, original_filename=original_filename)
            package = importer.normalize()
            package = self._with_verified_import_trust(
                package, importer.trust_payload()
            )
            return self._persist_imported_archive(
                data,
                importer=importer,
                package=package,
                original_filename=original_filename,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
                duplicate_action=duplicate_action,
            )
        except Exception as exc:
            self._log_import_failure(original_filename, exc)
            raise

    def import_prepared_archive(
        self,
        data: bytes,
        *,
        package: WorkflowPackage,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
        duplicate_action: str | None = None,
    ) -> WorkflowPackage:
        try:
            return self._persist_imported_archive(
                data,
                importer=NoofyArchiveImporter(data, original_filename=original_filename),
                package=package,
                original_filename=original_filename,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
                duplicate_action=duplicate_action,
            )
        except Exception as exc:
            self._log_import_failure(original_filename, exc)
            raise

    def _persist_imported_archive(
        self,
        data: bytes,
        *,
        importer: NoofyArchiveImporter,
        package: WorkflowPackage,
        original_filename: str | None,
        allow_unverified_community_preparation: bool,
        duplicate_action: str | None,
    ) -> WorkflowPackage:
        if duplicate_action == "copy":
            package = _package_imported_as_copy(package, self.root_dir)
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
        runtime_resolution_unavailable: dict[str, object] | None = None
        try:
            app_capsule_lock: CapsuleLock | None = self._build_capsule_lock(package)
        except ImportCapsuleLockError as exc:
            if isinstance(exc.__cause__, RuntimeProfileSelectionError):
                runtime_resolution_unavailable = _unsupported_local_runtime_resolution(
                    exc.__cause__
                )
            if runtime_resolution_unavailable is not None:
                self.log_store.add(
                    "warning",
                    "Capsule lock unavailable — no runtime profile for this platform",
                    "workflow.import",
                    details={
                        **runtime_resolution_unavailable,
                        "error": str(exc),
                    },
                )
                app_capsule_lock = None
            else:
                raise NoofyImportError(str(exc)) from exc
        try:
            write_imported_package_transaction(
                root_dir=self.root_dir,
                target_dir=target_dir,
                package=package,
                app_capsule_lock=app_capsule_lock,
                runtime_resolution_unavailable=runtime_resolution_unavailable,
                archive_data=data,
                original_filename=original_filename,
                schema_version=NOOFY_ARCHIVE_SCHEMA_VERSION,
                extract_source_files=importer.extract_source_files,
                dashboard_assets_dir=self.dashboard_assets_dir,
                replace_existing=duplicate_action == "replace",
            )
        except FileExistsError as exc:
            raise NoofyImportError(str(exc)) from exc

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
                "required_model_count": len(unique_required_models(package.required_models)),
                "unresolved_input_count": len(package.unresolved_runtime_inputs),
            },
        )
        return package

    def persist_model_identities(self, package: WorkflowPackage) -> None:
        package_dir = self.package_dir(package)
        package_file = package_dir / "package.json"
        capsule_file = package_dir / "capsule.lock.json"
        assert_path_within(self.root_dir, package_file, purpose="update imported workflow models")
        assert_path_within(self.root_dir, capsule_file, purpose="update imported workflow capsule")

        package_payload = json.loads(package_file.read_text(encoding="utf-8"))
        package_payload["required_models"] = [
            model.model_dump(mode="json", exclude_none=True)
            for model in package.required_models
        ]
        try:
            capsule_lock = self._build_capsule_lock(package)
        except ImportCapsuleLockError as exc:
            if not isinstance(exc.__cause__, RuntimeProfileSelectionError):
                raise
            capsule_lock = None

        package_tmp = package_file.with_suffix(".json.tmp")
        capsule_tmp = capsule_file.with_suffix(f".json.{uuid4().hex}.tmp")
        try:
            package_tmp.write_text(
                json.dumps(package_payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            if capsule_lock is not None:
                capsule_tmp.write_text(
                    capsule_lock.model_dump_json(indent=2),
                    encoding="utf-8",
                )
            package_tmp.replace(package_file)
            if capsule_lock is not None:
                capsule_tmp.replace(capsule_file)
        finally:
            package_tmp.unlink(missing_ok=True)
            capsule_tmp.unlink(missing_ok=True)
        self.log_store.add(
            "info",
            "Persisted updated workflow model identities",
            "workflow.models",
            workflow_id=package.metadata.id,
            details={
                "model_count": len(unique_required_models(package.required_models)),
                "capsule_lock_updated": capsule_lock is not None,
            },
        )

    def refresh_capsule_lock(self, package: WorkflowPackage) -> CapsuleLock | None:
        """Refresh the app-owned lock when the selected runtime profile changes."""
        capsule_file = self.package_dir(package) / "capsule.lock.json"
        assert_path_within(
            self.root_dir,
            capsule_file,
            purpose="refresh imported workflow capsule",
        )
        try:
            capsule_lock = self._build_capsule_lock(package)
        except ImportCapsuleLockError as exc:
            if not isinstance(exc.__cause__, RuntimeProfileSelectionError):
                raise NoofyImportError(str(exc)) from exc
            return None

        try:
            current = CapsuleLock.model_validate_json(
                capsule_file.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            current = None
        if current == capsule_lock:
            return capsule_lock

        capsule_tmp = capsule_file.with_suffix(".json.tmp")
        try:
            capsule_tmp.write_text(
                capsule_lock.model_dump_json(indent=2),
                encoding="utf-8",
            )
            capsule_tmp.replace(capsule_file)
        finally:
            capsule_tmp.unlink(missing_ok=True)
        self.log_store.add(
            "info",
            "Refreshed imported workflow runtime capsule",
            "workflow.runtime",
            workflow_id=package.metadata.id,
            details={
                "previous_capsule_fingerprint": (
                    current.runtime.capsule_fingerprint if current is not None else None
                ),
                "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                "runtime_profile_manifest_hash": (
                    capsule_lock.runtime.runtime_profile_manifest_hash
                ),
            },
        )
        return capsule_lock

    def _build_capsule_lock(self, package: WorkflowPackage) -> CapsuleLock:
        catalog = (
            self.runtime_profile_catalog_provider()
            if self.runtime_profile_catalog_provider is not None
            else None
        )
        if catalog is None:
            return build_imported_package_capsule_lock(package)
        return build_imported_package_capsule_lock(
            package,
            runtime_profile_catalog=catalog,
        )

    def _log_import_failure(
        self,
        original_filename: str | None,
        exc: Exception,
    ) -> None:
        self.log_store.add(
            "warning",
            "Workflow import failed",
            "workflow.import",
            details={
                "original_filename": original_filename,
                "error": str(exc),
            },
        )

    def package_dir(self, package: WorkflowPackage) -> Path:
        package_dir = mutable_package_dir(self.root_dir, package)
        if package_dir is None:
            raise NoofyImportError("Imported package is missing identity metadata")
        return package_dir

    def has_package_identity(self, package: WorkflowPackage) -> bool:
        package_dir = mutable_package_dir(self.root_dir, package)
        return package_dir is not None and package_dir.exists()

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
        self._json_cache: dict[str, dict[str, Any]] = {}

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
        trust_level = _normalized_package_trust_level(package_json)
        display_name = _normalized_display_name(package_json, fallback=package_id)
        metadata_fields = _normalized_metadata_fields(package_json)

        models = _with_comfyui_workflow_model_source_urls(
            _normalize_models(capsule_json),
            (
                self._read_json("comfyui_workflow.json")
                if "comfyui_workflow.json" in self.members
                else None
            ),
            graph,
        )
        custom_nodes = _normalize_custom_nodes(capsule_json, package_json)
        dashboard = _normalize_dashboard(dashboard_json)
        dashboard_inputs = [
            WorkflowInput.model_validate(i)
            for i in (dashboard_json.get("inputs") or [])
            if isinstance(i, dict)
        ]
        self._validate_package_asset_defaults(dashboard_inputs)
        dashboard_outputs = [
            WorkflowOutput.model_validate(o)
            for o in (dashboard_json.get("outputs") or [])
            if isinstance(o, dict)
        ]
        unresolved_inputs = filter_resolved_runtime_inputs(
            merge_unresolved_runtime_inputs(
                normalize_unresolved_runtime_inputs(
                    package_json.get("unresolved_runtime_inputs")
                ),
                _detect_unresolved_runtime_inputs(graph),
            ),
            dashboard_inputs,
        )
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
                    display_name=display_name,
                    version=version,
                    description="",
                    author=publisher_id,
                ),
                display_name=display_name,
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
                    display_name=display_name,
                    version=version,
                    description=metadata_fields["description"],
                    author=metadata_fields["author"] or publisher_id,
                    website=metadata_fields["website"],
                    category=metadata_fields["category"],
                    tags=metadata_fields["tags"],
                    icon=metadata_fields["icon"],
                ),
                display_name=display_name,
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
                inputs=dashboard_inputs,
                outputs=dashboard_outputs,
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
                comfyui_widget_metadata=normalize_comfyui_widget_metadata(
                    package_json.get("comfyui_widget_metadata"),
                    graph=graph,
                ),
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
            dashboard_json=(
                self._read_json("dashboard.json")
                if "dashboard.json" in self.members
                else {}
            ),
            capsule_json=self._read_json("capsule.lock.json"),
            export_report=self._read_json("export-report.json"),
        )

    def extract_source_files(self, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        extracted_bytes = 0
        for name, info in self.members.items():
            if info.is_dir():
                continue
            try:
                target = contained_destination(target_dir, name)
            except PathSafetyError as exc:
                raise NoofyImportError(
                    "Workflow package contains an unsafe path."
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with (
                    self.archive.open(info, "r") as source,
                    target.open("xb") as dest,
                ):
                    copied_bytes = copy_stream_limited(
                        source,
                        dest,
                        max_bytes=MAX_TOTAL_UNCOMPRESSED_BYTES - extracted_bytes,
                    )
            except StreamLimitError as exc:
                target.unlink(missing_ok=True)
                raise NoofyImportError(
                    "Workflow package expands to too much data."
                ) from exc
            extracted_bytes += copied_bytes
            if copied_bytes != info.file_size:
                target.unlink(missing_ok=True)
                raise NoofyImportError(
                    "Workflow package member size could not be verified."
                )

    def _validate_package_asset_defaults(self, inputs: list[WorkflowInput]) -> None:
        for workflow_input in inputs:
            if not is_package_asset_value(workflow_input.default):
                continue
            try:
                reference = validate_package_asset_reference(
                    workflow_input.default,
                    workflow_input=workflow_input,
                )
                member_name = package_asset_archive_path(reference["asset_id"])
            except PackageAssetError as exc:
                raise NoofyImportError(
                    f"Workflow input '{workflow_input.id}' has an invalid packaged default asset."
                ) from exc
            if member_name not in self.members:
                raise NoofyImportError(
                    f"Workflow input '{workflow_input.id}' references a packaged default asset that is missing from the archive."
                )
            info = self.members[member_name]
            if isinstance(reference.get("size_bytes"), int) and info.file_size != reference["size_bytes"]:
                raise NoofyImportError(
                    f"Workflow input '{workflow_input.id}' references a packaged default asset with mismatched size metadata."
                )
            sha256 = reference.get("sha256")
            if isinstance(sha256, str):
                digest = hashlib.sha256(self.archive.read(info)).hexdigest()
                if digest != sha256.removeprefix("sha256:"):
                    raise NoofyImportError(
                        f"Workflow input '{workflow_input.id}' references a packaged default asset with mismatched content metadata."
                    )

    def _read_json(self, name: str) -> dict[str, Any]:
        cached = self._json_cache.get(name)
        if cached is not None:
            return cached
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
        self._json_cache[name] = payload
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


def _package_imported_as_copy(package: WorkflowPackage, root_dir: Path) -> WorkflowPackage:
    if package.identity is None:
        raise NoofyImportError("Cannot import a copy of a package without identity metadata.")

    original_identity = package.identity
    display_name = workflow_package_display_name(package)
    copy_index = 1
    while True:
        suffix = "-copy" if copy_index == 1 else f"-copy-{copy_index}"
        package_id = f"{original_identity.package_id}{suffix}"
        name_suffix = " Copy" if copy_index == 1 else f" Copy {copy_index}"
        identity = WorkflowPackageIdentity(
            publisher_id="local",
            package_id=package_id,
            version=original_identity.version,
            trust_level=TrustLevel.QUARANTINED_COMMUNITY.value,
            source="local_copy",
            signature=None,
            signatures=[],
            signed_registry_metadata=None,
        )
        if not package_identity_dir(root_dir, identity).exists():
            workflow_id = imported_workflow_id(
                identity.publisher_id,
                identity.package_id,
                identity.version,
            )
            import_metadata = package.import_metadata
            if import_metadata is not None:
                developer_details = dict(import_metadata.developer_details)
                developer_details.pop("trust_verification", None)
                developer_details["copied_from_identity"] = original_identity.model_dump(mode="json")
                import_metadata = import_metadata.model_copy(
                    update={"developer_details": developer_details}
                )
            return package.model_copy(
                update={
                    "identity": identity,
                    "display_name": f"{display_name}{name_suffix}",
                    "metadata": package.metadata.model_copy(
                        update={
                            "id": workflow_id,
                            "name": f"{display_name}{name_suffix}",
                            "display_name": f"{display_name}{name_suffix}",
                        }
                    ),
                    "import_metadata": import_metadata,
                    "source_policy": None,
                }
            )
        copy_index += 1


def _zip_member_is_symlink(info: zipfile.ZipInfo) -> bool:
    return zip_member_is_symlink(info)


def _string_field(data: dict[str, Any], key: str, *, fallback: str) -> str:
    return string_field(data, key, fallback=fallback)


def _optional_string_field(data: dict[str, Any], key: str) -> str | None:
    return optional_string_field(data, key)


def _normalized_display_name(data: dict[str, Any], *, fallback: str) -> str:
    return normalized_display_name(data, fallback=fallback)


def _normalized_metadata_fields(data: dict[str, Any]) -> dict[str, Any]:
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    def text(key: str) -> str:
        nested = metadata.get(key)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return ""

    tags_value = metadata.get("tags")
    if not isinstance(tags_value, list):
        tags_value = data.get("tags")
    tags = []
    seen = set()
    if isinstance(tags_value, list):
        for item in tags_value:
            if not isinstance(item, str):
                continue
            cleaned = item.strip()
            if not cleaned or cleaned.casefold() in seen:
                continue
            seen.add(cleaned.casefold())
            tags.append(cleaned)

    return {
        "description": text("description"),
        "author": text("author"),
        "website": text("website"),
        "category": text("category"),
        "tags": tags,
        "icon": text("icon"),
    }


def _normalize_trust_level(value: Any) -> str:
    return normalize_trust_level(value)


def _normalized_package_trust_level(package_json: dict[str, Any]) -> str:
    trust_level = _normalize_trust_level(package_json.get("trust_level"))
    source_policy = package_json.get("source_policy")
    if trust_level == "unsupported" and source_policy == "local":
        return "quarantined_community"
    return trust_level


def _normalize_signatures(value: Any) -> list[WorkflowPackageSignature]:
    return normalize_signatures(value)


def _normalize_signed_registry_metadata(value: Any) -> SignedRegistryMetadata | None:
    return normalize_signed_registry_metadata(value)


def _normalize_models(capsule_json: dict[str, Any]) -> list[RequiredModel]:
    return normalize_models(capsule_json)


def _with_comfyui_workflow_model_source_urls(
    models: list[RequiredModel],
    comfyui_workflow: dict[str, Any] | None,
    comfyui_graph: dict[str, Any] | None = None,
) -> list[RequiredModel]:
    if not isinstance(comfyui_workflow, dict) and not isinstance(comfyui_graph, dict):
        return models
    workflow_models = required_models_from_comfyui_workflow(
        comfyui_workflow or {},
        comfyui_graph=comfyui_graph,
    )
    if not workflow_models:
        return models
    existing_by_target = {(model.folder, model.filename): model for model in models}
    merged = list(models)
    for workflow_model in workflow_models:
        target = (workflow_model.folder, workflow_model.filename)
        model = existing_by_target.get(target)
        if model is None:
            existing_by_target[target] = workflow_model
            merged.append(workflow_model)
            continue
        if model.source_urls or model.source_url:
            continue
        if not workflow_model.source_urls:
            continue
        model.source_urls = list(workflow_model.source_urls)
        model.source_url = workflow_model.source_urls[0]
    return merged

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
    package_json: dict[str, Any] | None = None,
) -> list[WorkflowCustomNodeRecord]:
    return normalize_custom_nodes(capsule_json, package_json)


def _normalize_dashboard(
    dashboard_json: dict[str, Any],
) -> DashboardSchema:
    return normalize_dashboard(dashboard_json)


def merge_unresolved_runtime_inputs(
    exported_inputs: list[UnresolvedRuntimeInput],
    detected_inputs: list[UnresolvedRuntimeInput],
) -> list[UnresolvedRuntimeInput]:
    merged: list[UnresolvedRuntimeInput] = []
    seen: set[tuple[str, str]] = set()
    for runtime_input in exported_inputs:
        key = (runtime_input.node_id, runtime_input.input_name)
        if key in seen:
            continue
        seen.add(key)
        merged.append(runtime_input)
    for runtime_input in detected_inputs:
        key = (runtime_input.node_id, runtime_input.input_name)
        if key in seen:
            continue
        seen.add(key)
        merged.append(runtime_input)
    return merged


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


def _unsupported_local_runtime_resolution(
    exc: RuntimeProfileSelectionError,
) -> dict[str, object] | None:
    os_name = current_os_name()
    architecture = current_architecture()
    if os_name != "darwin" or architecture != "x64":
        return None
    expected_message = f"No supported runtime profile variant for {os_name}/{architecture}."
    if str(exc) != expected_message:
        return None
    return {
        "selection_stage": "unavailable",
        "reason": "unsupported_local_runtime_platform",
        "os": os_name,
        "architecture": architecture,
    }


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
