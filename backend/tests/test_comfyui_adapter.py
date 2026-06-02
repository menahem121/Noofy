import json
from pathlib import Path

import httpx
import pytest

import app.engine.comfyui_adapter as comfyui_adapter_module
from app.diagnostics import LogStore
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.engine.models import JobProgress, JobResult
from app.engine.service import EngineService
from app.runs.credentials import (
    CredentialRequirementError,
    build_credential_injection_plan,
    options_with_credential_plan,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage


def _media_package(control: str, node_id: str = "10", input_name: str = "image") -> WorkflowPackage:
    return WorkflowPackage.model_validate(
        {
            "metadata": {"id": "media_wf", "name": "Media Workflow", "version": "1.0.0"},
            "engine": "comfyui",
            "required_models": [],
            "custom_nodes": [],
            "comfyui_graph": {node_id: {"class_type": "MediaNode", "inputs": {input_name: ""}}},
            "inputs": [
                {
                    "id": "media",
                    "label": "Media",
                    "control": control,
                    "binding": {"node_id": node_id, "input_name": input_name},
                    "default": None,
                }
            ],
        }
    )


def _api_credential_package() -> WorkflowPackage:
    return WorkflowPackage.model_validate(
        {
            "metadata": {"id": "api_wf", "name": "API Workflow", "version": "1.0.0"},
            "engine": "comfyui",
            "required_models": [],
            "custom_nodes": [],
            "comfyui_graph": {
                "1": {
                    "class_type": "ComfyAPINode",
                    "inputs": {"prompt": "hello"},
                }
            },
            "dashboard": {
                "version": "0.1.0",
                "status": "configured",
                "sections": [
                    {
                        "id": "main",
                        "title": "Controls",
                        "controls": [
                            {
                                "id": "comfy_account_key",
                                "type": "api_credential",
                                "label": "ComfyUI Account API Key",
                                "provider": "comfy_org",
                                "required": True,
                                "secret_ref": "api-key:comfy_org",
                                "injection_strategy": {
                                    "kind": "comfyui_extra_data",
                                    "field": "api_key_comfy_org",
                                },
                            }
                        ],
                    }
                ],
            },
        }
    )


def test_result_from_history_adds_view_urls(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

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
                },
                "12": {
                    "audio": [
                        {
                            "filename": "speech.wav",
                            "subfolder": "",
                            "type": "output",
                            "size": 1024,
                        }
                    ]
                }
            },
        },
    )

    assert result.status == "completed"
    assert result.outputs[0]["node_id"] == "9"
    view_url = result.outputs[0]["output"]["images"][0]["view_url"]
    assert view_url.startswith("/api/jobs/job-1/outputs/view?")
    assert "sample+image.png" in view_url
    audio = result.outputs[1]["output"]["audio"][0]
    assert audio["kind"] == "audio"
    assert audio["type"] == "audio"
    assert audio["output_type"] == "output"
    assert audio["mime_type"] == "audio/x-wav"
    assert audio["url"].startswith("/api/jobs/job-1/outputs/view?")


def test_result_from_history_recognizes_video_inside_images_bucket(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

    result = adapter._result_from_history(
        "job-video",
        {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "15": {
                    "images": [
                        {"filename": "generated.webm", "subfolder": "", "type": "output"}
                    ]
                }
            },
        },
    )

    video = result.outputs[0]["output"]["images"][0]
    assert video["kind"] == "video"
    assert video["type"] == "video"
    assert video["output_type"] == "output"
    assert video["mime_type"] == "video/webm"
    assert video["url"].startswith("/api/jobs/job-video/outputs/view?")


def test_result_from_history_normalizes_generic_file_output(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

    result = adapter._result_from_history(
        "job-file",
        {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "20": {
                    "files": [
                        {"filename": "summary.json", "subfolder": "", "type": "output", "size": 18}
                    ]
                }
            },
        },
    )

    file_output = result.outputs[0]["output"]["files"][0]
    assert file_output["kind"] == "file"
    assert file_output["type"] == "file"
    assert file_output["extension"] == ".json"
    assert file_output["mime_type"] == "application/json"
    assert file_output["url"].startswith("/api/jobs/job-file/outputs/view?")


