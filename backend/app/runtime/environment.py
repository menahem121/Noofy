import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.diagnostics import DiagnosticsSink
from app.engine.models import (
    RuntimeBootstrapResult,
    RuntimeDependencyStatus,
    RuntimeEnvironmentStatus,
    RuntimeHardwareProfile,
    TorchInstallPlan,
)
from app.runtime.hardware import (
    UNSUPPORTED_MACOS_INTEL_ACCELERATOR,
    detect_hardware,
    plan_torch_install,
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[[list[str], Path | None], Awaitable[CommandResult]]

_REQUIRED_RUNTIME_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "torch.library.custom_op",
        "import torch\n"
        "if not hasattr(torch.library, 'custom_op'):\n"
        "    raise RuntimeError('torch.library.custom_op is required by comfy-kitchen')",
    ),
    ("comfy_kitchen", "import comfy_kitchen"),
)


class RuntimeEnvironment:
    def __init__(
        self,
        *,
        repo_dir: Path,
        runtime_dir: Path,
        log_store: DiagnosticsSink,
        bootstrap_python_executable: str = "python3",
        python_executable_override: str | None = None,
        expected_python_version: str | None = None,
        packaged_runtime: bool = False,
        required_imports: tuple[str, ...] = ("torch", "aiohttp"),
        required_runtime_checks: tuple[tuple[str, str], ...] = _REQUIRED_RUNTIME_CHECKS,
        torch_cuda_index_url: str | None = None,
        torch_cpu_index_url: str = "https://download.pytorch.org/whl/cpu",
        hardware_profile: RuntimeHardwareProfile | None = None,
        command_runner: CommandRunner | None = None,
        logs_dir: Path | None = None,
        cache_dir: Path | None = None,
        venv_dir_override: Path | None = None,
    ) -> None:
        self.repo_dir = repo_dir
        self.runtime_dir = runtime_dir
        self._logs_dir = logs_dir
        self._cache_dir = cache_dir
        self._venv_dir_override = venv_dir_override
        self.bootstrap_python_executable = bootstrap_python_executable
        self.python_executable_override = python_executable_override
        self.expected_python_version = expected_python_version
        self.packaged_runtime = packaged_runtime
        self.required_imports = required_imports
        self.required_runtime_checks = required_runtime_checks
        self.torch_cuda_index_url = torch_cuda_index_url
        self.torch_cpu_index_url = torch_cpu_index_url
        self._hardware_profile = hardware_profile
        self.log_store = log_store
        self._command_runner = command_runner or self._run_command
        self._last_bootstrap_python_attempts: list[dict[str, object]] = []
        self._bootstrap_status_label: str | None = None

    @property
    def bootstrap_status_label(self) -> str | None:
        return self._bootstrap_status_label

    @property
    def requirements_file(self) -> Path:
        return self.repo_dir / "requirements.txt"

    @property
    def main_py(self) -> Path:
        return self.repo_dir / "main.py"

    @property
    def venv_dir(self) -> Path:
        if self._venv_dir_override is not None:
            return self._venv_dir_override
        return self.runtime_dir / "comfyui-venv"

    @property
    def log_dir(self) -> Path:
        return (
            self._logs_dir if self._logs_dir is not None else self.runtime_dir / "logs"
        )

    @property
    def cache_dir(self) -> Path:
        return (
            self._cache_dir
            if self._cache_dir is not None
            else self.runtime_dir / "cache"
        )

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
        hardware = await self._detect_hardware()
        torch_install_plan = plan_torch_install(
            hardware,
            cuda_index_url=self.torch_cuda_index_url,
            cpu_index_url=self.torch_cpu_index_url,
        )
        python_version = (
            await self._detect_python_version(self.python_executable)
            if python_exists
            else None
        )
        python_version_matches = (
            self.expected_python_version is None
            or python_version == self.expected_python_version
        )
        dependencies = (
            await self._check_dependencies()
            if python_exists
            and python_version_matches
            and not _torch_install_plan_is_unsupported(torch_install_plan)
            else []
        )
        error = self._status_error(
            runtime_dir_writable,
            python_exists,
            python_version,
            python_version_matches,
            dependencies,
            torch_install_plan=torch_install_plan,
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
            runtime_distribution=(
                "packaged" if self.packaged_runtime else "source_checkout"
            ),
            bootstrap_python_executable=self.bootstrap_python_executable,
            bootstrap_python_attempts=list(self._last_bootstrap_python_attempts),
            python_executable=self.python_executable,
            python_exists=python_exists,
            python_version=python_version,
            expected_python_version=self.expected_python_version,
            python_version_matches=python_version_matches,
            hardware=hardware,
            torch_install_plan=torch_install_plan,
            dependencies=dependencies,
            prepared=error is None,
            error=error,
        )

    async def bootstrap(self) -> RuntimeBootstrapResult:
        self._bootstrap_status_label = "Checking ComfyUI engine requirements"
        try:
            current = await self.status()
            if current.prepared:
                self.log_store.add(
                    "info",
                    "ComfyUI runtime environment already prepared",
                    "runtime.environment",
                )
                return RuntimeBootstrapResult(
                    status="already_prepared", environment=current
                )

            if not current.requirements_file_exists:
                self.log_store.add(
                    "error",
                    "ComfyUI requirements file missing",
                    "runtime.environment",
                    details={"requirements_file": current.requirements_file},
                )
                return RuntimeBootstrapResult(
                    status="requirements_missing", environment=current
                )

            if _torch_install_plan_is_unsupported(current.torch_install_plan):
                self.log_store.add(
                    "error",
                    "ComfyUI managed runtime is not supported on this platform",
                    "runtime.environment",
                    details={"reason": current.torch_install_plan.reason},
                )
                return RuntimeBootstrapResult(
                    status="platform_unsupported", environment=current
                )

            if self.python_executable_override:
                self.log_store.add(
                    "error",
                    "Noofy engine runtime override is not prepared",
                    "runtime.environment",
                    details={
                        "python_executable": self.python_executable_override,
                        "error": current.error,
                    },
                )
                return RuntimeBootstrapResult(
                    status="python_not_prepared", environment=current
                )

            self._bootstrap_status_label = "Finding bundled Python runtime"
            bootstrap_python = await self._resolve_bootstrap_python_executable()
            if bootstrap_python is None:
                error = self._bootstrap_python_missing_message()
                log_details: dict[str, object] = {
                    "configured_python_executable": self.bootstrap_python_executable,
                    "expected_python_version": self.expected_python_version,
                    "runtime_distribution": (
                        "packaged" if self.packaged_runtime else "source_checkout"
                    ),
                    "attempts": self._last_bootstrap_python_attempts,
                }
                if self.packaged_runtime:
                    log_message = (
                        "Packaged Noofy bundled Python does not match the "
                        "selected runtime profile"
                    )
                else:
                    log_message = (
                        "Source-checkout ComfyUI bootstrap Python does not "
                        "match the selected runtime profile"
                    )
                self.log_store.add(
                    "error",
                    log_message,
                    "runtime.environment",
                    details=log_details,
                )
                return RuntimeBootstrapResult(
                    status="python_missing",
                    environment=current.model_copy(
                        update={
                            "error": error,
                            "bootstrap_python_attempts": list(
                                self._last_bootstrap_python_attempts
                            ),
                        }
                    ),
                )

            self._bootstrap_status_label = "Preparing ComfyUI runtime folders"
            self._ensure_writable_runtime_dirs()
            if (
                current.python_exists
                and self.expected_python_version is not None
                and current.python_version != self.expected_python_version
            ):
                self.log_store.add(
                    "info",
                    "Rebuilding ComfyUI runtime environment for selected profile Python",
                    "runtime.environment",
                    details={
                        "python_executable": current.python_executable,
                        "current_python_version": current.python_version,
                        "expected_python_version": self.expected_python_version,
                    },
                )
                shutil.rmtree(self.venv_dir, ignore_errors=True)
            venv_result = await self._run_logged(
                [bootstrap_python, "-m", "venv", str(self.venv_dir)],
                cwd=None,
                action="Create ComfyUI runtime virtual environment",
            )
            if venv_result.returncode != 0:
                return RuntimeBootstrapResult(
                    status="bootstrap_failed", environment=await self.status()
                )

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
                return RuntimeBootstrapResult(
                    status="bootstrap_failed", environment=await self.status()
                )

            install_result = await self._run_logged(
                [
                    self.python_executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    str(self.requirements_file),
                ],
                cwd=self.repo_dir,
                action="Install ComfyUI runtime requirements",
            )
            if install_result.returncode != 0:
                return RuntimeBootstrapResult(
                    status="bootstrap_failed", environment=await self.status()
                )

            self._bootstrap_status_label = "Checking installed ComfyUI components"
            prepared = await self.status()
            if prepared.prepared:
                self.log_store.add(
                    "info", "ComfyUI runtime environment prepared", "runtime.environment"
                )
                return RuntimeBootstrapResult(status="prepared", environment=prepared)

            self.log_store.add(
                "error",
                "ComfyUI runtime dependency check failed after bootstrap",
                "runtime.environment",
                details={"error": prepared.error},
            )
            return RuntimeBootstrapResult(
                status="dependency_check_failed", environment=prepared
            )
        finally:
            self._bootstrap_status_label = None

    def _status_error(
        self,
        runtime_dir_writable: bool,
        python_exists: bool,
        python_version: str | None,
        python_version_matches: bool,
        dependencies: list[RuntimeDependencyStatus],
        *,
        torch_install_plan: TorchInstallPlan,
    ) -> str | None:
        if not self.repo_dir.exists():
            return f"Noofy could not find the bundled ComfyUI engine files: {self.repo_dir}"
        if not self.main_py.exists():
            return f"Noofy could not find the bundled ComfyUI engine entrypoint: {self.repo_dir}"
        if not self.requirements_file.exists():
            return f"Noofy could not find the bundled engine requirements: {self.repo_dir}"
        if not runtime_dir_writable:
            return f"Noofy cannot write to its engine runtime folder: {self.runtime_dir}"
        if _torch_install_plan_is_unsupported(torch_install_plan):
            return torch_install_plan.reason
        if not python_exists:
            return f"Noofy could not find its prepared engine runtime: {self.python_executable}"
        if not python_version_matches:
            if python_version is None:
                return (
                    "Noofy could not inspect its managed ComfyUI Python version. "
                    f"The selected runtime profile requires Python {self.expected_python_version}."
                )
            return (
                "Noofy's managed ComfyUI runtime uses Python "
                f"{python_version}, but the selected runtime profile requires "
                f"Python {self.expected_python_version}."
            )

        missing = [
            dependency for dependency in dependencies if not dependency.available
        ]
        if missing:
            names = ", ".join(dependency.name for dependency in missing)
            return f"Noofy's engine runtime is missing required components: {names}"
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
                    error=(
                        self._redact_local_paths(result.stderr or result.stdout)
                        if result.returncode != 0
                        else None
                    ),
                )
            )
        for check_name, check_code in self.required_runtime_checks:
            result = await self._command_runner(
                [self.python_executable, "-c", check_code],
                None,
            )
            dependencies.append(
                RuntimeDependencyStatus(
                    name=check_name,
                    available=result.returncode == 0,
                    error=(
                        self._redact_local_paths(result.stderr or result.stdout)
                        if result.returncode != 0
                        else None
                    ),
                )
            )
        return dependencies

    async def _detect_python_version(self, executable: str) -> str | None:
        result = await self._command_runner(
            [
                executable,
                "-c",
                "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
            ],
            None,
        )
        if result.returncode != 0:
            return None
        version = result.stdout.strip()
        parts = version.split(".")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            return None
        return version

    async def _resolve_bootstrap_python_executable(self) -> str | None:
        self._last_bootstrap_python_attempts = []
        if self.expected_python_version is None:
            if self._executable_exists(self.bootstrap_python_executable):
                return self.bootstrap_python_executable
            self._last_bootstrap_python_attempts.append(
                {
                    "python_executable": self.bootstrap_python_executable,
                    "exists": False,
                    "python_version": None,
                    "expected_python_version": None,
                }
            )
            return None

        mismatches: list[dict[str, object]] = []
        for candidate in self._bootstrap_python_candidates():
            if not self._executable_exists(candidate):
                self._last_bootstrap_python_attempts.append(
                    {
                        "python_executable": candidate,
                        "exists": False,
                        "python_version": None,
                        "expected_python_version": self.expected_python_version,
                    }
                )
                continue
            version = await self._detect_python_version(candidate)
            attempt = {
                "python_executable": candidate,
                "exists": True,
                "python_version": version,
                "expected_python_version": self.expected_python_version,
            }
            self._last_bootstrap_python_attempts.append(attempt)
            if version == self.expected_python_version:
                return candidate
            mismatches.append(attempt)
        if mismatches:
            self.log_store.add(
                "warning",
                "Rejected ComfyUI bootstrap Python candidates with the wrong ABI",
                "runtime.environment",
                details={"candidates": mismatches},
            )
        return None

    def _bootstrap_python_missing_message(self) -> str:
        tried = _format_bootstrap_attempts(self._last_bootstrap_python_attempts)
        if self.expected_python_version is None:
            return (
                "Noofy could not find the Python executable configured for managed "
                f"ComfyUI runtime preparation. Tried: {tried}."
            )
        if self.packaged_runtime:
            return (
                "Packaged Noofy is missing a bundled Python runtime that matches "
                f"the selected managed ComfyUI runtime profile. Required Python "
                f"{self.expected_python_version}. Tried: {tried}. Reinstall or "
                "update Noofy; normal users should not install Python manually."
            )
        return (
            f"This source checkout needs Python {self.expected_python_version} "
            "for managed ComfyUI runtime preparation. Tried: "
            f"{tried}. Recommended source/dev fix: use uv-managed Python, or set "
            "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE to a Python "
            f"{self.expected_python_version} executable. Then rerun make install "
            "or make run."
        )

    def _bootstrap_python_candidates(self) -> list[str]:
        if self.expected_python_version is None:
            return [self.bootstrap_python_executable]
        if self.packaged_runtime:
            return [self.bootstrap_python_executable]
        profile_python = f"python{self.expected_python_version}"
        configured = self.bootstrap_python_executable
        if _is_generic_python_executable(configured):
            candidates = [profile_python, configured]
        else:
            candidates = [configured, profile_python]
        unique: list[str] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
        return unique

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
        self._bootstrap_status_label = action
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
            # Use the noofy cache dir as TMPDIR so that large pip wheel downloads
            # (e.g. PyTorch CUDA) don't exhaust the system /tmp tmpfs quota.
            env = dict(os.environ)
            pip_tmp = self.cache_dir / "pip-tmp"
            pip_tmp.mkdir(parents=True, exist_ok=True)
            env["TMPDIR"] = str(pip_tmp)
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
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

    def _redact_local_paths(self, text: str) -> str | None:
        if not text:
            return None
        home = str(Path.home())
        return text.replace(home, "~")


def _torch_install_plan_is_unsupported(plan: TorchInstallPlan) -> bool:
    return (
        plan.accelerator == UNSUPPORTED_MACOS_INTEL_ACCELERATOR
        or not plan.packages
    )


def _is_generic_python_executable(executable: str) -> bool:
    name = Path(executable).name.lower()
    return name in {"python", "python.exe", "python3", "python3.exe"}


def _format_bootstrap_attempts(attempts: list[dict[str, object]]) -> str:
    if not attempts:
        return "<none>"
    formatted: list[str] = []
    for attempt in attempts:
        executable = str(attempt.get("python_executable") or "<unknown>")
        if not attempt.get("exists"):
            formatted.append(f"{executable} (missing)")
            continue
        version = attempt.get("python_version") or "unknown version"
        formatted.append(f"{executable} ({version})")
    return ", ".join(formatted)
