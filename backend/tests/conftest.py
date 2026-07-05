import os
from pathlib import Path
import sys
from dataclasses import fields
from typing import Any

import pytest

from app.composition import ApiServices


_API_SERVICE_PLACEHOLDER = object()


def make_api_services(**overrides: Any) -> ApiServices:
    defaults = {
        field.name: None
        if field.name
        in {
            "workflow_library_service",
            "dashboard_authoring_service",
            "workflow_exporter",
            "workflow_import_orchestrator",
            "workflow_runner_lifecycle_service",
            "run_job_service",
            "run_orchestrator",
            "run_result_service",
            "history_service",
            "civitai_lora_service",
            "fp8_conversion_service",
        }
        else _API_SERVICE_PLACEHOLDER
        for field in fields(ApiServices)
    }
    defaults.update(overrides)
    return ApiServices(**defaults)


def pytest_configure(config: pytest.Config) -> None:
    if sys.platform != "win32" or config.option.basetemp:
        return

    # Keep Windows path-sensitive runtime tests below Noofy's product MAX_PATH
    # guard without disabling that guard. The default pytest temp root is long.
    root = Path(os.environ.get("NOOFY_PYTEST_BASETEMP", r"C:\nt"))
    config.option.basetemp = str(root / str(os.getpid()))


@pytest.fixture(scope="session", autouse=True)
def _backend_working_dir():
    """Tests use Path("app/...") relative paths that require CWD == backend/."""
    backend_dir = Path(__file__).parent.parent
    old_cwd = os.getcwd()
    os.chdir(backend_dir)
    yield
    os.chdir(old_cwd)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
