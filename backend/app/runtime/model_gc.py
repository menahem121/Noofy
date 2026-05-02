"""Model-reference cleanup policy helpers.

Phase 5d only records model ownership and materialized model views. Full
reference-index garbage collection lands later, but deletion decisions should
already have one explicit policy surface so user-owned model sources are never
treated like app-owned blobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.artifacts import AssetOwnership
from app.runtime.isolation import InstalledModelReference


@dataclass(frozen=True)
class ModelReferenceCleanupPolicy:
    source_path: Path | None
    may_delete_source: bool
    materialized_path: Path | None
    may_delete_materialized: bool


def model_reference_cleanup_policy(ref: InstalledModelReference) -> ModelReferenceCleanupPolicy:
    """Return deletion permissions for paths named by an install-state model ref."""
    if ref.asset_ownership in {AssetOwnership.NOOFY_DOWNLOADED, AssetOwnership.NOOFY_IMPORTED}:
        source_path = Path(ref.blob_path or ref.source_path) if (ref.blob_path or ref.source_path) else None
        may_delete_source = source_path is not None
    else:
        source_path = Path(ref.source_path) if ref.source_path else None
        may_delete_source = False
    return ModelReferenceCleanupPolicy(
        source_path=source_path,
        may_delete_source=may_delete_source,
        materialized_path=Path(ref.materialized_path) if ref.materialized_path else None,
        may_delete_materialized=ref.materialized_path is not None,
    )