def test_result_from_history_recognizes_native_three_d_bucket(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

    result = adapter._result_from_history(
        "job-three-d",
        {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "30": {
                    "3d": [
                        {"filename": "mesh.glb", "subfolder": "", "type": "output"}
                    ]
                }
            },
        },
    )

    model = result.outputs[0]["output"]["3d"][0]
    assert model["kind"] == "3d"
    assert model["type"] == "3d"
    assert model["extension"] == ".glb"
    assert model["size"] is None
    assert model["url"].startswith("/api/jobs/job-three-d/outputs/view?")


@pytest.mark.anyio
async def test_probe_output_metadata_uses_range_response_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/view"
        assert request.headers["range"] == "bytes=0-0"
        return httpx.Response(
            206,
            headers={"content-range": "bytes 0-0/4096", "content-type": "model/gltf-binary"},
            content=b"x",
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    adapter = ComfyUIEngineAdapter("http://comfyui.test", tmp_path, log_store=LogStore())

    assert await adapter._probe_output_metadata(
        {"filename": "mesh.glb", "subfolder": "", "type": "output"}
    ) == (4096, "model/gltf-binary")


def test_result_from_history_prefers_declared_output_kind(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )
    adapter._output_kinds_by_job["job-video"] = {"15": "video"}

    result = adapter._result_from_history(
        "job-video",
        {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"15": {"images": [{"filename": "result.bin", "type": "output"}]}},
        },
    )

    assert result.outputs[0]["output"]["images"][0]["kind"] == "video"


def test_result_from_history_accepts_compatibility_media_hints(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

    result = adapter._result_from_history(
        "job-video",
        {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "15": {
                    "images": [
                        {
                            "filename": "result.bin",
                            "subfolder": "",
                            "type": "output",
                            "content_type": "video/mp4",
                        }
                    ]
                }
            },
        },
    )

    video = result.outputs[0]["output"]["images"][0]
    assert video["kind"] == "video"
    assert video["mime_type"] == "video/mp4"


def test_result_from_history_keeps_media_type_separate_from_output_type(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

    result = adapter._result_from_history(
        "job-video",
        {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "15": {
                    "images": [
                        {
                            "filename": "result.mp4",
                            "subfolder": "",
                            "type": "video",
                            "mime_type": "video/mp4",
                        }
                    ]
                }
            },
        },
    )

    video = result.outputs[0]["output"]["images"][0]
    assert video["kind"] == "video"
    assert video["type"] == "video"
    assert video["output_type"] == "output"
    assert "type=output" in video["url"]


@pytest.mark.anyio
async def test_upload_workflow_image_posts_to_configured_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/upload/image"
        return httpx.Response(200, json={"name": "stored.png"})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    adapter = ComfyUIEngineAdapter(
        "http://comfyui.test", tmp_path, log_store=LogStore()
    )
    package = WorkflowPackageLoader(Path("app/workflows/packages")).get_package(
        "text_to_image_v0"
    )

    result = await adapter.upload_workflow_image(
        package,
        "input.png",
        b"image-bytes",
        "image/png",
    )

    assert result == {"filename": "stored.png"}
    assert len(requests) == 1


def test_credential_plan_is_built_from_saved_dashboard_schema() -> None:
    package = _api_credential_package()
    submitted_inputs = {
        "comfy_account_key": {
            "kind": "api_key_ref",
            "provider": "comfy_org",
            "secret_ref": "api-key:evil",
        }
    }

    with pytest.raises(CredentialRequirementError, match="does not match"):
        build_credential_injection_plan(
            package=package,
            submitted_inputs=submitted_inputs,
            credential_resolver=lambda provider: "secret-from-store",
        )


