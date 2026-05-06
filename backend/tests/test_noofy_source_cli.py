import importlib.util
import sys
from pathlib import Path


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

    env = cli.source_checkout_env(data_dir=data_dir, backend_host="127.0.0.1", backend_port=9876, include_frontend_api=True)

    assert env["NOOFY_DATA_DIR"] == str(data_dir)
    assert env["COMFYUI_RUNTIME_MODE"] == "managed"
    assert env["NOOFY_BACKEND_PORT"] == "9876"
    assert env["VITE_NOOFY_API_BASE_URL"] == "http://127.0.0.1:9876/api"


def test_backend_python_path_uses_windows_scripts_directory(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    assert cli.backend_python_path(tmp_path, os_name="nt") == (
        tmp_path / "backend" / ".venv" / "Scripts" / "python.exe"
    )


def test_runtime_bootstrap_command_delegates_to_backend_service(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    command = cli.bootstrap_runtime_command(tmp_path / "backend" / ".venv" / "bin" / "python")
    code = command[-1]

    assert "create_default_engine_service" in code
    assert "bootstrap_comfyui_runtime" in code
    assert "pip install" not in code
    assert "torch" not in code.lower()


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


def test_run_reports_missing_frontend_dependencies(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    backend_python.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")
    (tmp_path / "frontend").mkdir()

    result = cli.NoofyCheckout(root=tmp_path).run()

    assert result == 1


def test_install_delegates_runtime_preparation_to_bootstrap_service(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    data_dir = tmp_path / ".noofy-runtime" / "data"
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
