"""Persistence for Noofy resolved dependency locks."""

from __future__ import annotations

import json
from pathlib import Path

from app.runtime.dependency_lock import ResolvedDependencyLock, resolved_dependency_lock_hash


class ResolvedDependencyLockStore:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def path_for_hash(self, lock_hash: str) -> Path:
        return self.root_dir / _safe_fingerprint(lock_hash) / "dependency-lock.json"

    def exists(self, lock_hash: str) -> bool:
        return self.path_for_hash(lock_hash).exists()

    def read(self, lock_hash: str) -> ResolvedDependencyLock:
        with self.path_for_hash(lock_hash).open("r", encoding="utf-8") as file:
            lock = ResolvedDependencyLock.model_validate(json.load(file))
        stored_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        if stored_hash != lock_hash:
            raise ValueError("Resolved dependency lock hash does not match store path.")
        return lock

    def write(self, lock: ResolvedDependencyLock) -> Path:
        lock_hash = lock.lock_hash or resolved_dependency_lock_hash(lock)
        path = self.path_for_hash(lock_hash)
        if path.exists():
            existing = self.read(lock_hash)
            if existing != lock:
                raise ValueError("Existing resolved dependency lock differs for hash.")
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(lock.model_dump_json(indent=2), encoding="utf-8")
        tmp_path.replace(path)
        return path


def _safe_fingerprint(fingerprint: str) -> str:
    return fingerprint.replace("sha256:", "").replace("/", "_").replace("\\", "_").replace(":", "_")
