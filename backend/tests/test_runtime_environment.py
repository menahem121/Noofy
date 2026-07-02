import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.diagnostics import LogStore
from app.engine.models import RuntimeHardwareProfile
from app.runtime.environment import (
    _REQUIRED_RUNTIME_CHECKS,
    CommandResult,
    RuntimeEnvironment,
)
from app.runtime.hardware import plan_torch_install

SUPPORTED_TEST_HARDWARE = RuntimeHardwareProfile(
    os_name="Linux",
    os_version="",
    machine="x86_64",
    architecture="x86_64",
    accelerator="cpu",
)


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
    if os.name == "nt" and not python.name.lower().endswith(".exe"):
        (python.parent / f"{python.name}.exe").write_text("", encoding="utf-8")
    return python


def venv_python(runtime_dir: Path) -> Path:
    venv_dir = runtime_dir / "comfyui-venv"
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def command_name(command: str) -> str:
    return Path(command).name


def test_torch_custom_op_runtime_check_accepts_present_api() -> None:
    check_code = dict(_REQUIRED_RUNTIME_CHECKS)["torch.library.custom_op"]
    original_torch = sys.modules.get("torch")
    sys.modules["torch"] = SimpleNamespace(
        library=SimpleNamespace(custom_op=lambda *args, **kwargs: None)
    )
    try:
        exec(check_code, {})
    finally:
        if original_torch is None:
            sys.modules.pop("torch", None)
        else:
            sys.modules["torch"] = original_torch


@pytest.mark.anyio
async def test_environment_reports_missing_python_executable(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=str(tmp_path / "missing-python"),
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        log_store=LogStore(),
    )

    status = await environment.status()

    assert not status.prepared
    assert not status.python_exists
    assert "prepared engine runtime" in (status.error or "")


@pytest.mark.anyio
async def test_bootstrap_reports_missing_requirements_file(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path, requirements=False)
    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable=str(create_python(tmp_path, "bootstrap-python")),
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        log_store=LogStore(),
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
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    status = await environment.status()

    assert not status.prepared
    assert status.dependencies[0].name == "torch"
    assert not status.dependencies[0].available
    assert "torch" in (status.error or "")


@pytest.mark.anyio
async def test_environment_reports_python_version_mismatch_for_selected_profile(
    tmp_path: Path,
) -> None:
    repo_dir = create_repo(tmp_path)
    runtime_python = create_python(tmp_path, "runtime-python")

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        if "sys.version_info" in command[-1]:
            return CommandResult(returncode=0, stdout="3.14\n")
        raise AssertionError("dependency checks should be skipped on ABI mismatch")

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=str(runtime_python),
        expected_python_version="3.13",
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    status = await environment.status()

    assert not status.prepared
    assert status.python_version == "3.14"
    assert status.expected_python_version == "3.13"
    assert status.python_version_matches is False
    assert "requires Python 3.13" in (status.error or "")


@pytest.mark.anyio
async def test_environment_reports_missing_required_torch_runtime_api(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    runtime_python = create_python(tmp_path, "runtime-python")

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        if "torch.library.custom_op" in command[-1]:
            return CommandResult(
                returncode=1,
                stderr="RuntimeError: torch.library.custom_op is required by comfy-kitchen",
            )
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        python_executable_override=str(runtime_python),
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    status = await environment.status()

    assert not status.prepared
    failed = {dependency.name for dependency in status.dependencies if not dependency.available}
    assert failed == {"torch.library.custom_op"}
    assert "torch.library.custom_op" in (status.error or "")


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
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
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
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        log_store=log_store,
        command_runner=command_runner,
    )

    result = await environment.bootstrap()

    assert result.status == "bootstrap_failed"
    assert log_store.latest_error() is not None
    assert (
        log_store.latest_error().message
        == "Create ComfyUI runtime virtual environment failed"
    )


