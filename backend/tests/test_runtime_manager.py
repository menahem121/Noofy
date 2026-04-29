import socket
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.environment import RuntimeEnvironment
from app.runtime.manager import RuntimeManager, select_free_port


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 4321
        self.returncode: int | None = None
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


def test_select_free_port_returns_bindable_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        occupied_port = occupied.getsockname()[1]

        selected_port = select_free_port("127.0.0.1")

    assert selected_port != occupied_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", selected_port))


def test_managed_runtime_selects_free_port_when_unconfigured(tmp_path: Path) -> None:
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=tmp_path,
        python_executable="python3",
    )

    parsed = urlparse(manager.base_url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None


@pytest.mark.anyio
async def test_managed_start_reports_missing_comfyui_repo(tmp_path: Path) -> None:
    async def unreachable(_: str) -> tuple[bool, str | None]:
        return False, "not reachable"

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("process should not start when repo is missing")

    log_store = LogStore()
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=tmp_path / "missing",
        python_executable="python3",
        log_store=log_store,
        process_factory=fail_if_called,
        health_check=unreachable,
    )

    result = await manager.start()

    assert result.status == "repo_missing"
    assert "ComfyUI repo not found" in (result.comfyui.error or "")
    assert log_store.latest_error() is not None
    assert log_store.latest_error().message == "ComfyUI runtime cannot start"


@pytest.mark.anyio
async def test_managed_start_checks_environment_before_process_start(tmp_path: Path) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    (repo_dir / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")

    async def unreachable(_: str) -> tuple[bool, str | None]:
        return False, "not reachable"

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("process should not start when environment is not ready")

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=str(tmp_path / "missing-python"),
    )
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        environment=environment,
        process_factory=fail_if_called,
        health_check=unreachable,
    )

    result = await manager.start()

    assert result.status == "environment_not_ready"
    assert result.comfyui.environment is not None
    assert not result.comfyui.environment.prepared


@pytest.mark.anyio
async def test_managed_startup_timeout_stops_process(tmp_path: Path) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    fake_process = FakeProcess()

    async def create_process(*args, **kwargs):
        return fake_process

    async def unreachable(_: str) -> tuple[bool, str | None]:
        return False, "not reachable"

    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        startup_timeout_seconds=0.01,
        health_poll_interval_seconds=0.001,
        process_factory=create_process,
        health_check=unreachable,
    )

    result = await manager.start()

    assert result.status == "startup_timeout"
    assert fake_process.terminated
    assert not result.comfyui.managed_process_running
    assert "timed out" in (result.comfyui.error or "")


@pytest.mark.anyio
async def test_external_start_reports_already_running(tmp_path: Path) -> None:
    process_called = False

    async def reachable(_: str) -> tuple[bool, str | None]:
        return True, None

    async def create_process(*args, **kwargs):
        nonlocal process_called
        process_called = True
        return FakeProcess()

    manager = RuntimeManager(
        mode="external",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=tmp_path,
        python_executable="python3",
        process_factory=create_process,
        health_check=reachable,
    )

    result = await manager.start()

    assert result.status == "already_running"
    assert result.comfyui.mode == "external"
    assert result.comfyui.reachable
    assert not process_called


@pytest.mark.anyio
async def test_managed_stop_terminates_process(tmp_path: Path) -> None:
    async def unreachable(_: str) -> tuple[bool, str | None]:
        return False, "not reachable"

    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=tmp_path,
        python_executable="python3",
        health_check=unreachable,
    )
    fake_process = FakeProcess()
    manager._process = fake_process

    result = await manager.stop()

    assert result.status == "stopped"
    assert fake_process.terminated
    assert manager._process is None
