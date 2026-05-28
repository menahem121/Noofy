from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.engine.factory import comfyui_adapter_factory
from app.engine.models import ModelInfo
from app.runtime.runners.runner_coordinator import RunnerProcessCoordinator
from app.runtime.runners.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.runners.supervisor import (
    RunnerDescriptor,
    RunnerKind,
    RunnerStatus,
    RunnerSupervisor,
)


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 9100
        self.returncode = None
        self.stdout = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


class RecordingAdapter:
    def __init__(self, base_url: str, ws_url: str | None) -> None:
        self.base_url = base_url
        self.ws_url = ws_url

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        self.base_url = base_url
        self.ws_url = ws_url

    async def list_available_models(self):
        return [ModelInfo(folder="checkpoints", filename="model.safetensors")]


def _spec(tmp_path: Path) -> RunnerLaunchSpec:
    return RunnerLaunchSpec(
        runner_id="isolated-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        fingerprint="sha256:" + ("a" * 64),
        python_executable="/opt/noofy/python",
        working_dir=tmp_path,
        port=9001,
    )


@pytest.mark.anyio
async def test_coordinator_registers_started_runner_endpoint_and_adapter(
    tmp_path: Path,
) -> None:
    async def process_factory(command: list[str], **kwargs):
        return FakeProcess()

    async def healthy(base_url: str):
        return True, None

    runner_supervisor = RunnerSupervisor()
    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
    coordinator = RunnerProcessCoordinator(
        runner_supervisor=runner_supervisor,
        process_supervisor=process_supervisor,
        adapter_factory=lambda descriptor: RecordingAdapter(
            descriptor.base_url, descriptor.ws_url
        ),
        log_store=LogStore(),
    )

    handle = await coordinator.start_runner(
        _spec(tmp_path), workflow_id="text_to_image_v0"
    )

    registered = runner_supervisor.get_runner("isolated-1")
    adapter = runner_supervisor.get_adapter("isolated-1")
    assert registered == handle.descriptor
    assert registered.status is RunnerStatus.READY
    assert adapter.base_url == "http://127.0.0.1:9001"
    assert adapter.ws_url == "ws://127.0.0.1:9001/ws"
    assert (
        runner_supervisor.runner_for_workflow("text_to_image_v0").runner_id
        == "isolated-1"
    )


@pytest.mark.anyio
async def test_coordinator_refresh_and_stop_update_registry_status(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    healthy = {"value": True}

    async def process_factory(command: list[str], **kwargs):
        return process

    async def health_check(base_url: str):
        if healthy["value"]:
            return True, None
        return False, "not reachable"

    runner_supervisor = RunnerSupervisor()
    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=health_check,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
    coordinator = RunnerProcessCoordinator(
        runner_supervisor=runner_supervisor,
        process_supervisor=process_supervisor,
        adapter_factory=lambda descriptor: RecordingAdapter(
            descriptor.base_url, descriptor.ws_url
        ),
        log_store=LogStore(),
    )
    await coordinator.start_runner(_spec(tmp_path))

    healthy["value"] = False
    unreachable = await coordinator.refresh_runner_status("isolated-1")
    stopped = await coordinator.stop_runner("isolated-1")

    assert unreachable.status is RunnerStatus.UNREACHABLE
    assert stopped.status is RunnerStatus.STOPPED
    assert runner_supervisor.get_runner("isolated-1").status is RunnerStatus.STOPPED


@pytest.mark.anyio
async def test_coordinator_stop_all_updates_status_and_unbinds_workflows(
    tmp_path: Path,
) -> None:
    async def process_factory(command: list[str], **kwargs):
        return FakeProcess()

    async def healthy(base_url: str):
        return True, None

    runner_supervisor = RunnerSupervisor()
    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
    coordinator = RunnerProcessCoordinator(
        runner_supervisor=runner_supervisor,
        process_supervisor=process_supervisor,
        adapter_factory=lambda descriptor: RecordingAdapter(
            descriptor.base_url, descriptor.ws_url
        ),
        log_store=LogStore(),
    )
    await coordinator.start_runner(_spec(tmp_path), workflow_id="text_to_image_v0")

    statuses = await coordinator.stop_all_runners()

    assert [status.runner_id for status in statuses] == ["isolated-1"]
    assert runner_supervisor.get_runner("isolated-1").status is RunnerStatus.STOPPED
    assert runner_supervisor.runner_for_workflow("text_to_image_v0") is None


def test_comfyui_adapter_factory_configures_endpoint(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    descriptor = RunnerDescriptor(
        runner_id="isolated-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=RunnerStatus.READY,
        runner_workspace_path=str(workspace),
    )
    factory = comfyui_adapter_factory(
        models_dir=tmp_path / "models",
        dashboard_assets_dir=tmp_path / "assets",
        log_store=LogStore(),
    )

    adapter = factory(descriptor)

    assert adapter.base_url == "http://127.0.0.1:9001"
    assert adapter.ws_url == "ws://127.0.0.1:9001/ws"
    assert adapter.dashboard_assets_dir == tmp_path / "assets"
    assert adapter.comfyui_input_dir == workspace / "input"