def test_missing_api_credential_returns_clean_error() -> None:
    package = _api_credential_package()

    with pytest.raises(CredentialRequirementError) as exc:
        build_credential_injection_plan(
            package=package,
            submitted_inputs={},
            credential_resolver=lambda provider: None,
        )

    assert "ComfyUI Account API Key is required" in str(exc.value)
    assert "api_key_comfy_org" not in str(exc.value)


def test_raw_api_credential_input_is_rejected_before_snapshot() -> None:
    package = _api_credential_package()

    with pytest.raises(CredentialRequirementError) as exc:
        build_credential_injection_plan(
            package=package,
            submitted_inputs={"comfy_account_key": "raw-secret-should-not-snapshot"},
            credential_resolver=lambda provider: "secret-from-store",
        )

    assert "ComfyUI Account API Key must be saved before running this workflow" in str(exc.value)
    assert "raw-secret-should-not-snapshot" not in str(exc.value)


@pytest.mark.anyio
async def test_api_nodes_disabled_returns_clean_non_secret_error() -> None:
    service = EngineService.__new__(EngineService)
    service.runtime_manager = type(
        "RuntimeManager",
        (),
        {"api_nodes_disabled": True, "managed_extra_args": []},
    )()

    reason = await service._api_nodes_unavailable_reason(
        _api_credential_package(),
        object(),
    )

    assert reason == "ComfyUI API nodes are disabled for the active runtime."


@pytest.mark.anyio
async def test_missing_partner_api_node_support_returns_clean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EngineService.__new__(EngineService)
    service.runtime_manager = type(
        "RuntimeManager",
        (),
        {"api_nodes_disabled": False, "managed_extra_args": []},
    )()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/object_info"
        return httpx.Response(200, json={"KSampler": {}})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    adapter = type("Adapter", (), {"base_url": "http://comfyui.test"})()

    reason = await service._api_nodes_unavailable_reason(
        _api_credential_package(),
        adapter,
    )

    assert reason is not None
    assert "Partner/API node support is unavailable" in reason
    assert "ComfyAPINode" in reason


@pytest.mark.anyio
async def test_run_workflow_posts_comfyui_extra_data_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/prompt"
        return httpx.Response(200, json={"prompt_id": "job"})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    package = _api_credential_package()
    plan = build_credential_injection_plan(
        package=package,
        submitted_inputs={
            "comfy_account_key": {
                "kind": "api_key_ref",
                "provider": "comfy_org",
                "secret_ref": "api-key:comfy_org",
            }
        },
        credential_resolver=lambda provider: "resolved-comfy-secret-1234",
    )
    adapter = ComfyUIEngineAdapter("http://comfyui.test", tmp_path, log_store=LogStore())

    await adapter.run_workflow(
        package,
        package.comfyui_graph,
        {},
        options_with_credential_plan(
            {"listen_for_events": False, "client_id": "client-1"},
            plan,
        ),
    )

    payload = json_payload(requests[0])
    assert payload["extra_data"]["api_key_comfy_org"] == "resolved-comfy-secret-1234"
    assert payload["prompt"]["1"]["inputs"] == {"prompt": "hello"}


@pytest.mark.anyio
async def test_adapter_redacts_resolved_api_key_from_http_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "resolved-comfy-secret-error"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            text=f"bad request contains {secret}",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    package = _api_credential_package()
    plan = build_credential_injection_plan(
        package=package,
        submitted_inputs={},
        credential_resolver=lambda provider: secret,
    )
    log_store = LogStore()
    adapter = ComfyUIEngineAdapter("http://comfyui.test", tmp_path, log_store=log_store)

    with pytest.raises(ValueError) as exc:
        await adapter.run_workflow(
            package,
            package.comfyui_graph,
            {},
            options_with_credential_plan({"listen_for_events": False}, plan),
        )

    assert secret not in str(exc.value)
    assert secret not in str(log_store.list_events().model_dump(mode="json"))
    assert secret not in str(adapter.job_store._progress)
    assert secret not in str(adapter.job_store._results)


