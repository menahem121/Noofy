"""Dependency environment installation for resolved locks.

Phase 5b keeps dependency environments immutable and derives them from resolved
registry facts. The `UvDependencyEnvironmentInstaller` is intentionally fed a
Noofy lock rather than raw requirements so policy checks happen before `uv`
is allowed to create an environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.accelerator_policy import BUILD_BLOCKED_PACKAGES
from app.runtime.dependencies.dependency_lock import (
    DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    DependencyPolicyError,
    DependencyPolicyErrorCode,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyWheel,
    normalize_package_name,
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
        output: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.command = command or []
        self.output = output


@dataclass(frozen=True)
class DependencyEnvironmentInstallRequest:
    lock: ResolvedDependencyLock
    target_dir: Path
    python_version: str
    workflow_id: str
    python_executable: str | None = None
    transaction_root: Path | None = None


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
    """Create a staged dependency overlay from a Noofy-owned resolved lock."""

    def __init__(
        self,
        *,
        wheel_cache_dir: Path,
        log_store: DiagnosticsSink,
        uv_executable: str = "uv",
        command_runner: _CommandRunner | None = None,
    ) -> None:
        self.wheel_cache_dir = wheel_cache_dir
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
        is_v2_lock = (
            lock.install_policy_version == DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION
            and not lock.wheels
        )
        requirements_path = request.target_dir / (
            "requirements.lock" if is_v2_lock else "requirements.hashes.txt"
        )
        excludes_path = request.target_dir / "runtime-excludes.txt"
        build_constraints_path = request.target_dir / "build-constraints.txt"
        diagnostics_dir = request.target_dir / "diagnostics"
        venv_dir = request.target_dir / "venv"
        lock_path.write_text(lock.model_dump_json(indent=2), encoding="utf-8")
        requirements_path.write_text(
            lock.requirements_lock
            if is_v2_lock
            else _requirements_text(lock),
            encoding="utf-8",
        )
        excludes_path.write_text(
            "\n".join(lock.runtime_excludes)
            + ("\n" if lock.runtime_excludes else ""),
            encoding="utf-8",
        )
        build_constraints_path.write_text(
            lock.build_constraints or "", encoding="utf-8"
        )
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        if request.transaction_root is not None:
            resolver_diagnostics = (
                request.transaction_root / "dependency-resolution" / "diagnostics"
            )
            if resolver_diagnostics.is_dir():
                shutil.copytree(
                    resolver_diagnostics,
                    diagnostics_dir / "resolution",
                    dirs_exist_ok=True,
                )

        self._run(
            [
                self.uv_executable,
                "venv",
                "--python",
                request.python_executable or request.python_version,
                "--no-python-downloads",
                "--no-progress",
                str(venv_dir),
            ],
            cwd=request.target_dir,
            workflow_id=request.workflow_id,
            transaction_root=request.transaction_root,
            diagnostics_dir=diagnostics_dir,
            phase="create-venv",
        )
        install_command = self._install_command(
            lock,
            venv_dir=venv_dir,
            requirements_path=requirements_path,
            excludes_path=excludes_path,
            build_constraints_path=build_constraints_path,
        )
        self._run(
            install_command,
            cwd=request.target_dir,
            workflow_id=request.workflow_id,
            transaction_root=request.transaction_root,
            diagnostics_dir=diagnostics_dir,
            phase="install",
        )
        self._validate_build_cache(request.transaction_root or request.target_dir)
        self._validate_overlay(lock, venv_dir)
        _write_installed_inventory(
            lock,
            venv_dir=venv_dir,
            path=request.target_dir / "installed-dependencies.json",
        )

    def _validate_installable_lock(self, lock: ResolvedDependencyLock) -> None:
        validate_quarantined_community_lock(
            lock,
            wheel_cache_dir=(
                None
                if (
                    lock.install_policy_version
                    == DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION
                    and not lock.wheels
                )
                else self.wheel_cache_dir
            ),
        )
        if (
            lock.install_policy_version
            == DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION
            and not lock.wheels
        ):
            return
        for wheel in lock.wheels:
            if wheel.source_kind is not DependencySourceKind.APPROVED_CACHE:
                raise DependencyEnvironmentInstallError(
                    DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
                    f"Dependency {wheel.name} must be materialized into the approved wheel cache before install.",
                )

    def _install_command(
        self,
        lock: ResolvedDependencyLock,
        *,
        venv_dir: Path,
        requirements_path: Path,
        excludes_path: Path,
        build_constraints_path: Path,
    ) -> list[str]:
        if (
            lock.install_policy_version != DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION
            or lock.wheels
        ):
            return [
                self.uv_executable,
                "pip",
                "install",
                "--python",
                str(_venv_python_path(venv_dir)),
                "--require-hashes",
                "--only-binary",
                ":all:",
                "--no-deps",
                "--no-index",
                "--find-links",
                str(self.wheel_cache_dir),
                "--strict",
                "--no-progress",
                "-r",
                str(requirements_path),
            ]
        command = [
            self.uv_executable,
            "pip",
            "install",
            "--python",
            str(_venv_python_path(venv_dir)),
            "--require-hashes",
            "--no-deps",
            "--excludes",
            str(excludes_path.resolve()),
            "--no-sources",
            "--no-config",
            "--default-index",
            lock.approved_index_url or "https://pypi.org/simple",
            "--no-progress",
        ]
        if lock.resolution_cutoff:
            command.extend(["--exclude-newer", lock.resolution_cutoff])
        if lock.build_constraints:
            command.extend(
                ["--build-constraints", str(build_constraints_path.resolve())]
            )
        command.extend(["-r", str(requirements_path)])
        return command

    def _run(
        self,
        command: list[str],
        *,
        cwd: Path,
        workflow_id: str,
        transaction_root: Path | None,
        diagnostics_dir: Path,
        phase: str,
    ) -> None:
        self.log_store.add(
            "info",
            "Running dependency environment installer command",
            "runtime.dependency_env",
            workflow_id=workflow_id,
            details={"command": _redacted_command(command), "cwd": str(cwd)},
        )
        try:
            result = self.command_runner(
                command,
                cwd=cwd,
                env=self._command_env(
                    transaction_root or cwd,
                ),
            )
        except FileNotFoundError as exc:
            raise DependencyEnvironmentInstallError(
                DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
                "uv executable is not available for dependency environment installation.",
                command=command,
            ) from exc
        _write_command_result(diagnostics_dir, phase, command, result)
        if result.returncode != 0:
            output = "\n".join(
                part for part in (result.stdout, result.stderr) if part
            )
            code = _classify_uv_install_failure(output)
            raise DependencyEnvironmentInstallError(
                code,
                _summarize_uv_failure(result),
                command=command,
                output=output,
            )

    def _command_env(self, transaction_root: Path) -> dict[str, str]:
        env = dict(os.environ)
        env["UV_NO_PROGRESS"] = "1"
        env["UV_NO_PYTHON_DOWNLOADS"] = "1"
        cache_dir = transaction_root / "dependency-uv-cache"
        temp_dir = transaction_root / "dependency-build-tmp"
        cache_dir.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        env["UV_CACHE_DIR"] = str(cache_dir)
        env["TMPDIR"] = str(temp_dir)
        env["TEMP"] = str(temp_dir)
        env["TMP"] = str(temp_dir)
        return env

    def _validate_overlay(
        self, lock: ResolvedDependencyLock, venv_dir: Path
    ) -> None:
        installed = {
            item["name"]
            for item in _installed_distributions(venv_dir)
            if isinstance(item.get("name"), str)
        }
        protected = installed.intersection(lock.runtime_excludes)
        if protected:
            raise DependencyEnvironmentInstallError(
                DependencyPolicyErrorCode.DEPENDENCY_OVERLAY_VALIDATION_FAILED,
                "The staged dependency environment attempted to replace protected "
                "runtime packages: " + ", ".join(sorted(protected)),
            )

    def _validate_build_cache(self, transaction_root: Path) -> None:
        cache_dir = transaction_root / "dependency-uv-cache"
        if not cache_dir.is_dir():
            return
        blocked: set[str] = set()
        for metadata_path in cache_dir.rglob("*.dist-info/METADATA"):
            metadata = _read_metadata_headers(metadata_path)
            raw_name = metadata.get("Name")
            if (
                raw_name
                and normalize_package_name(raw_name) in BUILD_BLOCKED_PACKAGES
            ):
                blocked.add(normalize_package_name(raw_name))
        if blocked:
            raise DependencyEnvironmentInstallError(
                DependencyPolicyErrorCode.DEPENDENCY_BUILD_POLICY_BLOCKED,
                "A source package requested unsupported build tooling: "
                + ", ".join(sorted(blocked)),
            )


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
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    error_line = next(
        (
            line
            for line in lines
            if line.startswith(("error:", "ERROR:", "Error:"))
        ),
        lines[0],
    )
    return f"Dependency environment installer failed: {error_line}"


def _classify_uv_install_failure(output: str) -> DependencyPolicyErrorCode:
    normalized = output.lower()
    if any(
        marker in normalized
        for marker in (
            "build-system.requires",
            "build dependencies",
            "build requirements",
            "get_requires_for_build",
        )
    ):
        return DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED
    if "build" in normalized:
        return DependencyPolicyErrorCode.DEPENDENCY_SOURCE_BUILD_FAILED
    return DependencyPolicyErrorCode.DEPENDENCY_INSTALL_FAILED


def _redacted_command(command: list[str]) -> list[str]:
    return [part if len(part) < 240 else part[:237] + "..." for part in command]


def _write_command_result(
    diagnostics_dir: Path,
    phase: str,
    command: list[str],
    result: subprocess.CompletedProcess[str],
) -> None:
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    (diagnostics_dir / f"{phase}.command.json").write_text(
        json.dumps(
            {
                "command": _redacted_command(command),
                "exit_code": result.returncode,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (diagnostics_dir / f"{phase}.stdout.log").write_text(
        result.stdout or "", encoding="utf-8"
    )
    (diagnostics_dir / f"{phase}.stderr.log").write_text(
        result.stderr or "", encoding="utf-8"
    )


def _installed_distributions(venv_dir: Path) -> list[dict[str, object]]:
    site_packages = [
        *(venv_dir / "lib").glob("python*/site-packages"),
        venv_dir / "Lib" / "site-packages",
    ]
    distributions: list[dict[str, object]] = []
    for site_packages_dir in site_packages:
        if not site_packages_dir.is_dir():
            continue
        for dist_info in sorted(site_packages_dir.glob("*.dist-info")):
            metadata = _read_metadata_headers(dist_info / "METADATA")
            raw_name = metadata.get("Name")
            if not raw_name:
                continue
            distributions.append(
                {
                    "name": normalize_package_name(raw_name),
                    "version": metadata.get("Version"),
                    "import_names": _distribution_import_names(
                        dist_info, site_packages_dir
                    ),
                }
            )
    return distributions


def _read_metadata_headers(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    headers: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line:
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        if key in {"Name", "Version"}:
            headers[key] = value.strip()
    return headers


def _distribution_import_names(
    dist_info: Path, site_packages_dir: Path
) -> list[str]:
    top_level = dist_info / "top_level.txt"
    if top_level.exists():
        names = {
            line.strip()
            for line in top_level.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if _valid_import_name(line.strip())
        }
        if names:
            return sorted(names)
    record = dist_info / "RECORD"
    if not record.exists():
        return []
    names: set[str] = set()
    for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
        raw_path = line.split(",", 1)[0]
        root = raw_path.split("/", 1)[0]
        candidate = root[:-3] if root.endswith(".py") else root
        if (
            candidate
            and not candidate.endswith((".dist-info", ".data", ".libs"))
            and _valid_import_name(candidate)
            and (
                raw_path.endswith(".py")
                or (site_packages_dir / candidate / "__init__.py").exists()
            )
        ):
            names.add(candidate)
    return sorted(names)


def _valid_import_name(value: str) -> bool:
    return bool(value and value.isidentifier())


def _write_installed_inventory(
    lock: ResolvedDependencyLock,
    *,
    venv_dir: Path,
    path: Path,
) -> None:
    source_by_name = {requirement.name: requirement for requirement in lock.requirements}
    distributions = _installed_distributions(venv_dir)
    for item in distributions:
        name = item.get("name")
        if isinstance(name, str):
            source = source_by_name.get(name)
            item["distribution_kind"] = (
                source.distribution_kind.value if source is not None else "unknown"
            )
            if source is not None:
                item["distribution_filename"] = source.distribution_filename
                item["distribution_url"] = source.distribution_url
                item["distribution_sha256"] = source.distribution_sha256
                item["source_index_url"] = source.source_index_url
    path.write_text(
        json.dumps(
            {
                "schema_version": "0.2.0",
                "distributions": distributions,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
