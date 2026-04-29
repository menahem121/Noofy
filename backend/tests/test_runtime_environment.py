from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.environment import CommandResult, RuntimeEnvironment


def create_repo(tmp_path: Path, *, requirements: bool = True) -> Path:
    repo_dir = tmp_path / "ComfyUI"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("", encoding="utf-8")
    if requirements:
        (repo_dir / "requirements.txt").write_text("aiohttp\n", encoding="utf-8")
    return repo_dir


def create_python(tmp_path: Path, name: str = "python") -> Path:
    python = tmp_path / name
    python.write_text("", encoding="utf-8")
    python.chmod(0o755)
    return python


@pytest.mark.anyio
async def test_environment_reports_missing_python_executable(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=str(tmp_path / "missing-python"),
    )

    status = await environment.status()

    assert not status.prepared
    assert not status.python_exists
    assert "Runtime Python executable not found" in (status.error or "")


@pytest.mark.anyio
async def test_bootstrap_reports_missing_requirements_file(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path, requirements=False)
    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable=str(create_python(tmp_path, "bootstrap-python")),
    )

    result = await environment.bootstrap()

    assert result.status == "requirements_missing"
    assert result.environment is not None
    assert not result.environment.requirements_file_exists


@pytest.mark.anyio
async def test_environment_reports_dependency_check_failure(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    runtime_python = create_python(tmp_path, "runtime-python")

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        if "torch" in command[-1]:
            return CommandResult(returncode=1, stderr="No module named torch")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=str(runtime_python),
        command_runner=command_runner,
    )

    status = await environment.status()

    assert not status.prepared
    assert status.dependencies[0].name == "torch"
    assert not status.dependencies[0].available
    assert "torch" in (status.error or "")


@pytest.mark.anyio
async def test_bootstrap_reports_environment_already_prepared(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    runtime_python = create_python(tmp_path, "runtime-python")
    command_calls: list[list[str]] = []

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        command_calls.append(command)
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=str(runtime_python),
        command_runner=command_runner,
    )

    result = await environment.bootstrap()

    assert result.status == "already_prepared"
    assert all("-m" not in command for command in command_calls)


@pytest.mark.anyio
async def test_bootstrap_failure_is_logged(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    log_store = LogStore()

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        return CommandResult(returncode=2, stderr="venv failed")

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable=str(create_python(tmp_path, "bootstrap-python")),
        log_store=log_store,
        command_runner=command_runner,
    )

    result = await environment.bootstrap()

    assert result.status == "bootstrap_failed"
    assert log_store.latest_error() is not None
    assert log_store.latest_error().message == "Create ComfyUI runtime virtual environment failed"
