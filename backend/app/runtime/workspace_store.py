"""Manifest persistence for Phase 4 runtime artifacts.

Dependency environments and runner workspaces become immutable once they are
ready. This store writes and reads manifest files in their fingerprint-derived
locations without creating or launching the actual environments.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from app.runtime.isolation import (
    DependencyEnvManifest,
    InstallStatus,
    RunnerWorkspaceManifest,
)

ManifestT = TypeVar("ManifestT", DependencyEnvManifest, RunnerWorkspaceManifest)


class ManifestStoreError(RuntimeError):
    """Raised when a runtime manifest cannot be safely persisted."""


class RuntimeManifestStore(Generic[ManifestT]):
    def __init__(
        self,
        *,
        root_dir: Path,
        directory_prefix: str,
        manifest_type: type[ManifestT],
    ) -> None:
        self.root_dir = root_dir
        self.directory_prefix = directory_prefix
        self.manifest_type = manifest_type

    def artifact_dir(self, fingerprint: str) -> Path:
        return self.root_dir / f"{self.directory_prefix}-{_safe_fingerprint(fingerprint)}"

    def manifest_path(self, fingerprint: str) -> Path:
        return self.artifact_dir(fingerprint) / "manifest.json"

    def exists(self, fingerprint: str) -> bool:
        return self.manifest_path(fingerprint).exists()

    def read(self, fingerprint: str) -> ManifestT:
        path = self.manifest_path(fingerprint)
        with path.open("r", encoding="utf-8") as file:
            return self.manifest_type.model_validate(json.load(file))

    def write_new(self, manifest: ManifestT) -> Path:
        path = self.manifest_path(manifest.fingerprint)
        if path.exists():
            raise ManifestStoreError(f"Manifest already exists: {manifest.fingerprint}")
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, manifest)
        return path

    def save_staged(self, manifest: ManifestT) -> Path:
        """Write or replace a non-ready manifest during staging."""
        path = self.manifest_path(manifest.fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = self.read(manifest.fingerprint) if path.exists() else None
        if existing is not None and existing.status is InstallStatus.READY:
            raise ManifestStoreError(f"Ready manifest is immutable: {manifest.fingerprint}")
        _atomic_write_json(path, manifest)
        return path


class DependencyEnvManifestStore(RuntimeManifestStore[DependencyEnvManifest]):
    def __init__(self, root_dir: Path) -> None:
        super().__init__(
            root_dir=root_dir,
            directory_prefix="dep-env",
            manifest_type=DependencyEnvManifest,
        )


class RunnerWorkspaceManifestStore(RuntimeManifestStore[RunnerWorkspaceManifest]):
    def __init__(self, root_dir: Path) -> None:
        super().__init__(
            root_dir=root_dir,
            directory_prefix="runner-workspace",
            manifest_type=RunnerWorkspaceManifest,
        )


def _atomic_write_json(path: Path, manifest: BaseModel) -> None:
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _safe_fingerprint(fingerprint: str) -> str:
    return fingerprint.replace("sha256:", "").replace("/", "_").replace("\\", "_").replace(":", "_")
