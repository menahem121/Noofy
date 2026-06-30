import asyncio
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.diagnostics import LogStore
from app.engine.models import RuntimeHardwareProfile
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
        log_store=LogStore(),
    )

    parsed = urlparse(manager.base_url)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port is not None


@pytest.mark.anyio
async def test_managed_process_pid_reports_only_running_process(tmp_path: Path) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    fake_process = FakeProcess()
    process_started = False

    async def create_process(*args, **kwargs):
        nonlocal process_started
        process_started = True
        return fake_process

    async def health_check(_: str) -> tuple[bool, str | None]:
        return process_started, None if process_started else "not reachable"

    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        process_factory=create_process,
        health_check=health_check,
        log_store=LogStore(),
    )

    assert manager.managed_process_pid() is None

    result = await manager.start()

    assert result.status == "started"
    assert manager.managed_process_pid() == 4321

    fake_process.returncode = 0

    assert manager.managed_process_pid() is None


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
    assert "bundled ComfyUI engine files" in (result.comfyui.error or "")
    assert log_store.latest_error() is not None
    assert log_store.latest_error().message == "ComfyUI runtime cannot start"


@pytest.mark.anyio
async def test_managed_start_checks_environment_before_process_start(
    tmp_path: Path,
) -> None:
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
        hardware_profile=RuntimeHardwareProfile(
            os_name="Linux",
            os_version="",
            machine="x86_64",
            architecture="x86_64",
            accelerator="cpu",
        ),
        log_store=LogStore(),
    )
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        environment=environment,
        process_factory=fail_if_called,
        health_check=unreachable,
        log_store=LogStore(),
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
        log_store=LogStore(),
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
        log_store=LogStore(),
    )

    result = await manager.start()

    assert result.status == "already_running"
    assert result.comfyui.mode == "external"
    assert result.comfyui.reachable
    assert not process_called


@pytest.mark.anyio
async def test_managed_start_command_uses_hidden_runtime_data_paths(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "third_party" / "comfyui"
    repo_dir.mkdir(parents=True)
    (repo_dir / "main.py").write_text("", encoding="utf-8")

    process_started = False
    captured_command: list[str] = []
    captured_env: dict[str, str] = {}

    async def create_process(command, **kwargs):
        nonlocal process_started, captured_command, captured_env
        process_started = True
        captured_command = list(command)
        captured_env = dict(kwargs.get("env") or {})
        return FakeProcess()

    async def health_check(_: str) -> tuple[bool, str | None]:
        return process_started, None if process_started else "not reachable"

    data_dir = tmp_path / "data"
    extra_model_paths = data_dir / "runtime-store" / "settings" / "extra-model-paths.yaml"
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        process_factory=create_process,
        health_check=health_check,
        managed_base_directory=data_dir,
        managed_output_directory=data_dir / "outputs",
        managed_input_directory=data_dir / "input",
        managed_temp_directory=data_dir,
        managed_user_directory=data_dir / "user-state" / "comfyui",
        managed_database_url=f"sqlite:///{(data_dir / 'user-state' / 'comfyui' / 'comfyui.db').as_posix()}",
        python_cache_dir=data_dir / "cache" / "python",
        managed_extra_model_paths_config=extra_model_paths,
        log_store=LogStore(),
    )

    result = await manager.start()

    assert result.status == "started"
    assert "--disable-auto-launch" in captured_command
    assert "--dont-print-server" in captured_command
    assert _arg_value(captured_command, "--base-directory") == str(data_dir)
    assert _arg_value(captured_command, "--output-directory") == str(
        data_dir / "outputs"
    )
    assert _arg_value(captured_command, "--input-directory") == str(data_dir / "input")
    assert _arg_value(captured_command, "--temp-directory") == str(data_dir)
    assert _arg_value(captured_command, "--user-directory") == str(
        data_dir / "user-state" / "comfyui"
    )
    assert _arg_value(captured_command, "--database-url").endswith(
        "/data/user-state/comfyui/comfyui.db"
    )
    assert _arg_value(captured_command, "--extra-model-paths-config") == str(
        extra_model_paths
    )
    assert _arg_value(captured_command, "--preview-method") == "auto"
    assert _arg_value(captured_command, "--preview-size") == "512"
    assert captured_env["PYTHONPYCACHEPREFIX"] == str(data_dir / "cache" / "python")
    assert all(
        str(repo_dir) not in value
        for value in captured_command
        if value.startswith(str(data_dir))
    )


