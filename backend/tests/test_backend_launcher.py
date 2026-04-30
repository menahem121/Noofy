import os
import queue
import subprocess
import sys
import threading
import time

import httpx

from app.__main__ import runtime_config_line


def test_runtime_config_line_advertises_api_base_url() -> None:
    assert runtime_config_line("127.0.0.1", 9123) == "NOOFY_BACKEND_API_BASE_URL=http://127.0.0.1:9123/api"


def test_backend_module_starts_on_free_port_and_serves_paths() -> None:
    env = os.environ.copy()
    env.pop("NOOFY_API_TOKEN", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "app", "--port", "0", "--log-level", "warning"],
        cwd=os.getcwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    lines: queue.Queue[str] = queue.Queue()

    def read_stdout() -> None:
        for line in process.stdout:
            lines.put(line)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()

    try:
        first_line = lines.get(timeout=10).strip()
        prefix = "NOOFY_BACKEND_API_BASE_URL="
        assert first_line.startswith(prefix)
        api_base_url = first_line.removeprefix(prefix)

        deadline = time.monotonic() + 10
        response = None
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{api_base_url}/paths", timeout=1)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)

        assert response is not None
        assert response.status_code == 200
        assert "data_dir" in response.json()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
