import asyncio
import contextlib
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import httpx

from app.engine.diagnostics import LogStore
from app.engine.models import ComfyUIRuntimeStatus, ProcessActionResult, RuntimeBootstrapResult, RuntimeMode
from app.runtime.environment import RuntimeEnvironment

HealthCheck = Callable[[str], Awaitable[tuple[bool, str | None]]]
ProcessFactory = Callable[..., Awaitable[Any]]


def select_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class RuntimeManager:
    def __init__(
        self,
        *,
        mode: str,
        external_base_url: str,
        repo_dir: Path,
        python_executable: str,
        managed_host: str = "127.0.0.1",
        managed_port: int | None = None,
        external_ws_url: str | None = None,
        startup_timeout_seconds: float = 60,
        health_poll_interval_seconds: float = 0.5,
        log_store: LogStore | None = None,
        environment: RuntimeEnvironment | None = None,
        process_factory: ProcessFactory | None = None,
        health_check: HealthCheck | None = None,
    ) -> None:
        if mode not in {"external", "managed"}:
            raise ValueError(f"Unsupported ComfyUI runtime mode: {mode}")

        self.mode = cast(RuntimeMode, mode)
        self.repo_dir = repo_dir
        self.python_executable = python_executable
        self.managed_host = managed_host
        self._managed_port_configured = managed_port is not None
        self.managed_port = managed_port or select_free_port(managed_host)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.health_poll_interval_seconds = health_poll_interval_seconds
        self.log_store = log_store or LogStore()
        self.environment = environment
        self._process_factory = process_factory or self._create_process
        self._health_check = health_check or self._default_health_check
        self._process: Any | None = None
        self._log_task: asyncio.Task[None] | None = None
        self._last_error: str | None = None

        self.external_base_url = external_base_url.rstrip("/")
        self.external_ws_url = external_ws_url
        self.base_url = self.external_base_url if self.mode == "external" else self._managed_base_url()
        self.ws_url = (
            self.external_ws_url
            if self.mode == "external" and self.external_ws_url
            else self._default_ws_url(self.base_url)
        )

    async def status(self) -> ComfyUIRuntimeStatus:
        self._record_process_exit_if_needed()
        reachable, reachability_error = await self._health_check(self.base_url)
        environment_status = await self.environment.status() if self.environment is not None else None
        error = None if reachable else self._last_error or reachability_error
        return ComfyUIRuntimeStatus(
            mode=self.mode,
            reachable=reachable,
            base_url=self.base_url,
            repo_dir=str(self.repo_dir),
            managed_process_running=self._is_managed_process_running(),
            pid=self._process.pid if self._is_managed_process_running() else None,
            error=error,
            environment=environment_status,
        )

    async def start(self) -> ProcessActionResult:
        current = await self.status()
        if self.mode == "external":
            if current.reachable:
                self.log_store.add(
                    "info",
                    "External ComfyUI already reachable",
                    "runtime.manager",
                    details={"base_url": self.base_url},
                )
                return ProcessActionResult(status="already_running", comfyui=current)

            self.log_store.add(
                "warning",
                "External ComfyUI is not reachable",
                "runtime.manager",
                details={"base_url": self.base_url, "error": current.error},
            )
            return ProcessActionResult(status="external_unreachable", comfyui=current)

        if current.reachable:
            self.log_store.add(
                "info",
                "Managed ComfyUI already reachable",
                "runtime.manager",
                details={"base_url": self.base_url},
            )
            return ProcessActionResult(status="already_running", comfyui=current)

        repo_error = self._repo_error()
        if repo_error is not None:
            self._last_error = repo_error
            self.log_store.add(
                "error",
                "ComfyUI runtime cannot start",
                "runtime.manager",
                details={"error": repo_error},
            )
            return ProcessActionResult(status="repo_missing", comfyui=(await self.status()))

        if self.environment is not None:
            environment_status = await self.environment.status()
            if not environment_status.prepared:
                self._last_error = environment_status.error or "ComfyUI runtime environment is not prepared"
                self.log_store.add(
                    "error",
                    "ComfyUI runtime environment is not prepared",
                    "runtime.manager",
                    details={"error": self._last_error},
                )
                return ProcessActionResult(status="environment_not_ready", comfyui=(await self.status()))

        if not self._is_managed_process_running():
            await self._start_process()

        started = await self._poll_until_reachable(self.startup_timeout_seconds)
        if started:
            self._last_error = None
            status = await self.status()
            self.log_store.add(
                "info",
                "Managed ComfyUI started",
                "runtime.manager",
                details={"base_url": self.base_url, "pid": status.pid},
            )
            return ProcessActionResult(status="started", comfyui=status)

        if self._process is not None and getattr(self._process, "returncode", None) is not None:
            self._last_error = f"ComfyUI process exited during startup with code {self._process.returncode}"
            self.log_store.add(
                "error",
                "Managed ComfyUI startup failed",
                "runtime.manager",
                details={"error": self._last_error},
            )
            return ProcessActionResult(status="startup_failed", comfyui=(await self.status()))

        self._last_error = f"ComfyUI startup timed out after {self.startup_timeout_seconds:g} seconds"
        self.log_store.add(
            "error",
            "Managed ComfyUI startup timed out",
            "runtime.manager",
            details={"error": self._last_error},
        )
        await self._stop_managed_process()
        return ProcessActionResult(status="startup_timeout", comfyui=(await self.status()))

    async def stop(self) -> ProcessActionResult:
        if self.mode == "external":
            self.log_store.add("warning", "ComfyUI stop requested in external runtime mode", "runtime.manager")
            return ProcessActionResult(status="not_managed", comfyui=await self.status())

        if not self._is_managed_process_running():
            self.log_store.add(
                "warning",
                "ComfyUI stop requested but no managed process is running",
                "runtime.manager",
            )
            return ProcessActionResult(status="not_running", comfyui=await self.status())

        await self._stop_managed_process()
        self._last_error = None
        self.log_store.add("info", "Managed ComfyUI stopped", "runtime.manager")
        return ProcessActionResult(status="stopped", comfyui=await self.status())

    async def bootstrap_environment(self) -> RuntimeBootstrapResult:
        if self.environment is None:
            status = await self.status()
            self.log_store.add("warning", "No managed runtime environment is configured", "runtime.manager")
            return RuntimeBootstrapResult(
                status="not_configured",
                environment=status.environment,
            )
        return await self.environment.bootstrap()

    async def _start_process(self) -> None:
        if not self._managed_port_configured:
            self.managed_port = select_free_port(self.managed_host)
            self.base_url = self._managed_base_url()
            self.ws_url = self._default_ws_url(self.base_url)

        command = [
            self._resolved_python_executable(),
            "main.py",
            "--listen",
            self.managed_host,
            "--port",
            str(self.managed_port),
        ]
        self._process = await self._process_factory(
            command,
            cwd=self.repo_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._last_error = None
        self.log_store.add(
            "info",
            "Managed ComfyUI process started",
            "runtime.manager",
            details={"pid": self._process.pid, "host": self.managed_host, "port": self.managed_port},
        )
        self._log_task = asyncio.create_task(self._capture_process_output(self._process))

    async def _stop_managed_process(self) -> None:
        process = self._process
        if process is None:
            return

        if getattr(process, "returncode", None) is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                process.kill()
                await asyncio.wait_for(process.wait(), timeout=10)

        if self._log_task is not None:
            self._log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._log_task
        self._log_task = None
        self._process = None

    async def _capture_process_output(self, process: Any) -> None:
        stream = getattr(process, "stdout", None)
        if stream is None:
            return

        while True:
            line = await stream.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip() if isinstance(line, bytes) else str(line).rstrip()
            if text:
                self.log_store.add("debug", text, "comfyui.stdout")

    async def _poll_until_reachable(self, timeout_seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            self._record_process_exit_if_needed()
            if self._process is not None and getattr(self._process, "returncode", None) is not None:
                return False

            reachable, _ = await self._health_check(self.base_url)
            if reachable:
                return True

            await asyncio.sleep(self.health_poll_interval_seconds)
        return False

    def _repo_error(self) -> str | None:
        if not self.repo_dir.exists():
            return f"ComfyUI repo not found: {self.repo_dir}"
        if not (self.repo_dir / "main.py").exists():
            return f"ComfyUI main.py not found in: {self.repo_dir}"
        return None

    def _record_process_exit_if_needed(self) -> None:
        if self.mode != "managed" or self._process is None:
            return
        returncode = getattr(self._process, "returncode", None)
        if returncode is None:
            return
        error = f"Managed ComfyUI process exited with code {returncode}"
        if self._last_error != error:
            self._last_error = error
            self.log_store.add(
                "error",
                "Managed ComfyUI process exited",
                "runtime.manager",
                details={"returncode": returncode},
            )

    def _is_managed_process_running(self) -> bool:
        return (
            self.mode == "managed"
            and self._process is not None
            and getattr(self._process, "returncode", None) is None
        )

    def _managed_base_url(self) -> str:
        return f"http://{self.managed_host}:{self.managed_port}"

    def _resolved_python_executable(self) -> str:
        if self.environment is not None:
            return self.environment.python_executable
        return self.python_executable

    async def _create_process(self, command: list[str], **kwargs: Any) -> asyncio.subprocess.Process:
        return await asyncio.create_subprocess_exec(*command, **kwargs)

    async def _default_health_check(self, base_url: str) -> tuple[bool, str | None]:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{base_url}/system_stats")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return False, str(exc)
        return True, None

    def _default_ws_url(self, base_url: str) -> str:
        parsed = urlparse(base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, "/ws", "", "", ""))
