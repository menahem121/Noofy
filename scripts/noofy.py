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
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Mapping, Optional


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
DEFAULT_DATA_DIR = REPO_ROOT / ".noofy-runtime" / "data"
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = 8765
RUNTIME_TOOL_VERSIONS_RELATIVE_PATH = (
    Path("backend") / "app" / "runtime" / "runtime_tool_versions.json"
)
RUNTIME_BOOTSTRAP_READY_STATUSES = {"prepared", "already_prepared"}
RUNTIME_BOOTSTRAP_NONFATAL_INSTALL_STATUSES = {"platform_unsupported"}
SOURCE_PROCESS_LEASE_NAME = "source-processes.json"
SOURCE_PROCESS_LOCK_NAME = "source-processes.lock"

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
        result = await service.runtime_manager.status(include_environment=True)
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


class LauncherTermination(Exception):
    def __init__(self, signal_number: int):
        self.signal_number = signal_number
        super().__init__(f"Noofy launcher received signal {signal_number}")


@dataclass(frozen=True)
class OwnedProcess:
    process: subprocess.Popen[bytes]
    role: str
    cwd: Path
    windows_job_handle: int | None = None


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


def backend_uv_path(root: Path = REPO_ROOT, *, os_name: str | None = None) -> Path:
    selected_os = os.name if os_name is None else os_name
    executable_name = "uv.exe" if selected_os == "nt" else "uv"
    return backend_venv_bin_dir(root, os_name=selected_os) / executable_name


def runtime_tool_versions_path(root: Path = REPO_ROOT) -> Path:
    return root / RUNTIME_TOOL_VERSIONS_RELATIVE_PATH


def supported_uv_version(root: Path = REPO_ROOT) -> str:
    path = runtime_tool_versions_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            f"Noofy source checkout is missing its runtime tool version manifest: {path}"
        ) from None
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"Noofy source checkout has an invalid runtime tool version manifest: {path}: {exc}"
        ) from None

    version = payload.get("uv")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit(
            f"Noofy source checkout runtime tool version manifest must define a uv version: {path}"
        )
    return version.strip()


def supported_uv_requirement(root: Path = REPO_ROOT) -> str:
    return f"uv=={supported_uv_version(root)}"


def parse_uv_version_output(output: str) -> str | None:
    parts = output.strip().split()
    if len(parts) >= 2 and parts[0] == "uv":
        return parts[1]
    return None


def frontend_install_command(frontend_dir: Path = FRONTEND_DIR) -> list[str]:
    if (frontend_dir / "package-lock.json").exists():
        return ["npm", "ci"]
    return ["npm", "install"]


