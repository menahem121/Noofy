"""Dependency environment installation for resolved locks.

Phase 5b keeps dependency environments immutable and derives them from resolved
wheel facts. The `UvDependencyEnvironmentInstaller` is intentionally fed a
Noofy lock rather than raw requirements so policy checks happen before `uv`
is allowed to create an environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.dependency_lock import (
    DependencyPolicyError,
    DependencyPolicyErrorCode,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyWheel,
    validate_quarantined_community_lock,
    with_computed_lock_hash,
)


class DependencyEnvironmentInstallError(RuntimeError):
    def __init__(
        self,
        code: DependencyPolicyErrorCode,
        message: str,
        *,
        command: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.command = command or []


@dataclass(frozen=True)
class DependencyEnvironmentInstallRequest:
    lock: ResolvedDependencyLock
    target_dir: Path
    python_version: str
    workflow_id: str
    python_executable: str | None = None


class DependencyEnvironmentInstaller(Protocol):
    def install(self, request: DependencyEnvironmentInstallRequest) -> None:
        """Install dependencies into request.target_dir or raise a policy/install error."""


class _CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]: ...


class UvDependencyEnvironmentInstaller:
    """Create a venv with `uv` and install only hash-verified cached wheels."""

    def __init__(
        self,
        *,
        wheel_cache_dir: Path,
        log_store: DiagnosticsSink,
        uv_cache_dir: Path | None = None,
        uv_executable: str = "uv",
        command_runner: _CommandRunner | None = None,
    ) -> None:
        self.wheel_cache_dir = wheel_cache_dir
        self.uv_cache_dir = uv_cache_dir
        self.uv_executable = uv_executable
        self.command_runner = command_runner or _run_command
        self.log_store = log_store

    def install(self, request: DependencyEnvironmentInstallRequest) -> None:
        lock = (
            request.lock
            if request.lock.lock_hash is not None
            else with_computed_lock_hash(request.lock)
        )
        try:
            self._validate_installable_lock(lock)
        except DependencyPolicyError as exc:
            raise DependencyEnvironmentInstallError(exc.code, str(exc)) from exc

        if request.target_dir.exists():
            shutil.rmtree(request.target_dir)
        request.target_dir.mkdir(parents=True)

        lock_path = request.target_dir / "noofy-dependency-lock.json"
        requirements_path = request.target_dir / "requirements.hashes.txt"
        venv_dir = request.target_dir / "venv"
        lock_path.write_text(lock.model_dump_json(indent=2), encoding="utf-8")
        requirements_path.write_text(_requirements_text(lock), encoding="utf-8")

        self._run(
            [
                self.uv_executable,
                "venv",
                "--python",
                request.python_executable or request.python_version,
                "--no-python-downloads",
                "--no-progress",
                *self._uv_cache_args(),
                str(venv_dir),
            ],
            cwd=request.target_dir,
            workflow_id=request.workflow_id,
        )
        self._run(
            [
                self.uv_executable,
                "pip",
                "install",
                "--python",
                str(_venv_python_path(venv_dir)),
                "--require-hashes",
                "--only-binary",
                ":all:",
                "--no-index",
                "--find-links",
                str(self.wheel_cache_dir),
                "--strict",
                "--no-progress",
                *self._uv_cache_args(),
                "-r",
                str(requirements_path),
            ],
            cwd=request.target_dir,
            workflow_id=request.workflow_id,
        )

    def _validate_installable_lock(self, lock: ResolvedDependencyLock) -> None:
        validate_quarantined_community_lock(lock, wheel_cache_dir=self.wheel_cache_dir)
        for wheel in lock.wheels:
            if wheel.source_kind is not DependencySourceKind.APPROVED_CACHE:
                raise DependencyEnvironmentInstallError(
                    DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
                    f"Dependency {wheel.name} must be materialized into the approved wheel cache before install.",
                )

    def _run(self, command: list[str], *, cwd: Path, workflow_id: str) -> None:
        self.log_store.add(
            "info",
            "Running dependency environment installer command",
            "runtime.dependency_env",
            workflow_id=workflow_id,
            details={"command": _redacted_command(command), "cwd": str(cwd)},
        )
        try:
            result = self.command_runner(command, cwd=cwd, env=self._command_env())
        except FileNotFoundError as exc:
            raise DependencyEnvironmentInstallError(
                DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
                "uv executable is not available for dependency environment installation.",
                command=command,
            ) from exc
        if result.returncode != 0:
            raise DependencyEnvironmentInstallError(
                DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
                _summarize_uv_failure(result),
                command=command,
            )

    def _command_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["UV_NO_PROGRESS"] = "1"
        env["UV_NO_PYTHON_DOWNLOADS"] = "1"
        if self.uv_cache_dir is not None:
            env["UV_CACHE_DIR"] = str(self.uv_cache_dir)
        return env

    def _uv_cache_args(self) -> list[str]:
        if self.uv_cache_dir is None:
            return []
        return ["--cache-dir", str(self.uv_cache_dir)]


def _requirements_text(lock: ResolvedDependencyLock) -> str:
    lines = [
        "# Generated by Noofy from a resolved dependency lock.",
        "# Do not edit by hand.",
    ]
    for wheel in sorted(
        lock.wheels, key=lambda item: (item.name, item.version, item.wheel_filename)
    ):
        lines.append(_requirement_line(wheel))
    return "\n".join(lines) + "\n"


def _requirement_line(wheel: ResolvedDependencyWheel) -> str:
    assert wheel.sha256 is not None
    marker = f"; {wheel.environment_marker}" if wheel.environment_marker else ""
    return f"{wheel.name}=={wheel.version}{marker} --hash={wheel.sha256}"


def _venv_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _summarize_uv_failure(result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stderr or result.stdout or "").strip()
    if not output:
        return f"Dependency environment installer failed with exit code {result.returncode}."
    first_line = output.splitlines()[0].strip()
    return f"Dependency environment installer failed: {first_line}"


def _redacted_command(command: list[str]) -> list[str]:
    return [part if len(part) < 240 else part[:237] + "..." for part in command]
