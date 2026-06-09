import contextlib
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


def test_source_checkout_env_sets_managed_runtime_and_frontend_proxy_port(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    passphrase_file = config_dir / "noofy" / "api-key-vault.passphrase"

    env = cli.source_checkout_env(
        data_dir=data_dir,
        backend_host="127.0.0.1",
        backend_port=9876,
        include_frontend_dev_proxy=True,
        base_env={
            "XDG_CONFIG_HOME": str(config_dir),
            "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE": str(passphrase_file),
        },
    )

    assert env["NOOFY_DATA_DIR"] == str(data_dir)
    assert env["COMFYUI_RUNTIME_MODE"] == "managed"
    assert env["NOOFY_BACKEND_PORT"] == "9876"
    assert "VITE_NOOFY_API_BASE_URL" not in env
    assert env["VITE_DEV_BACKEND_PORT"] == "9876"
    assert env["NOOFY_API_KEY_STORE"] == "encrypted-vault"
    assert "NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE" not in env
    assert Path(env["NOOFY_API_KEY_VAULT_PASSPHRASE_FILE"]) == passphrase_file
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
        base_env={
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE": str(tmp_path / "secrets" / "vault.passphrase"),
        },
    )

    assert env["NOOFY_API_KEY_STORE"] == "encrypted-vault"
    assert env["NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE"] == "1"


def test_source_checkout_env_does_not_allow_arbitrary_repo_local_secret_storage(tmp_path: Path) -> None:
    cli = load_noofy_cli()

    env = cli.source_checkout_env(
        data_dir=cli.REPO_ROOT / "scratch-data",
        base_env={
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE": str(tmp_path / "secrets" / "vault.passphrase"),
        },
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
            "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE": str(tmp_path / "secrets" / "vault.passphrase"),
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


def test_signal_process_tree_targets_owned_windows_process_tree(monkeypatch) -> None:
    cli = load_noofy_cli()
    calls = []

    class Process:
        pid = 1234

    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(
        cli,
        "terminate_windows_process_tree",
        lambda pid, force=False: calls.append((pid, force)),
    )

    cli.signal_process_tree(Process(), signal.SIGTERM)
    cli.kill_process_tree(Process())

    assert calls == [(1234, False), (1234, True)]


def test_own_process_attaches_windows_kill_on_close_job(monkeypatch, tmp_path: Path) -> None:
    cli = load_noofy_cli()
    process = object()
    monkeypatch.setattr(cli.os, "name", "nt")
    monkeypatch.setattr(cli, "create_windows_kill_on_close_job", lambda child: 99)

    owned = cli.own_process(process, "backend", tmp_path)

    assert owned.windows_job_handle == 99


def test_terminate_processes_signals_exited_process_group(monkeypatch, tmp_path: Path) -> None:
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
            [owned.process.pid for owned in processes]
        ),
    )
    monkeypatch.setattr(
        cli, "kill_process_tree", lambda process: kills.append(process.pid)
    )

    cli.terminate_processes([cli.OwnedProcess(Process(), "backend", tmp_path)])

    assert signals == [(1234, signal.SIGTERM)]
    assert waits == [[1234], [1234]]
    assert kills == [1234]


def test_supervise_processes_cleans_up_after_hangup(monkeypatch) -> None:
    cli = load_noofy_cli()
    process = object()
    owned = cli.OwnedProcess(process, "backend", Path("/checkout/backend"))
    cleaned = []
    hangup = getattr(signal, "SIGHUP", signal.SIGTERM)

    monkeypatch.setattr(cli, "termination_signal_handler", contextlib.nullcontext)
    monkeypatch.setattr(
        cli,
        "wait_for_processes",
        lambda processes: (_ for _ in ()).throw(cli.LauncherTermination(hangup)),
    )
    monkeypatch.setattr(
        cli, "terminate_processes", lambda processes: cleaned.extend(processes)
    )

    assert cli.supervise_processes([owned]) == 128 + hangup
    assert cleaned == [owned]


