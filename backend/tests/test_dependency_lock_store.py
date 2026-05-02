from pathlib import Path

import pytest

from app.runtime.dependency_lock import (
    DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    ResolvedDependencyLock,
    ResolverMetadata,
    with_computed_lock_hash,
)
from app.runtime.dependency_lock_store import ResolvedDependencyLockStore


def _lock() -> ResolvedDependencyLock:
    return with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id="noofy-comfyui-v1-default",
            runtime_profile_variant_id="darwin-arm64-mps",
            runtime_profile_manifest_hash="sha256:" + ("9" * 64),
            install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            resolver=ResolverMetadata(name="uv", version="0.9.0"),
            wheels=[],
        )
    )


def test_dependency_lock_store_persists_lock_by_hash(tmp_path: Path) -> None:
    store = ResolvedDependencyLockStore(tmp_path)
    lock = _lock()

    path = store.write(lock)

    assert path == store.path_for_hash(lock.lock_hash)
    assert store.exists(lock.lock_hash)
    assert store.read(lock.lock_hash) == lock


def test_dependency_lock_store_rejects_hash_path_mismatch(tmp_path: Path) -> None:
    store = ResolvedDependencyLockStore(tmp_path)
    lock = _lock()
    path = store.write(lock)
    path.write_text(
        lock.model_copy(update={"lock_hash": "sha256:" + ("1" * 64)}).model_dump_json(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="hash does not match"):
        store.read(lock.lock_hash)
