from __future__ import annotations

from typing import Any

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.workflows.package import (
    DashboardSchema,
    RequiredModel,
    SignedRegistryMetadata,
    UnresolvedRuntimeInput,
    WorkflowCustomNodeRecord,
    WorkflowPackageSignature,
)

NOOFY_ARCHIVE_SCHEMA_VERSION = "0.1.0"
LOCAL_IMAGE_NODE_TYPES = {"LoadImage", "LoadImageMask"}
UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS = {
    "comfyui_launch_options",
    "launch_config",
    "launch_options",
    "runner_launch_options",
}


class ImportNormalizationError(RuntimeError):
    """Raised when normalized archive metadata has unsupported app semantics."""


def string_field(data: dict[str, Any], key: str, *, fallback: str) -> str:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def optional_string_field(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def normalize_source_urls(value: Any, *, fallback: Any = None) -> list[str]:
    urls: list[str] = []

    def add(candidate: str) -> None:
        cleaned = candidate.strip()
        if cleaned:
            urls.append(cleaned)

    def add_value(raw: Any) -> None:
        if isinstance(raw, str):
            add(raw)
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if isinstance(item, str):
                add(item)

    add_value(value)
    add_value(fallback)

    normalized: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def normalized_display_name(data: dict[str, Any], *, fallback: str) -> str:
    value = string_field(data, "display_name", fallback=fallback)
    cleaned = value.lstrip("*").strip()
    return cleaned or fallback


def normalize_trust_level(value: Any) -> str:
    if value == "noofy_verified":
        return "noofy_verified"
    if value == "registry_locked":
        return "registry_locked"
    if value in {"public_unverified", "quarantined_community"}:
        return "quarantined_community"
    return "unsupported"


def normalize_signatures(value: Any) -> list[WorkflowPackageSignature]:
    if not isinstance(value, list):
        return []
    signatures: list[WorkflowPackageSignature] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key_id = item.get("key_id")
        algorithm = item.get("algorithm")
        signature_value = item.get("value")
        if not all(
            isinstance(field, str) and field.strip()
            for field in (key_id, algorithm, signature_value)
        ):
            continue
        signatures.append(
            WorkflowPackageSignature(
                key_id=key_id.strip(),
                algorithm=algorithm.strip(),
                value=signature_value.strip(),
            )
        )
    return signatures


def normalize_signed_registry_metadata(value: Any) -> SignedRegistryMetadata | None:
    if not isinstance(value, dict):
        return None
    registry_id = value.get("registry_id")
    snapshot_hash = value.get("snapshot_hash")
    signature = value.get("signature")
    if not all(
        isinstance(field, str) and field.strip()
        for field in (registry_id, snapshot_hash, signature)
    ):
        return None
    return SignedRegistryMetadata(
        registry_id=registry_id.strip(),
        snapshot_hash=snapshot_hash.strip(),
        signature=signature.strip(),
        key_id=optional_string_field(value, "key_id"),
        algorithm=optional_string_field(value, "algorithm"),
    )


def normalize_models(capsule_json: dict[str, Any]) -> list[RequiredModel]:
    models = capsule_json.get("models", [])
    if not isinstance(models, list):
        return []

    normalized: list[RequiredModel] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        folder = string_field(model, "comfyui_folder", fallback="")
        filename = string_field(model, "filename", fallback="")
        if not folder or not filename:
            continue
        source_urls = normalize_source_urls(
            model.get("source_urls"),
            fallback=model.get("source_url"),
        )
        sha256 = model.get("sha256")
        checksum = None
        if isinstance(sha256, str) and sha256:
            checksum = sha256 if sha256.startswith("sha256:") else f"sha256:{sha256}"
        size_bytes_value = model.get("size_bytes")
        size_bytes = size_bytes_value if isinstance(size_bytes_value, int) else None
        model_type = model.get("model_type")
        node_id = model.get("node_id")
        node_type = model.get("node_type")
        input_name = model.get("input_name")
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
                node_id=node_id.strip() if isinstance(node_id, str) and node_id.strip() else None,
                node_type=node_type.strip() if isinstance(node_type, str) and node_type.strip() else None,
                input_name=input_name.strip() if isinstance(input_name, str) and input_name.strip() else None,
                source_url=source_urls[0] if source_urls else None,
                source_urls=source_urls,
                checksum=checksum,
                model_type=model_type if isinstance(model_type, str) else None,
                size_bytes=size_bytes,
                verification_level=normalize_model_verification_level(
                    model,
                    checksum=checksum,
                    size_bytes=size_bytes,
                ),
                identity_verified_by_exporter=(
                    identity_verified
                    if isinstance(identity_verified, bool)
                    else checksum is not None and size_bytes is not None
                ),
                local_file_available_at_export=(
                    local_file_available
                    if isinstance(local_file_available, bool)
                    else None
                ),
                bundled=bundled if isinstance(bundled, bool) else False,
                asset_ownership=normalize_asset_ownership(
                    model.get("asset_ownership")
                ),
                identity_warnings=identity_warnings,
            )
        )
    return normalized