def test_supervise_processes_cleans_up_when_child_exits(monkeypatch) -> None:
    cli = load_noofy_cli()
    process = object()
    owned = cli.OwnedProcess(process, "backend", Path("/checkout/backend"))
    cleaned = []

    monkeypatch.setattr(cli, "termination_signal_handler", contextlib.nullcontext)
    monkeypatch.setattr(cli, "wait_for_processes", lambda processes: 7)
    monkeypatch.setattr(
        cli, "terminate_processes", lambda processes: cleaned.extend(processes)
    )

    assert cli.supervise_processes([owned]) == 7
    assert cleaned == [owned]


def test_wait_for_backend_listener_returns_when_port_accepts(monkeypatch) -> None:
    cli = load_noofy_cli()
    attempts = []

    class Process:
        def poll(self):
            attempts.append("poll")
            return None

    class Connection:
        def __enter__(self):
            attempts.append("connected")
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(cli.socket, "create_connection", lambda address, timeout: Connection())

    cli.wait_for_backend_listener(Process(), "127.0.0.1", 8765)

    assert attempts == ["poll", "connected"]


def test_wait_for_backend_listener_fails_when_backend_exits(monkeypatch) -> None:
    cli = load_noofy_cli()

    class Process:
        def poll(self):
            return 7

    with pytest.raises(RuntimeError, match="exited before accepting connections"):
        cli.wait_for_backend_listener(Process(), "127.0.0.1", 8765)


def test_supervise_processes_cleans_up_partial_startup(monkeypatch) -> None:
    cli = load_noofy_cli()
    process = object()
    owned = cli.OwnedProcess(process, "backend", Path("/checkout/backend"))
    processes = []
    cleaned = []

    def start():
        processes.append(owned)
        raise OSError("frontend failed to start")

    monkeypatch.setattr(cli, "termination_signal_handler", contextlib.nullcontext)
    monkeypatch.setattr(
        cli, "terminate_processes", lambda children: cleaned.extend(children)
    )

    with pytest.raises(OSError, match="frontend failed to start"):
        cli.supervise_processes(processes, start=start)

    assert cleaned == [owned]


def test_run_waits_for_backend_listener_before_frontend_start(
    tmp_path: Path, monkeypatch
) -> None:
    cli = load_noofy_cli()
    backend_python = cli.backend_python_path(tmp_path)
    backend_python.parent.mkdir(parents=True)
    backend_python.write_text("", encoding="utf-8")
    frontend_dir = tmp_path / "frontend"
    (frontend_dir / "node_modules").mkdir(parents=True)
    events = []

    class Process:
        _next_pid = 1000

        def __init__(self, command, cwd=None, **kwargs):
            self.command = command
            self.cwd = cwd
            self.pid = Process._next_pid
            Process._next_pid += 1
            events.append(("start", Path(cwd).name))

        def poll(self):
            return None

    def supervise(processes, *, start, **kwargs):
        start()
        return 0

    monkeypatch.setenv(
        "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE",
        str(tmp_path / "secrets" / "vault.passphrase"),
    )
    monkeypatch.setattr(cli, "require_node", lambda: None)
    monkeypatch.setattr(cli.subprocess, "Popen", Process)
    monkeypatch.setattr(cli, "write_process_lease", lambda *args: None)
    monkeypatch.setattr(cli, "supervise_processes", supervise)
    monkeypatch.setattr(
        cli,
        "wait_for_backend_listener",
        lambda process, host, port: events.append(("wait", process.pid, host, port)),
    )

    result = cli.NoofyCheckout(root=tmp_path).run()

    assert result == 0
    assert events == [
        ("start", "backend"),
        ("wait", 1000, "127.0.0.1", 8765),
        ("start", "frontend"),
    ]


