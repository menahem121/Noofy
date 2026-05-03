from pathlib import Path
import os
import subprocess

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.supervisor import RunnerKind, RunnerMemoryClass, RunnerStatus


class FakeProcess:
    def __init__(self, *, pid: int = 1001, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.stdout = None
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _launch_spec(tmp_path: Path, *, port: int | None = 8188) -> RunnerLaunchSpec:
    return RunnerLaunchSpec(
        runner_id="runner-1",
        kind=RunnerKind.CORE_COMFYUI,
        fingerprint="sha256:" + ("a" * 64),
        python_executable="/opt/noofy/python",
        working_dir=tmp_path,
        host="127.0.0.1",
        port=port,
    )


@pytest.mark.anyio
async def test_start_returns_ready_handle_and_descriptor(tmp_path: Path) -> None:
    created: list[tuple[list[str], dict]] = []
    process = FakeProcess(pid=1234)

    async def process_factory(command: list[str], **kwargs):
        created.append((command, kwargs))
        return process

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    log_store = LogStore()
    supervisor = RunnerProcessSupervisor(
        log_store=log_store,
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )

    handle = await supervisor.start(_launch_spec(tmp_path))

    assert handle.pid == 1234
    assert handle.descriptor.status is RunnerStatus.READY
    assert handle.descriptor.pid == 1234
    assert handle.descriptor.base_url == "http://127.0.0.1:8188"
    assert handle.descriptor.ws_url == "ws://127.0.0.1:8188/ws"
    assert created[0][0] == [
        "/opt/noofy/python",
        "main.py",
        "--listen",
        "127.0.0.1",
        "--port",
        "8188",
    ]
    assert created[0][1]["cwd"] == tmp_path
    if os.name == "nt":
        assert created[0][1]["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert created[0][1]["start_new_session"] is True
    assert any(event.message == "Runner process ready" for event in log_store.list_events().events)


@pytest.mark.anyio
async def test_runner_pid_file_written_and_removed(tmp_path: Path) -> None:
    async def process_factory(command: list[str], **kwargs):
        return FakeProcess(pid=1234)

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        pid_dir=tmp_path / "runner-pids",
    )

    await supervisor.start(_launch_spec(tmp_path))
    pid_file = tmp_path / "runner-pids" / "runner-runner-1.pid"

    assert pid_file.read_text(encoding="utf-8") == "1234"

    await supervisor.stop("runner-1")

    assert not pid_file.exists()


def test_runner_startup_sweep_removes_stale_pid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[int] = []
    pid_dir = tmp_path / "runner-pids"
    pid_dir.mkdir()
    (pid_dir / "runner-old.pid").write_text("4321", encoding="utf-8")
    monkeypatch.setattr("app.runtime.runner_process._is_pid_alive", lambda pid: True)
    monkeypatch.setattr("app.runtime.runner_process._terminate_stale_pid", lambda pid: killed.append(pid))
    supervisor = RunnerProcessSupervisor(pid_dir=pid_dir)

    cleaned = supervisor.cleanup_stale_pid_files()

    assert cleaned == 1
    assert killed == [4321]
    assert not (pid_dir / "runner-old.pid").exists()


@pytest.mark.anyio
async def test_start_copies_launch_metadata_to_descriptor(tmp_path: Path) -> None:
    async def process_factory(command: list[str], **kwargs):
        return FakeProcess(pid=1234)

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )
    spec = _launch_spec(tmp_path).model_copy(
        update={
            "runner_workspace_fingerprint": "runner-fp",
            "dependency_env_fingerprint": "dep-fp",
            "runner_process_compatibility_key": "compat-key",
            "model_view_fingerprint": "model-view-fp",
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "linux-x64-cuda130",
            "memory_class": RunnerMemoryClass.GPU_HEAVY,
        }
    )

    handle = await supervisor.start(spec)

    assert handle.descriptor.runner_workspace_fingerprint == "runner-fp"
    assert handle.descriptor.dependency_env_fingerprint == "dep-fp"
    assert handle.descriptor.runner_process_compatibility_key == "compat-key"
    assert handle.descriptor.model_view_fingerprint == "model-view-fp"
    assert handle.descriptor.runtime_profile_id == "noofy-comfyui-v1-default"
    assert handle.descriptor.runtime_profile_variant_id == "linux-x64-cuda130"
    assert handle.descriptor.memory_class is RunnerMemoryClass.GPU_HEAVY


