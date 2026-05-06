from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.engine.models import RuntimeHardwareProfile
from app.runtime.environment import CommandResult, RuntimeEnvironment
from app.runtime.hardware import plan_torch_install


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


@pytest.mark.anyio
async def test_bootstrap_installs_torch_before_comfyui_requirements(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    command_calls: list[list[str]] = []
    runtime_dir = tmp_path / "runtime"

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        command_calls.append(command)
        if command[1:3] == ["-m", "venv"]:
            python = runtime_dir / "comfyui-venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=runtime_dir,
        bootstrap_python_executable=str(create_python(tmp_path, "bootstrap-python")),
        hardware_profile=RuntimeHardwareProfile(
            os_name="Darwin",
            os_version="14.0",
            machine="x86_64",
            architecture="i386",
            accelerator="cpu",
        ),
        command_runner=command_runner,
    )

    result = await environment.bootstrap()

    torch_install_index = next(index for index, command in enumerate(command_calls) if "torch" in command)
    requirements_index = next(index for index, command in enumerate(command_calls) if "-r" in command)
    assert result.status == "prepared"
    assert torch_install_index < requirements_index


def test_torch_plan_uses_standard_macos_wheels_for_intel_mac() -> None:
    plan = plan_torch_install(
        RuntimeHardwareProfile(
            os_name="Darwin",
            os_version="14.0",
            machine="x86_64",
            architecture="i386",
            accelerator="cpu",
        )
    )

    assert plan.accelerator == "cpu"
    assert plan.index_url is None
    assert plan.pip_args == []


def test_torch_plan_uses_cuda_wheels_for_nvidia_gpu() -> None:
    plan = plan_torch_install(
        RuntimeHardwareProfile(
            os_name="Windows",
            os_version="11",
            machine="AMD64",
            architecture="AMD64",
            accelerator="nvidia_cuda",
            gpu_names=["NVIDIA GeForce RTX"],
            cuda_version="12.8",
        ),
        cuda_index_url="https://download.pytorch.org/whl/cu128",
    )

    assert plan.accelerator == "nvidia_cuda"
    assert plan.pip_args == ["--index-url", "https://download.pytorch.org/whl/cu128"]


def test_torch_plan_selects_cuda_wheel_from_driver_capability() -> None:
    plan = plan_torch_install(
        RuntimeHardwareProfile(
            os_name="Linux",
            os_version="",
            machine="x86_64",
            architecture="x86_64",
            accelerator="nvidia_cuda",
            gpu_names=["NVIDIA GeForce RTX"],
            cuda_version="12.4",
        )
    )

    assert plan.accelerator == "nvidia_cuda"
    assert plan.pip_args == ["--index-url", "https://download.pytorch.org/whl/cu124"]


def test_torch_plan_selects_cuda_130_for_modern_driver_capability() -> None:
    plan = plan_torch_install(
        RuntimeHardwareProfile(
            os_name="Linux",
            os_version="",
            machine="x86_64",
            architecture="x86_64",
            accelerator="nvidia_cuda",
            gpu_names=["NVIDIA A10G"],
            cuda_version="13.2",
        )
    )

    assert plan.accelerator == "nvidia_cuda"
    assert plan.pip_args == ["--index-url", "https://download.pytorch.org/whl/cu130"]
