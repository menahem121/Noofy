import os
from pathlib import Path

import pytest


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