def test_supervise_processes_does_not_remove_unowned_lease_when_prepare_fails(
    tmp_path: Path, monkeypatch
) -> None:
    cli = load_noofy_cli()
    lease_path = tmp_path / "source-processes.json"
    lease_path.write_text("owned-by-another-launcher", encoding="utf-8")

    monkeypatch.setattr(cli, "termination_signal_handler", contextlib.nullcontext)
    monkeypatch.setattr(cli, "terminate_processes", lambda processes: None)

    with pytest.raises(SystemExit, match="already running"):
        cli.supervise_processes(
            [],
            prepare=lambda: (_ for _ in ()).throw(SystemExit("already running")),
            lease_path=lease_path,
            launcher_lock=contextlib.nullcontext(),
        )

    assert lease_path.read_text(encoding="utf-8") == "owned-by-another-launcher"


def test_supervise_processes_installs_handlers_before_prepare_and_start(monkeypatch) -> None:
    cli = load_noofy_cli()
    calls = []

    @contextlib.contextmanager
    def handlers():
        calls.append("handlers-enter")
        yield
        calls.append("handlers-exit")

    monkeypatch.setattr(cli, "termination_signal_handler", handlers)
    monkeypatch.setattr(cli, "wait_for_processes", lambda processes: calls.append("wait") or 0)
    monkeypatch.setattr(cli, "terminate_processes", lambda processes: calls.append("cleanup"))

    assert cli.supervise_processes(
        [], prepare=lambda: calls.append("prepare"), start=lambda: calls.append("start")
    ) == 0
    assert calls == [
        "handlers-enter",
        "prepare",
        "start",
        "wait",
        "handlers-exit",
        "cleanup",
    ]


def test_recover_stale_processes_only_signals_validated_checkout_children(
    tmp_path: Path, monkeypatch
) -> None:
    cli = load_noofy_cli()
    data_dir = tmp_path / "data"
    checkout = tmp_path / "checkout"
    lease = cli.source_process_lease_path(data_dir)
    lease.parent.mkdir(parents=True)
    lease.write_text(
        '{"checkout_root":"%s","launcher_pid":123,"launcher_identity":"old","children":['
        '{"pid":456,"identity":"child-456","role":"backend","cwd":"%s"},'
        '{"pid":789,"identity":"child-789","role":"frontend","cwd":"%s"}]}'
        % (checkout, checkout / "backend", checkout / "frontend"),
        encoding="utf-8",
    )
    signaled = []
    monkeypatch.setattr(cli, "process_exists", lambda pid: pid == 456)
    monkeypatch.setattr(cli, "owned_process_tree_exists", lambda pid: pid == 456)
    monkeypatch.setattr(cli, "process_identity", lambda pid: f"child-{pid}")
    monkeypatch.setattr(
        cli, "stale_process_matches", lambda pid, role, cwd, root: pid == 456
    )
    monkeypatch.setattr(cli, "signal_stale_process_group", signaled.append)

    cli.recover_stale_processes(data_dir, checkout)

    assert signaled == [456]
    assert not lease.exists()


def test_recover_stale_processes_refuses_to_signal_unverified_live_process(
    tmp_path: Path, monkeypatch
) -> None:
    cli = load_noofy_cli()
    data_dir = tmp_path / "data"
    checkout = tmp_path / "checkout"
    lease = cli.source_process_lease_path(data_dir)
    lease.parent.mkdir(parents=True)
    lease.write_text(
        '{"checkout_root":"%s","launcher_pid":123,"launcher_identity":"old","children":['
        '{"pid":456,"identity":"child-456","role":"backend","cwd":"%s"}]}'
        % (checkout, checkout / "backend"),
        encoding="utf-8",
    )
    signaled = []
    monkeypatch.setattr(cli, "process_exists", lambda pid: pid == 456)
    monkeypatch.setattr(cli, "owned_process_tree_exists", lambda pid: pid == 456)
    monkeypatch.setattr(cli, "process_identity", lambda pid: f"child-{pid}")
    monkeypatch.setattr(cli, "stale_process_matches", lambda *args: False)
    monkeypatch.setattr(cli, "signal_stale_process_group", signaled.append)

    with pytest.raises(SystemExit, match="could not be safely identified"):
        cli.recover_stale_processes(data_dir, checkout)

    assert signaled == []
    assert lease.exists()