def json_payload(request: httpx.Request) -> dict:
    request.read()
    return json.loads(request.content.decode("utf-8"))


@pytest.mark.anyio
async def test_release_memory_posts_comfyui_free_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    adapter = ComfyUIEngineAdapter("http://comfyui.test", tmp_path, log_store=LogStore())

    await adapter.release_memory()

    assert len(requests) == 1
    assert requests[0].url.path == "/free"
    assert json_payload(requests[0]) == {
        "unload_models": True,
        "free_memory": True,
    }


@pytest.mark.anyio
async def test_fetch_output_reads_from_configured_view_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == "/view"
        assert request.url.params["filename"] == "result.png"
        assert request.url.params["subfolder"] == "preview"
        assert request.url.params["type"] == "output"
        return httpx.Response(
            200,
            content=b"image-bytes",
            headers={"content-type": "image/png"},
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    adapter = ComfyUIEngineAdapter(
        "http://comfyui.test", tmp_path, log_store=LogStore()
    )
    adapter.job_store.set_result(
        JobResult(
            job_id="job-1",
            status="completed",
            outputs=[
                {
                    "node_id": "9",
                    "output": {
                        "images": [
                            {
                                "filename": "result.png",
                                "subfolder": "preview",
                                "type": "output",
                            }
                        ]
                    },
                }
            ],
        )
    )

    content, media_type = await adapter.fetch_output(
        "job-1",
        "result.png",
        "preview",
        "output",
    )

    assert content == b"image-bytes"
    assert media_type == "image/png"
    assert len(requests) == 1


@pytest.mark.anyio
async def test_stream_output_forwards_range_and_response_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["range"] == "bytes=0-4"
        return httpx.Response(
            206,
            content=b"audio",
            headers={
                "accept-ranges": "bytes",
                "content-range": "bytes 0-4/10",
                "content-type": "audio/wav",
            },
        )

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    adapter = ComfyUIEngineAdapter("http://comfyui.test", tmp_path, log_store=LogStore())
    adapter.job_store.set_result(
        JobResult(
            job_id="job-1",
            status="completed",
            outputs=[
                {
                    "node_id": "12",
                    "output": {
                        "audio": [{"filename": "speech.wav", "subfolder": "", "type": "output"}]
                    },
                }
            ],
        )
    )

    output = await adapter.stream_output("job-1", "speech.wav", "", "output", "bytes=0-4")

    assert b"".join([chunk async for chunk in output.body]) == b"audio"
    assert output.status_code == 206
    assert output.media_type == "audio/wav"
    assert output.headers["accept-ranges"] == "bytes"
    assert output.headers["content-range"] == "bytes 0-4/10"


@pytest.mark.anyio
async def test_fetch_output_rejects_files_not_owned_by_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/history/job-1":
            return httpx.Response(200, json={"job-1": {"outputs": {}, "status": {}}})
        return httpx.Response(200, content=b"leaked-bytes")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    adapter = ComfyUIEngineAdapter(
        "http://comfyui.test", tmp_path, log_store=LogStore()
    )

    with pytest.raises(ValueError, match="not part of this workflow job"):
        await adapter.fetch_output("job-1", "other.png", "", "output")

    assert [request.url.path for request in requests] == ["/history/job-1"]


def test_terminal_progress_logs_once(tmp_path: Path) -> None:
    log_store = LogStore()
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=log_store
    )
    progress = adapter._progress_from_history("job-1", {"status": {"completed": True}})

    adapter._log_terminal_progress_once(progress)
    adapter._log_terminal_progress_once(progress)

    events = log_store.list_events(job_id="job-1").events
    assert len(events) == 1
    assert events[0].message == "ComfyUI execution completed"


