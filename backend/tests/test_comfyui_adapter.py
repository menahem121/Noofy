from pathlib import Path

import httpx
import pytest

from app.engine.diagnostics import LogStore
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.engine.models import JobProgress


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


def test_stage_assets_copies_file_and_rewrites_graph(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    # Create a fake asset file with a valid UUID-based name.
    asset_id = "12345678-1234-1234-1234-123456789abc.png"
    (assets_dir / asset_id).write_bytes(b"fake-image-data")

    models_dir = tmp_path / "models"
    models_dir.mkdir()

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        models_dir,
        dashboard_assets_dir=assets_dir,
    )

    graph = {
        "10": {
            "class_type": "LoadImage",
            "inputs": {"image": asset_id, "upload": "image"},
        }
    }

    new_graph, staged = adapter._stage_assets(graph, "job-99")

    assert len(staged) == 1
    staged_path = staged[0]
    assert staged_path.exists()
    assert staged_path.parent == tmp_path / "input" / "staging"
    assert staged_path.read_bytes() == b"fake-image-data"
    # Graph node value must reference the staging subfolder.
    assert new_graph["10"]["inputs"]["image"].startswith("staging/")
    assert asset_id in new_graph["10"]["inputs"]["image"]
    # Original graph must be unchanged (deep copy).
    assert graph["10"]["inputs"]["image"] == asset_id


def test_stage_assets_uses_explicit_comfyui_input_dir(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_id = "12345678-1234-1234-1234-123456789abc.png"
    (assets_dir / asset_id).write_bytes(b"fake-image-data")

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "external-models",
        dashboard_assets_dir=assets_dir,
        comfyui_input_dir=tmp_path / "noofy-input",
    )

    _new_graph, staged = adapter._stage_assets(
        {"10": {"class_type": "LoadImage", "inputs": {"image": asset_id}}},
        "job-99",
    )

    assert staged[0].parent == tmp_path / "noofy-input" / "staging"


def test_stage_assets_skips_missing_asset(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        models_dir,
        dashboard_assets_dir=assets_dir,
    )
    graph = {"10": {"class_type": "LoadImage", "inputs": {"image": "missing-00000000-0000-0000-0000-000000000000.png"}}}
    new_graph, staged = adapter._stage_assets(graph, "job-1")
    assert staged == []
    assert new_graph["10"]["inputs"]["image"] == "missing-00000000-0000-0000-0000-000000000000.png"


def test_stage_assets_ignores_non_asset_values(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        models_dir,
        dashboard_assets_dir=assets_dir,
    )
    graph = {"5": {"class_type": "KSampler", "inputs": {"seed": 42, "model": "v1-5.safetensors"}}}
    new_graph, staged = adapter._stage_assets(graph, "job-1")
    assert staged == []
    assert new_graph["5"]["inputs"]["seed"] == 42


def test_cleanup_staged_files_removes_files(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", models_dir)

    staged_file = tmp_path / "staged.png"
    staged_file.write_bytes(b"data")
    adapter._staged_files["job-99"] = [staged_file]

    adapter._cleanup_staged_files("job-99")

    assert not staged_file.exists()
    assert "job-99" not in adapter._staged_files


@pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
def test_terminal_progress_cleans_staged_files_for_terminal_statuses(tmp_path: Path, status: str) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    adapter = ComfyUIEngineAdapter("http://127.0.0.1:8188", models_dir)

    staged_file = tmp_path / f"{status}.png"
    staged_file.write_bytes(b"data")
    adapter._staged_files["job-terminal"] = [staged_file]

    adapter._log_terminal_progress_once(JobProgress(job_id="job-terminal", status=status))

    assert not staged_file.exists()
    assert "job-terminal" not in adapter._staged_files


@pytest.mark.anyio
async def test_cancel_job_cleans_staged_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/interrupt"
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    adapter = ComfyUIEngineAdapter("http://comfyui.test", models_dir)
    staged_file = tmp_path / "canceled.png"
    staged_file.write_bytes(b"data")
    adapter._staged_files["job-cancel"] = [staged_file]

    await adapter.cancel_job("job-cancel")

    assert not staged_file.exists()
    assert "job-cancel" not in adapter._staged_files


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
