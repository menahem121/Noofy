from __future__ import annotations

import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.paths import NoofyPaths
from app.diagnostics import DiagnosticsSink


AsyncCleanupStep = Callable[[], Awaitable[Any]]


@dataclass(frozen=True)
class DeletedPath:
    path: str
    bytes_deleted: int


class LocalEngineFilesService:
    """Removes app-owned runtime files that Noofy can regenerate."""

    def __init__(
        self,
        *,
        paths: NoofyPaths,
        stop_managed_runtime: AsyncCleanupStep | None = None,
        stop_workflow_runners: AsyncCleanupStep | None = None,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.paths = paths
        self.stop_managed_runtime = stop_managed_runtime
        self.stop_workflow_runners = stop_workflow_runners
        self.log_store = log_store

    async def remove(self) -> dict[str, Any]:
        if self.stop_workflow_runners is not None:
            await self.stop_workflow_runners()
        if self.stop_managed_runtime is not None:
            await self.stop_managed_runtime()

        deleted: list[DeletedPath] = []
        skipped: list[str] = []
        for path in self._cleanup_targets():
            if not path.exists():
                skipped.append(str(path))
                continue
            if not self._safe_delete_target(path):
                raise RuntimeError(f"Refusing to remove unsafe engine path: {path}")
            bytes_deleted = _directory_size(path)
            _remove_path(path)
            deleted.append(DeletedPath(path=str(path), bytes_deleted=bytes_deleted))

        total_bytes_deleted = sum(item.bytes_deleted for item in deleted)
        self._log(
            "info",
            "Removed local engine runtime files",
            details={
                "bytes_deleted": total_bytes_deleted,
                "deleted_paths": [item.path for item in deleted],
            },
        )
        return {
            "status": "removed",
            "bytes_deleted": total_bytes_deleted,
            "deleted_paths": [item.__dict__ for item in deleted],
            "skipped_paths": skipped,
            "preserved_paths": {
                "models": str(self.paths.models_dir),
                "outputs": str(self.paths.outputs_dir),
                "workflows": str(self.paths.workflow_store_dir),
            },
        }

    def _cleanup_targets(self) -> list[Path]:
        return _unique_paths(
            [
                self.paths.runtime_dir,
                self.paths.runtime_store_dir,
                self.paths.python_cache_dir,
                self.paths.custom_node_cache_dir,
                self.paths.wheel_cache_dir,
                self.paths.comfyui_custom_nodes_dir,
                self.paths.comfyui_user_dir,
                self.paths.input_dir,
                self.paths.temp_dir,
            ]
        )

    def _safe_delete_target(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        data_dir = self.paths.data_dir.resolve(strict=False)
        runtime_dir = self.paths.runtime_dir.resolve(strict=False)
        if resolved in {Path("/"), Path.home().resolve(strict=False)}:
            return False
        if resolved == runtime_dir:
            return True
        return _is_relative_to(resolved, data_dir)

    def _log(
        self,
        level: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        if self.log_store is not None:
            self.log_store.add(level, message, "settings.local_engine", details=details)


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        normalized = path.resolve(strict=False)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(path)
    return result


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _directory_size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for directory in list(dirs):
            directory_path = Path(root) / directory
            if directory_path.is_symlink():
                dirs.remove(directory)
                try:
                    total += directory_path.lstat().st_size
                except OSError:
                    pass
        for filename in files:
            file_path = Path(root) / filename
            try:
                total += file_path.lstat().st_size
            except OSError:
                pass
    return total


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)
