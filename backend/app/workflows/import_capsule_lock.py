from __future__ import annotations

from typing import Any

from app.runtime.dependencies.dependency_lock import DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION
from app.runtime.fingerprints import (
    FINGERPRINT_SCHEMA_VERSION,
    capsule_fingerprint,
    dependency_env_fingerprint,
    runner_workspace_fingerprint,
    sha256_fingerprint,
)
from app.runtime.dependencies.isolation import (
    CapsuleLock,
    CustomNodeLock,
    HardwareObservations,
    ModelLock,
    TrustMetadata,
)
from app.runtime.profiles import (
    DEFAULT_RUNTIME_PROFILE_CATALOG_PATH,
    RuntimeProfileCatalog,
    RuntimeProfileVariant,
    load_runtime_profile_catalog,
)
from app.trust import workflow_source_policy
from app.workflows.import_normalization import (
    ImportNormalizationError,
    reject_unsupported_exported_launch_options,
)
from app.workflows.import_policy import trust_level_from_string
from app.workflows.import_runtime_profile import (
    RuntimeProfileSelectionError,
    select_import_runtime_profile,
)
from app.workflows.model_grouping import unique_required_models
from app.workflows.package import RequiredModel, WorkflowPackage

NOOFY_ARCHIVE_SCHEMA_VERSION = "0.1.0"


class ImportCapsuleLockError(RuntimeError):
    """Raised when an imported workflow cannot receive an app-owned capsule lock."""


def imported_package_capsule_lock(
    package: WorkflowPackage,
    *,
    runtime_profile_catalog: RuntimeProfileCatalog | None = None,
) -> CapsuleLock:
    if package.identity is None:
        raise ImportCapsuleLockError("Imported package is missing identity metadata.")
    try:
        reject_unsupported_exported_launch_options(package.exported_capsule)
        reject_unsupported_exported_launch_options(package.exported_package)
        catalog = runtime_profile_catalog or load_runtime_profile_catalog(
            DEFAULT_RUNTIME_PROFILE_CATALOG_PATH
        )
        profile, variant = select_import_runtime_profile(catalog.profiles)
    except (ImportNormalizationError, RuntimeProfileSelectionError) as exc:
        raise ImportCapsuleLockError(str(exc)) from exc
    trust_level = trust_level_from_string(package.identity.trust_level)
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
    source_policy = package.source_policy or workflow_source_policy(
        package,
        community_preparation_opted_in=True,
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
    models = model_locks_from_package(package)
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
        launch_config_hash=launch_config_hash(
            package.engine, variant, sha256_fingerprint(custom_nodes)
        ),
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
                "preview_method": variant.launch_defaults.preview_method,
                "preview_size": variant.launch_defaults.preview_size,
                "vram_mode": variant.launch_defaults.vram_mode,
                "attention_backend": variant.launch_defaults.attention_backend,
                "precision_policy": variant.launch_defaults.precision_policy,
                "noofy_environment": dict(variant.launch_defaults.noofy_environment),
                "os": variant.os,
                "architecture": variant.architecture,
                "python_version": variant.python_version,
                "python_build_id": variant.python_build_id,
                "gpu_backend": variant.gpu_backend_profile,
                "dependency_lock_hash": dependency_lock_hash,
                "runner_workspace_hash": runner_fingerprint,
            },
            "custom_nodes": [
                node.model_dump(mode="json", exclude_none=True) for node in custom_nodes
            ],
            "dependencies": {
                "lock_file": "community-runtime.lock",
                "install_policy": DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            },
            "models": [
                model.model_dump(mode="json", exclude_none=True) for model in models
            ],
            "hardware_observations": hardware_observations_from_package(
                package
            ).model_dump(mode="json", exclude_none=True),
            "trust": trust.model_dump(mode="json", exclude_none=True),
            "source_policy": source_policy.model_dump(mode="json", exclude_none=True),
        }
    )


def model_locks_from_package(package: WorkflowPackage) -> list[ModelLock]:
    models: list[ModelLock] = []
    for model in unique_required_models(package.required_models):
        if model.checksum is None or model.size_bytes is None:
            continue
        models.append(
            ModelLock(
                id=model_id(model),
                sha256=model.checksum,
                size_bytes=model.size_bytes,
                source_urls=model.source_urls
                or ([model.source_url] if model.source_url else []),
                comfyui_folder=model.folder,
                filename=model.filename,
            )
        )
    return models


def model_id(model: RequiredModel) -> str:
    if model.source_urls:
        return model.source_urls[0]
    if model.source_url:
        return model.source_url
    return f"{model.folder}/{model.filename}"


def hardware_observations_from_package(
    package: WorkflowPackage,
) -> HardwareObservations:
    observed = package.observed_hardware
    return HardwareObservations(
        observed_peak_vram_mb=(
            observed.get("observed_peak_vram_mb")
            if isinstance(observed.get("observed_peak_vram_mb"), int)
            else None
        ),
        observed_peak_ram_mb=(
            observed.get("observed_peak_ram_mb")
            if isinstance(observed.get("observed_peak_ram_mb"), int)
            else None
        ),
        tested_resolution=(
            observed.get("tested_resolution")
            if isinstance(observed.get("tested_resolution"), str)
            else None
        ),
        tested_batch_size=(
            observed.get("tested_batch_size")
            if isinstance(observed.get("tested_batch_size"), int)
            else None
        ),
        gpu_name=(
            observed.get("gpu_name")
            if isinstance(observed.get("gpu_name"), str)
            else None
        ),
        os=observed.get("os") if isinstance(observed.get("os"), str) else None,
        backend=(
            observed.get("backend")
            if isinstance(observed.get("backend"), str)
            else None
        ),
        precision=(
            observed.get("precision")
            if isinstance(observed.get("precision"), str)
            else None
        ),
        recommended_vram_mb=(
            observed.get("recommended_vram_mb")
            if isinstance(observed.get("recommended_vram_mb"), int)
            else None
        ),
        recommended_ram_mb=(
            observed.get("recommended_ram_mb")
            if isinstance(observed.get("recommended_ram_mb"), int)
            else None
        ),
    )


def launch_config_hash(
    engine: str, variant: RuntimeProfileVariant, enabled_custom_node_hash: str
) -> str:
    launch_defaults = variant.launch_defaults
    return sha256_fingerprint(
        {
            "kind": "runner_launch_config",
            "engine": engine,
            "preview_method": launch_defaults.preview_method,
            "preview_size": launch_defaults.preview_size,
            "vram_mode": launch_defaults.vram_mode,
            "attention_backend": launch_defaults.attention_backend,
            "precision_policy": launch_defaults.precision_policy,
            "enabled_custom_node_set": enabled_custom_node_hash,
            "extra_model_paths_mode": launch_defaults.extra_model_paths_mode,
            "noofy_environment": launch_defaults.noofy_environment,
        }
    )
