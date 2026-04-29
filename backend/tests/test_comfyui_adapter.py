from pathlib import Path

import httpx
import pytest

from app.engine.diagnostics import LogStore
from app.engine.comfyui_adapter import ComfyUIEngineAdapter


def test_result_from_history_adds_view_urls(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path)

    result = adapter._result_from_history(
        "job-1",
        {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "9": {
                    "images": [
                        {
                            "filename": "sample image.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            },
        },
    )

    assert result.status == "completed"
    assert result.outputs[0]["node_id"] == "9"
    assert "sample+image.png" in result.outputs[0]["output"]["images"][0]["view_url"]


def test_terminal_progress_logs_once(tmp_path: Path) -> None:
    log_store = LogStore()
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path, log_store=log_store)
    progress = adapter._progress_from_history("job-1", {"status": {"completed": True}})

    adapter._log_terminal_progress_once(progress)
    adapter._log_terminal_progress_once(progress)

    events = log_store.list_events(job_id="job-1").events
    assert len(events) == 1
    assert events[0].message == "ComfyUI execution completed"


def test_progress_from_failed_history(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path)

    progress = adapter._progress_from_history(
        "job-1",
        {"status": {"completed": False, "status_str": "error"}},
    )

    assert progress.status == "failed"


def test_progress_from_comfyui_progress_ws_message(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path)

    progress = adapter._progress_from_ws_message(
        "job-1",
        {
            "type": "progress",
            "data": {
                "prompt_id": "job-1",
                "node": "3",
                "value": 7,
                "max": 20,
            },
        },
    )

    assert progress is not None
    assert progress.status == "running"
    assert progress.current_node == "3"
    assert progress.value == 7
    assert progress.max == 20


def test_progress_from_comfyui_error_ws_message(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path)

    progress = adapter._progress_from_ws_message(
        "job-1",
        {
            "type": "execution_error",
            "data": {
                "prompt_id": "job-1",
                "node_id": "3",
                "exception_message": "model failed",
            },
        },
    )

    assert progress is not None
    assert progress.status == "failed"
    assert progress.current_node == "3"
    assert progress.message == "model failed"


def test_handle_ws_error_message_logs_failure(tmp_path: Path) -> None:
    log_store = LogStore()
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path, log_store=log_store)

    should_stop = adapter._handle_ws_message(
        "job-1",
        """
        {
          "type": "execution_error",
          "data": {
            "prompt_id": "job-1",
            "node_id": "3",
            "exception_message": "model failed"
          }
        }
        """,
    )

    assert should_stop
    assert log_store.latest_error() is not None
    assert log_store.latest_error().message == "ComfyUI execution failed"


def test_result_from_comfyui_executed_ws_message(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path)

    result = adapter._result_from_ws_message(
        "job-1",
        {
            "type": "executed",
            "data": {
                "prompt_id": "job-1",
                "node": "9",
                "output": {
                    "images": [
                        {
                            "filename": "sample.png",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                },
            },
        },
    )

    assert result is not None
    assert result.status == "running"
    assert result.outputs[0]["node_id"] == "9"
    assert result.outputs[0]["output"]["images"][0]["view_url"].endswith(
        "/view?filename=sample.png&subfolder=&type=output"
    )


def test_ws_url_for_client_uses_configured_ws_url(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", tmp_path, "ws://remote.test:9000/ws")

    assert adapter._ws_url_for_client("abc 123") == "ws://remote.test:9000/ws?clientId=abc+123"


@pytest.mark.anyio
async def test_list_available_models_uses_comfyui_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json=["checkpoints"])
        if request.url.path == "/models/checkpoints":
            return httpx.Response(200, json=["remote-model.safetensors"])
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    adapter = ComfyUIEngineAdapter("http://comfyui.test", tmp_path)
    models = await adapter.list_available_models()

    assert [(model.folder, model.filename) for model in models] == [("checkpoints", "remote-model.safetensors")]
