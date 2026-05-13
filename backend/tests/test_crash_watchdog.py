"""Tests for managed ComfyUI sidecar crash detection, auto-restart, and PID file management."""

import asyncio
from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.runtime.manager import RuntimeManager


class FakeProcess:
    """Process stub that resolves wait() when terminate/kill is called or on explicit crash."""

    def __init__(self, *, pid: int = 9999) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.stdout = None
        self._wait_event = asyncio.Event()

    def crash(self, code: int = 1) -> None:
        """Simulate an unexpected process exit."""
        self.returncode = code
        self._wait_event.set()

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0
        self._wait_event.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._wait_event.set()

    async def wait(self) -> int:
        await self._wait_event.wait()
        return self.returncode or 0


class HealthGate:
    """Health check that becomes reachable only after a process is spawned."""

    def __init__(self) -> None:
        self.spawned_count = 0
        self._reachable_after_spawn: int = 1  # becomes healthy after Nth spawn
        self._fail_after_spawn: int | None = None  # fail for spawns > N

    async def __call__(self, _: str) -> tuple[bool, str | None]:
        if self._fail_after_spawn is not None and self.spawned_count > self._fail_after_spawn:
            return False, "unreachable (restart)"
        if self.spawned_count >= self._reachable_after_spawn:
            return True, None
        return False, "not ready"

    def fail_restarts_after_first(self) -> None:
        """Health passes for the first spawn, fails for all restarts."""
        self._fail_after_spawn = 1


def _base_manager(
    tmp_path: Path,
    *,
    process_factory=None,
    health_check=None,
    max_restart_attempts: int = 3,
    restart_backoff_base_seconds: float = 0.01,
    startup_timeout_seconds: float = 0.3,
    health_poll_interval_seconds: float = 0.01,
    managed_port: int | None = None,
    log_store: LogStore | None = None,
    pid_dir: Path | None = None,
) -> RuntimeManager:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir(exist_ok=True)
    (repo_dir / "main.py").write_text("", encoding="utf-8")

    return RuntimeManager(
        mode="managed",
        external_base_url="http://127.0.0.1:8188",
        repo_dir=repo_dir,
        python_executable="python3",
        managed_port=managed_port,
        startup_timeout_seconds=startup_timeout_seconds,
        health_poll_interval_seconds=health_poll_interval_seconds,
        max_restart_attempts=max_restart_attempts,
        restart_backoff_base_seconds=restart_backoff_base_seconds,
        log_store=log_store or LogStore(),
        process_factory=process_factory,
        health_check=health_check,
        pid_dir=pid_dir,
    )


# -----------------------------------------------------------------------
# Crash detection & restart
# -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_watchdog_detects_crash_and_restarts(tmp_path: Path) -> None:
    """When the process exits unexpectedly, the watchdog restarts it."""
    processes: list[FakeProcess] = []
    health = HealthGate()

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=1000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    manager = _base_manager(tmp_path, process_factory=factory, health_check=health)

    result = await manager.start()
    assert result.status == "started"
    assert len(processes) == 1

    # Simulate crash.
    processes[0].crash(code=1)
    await asyncio.sleep(0.3)

    # Watchdog should have spawned a second process.
    assert len(processes) == 2
    assert manager._crash_count == 1
    assert manager._restart_attempt == 0  # Reset after success.

    await manager.stop()


@pytest.mark.anyio
async def test_watchdog_respects_max_restart_attempts(tmp_path: Path) -> None:
    """After max_restart_attempts exhausted, watchdog stops retrying."""
    processes: list[FakeProcess] = []
    health = HealthGate()
    health.fail_restarts_after_first()  # Only first spawn is healthy.

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=2000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    log_store = LogStore()
    manager = _base_manager(
        tmp_path,
        process_factory=factory,
        health_check=health,
        max_restart_attempts=2,
        startup_timeout_seconds=0.05,
        log_store=log_store,
    )

    result = await manager.start()
    assert result.status == "started"

    # Crash the initial process.
    processes[0].crash(code=1)

    # Wait for 2 restart attempts + exhaustion.
    await asyncio.sleep(1.5)

    assert manager._crash_count >= 1
    assert "exhausted" in (manager._last_error or "").lower()


@pytest.mark.anyio
async def test_watchdog_does_not_restart_on_intentional_stop(tmp_path: Path) -> None:
    """Calling stop() prevents the watchdog from restarting."""
    processes: list[FakeProcess] = []
    health = HealthGate()

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=3000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    manager = _base_manager(tmp_path, process_factory=factory, health_check=health)

    await manager.start()
    assert len(processes) == 1

    await manager.stop()
    assert manager._stopping is True

    # Give watchdog time – it should NOT restart.
    await asyncio.sleep(0.1)
    assert len(processes) == 1


