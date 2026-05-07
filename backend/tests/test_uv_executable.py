"""Tests for the Noofy-controlled uv executable resolver."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.uv_executable import _venv_uv_path, resolve_noofy_uv_executable

# ---------------------------------------------------------------------------
# _venv_uv_path — cross-platform path construction
# ---------------------------------------------------------------------------


def test_venv_uv_path_unix(tmp_path: Path) -> None:
    python = tmp_path / "bin" / "python"
    result = (
        _venv_uv_path.__wrapped__(python)
        if hasattr(_venv_uv_path, "__wrapped__")
        else None
    )
    with patch("app.runtime.uv_executable.os.name", "posix"):
        result = _venv_uv_path(python)
    assert result == tmp_path / "bin" / "uv"


def test_venv_uv_path_windows(tmp_path: Path) -> None:
    python = tmp_path / "Scripts" / "python.exe"
    with patch("app.runtime.uv_executable.os.name", "nt"):
        result = _venv_uv_path(python)
    assert result == tmp_path / "Scripts" / "uv.exe"


# ---------------------------------------------------------------------------
# resolve_noofy_uv_executable — resolver logic
# ---------------------------------------------------------------------------


def test_resolve_returns_absolute_path_when_uv_present(tmp_path: Path) -> None:
    fake_uv = tmp_path / "bin" / "uv"
    fake_uv.parent.mkdir(parents=True)
    fake_uv.touch()
    fake_python = tmp_path / "bin" / "python"

    with patch("app.runtime.uv_executable.sys") as mock_sys, patch(
        "app.runtime.uv_executable.os.name", "posix"
    ):
        mock_sys.executable = str(fake_python)
        result = resolve_noofy_uv_executable()

    assert result == str(fake_uv)
    assert Path(result).is_absolute()


def test_resolve_uses_noofy_uv_executable_override(tmp_path: Path) -> None:
    bundled_uv = (
        tmp_path
        / "Noofy.app"
        / "Contents"
        / "Resources"
        / "noofy-runtime"
        / "python"
        / "bin"
        / "uv"
    )
    bundled_uv.parent.mkdir(parents=True)
    bundled_uv.touch()

    with patch.dict("os.environ", {"NOOFY_UV_EXECUTABLE": str(bundled_uv)}):
        assert resolve_noofy_uv_executable() == str(bundled_uv)


def test_resolve_rejects_missing_noofy_uv_executable_override(tmp_path: Path) -> None:
    missing = tmp_path / "missing-uv"

    with patch.dict("os.environ", {"NOOFY_UV_EXECUTABLE": str(missing)}):
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_noofy_uv_executable()

    assert str(missing) in str(exc_info.value)


def test_resolve_raises_file_not_found_when_uv_missing(tmp_path: Path) -> None:
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    # Do NOT create uv alongside it.

    with patch("app.runtime.uv_executable.sys") as mock_sys, patch(
        "app.runtime.uv_executable.os.name", "posix"
    ):
        mock_sys.executable = str(fake_python)
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_noofy_uv_executable()

    msg = str(exc_info.value)
    assert "uv" in msg.lower()
    assert "make install" in msg


def test_resolve_error_message_names_expected_path(tmp_path: Path) -> None:
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)

    with patch("app.runtime.uv_executable.sys") as mock_sys, patch(
        "app.runtime.uv_executable.os.name", "posix"
    ):
        mock_sys.executable = str(fake_python)
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_noofy_uv_executable()

    assert str(tmp_path / "bin" / "uv") in str(exc_info.value)


def test_resolve_does_not_fall_back_to_global_path(tmp_path: Path) -> None:
    """Even if a global `uv` is on PATH, resolver must not use it."""
    fake_python = tmp_path / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    # No uv in the fake venv.

    # Put a fake `uv` on PATH that would succeed if shutil.which were used.
    global_uv = tmp_path / "global_bin" / "uv"
    global_uv.parent.mkdir()
    global_uv.touch()
    global_uv.chmod(0o755)

    import os

    env_with_global_uv = os.environ.copy()
    env_with_global_uv["PATH"] = (
        str(global_uv.parent) + os.pathsep + env_with_global_uv.get("PATH", "")
    )

    with patch("app.runtime.uv_executable.sys") as mock_sys, patch(
        "app.runtime.uv_executable.os.name", "posix"
    ), patch.dict("os.environ", env_with_global_uv):
        mock_sys.executable = str(fake_python)
        # Must still fail — it should look in the venv, not on PATH.
        with pytest.raises(FileNotFoundError):
            resolve_noofy_uv_executable()


# ---------------------------------------------------------------------------
# Actual venv — integration check
# ---------------------------------------------------------------------------


def test_resolve_finds_uv_in_running_venv() -> None:
    """Confirm uv is present in the running backend venv (i.e. make install works)."""
    path = resolve_noofy_uv_executable()
    assert Path(path).is_file(), f"uv not found at resolved path: {path}"
    # It must be the same venv that's running this test.
    assert Path(path).parent == Path(sys.executable).parent


def test_resolved_uv_is_executable_and_reports_version() -> None:
    """The resolved uv binary must actually run."""
    path = resolve_noofy_uv_executable()
    result = subprocess.run([path, "--version"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "uv" in result.stdout.lower()


# ---------------------------------------------------------------------------
# UvDependencyEnvironmentInstaller uses the controlled path
# ---------------------------------------------------------------------------


def test_dependency_env_installer_uses_provided_uv_executable(tmp_path: Path) -> None:
    """When uv_executable is passed, it must appear as command[0]."""
    from app.runtime.dependency_env import (
        UvDependencyEnvironmentInstaller,
        DependencyEnvironmentInstallRequest,
    )
    from tests.test_dependency_env import _lock_for_cached_wheel

    cache_dir = tmp_path / "wheel-cache"
    cache_dir.mkdir()
    lock = _lock_for_cached_wheel(cache_dir)

    controlled_uv = "/noofy/venv/bin/uv"
    seen_executables: list[str] = []

    def runner(command, *, cwd, env):
        seen_executables.append(command[0])
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=cache_dir,
        uv_executable=controlled_uv,
        command_runner=runner,
        log_store=LogStore(),
    )
    installer.install(
        DependencyEnvironmentInstallRequest(
            lock=lock,
            target_dir=tmp_path / "stage",
            python_version="3.13",
            workflow_id="wf",
        )
    )

    assert all(
        exe == controlled_uv for exe in seen_executables
    ), f"Expected all commands to use {controlled_uv!r}, got: {seen_executables}"


# ---------------------------------------------------------------------------
# UvDependencyLockResolver uses the controlled path
# ---------------------------------------------------------------------------


def test_dependency_lock_resolver_stores_provided_uv_executable(tmp_path: Path) -> None:
    """uv_executable is stored and will be used for all subprocess calls."""
    from app.runtime.dependency_resolver import UvDependencyLockResolver

    controlled_uv = "/noofy/venv/bin/uv"

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheels",
        work_dir=tmp_path / "work",
        uv_executable=controlled_uv,
        log_store=LogStore(),
    )

    assert resolver.uv_executable == controlled_uv


def test_dependency_lock_resolver_uses_uv_executable_in_commands(
    tmp_path: Path,
) -> None:
    """Resolver must pass uv_executable as command[0] when it invokes uv."""
    from app.runtime.dependency_resolver import UvDependencyLockResolver

    controlled_uv = "/noofy/venv/bin/uv"
    seen_executables: list[str] = []

    def runner(command, *, cwd, env):
        seen_executables.append(command[0])
        if command[1:2] == ["--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="uv 0.9.0\n", stderr=""
            )
        output_flag_index = command.index("--output-file")
        Path(command[output_flag_index + 1]).write_text("", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheels",
        work_dir=tmp_path / "work",
        uv_executable=controlled_uv,
        command_runner=runner,
        log_store=LogStore(),
    )
    (tmp_path / "wheels").mkdir()

    # _resolver_metadata() runs [uv_executable, "--version"] — invoke it directly.
    resolver._resolver_metadata(cwd=tmp_path)

    assert seen_executables == [controlled_uv]
