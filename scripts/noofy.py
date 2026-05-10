#!/usr/bin/env python3
"""Source-checkout install and run helper for Noofy.

This script intentionally uses only the Python standard library. It is the
cross-platform implementation behind thin Makefile and PowerShell wrappers.
"""

from __future__ import annotations

import argparse
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

BOOTSTRAP_RUNTIME_CODE = r"""
import asyncio
import json

from app.engine.factory import create_default_engine_service


async def main():
    service = create_default_engine_service()
    try:
        result = await service.bootstrap_comfyui_runtime()
        print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
        if result.status not in {"prepared", "already_prepared"}:
            raise SystemExit(1)
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
) -> dict[str, str]:
    env = dict(os.environ)
    api_base = f"http://{backend_host}:{backend_port}/api"
    env.update(
        {
            "NOOFY_DATA_DIR": str(data_dir),
            "COMFYUI_RUNTIME_MODE": "managed",
            "NOOFY_BACKEND_HOST": backend_host,
            "NOOFY_BACKEND_PORT": str(backend_port),
        }
    )
    if include_frontend_api:
        env["VITE_NOOFY_API_BASE_URL"] = api_base
    return env


def bootstrap_runtime_command(backend_python: Path) -> list[str]:
    return [str(backend_python), "-c", BOOTSTRAP_RUNTIME_CODE]


def runtime_status_command(backend_python: Path) -> list[str]:
    return [str(backend_python), "-c", RUNTIME_STATUS_CODE]


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
            self.bootstrap_managed_runtime(data_dir=data_dir)
        print()
        print("Noofy source checkout is installed.")
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

    def bootstrap_managed_runtime(self, *, data_dir: Path) -> None:
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
        backend = subprocess.Popen(backend_command, cwd=self.backend_dir, env=backend_env)
        print(f"Starting Noofy frontend: {frontend_url}")
        frontend = subprocess.Popen(frontend_command, cwd=self.frontend_dir, env=frontend_env)
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
        terminate_processes(processes)
        return 130


def terminate_processes(
    processes: list[subprocess.Popen[bytes]],
    *,
    except_process: subprocess.Popen[bytes] | None = None,
) -> None:
    for process in processes:
        if process is except_process or process.poll() is not None:
            continue
        with suppress_process_errors():
            if os.name == "nt":
                process.terminate()
            else:
                process.send_signal(signal.SIGTERM)
    deadline = time.monotonic() + 8
    for process in processes:
        if process is except_process:
            continue
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            with suppress_process_errors():
                process.kill()


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
