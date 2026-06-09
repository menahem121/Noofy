from __future__ import annotations

from pathlib import Path

from app.diagnostics import DiagnosticsSink
from app.runtime.comfyui.comfyui_update_records import LocalComfyUIVersionRecord
from app.runtime.profiles import ActiveRuntimeProfileSnapshot, ActiveRuntimeProfileState
from app.runtime.runners.runner_coordinator import RunnerProcessCoordinator
from app.runtime.runners.supervisor import (
    QueuedRunnerStartStatus,
    RunnerStatus,
    RunnerSupervisor,
)


class ComfyUIActivationError(RuntimeError):
    """Raised when a validated runtime cannot safely become active yet."""


class WorkflowRuntimeActivationCoordinator:
    """Coordinates a managed ComfyUI switch with isolated workflow runners."""

    def __init__(
        self,
        *,
        runtime_profile_state: ActiveRuntimeProfileState,
        runner_supervisor: RunnerSupervisor,
        runner_process_coordinator: RunnerProcessCoordinator,
        log_store: DiagnosticsSink,
    ) -> None:
        self.runtime_profile_state = runtime_profile_state
        self.runner_supervisor = runner_supervisor
        self.runner_process_coordinator = runner_process_coordinator
        self.log_store = log_store
        self._pending: dict[str, ActiveRuntimeProfileSnapshot] = {}

    async def prepare(self, record: LocalComfyUIVersionRecord) -> None:
        if not record.source_hash or not record.source_path:
            raise ComfyUIActivationError(
                "Validated ComfyUI runtime is missing source identity metadata."
            )
        snapshot = self.runtime_profile_state.prepare_local_activation(
            comfyui_core_version=record.tag,
            comfyui_core_source_hash=record.source_hash,
            source_reference=record.archive_url or record.tag,
            source_dir=Path(record.source_path),
        )
        busy_runners = self.runner_supervisor.begin_runtime_activation()
        if busy_runners:
            raise ComfyUIActivationError(
                "Cannot activate ComfyUI while workflow runtime work is active: "
                + ", ".join(sorted(busy_runners))
            )
        activation_key = self._activation_key(record)
        try:
            canceled_queue_ids: list[str] = []
            for queued in self.runner_supervisor.list_queued_runner_starts(status=None):
                if queued.status in {
                    QueuedRunnerStartStatus.QUEUED,
                    QueuedRunnerStartStatus.HANDING_OFF,
                    QueuedRunnerStartStatus.REQUEUED,
                }:
                    self.runner_supervisor.cancel_queued_runner_start(queued.queue_id)
                    canceled_queue_ids.append(queued.queue_id)

            stopped = await self.runner_process_coordinator.stop_all_runners()
            failed_stops = [
                status.runner_id
                for status in stopped
                if status.status is not RunnerStatus.STOPPED
            ]
            if failed_stops:
                raise ComfyUIActivationError(
                    "Could not stop workflow runners before ComfyUI activation: "
                    + ", ".join(sorted(failed_stops))
                )
            self._pending[activation_key] = snapshot
            self.log_store.add(
                "info",
                "Prepared isolated workflow runners for ComfyUI activation",
                "runtime.comfyui_update",
                details={
                    "tag": record.tag,
                    "stopped_runner_ids": [status.runner_id for status in stopped],
                    "canceled_runner_queue_ids": canceled_queue_ids,
                    "runtime_profile_manifest_hash": (
                        snapshot.catalog.profiles[0].runtime_profile_manifest_hash
                    ),
                },
            )
        except Exception:
            self._pending.pop(activation_key, None)
            self.runner_supervisor.end_runtime_activation()
            raise

    def commit(self, record: LocalComfyUIVersionRecord) -> None:
        snapshot = self._pending.pop(self._activation_key(record), None)
        if snapshot is None:
            raise ComfyUIActivationError(
                "ComfyUI activation was not prepared for isolated workflow runners."
            )
        try:
            self.runtime_profile_state.activate(snapshot)
        finally:
            self.runner_supervisor.end_runtime_activation()
        self.log_store.add(
            "info",
            "Activated ComfyUI runtime profile for isolated workflow runners",
            "runtime.comfyui_update",
            details={
                "tag": record.tag,
                "source_hash": record.source_hash,
                "runtime_profile_manifest_hash": (
                    snapshot.catalog.profiles[0].runtime_profile_manifest_hash
                ),
            },
        )

    def abort(self, record: LocalComfyUIVersionRecord) -> None:
        self._pending.pop(self._activation_key(record), None)
        self.runner_supervisor.end_runtime_activation()
        self.log_store.add(
            "warning",
            "Aborted prepared ComfyUI activation for isolated workflow runners",
            "runtime.comfyui_update",
            details={"tag": record.tag, "source_hash": record.source_hash},
        )

    @staticmethod
    def _activation_key(record: LocalComfyUIVersionRecord) -> str:
        return f"{record.tag}:{record.source_hash or ''}"