def test_progress_from_failed_history(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

    progress = adapter._progress_from_history(
        "job-1",
        {"status": {"completed": False, "status_str": "error"}},
    )

    assert progress.status == "failed"


def test_progress_from_comfyui_progress_ws_message(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

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
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

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
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=log_store
    )

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
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", tmp_path, log_store=LogStore()
    )

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
    assert result.outputs[0]["output"]["images"][0]["view_url"] == (
        "/api/jobs/job-1/outputs/view?filename=sample.png&subfolder=&type=output"
    )


def test_ws_url_for_client_uses_configured_ws_url(tmp_path: Path) -> None:
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path,
        "ws://remote.test:9000/ws",
        log_store=LogStore(),
    )

    assert (
        adapter._ws_url_for_client("abc 123")
        == "ws://remote.test:9000/ws?clientId=abc+123"
    )


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
        log_store=LogStore(),
    )

    graph = {
        "10": {
            "class_type": "LoadImage",
            "inputs": {"image": asset_id, "upload": "image"},
        }
    }

    new_graph, staged = adapter._stage_assets(
        _media_package("load_image", input_name="image"),
        graph,
        "job-99",
    )

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
        log_store=LogStore(),
    )

    _new_graph, staged = adapter._stage_assets(
        _media_package("load_image", input_name="image"),
        {"10": {"class_type": "LoadImage", "inputs": {"image": asset_id}}},
        "job-99",
    )

    assert staged[0].parent == tmp_path / "noofy-input" / "staging"


def test_stage_assets_uses_audio_dashboard_binding(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_id = "12345678-1234-1234-1234-123456789abc.wav"
    (assets_dir / asset_id).write_bytes(b"RIFF\x24\x00\x00\x00WAVEaudio")

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "models",
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )
    graph = {
        "custom": {
            "class_type": "CustomAudioNode",
            "inputs": {"audio_path": asset_id, "other": asset_id},
        }
    }

    new_graph, staged = adapter._stage_assets(
        _media_package("load_audio", node_id="custom", input_name="audio_path"),
        graph,
        "job-audio",
    )

    assert len(staged) == 1
    assert new_graph["custom"]["inputs"]["audio_path"].startswith("staging/")
    assert new_graph["custom"]["inputs"]["other"] == asset_id
    assert graph["custom"]["inputs"]["audio_path"] == asset_id


def test_stage_assets_uses_saved_video_dashboard_binding(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_id = "12345678-1234-1234-1234-123456789abc.mp4"
    (assets_dir / asset_id).write_bytes(b"\x00\x00\x00\x18ftypisom")

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "models",
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )
    graph = {
        "custom": {
            "class_type": "CustomVideoNode",
            "inputs": {"video_path": asset_id, "other": asset_id},
        }
    }

    new_graph, staged = adapter._stage_assets(
        _media_package("load_video", node_id="custom", input_name="video_path"),
        graph,
        "job-video",
    )

    assert len(staged) == 1
    assert new_graph["custom"]["inputs"]["video_path"].startswith("staging/")
    assert new_graph["custom"]["inputs"]["other"] == asset_id
    assert graph["custom"]["inputs"]["video_path"] == asset_id


def test_stage_assets_uses_saved_file_dashboard_binding(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_id = "12345678-1234-1234-1234-123456789abc.workflow.config.json"
    (assets_dir / asset_id).write_bytes(b"{}")

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "models",
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )
    graph = {
        "custom": {
            "class_type": "CustomFileNode",
            "inputs": {"file_path": asset_id, "other": asset_id},
        }
    }

    new_graph, staged = adapter._stage_assets(
        _media_package("load_file", node_id="custom", input_name="file_path"),
        graph,
        "job-file",
    )

    assert len(staged) == 1
    assert new_graph["custom"]["inputs"]["file_path"].startswith("staging/")
    assert new_graph["custom"]["inputs"]["other"] == asset_id
    assert graph["custom"]["inputs"]["file_path"] == asset_id