@pytest.mark.anyio
async def test_bootstrap_prefers_profile_python_over_generic_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = create_repo(tmp_path)
    runtime_dir = tmp_path / "runtime"
    create_python(tmp_path, "python3")
    create_python(tmp_path, "python3.13")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    command_calls: list[list[str]] = []

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        command_calls.append(command)
        if "sys.version_info" in command[-1]:
            if command_name(command[0]) in {"python3.13", "python3.13.exe"} or command[0] == str(venv_python(runtime_dir)):
                return CommandResult(returncode=0, stdout="3.13\n")
            return CommandResult(returncode=0, stdout="3.14\n")
        if command[1:3] == ["-m", "venv"]:
            python = venv_python(runtime_dir)
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=runtime_dir,
        bootstrap_python_executable="python3",
        expected_python_version="3.13",
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    result = await environment.bootstrap()

    assert result.status == "prepared"
    venv_command = next(command for command in command_calls if command[1:3] == ["-m", "venv"])
    assert command_name(venv_command[0]) in {"python3.13", "python3.13.exe"}


@pytest.mark.anyio
async def test_bootstrap_rejects_generic_python_with_wrong_profile_abi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = create_repo(tmp_path)
    create_python(tmp_path, "python3")
    monkeypatch.setenv("PATH", str(tmp_path))
    command_calls: list[list[str]] = []

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        command_calls.append(command)
        if "sys.version_info" in command[-1]:
            return CommandResult(returncode=0, stdout="3.14\n")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable="python3",
        expected_python_version="3.13",
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    result = await environment.bootstrap()

    assert result.status == "python_missing"
    assert result.environment is not None
    assert "source checkout" in (result.environment.error or "")
    assert "needs Python 3.13" in (result.environment.error or "")
    assert "uv-managed Python" in (result.environment.error or "")
    assert "python3.13 (missing)" in (result.environment.error or "")
    assert "python3 (3.14)" in (result.environment.error or "")
    assert "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE" in (result.environment.error or "")
    assert result.environment.bootstrap_python_attempts == [
        {
            "python_executable": "python3.13",
            "exists": False,
            "python_version": None,
            "expected_python_version": "3.13",
        },
        {
            "python_executable": "python3",
            "exists": True,
            "python_version": "3.14",
            "expected_python_version": "3.13",
        },
    ]
    assert all(command[1:3] != ["-m", "venv"] for command in command_calls)


@pytest.mark.anyio
async def test_packaged_bootstrap_failure_points_to_reinstall_not_manual_python(
    tmp_path: Path,
) -> None:
    repo_dir = create_repo(tmp_path)
    packaged_python = create_python(tmp_path, "bundled-python")

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        if "sys.version_info" in command[-1]:
            return CommandResult(returncode=0, stdout="3.14\n")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable=str(packaged_python),
        expected_python_version="3.13",
        packaged_runtime=True,
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    result = await environment.bootstrap()

    assert result.status == "python_missing"
    assert result.environment is not None
    assert result.environment.runtime_distribution == "packaged"
    assert "Packaged Noofy" in (result.environment.error or "")
    assert "Required Python 3.13" in (result.environment.error or "")
    assert f"{packaged_python} (3.14)" in (result.environment.error or "")
    assert "Reinstall or update Noofy" in (result.environment.error or "")
    assert "should not install Python manually" in (result.environment.error or "")


@pytest.mark.anyio
async def test_packaged_bootstrap_never_falls_back_to_path_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = create_repo(tmp_path)
    packaged_python = create_python(tmp_path, "bundled-python")
    create_python(tmp_path, "python3.13")
    monkeypatch.setenv("PATH", str(tmp_path))
    command_calls: list[list[str]] = []

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        command_calls.append(command)
        if "sys.version_info" in command[-1]:
            if command[0] == str(packaged_python):
                return CommandResult(returncode=0, stdout="3.14\n")
            return CommandResult(returncode=0, stdout="3.13\n")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable=str(packaged_python),
        expected_python_version="3.13",
        packaged_runtime=True,
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    result = await environment.bootstrap()

    assert result.status == "python_missing"
    assert result.environment is not None
    assert result.environment.bootstrap_python_attempts == [
        {
            "python_executable": str(packaged_python),
            "exists": True,
            "python_version": "3.14",
            "expected_python_version": "3.13",
        }
    ]
    assert all(command[0] != "python3.13" for command in command_calls)
    assert all(command[1:3] != ["-m", "venv"] for command in command_calls)


