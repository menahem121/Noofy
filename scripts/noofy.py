#!/usr/bin/env python3
"""Source-checkout install and run helper for Noofy.

This script intentionally uses only the Python standard library. It is the
cross-platform implementation behind thin Makefile and PowerShell wrappers.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
DEFAULT_DATA_DIR = REPO_ROOT / ".noofy-runtime" / "data"
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8765
RUNTIME_BOOTSTRAP_READY_STATUSES = {"prepared", "already_prepared"}
RUNTIME_BOOTSTRAP_NONFATAL_INSTALL_STATUSES = {"platform_unsupported"}

BOOTSTRAP_RUNTIME_CODE = r"""
import asyncio
import json

from app.engine.factory import create_default_engine_service


async def main():
    service = create_default_engine_service()
    try:
        result = await service.bootstrap_comfyui_runtime()
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    finally:
        await service.shutdown()


asyncio.run(main())
"""

RUNTIME_STATUS_CODE = r"""
import asyncio
import json

from app.engine.factory import create_default_engine_service


async def main():
    service = create_default_engine_service()
    try:
        result = await service.runtime_manager.status()
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    finally:
        await service.shutdown()


asyncio.run(main())
"""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


CommandRunner = Callable[
    [list[str], Optional[Path], Optional[Mapping[str, str]], bool],
    CommandResult,
]


def backend_python_path(root: Path = REPO_ROOT, *, os_name: str | None = None) -> Path:
    selected_os = os.name if os_name is None else os_name
    if selected_os == "nt":
        return root / "backend" / ".venv" / "Scripts" / "python.exe"
    return root / "backend" / ".venv" / "bin" / "python"


def backend_venv_bin_dir(root: Path = REPO_ROOT, *, os_name: str | None = None) -> Path:
    selected_os = os.name if os_name is None else os_name
    if selected_os == "nt":
        return root / "backend" / ".venv" / "Scripts"
    return root / "backend" / ".venv" / "bin"


def frontend_install_command(frontend_dir: Path = FRONTEND_DIR) -> list[str]:
    if (frontend_dir / "package-lock.json").exists():
        return ["npm", "ci"]
    return ["npm", "install"]


def source_checkout_env(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    backend_host: str = DEFAULT_BACKEND_HOST,
    backend_port: int = DEFAULT_BACKEND_PORT,
    include_frontend_api: bool = False,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    api_base = f"http://{backend_host}:{backend_port}/api"
    env.update(
        {
            "NOOFY_DATA_DIR": str(data_dir),
            "COMFYUI_RUNTIME_MODE": "managed",
            "NOOFY_BACKEND_HOST": backend_host,
            "NOOFY_BACKEND_PORT": str(backend_port),
        }
    )
    configure_source_checkout_api_key_store(env)
    if include_frontend_api:
        env["VITE_NOOFY_API_BASE_URL"] = api_base
        env["VITE_DEV_BACKEND_PORT"] = str(backend_port)
    return env


def configure_source_checkout_api_key_store(env: dict[str, str]) -> None:
    """Default source-checkout runs to encrypted vault storage.

    Headless Linux servers commonly lack an unlocked OS keyring. The backend
    still respects explicit `NOOFY_API_KEY_STORE` choices, but source-checkout
    helpers should be usable out of the box.
    """

    raw_mode = env.get("NOOFY_API_KEY_STORE")
    mode = (raw_mode or "").strip().lower()
    explicit_mode = raw_mode is not None and bool(raw_mode.strip())
    if explicit_mode and mode != "encrypted-vault":
        return

    env["NOOFY_API_KEY_STORE"] = "encrypted-vault"
    passphrase_path = env.get("NOOFY_API_KEY_VAULT_PASSPHRASE_FILE")
    if not passphrase_path:
        passphrase_path = str(source_checkout_api_key_vault_passphrase_file(env).resolve(strict=False))
        env["NOOFY_API_KEY_VAULT_PASSPHRASE_FILE"] = passphrase_path
    else:
        passphrase_path = str(Path(passphrase_path).expanduser().resolve(strict=False))
        env["NOOFY_API_KEY_VAULT_PASSPHRASE_FILE"] = passphrase_path

    data_dir = Path(env["NOOFY_DATA_DIR"]).expanduser().resolve(strict=False)
    source_runtime_dir = DEFAULT_DATA_DIR.parent.resolve(strict=False)
    if "NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE" not in env and _is_relative_to(data_dir, source_runtime_dir):
        env["NOOFY_ALLOW_REPO_LOCAL_SECRET_STORAGE"] = "1"

    ensure_api_key_vault_passphrase(Path(passphrase_path))


def source_checkout_api_key_vault_passphrase_file(env: Mapping[str, str] | None = None) -> Path:
    _env = os.environ if env is None else env
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Noofy" / "api-key-vault.passphrase"
    if sys.platform == "win32":
        appdata = _env.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Noofy" / "api-key-vault.passphrase"
    xdg_config_home = _env.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "noofy" / "api-key-vault.passphrase"


def ensure_api_key_vault_passphrase(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            path.parent.chmod(0o700)
    if path.exists():
        if not path.is_file():
            raise SystemExit(f"API key vault passphrase path exists but is not a file: {path}")
        if os.name == "posix":
            with contextlib.suppress(OSError):
                path.chmod(0o600)
        if path.stat().st_size > 0:
            return
        flags = os.O_WRONLY | os.O_TRUNC
    else:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL

    passphrase = base64.urlsafe_b64encode(os.urandom(48)).decode("ascii")
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        file.write(f"{passphrase}\n")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def bootstrap_runtime_command(backend_python: Path) -> list[str]:
    return [str(backend_python), "-c", BOOTSTRAP_RUNTIME_CODE]


def runtime_status_command(backend_python: Path) -> list[str]:
    return [str(backend_python), "-c", RUNTIME_STATUS_CODE]


def managed_runtime_python_setup_guidance(
    environment: Mapping[str, object],
    *,
    platform: str | None = None,
) -> str | None:
    expected_version = environment.get("expected_python_version")
    if not isinstance(expected_version, str) or not expected_version:
        return None
    if environment.get("runtime_distribution") == "packaged":
        return None

    attempts = environment.get("bootstrap_python_attempts")
    selected_platform = sys.platform if platform is None else platform
    lines = [
        "",
        "Source/development Python fix",
        "=============================",
        f"Noofy managed ComfyUI needs Python {expected_version}.",
        "Your backend/source-helper Python can be different.",
    ]
    attempted_lines = _format_bootstrap_python_attempt_lines(attempts)
    if attempted_lines:
        lines += [
            "",
            "Already tried:",
            *[f"  - {line}" for line in attempted_lines],
        ]
    lines += [
        "",
        "Noofy will not install system Python or run privileged commands for you.",
        "",
    ]

    if selected_platform == "darwin":
        lines += [
            "Priority 1 - recommended: use uv-managed Python",
            f"  backend/.venv/bin/uv python install {expected_version}",
            (
                '  COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE="$(backend/.venv/bin/uv '
                f'python find {expected_version})" make install'
            ),
            "",
            "Priority 2 - macOS package manager fallback",
            f"  brew install python@{expected_version}",
            (
                '  COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE="$(brew --prefix '
                f"python@{expected_version})/bin/python{expected_version}\" make install"
            ),
        ]
    elif selected_platform == "win32":
        lines += [
            "Priority 1 - recommended: use uv-managed Python",
            f"  .\\backend\\.venv\\Scripts\\uv.exe python install {expected_version}",
            f"  $py = .\\backend\\.venv\\Scripts\\uv.exe python find {expected_version}",
            "  $env:COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE = $py",
            "  .\\scripts\\install.ps1",
            "",
            "Priority 2 - Windows package manager fallback",
            f"  winget install Python.Python.{expected_version}",
            (
                f"  $py = py -{expected_version} -c \"import sys; "
                "print(sys.executable)\""
            ),
            "  $env:COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE = $py",
            "  .\\scripts\\install.ps1",
        ]
    else:
        lines += [
            "Priority 1 - recommended: use uv-managed Python",
            f"  backend/.venv/bin/uv python install {expected_version}",
            (
                '  COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE="$(backend/.venv/bin/uv '
                f'python find {expected_version})" make install'
            ),
            "",
            "Priority 2 - Linux distro package fallback, only if your distro offers it",
            f"  apt install python{expected_version} python{expected_version}-venv",
            f"  dnf install python{expected_version}",
            f"  COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE=python{expected_version} make install",
            "",
            "Priority 3 - any other developer Python tool",
            (
                "  Point COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE at a real "
                f"Python {expected_version} executable, then rerun make install."
            ),
        ]
    return "\n".join(lines)


def _format_bootstrap_python_attempt_lines(attempts: object) -> list[str]:
    if not isinstance(attempts, list):
        return []
    lines: list[str] = []
    for attempt in attempts:
        if not isinstance(attempt, Mapping):
            continue
        executable = attempt.get("python_executable")
        if not isinstance(executable, str) or not executable:
            continue
        exists = attempt.get("exists")
        version = attempt.get("python_version")
        if exists is False:
            lines.append(f"{executable} (missing)")
        elif isinstance(version, str) and version:
            lines.append(f"{executable} ({version})")
        else:
            lines.append(f"{executable} (version unknown)")
    return lines


def run_command(
    command: list[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
) -> CommandResult:
    print(f"$ {_format_command(command)}")
    if capture:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        return CommandResult(result.returncode, result.stdout, result.stderr)

    result = subprocess.run(command, cwd=cwd, env=dict(env) if env is not None else None)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command)
    return CommandResult(result.returncode)


def _format_command(command: list[str]) -> str:
    redacted = list(command)
    for index, part in enumerate(redacted[:-1]):
        if part == "-c":
            redacted[index + 1] = "<python-code>"
            break
    return " ".join(redacted)


class NoofyCheckout:
    def __init__(
        self,
        root: Path = REPO_ROOT,
        *,
        python_executable: str = sys.executable,
        command_runner: CommandRunner = run_command,
    ) -> None:
        self.root = root
        self.backend_dir = root / "backend"
        self.frontend_dir = root / "frontend"
        self.python_executable = python_executable
        self.command_runner = command_runner

    @property
    def backend_python(self) -> Path:
        return backend_python_path(self.root)

    @property
    def backend_venv_dir(self) -> Path:
        return self.root / "backend" / ".venv"

    @property
    def backend_venv_bin_dir(self) -> Path:
        return backend_venv_bin_dir(self.root)

    def install(
        self,
        *,
        data_dir: Path = DEFAULT_DATA_DIR,
        skip_frontend: bool = False,
        skip_runtime: bool = False,
    ) -> None:
        require_python_version()
        self.ensure_backend_venv()
        self.install_backend_dependencies()
        if not skip_frontend:
            self.install_frontend_dependencies()
        data_dir.mkdir(parents=True, exist_ok=True)
        if not skip_runtime:
            runtime_ready = self.bootstrap_managed_runtime(data_dir=data_dir)
        else:
            runtime_ready = None
        print()
        print("Noofy source checkout is installed.")
        if runtime_ready is False:
            print("Managed ComfyUI runtime was not prepared on this machine.")
            print("Workflow execution that requires managed ComfyUI will be unavailable.")
        if runtime_ready is None:
            print("Managed ComfyUI runtime preparation was skipped.")
        print("Run it with: make run")

    def ensure_backend_venv(self) -> None:
        if self.backend_python.exists():
            print(f"Backend venv already exists: {self.backend_venv_dir}")
            return
        print(f"Creating trusted backend venv: {self.backend_venv_dir}")
        self.command_runner(
            [self.python_executable, "-m", "venv", str(self.backend_venv_dir)],
            None,
            None,
            False,
        )

    def install_backend_dependencies(self) -> None:
        print("Installing trusted backend dependencies into backend/.venv")
        self.command_runner(
            [str(self.backend_python), "-m", "pip", "install", "--upgrade", "pip"],
            self.backend_dir,
            None,
            False,
        )
        self.command_runner(
            [str(self.backend_python), "-m", "pip", "install", "-e", ".[dev]"],
            self.backend_dir,
            None,
            False,
        )

    def install_frontend_dependencies(self) -> None:
        require_node()
        command = frontend_install_command(self.frontend_dir)
        print(f"Installing frontend dependencies with {' '.join(command)}")
        self.command_runner(command, self.frontend_dir, None, False)

    def bootstrap_managed_runtime(self, *, data_dir: Path) -> bool:
        print("Preparing managed ComfyUI runtime through the backend bootstrap service")
        env = source_checkout_env(data_dir=data_dir)
        env["PATH"] = str(self.backend_venv_bin_dir) + os.pathsep + env.get("PATH", "")
        result = self.command_runner(
            bootstrap_runtime_command(self.backend_python),
            self.backend_dir,
            env,
            True,
        )
        payload = _load_json_output(result.stdout)
        status = payload.get("status", "unknown")
        environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
        torch_plan = environment.get("torch_install_plan") if isinstance(environment, dict) else {}
        python_executable = environment.get("python_executable") if isinstance(environment, dict) else None
        print(f"Managed ComfyUI runtime status: {status}")
        if python_executable:
            print(f"Managed runtime Python: {python_executable}")
        if isinstance(torch_plan, dict) and torch_plan.get("index_url"):
            print(f"PyTorch wheel index: {torch_plan['index_url']}")
        error = environment.get("error") if isinstance(environment, dict) else None
        if error:
            print(f"Managed runtime note: {error}")
        if status in RUNTIME_BOOTSTRAP_READY_STATUSES:
            return True
        if status in RUNTIME_BOOTSTRAP_NONFATAL_INSTALL_STATUSES:
            return False
        message = f"Managed ComfyUI runtime preparation failed with status: {status}"
        guidance = managed_runtime_python_setup_guidance(environment)
        if guidance:
            message = f"{message}\n{guidance}"
        raise SystemExit(message)

    def doctor(self, *, data_dir: Path = DEFAULT_DATA_DIR) -> int:
        print("Noofy source-checkout doctor")
        print(f"Repo: {self.root}")
        print(f"Data dir: {data_dir}")
        print(f"Backend Python: {self.backend_python}")
        if not self.backend_python.exists():
            print("Backend venv is missing. Run: make install")
            return 1

        env = source_checkout_env(data_dir=data_dir)
        env["PATH"] = str(self.backend_venv_bin_dir) + os.pathsep + env.get("PATH", "")
        result = self.command_runner(runtime_status_command(self.backend_python), self.backend_dir, env, True)
        payload = _load_json_output(result.stdout)
        environment = payload.get("environment") if isinstance(payload.get("environment"), dict) else {}
        print(f"Runtime status: {payload.get('status', 'unknown')}")
        print(f"Runtime prepared: {environment.get('prepared')}")
        error = environment.get("error")
        if error:
            print(f"Runtime error: {error}")
            return 1
        return 0

    def serve(
        self,
        *,
        data_dir: Path = DEFAULT_DATA_DIR,
        host: str = DEFAULT_BACKEND_HOST,
        backend_port: int = DEFAULT_BACKEND_PORT,
    ) -> int:
        if not self.backend_python.exists():
            print("Backend venv is missing. Run: make install", file=sys.stderr)
            return 1
        env = source_checkout_env(data_dir=data_dir, backend_host=host, backend_port=backend_port)
        env["PATH"] = str(self.backend_venv_bin_dir) + os.pathsep + env.get("PATH", "")
        command = [str(self.backend_python), "-m", "app", "--host", host, "--port", str(backend_port)]
        return subprocess.call(command, cwd=self.backend_dir, env=env)

    def run(
        self,
        *,
        data_dir: Path = DEFAULT_DATA_DIR,
        host: str = DEFAULT_BACKEND_HOST,
        backend_port: int = DEFAULT_BACKEND_PORT,
        skip_frontend: bool = False,
    ) -> int:
        if skip_frontend:
            return self.serve(data_dir=data_dir, host=host, backend_port=backend_port)
        if not self.backend_python.exists():
            print("Backend venv is missing. Run: make install", file=sys.stderr)
            return 1
        require_node()
        if not (self.frontend_dir / "node_modules").exists():
            print("Frontend dependencies are missing. Run: make install", file=sys.stderr)
            return 1

        backend_env = source_checkout_env(data_dir=data_dir, backend_host=host, backend_port=backend_port)
        backend_env["PATH"] = str(self.backend_venv_bin_dir) + os.pathsep + backend_env.get("PATH", "")
        frontend_env = source_checkout_env(
            data_dir=data_dir,
            backend_host=host,
            backend_port=backend_port,
            include_frontend_api=True,
        )
        backend_api = frontend_env["VITE_NOOFY_API_BASE_URL"]
        frontend_url = "http://127.0.0.1:5173"

        backend_command = [str(self.backend_python), "-m", "app", "--host", host, "--port", str(backend_port)]
        frontend_command = ["npm", "run", "dev"]

        print(f"Starting Noofy backend: {backend_api}")
        backend = subprocess.Popen(
            backend_command,
            cwd=self.backend_dir,
            env=backend_env,
            **child_process_popen_kwargs(),
        )
        print(f"Starting Noofy frontend: {frontend_url}")
        frontend = subprocess.Popen(
            frontend_command,
            cwd=self.frontend_dir,
            env=frontend_env,
            **child_process_popen_kwargs(),
        )
        print()
        print(f"Open Noofy at {frontend_url}")
        print("Press Ctrl+C to stop Noofy.")
        return wait_for_processes([backend, frontend])


def require_python_version() -> None:
    if sys.version_info < (3, 11):
        raise SystemExit("Noofy requires Python 3.11 or newer.")


def require_command(command: str, message: str) -> None:
    if shutil.which(command) is None:
        raise SystemExit(message)


# Minimum Node.js major version required to build and run the frontend.
_MIN_NODE_MAJOR = 18


def require_node() -> None:
    """Check that node and npm are present and meet the minimum version."""
    node = shutil.which("node")
    npm = shutil.which("npm")
    missing = [tool for tool, found in (("node", node), ("npm", npm)) if not found]
    if missing:
        _fail_missing_node(missing)

    result = subprocess.run(
        ["node", "--version"], capture_output=True, text=True
    )
    raw = result.stdout.strip().lstrip("v")
    try:
        major = int(raw.split(".")[0])
    except ValueError:
        major = 0
    if major < _MIN_NODE_MAJOR:
        _fail_old_node(raw)

    npm_result = subprocess.run(["npm", "--version"], capture_output=True, text=True)
    print(f"node {result.stdout.strip()}  npm {npm_result.stdout.strip()}")


def _fail_missing_node(missing: list[str]) -> None:
    tools = " and ".join(missing)
    platform = sys.platform
    msg = [
        f"\nError: {tools} not found.",
        "Noofy source-checkout requires Node.js LTS (v18 or newer) and npm",
        "to build and run the frontend. These are source-build tools and are",
        "not installed automatically.",
        "",
    ]
    if platform == "darwin":
        msg += [
            "Install on macOS:",
            "  brew install node          # Homebrew",
            "  https://nodejs.org/en/download  # official LTS installer",
            "  https://github.com/nvm-sh/nvm   # version manager (nvm)",
        ]
    elif platform == "win32":
        msg += [
            "Install on Windows:",
            "  https://nodejs.org/en/download  # official LTS installer (.msi)",
            "  winget install OpenJS.NodeJS.LTS",
            "  https://github.com/coreybutler/nvm-windows  # version manager",
        ]
    else:
        msg += [
            "Install on Linux:",
            "  sudo apt install nodejs npm          # Debian/Ubuntu (may need NodeSource for LTS)",
            "  sudo dnf install nodejs npm          # Fedora/RHEL",
            "  https://github.com/nodesource/distributions  # NodeSource LTS packages",
            "  https://github.com/nvm-sh/nvm        # version manager (nvm)",
        ]
    raise SystemExit("\n".join(msg))


def _fail_old_node(found: str) -> None:
    raise SystemExit(
        f"\nError: Node.js v{found} is too old.\n"
        f"Noofy requires Node.js LTS v{_MIN_NODE_MAJOR} or newer.\n"
        "Update via your package manager or https://nodejs.org/en/download"
    )


def wait_for_processes(processes: list[subprocess.Popen[bytes]]) -> int:
    try:
        while True:
            for process in processes:
                returncode = process.poll()
                if returncode is not None:
                    terminate_processes(processes, except_process=process)
                    return int(returncode)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping Noofy...")
        terminate_processes(processes)
        return 130


def child_process_popen_kwargs(*, os_name: str | None = None) -> dict[str, object]:
    selected_os = os.name if os_name is None else os_name
    if selected_os == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": creationflags} if creationflags else {}
    return {"start_new_session": True}


def terminate_processes(
    processes: list[subprocess.Popen[bytes]],
    *,
    except_process: subprocess.Popen[bytes] | None = None,
) -> None:
    with suppress_cleanup_interrupts() as interrupted:
        for process in processes:
            if process is except_process:
                continue
            signal_process_tree(process, signal.SIGTERM)

        wait_for_process_shutdown(
            processes,
            except_process=except_process,
            deadline=time.monotonic() + 8,
            interrupted=interrupted,
        )

        for process in processes:
            if process is except_process:
                continue
            kill_process_tree(process)

        wait_for_process_shutdown(
            processes,
            except_process=except_process,
            deadline=time.monotonic() + 2,
            interrupted=interrupted,
        )


def wait_for_process_shutdown(
    processes: list[subprocess.Popen[bytes]],
    *,
    except_process: subprocess.Popen[bytes] | None,
    deadline: float,
    interrupted: Callable[[], bool],
) -> None:
    for process in processes:
        if process is except_process:
            continue
        while process.poll() is None and time.monotonic() < deadline and not interrupted():
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass


def signal_process_tree(process: subprocess.Popen[bytes], signal_number: int) -> None:
    with suppress_process_errors():
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal_number)


def kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    with suppress_process_errors():
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)


@contextlib.contextmanager
def suppress_cleanup_interrupts():
    interrupted = False

    def mark_interrupted(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, mark_interrupted)
    signal.signal(signal.SIGTERM, mark_interrupted)
    try:
        yield lambda: interrupted
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)


def suppress_process_errors():
    return contextlib.suppress(ProcessLookupError, OSError)


def _load_json_output(stdout: str) -> dict[str, object]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError:
        print(stdout, end="")
        return {}
    return value if isinstance(value, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install and run a Noofy source checkout.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    install = subcommands.add_parser("install", help="Install Noofy for source-checkout use.")
    install.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    install.add_argument("--skip-frontend", action="store_true")
    install.add_argument("--skip-runtime", action="store_true")

    run = subcommands.add_parser("run", help="Run the Noofy backend and frontend.")
    run.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    run.add_argument("--host", default=DEFAULT_BACKEND_HOST)
    run.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    run.add_argument("--skip-frontend", action="store_true")

    serve = subcommands.add_parser("serve", help="Run only the Noofy backend API.")
    serve.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    serve.add_argument("--host", default=DEFAULT_BACKEND_HOST)
    serve.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)

    doctor = subcommands.add_parser("doctor", help="Report source-checkout readiness.")
    doctor.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    checkout = NoofyCheckout()
    if args.command == "install":
        checkout.install(data_dir=args.data_dir, skip_frontend=args.skip_frontend, skip_runtime=args.skip_runtime)
        return 0
    if args.command == "run":
        return checkout.run(
            data_dir=args.data_dir,
            host=args.host,
            backend_port=args.backend_port,
            skip_frontend=args.skip_frontend,
        )
    if args.command == "serve":
        return checkout.serve(data_dir=args.data_dir, host=args.host, backend_port=args.backend_port)
    if args.command == "doctor":
        return checkout.doctor(data_dir=args.data_dir)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