def normalize_model_verification_level(
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


def normalize_asset_ownership(value: Any) -> str:
    if value in {item.value for item in AssetOwnership}:
        return str(value)
    return AssetOwnership.EXTERNAL_REFERENCE.value


def normalize_custom_nodes(
    capsule_json: dict[str, Any],
) -> list[WorkflowCustomNodeRecord]:
    nodes = capsule_json.get("custom_nodes", [])
    if not isinstance(nodes, list):
        return []

    normalized: list[WorkflowCustomNodeRecord] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        folder_name = string_field(
            node,
            "folder_name",
            fallback=string_field(node, "id", fallback="custom-node"),
        )
        node_id = string_field(node, "id", fallback=folder_name)
        requirements_files = [
            item for item in node.get("requirements_files", []) if isinstance(item, str)
        ]
        node_types = [
            item for item in node.get("node_types", []) if isinstance(item, str)
        ]
        normalized.append(
            WorkflowCustomNodeRecord(
                id=node_id,
                folder_name=folder_name,
                source=string_field(node, "source", fallback="unknown"),
                included=bool(node.get("included")),
                node_types=node_types,
                requirements_files=requirements_files,
                has_install_py=bool(node.get("has_install_py")),
                sha256_manifest=(
                    node.get("sha256_manifest")
                    if isinstance(node.get("sha256_manifest"), str)
                    else None
                ),
                source_ref=(
                    node.get("source_ref")
                    if isinstance(node.get("source_ref"), str)
                    else None
                ),
                source_content_hash=(
                    node.get("source_content_hash")
                    if isinstance(node.get("source_content_hash"), str)
                    else None
                ),
                source_cache_ref=(
                    node.get("source_cache_ref")
                    if isinstance(node.get("source_cache_ref"), str)
                    else None
                ),
                source_archive_subdir=optional_string_field(
                    node, "source_archive_subdir"
                )
                or optional_string_field(node, "archive_subdir"),
                resolution_method=(
                    node.get("resolution_method")
                    if isinstance(node.get("resolution_method"), str)
                    else None
                ),
            )
        )
    return normalized


def normalize_dashboard(
    dashboard_json: dict[str, Any],
) -> DashboardSchema:
    stripped = {
        k: v for k, v in dashboard_json.items() if k not in ("inputs", "outputs")
    }
    archive_status = stripped.get("status")
    if archive_status not in ("configured", "not_configured", "invalid"):
        archive_status = None
    if "version" in stripped and isinstance(stripped.get("sections"), list):
        try:
            schema = DashboardSchema.model_validate(stripped)
            if archive_status is None and not any(s.controls for s in schema.sections):
                schema = schema.model_copy(update={"status": "not_configured"})
            return schema
        except Exception:
            pass
    explicit_status = archive_status if archive_status else "not_configured"
    return DashboardSchema(
        version=NOOFY_ARCHIVE_SCHEMA_VERSION,
        status=explicit_status,
        sections=[],
    )


def detect_unresolved_runtime_inputs(
    graph: dict[str, Any],
) -> list[UnresolvedRuntimeInput]:
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


def observed_hardware(
    capsule_json: dict[str, Any], export_report: dict[str, Any]
) -> dict[str, Any]:
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


def reject_unsupported_exported_launch_options(data: dict[str, Any]) -> None:
    launch_keys = sorted(
        key
        for key in UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS
        if has_nonempty_launch_option(data, key)
    )
    runtime = data.get("runtime")
    if isinstance(runtime, dict):
        launch_keys.extend(
            f"runtime.{key}"
            for key in sorted(UNSUPPORTED_EXPORTED_LAUNCH_OPTION_KEYS)
            if has_nonempty_launch_option(runtime, key)
        )
    if launch_keys:
        raise ImportNormalizationError(
            "Workflow package declares unsupported launch options: "
            + ", ".join(launch_keys)
        )


def has_nonempty_launch_option(data: dict[str, Any], key: str) -> bool:
    value = data.get(key)
    return value not in (None, {}, [], "")