@pytest.mark.anyio
async def test_managed_start_command_omits_vram_flag_for_normal_mode(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    captured_command: list[str] = []
    process_started = False

    async def create_process(command, **kwargs):
        nonlocal captured_command, process_started
        process_started = True
        captured_command = list(command)
        return FakeProcess()

    async def health_check(_: str) -> tuple[bool, str | None]:
        return process_started, None if process_started else "not reachable"

    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        process_factory=create_process,
        health_check=health_check,
        managed_vram_mode="normal",
        log_store=LogStore(),
    )

    result = await manager.start()

    assert result.status == "started"
    assert result.comfyui.managed_vram_mode == "normal"
    assert "--highvram" not in captured_command
    assert "--lowvram" not in captured_command
    assert "--novram" not in captured_command
    assert "--cpu" not in captured_command


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "flag"),
    [
        ("highvram", "--highvram"),
        ("lowvram", "--lowvram"),
        ("novram", "--novram"),
        ("cpu", "--cpu"),
    ],
)
async def test_managed_start_command_includes_selected_vram_flag(
    tmp_path: Path,
    mode: str,
    flag: str,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    captured_command: list[str] = []
    process_started = False

    async def create_process(command, **kwargs):
        nonlocal captured_command, process_started
        process_started = True
        captured_command = list(command)
        return FakeProcess()

    async def health_check(_: str) -> tuple[bool, str | None]:
        return process_started, None if process_started else "not reachable"

    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        process_factory=create_process,
        health_check=health_check,
        managed_vram_mode=mode,
        log_store=LogStore(),
    )

    result = await manager.start()

    assert result.status == "started"
    assert result.comfyui.managed_vram_mode == mode
    assert captured_command.count(flag) == 1
    assert (
        sum(
            1
            for item in captured_command
            if item in {"--highvram", "--lowvram", "--novram", "--cpu"}
        )
        == 1
    )


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
        log_store=LogStore(),
    )
    fake_process = FakeProcess()
    manager._process = fake_process

    result = await manager.stop()

    assert result.status == "stopped"
    assert fake_process.terminated
    assert manager._process is None


@pytest.mark.anyio
async def test_managed_runtime_smoke_starts_health_checks_and_stops_fake_comfyui(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    (repo_dir / "main.py").write_text(
        """
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/system_stats":
            payload = json.dumps({"system": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


parser = argparse.ArgumentParser()
parser.add_argument("--listen", default="127.0.0.1")
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--disable-auto-launch", action="store_true")
parser.add_argument("--dont-print-server", action="store_true")
parser.add_argument("--preview-method", default="none")
parser.add_argument("--preview-size", type=int, default=512)
args = parser.parse_args()
HTTPServer((args.listen, args.port), Handler).serve_forever()
        """,
        encoding="utf-8",
    )
    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=sys.executable,
        required_imports=("json",),
        required_runtime_checks=(),
        hardware_profile=RuntimeHardwareProfile(
            os_name="Linux",
            os_version="",
            machine="x86_64",
            architecture="x86_64",
            accelerator="cpu",
        ),
        log_store=LogStore(),
    )
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable=sys.executable,
        startup_timeout_seconds=5,
        health_poll_interval_seconds=0.05,
        environment=environment,
        log_store=LogStore(),
    )

    start_result = await manager.start()
    status = await manager.status(include_environment=True)
    stop_result = await manager.stop()

    assert start_result.status == "started"
    assert status.reachable
    assert status.pid is not None
    assert status.managed_process_running
    assert status.environment is not None
    assert status.environment.prepared
    assert stop_result.status == "stopped"
    assert not stop_result.comfyui.managed_process_running