@pytest.mark.anyio
async def test_bootstrap_rebuilds_existing_venv_with_wrong_profile_abi(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_dir = create_repo(tmp_path)
    runtime_dir = tmp_path / "runtime"
    runtime_python = venv_python(runtime_dir)
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    stale_marker = runtime_dir / "comfyui-venv" / "stale.txt"
    stale_marker.write_text("old", encoding="utf-8")
    create_python(tmp_path, "python3.13")
    monkeypatch.setenv("PATH", str(tmp_path))
    venv_created = False

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        nonlocal venv_created
        if "sys.version_info" in command[-1]:
            if command[0] == str(runtime_python) and not venv_created:
                return CommandResult(returncode=0, stdout="3.14\n")
            return CommandResult(returncode=0, stdout="3.13\n")
        if command[1:3] == ["-m", "venv"]:
            venv_created = True
            runtime_python.parent.mkdir(parents=True)
            runtime_python.write_text("", encoding="utf-8")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=runtime_dir,
        bootstrap_python_executable="python3",
        expected_python_version="3.13",
        hardware_profile=SUPPORTED_TEST_HARDWARE,
        command_runner=command_runner,
        log_store=LogStore(),
    )

    result = await environment.bootstrap()

    assert result.status == "prepared"
    assert venv_created
    assert not stale_marker.exists()


@pytest.mark.anyio
async def test_bootstrap_installs_torch_before_comfyui_requirements(
    tmp_path: Path,
) -> None:
    repo_dir = create_repo(tmp_path)
    command_calls: list[list[str]] = []
    runtime_dir = tmp_path / "runtime"

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        command_calls.append(command)
        if command[1:3] == ["-m", "venv"]:
            python = venv_python(runtime_dir)
            python.parent.mkdir(parents=True)
            python.write_text("", encoding="utf-8")
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=runtime_dir,
        bootstrap_python_executable=str(create_python(tmp_path, "bootstrap-python")),
        hardware_profile=RuntimeHardwareProfile(
            os_name="Linux",
            os_version="",
            machine="x86_64",
            architecture="x86_64",
            accelerator="cpu",
        ),
        command_runner=command_runner,
        log_store=LogStore(),
    )

    result = await environment.bootstrap()

    torch_install_index = next(
        index for index, command in enumerate(command_calls) if "torch" in command
    )
    requirements_index = next(
        index for index, command in enumerate(command_calls) if "-r" in command
    )
    assert result.status == "prepared"
    assert torch_install_index < requirements_index


@pytest.mark.anyio
async def test_bootstrap_fails_closed_on_macos_intel(tmp_path: Path) -> None:
    repo_dir = create_repo(tmp_path)
    command_calls: list[list[str]] = []

    async def command_runner(command: list[str], cwd: Path | None) -> CommandResult:
        command_calls.append(command)
        return CommandResult(returncode=0)

    environment = RuntimeEnvironment(
        repo_dir=repo_dir,
        runtime_dir=tmp_path / "runtime",
        bootstrap_python_executable=str(create_python(tmp_path, "bootstrap-python")),
        hardware_profile=RuntimeHardwareProfile(
            os_name="Darwin",
            os_version="14.0",
            machine="x86_64",
            architecture="x86_64",
            accelerator="unsupported_macos_intel",
        ),
        command_runner=command_runner,
        log_store=LogStore(),
    )

    result = await environment.bootstrap()

    assert result.status == "platform_unsupported"
    assert result.environment is not None
    assert not result.environment.prepared
    assert "macOS Intel is unsupported" in (result.environment.error or "")
    assert command_calls == []


def test_torch_plan_rejects_macos_intel() -> None:
    plan = plan_torch_install(
        RuntimeHardwareProfile(
            os_name="Darwin",
            os_version="14.0",
            machine="x86_64",
            architecture="x86_64",
            accelerator="unsupported_macos_intel",
        )
    )

    assert plan.accelerator == "unsupported_macos_intel"
    assert plan.packages == []
    assert plan.index_url is None
    assert "macOS Intel is unsupported" in plan.reason


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
