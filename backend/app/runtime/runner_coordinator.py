"""Bridge runner processes into the app runner registry.

`RunnerProcessSupervisor` owns subprocess lifecycle. `RunnerSupervisor` owns
engine routing. This coordinator wires the two together after a process has
started and produced an endpoint descriptor.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.engine.adapter import EngineAdapter
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.engine.diagnostics import LogStore
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessHandle, RunnerProcessSupervisor, RunnerProcessStatus
from app.runtime.supervisor import RunnerDescriptor, RunnerNotFoundError, RunnerStatus, RunnerSupervisor

AdapterFactory = Callable[[RunnerDescriptor], EngineAdapter]


class RunnerProcessCoordinator:
    def __init__(
        self,
        *,
        runner_supervisor: RunnerSupervisor,
        process_supervisor: RunnerProcessSupervisor,
        adapter_factory: AdapterFactory,
        log_store: LogStore | None = None,
    ) -> None:
        self.runner_supervisor = runner_supervisor
        self.process_supervisor = process_supervisor
        self.adapter_factory = adapter_factory
        self.log_store = log_store or LogStore()

    async def start_runner(
        self,
        spec: RunnerLaunchSpec,
        *,
        workflow_id: str | None = None,
    ) -> RunnerProcessHandle:
        handle = await self.process_supervisor.start(spec)
        adapter = self.adapter_factory(handle.descriptor)
        self.runner_supervisor.upsert_runner(handle.descriptor, adapter)
        if workflow_id is not None:
            self.runner_supervisor.bind_workflow_runner(workflow_id, handle.runner_id)
        self.log_store.add(
            "info",
            "Runner endpoint registered",
            "runtime.runner_coordinator",
            details={
                "runner_id": handle.runner_id,
                "base_url": handle.descriptor.base_url,
                "fingerprint": handle.descriptor.fingerprint,
                "workflow_id": workflow_id,
            },
        )
        return handle

    async def refresh_runner_status(self, runner_id: str) -> RunnerProcessStatus:
        status = await self.process_supervisor.status(runner_id)
        self._update_registry_status(runner_id, status.status)
        return status

    async def stop_runner(self, runner_id: str) -> RunnerProcessStatus:
        status = await self.process_supervisor.stop(runner_id)
        self._update_registry_status(runner_id, status.status)
        if status.status is RunnerStatus.STOPPED:
            self.runner_supervisor.unbind_runner(runner_id)
        return status

    async def stop_all_runners(self) -> list[RunnerProcessStatus]:
        statuses = await self.process_supervisor.stop_all()
        for status in statuses:
            self._update_registry_status(status.runner_id, status.status)
            if status.status is RunnerStatus.STOPPED:
                self.runner_supervisor.unbind_runner(status.runner_id)
        if statuses:
            self.log_store.add(
                "info",
                "Stopped backend-owned runner processes",
                "runtime.runner_coordinator",
                details={
                    "runner_ids": [status.runner_id for status in statuses],
                    "count": len(statuses),
                },
            )
        return statuses

    def _update_registry_status(self, runner_id: str, status: RunnerStatus) -> None:
        try:
            self.runner_supervisor.update_runner_status(runner_id, status)
        except RunnerNotFoundError:
            return


def comfyui_adapter_factory(
    *,
    models_dir: Path,
    log_store: LogStore | None = None,
) -> AdapterFactory:
    def factory(descriptor: RunnerDescriptor) -> EngineAdapter:
        return ComfyUIEngineAdapter(
            descriptor.base_url,
            models_dir,
            descriptor.ws_url,
            log_store=log_store,
        )

    return factory
