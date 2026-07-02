import os
from pathlib import Path
import sys

import pytest


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
