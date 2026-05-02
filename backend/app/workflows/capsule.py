"""Discovery and loading of immutable workflow capsule locks.

A capsule lock describes the resolved runtime facts a workflow needs and is
shipped alongside the workflow package as `capsule.lock.json`. The lock is
content-addressed and immutable; mutable per-machine state lives in the
install-state store, never inside the lock.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.runtime.isolation import CapsuleLock

CAPSULE_LOCK_FILENAME = "capsule.lock.json"


class CapsuleLockLoader:
    """Load capsule locks from bundled or user package directories."""

    def __init__(
        self,
        packages_dir: Path,
        user_packages_dir: Path | None = None,
        imported_packages_dir: Path | None = None,
    ) -> None:
        self.packages_dir = packages_dir
        self.user_packages_dir = user_packages_dir
        self.imported_packages_dir = imported_packages_dir

    def get_capsule_lock(self, workflow_id: str) -> CapsuleLock:
        """Return the capsule lock for `workflow_id`.

        Bundled directories are searched first; user packages can ship their
        own lock but cannot replace a bundled lock by id (mirrors the workflow
        loader's anti-shadowing rule).
        """
        for directory in self._search_dirs():
            for lock_path in self._candidate_lock_paths(directory, workflow_id):
                if lock_path.exists():
                    return self._load(lock_path)
            if directory == self.imported_packages_dir:
                imported = self._find_imported_lock(directory, workflow_id)
                if imported is not None:
                    return imported
        raise KeyError(f"No capsule lock found for workflow: {workflow_id}")

    def get_bundled_capsule_lock(self, workflow_id: str) -> CapsuleLock:
        """Return only the bundled lock for `workflow_id`.

        Phase 3 prepares Noofy-shipped starter workflows only. User capsule
        locks are still loadable as data, but they must not enter the verified
        install path until the community resolver and trust policy exist.
        """
        lock_path = self.packages_dir / workflow_id / CAPSULE_LOCK_FILENAME
        if not lock_path.exists():
            raise KeyError(f"No bundled capsule lock found for workflow: {workflow_id}")
        return self._load(lock_path)

    def has_capsule_lock(self, workflow_id: str) -> bool:
        try:
            self.get_capsule_lock(workflow_id)
        except KeyError:
            return False
        return True

    def list_capsule_locks(self) -> list[CapsuleLock]:
        seen: set[str] = set()
        locks: list[CapsuleLock] = []
        for directory in self._search_dirs():
            if not directory.exists():
                continue
            for lock_path in sorted(directory.glob(f"*/{CAPSULE_LOCK_FILENAME}")):
                lock = self._load(lock_path)
                if lock.workflow.package_id in seen:
                    continue
                seen.add(lock.workflow.package_id)
                locks.append(lock)
        return locks

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _search_dirs(self) -> list[Path]:
        directories = [self.packages_dir]
        if self.user_packages_dir is not None:
            directories.append(self.user_packages_dir)
        if self.imported_packages_dir is not None and self.imported_packages_dir not in directories:
            directories.append(self.imported_packages_dir)
        return directories

    def _candidate_lock_paths(self, directory: Path, workflow_id: str) -> list[Path]:
        return [
            directory / workflow_id / CAPSULE_LOCK_FILENAME,
            *sorted(directory.glob(f"*/{workflow_id}/*/{CAPSULE_LOCK_FILENAME}")),
        ]

    def _find_imported_lock(self, directory: Path, workflow_id: str) -> CapsuleLock | None:
        if not directory.exists():
            return None
        for lock_path in sorted(directory.glob(f"*/*/*/{CAPSULE_LOCK_FILENAME}")):
            lock = self._load(lock_path)
            if _imported_workflow_id(
                lock.workflow.publisher_id,
                lock.workflow.package_id,
                lock.workflow.version,
            ) == workflow_id:
                return lock
        return None

    def _load(self, lock_path: Path) -> CapsuleLock:
        with lock_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return CapsuleLock.model_validate(data)


def _imported_workflow_id(publisher_id: str, package_id: str, version: str) -> str:
    return "__".join(
        [
            _safe_store_segment(publisher_id),
            _safe_store_segment(package_id),
            _safe_store_segment(version),
        ]
    )


def _safe_store_segment(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in value.strip())
    cleaned = cleaned.strip(".-_")
    return cleaned or "unknown"