def source_checkout_env(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    backend_host: str = DEFAULT_BACKEND_HOST,
    backend_port: int = DEFAULT_BACKEND_PORT,
    include_frontend_dev_proxy: bool = False,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    env.update(
        {
            "NOOFY_DATA_DIR": str(data_dir),
            "COMFYUI_RUNTIME_MODE": "managed",
            "NOOFY_BACKEND_HOST": backend_host,
            "NOOFY_BACKEND_PORT": str(backend_port),
        }
    )
    configure_source_checkout_api_key_store(env)
    if include_frontend_dev_proxy:
        # Keep browser dev builds on the same-origin Vite /api proxy. An
        # absolute 127.0.0.1 backend URL breaks remote/forwarded development
        # because the browser resolves it on the client machine.
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

    @property
    def backend_uv(self) -> Path:
        return backend_uv_path(self.root)

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
        self.ensure_backend_pip()
        expected_uv_version = supported_uv_version(self.root)
        uv_requirement = f"uv=={expected_uv_version}"
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
        print(f"Ensuring supported backend dependency tool: {uv_requirement}")
        self.command_runner(
            [str(self.backend_python), "-m", "pip", "install", uv_requirement],
            self.backend_dir,
            None,
            False,
        )
        self.verify_backend_uv_version(expected_version=expected_uv_version)

    def verify_backend_uv_version(self, *, expected_version: str | None = None) -> None:
        expected = expected_version or supported_uv_version(self.root)
        try:
            result = self.command_runner(
                [str(self.backend_uv), "--version"],
                self.backend_dir,
                None,
                True,
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit(
                "Noofy could not verify the backend uv executable after source-checkout install.\n"
                f"Expected uv {expected} at: {self.backend_uv}\n"
                "Remove backend/.venv and rerun make install if this continues."
            ) from exc

        actual = parse_uv_version_output(result.stdout)
        if actual != expected:
            found = actual or "unknown"
            raise SystemExit(
                "Noofy source-checkout install left backend/.venv with an unsupported uv version.\n"
                f"Expected uv {expected}; found {found} at: {self.backend_uv}\n"
                "Rerun make install to repair the venv. Remove backend/.venv first if the mismatch continues."
            )

    def ensure_backend_pip(self) -> None:
        try:
            self.command_runner(
                [
                    str(self.backend_python),
                    "-c",
                    "import sys\ntry:\n import pip\nexcept Exception:\n sys.exit(1)",
                ],
                self.backend_dir,
                None,
                False,
            )
        except subprocess.CalledProcessError:
            print("Backend venv is missing pip; bootstrapping it with ensurepip")
            try:
                self.command_runner(
                    [str(self.backend_python), "-m", "ensurepip", "--upgrade"],
                    self.backend_dir,
                    None,
                    False,
                )
            except subprocess.CalledProcessError:
                raise SystemExit(
                    "Backend venv is missing pip, and Python could not bootstrap it with ensurepip.\n"
                    "Install Python with venv/ensurepip support, remove backend/.venv, "
                    "and run the source-checkout install command again."
                ) from None

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
        print(f"Runtime mode: {payload.get('mode', 'unknown')}")
        print(f"Runtime prepared: {environment.get('prepared')}")
        print(f"Sidecar reachable: {payload.get('reachable')}")
        if not environment:
            print("Runtime error: managed runtime environment status is unavailable")
            return 1
        error = environment.get("error")
        if error:
            print(f"Runtime error: {error}")
            return 1
        if environment.get("prepared") is not True:
            print("Runtime error: managed runtime environment is not prepared")
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
        processes: list[OwnedProcess] = []

        def start_backend() -> None:
            processes.append(
                own_process(
                    subprocess.Popen(
                        command,
                        cwd=self.backend_dir,
                        env=env,
                        **child_process_popen_kwargs(),
                    ),
                    "backend",
                    self.backend_dir,
                )
            )
            write_process_lease(data_dir, self.root, processes)

        return supervise_processes(
            processes,
            start=start_backend,
            prepare=lambda: recover_stale_processes(data_dir, self.root),
            lease_path=source_process_lease_path(data_dir),
            launcher_lock=source_launcher_lock(data_dir),
        )

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
            include_frontend_dev_proxy=True,
        )
        backend_api = f"http://{host}:{backend_port}/api"
        frontend_url = "http://127.0.0.1:5173"

        backend_command = [str(self.backend_python), "-m", "app", "--host", host, "--port", str(backend_port)]
        frontend_command = ["npm", "run", "dev"]

        processes: list[OwnedProcess] = []

        def start_processes() -> None:
            print(f"Starting Noofy backend: {backend_api}")
            processes.append(
                own_process(
                    subprocess.Popen(
                        backend_command,
                        cwd=self.backend_dir,
                        env=backend_env,
                        **child_process_popen_kwargs(),
                    ),
                    "backend",
                    self.backend_dir,
                )
            )
            write_process_lease(data_dir, self.root, processes)
            wait_for_backend_listener(processes[0].process, host, backend_port)
            print(f"Starting Noofy frontend: {frontend_url}")
            processes.append(
                own_process(
                    subprocess.Popen(
                        frontend_command,
                        cwd=self.frontend_dir,
                        env=frontend_env,
                        **child_process_popen_kwargs(),
                    ),
                    "frontend",
                    self.frontend_dir,
                )
            )
            write_process_lease(data_dir, self.root, processes)
            print()
            print(f"Open Noofy at {frontend_url}")
            print("Press Ctrl+C to stop Noofy.")

        return supervise_processes(
            processes,
            start=start_processes,
            prepare=lambda: recover_stale_processes(data_dir, self.root),
            lease_path=source_process_lease_path(data_dir),
            launcher_lock=source_launcher_lock(data_dir),
        )


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


def supervise_processes(
    processes: list[OwnedProcess],
    *,
    start: Callable[[], None] | None = None,
    prepare: Callable[[], None] | None = None,
    lease_path: Path | None = None,
    launcher_lock: contextlib.AbstractContextManager[None] | None = None,
) -> int:
    try:
        with termination_signal_handler():
            with launcher_lock or contextlib.nullcontext():
                if prepare is not None:
                    prepare()
                if start is not None:
                    start()
                return wait_for_processes(processes)
    except KeyboardInterrupt:
        print("\nStopping Noofy...")
        return 130
    except LauncherTermination as termination:
        print("\nStopping Noofy...")
        return 128 + termination.signal_number
    finally:
        terminate_processes(processes)
        if processes and lease_path is not None:
            lease_path.unlink(missing_ok=True)


def wait_for_processes(processes: list[OwnedProcess]) -> int:
    while True:
        for owned in processes:
            returncode = owned.process.poll()
            if returncode is not None:
                return int(returncode)
        time.sleep(0.5)


def wait_for_backend_listener(
    process: subprocess.Popen[bytes],
    host: str,
    port: int,
    *,
    timeout_seconds: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(f"Noofy backend exited before accepting connections (exit code {returncode}).")
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Noofy backend did not accept connections on {host}:{port} within {timeout_seconds:g}s.")


def source_process_lease_path(data_dir: Path) -> Path:
    return data_dir / "launcher" / SOURCE_PROCESS_LEASE_NAME


def source_process_lock_path(data_dir: Path) -> Path:
    return data_dir / "launcher" / SOURCE_PROCESS_LOCK_NAME


@contextlib.contextmanager
def source_launcher_lock(data_dir: Path):
    path = source_process_lock_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = acquire_source_launcher_lock(path)
    try:
        yield
    finally:
        release_source_launcher_lock(lock_file)


def acquire_source_launcher_lock(path: Path) -> BinaryIO:
    lock_file = path.open("a+b", buffering=0)
    try:
        _lock_file_nonblocking(lock_file)
    except OSError:
        owner = _read_json_file(path)
        lock_file.close()
        owner_pid = owner.get("pid")
        if isinstance(owner_pid, int):
            raise SystemExit(
                f"Noofy is already running for this checkout (launcher PID {owner_pid})."
            ) from None
        raise SystemExit(f"Noofy is already running for this checkout ({path}).") from None

    try:
        identity = require_process_identity(os.getpid(), "launcher")
    except RuntimeError as error:
        release_source_launcher_lock(lock_file)
        raise SystemExit(str(error)) from error
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(
        json.dumps(
            {
                "schema_version": 1,
                "pid": os.getpid(),
                "identity": identity,
            },
            sort_keys=True,
        ).encode("utf-8")
    )
    return lock_file


def _lock_file_nonblocking(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(b"\0")
            lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def release_source_launcher_lock(lock_file: BinaryIO) -> None:
    if lock_file.closed:
        return
    try:
        lock_file.seek(0)
        lock_file.truncate()
        if os.name == "nt":
            import msvcrt

            lock_file.write(b"\0")
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()


def _read_json_file(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def write_process_lease(data_dir: Path, checkout_root: Path, processes: list[OwnedProcess]) -> None:
    path = source_process_lease_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    launcher_identity = require_process_identity(os.getpid(), "launcher")
    children = [
        {
            "pid": owned.process.pid,
            "identity": require_process_identity(owned.process.pid, owned.role),
            "role": owned.role,
            "cwd": str(owned.cwd.resolve()),
        }
        for owned in processes
    ]
    payload = {
        "schema_version": 2,
        "checkout_root": str(checkout_root.resolve()),
        "launcher_pid": os.getpid(),
        "launcher_identity": launcher_identity,
        "children": children,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def recover_stale_processes(data_dir: Path, checkout_root: Path) -> None:
    path = source_process_lease_path(data_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except (json.JSONDecodeError, OSError):
        path.unlink(missing_ok=True)
        return

    if payload.get("checkout_root") != str(checkout_root.resolve()):
        raise SystemExit(
            f"Refusing to use process lease owned by another checkout: {path}"
        )
    launcher_pid = payload.get("launcher_pid")
    launcher_identity = payload.get("launcher_identity")
    if (
        isinstance(launcher_pid, int)
        and isinstance(launcher_identity, str)
        and process_identity(launcher_pid) == launcher_identity
    ):
        raise SystemExit(
            f"Noofy is already running for this checkout (launcher PID {launcher_pid})."
        )

    unresolved: list[int] = []
    for child in payload.get("children", []):
        if not isinstance(child, dict):
            continue
        pid = child.get("pid")
        identity = child.get("identity")
        role = child.get("role")
        cwd = child.get("cwd")
        if not isinstance(pid, int) or not owned_process_tree_exists(pid):
            continue
        if not isinstance(identity, str) or process_identity(pid) != identity:
            unresolved.append(pid)
            continue
        if (
            isinstance(role, str)
            and isinstance(cwd, str)
            and stale_process_matches(pid, role, Path(cwd), checkout_root)
        ):
            print(f"Stopping stale Noofy {role} process group from this checkout (PID {pid}).")
            signal_stale_process_group(pid)
        else:
            unresolved.append(pid)
    if unresolved:
        raise SystemExit(
            "Recorded Noofy child processes are still running but could not be safely "
            f"identified: {', '.join(str(pid) for pid in unresolved)}"
        )
    path.unlink(missing_ok=True)


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_identity(pid: int) -> str | None:
    if os.name == "nt":
        return windows_process_identity(pid)
    if sys.platform == "darwin":
        identity = macos_process_identity(pid)
        if identity is not None:
            return identity
    proc_stat = Path("/proc") / str(pid) / "stat"
    with contextlib.suppress(OSError):
        start_time = linux_proc_stat_start_time(proc_stat.read_text(encoding="utf-8"))
        if start_time is not None:
            return f"proc-start:{start_time}"
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart="],
        capture_output=True,
        text=True,
    )
    started = result.stdout.strip()
    return f"ps-start:{started}" if started else None


def require_process_identity(pid: int, role: str) -> str:
    identity = process_identity(pid)
    if identity is None:
        raise RuntimeError(
            f"Could not establish stable Noofy {role} process identity (PID {pid})."
        )
    return identity


def linux_proc_stat_start_time(stat: str) -> str | None:
    # The second field is parenthesized and may contain spaces or parentheses.
    command_end = stat.rfind(")")
    if command_end < 0:
        return None
    fields_after_command = stat[command_end + 1 :].split()
    return fields_after_command[19] if len(fields_after_command) > 19 else None


def macos_process_identity(pid: int) -> str | None:
    if sys.platform != "darwin":
        return None
    import ctypes

    class ProcBsdInfo(ctypes.Structure):
        _fields_ = [
            ("pbi_flags", ctypes.c_uint32),
            ("pbi_status", ctypes.c_uint32),
            ("pbi_xstatus", ctypes.c_uint32),
            ("pbi_pid", ctypes.c_uint32),
            ("pbi_ppid", ctypes.c_uint32),
            ("pbi_uid", ctypes.c_uint32),
            ("pbi_gid", ctypes.c_uint32),
            ("pbi_ruid", ctypes.c_uint32),
            ("pbi_rgid", ctypes.c_uint32),
            ("pbi_svuid", ctypes.c_uint32),
            ("pbi_svgid", ctypes.c_uint32),
            ("rfu_1", ctypes.c_uint32),
            ("pbi_comm", ctypes.c_char * 16),
            ("pbi_name", ctypes.c_char * 32),
            ("pbi_nfiles", ctypes.c_uint32),
            ("pbi_pgid", ctypes.c_uint32),
            ("pbi_pjobc", ctypes.c_uint32),
            ("e_tdev", ctypes.c_uint32),
            ("e_tpgid", ctypes.c_uint32),
            ("pbi_nice", ctypes.c_int32),
            ("pbi_start_tvsec", ctypes.c_uint64),
            ("pbi_start_tvusec", ctypes.c_uint64),
        ]

    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    info = ProcBsdInfo()
    size = libproc.proc_pidinfo(
        pid,
        3,
        0,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if size != ctypes.sizeof(info):
        return None
    return f"macos-start:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"


def windows_process_identity(pid: int) -> str | None:
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    try:
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return f"windows-created:{ticks}"
    finally:
        kernel32.CloseHandle(handle)


def owned_process_tree_exists(pid: int) -> bool:
    if os.name == "nt":
        return process_exists(pid)
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def stale_process_matches(pid: int, role: str, cwd: Path, checkout_root: Path) -> bool:
    if os.name == "nt" or role not in {"backend", "frontend"}:
        return False
    expected_cwd = (checkout_root / role).resolve()
    if cwd.resolve() != expected_cwd or not owned_process_tree_exists(pid):
        return False
    expected_commands = ("-m app",) if role == "backend" else ("npm run dev", "vite --host")
    return any(
        process_cwd(member_pid) == expected_cwd
        and any(expected in command for expected in expected_commands)
        for member_pid, command in process_group_members(pid)
    )


def process_group_members(process_group_id: int) -> list[tuple[int, str]]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,pgid=,command="],
        capture_output=True,
        text=True,
    )
    members = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=2)
        if len(fields) != 3:
            continue
        with contextlib.suppress(ValueError):
            pid, pgid = int(fields[0]), int(fields[1])
            if pgid == process_group_id:
                members.append((pid, fields[2]))
    return members


def process_cwd(pid: int) -> Path | None:
    proc_cwd = Path("/proc") / str(pid) / "cwd"
    with contextlib.suppress(OSError):
        return proc_cwd.resolve(strict=True)
    result = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            return Path(line[1:]).resolve()
    return None


def signal_stale_process_group(pid: int) -> None:
    with suppress_process_errors():
        os.killpg(pid, signal.SIGTERM)
    deadline = time.monotonic() + 8
    while owned_process_tree_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if owned_process_tree_exists(pid):
        with suppress_process_errors():
            os.killpg(pid, signal.SIGKILL)


@contextlib.contextmanager
def termination_signal_handler():
    handled_signals = [signal.SIGTERM]
    if hasattr(signal, "SIGHUP"):
        handled_signals.append(signal.SIGHUP)
    previous_handlers = {
        signal_number: signal.getsignal(signal_number)
        for signal_number in handled_signals
    }

    def request_shutdown(signal_number, frame):
        raise LauncherTermination(signal_number)

    for signal_number in handled_signals:
        signal.signal(signal_number, request_shutdown)
    try:
        yield
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)


def child_process_popen_kwargs(*, os_name: str | None = None) -> dict[str, object]:
    selected_os = os.name if os_name is None else os_name
    if selected_os == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return {"creationflags": creationflags} if creationflags else {}
    return {"start_new_session": True}


def own_process(
    process: subprocess.Popen[bytes],
    role: str,
    cwd: Path,
) -> OwnedProcess:
    windows_job_handle = None
    if os.name == "nt":
        try:
            windows_job_handle = create_windows_kill_on_close_job(process)
        except BaseException:
            terminate_windows_process_tree(process.pid, force=True)
            with suppress_process_errors():
                process.wait(timeout=5)
            raise
    return OwnedProcess(process, role, cwd, windows_job_handle)


def terminate_processes(
    processes: list[OwnedProcess],
) -> None:
    with suppress_cleanup_interrupts() as interrupted:
        for owned in processes:
            signal_process_tree(owned.process, signal.SIGTERM)

        wait_for_process_shutdown(
            processes,
            deadline=time.monotonic() + 8,
            interrupted=interrupted,
        )
        for owned in processes:
            close_windows_job_handle(owned.windows_job_handle)

        for owned in processes:
            kill_process_tree(owned.process)

        wait_for_process_shutdown(
            processes,
            deadline=time.monotonic() + 2,
            interrupted=interrupted,
        )


def wait_for_process_shutdown(
    processes: list[OwnedProcess],
    *,
    deadline: float,
    interrupted: Callable[[], bool],
) -> None:
    for owned in processes:
        process = owned.process
        while process.poll() is None and time.monotonic() < deadline and not interrupted():
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass


def signal_process_tree(process: subprocess.Popen[bytes], signal_number: int) -> None:
    with suppress_process_errors():
        if os.name == "nt":
            terminate_windows_process_tree(process.pid)
        else:
            os.killpg(process.pid, signal_number)


def kill_process_tree(process: subprocess.Popen[bytes]) -> None:
    with suppress_process_errors():
        if os.name == "nt":
            terminate_windows_process_tree(process.pid, force=True)
        else:
            os.killpg(process.pid, signal.SIGKILL)


def terminate_windows_process_tree(pid: int, *, force: bool = False) -> None:
    command = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        command.append("/F")
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def create_windows_kill_on_close_job(process: subprocess.Popen[bytes]) -> int:
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", BasicLimitInformation),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    info = ExtendedLimitInformation()
    info.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
        kernel32.CloseHandle(job)
        raise ctypes.WinError(ctypes.get_last_error())
    process_handle = wintypes.HANDLE(getattr(process, "_handle"))
    if not kernel32.AssignProcessToJobObject(job, process_handle):
        kernel32.CloseHandle(job)
        raise ctypes.WinError(ctypes.get_last_error())
    return int(job)


def close_windows_job_handle(handle: int | None) -> None:
    if os.name != "nt" or handle is None:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle(wintypes.HANDLE(handle))


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
