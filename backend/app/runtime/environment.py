import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.engine.diagnostics import LogStore
from app.engine.models import (
    RuntimeBootstrapResult,
    RuntimeDependencyStatus,
    RuntimeEnvironmentStatus,
    RuntimeHardwareProfile,
)
from app.runtime.hardware import detect_hardware, plan_torch_install


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[list[str], Path | None], Awaitable[CommandResult]]


class RuntimeEnvironment:
    def __init__(
        self,
        *,
        repo_dir: Path,
        runtime_dir: Path,
        bootstrap_python_executable: str = "python3",
        python_executable_override: str | None = None,
        required_imports: tuple[str, ...] = ("torch", "aiohttp"),
        torch_cuda_index_url: str | None = None,
        torch_cpu_index_url: str = "https://download.pytorch.org/whl/cpu",
        hardware_profile: RuntimeHardwareProfile | None = None,
        log_store: LogStore | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.repo_dir = repo_dir
        self.runtime_dir = runtime_dir
        self.bootstrap_python_executable = bootstrap_python_executable
        self.python_executable_override = python_executable_override
        self.required_imports = required_imports
        self.torch_cuda_index_url = torch_cuda_index_url
        self.torch_cpu_index_url = torch_cpu_index_url
        self._hardware_profile = hardware_profile
        self.log_store = log_store or LogStore()
        self._command_runner = command_runner or self._run_command

    @property
    def requirements_file(self) -> Path:
        return self.repo_dir / "requirements.txt"

    @property
    def main_py(self) -> Path:
        return self.repo_dir / "main.py"

    @property
    def venv_dir(self) -> Path:
        return self.runtime_dir / "comfyui-venv"

    @property
    def log_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.runtime_dir / "cache"

    @property
    def python_executable(self) -> str:
        if self.python_executable_override:
            return self.python_executable_override
        if os.name == "nt":
            return str(self.venv_dir / "Scripts" / "python.exe")
        return str(self.venv_dir / "bin" / "python")

    async def status(self) -> RuntimeEnvironmentStatus:
        runtime_dir_writable = self._ensure_writable_runtime_dirs()
        python_exists = self._executable_exists(self.python_executable)
        dependencies = await self._check_dependencies() if python_exists else []
        error = self._status_error(runtime_dir_writable, python_exists, dependencies)
        hardware = await self._detect_hardware()
        torch_install_plan = plan_torch_install(
            hardware,
            cuda_index_url=self.torch_cuda_index_url,
            cpu_index_url=self.torch_cpu_index_url,
        )

        return RuntimeEnvironmentStatus(
            repo_dir=str(self.repo_dir),
            main_py_exists=self.main_py.exists(),
            requirements_file=str(self.requirements_file),
            requirements_file_exists=self.requirements_file.exists(),
            runtime_dir=str(self.runtime_dir),
            runtime_dir_writable=runtime_dir_writable,
            log_dir=str(self.log_dir),
            cache_dir=str(self.cache_dir),
            python_executable=self.python_executable,
            python_exists=python_exists,
            hardware=hardware,
            torch_install_plan=torch_install_plan,
            dependencies=dependencies,
            prepared=error is None,
            error=error,
        )

    async def bootstrap(self) -> RuntimeBootstrapResult:
        current = await self.status()
        if current.prepared:
            self.log_store.add("info", "ComfyUI runtime environment already prepared", "runtime.environment")
            return RuntimeBootstrapResult(status="already_prepared", environment=current)

        if not current.requirements_file_exists:
            self.log_store.add(
                "error",
                "ComfyUI requirements file missing",
                "runtime.environment",
                details={"requirements_file": current.requirements_file},
            )
            return RuntimeBootstrapResult(status="requirements_missing", environment=current)

        if self.python_executable_override:
            self.log_store.add(
                "error",
                "Runtime Python override is not prepared",
                "runtime.environment",
                details={"python_executable": self.python_executable_override, "error": current.error},
            )
            return RuntimeBootstrapResult(status="python_not_prepared", environment=current)

        if not self._executable_exists(self.bootstrap_python_executable):
            self.log_store.add(
                "error",
                "Bootstrap Python executable missing",
                "runtime.environment",
                details={"python_executable": self.bootstrap_python_executable},
            )
            return RuntimeBootstrapResult(status="python_missing", environment=current)

        self._ensure_writable_runtime_dirs()
        venv_result = await self._run_logged(
            [self.bootstrap_python_executable, "-m", "venv", str(self.venv_dir)],
            cwd=None,
            action="Create ComfyUI runtime virtual environment",
        )
        if venv_result.returncode != 0:
            return RuntimeBootstrapResult(status="bootstrap_failed", environment=await self.status())

        torch_plan = (await self.status()).torch_install_plan
        torch_result = await self._run_logged(
            [
                self.python_executable,
                "-m",
                "pip",
                "install",
                *torch_plan.pip_args,
                *torch_plan.packages,
            ],
            cwd=None,
            action=f"Install PyTorch runtime ({torch_plan.accelerator})",
            details={
                "accelerator": torch_plan.accelerator,
                "reason": torch_plan.reason,
                "warnings": torch_plan.warnings,
            },
        )
        if torch_result.returncode != 0:
            return RuntimeBootstrapResult(status="bootstrap_failed", environment=await self.status())

        install_result = await self._run_logged(
            [self.python_executable, "-m", "pip", "install", "-r", str(self.requirements_file)],
            cwd=self.repo_dir,
            action="Install ComfyUI runtime requirements",
        )
        if install_result.returncode != 0:
            return RuntimeBootstrapResult(status="bootstrap_failed", environment=await self.status())

        prepared = await self.status()
        if prepared.prepared:
            self.log_store.add("info", "ComfyUI runtime environment prepared", "runtime.environment")
            return RuntimeBootstrapResult(status="prepared", environment=prepared)

        self.log_store.add(
            "error",
            "ComfyUI runtime dependency check failed after bootstrap",
            "runtime.environment",
            details={"error": prepared.error},
        )
        return RuntimeBootstrapResult(status="dependency_check_failed", environment=prepared)

    def _status_error(
        self,
        runtime_dir_writable: bool,
        python_exists: bool,
        dependencies: list[RuntimeDependencyStatus],
    ) -> str | None:
        if not self.repo_dir.exists():
            return f"ComfyUI repo not found: {self.repo_dir}"
        if not self.main_py.exists():
            return f"ComfyUI main.py not found in: {self.repo_dir}"
        if not self.requirements_file.exists():
            return f"ComfyUI requirements.txt not found in: {self.repo_dir}"
        if not runtime_dir_writable:
            return f"Runtime directory is not writable: {self.runtime_dir}"
        if not python_exists:
            return f"Runtime Python executable not found: {self.python_executable}"

        missing = [dependency for dependency in dependencies if not dependency.available]
        if missing:
            names = ", ".join(dependency.name for dependency in missing)
            return f"Runtime Python is missing required imports: {names}"
        return None

    async def _check_dependencies(self) -> list[RuntimeDependencyStatus]:
        dependencies: list[RuntimeDependencyStatus] = []
        for import_name in self.required_imports:
            result = await self._command_runner(
                [self.python_executable, "-c", f"import {import_name}"],
                None,
            )
            dependencies.append(
                RuntimeDependencyStatus(
                    name=import_name,
                    available=result.returncode == 0,
                    error=(result.stderr or result.stdout or None) if result.returncode != 0 else None,
                )
            )
        return dependencies

    async def _detect_hardware(self) -> RuntimeHardwareProfile:
        if self._hardware_profile is None:
            self._hardware_profile = await detect_hardware(self._command_runner)
        return self._hardware_profile

    async def _run_logged(
        self,
        command: list[str],
        *,
        cwd: Path | None,
        action: str,
        details: dict[str, object] | None = None,
    ) -> CommandResult:
        log_details = {"command": self._redacted_command(command)}
        if details is not None:
            log_details.update(details)
        self.log_store.add(
            "info",
            action,
            "runtime.environment",
            details=log_details,
        )
        result = await self._command_runner(command, cwd)
        for line in result.stdout.splitlines():
            self.log_store.add("debug", line, "runtime.environment.stdout")
        for line in result.stderr.splitlines():
            self.log_store.add("debug", line, "runtime.environment.stderr")
        if result.returncode != 0:
            self.log_store.add(
                "error",
                f"{action} failed",
                "runtime.environment",
                details={"returncode": result.returncode},
            )
        return result

    async def _run_command(self, command: list[str], cwd: Path | None) -> CommandResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return CommandResult(returncode=127, stderr=str(exc))
        stdout, stderr = await process.communicate()
        return CommandResult(
            returncode=process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )

    def _ensure_writable_runtime_dirs(self) -> bool:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            probe = self.runtime_dir / ".write-test"
            probe.write_text("", encoding="utf-8")
            probe.unlink(missing_ok=True)
        except OSError:
            return False
        return True

    def _executable_exists(self, executable: str) -> bool:
        path = Path(executable)
        if path.is_absolute() or path.parent != Path("."):
            return path.exists()
        return shutil.which(executable) is not None

    def _redacted_command(self, command: list[str]) -> list[str]:
        home = str(Path.home())
        return [part.replace(home, "~") for part in command]
