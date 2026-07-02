from pathlib import Path

import pytest

from app.runtime.dependencies.dependency_lock import (
    DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyWheel,
    ResolverMetadata,
    with_computed_lock_hash,
)
from app.runtime.dependencies.dependency_lock_store import ResolvedDependencyLockStore
from app.source_policy import SourcePolicy


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


def _policy(package_source_type: str) -> SourcePolicy:
    return SourcePolicy(
        trust_level="quarantined_community",
        source_policy="explicit_opt_in_and_isolated_capsule_required",
        package_source_type=package_source_type,
        automatic_preparation_allowed=True,
        allowed_source_origins=["explicit-metadata", "registry-locked"],
        model_source_trust="hashed",
        community_preparation_opt_in_required=True,
        community_preparation_opted_in=True,
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


def test_dependency_lock_store_variant_path_stays_below_windows_path_limit() -> None:
    root = Path(
        r"C:\Users\Administrator\AppData\Roaming\Noofy\runtime-store\dependency-locks"
    )
    store = ResolvedDependencyLockStore(root)
    lock = _lock_with_hash("sha256:" + ("2" * 64), "linux-x64-cuda130")

    assert len(str(store.path_for_lock(lock).with_suffix(".json.tmp"))) < 260


def test_dependency_lock_store_keeps_same_runtime_identity_policy_variants(tmp_path: Path) -> None:
    store = ResolvedDependencyLockStore(tmp_path)
    lock_hash = "sha256:" + ("3" * 64)
    first = _lock_with_hash(lock_hash, "linux-x64-cuda130").model_copy(
        update={"source_policy": _policy("unknown")}
    )
    second = _lock_with_hash(lock_hash, "linux-x64-cuda130").model_copy(
        update={"source_policy": _policy("noofy_archive_import")}
    )

    first_path = store.write(first)
    second_path = store.write(second)

    assert first_path == store.path_for_hash(lock_hash)
    assert second_path != first_path
    assert second_path.exists()
    assert store.read_matching(
        lock_hash,
        runtime_profile_id=second.runtime_profile_id,
        runtime_profile_variant_id=second.runtime_profile_variant_id,
        runtime_profile_manifest_hash=second.runtime_profile_manifest_hash,
        install_policy_version=second.install_policy_version,
        source_policy=_policy("noofy_archive_import"),
    ) == second
    assert store.read_matching(
        lock_hash,
        runtime_profile_id=first.runtime_profile_id,
        runtime_profile_variant_id=first.runtime_profile_variant_id,
        runtime_profile_manifest_hash=first.runtime_profile_manifest_hash,
        install_policy_version=first.install_policy_version,
        source_policy=_policy("unknown"),
    ) == first
    assert store.write(second) == second_path


def test_dependency_lock_store_does_not_policy_fallback_to_unattributed_packages(
    tmp_path: Path,
) -> None:
    store = ResolvedDependencyLockStore(tmp_path)
    lock_hash = "sha256:" + ("4" * 64)
    lock = _lock_with_hash(lock_hash, "linux-x64-cuda130").model_copy(
        update={
            "wheels": [
                ResolvedDependencyWheel(
                    name="demo-package",
                    version="1.0.0",
                    wheel_filename="demo_package-1.0.0-py3-none-any.whl",
                    sha256="sha256:" + ("a" * 64),
                    source_kind=DependencySourceKind.APPROVED_CACHE,
                    approved_cache_ref="demo_package-1.0.0-py3-none-any.whl",
                    platform_tags=["py3-none-any"],
                    relationship=DependencyRelationship.DIRECT,
                    requested_by=["node-a"],
                    resolver_name="noofy-managed-core",
                    resolver_version="0.1.0",
                )
            ]
        }
    )
    store.write(lock)

    assert (
        store.read_matching(
            lock_hash,
            runtime_profile_id=lock.runtime_profile_id,
            runtime_profile_variant_id=lock.runtime_profile_variant_id,
            runtime_profile_manifest_hash=lock.runtime_profile_manifest_hash,
            install_policy_version=lock.install_policy_version,
            source_policy=_policy("noofy_archive_import"),
        )
        is None
    )
