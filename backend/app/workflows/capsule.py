"""Discovery and loading of immutable workflow capsule locks.

A capsule lock describes the resolved runtime facts a workflow needs and is
shipped alongside the workflow package as `capsule.lock.json`. The lock is
content-addressed and immutable; mutable per-machine state lives in the
install-state store, never inside the lock.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.runtime.dependencies.isolation import CapsuleLock
from app.workflows.import_normalization import normalize_custom_nodes
from app.workflows.store_paths import imported_workflow_id

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
            if imported_workflow_id(
                lock.workflow.publisher_id,
                lock.workflow.package_id,
                lock.workflow.version,
            ) == workflow_id:
                return lock
        return None

    def _load(self, lock_path: Path) -> CapsuleLock:
        with lock_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not data.get("custom_nodes"):
            source_capsule_path = lock_path.parent / "source-files" / CAPSULE_LOCK_FILENAME
            if source_capsule_path.exists():
                try:
                    source_data = json.loads(source_capsule_path.read_text(encoding="utf-8"))
                    source_package_path = lock_path.parent / "source-files" / "package.json"
                    source_package = (
                        json.loads(source_package_path.read_text(encoding="utf-8"))
                        if source_package_path.exists()
                        else {}
                    )
                except (OSError, json.JSONDecodeError):
                    source_data = {}
                    source_package = {}
                repaired_nodes = [
                    node
                    for node in normalize_custom_nodes(source_data, source_package)
                    if node.included
                ]
                if repaired_nodes:
                    trust = data.get("trust") if isinstance(data.get("trust"), dict) else {}
                    trust_level = trust.get("level") or data.get("workflow", {}).get("trust_level")
                    data["custom_nodes"] = [
                        {
                            "package_id": node.id,
                            "source": node.source,
                            "source_ref": node.source_ref,
                            "source_content_hash": node.source_content_hash,
                            "source_cache_ref": node.source_cache_ref,
                            "source_archive_subdir": node.source_archive_subdir,
                            "source_repo_url": node.source_repo_url,
                            "trust_level": trust_level,
                            "node_types": node.node_types,
                        }
                        for node in repaired_nodes
                    ]
        return CapsuleLock.model_validate(data)
