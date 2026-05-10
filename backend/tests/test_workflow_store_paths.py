from pathlib import Path

from app.workflows.package import WorkflowPackageIdentity
from app.workflows.store_paths import (
    imported_workflow_id,
    package_identity_dir,
    safe_store_segment,
)


def test_safe_store_segment_normalizes_empty_and_unsafe_values() -> None:
    assert safe_store_segment(" Publisher/Name ") == "Publisher-Name"
    assert safe_store_segment("...") == "unknown"


def test_imported_workflow_id_uses_store_segments() -> None:
    assert imported_workflow_id("Noofy Labs", "Demo/Pack", "1.0.0") == (
        "Noofy-Labs__Demo-Pack__1.0.0"
    )


def test_package_identity_dir_uses_three_level_store_layout() -> None:
    identity = WorkflowPackageIdentity(
        publisher_id="Noofy Labs",
        package_id="Demo/Pack",
        version="1.0.0",
        trust_level="community",
    )

    assert package_identity_dir(Path("/store"), identity) == Path(
        "/store/Noofy-Labs/Demo-Pack/1.0.0"
    )