@pytest.mark.anyio
async def test_passive_status_skips_environment_dependency_checks(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()

    async def reachable(_: str) -> tuple[bool, str | None]:
        return True, None

    async def fail_if_checked(*args, **kwargs):
        raise AssertionError("passive status should not run dependency checks")

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=sys.executable,
        required_imports=("json",),
        required_runtime_checks=(),
        hardware_profile=RuntimeHardwareProfile(
            os_name="Linux",
            os_version="",
            machine="x86_64",
            architecture="x86_64",
            accelerator="cpu",
        ),
        command_runner=fail_if_checked,
        log_store=LogStore(),
    )
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable=sys.executable,
        environment=environment,
        health_check=reachable,
        log_store=LogStore(),
    )

    status = await manager.status()

    assert status.reachable
    assert status.environment is None


@pytest.mark.anyio
async def test_status_reports_environment_bootstrap_progress(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    (repo_dir / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    bootstrap_python = tmp_path / "bootstrap-python"
    bootstrap_python.write_text("", encoding="utf-8")
    bootstrap_python.chmod(0o755)
    venv_python = tmp_path / "runtime" / "comfyui-venv" / "bin" / "python"
    command_started = asyncio.Event()
    release_command = asyncio.Event()

    async def command_runner(command: list[str], cwd: Path | None):
        del cwd
        if command == [str(bootstrap_python), "-m", "venv", str(venv_python.parent.parent)]:
            command_started.set()
            await release_command.wait()
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")
            venv_python.chmod(0o755)
            return _command_result()
        if "sys.version_info" in command[-1]:
            return _command_result(stdout="3.13\n")
        return _command_result()

    async def unreachable(_: str) -> tuple[bool, str | None]:
        return False, "not reachable"

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable=str(bootstrap_python),
        required_imports=("json",),
        required_runtime_checks=(),
        hardware_profile=RuntimeHardwareProfile(
            os_name="Linux",
            os_version="",
            machine="x86_64",
            architecture="x86_64",
            accelerator="cpu",
        ),
        command_runner=command_runner,
        log_store=LogStore(),
    )
    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable=str(venv_python),
        environment=environment,
        health_check=unreachable,
        log_store=LogStore(),
    )

    bootstrap_task = asyncio.create_task(manager.bootstrap_environment())
    await asyncio.wait_for(command_started.wait(), timeout=1)

    status = await manager.status()

    assert status.environment_bootstrap_running
    assert status.environment_bootstrap_label == "Create ComfyUI runtime virtual environment"

    release_command.set()
    result = await bootstrap_task
    finished_status = await manager.status()

    assert result.status == "prepared"
    assert not finished_status.environment_bootstrap_running
    assert finished_status.environment_bootstrap_label is None