def test_stage_assets_uses_saved_three_d_dashboard_binding(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_id = "12345678-1234-1234-1234-123456789abc.glb"
    (assets_dir / asset_id).write_bytes(b"glTF\x02\x00\x00\x00")

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "models",
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )
    graph = {
        "custom": {
            "class_type": "Load3D",
            "inputs": {"model_file": asset_id, "other": asset_id},
        }
    }

    new_graph, staged = adapter._stage_assets(
        _media_package("load_3d", node_id="custom", input_name="model_file"),
        graph,
        "job-three-d",
    )

    assert len(staged) == 1
    assert new_graph["custom"]["inputs"]["model_file"].startswith("staging/")
    assert new_graph["custom"]["inputs"]["other"] == asset_id
    assert graph["custom"]["inputs"]["model_file"] == asset_id


def test_stage_assets_reuses_one_file_for_multiple_saved_bindings(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_id = "12345678-1234-1234-1234-123456789abc.wav"
    (assets_dir / asset_id).write_bytes(b"RIFF\x24\x00\x00\x00WAVEaudio")
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "audio_wf", "name": "Audio Workflow", "version": "1.0.0"},
            "engine": "comfyui",
            "required_models": [],
            "custom_nodes": [],
            "comfyui_graph": {},
            "inputs": [
                {"id": "first", "label": "First", "control": "load_audio", "binding": {"node_id": "1", "input_name": "audio"}},
                {"id": "second", "label": "Second", "control": "load_audio", "binding": {"node_id": "2", "input_name": "path"}},
            ],
        }
    )
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "models",
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )

    graph, staged = adapter._stage_assets(
        package,
        {
            "1": {"class_type": "FirstAudioNode", "inputs": {"audio": asset_id}},
            "2": {"class_type": "SecondAudioNode", "inputs": {"path": asset_id}},
        },
        "job-shared",
    )

    assert len(staged) == 1
    assert graph["1"]["inputs"]["audio"] == graph["2"]["inputs"]["path"]


def test_stage_assets_cleans_partial_files_when_staging_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    first_asset_id = "12345678-1234-1234-1234-123456789abc.wav"
    second_asset_id = "abcdef12-1234-1234-1234-123456789abc.wav"
    (assets_dir / first_asset_id).write_bytes(b"first")
    (assets_dir / second_asset_id).write_bytes(b"second")
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "audio_wf", "name": "Audio Workflow", "version": "1.0.0"},
            "engine": "comfyui",
            "required_models": [],
            "custom_nodes": [],
            "comfyui_graph": {},
            "inputs": [
                {"id": "first", "label": "First", "control": "load_audio", "binding": {"node_id": "1", "input_name": "audio"}},
                {"id": "second", "label": "Second", "control": "load_audio", "binding": {"node_id": "2", "input_name": "audio"}},
            ],
        }
    )
    original_stage = comfyui_adapter_module._stage_asset_file
    calls = 0

    def fail_second_stage(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk failure")
        original_stage(source, target)

    monkeypatch.setattr(comfyui_adapter_module, "_stage_asset_file", fail_second_stage)
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "models",
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )

    with pytest.raises(OSError, match="disk failure"):
        adapter._stage_assets(
            package,
            {
                "1": {"class_type": "FirstAudioNode", "inputs": {"audio": first_asset_id}},
                "2": {"class_type": "SecondAudioNode", "inputs": {"audio": second_asset_id}},
            },
            "job-partial",
        )

    assert not list((tmp_path / "input" / "staging").glob("*"))


def test_stage_assets_skips_missing_asset(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        models_dir,
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )
    graph = {
        "10": {
            "class_type": "LoadImage",
            "inputs": {"image": "missing-00000000-0000-0000-0000-000000000000.png"},
        }
    }
    new_graph, staged = adapter._stage_assets(
        _media_package("load_image", input_name="image"),
        graph,
        "job-1",
    )
    assert staged == []
    assert (
        new_graph["10"]["inputs"]["image"]
        == "missing-00000000-0000-0000-0000-000000000000.png"
    )


