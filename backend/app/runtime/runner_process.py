"""Process supervision primitives for ComfyUI runners.

This module is the Phase 4 process layer beneath `RunnerSupervisor`. It can
start and stop a runner process from a launch spec, poll health, and expose a
runner descriptor. It does not choose workflows or mutate engine routing.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field

from app.engine.diagnostics import LogStore
from app.runtime.manager import select_free_port
from app.runtime.supervisor import RunnerDescriptor, RunnerKind, RunnerStatus

RunnerProcessFactory = Callable[..., Awaitable[Any]]
RunnerHealthCheck = Callable[[str], Awaitable[tuple[bool, str | None]]]


class RunnerLaunchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(min_length=1)
    kind: RunnerKind = RunnerKind.ISOLATED_COMFYUI
    fingerprint: str = Field(min_length=1)
    python_executable: str = Field(min_length=1)
    working_dir: Path
    dependency_env_path: Path | None = None
    runner_workspace_path: Path | None = None
    host: str = "127.0.0.1"
    port: int | None = None
    entrypoint: str = "main.py"
    extra_args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None


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
        log_store: LogStore | None = None,
        process_factory: RunnerProcessFactory | None = None,
        health_check: RunnerHealthCheck | None = None,
        startup_timeout_seconds: float = 60,
        health_poll_interval_seconds: float = 0.5,
    ) -> None:
        self.log_store = log_store or LogStore()
        self._process_factory = process_factory or self._create_process
        self._health_check = health_check or self._default_health_check
        self.startup_timeout_seconds = startup_timeout_seconds
        self.health_poll_interval_seconds = health_poll_interval_seconds
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
        command = tuple(
            [
                spec.python_executable,
                spec.entrypoint,
                "--listen",
                spec.host,
                "--port",
                str(port),
                *spec.extra_args,
            ]
        )
        process_env = None
        if spec.env is not None:
            process_env = {**os.environ, **spec.env}
        descriptor = RunnerDescriptor(
            runner_id=spec.runner_id,
            kind=spec.kind,
            base_url=base_url,
            ws_url=ws_url,
            fingerprint=spec.fingerprint,
            status=RunnerStatus.STARTING,
        )

        try:
            process = await self._process_factory(
                list(command),
                cwd=spec.working_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=process_env,
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
            descriptor=descriptor,
            process=process,
            command=command,
        )
        record.log_task = asyncio.create_task(self._capture_process_output(record))
        self._records[spec.runner_id] = record
        self.log_store.add(
            "info",
            "Runner process started",
            "runtime.runner_process",
            details={
                "runner_id": spec.runner_id,
                "pid": process.pid,
                "base_url": base_url,
                "dependency_env_path": str(spec.dependency_env_path) if spec.dependency_env_path else None,
                "runner_workspace_path": str(spec.runner_workspace_path) if spec.runner_workspace_path else None,
            },
        )

        reachable = await self._poll_until_reachable(record)
        if reachable:
            record.descriptor = record.descriptor.model_copy(update={"status": RunnerStatus.READY})
            record.last_error = None
            self.log_store.add(
                "info",
                "Runner process ready",
                "runtime.runner_process",
                details={"runner_id": spec.runner_id, "base_url": base_url},
            )
            return self._handle(record)

        if getattr(process, "returncode", None) is not None:
            record.last_error = f"Runner exited during startup with code {process.returncode}"
        else:
            record.last_error = f"Runner startup timed out after {self.startup_timeout_seconds:g} seconds"
        record.descriptor = record.descriptor.model_copy(update={"status": RunnerStatus.UNREACHABLE})
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
            record.descriptor = record.descriptor.model_copy(update={"status": RunnerStatus.STOPPING})
            record.process.terminate()
            try:
                await asyncio.wait_for(record.process.wait(), timeout=10)
            except TimeoutError:
                record.process.kill()
                await asyncio.wait_for(record.process.wait(), timeout=10)

        if record.log_task is not None:
            record.log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await record.log_task
            record.log_task = None

        record.descriptor = record.descriptor.model_copy(update={"status": RunnerStatus.STOPPED})
        self.log_store.add(
            "info",
            "Runner process stopped",
            "runtime.runner_process",
            details={"runner_id": runner_id},
        )
        status = self._status_from_record(record)
        self._records.pop(runner_id, None)
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
            record.descriptor = record.descriptor.model_copy(update={"status": RunnerStatus.STOPPED})
            self.log_store.add(
                "error",
                "Runner process exited",
                "runtime.runner_process",
                details={"runner_id": runner_id, "returncode": returncode},
            )
            return self._status_from_record(record)

        reachable, error = await self._health_check(record.descriptor.base_url)
        record.descriptor = record.descriptor.model_copy(
            update={"status": RunnerStatus.READY if reachable else RunnerStatus.UNREACHABLE}
        )
        record.last_error = None if reachable else error
        return self._status_from_record(record)

    def descriptor(self, runner_id: str) -> RunnerDescriptor | None:
        record = self._records.get(runner_id)
        if record is None:
            return None
        return record.descriptor

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
            text = line.decode(errors="replace").rstrip() if isinstance(line, bytes) else str(line).rstrip()
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

    async def _create_process(self, command: list[str], **kwargs: Any) -> asyncio.subprocess.Process:
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


def _default_ws_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "/ws", "", "", ""))
