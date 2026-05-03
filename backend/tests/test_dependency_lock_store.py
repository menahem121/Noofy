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


def _lock_with_hash(lock_hash: str, variant_id: str) -> ResolvedDependencyLock:
    return ResolvedDependencyLock(
        lock_hash=lock_hash,
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_variant_id=variant_id,
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
        resolver=ResolverMetadata(name="noofy-managed-core", version="0.1.0"),
        wheels=[],
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


def test_dependency_lock_store_keeps_runtime_variants_for_same_dependency_hash(tmp_path: Path) -> None:
    store = ResolvedDependencyLockStore(tmp_path)
    lock_hash = "sha256:" + ("2" * 64)
    darwin_lock = _lock_with_hash(lock_hash, "darwin-arm64-mps")
    linux_lock = _lock_with_hash(lock_hash, "linux-x64-cuda130")

    darwin_path = store.write(darwin_lock)
    linux_path = store.write(linux_lock)

    assert darwin_path == store.path_for_hash(lock_hash)
    assert linux_path == store.path_for_lock(linux_lock)
    assert store.read_matching(
        lock_hash,
        runtime_profile_id=darwin_lock.runtime_profile_id,
        runtime_profile_variant_id=darwin_lock.runtime_profile_variant_id,
        runtime_profile_manifest_hash=darwin_lock.runtime_profile_manifest_hash,
        install_policy_version=darwin_lock.install_policy_version,
    ) == darwin_lock
    assert store.read_matching(
        lock_hash,
        runtime_profile_id=linux_lock.runtime_profile_id,
        runtime_profile_variant_id=linux_lock.runtime_profile_variant_id,
        runtime_profile_manifest_hash=linux_lock.runtime_profile_manifest_hash,
        install_policy_version=linux_lock.install_policy_version,
    ) == linux_lock