@pytest.mark.anyio
async def test_start_merges_runner_environment_with_parent_environment(tmp_path: Path, monkeypatch) -> None:
    created: list[tuple[list[str], dict]] = []
    monkeypatch.setenv("NOOFY_PARENT_ENV", "kept")

    async def process_factory(command: list[str], **kwargs):
        created.append((command, kwargs))
        return FakeProcess()

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )
    spec = _launch_spec(tmp_path).model_copy(update={"env": {"NOOFY_WORKFLOW_ID": "text_to_image_v0"}})

    await supervisor.start(spec)

    process_env = created[0][1]["env"]
    assert process_env["NOOFY_PARENT_ENV"] == "kept"
    assert process_env["NOOFY_WORKFLOW_ID"] == "text_to_image_v0"


@pytest.mark.anyio
async def test_status_marks_running_unhealthy_process_unreachable(tmp_path: Path) -> None:
    async def process_factory(command: list[str], **kwargs):
        return FakeProcess()

    healthy = {"value": True}

    async def health_check(base_url: str) -> tuple[bool, str | None]:
        if healthy["value"]:
            return True, None
        return False, "health failed"

    supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=health_check,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )
    await supervisor.start(_launch_spec(tmp_path))
    healthy["value"] = False

    status = await supervisor.status("runner-1")

    assert status.status is RunnerStatus.UNREACHABLE
    assert status.error == "health failed"


@pytest.mark.anyio
async def test_stop_is_idempotent_and_terminates_running_process(tmp_path: Path) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )
    await supervisor.start(_launch_spec(tmp_path))

    first = await supervisor.stop("runner-1")
    second = await supervisor.stop("runner-1")

    assert first.status is RunnerStatus.STOPPED
    assert second.status is RunnerStatus.STOPPED
    assert process.terminated


@pytest.mark.anyio
async def test_stop_uses_process_tree_terminator_when_configured(tmp_path: Path) -> None:
    process = FakeProcess()
    terminated: list[int] = []

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    async def terminate_tree(running_process) -> None:
        terminated.append(running_process.pid)
        running_process.returncode = 0

    supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        process_tree_terminator=terminate_tree,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )
    await supervisor.start(_launch_spec(tmp_path))

    status = await supervisor.stop("runner-1")

    assert status.status is RunnerStatus.STOPPED
    assert terminated == [1001]
    assert not process.terminated
    assert not process.killed


@pytest.mark.anyio
async def test_stop_all_terminates_all_tracked_processes(tmp_path: Path) -> None:
    processes = [FakeProcess(pid=2001), FakeProcess(pid=2002)]

    async def process_factory(command: list[str], **kwargs):
        return processes.pop(0)

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )
    await supervisor.start(_launch_spec(tmp_path).model_copy(update={"runner_id": "runner-1", "port": 8191}))
    await supervisor.start(_launch_spec(tmp_path).model_copy(update={"runner_id": "runner-2", "port": 8192}))

    statuses = await supervisor.stop_all()

    assert [status.runner_id for status in statuses] == ["runner-1", "runner-2"]
    assert all(status.status is RunnerStatus.STOPPED for status in statuses)
    assert (await supervisor.stop("runner-1")).status is RunnerStatus.STOPPED
    assert (await supervisor.stop("runner-2")).status is RunnerStatus.STOPPED


@pytest.mark.anyio
async def test_startup_timeout_stops_process_and_emits_diagnostic(tmp_path: Path) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def unhealthy(base_url: str) -> tuple[bool, str | None]:
        return False, "not yet"

    log_store = LogStore()
    supervisor = RunnerProcessSupervisor(
        log_store=log_store,
        process_factory=process_factory,
        health_check=unhealthy,
        startup_timeout_seconds=0.01,
        health_poll_interval_seconds=0.001,
    )

    with pytest.raises(RuntimeError, match="timed out"):
        await supervisor.start(_launch_spec(tmp_path))

    assert process.terminated
    assert any(
        event.level == "error" and event.message == "Runner process startup failed"
        for event in log_store.list_events().events
    )


@pytest.mark.anyio
async def test_spawn_failure_is_reported_with_diagnostics(tmp_path: Path) -> None:
    async def process_factory(command: list[str], **kwargs):
        raise RuntimeError("spawn denied")

    async def healthy(base_url: str) -> tuple[bool, str | None]:
        return True, None

    log_store = LogStore()
    supervisor = RunnerProcessSupervisor(
        log_store=log_store,
        process_factory=process_factory,
        health_check=healthy,
    )

    with pytest.raises(RuntimeError, match="spawn denied"):
        await supervisor.start(_launch_spec(tmp_path))

    latest_error = log_store.latest_error()
    assert latest_error is not None
    assert latest_error.message == "Runner process spawn failed"
