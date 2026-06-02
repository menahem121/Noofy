import os
import queue
import subprocess
import sys
import threading
import time

import httpx

from app.__main__ import runtime_config_line, uvicorn_log_config
from app.core.logging import SuppressResourceMonitorAccessLogFilter


def test_runtime_config_line_advertises_api_base_url() -> None:
    assert runtime_config_line("127.0.0.1", 9123) == "NOOFY_BACKEND_API_BASE_URL=http://127.0.0.1:9123/api"


def test_backend_log_config_filters_resource_monitor_access_logs() -> None:
    log_config = uvicorn_log_config()

    assert log_config["handlers"]["access"]["filters"] == ["noofy_resource_monitor"]


def test_resource_monitor_access_log_filter_suppresses_high_frequency_polling() -> None:
    access_filter = SuppressResourceMonitorAccessLogFilter()
    resources_record = logging_record(("127.0.0.1:1234", "GET", "/api/resources", "1.1", 200))
    verification_record = logging_record((
        "127.0.0.1:1234",
        "GET",
        "/api/workflows/import/import-abc/model-verification",
        "1.1",
        200,
    ))
    runtime_record = logging_record(("127.0.0.1:1234", "GET", "/api/runtime", "1.1", 200))
    runtime_query_record = logging_record(("127.0.0.1:1234", "GET", "/api/runtime?detail=1", "1.1", 200))
    runtime_line_record = logging_record((
        "127.0.0.1:1234",
        "GET /api/runtime HTTP/1.1",
    ))
    import_preview_record = logging_record((
        "127.0.0.1:1234",
        "POST",
        "/api/workflows/import/preview",
        "1.1",
        200,
    ))

    assert access_filter.filter(resources_record) is False
    assert access_filter.filter(verification_record) is False
    assert access_filter.filter(runtime_record) is False
    assert access_filter.filter(runtime_query_record) is False
    assert access_filter.filter(runtime_line_record) is False
    assert access_filter.filter(import_preview_record) is True


def test_access_log_filter_drops_query_token_requests() -> None:
    access_filter = SuppressResourceMonitorAccessLogFilter()
    event_record = logging_record((
        "127.0.0.1:1234",
        "GET",
        "/api/jobs/job-1/events?token=runtime-secret",
        "1.1",
        200,
    ))
    output_record = logging_record((
        "127.0.0.1:1234",
        "GET /api/jobs/job-1/outputs/view?filename=result.png&token=runtime-secret HTTP/1.1",
    ))

    assert access_filter.filter(event_record) is False
    assert access_filter.filter(output_record) is False


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


def logging_record(args: tuple[object, ...]):
    import logging

    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %s',
        args=args,
        exc_info=None,
    )
