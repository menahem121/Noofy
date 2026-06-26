"""Backend-owned automatic runtime storage maintenance."""

from __future__ import annotations

import asyncio
import shutil
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.isolation import InstallState
from app.runtime.install_state import InstallStateStore
from app.runtime.runners.supervisor import RunnerDescriptor
from app.runtime.storage.storage_gc import (
    ModelReferenceValidator,
    RuntimeStorageGarbageCollector,
    RuntimeStorageGcConfig,
    RuntimeStorageGcResult,
    RuntimeStorageRoots,
)

DEFAULT_RUNTIME_STORAGE_IDLE_INTERVAL_SECONDS = 30 * 60


@dataclass(frozen=True)
class RuntimeStorageMaintenanceResult:
    reason: str
    aggressive: bool
    disk_free_bytes: int | None
    disk_total_bytes: int | None
    gc_result: RuntimeStorageGcResult


class RuntimeStorageMaintenanceService:
    def __init__(
        self,
        *,
        roots: RuntimeStorageRoots,
        install_state_store: InstallStateStore,
        runner_descriptors: Callable[[], Iterable[RunnerDescriptor]],
        log_store: DiagnosticsSink,
        model_reference_validator: ModelReferenceValidator | None = None,
        config: RuntimeStorageGcConfig | None = None,
        disk_usage: Callable[[Path], shutil._ntuple_diskusage] = shutil.disk_usage,
    ) -> None:
        self.roots = roots
        self.install_state_store = install_state_store
        self.runner_descriptors = runner_descriptors
        self.log_store = log_store
        self.model_reference_validator = model_reference_validator
        self.config = config or RuntimeStorageGcConfig()
        self._disk_usage = disk_usage
        self._lock = threading.Lock()
        self._idle_task: asyncio.Task[None] | None = None

    def run(
        self,
        *,
        reason: str,
        workflow_id: str | None = None,
        force_aggressive: bool = False,
    ) -> RuntimeStorageMaintenanceResult | None:
        if not self._lock.acquire(blocking=False):
            self.log_store.add(
                "info",
                "Runtime storage maintenance already running",
                "runtime.storage_maintenance",
                workflow_id=workflow_id,
                details={"reason": reason},
            )
            return None
        try:
            disk = self._runtime_disk_usage()
            aggressive = force_aggressive or self._low_disk(disk)
            states = self._install_states()
            runners = list(self.runner_descriptors())
            collector = RuntimeStorageGarbageCollector(
                roots=self.roots,
                install_states=states,
                runner_descriptors=runners,
                log_store=self.log_store,
                config=self.config,
                model_reference_validator=self.model_reference_validator,
            )
            result = collector.collect_garbage(aggressive=aggressive)
            self.log_store.add(
                "info",
                "Runtime storage maintenance completed",
                "runtime.storage_maintenance",
                workflow_id=workflow_id,
                details={
                    "reason": reason,
                    "aggressive": aggressive,
                    "disk_free_bytes": disk.free_bytes,
                    "disk_total_bytes": disk.total_bytes,
                    "decision_count": len(result.decisions),
                    "bytes_deleted": result.bytes_deleted,
                },
            )
            return RuntimeStorageMaintenanceResult(
                reason=reason,
                aggressive=aggressive,
                disk_free_bytes=disk.free_bytes,
                disk_total_bytes=disk.total_bytes,
                gc_result=result,
            )
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Runtime storage maintenance failed",
                "runtime.storage_maintenance",
                workflow_id=workflow_id,
                details={
                    "reason": reason,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return None
        finally:
            self._lock.release()

    def start_idle_maintenance(
        self,
        *,
        interval_seconds: float = DEFAULT_RUNTIME_STORAGE_IDLE_INTERVAL_SECONDS,
    ) -> None:
        if self._idle_task is not None and not self._idle_task.done():
            return
        self._idle_task = asyncio.create_task(self._idle_loop(interval_seconds))

    async def shutdown(self) -> None:
        task = self._idle_task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _idle_loop(self, interval_seconds: float) -> None:
        while True:
            await asyncio.sleep(interval_seconds)
            await asyncio.to_thread(self.run, reason="idle_maintenance")

    def _install_states(self) -> list[InstallState]:
        try:
            return self.install_state_store.list_states()
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Runtime storage maintenance could not read install states",
                "runtime.storage_maintenance",
                details={"error": str(exc), "error_type": type(exc).__name__},
            )
            return []

    def _runtime_disk_usage(self) -> "_DiskUsage":
        probe = self.roots.model_materialized_dir
        while not probe.exists():
            if probe.parent == probe:
                return _DiskUsage(free_bytes=None, total_bytes=None)
            probe = probe.parent
        try:
            usage = self._disk_usage(probe)
        except OSError:
            return _DiskUsage(free_bytes=None, total_bytes=None)
        return _DiskUsage(free_bytes=usage.free, total_bytes=usage.total)

    def _low_disk(self, disk: "_DiskUsage") -> bool:
        if disk.free_bytes is None or disk.total_bytes in {None, 0}:
            return False
        free_ratio = disk.free_bytes / disk.total_bytes
        return (
            disk.free_bytes <= self.config.low_disk_free_bytes
            or free_ratio <= self.config.low_disk_free_ratio
        )


@dataclass(frozen=True)
class _DiskUsage:
    free_bytes: int | None
    total_bytes: int | None
