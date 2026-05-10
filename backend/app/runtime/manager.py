import asyncio
import contextlib
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse, urlunparse

import httpx

from app.engine.diagnostics import DiagnosticsSink
from app.engine.models import (
    ComfyUIRuntimeStatus,
    ComfyUIVersionMetadata,
    ProcessActionResult,
    RuntimeBootstrapResult,
    RuntimeMode,
)
from app.runtime.environment import RuntimeEnvironment
from app.runtime.launch_settings import comfyui_vram_args

HealthCheck = Callable[[str], Awaitable[tuple[bool, str | None]]]
ProcessFactory = Callable[..., Awaitable[Any]]
OnRestartCallback = Callable[[], None]


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
        log_store: DiagnosticsSink,
        managed_host: str = "127.0.0.1",
        managed_port: int | None = None,
        external_ws_url: str | None = None,
        startup_timeout_seconds: float = 60,
        health_poll_interval_seconds: float = 0.5,
        max_restart_attempts: int = 3,
        restart_backoff_base_seconds: float = 2.0,
        environment: RuntimeEnvironment | None = None,
        process_factory: ProcessFactory | None = None,
        health_check: HealthCheck | None = None,
        on_restart: OnRestartCallback | None = None,
        pid_dir: Path | None = None,
        managed_base_directory: Path | None = None,
        managed_output_directory: Path | None = None,
        managed_input_directory: Path | None = None,
        managed_temp_directory: Path | None = None,
        managed_user_directory: Path | None = None,
        managed_database_url: str | None = None,
        python_cache_dir: Path | None = None,
        managed_extra_model_paths_config: Path | None = None,
        managed_model_roots: list[Path] | None = None,
        version_metadata: ComfyUIVersionMetadata | None = None,
        managed_vram_mode: str = "normal",
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
        self.max_restart_attempts = max_restart_attempts
        self.restart_backoff_base_seconds = restart_backoff_base_seconds
        self.log_store = log_store
        self.environment = environment
        self._process_factory = process_factory or self._create_process
        self._health_check = health_check or self._default_health_check
        self._on_restart = on_restart
        self._pid_dir = pid_dir
        self._managed_base_directory = managed_base_directory
        self._managed_output_directory = managed_output_directory
        self._managed_input_directory = managed_input_directory
        self._managed_temp_directory = managed_temp_directory
        self._managed_user_directory = managed_user_directory
        self._managed_database_url = managed_database_url
        self._python_cache_dir = python_cache_dir
        self._managed_extra_model_paths_config = managed_extra_model_paths_config
        self._managed_model_roots = managed_model_roots or []
        self._version_metadata = version_metadata
        self.managed_vram_mode = managed_vram_mode
        comfyui_vram_args(self.managed_vram_mode)
        self._process: Any | None = None
        self._log_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._last_error: str | None = None
        self._sidecar_starting: bool = False
        self._start_lock: asyncio.Lock = asyncio.Lock()

        # Crash / restart state
        self._crash_count: int = 0
        self._restart_attempt: int = 0
        self._started_at: datetime | None = None
        self._last_crash_at: datetime | None = None
        self._stopping: bool = False

        self.external_base_url = external_base_url.rstrip("/")
        self.external_ws_url = external_ws_url
        self.base_url = (
            self.external_base_url
            if self.mode == "external"
            else self._managed_base_url()
        )
        self.ws_url = (
            self.external_ws_url
            if self.mode == "external" and self.external_ws_url
            else self._default_ws_url(self.base_url)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def status(self) -> ComfyUIRuntimeStatus:
        self._record_process_exit_if_needed()
        reachable, reachability_error = await self._health_check(self.base_url)
        environment_status = (
            await self.environment.status() if self.environment is not None else None
        )
        error = None if reachable else self._last_error or reachability_error

        uptime: float | None = None
        if self._started_at is not None and self._is_managed_process_running():
            uptime = (datetime.now(timezone.utc) - self._started_at).total_seconds()

        return ComfyUIRuntimeStatus(
            mode=self.mode,
            reachable=reachable,
            base_url=self.base_url,
            repo_dir=str(self.repo_dir),
            managed_process_running=self._is_managed_process_running(),
            sidecar_starting=self._sidecar_starting,
            pid=self._process.pid if self._is_managed_process_running() else None,
            error=error,
            environment=environment_status,
            crash_count=self._crash_count,
            restart_attempt=self._restart_attempt,
            max_restart_attempts=self.max_restart_attempts,
            uptime_seconds=uptime,
            last_crash_at=(
                self._last_crash_at.isoformat() if self._last_crash_at else None
            ),
            version=self._version_metadata,
            managed_vram_mode=self.managed_vram_mode,
            model_paths={
                "noofy_models_dir": str(self._managed_model_roots[0])
                if self._managed_model_roots
                else None,
                "external_comfyui_models_dir": str(self._managed_model_roots[1])
                if len(self._managed_model_roots) > 1
                else None,
                "extra_model_paths_config": str(self._managed_extra_model_paths_config)
                if self._managed_extra_model_paths_config
                else None,
            },
        )

    def reconfigure_managed_runtime(
        self,
        *,
        repo_dir: Path,
        python_executable: str,
        environment: RuntimeEnvironment,
        version_metadata: ComfyUIVersionMetadata | None,
    ) -> None:
        if self.mode != "managed":
            raise RuntimeError("Only managed ComfyUI runtimes can be reconfigured.")
        if self._is_managed_process_running():
            raise RuntimeError(
                "Cannot reconfigure ComfyUI while the managed process is running."
            )
        self.repo_dir = repo_dir
        self.python_executable = python_executable
        self.environment = environment
        self._version_metadata = version_metadata

    def set_managed_vram_mode(self, mode: str) -> None:
        comfyui_vram_args(mode)
        self.managed_vram_mode = mode

    def set_managed_extra_model_paths_config(self, path: Path | None) -> None:
        self._managed_extra_model_paths_config = path

    def set_managed_model_roots(self, model_roots: list[Path]) -> None:
        self._managed_model_roots = list(model_roots)

    async def start(self) -> ProcessActionResult:
        async with self._start_lock:
            return await self._start_locked()

    async def _start_locked(self) -> ProcessActionResult:
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
            return ProcessActionResult(
                status="repo_missing", comfyui=(await self.status())
            )

        if self.environment is not None:
            environment_status = await self.environment.status()
            if not environment_status.prepared:
                self._last_error = (
                    environment_status.error
                    or "ComfyUI runtime environment is not prepared"
                )
                self.log_store.add(
                    "error",
                    "ComfyUI runtime environment is not prepared",
                    "runtime.manager",
                    details={"error": self._last_error},
                )
                return ProcessActionResult(
                    status="environment_not_ready", comfyui=(await self.status())
                )

        # Clean up any orphan from a previous backend crash.
        self._cleanup_stale_pid()

        self._stopping = False
        self._restart_attempt = 0
        self._sidecar_starting = True
        try:
            if not self._is_managed_process_running():
                await self._start_process()

            started = await self._poll_until_reachable(self.startup_timeout_seconds)
            if started:
                self._last_error = None
                self._started_at = datetime.now(timezone.utc)
                status = await self.status()
                self.log_store.add(
                    "info",
                    "Managed ComfyUI started",
                    "runtime.manager",
                    details={"base_url": self.base_url, "pid": status.pid},
                )
                return ProcessActionResult(status="started", comfyui=status)

            if (
                self._process is not None
                and getattr(self._process, "returncode", None) is not None
            ):
                self._last_error = f"ComfyUI process exited during startup with code {self._process.returncode}"
                self.log_store.add(
                    "error",
                    "Managed ComfyUI startup failed",
                    "runtime.manager",
                    details={"error": self._last_error},
                )
                return ProcessActionResult(
                    status="startup_failed", comfyui=(await self.status())
                )

            self._last_error = f"ComfyUI startup timed out after {self.startup_timeout_seconds:g} seconds"
            self.log_store.add(
                "error",
                "Managed ComfyUI startup timed out",
                "runtime.manager",
                details={"error": self._last_error},
            )
            await self._stop_managed_process()
            return ProcessActionResult(
                status="startup_timeout", comfyui=(await self.status())
            )
        finally:
            self._sidecar_starting = False

    async def stop(self) -> ProcessActionResult:
        if self.mode == "external":
            self.log_store.add(
                "warning",
                "ComfyUI stop requested in external runtime mode",
                "runtime.manager",
            )
            return ProcessActionResult(
                status="not_managed", comfyui=await self.status()
            )

        if not self._is_managed_process_running():
            self.log_store.add(
                "warning",
                "ComfyUI stop requested but no managed process is running",
                "runtime.manager",
            )
            return ProcessActionResult(
                status="not_running", comfyui=await self.status()
            )

        self._stopping = True
        await self._stop_managed_process()
        self._last_error = None
        self._started_at = None
        self.log_store.add("info", "Managed ComfyUI stopped", "runtime.manager")
        return ProcessActionResult(status="stopped", comfyui=await self.status())

    async def bootstrap_environment(self) -> RuntimeBootstrapResult:
        if self.environment is None:
            status = await self.status()
            self.log_store.add(
                "warning",
                "No managed runtime environment is configured",
                "runtime.manager",
            )
            return RuntimeBootstrapResult(
                status="not_configured",
                environment=status.environment,
            )
        return await self.environment.bootstrap()

    def is_managed_process_running(self) -> bool:
        return self._is_managed_process_running()

    # ------------------------------------------------------------------
    # Process lifecycle
    # ------------------------------------------------------------------

    async def _start_process(self, *, start_watchdog: bool = True) -> None:
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
            "--disable-auto-launch",
            "--dont-print-server",
        ]
        command.extend(self._managed_vram_args())
        command.extend(self._managed_path_args())
        process_env = self._managed_process_env()
        self._process = await self._process_factory(
            command,
            cwd=self.repo_dir,
            env=process_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._last_error = None
        self.log_store.add(
            "info",
            "Managed ComfyUI process started",
            "runtime.manager",
            details={
                "pid": self._process.pid,
                "host": self.managed_host,
                "port": self.managed_port,
            },
        )
        self._write_pid_file(self._process.pid)
        self._log_task = asyncio.create_task(
            self._capture_process_output(self._process)
        )
        if start_watchdog:
            self._start_watchdog()

    async def _stop_managed_process(self) -> None:
        # Cancel watchdog first to prevent restart during teardown.
        self._cancel_watchdog()
        await self._cleanup_process()

    async def _cleanup_process(self) -> None:
        """Terminate/kill the process and clean up. Does NOT cancel the watchdog."""
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
        self._remove_pid_file()

    # ------------------------------------------------------------------
    # Crash watchdog
    # ------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        self._cancel_watchdog()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    def _cancel_watchdog(self) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                pass  # Fire and forget – the task will clean up.
            self._watchdog_task = None

    async def _watchdog(self) -> None:
        """Await the managed process; on unexpected exit, attempt restart."""
        process = self._process
        if process is None:
            return

        try:
            await process.wait()
        except asyncio.CancelledError:
            return

        # Process has exited.
        returncode = getattr(process, "returncode", None)

        # If we are stopping intentionally, do not restart.
        if self._stopping:
            return

        self._crash_count += 1
        self._last_crash_at = datetime.now(timezone.utc)
        self._record_process_exit_if_needed()
        self.log_store.add(
            "error",
            "Managed ComfyUI crashed",
            "runtime.manager",
            details={
                "returncode": returncode,
                "crash_count": self._crash_count,
                "restart_attempt": self._restart_attempt,
            },
        )

        # Attempt controlled restart.
        while self._restart_attempt < self.max_restart_attempts and not self._stopping:
            self._restart_attempt += 1
            delay = self.restart_backoff_base_seconds * (
                2 ** (self._restart_attempt - 1)
            )
            self.log_store.add(
                "info",
                f"Scheduling ComfyUI restart (attempt {self._restart_attempt}/{self.max_restart_attempts})",
                "runtime.manager",
                details={"delay_seconds": delay},
            )

            try:
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                return

            if self._stopping:
                return

            # Clean up the dead process state.
            self._process = None
            self._remove_pid_file()

            try:
                await self._start_process(start_watchdog=False)
            except Exception as exc:
                self.log_store.add(
                    "error",
                    "ComfyUI restart process spawn failed",
                    "runtime.manager",
                    details={
                        "error": str(exc),
                        "restart_attempt": self._restart_attempt,
                    },
                )
                continue

            reachable = await self._poll_until_reachable(self.startup_timeout_seconds)
            if reachable:
                self._last_error = None
                self._started_at = datetime.now(timezone.utc)
                self._restart_attempt = 0  # Reset attempt counter on success.
                self.log_store.add(
                    "info",
                    "Managed ComfyUI restarted successfully",
                    "runtime.manager",
                    details={
                        "base_url": self.base_url,
                        "pid": self._process.pid if self._process else None,
                        "crash_count": self._crash_count,
                    },
                )
                # Notify the engine service to reconfigure adapter endpoint.
                if self._on_restart is not None:
                    self._on_restart()
                # Start a fresh watchdog for the newly spawned process.
                self._start_watchdog()
                return

            # Restart attempt failed – loop to next attempt.
            self.log_store.add(
                "error",
                "ComfyUI restart failed (not reachable after startup timeout)",
                "runtime.manager",
                details={"restart_attempt": self._restart_attempt},
            )
            await self._cleanup_process()

        # Exhausted all restart attempts.
        if not self._stopping:
            self._last_error = f"ComfyUI crashed and exhausted {self.max_restart_attempts} restart attempts"
            self.log_store.add(
                "error",
                "ComfyUI restart attempts exhausted",
                "runtime.manager",
                details={
                    "crash_count": self._crash_count,
                    "max_restart_attempts": self.max_restart_attempts,
                },
            )

    # ------------------------------------------------------------------
    # PID file management
    # ------------------------------------------------------------------

    @property
    def _pid_file(self) -> Path | None:
        if self._pid_dir is None:
            return None
        return self._pid_dir / "comfyui.pid"

    def _write_pid_file(self, pid: int) -> None:
        pid_file = self._pid_file
        if pid_file is None:
            return
        try:
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(pid), encoding="utf-8")
        except OSError:
            pass

    def _remove_pid_file(self) -> None:
        pid_file = self._pid_file
        if pid_file is None:
            return
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    def _cleanup_stale_pid(self) -> None:
        """Kill an orphan ComfyUI process left by a previous backend crash."""
        pid_file = self._pid_file
        if pid_file is None or not pid_file.exists():
            return
        try:
            stale_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            self._remove_pid_file()
            return

        if self._is_pid_alive(stale_pid):
            self.log_store.add(
                "warning",
                "Killing orphan ComfyUI process from previous run",
                "runtime.manager",
                details={"pid": stale_pid},
            )
            try:
                os.kill(stale_pid, signal.SIGTERM)
            except OSError:
                pass
        self._remove_pid_file()

    @staticmethod
    def _is_pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # Process exists but we can't signal it.
        return True

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _capture_process_output(self, process: Any) -> None:
        stream = getattr(process, "stdout", None)
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
                    "debug", self._redact_local_paths(text), "comfyui.stdout"
                )

    async def _poll_until_reachable(self, timeout_seconds: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            self._record_process_exit_if_needed()
            if (
                self._process is not None
                and getattr(self._process, "returncode", None) is not None
            ):
                return False

            reachable, _ = await self._health_check(self.base_url)
            if reachable:
                return True

            await asyncio.sleep(self.health_poll_interval_seconds)
        return False

    def _redact_local_paths(self, text: str) -> str:
        return text.replace(str(Path.home()), "~")

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

    def _managed_path_args(self) -> list[str]:
        args: list[str] = []
        path_args = (
            ("--base-directory", self._managed_base_directory),
            ("--output-directory", self._managed_output_directory),
            ("--input-directory", self._managed_input_directory),
            ("--temp-directory", self._managed_temp_directory),
            ("--user-directory", self._managed_user_directory),
        )
        for flag, path in path_args:
            if path is not None:
                args.extend([flag, str(path)])
        if self._managed_database_url:
            args.extend(["--database-url", self._managed_database_url])
        if self._managed_extra_model_paths_config is not None:
            args.extend(["--extra-model-paths-config", str(self._managed_extra_model_paths_config)])
        return args

    def _managed_vram_args(self) -> list[str]:
        return comfyui_vram_args(self.managed_vram_mode)

    def _managed_process_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self._python_cache_dir is not None:
            env["PYTHONPYCACHEPREFIX"] = str(self._python_cache_dir)
        return env

    async def _create_process(
        self, command: list[str], **kwargs: Any
    ) -> asyncio.subprocess.Process:
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
