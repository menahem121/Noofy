"""Persistence for Noofy resolved dependency locks."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from app.runtime.dependencies.dependency_lock import (
    ResolvedDependencyLock,
    dependency_lock_source_policy_matches,
    resolved_dependency_lock_hash,
)
from app.source_policy import SourcePolicy


class ResolvedDependencyLockStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def path_for_hash(self, lock_hash: str) -> Path:
        return self.root_dir / _safe_fingerprint(lock_hash) / "dependency-lock.json"

    def path_for_lock(self, lock: ResolvedDependencyLock) -> Path:
        lock_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        runtime_identity = _identity_digest(
            "runtime",
            (
                lock.runtime_profile_id,
                lock.runtime_profile_variant_id,
                lock.runtime_profile_manifest_hash,
                lock.install_policy_version,
            ),
        )
        return (
            self.root_dir
            / _safe_fingerprint(lock_hash)
            / f"dependency-lock.{runtime_identity}.json"
        )

    def disambiguated_path_for_lock(self, lock: ResolvedDependencyLock) -> Path:
        base_path = self.path_for_lock(lock)
        lock_identity = _identity_digest("lock", (resolved_dependency_lock_hash(lock),))
        return base_path.with_name(f"{base_path.stem}.{lock_identity}.json")

    def exists(self, lock_hash: str) -> bool:
        return any(path.exists() for path in self._candidate_paths(lock_hash))

    def read(self, lock_hash: str) -> ResolvedDependencyLock:
        lock = self._read_path(self.path_for_hash(lock_hash))
        stored_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        if stored_hash != lock_hash:
            raise ValueError("Resolved dependency lock hash does not match store path.")
        return lock

    def read_matching(
        self,
        lock_hash: str,
        *,
        runtime_profile_id: str,
        runtime_profile_variant_id: str,
        runtime_profile_manifest_hash: str,
        install_policy_version: str,
        source_policy: SourcePolicy | None = None,
    ) -> ResolvedDependencyLock | None:
        no_policy_fallback: ResolvedDependencyLock | None = None
        for path in self._candidate_paths(lock_hash):
            if not path.exists():
                continue
            lock = self._read_path(path)
            stored_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
            if stored_hash != lock_hash:
                raise ValueError("Resolved dependency lock hash does not match store path.")
            if (
                lock.runtime_profile_id == runtime_profile_id
                and lock.runtime_profile_variant_id == runtime_profile_variant_id
                and lock.runtime_profile_manifest_hash == runtime_profile_manifest_hash
                and lock.install_policy_version == install_policy_version
            ):
                if source_policy is None:
                    return lock
                if dependency_lock_source_policy_matches(lock, source_policy):
                    return lock
                if (
                    lock.source_policy is None
                    and not lock.wheels
                    and not lock.requirements
                    and no_policy_fallback is None
                ):
                    no_policy_fallback = lock
        if source_policy is not None:
            return no_policy_fallback
        return None

    def write(self, lock: ResolvedDependencyLock) -> Path:
        lock_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        path = self.path_for_hash(lock_hash)
        if path.exists():
            existing = self.read(lock_hash)
            if existing != lock:
                path = self.path_for_lock(lock)
                if path.exists():
                    existing = self._read_path(path)
                    if existing != lock:
                        path = self.disambiguated_path_for_lock(lock)
                        if path.exists():
                            existing = self._read_path(path)
                            if existing != lock:
                                raise ValueError("Existing resolved dependency lock differs for hash.")
                            return path
                        return self._write_path(path, lock)
                    return path
                return self._write_path(path, lock)
            return path
        return self._write_path(path, lock)

    def _candidate_paths(self, lock_hash: str) -> list[Path]:
        directory = self.root_dir / _safe_fingerprint(lock_hash)
        return [self.path_for_hash(lock_hash), *sorted(directory.glob("dependency-lock.*.json"))]

    def _read_path(self, path: Path) -> ResolvedDependencyLock:
        with path.open("r", encoding="utf-8") as file:
            return ResolvedDependencyLock.model_validate(json.load(file))

    def _write_path(self, path: Path, lock: ResolvedDependencyLock) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(lock.model_dump_json(indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return path


def _safe_fingerprint(fingerprint: str) -> str:
    return fingerprint.replace("sha256:", "").replace("/", "_").replace("\\", "_").replace(":", "_")


def _identity_digest(prefix: str, parts: tuple[str, ...]) -> str:
    digest = sha256("\0".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"
