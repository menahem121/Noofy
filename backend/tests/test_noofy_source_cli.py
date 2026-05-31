import importlib.util
import signal
import subprocess
import sys
from pathlib import Path

import pytest


def load_noofy_cli():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "noofy.py"
    spec = importlib.util.spec_from_file_location("noofy_source_cli", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_frontend_install_command_prefers_ci_when_lockfile_exists(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")

    assert cli.frontend_install_command(tmp_path) == ["npm", "ci"]


def test_frontend_install_command_uses_install_without_lockfile(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    assert cli.frontend_install_command(tmp_path) == ["npm", "install"]


def test_source_checkout_env_sets_managed_runtime_and_frontend_api(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"

    env = cli.source_checkout_env(
        data_dir=data_dir,
        backend_host="127.0.0.1",
        backend_port=9876,
        include_frontend_api=True,
        base_env={"XDG_CONFIG_HOME": str(config_dir)},
    )

    assert env["NOOFY_DATA_DIR"] == str(data_dir)
    assert env["COMFYUI_RUNTIME_MODE"] == "managed"
    assert env["NOOFY_BACKEND_PORT"] == "9876"
    assert env["VITE_NOOFY_API_BASE_URL"] == "http://127.0.0.1:9876/api"
    assert env["VITE_DEV_BACKEND_PORT"] == "9876"
    assert env["NOOFY_API_KEY_STORE"] == "encrypted-vault"
    assert "NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE" not in env
    passphrase_file = Path(env["NOOFY_API_KEY_VAULT_PASSPHRASE_FILE"])
    assert passphrase_file == config_dir / "noofy" / "api-key-vault.passphrase"
    assert passphrase_file.is_file()
    if cli.os.name == "posix":
        assert passphrase_file.stat().st_mode & 0o077 == 0


def test_source_checkout_env_respects_explicit_os_keyring_choice(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    config_dir = tmp_path / "config"

    env = cli.source_checkout_env(
        data_dir=tmp_path / "data",
        base_env={
            "XDG_CONFIG_HOME": str(config_dir),
            "NOOFY_API_KEY_STORE": "os-keyring",
        },
    )

    assert env["NOOFY_API_KEY_STORE"] == "os-keyring"
    assert "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE" not in env
    assert not (config_dir / "noofy" / "api-key-vault.passphrase").exists()


def test_source_checkout_env_initializes_explicit_encrypted_vault_passphrase(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    passphrase_file = tmp_path / "secrets" / "api-key-vault.passphrase"

    env = cli.source_checkout_env(
        data_dir=tmp_path / "data",
        base_env={
            "NOOFY_API_KEY_STORE": "encrypted-vault",
            "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE": str(passphrase_file),
        },
    )

    assert env["NOOFY_API_KEY_STORE"] == "encrypted-vault"
    assert env["NOOFY_API_KEY_VAULT_PASSPHRASE_FILE"] == str(passphrase_file)
    assert passphrase_file.is_file()


def test_source_checkout_env_allows_repo_local_encrypted_vault_for_default_data_dir(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    env = cli.source_checkout_env(
        data_dir=cli.DEFAULT_DATA_DIR,
        base_env={"XDG_CONFIG_HOME": str(tmp_path / "config")},
    )

    assert env["NOOFY_API_KEY_STORE"] == "encrypted-vault"
    assert env["NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE"] == "1"


def test_source_checkout_env_does_not_allow_arbitrary_repo_local_secret_storage(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    env = cli.source_checkout_env(
        data_dir=cli.REPO_ROOT / "scratch-data",
        base_env={"XDG_CONFIG_HOME": str(tmp_path / "config")},
    )

    assert env["NOOFY_API_KEY_STORE"] == "encrypted-vault"
    assert "NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE" not in env


def test_source_checkout_env_respects_explicit_repo_local_secret_storage_choice(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    env = cli.source_checkout_env(
        data_dir=cli.DEFAULT_DATA_DIR,
        base_env={
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE": "0",
        },
    )

    assert env["NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE"] == "0"


def test_ensure_api_key_vault_passphrase_fills_empty_file(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    passphrase_file = tmp_path / "config" / "api-key-vault.passphrase"
    passphrase_file.parent.mkdir()
    passphrase_file.write_text("", encoding="utf-8")

    cli.ensure_api_key_vault_passphrase(passphrase_file)

    assert passphrase_file.read_text(encoding="utf-8").strip()


def test_ensure_api_key_vault_passphrase_rejects_directory(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    passphrase_path = tmp_path / "config"
    passphrase_path.mkdir()

    with pytest.raises(SystemExit, match="passphrase path exists but is not a file"):
        cli.ensure_api_key_vault_passphrase(passphrase_path)


def test_backend_python_path_uses_windows_scripts_directory(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    assert cli.backend_python_path(tmp_path, os_name="nt") == (
        tmp_path / "backend" / ".venv" / "Scripts" / "python.exe"
    )


def test_child_process_popen_kwargs_starts_new_session_on_posix() -> None:
    cli = load_noofy_cli()

    assert cli.child_process_popen_kwargs(os_name="posix") == {"start_new_session": True}


def test_child_process_popen_kwargs_uses_new_process_group_on_windows(monkeypatch) -> None:
    cli = load_noofy_cli()
    monkeypatch.setattr(cli.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)

    assert cli.child_process_popen_kwargs(os_name="nt") == {"creationflags": 512}


def test_signal_process_tree_targets_posix_process_group(monkeypatch) -> None:
    cli = load_noofy_cli()
    calls = []

    class Process:
        pid = 1234

    monkeypatch.setattr(cli.os, "name", "posix")
    monkeypatch.setattr(cli.os, "killpg", lambda pid, sig: calls.append((pid, sig)))

    cli.signal_process_tree(Process(), signal.SIGTERM)

    assert calls == [(1234, signal.SIGTERM)]


def test_terminate_processes_signals_exited_process_group(monkeypatch) -> None:
    cli = load_noofy_cli()
    signals = []
    waits = []
    kills = []

    class Process:
        pid = 1234

        def poll(self):
            return 0

    monkeypatch.setattr(
        cli,
        "signal_process_tree",
        lambda process, sig: signals.append((process.pid, sig)),
    )
    monkeypatch.setattr(
        cli,
        "wait_for_process_shutdown",
        lambda processes, **kwargs: waits.append(
            [process.pid for process in processes]
        ),
    )
    monkeypatch.setattr(
        cli, "kill_process_tree", lambda process: kills.append(process.pid)
    )

    cli.terminate_processes([Process()])

    assert signals == [(1234, signal.SIGTERM)]
    assert waits == [[1234], [1234]]
    assert kills == [1234]


def test_runtime_bootstrap_command_delegates_to_backend_service(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    command = cli.bootstrap_runtime_command(tmp_path / "backend" / ".venv" / "bin" / "python")
    code = command[-1]

    assert "create_default_engine_service" in code
    assert "bootstrap_comfyui_runtime" in code
    assert "pip install" not in code
    assert "torch" not in code.lower()


def test_runtime_status_command_requests_environment_details(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    command = cli.runtime_status_command(tmp_path / "backend" / ".venv" / "bin" / "python")

    assert "status(include_environment=True)" in command[-1]


def test_managed_runtime_python_guidance_includes_os_specific_source_fix() -> None:
    cli = load_noofy_cli()
    environment = {
        "runtime_distribution": "source_checkout",
        "expected_python_version": "3.13",
        "bootstrap_python_attempts": [
            {
                "python_executable": "python3.13",
                "exists": False,
                "python_version": None,
            },
            {
                "python_executable": "python3",
                "exists": True,
                "python_version": "3.14",
            },
        ],
    }

    linux_guidance = cli.managed_runtime_python_setup_guidance(environment, platform="linux")
    macos_guidance = cli.managed_runtime_python_setup_guidance(environment, platform="darwin")
    windows_guidance = cli.managed_runtime_python_setup_guidance(environment, platform="win32")

    assert linux_guidance is not None
    assert "Source/development Python fix" in linux_guidance
    assert "Priority 1 - recommended: use uv-managed Python" in linux_guidance
    assert "backend/.venv/bin/uv python install 3.13" in linux_guidance
    assert (
        'COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE="$(backend/.venv/bin/uv python find 3.13)" make install'
        in linux_guidance
    )
    assert (
        "Priority 2 - Linux distro package fallback, only if your distro offers it"
        in linux_guidance
    )
    assert "apt install python3.13 python3.13-venv" in linux_guidance
    assert "dnf install python3.13" in linux_guidance
    assert "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE=python3.13 make install" in linux_guidance
    assert "python3.13 (missing)" in linux_guidance
    assert "python3 (3.14)" in linux_guidance
    assert "sudo" not in linux_guidance
    assert macos_guidance is not None
    assert "backend/.venv/bin/uv python install 3.13" in macos_guidance
    assert "brew install python@3.13" in macos_guidance
    assert "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE" in macos_guidance
    assert windows_guidance is not None
    assert ".\\backend\\.venv\\Scripts\\uv.exe python install 3.13" in windows_guidance
    assert "winget install Python.Python.3.13" in windows_guidance
    assert "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE" in windows_guidance


def test_managed_runtime_python_guidance_skips_packaged_runtime() -> None:
    cli = load_noofy_cli()

    assert (
        cli.managed_runtime_python_setup_guidance(
            {
                "runtime_distribution": "packaged",
                "expected_python_version": "3.13",
            }
        )
        is None
    )


def test_format_command_redacts_inline_python_code() -> None:
    cli = load_noofy_cli()

    assert cli._format_command(["python", "-c", "print('secretly long code')"]) == "python -c <python-code>"


def test_ensure_backend_venv_is_idempotent_when_python_exists(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    backend_python.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")
    calls = []

    def runner(command, cwd, env, capture):
        calls.append(command)
        return cli.CommandResult(returncode=0)

    cli.NoofyCheckout(root=tmp_path, command_runner=runner).ensure_backend_venv()

    assert calls == []


def test_install_backend_dependencies_keeps_existing_pip(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    calls = []

    def runner(command, cwd, env, capture):
        calls.append(command)
        return cli.CommandResult(returncode=0)

    cli.NoofyCheckout(root=tmp_path, command_runner=runner).install_backend_dependencies()

    assert [str(backend_python), "-m", "ensurepip", "--upgrade"] not in calls
    assert calls[-2:] == [
        [str(backend_python), "-m", "pip", "install", "--upgrade", "pip"],
        [str(backend_python), "-m", "pip", "install", "-e", ".[dev]"],
    ]


def test_install_backend_dependencies_bootstraps_missing_pip(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    calls = []

    def runner(command, cwd, env, capture):
        calls.append(command)
        if command[1] == "-c":
            raise subprocess.CalledProcessError(1, command)
        return cli.CommandResult(returncode=0)

    cli.NoofyCheckout(root=tmp_path, command_runner=runner).install_backend_dependencies()

    assert calls[1:] == [
        [str(backend_python), "-m", "ensurepip", "--upgrade"],
        [str(backend_python), "-m", "pip", "install", "--upgrade", "pip"],
        [str(backend_python), "-m", "pip", "install", "-e", ".[dev]"],
    ]


def test_install_backend_dependencies_reports_unavailable_ensurepip(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    def runner(command, cwd, env, capture):
        raise subprocess.CalledProcessError(1, command)

    checkout = cli.NoofyCheckout(root=tmp_path, command_runner=runner)

    with pytest.raises(SystemExit, match="Install Python with venv/ensurepip support"):
        checkout.install_backend_dependencies()


def test_doctor_reports_prepared_runtime_environment(tmp_path: Path, capsys) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    backend_python.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")

    def runner(command, cwd, env, capture):
        return cli.CommandResult(
            returncode=0,
            stdout='{"mode":"managed","reachable":false,"environment":{"prepared":true}}\n',
        )

    result = cli.NoofyCheckout(root=tmp_path, command_runner=runner).doctor()

    assert result == 0
    output = capsys.readouterr().out
    assert "Runtime mode: managed" in output
    assert "Runtime prepared: True" in output
    assert "Sidecar reachable: False" in output


def test_doctor_reports_missing_runtime_environment_details(tmp_path: Path, capsys) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    backend_python.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")

    def runner(command, cwd, env, capture):
        return cli.CommandResult(
            returncode=0,
            stdout='{"mode":"managed","reachable":false,"environment":null}\n',
        )

    result = cli.NoofyCheckout(root=tmp_path, command_runner=runner).doctor()

    assert result == 1
    assert "Runtime error: managed runtime environment status is unavailable" in capsys.readouterr().out


def test_run_reports_missing_frontend_dependencies(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    backend_python.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")
    (tmp_path / "frontend").mkdir()

    result = cli.NoofyCheckout(root=tmp_path).run()

    assert result == 1


def test_install_delegates_runtime_preparation_to_bootstrap_service(tmp_path: Path, monkeypatch) -> None:
    cli = load_noofy_cli()
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    data_dir = tmp_path / ".noofy-runtime" / "data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    calls = []
    captured_envs = []

    def runner(command, cwd, env, capture):
        calls.append(command)
        if command[1:3] == ["-m", "venv"]:
            backend_python = cli.backend_python_path(tmp_path)
            backend_python.parent.mkdir(parents=True)
            backend_python.write_text("", encoding="utf-8")
        if capture:
            captured_envs.append(env)
            return cli.CommandResult(
                returncode=0,
                stdout='{"status":"prepared","environment":{"python_executable":"managed-python"}}\n',
            )
        return cli.CommandResult(returncode=0)

    cli.NoofyCheckout(root=tmp_path, python_executable="/usr/bin/python3", command_runner=runner).install(
        data_dir=data_dir,
        skip_frontend=True,
    )

    assert any("bootstrap_comfyui_runtime" in command[-1] for command in calls)
    assert not any("torch" in part for command in calls for part in command[:3])
    assert captured_envs[0]["COMFYUI_RUNTIME_MODE"] == "managed"
    assert captured_envs[0]["NOOFY_DATA_DIR"] == str(data_dir)
    assert captured_envs[0]["NOOFY_API_KEY_STORE"] == "encrypted-vault"
    assert Path(captured_envs[0]["NOOFY_API_KEY_VAULT_PASSPHRASE_FILE"]).is_file()


def test_install_allows_unsupported_managed_runtime_platform(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    cli = load_noofy_cli()
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def runner(command, cwd, env, capture):
        if command[1:3] == ["-m", "venv"]:
            backend_python = cli.backend_python_path(tmp_path)
            backend_python.parent.mkdir(parents=True)
            backend_python.write_text("", encoding="utf-8")
        if capture:
            return cli.CommandResult(
                returncode=0,
                stdout=(
                    '{"status":"platform_unsupported","environment":'
                    '{"error":"macOS Intel is unsupported for Noofy managed ComfyUI runtime."}}\n'
                ),
            )
        return cli.CommandResult(returncode=0)

    cli.NoofyCheckout(root=tmp_path, python_executable="/usr/bin/python3", command_runner=runner).install(
        skip_frontend=True,
    )

    output = capsys.readouterr().out
    assert "Managed ComfyUI runtime status: platform_unsupported" in output
    assert "Managed runtime note: macOS Intel is unsupported" in output
    assert "Noofy source checkout is installed." in output


def test_install_fails_cleanly_when_runtime_preparation_fails(tmp_path: Path, monkeypatch) -> None:
    cli = load_noofy_cli()
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    def runner(command, cwd, env, capture):
        if command[1:3] == ["-m", "venv"]:
            backend_python = cli.backend_python_path(tmp_path)
            backend_python.parent.mkdir(parents=True)
            backend_python.write_text("", encoding="utf-8")
        if capture:
            return cli.CommandResult(
                returncode=0,
                stdout=(
                    '{"status":"python_missing","environment":'
                    '{"runtime_distribution":"source_checkout",'
                    '"expected_python_version":"3.13",'
                    '"bootstrap_python_attempts":['
                    '{"python_executable":"python3.13","exists":false,'
                    '"python_version":null},'
                    '{"python_executable":"python3","exists":true,'
                    '"python_version":"3.14"}]}}\n'
                ),
            )
        return cli.CommandResult(returncode=0)

    checkout = cli.NoofyCheckout(
        root=tmp_path,
        python_executable="/usr/bin/python3",
        command_runner=runner,
    )

    with pytest.raises(SystemExit) as exc_info:
        checkout.install(skip_frontend=True)

    message = str(exc_info.value)
    assert "python_missing" in message
    assert "Noofy managed ComfyUI needs Python 3.13" in message
    assert "Priority 1 - recommended: use uv-managed Python" in message
    assert "python3.13 (missing)" in message
    assert "python3 (3.14)" in message
    assert "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE" in message
    assert "sudo" not in message