def test_stage_assets_ignores_non_asset_values(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        models_dir,
        dashboard_assets_dir=assets_dir,
        log_store=LogStore(),
    )
    graph = {
        "5": {
            "class_type": "KSampler",
            "inputs": {"seed": 42, "model": "v1-5.safetensors"},
        }
    }
    new_graph, staged = adapter._stage_assets(
        _media_package("load_image", input_name="image"),
        graph,
        "job-1",
    )
    assert staged == []
    assert new_graph["5"]["inputs"]["seed"] == 42


def test_cleanup_staged_files_removes_files(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", models_dir, log_store=LogStore()
    )

    staged_file = tmp_path / "staged.png"
    staged_file.write_bytes(b"data")
    adapter._staged_files["job-99"] = [staged_file]

    adapter._cleanup_staged_files("job-99")

    assert not staged_file.exists()
    assert "job-99" not in adapter._staged_files


def test_pre_staged_files_are_limited_to_the_jobs_comfyui_staging_directory(tmp_path: Path) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    input_dir = tmp_path / "input"
    staging_dir = input_dir / "staging"
    staging_dir.mkdir(parents=True)
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        models_dir,
        comfyui_input_dir=input_dir,
        log_store=LogStore(),
    )
    trusted = staging_dir / "job-99_gallery_item.png"
    wrong_job = staging_dir / "job-100_gallery_item.png"
    outside = tmp_path / "outside.png"
    for path in (trusted, wrong_job, outside):
        path.write_bytes(b"data")

    assert adapter._trusted_pre_staged_files(
        "job-99",
        [str(trusted), str(wrong_job), str(outside)],
    ) == [trusted]


@pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
def test_terminal_progress_cleans_staged_files_for_terminal_statuses(
    tmp_path: Path, status: str
) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188", models_dir, log_store=LogStore()
    )

    staged_file = tmp_path / f"{status}.png"
    staged_file.write_bytes(b"data")
    adapter._staged_files["job-terminal"] = [staged_file]

    adapter._log_terminal_progress_once(
        JobProgress(job_id="job-terminal", status=status)
    )

    assert not staged_file.exists()
    assert "job-terminal" not in adapter._staged_files


@pytest.mark.anyio
async def test_cancel_job_cleans_staged_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/queue":
            return httpx.Response(
                200,
                json={"queue_running": [[0, "job-cancel"]], "queue_pending": []},
            )
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
    adapter = ComfyUIEngineAdapter(
        "http://comfyui.test", models_dir, log_store=LogStore()
    )
    staged_file = tmp_path / "canceled.png"
    staged_file.write_bytes(b"data")
    adapter._staged_files["job-cancel"] = [staged_file]

    await adapter.cancel_job("job-cancel")

    assert not staged_file.exists()
    assert "job-cancel" not in adapter._staged_files


@pytest.mark.anyio
async def test_cancel_pending_job_deletes_queue_item_without_interrupting_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path == "/queue":
            return httpx.Response(
                200,
                json={"queue_running": [], "queue_pending": [[0, "job-pending"]]},
            )
        if request.method == "POST" and request.url.path == "/queue":
            assert json_payload(request) == {"delete": ["job-pending"]}
            return httpx.Response(200, json={})
        return httpx.Response(500)

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs["transport"] = transport
        return original_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)

    adapter = ComfyUIEngineAdapter(
        "http://comfyui.test", tmp_path, log_store=LogStore()
    )

    progress = await adapter.cancel_job("job-pending")

    assert progress.status == "canceled"
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/queue"),
        ("POST", "/queue"),
    ]


@pytest.mark.anyio
async def test_list_available_models_uses_comfyui_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    adapter = ComfyUIEngineAdapter(
        "http://comfyui.test", tmp_path, log_store=LogStore()
    )
    models = await adapter.list_available_models()

    assert [(model.folder, model.filename) for model in models] == [
        ("checkpoints", "remote-model.safetensors")
    ]
