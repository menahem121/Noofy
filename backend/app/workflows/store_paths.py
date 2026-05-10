from __future__ import annotations

from pathlib import Path

from app.workflows.package import WorkflowPackage, WorkflowPackageIdentity


def safe_store_segment(value: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-"
        for char in value.strip()
    )
    cleaned = cleaned.strip(".-_")
    return cleaned or "unknown"


def imported_workflow_id(publisher_id: str, package_id: str, version: str) -> str:
    return "__".join(
        [
            safe_store_segment(publisher_id),
            safe_store_segment(package_id),
            safe_store_segment(version),
        ]
    )


def imported_workflow_id_for_identity(identity: WorkflowPackageIdentity) -> str:
    return imported_workflow_id(
        identity.publisher_id,
        identity.package_id,
        identity.version,
    )


def package_identity_dir(root_dir: Path, identity: WorkflowPackageIdentity) -> Path:
    return (
        root_dir
        / safe_store_segment(identity.publisher_id)
        / safe_store_segment(identity.package_id)
        / safe_store_segment(identity.version)
    )


def mutable_package_dir(root_dir: Path, package: WorkflowPackage) -> Path | None:
    if package.identity is None:
        return None
    return package_identity_dir(root_dir, package.identity)