@pytest.mark.anyio
async def test_restart_picks_new_port_when_not_pinned(tmp_path: Path) -> None:
    """After a crash, a new free port is selected if port was not configured."""
    ports_seen: list[int] = []
    processes: list[FakeProcess] = []
    health = HealthGate()

    async def tracking_factory(command, **kwargs):
        for i, arg in enumerate(command):
            if arg == "--port" and i + 1 < len(command):
                ports_seen.append(int(command[i + 1]))
        proc = FakeProcess(pid=4000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    manager = _base_manager(
        tmp_path,
        process_factory=tracking_factory,
        health_check=health,
    )

    await manager.start()
    assert len(ports_seen) >= 1

    # Crash → restart.
    processes[0].crash(code=1)
    await asyncio.sleep(0.3)

    assert len(ports_seen) >= 2
    assert len(processes) == 2

    await manager.stop()


@pytest.mark.anyio
async def test_crash_count_is_cumulative_but_restart_attempt_resets(tmp_path: Path) -> None:
    """crash_count accumulates; restart_attempt resets on successful restart."""
    processes: list[FakeProcess] = []
    health = HealthGate()

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=5000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    manager = _base_manager(
        tmp_path,
        process_factory=factory,
        health_check=health,
        max_restart_attempts=5,
    )

    await manager.start()

    # First crash.
    processes[0].crash()
    await asyncio.sleep(0.3)
    assert manager._crash_count == 1
    assert manager._restart_attempt == 0  # Reset on success.

    # Second crash.
    processes[1].crash()
    await asyncio.sleep(0.3)
    assert manager._crash_count == 2
    assert manager._restart_attempt == 0

    await manager.stop()


@pytest.mark.anyio
async def test_status_reports_crash_and_uptime_fields(tmp_path: Path) -> None:
    """status() includes crash_count, uptime_seconds, max_restart_attempts."""
    processes: list[FakeProcess] = []
    health = HealthGate()

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=6000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    manager = _base_manager(
        tmp_path,
        max_restart_attempts=3,
        process_factory=factory,
        health_check=health,
    )

    await manager.start()
    await asyncio.sleep(0.05)

    status = await manager.status()
    assert status.crash_count == 0
    assert status.restart_attempt == 0
    assert status.max_restart_attempts == 3
    assert status.uptime_seconds is not None
    assert status.uptime_seconds >= 0
    assert status.last_crash_at is None

    await manager.stop()


# -----------------------------------------------------------------------
# PID file management
# -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_pid_file_written_and_removed(tmp_path: Path) -> None:
    """PID file is written on start and removed on stop."""
    pid_dir = tmp_path / "runtime"
    processes: list[FakeProcess] = []
    health = HealthGate()

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=8000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    manager = _base_manager(
        tmp_path,
        process_factory=factory,
        health_check=health,
        pid_dir=pid_dir,
    )

    await manager.start()

    pid_file = pid_dir / "comfyui.pid"
    assert pid_file.exists()
    assert pid_file.read_text(encoding="utf-8").strip() == "8000"

    await manager.stop()
    assert not pid_file.exists()


@pytest.mark.anyio
async def test_stale_pid_file_cleaned_on_startup(tmp_path: Path) -> None:
    """If a stale PID file points to a dead process, it is cleaned up on start."""
    pid_dir = tmp_path / "runtime"
    pid_dir.mkdir()
    pid_file = pid_dir / "comfyui.pid"
    pid_file.write_text("99999999", encoding="utf-8")  # Almost certainly dead.

    processes: list[FakeProcess] = []
    health = HealthGate()

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=7000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        return proc

    log_store = LogStore()
    manager = _base_manager(
        tmp_path,
        process_factory=factory,
        health_check=health,
        log_store=log_store,
        pid_dir=pid_dir,
    )

    await manager.start()

    # PID file should now contain the new PID.
    assert pid_file.exists()
    assert pid_file.read_text(encoding="utf-8").strip() == "7000"

    await manager.stop()
    assert not pid_file.exists()


# -----------------------------------------------------------------------
# Backoff timing
# -----------------------------------------------------------------------


@pytest.mark.anyio
async def test_backoff_increases_exponentially(tmp_path: Path) -> None:
    """Restart delay follows base * 2^(attempt-1)."""
    restart_times: list[float] = []
    processes: list[FakeProcess] = []
    health = HealthGate()
    health.fail_restarts_after_first()  # Only first spawn is healthy.

    async def factory(*args, **kwargs):
        proc = FakeProcess(pid=9000 + len(processes))
        processes.append(proc)
        health.spawned_count = len(processes)
        if len(processes) > 1:
            restart_times.append(asyncio.get_event_loop().time())
        return proc

    manager = _base_manager(
        tmp_path,
        max_restart_attempts=3,
        restart_backoff_base_seconds=0.05,  # delays: 0.05, 0.10, 0.20
        startup_timeout_seconds=0.02,
        process_factory=factory,
        health_check=health,
    )

    await manager.start()
    crash_time = asyncio.get_event_loop().time()
    processes[0].crash(code=1)

    # Wait for all 3 attempts to exhaust.
    await asyncio.sleep(2.0)

    assert len(restart_times) >= 2, f"Expected at least 2 restart timestamps, got {len(restart_times)}"

    # The gap between restart attempt 1 and 2 should be larger than between crash and attempt 1.
    if len(restart_times) >= 2:
        gap1 = restart_times[0] - crash_time
        gap2 = restart_times[1] - restart_times[0]
        # gap2 should be roughly 2x gap1 (with tolerance for async overhead).
        assert gap2 > gap1 * 1.2, f"Backoff not increasing: gap1={gap1:.3f}, gap2={gap2:.3f}"
