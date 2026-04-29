import subprocess
from pathlib import Path

import httpx

from app.engine.models import ComfyUIRuntimeStatus, ProcessActionResult


class ComfyUIProcessManager:
    def __init__(
        self,
        base_url: str,
        repo_dir: Path,
        python_executable: str,
        host: str,
        port: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.repo_dir = repo_dir
        self.python_executable = python_executable
        self.host = host
        self.port = port
        self._process: subprocess.Popen[bytes] | None = None

    async def status(self) -> ComfyUIRuntimeStatus:
        reachable, error = await self._is_reachable()
        return ComfyUIRuntimeStatus(
            reachable=reachable,
            base_url=self.base_url,
            repo_dir=str(self.repo_dir),
            managed_process_running=self._is_managed_process_running(),
            pid=self._process.pid if self._is_managed_process_running() else None,
            error=error,
        )

    async def start(self) -> ProcessActionResult:
        current = await self.status()
        if current.reachable:
            return ProcessActionResult(status="already_running", comfyui=current)

        if not self.repo_dir.exists():
            return ProcessActionResult(
                status="repo_missing",
                comfyui=current.model_copy(update={"error": f"ComfyUI repo not found: {self.repo_dir}"}),
            )

        if not (self.repo_dir / "main.py").exists():
            return ProcessActionResult(
                status="entrypoint_missing",
                comfyui=current.model_copy(update={"error": f"ComfyUI main.py not found in: {self.repo_dir}"}),
            )

        if not self._is_managed_process_running():
            self._process = subprocess.Popen(
                [
                    self.python_executable,
                    "main.py",
                    "--listen",
                    self.host,
                    "--port",
                    str(self.port),
                ],
                cwd=self.repo_dir,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

        return ProcessActionResult(status="start_requested", comfyui=await self.status())

    async def stop(self) -> ProcessActionResult:
        if not self._is_managed_process_running():
            return ProcessActionResult(status="not_managed", comfyui=await self.status())

        assert self._process is not None
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=10)

        self._process = None
        return ProcessActionResult(status="stopped", comfyui=await self.status())

    async def _is_reachable(self) -> tuple[bool, str | None]:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(f"{self.base_url}/system_stats")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            return False, str(exc)
        return True, None

    def _is_managed_process_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