def test_recover_stale_processes_refuses_pid_reuse(tmp_path: Path, monkeypatch) -> None:
    cli = load_noofy_cli()
    data_dir = tmp_path / "data"
    checkout = tmp_path / "checkout"
    lease = cli.source_process_lease_path(data_dir)
    lease.parent.mkdir(parents=True)
    lease.write_text(
        '{"checkout_root":"%s","launcher_pid":123,"launcher_identity":"old-launcher",'
        '"children":[{"pid":456,"identity":"old-child","role":"backend","cwd":"%s"}]}'
        % (checkout, checkout / "backend"),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "owned_process_tree_exists", lambda pid: True)
    monkeypatch.setattr(cli, "process_identity", lambda pid: "reused-pid")
    signaled = []
    monkeypatch.setattr(cli, "signal_stale_process_group", signaled.append)

    with pytest.raises(SystemExit, match="could not be safely identified"):
        cli.recover_stale_processes(data_dir, checkout)

    assert signaled == []
    assert lease.exists()


def test_write_process_lease_requires_stable_identity(tmp_path: Path, monkeypatch) -> None:
    cli = load_noofy_cli()
    data_dir = tmp_path / "data"
    checkout = tmp_path / "checkout"

    class Process:
        pid = 456

    monkeypatch.setattr(cli, "process_identity", lambda pid: None)

    with pytest.raises(RuntimeError, match="stable Noofy launcher process identity"):
        cli.write_process_lease(
            data_dir,
            checkout,
            [cli.OwnedProcess(Process(), "backend", checkout / "backend")],
        )

    assert not cli.source_process_lease_path(data_dir).exists()


def test_source_launcher_lock_rejects_second_live_launcher(tmp_path: Path) -> None:
    cli = load_noofy_cli()
    lock_path = cli.source_process_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    first_lock = cli.acquire_source_launcher_lock(lock_path)

    try:
        with pytest.raises(SystemExit, match="already running"):
            cli.acquire_source_launcher_lock(lock_path)
    finally:
        cli.release_source_launcher_lock(first_lock)

    assert lock_path.exists()
    assert lock_path.read_bytes() in (b"", b"\0")


def test_linux_proc_identity_parser_accepts_spaces_and_parentheses_in_command() -> None:
    cli = load_noofy_cli()
    stat = (
        "123 (worker name) extra) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 "
        "424242 20"
    )

    assert cli.linux_proc_stat_start_time(stat) == "424242"


def test_stale_frontend_validation_accepts_owned_vite_descendant(
    tmp_path: Path, monkeypatch
) -> None:
    cli = load_noofy_cli()
    checkout = tmp_path / "checkout"
    frontend = checkout / "frontend"
    frontend.mkdir(parents=True)
    monkeypatch.setattr(cli.os, "name", "posix")
    monkeypatch.setattr(cli, "owned_process_tree_exists", lambda pgid: True)
    monkeypatch.setattr(
        cli, "process_group_members", lambda pgid: [(789, "node vite --host 127.0.0.1")]
    )
    monkeypatch.setattr(cli, "process_cwd", lambda pid: frontend.resolve())

    assert cli.stale_process_matches(456, "frontend", frontend, checkout)


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
    monkeypatch.setenv(
        "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE",
        str(tmp_path / "secrets" / "vault.passphrase"),
    )
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
    monkeypatch.setenv(
        "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE",
        str(tmp_path / "secrets" / "vault.passphrase"),
    )

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
    monkeypatch.setenv(
        "NOOFY_API_KEY_VAULT_PASSPHRASE_FILE",
        str(tmp_path / "secrets" / "vault.passphrase"),
    )

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
