"""Process supervision primitives for ComfyUI runners.

This module is the Phase 4 process layer beneath `RunnerSupervisor`. It can
start and stop a runner process from a launch spec, poll health, and expose a
runner descriptor. It does not choose workflows or mutate engine routing.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field

from app.engine.diagnostics import DiagnosticsSink
from app.runtime.manager import select_free_port
from app.runtime.supervisor import (
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    RunnerStatus,
)

RunnerProcessFactory = Callable[..., Awaitable[Any]]
RunnerHealthCheck = Callable[[str], Awaitable[tuple[bool, str | None]]]
RunnerProcessTreeTerminator = Callable[[Any], Awaitable[None]]


class RunnerLaunchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(min_length=1)
    kind: RunnerKind = RunnerKind.ISOLATED_COMFYUI
    fingerprint: str = Field(min_length=1)
    python_executable: str = Field(min_length=1)
    working_dir: Path
    dependency_env_path: Path | None = None
    runner_workspace_path: Path | None = None
    runner_workspace_fingerprint: str | None = None
    dependency_env_fingerprint: str | None = None
    runner_process_compatibility_key: str | None = None
    model_view_fingerprint: str | None = None
    runtime_profile_id: str | None = None
    runtime_profile_variant_id: str | None = None
    memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    host: str = "127.0.0.1"
    port: int | None = None
    entrypoint: str = "main.py"
    extra_args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    memory_telemetry_path: Path | None = None


class RunnerProcessStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: str
    status: RunnerStatus
    base_url: str
    ws_url: str
    pid: int | None = None
    returncode: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class RunnerProcessHandle:
    runner_id: str
    descriptor: RunnerDescriptor
    pid: int
    command: tuple[str, ...]


@dataclass
class _RunnerProcessRecord:
    spec: RunnerLaunchSpec
    descriptor: RunnerDescriptor
    process: Any
    command: tuple[str, ...]
    log_task: asyncio.Task[None] | None = None
    last_error: str | None = None


class RunnerProcessSupervisor:
    def __init__(
        self,
        *,
        log_store: DiagnosticsSink,
        process_factory: RunnerProcessFactory | None = None,
        health_check: RunnerHealthCheck | None = None,
        process_tree_terminator: RunnerProcessTreeTerminator | None = None,
        startup_timeout_seconds: float = 60,
        health_poll_interval_seconds: float = 0.5,
        pid_dir: Path | None = None,
    ) -> None:
        self.log_store = log_store
        self._process_factory = process_factory or self._create_process
        self._health_check = health_check or self._default_health_check
        self._process_tree_terminator = process_tree_terminator
        self._owns_process_tree = process_factory is None
        self.startup_timeout_seconds = startup_timeout_seconds
        self.health_poll_interval_seconds = health_poll_interval_seconds
        self.pid_dir = pid_dir
        self._records: dict[str, _RunnerProcessRecord] = {}

    async def start(self, spec: RunnerLaunchSpec) -> RunnerProcessHandle:
        existing = self._records.get(spec.runner_id)
        if existing is not None and self._is_running(existing.process):
            self.log_store.add(
                "info",
                "Runner process already running",
                "runtime.runner_process",
                details={"runner_id": spec.runner_id, "pid": existing.process.pid},
            )
            return self._handle(existing)

        port = spec.port or select_free_port(spec.host)
        base_url = f"http://{spec.host}:{port}"
        ws_url = _default_ws_url(base_url)
        target_command = [
            spec.entrypoint,
            "--listen",
            spec.host,
            "--port",
            str(port),
            *spec.extra_args,
        ]
        command = tuple(_memory_probe_command(spec, target_command))
        process_env = dict(os.environ)
        if spec.env is not None:
            process_env.update(spec.env)
        process_env.pop("NOOFY_API_TOKEN", None)
        descriptor = RunnerDescriptor(
            runner_id=spec.runner_id,
            kind=spec.kind,
            base_url=base_url,
            ws_url=ws_url,
            fingerprint=spec.fingerprint,
            status=RunnerStatus.STARTING,
            runner_workspace_fingerprint=spec.runner_workspace_fingerprint,
            dependency_env_fingerprint=spec.dependency_env_fingerprint,
            runner_process_compatibility_key=spec.runner_process_compatibility_key,
            model_view_fingerprint=spec.model_view_fingerprint,
            runtime_profile_id=spec.runtime_profile_id,
            runtime_profile_variant_id=spec.runtime_profile_variant_id,
            memory_class=spec.memory_class,
            memory_telemetry_path=(
                str(spec.memory_telemetry_path)
                if spec.memory_telemetry_path is not None
                else None
            ),
        )

        try:
            process = await self._process_factory(
                list(command),
                **{
                    "cwd": spec.working_dir,
                    "stdout": asyncio.subprocess.PIPE,
                    "stderr": asyncio.subprocess.STDOUT,
                    "env": process_env,
                    **_process_tree_start_kwargs(),
                },
            )
        except Exception as exc:
            self.log_store.add(
                "error",
                "Runner process spawn failed",
                "runtime.runner_process",
                details={"runner_id": spec.runner_id, "error": str(exc)},
            )
            raise

        record = _RunnerProcessRecord(
            spec=spec.model_copy(update={"port": port}),
            descriptor=descriptor.model_copy(update={"pid": process.pid}),
            process=process,
            command=command,
        )
        record.log_task = asyncio.create_task(self._capture_process_output(record))
        self._records[spec.runner_id] = record
        self._write_pid_file(spec.runner_id, process.pid)
        self.log_store.add(
            "info",
            "Runner process started",
            "runtime.runner_process",
            details={
                "runner_id": spec.runner_id,
                "pid": process.pid,
                "base_url": base_url,
                "dependency_env_path": (
                    str(spec.dependency_env_path) if spec.dependency_env_path else None
                ),
                "runner_workspace_path": (
                    str(spec.runner_workspace_path)
                    if spec.runner_workspace_path
                    else None
                ),
            },
        )

        reachable = await self._poll_until_reachable(record)
        if reachable:
            record.descriptor = record.descriptor.model_copy(
                update={"status": RunnerStatus.READY}
            )
            record.last_error = None
            self.log_store.add(
                "info",
                "Runner process ready",
                "runtime.runner_process",
                details={"runner_id": spec.runner_id, "base_url": base_url},
            )
            return self._handle(record)

        if getattr(process, "returncode", None) is not None:
            record.last_error = (
                f"Runner exited during startup with code {process.returncode}"
            )
        else:
            record.last_error = f"Runner startup timed out after {self.startup_timeout_seconds:g} seconds"
        record.descriptor = record.descriptor.model_copy(
            update={"status": RunnerStatus.UNREACHABLE}
        )
        self.log_store.add(
            "error",
            "Runner process startup failed",
            "runtime.runner_process",
            details={"runner_id": spec.runner_id, "error": record.last_error},
        )
        await self.stop(spec.runner_id)
        raise RuntimeError(record.last_error)

    async def stop(self, runner_id: str) -> RunnerProcessStatus:
        record = self._records.get(runner_id)
        if record is None:
            return RunnerProcessStatus(
                runner_id=runner_id,
                status=RunnerStatus.STOPPED,
                base_url="",
                ws_url="",
            )

        if self._is_running(record.process):
            record.descriptor = record.descriptor.model_copy(
                update={"status": RunnerStatus.STOPPING}
            )
            await self._terminate_runner_process(record)

        if record.log_task is not None:
            record.log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await record.log_task
            record.log_task = None

        record.descriptor = record.descriptor.model_copy(
            update={"status": RunnerStatus.STOPPED}
        )
        self.log_store.add(
            "info",
            "Runner process stopped",
            "runtime.runner_process",
            details={"runner_id": runner_id},
        )
        status = self._status_from_record(record)
        self._records.pop(runner_id, None)
        self._remove_pid_file(runner_id)
        return status

    async def stop_all(self) -> list[RunnerProcessStatus]:
        statuses: list[RunnerProcessStatus] = []
        for runner_id in list(self._records):
            statuses.append(await self.stop(runner_id))
        return statuses

    async def status(self, runner_id: str) -> RunnerProcessStatus:
        record = self._records.get(runner_id)
        if record is None:
            return RunnerProcessStatus(
                runner_id=runner_id,
                status=RunnerStatus.STOPPED,
                base_url="",
                ws_url="",
            )

        if not self._is_running(record.process):
            returncode = getattr(record.process, "returncode", None)
            record.last_error = f"Runner process exited with code {returncode}"
            record.descriptor = record.descriptor.model_copy(
                update={"status": RunnerStatus.STOPPED}
            )
            self._remove_pid_file(runner_id)
            self.log_store.add(
                "error",
                "Runner process exited",
                "runtime.runner_process",
                details={"runner_id": runner_id, "returncode": returncode},
            )
            return self._status_from_record(record)

        reachable, error = await self._health_check(record.descriptor.base_url)
        record.descriptor = record.descriptor.model_copy(
            update={
                "status": RunnerStatus.READY if reachable else RunnerStatus.UNREACHABLE
            }
        )
        record.last_error = None if reachable else error
        return self._status_from_record(record)

    def descriptor(self, runner_id: str) -> RunnerDescriptor | None:
        record = self._records.get(runner_id)
        if record is None:
            return None
        return record.descriptor

    def cleanup_stale_pid_files(self) -> int:
        if self.pid_dir is None or not self.pid_dir.exists():
            return 0
        cleaned = 0
        for pid_file in sorted(self.pid_dir.glob("runner-*.pid")):
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (ValueError, OSError):
                pid_file.unlink(missing_ok=True)
                cleaned += 1
                continue
            if _is_pid_alive(pid):
                self.log_store.add(
                    "warning",
                    "Killing orphan workflow runner process from previous run",
                    "runtime.runner_process",
                    details={"pid": pid, "pid_file": str(pid_file)},
                )
                _terminate_stale_pid(pid)
            pid_file.unlink(missing_ok=True)
            cleaned += 1
        return cleaned

    def _handle(self, record: _RunnerProcessRecord) -> RunnerProcessHandle:
        return RunnerProcessHandle(
            runner_id=record.spec.runner_id,
            descriptor=record.descriptor,
            pid=record.process.pid,
            command=record.command,
        )

    def _status_from_record(self, record: _RunnerProcessRecord) -> RunnerProcessStatus:
        return RunnerProcessStatus(
            runner_id=record.spec.runner_id,
            status=record.descriptor.status,
            base_url=record.descriptor.base_url,
            ws_url=record.descriptor.ws_url or "",
            pid=record.process.pid if self._is_running(record.process) else None,
            returncode=getattr(record.process, "returncode", None),
            error=record.last_error,
        )

    async def _poll_until_reachable(self, record: _RunnerProcessRecord) -> bool:
        deadline = asyncio.get_running_loop().time() + self.startup_timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if not self._is_running(record.process):
                return False
            reachable, _ = await self._health_check(record.descriptor.base_url)
            if reachable:
                return True
            await asyncio.sleep(self.health_poll_interval_seconds)
        return False

    async def _capture_process_output(self, record: _RunnerProcessRecord) -> None:
        stream = getattr(record.process, "stdout", None)
        if stream is None:
            return
        while True:
            line = await stream.readline()
            if not line:
                return
            text = (
                line.decode(errors="replace").rstrip()
                if isinstance(line, bytes)
                else str(line).rstrip()
            )
            if text:
                self.log_store.add(
                    "debug",
                    text,
                    "runtime.runner_process.stdout",
                    details={"runner_id": record.spec.runner_id},
                )

    @staticmethod
    def _is_running(process: Any) -> bool:
        return process is not None and getattr(process, "returncode", None) is None

    async def _terminate_runner_process(self, record: _RunnerProcessRecord) -> None:
        process = record.process
        if self._process_tree_terminator is not None:
            await self._process_tree_terminator(process)
            return
        if self._owns_process_tree:
            await self._terminate_owned_process_tree(process, record.spec.runner_id)
            return
        await self._terminate_direct_process(process)

    async def _terminate_owned_process_tree(self, process: Any, runner_id: str) -> None:
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int):
            await self._terminate_direct_process(process)
            return

        self.log_store.add(
            "info",
            "Stopping runner process tree",
            "runtime.runner_process",
            details={"runner_id": runner_id, "pid": pid},
        )
        if os.name == "nt":
            await self._terminate_windows_process_tree(process, pid)
            return

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as exc:
            self.log_store.add(
                "warning",
                "Runner process-group termination failed; falling back to parent process",
                "runtime.runner_process",
                details={"runner_id": runner_id, "pid": pid, "error": str(exc)},
            )
            await self._terminate_direct_process(process)
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            await asyncio.wait_for(process.wait(), timeout=10)

    async def _terminate_windows_process_tree(self, process: Any, pid: int) -> None:
        with contextlib.suppress(Exception):
            process.send_signal(signal.CTRL_BREAK_EVENT)
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
            return
        except TimeoutError:
            pass

        taskkill = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(taskkill.wait(), timeout=10)
        if self._is_running(process):
            with contextlib.suppress(Exception):
                process.kill()
            await asyncio.wait_for(process.wait(), timeout=10)

    async def _terminate_direct_process(self, process: Any) -> None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.kill()
            await asyncio.wait_for(process.wait(), timeout=10)

    async def _create_process(
        self, command: list[str], **kwargs: Any
    ) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(*command, **kwargs)

    async def _default_health_check(self, base_url: str) -> tuple[bool, str | None]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{base_url}/system_stats")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return False, str(exc)
        return True, None

    def _pid_file(self, runner_id: str) -> Path | None:
        if self.pid_dir is None:
            return None
        return self.pid_dir / f"runner-{_safe_runner_id(runner_id)}.pid"

    def _write_pid_file(self, runner_id: str, pid: int) -> None:
        pid_file = self._pid_file(runner_id)
        if pid_file is None:
            return
        try:
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(pid), encoding="utf-8")
        except OSError:
            pass

    def _remove_pid_file(self, runner_id: str) -> None:
        pid_file = self._pid_file(runner_id)
        if pid_file is None:
            return
        with contextlib.suppress(OSError):
            pid_file.unlink(missing_ok=True)


def _default_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/ws", "", "", ""))


def _process_tree_start_kwargs() -> dict[str, object]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _memory_probe_command(
    spec: RunnerLaunchSpec, target_command: list[str]
) -> list[str]:
    if spec.memory_telemetry_path is None:
        return [spec.python_executable, *target_command]
    return [
        spec.python_executable,
        str(Path(__file__).with_name("runner_memory_probe.py")),
        "--runner-id",
        spec.runner_id,
        "--telemetry-file",
        str(spec.memory_telemetry_path),
        "--sample-window",
        "runner_startup",
        "--sample-interval-seconds",
        "0.1",
        "--",
        *target_command,
    ]


def _safe_runner_id(runner_id: str) -> str:
    return "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_" for char in runner_id
    )


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_stale_pid(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
