from pathlib import Path

from app.diagnostics import DiagnosticsSink
from app.runtime.manager import RuntimeManager


class ComfyUIProcessManager(RuntimeManager):
    def __init__(
        self,
        base_url: str,
        repo_dir: Path,
        python_executable: str,
        host: str,
        port: int,
        log_store: DiagnosticsSink,
    ) -> None:
        super().__init__(
            mode="managed",
            external_base_url=base_url,
            repo_dir=repo_dir,
            python_executable=python_executable,
            managed_host=host,
            managed_port=port,
            log_store=log_store,
        )