@pytest.mark.anyio
async def test_passive_status_preserves_recent_reachable_runtime_during_health_timeout(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    checks = 0

    async def health(_: str) -> tuple[bool, str | None]:
        nonlocal checks
        checks += 1
        if checks == 1:
            return True, None
        return False, "health_check_timeout"

    manager = RuntimeManager(
        mode="external",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable=sys.executable,
        health_check=health,
        log_store=LogStore(),
    )

    initial = await manager.status()
    busy = await manager.status()

    assert initial.reachable
    assert busy.reachable
    assert busy.transient_health_failure
    assert busy.error == "health_check_timeout"
    assert busy.last_reachable_at is not None


@pytest.mark.anyio
async def test_passive_status_preserves_recent_reachable_runtime_during_disconnected_health_probe(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    checks = 0

    async def health(_: str) -> tuple[bool, str | None]:
        nonlocal checks
        checks += 1
        if checks == 1:
            return True, None
        return False, "Server disconnected without sending a response."

    manager = RuntimeManager(
        mode="external",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable=sys.executable,
        health_check=health,
        log_store=LogStore(),
    )

    assert (await manager.status()).reachable
    busy = await manager.status()

    assert busy.reachable
    assert busy.transient_health_failure
    assert busy.error == "Server disconnected without sending a response."


@pytest.mark.anyio
async def test_passive_status_does_not_preserve_first_health_timeout(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()

    async def health(_: str) -> tuple[bool, str | None]:
        return False, "health_check_timeout"

    manager = RuntimeManager(
        mode="external",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable=sys.executable,
        health_check=health,
        log_store=LogStore(),
    )

    status = await manager.status()

    assert not status.reachable
    assert not status.transient_health_failure


@pytest.mark.anyio
async def test_passive_status_does_not_preserve_non_timeout_failure(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    checks = 0

    async def health(_: str) -> tuple[bool, str | None]:
        nonlocal checks
        checks += 1
        if checks == 1:
            return True, None
        return False, "connection refused"

    manager = RuntimeManager(
        mode="external",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable=sys.executable,
        health_check=health,
        log_store=LogStore(),
    )

    assert (await manager.status()).reachable
    disconnected = await manager.status()

    assert not disconnected.reachable
    assert not disconnected.transient_health_failure


def _arg_value(command: list[str], flag: str) -> str:
    return command[command.index(flag) + 1]


def _command_result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    from app.runtime.environment import CommandResult

    return CommandResult(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------------------
# sidecar_starting flag
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_sidecar_starting_is_true_while_polling(tmp_path: Path) -> None:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    fake_process = FakeProcess()

    # Always unreachable until start() sets _sidecar_starting, then reachable.
    # Use an event so the health check only flips after the process was spawned
    # (i.e., inside _poll_until_reachable), not during the pre-start status check.
    process_spawned = asyncio.Event()
    poll_count = 0

    async def health_after_spawn(url: str) -> tuple[bool, str | None]:
        nonlocal poll_count
        if not process_spawned.is_set():
            return False, "not yet"
        poll_count += 1
        return True, None

    async def create_process(*args, **kwargs):
        process_spawned.set()
        return fake_process

    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        startup_timeout_seconds=5,
        health_poll_interval_seconds=0.001,
        process_factory=create_process,
        health_check=health_after_spawn,
        log_store=LogStore(),
    )

    # Before start() is called, sidecar_starting must be False.
    assert not manager._sidecar_starting

    result = await manager.start()

    assert result.status == "started"
    assert poll_count >= 1, "health check must have been polled after spawn"
    assert not manager._sidecar_starting


@pytest.mark.anyio
async def test_sidecar_starting_cleared_on_timeout(tmp_path: Path) -> None:
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
        log_store=LogStore(),
    )

    result = await manager.start()

    assert result.status == "startup_timeout"
    assert not (await manager.status()).sidecar_starting


# ---------------------------------------------------------------------------
# Concurrent start guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_concurrent_start_calls_are_serialised(tmp_path: Path) -> None:
    """Two concurrent start() calls must not spawn two processes."""
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")

    spawn_count = 0
    # Health returns unreachable until the process has been spawned, then reachable.
    # It never blocks, so there is no deadlock risk.
    spawned = False

    async def create_process(*args, **kwargs):
        nonlocal spawn_count, spawned
        spawn_count += 1
        spawned = True
        return FakeProcess()

    async def health(url: str) -> tuple[bool, str | None]:
        if spawned:
            return True, None
        return False, "not yet"

    manager = RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        startup_timeout_seconds=5,
        health_poll_interval_seconds=0.001,
        process_factory=create_process,
        health_check=health,
        log_store=LogStore(),
    )

    r1, r2 = await asyncio.gather(manager.start(), manager.start())

    assert spawn_count == 1, "process must only be spawned once"
    assert r1.status in {"started", "already_running"}
    assert r2.status in {"started", "already_running"}
