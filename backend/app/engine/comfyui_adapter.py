import asyncio
import contextlib
import json
import mimetypes
import os
import re
import shutil
from pathlib import Path
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from uuid import uuid4

import httpx
import websockets

from app.engine.errors import EngineUserFixableValidationError
from app.engine.job_store import JobStore
from app.diagnostics import DiagnosticsSink, sanitize_text
from app.media_types import MEDIA_KINDS as _MEDIA_KINDS
from app.media_types import MEDIA_OUTPUT_BUCKETS as _MEDIA_OUTPUT_BUCKETS
from app.media_types import classify_media_kind
from app.engine.models import (
    EngineJob,
    EngineOutputStream,
    JobProgress,
    JobResult,
    ModelInfo,
)
from app.engine.adapter import EngineMemoryCleanupCapabilities, EngineMemoryCleanupMode
from app.runs.credentials import plan_from_options
from app.workflows.package import WorkflowPackage
from app.workflows.run_input_validation import map_comfyui_submission_validation_error

_ASSET_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:\.[A-Za-z0-9_-]+)+$",
    re.IGNORECASE,
)
_DASHBOARD_MEDIA_CONTROLS = frozenset({"load_image", "load_image_mask", "load_audio", "load_video", "load_file", "load_3d"})


class ComfyUIEngineAdapter:
    def __init__(
        self,
        base_url: str,
        models_dir: Path,
        ws_url: str | None = None,
        job_store: JobStore | None = None,
        *,
        log_store: DiagnosticsSink,
        dashboard_assets_dir: Path | None = None,
        comfyui_input_dir: Path | None = None,
        model_roots: list[Path] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url or self._default_ws_url(self.base_url)
        self.models_dir = models_dir
        self.model_roots = model_roots or [models_dir]
        self.job_store = job_store or JobStore()
        self.log_store = log_store
        self.dashboard_assets_dir = dashboard_assets_dir
        self.comfyui_input_dir = comfyui_input_dir or self.models_dir.parent / "input"
        self._listener_tasks: dict[str, asyncio.Task[None]] = {}
        self._terminal_log_job_ids: set[str] = set()
        self._staged_files: dict[str, list[Path]] = {}
        self._output_kinds_by_job: dict[str, dict[str, str]] = {}
        self._terminal_notifier: Callable[[str], None] | None = None

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url or self._default_ws_url(self.base_url)

    def configure_model_roots(self, model_roots: list[Path]) -> None:
        self.model_roots = model_roots or [self.models_dir]
        self.models_dir = self.model_roots[0]

    def configure_terminal_notifier(self, notifier: Callable[[str], None] | None) -> None:
        self._terminal_notifier = notifier

    def memory_cleanup_capabilities(self) -> EngineMemoryCleanupCapabilities:
        return EngineMemoryCleanupCapabilities(
            modes=frozenset({EngineMemoryCleanupMode.RUNNER_FREE}),
            observed_release_confirmation=True,
            notes=(
                "ComfyUI /free supports unload_models and free_memory only in the vendored runtime.",
                "No stable public per-model or per-LoRA unload-by-reference route is exposed.",
            ),
        )

    async def release_memory(self) -> None:
        """Ask an idle ComfyUI runner to unload models and empty its cache."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.base_url}/free",
                    json={"unload_models": True, "free_memory": True},
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self.log_store.add(
                "error",
                "Failed to release ComfyUI memory",
                "comfyui.adapter",
                details={"error": str(exc)},
            )
            raise ValueError(
                f"Failed to release ComfyUI memory: {sanitize_text(str(exc))}"
            ) from exc
        self.log_store.add(
            "info",
            "Requested ComfyUI model and cache memory release",
            "comfyui.adapter",
        )

    async def run_workflow(
        self,
        workflow_package: WorkflowPackage,
        graph: dict[str, Any],
        _inputs: dict[str, Any],
        options: dict[str, Any],
    ) -> EngineJob:
        requested_job_id = options.get("job_id")
        job_id = str(requested_job_id) if isinstance(requested_job_id, str) and requested_job_id else str(uuid4())
        client_id = options.get("client_id") or f"local-ai-workflow-{uuid4()}"
        job = EngineJob(
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )
        self.job_store.add_job(job)
        self._output_kinds_by_job[job_id] = _unambiguous_output_kinds_by_node(workflow_package)
        self.log_store.add(
            "info",
            "Created ComfyUI job",
            "comfyui.adapter",
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
            details={"client_id": client_id},
        )

        pre_staged_files = self._trusted_pre_staged_files(
            job_id,
            options.get("_noofy_staged_files"),
        )
        if pre_staged_files:
            self._staged_files[job_id] = list(pre_staged_files)

        if self.dashboard_assets_dir is not None:
            graph, staged_files = self._stage_assets(
                workflow_package,
                graph,
                job_id,
            )
            if staged_files:
                self._staged_files.setdefault(job_id, []).extend(staged_files)

        if options.get("listen_for_events", True):
            await self._start_event_listener(
                job_id,
                client_id,
                connect_timeout=float(options.get("ws_connect_timeout", 2)),
            )

        credential_plan = plan_from_options(options)
        extra_data = {
            "workflow_id": workflow_package.metadata.id,
            "workflow_version": workflow_package.metadata.version,
            **credential_plan.extra_data,
        }
        payload = {
            "prompt": graph,
            "prompt_id": job_id,
            "client_id": client_id,
            "extra_data": extra_data,
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.base_url}/prompt", json=payload)
                if response.is_error:
                    response_text = response.text
                    try:
                        response_json: Any = response.json()
                    except ValueError:
                        response_json = None
                    user_error = map_comfyui_submission_validation_error(
                        package=workflow_package,
                        submitted_inputs=_inputs,
                        status_code=response.status_code,
                        response_json=response_json,
                        response_text=response_text,
                    )
                    if user_error is not None:
                        self._stop_event_listener(job_id)
                        self._cleanup_staged_files(job_id)
                        self.job_store.set_progress(
                            JobProgress(job_id=job_id, status="failed", message=user_error.user_message)
                        )
                        self.job_store.set_result(
                            JobResult(job_id=job_id, status="failed", error=user_error.message)
                        )
                        self.log_store.add(
                            "warning",
                            "ComfyUI validation failure mapped to user-fixable input error",
                            "comfyui.adapter",
                            job_id=job_id,
                            workflow_id=workflow_package.metadata.id,
                            details={"user_error": user_error.model_dump(mode="json")},
                        )
                        raise EngineUserFixableValidationError(user_error)
                response.raise_for_status()
        except EngineUserFixableValidationError:
            raise
        except httpx.HTTPError as exc:
            self._stop_event_listener(job_id)
            self._cleanup_staged_files(job_id)
            self.job_store.set_progress(
                JobProgress(job_id=job_id, status="failed", message=sanitize_text(str(exc)))
            )
            self.job_store.set_result(
                JobResult(job_id=job_id, status="failed", error=sanitize_text(str(exc)))
            )
            self.log_store.add(
                "error",
                "Failed to submit workflow to ComfyUI",
                "comfyui.adapter",
                job_id=job_id,
                workflow_id=workflow_package.metadata.id,
                details={"error": str(exc)},
            )
            raise ValueError(
                f"Failed to submit workflow to ComfyUI: {sanitize_text(str(exc))}"
            ) from exc

        self.log_store.add(
            "info",
            "Submitted workflow to ComfyUI",
            "comfyui.adapter",
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
        )
        return job

    async def get_progress(self, job_id: str) -> JobProgress:
        history_entry = await self._get_history_entry(job_id)
        if history_entry is not None:
            progress = self._progress_from_history(job_id, history_entry)
            self.job_store.set_progress(progress)
            self.job_store.set_result(self._result_from_history(job_id, history_entry))
            self._log_terminal_progress_once(progress)
            return progress

        queue_status = await self._get_queue_status(job_id)
        if queue_status is not None:
            stored_progress = self.job_store.get_progress(job_id)
            if (
                stored_progress.status == queue_status.status
                and self._has_progress_detail(stored_progress)
            ):
                return stored_progress
            self.job_store.set_progress(queue_status)
            return queue_status

        return self.job_store.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        queue_status = await self._get_queue_status(job_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if queue_status is not None and queue_status.status == "queued":
                    response = await client.post(
                        f"{self.base_url}/queue",
                        json={"delete": [job_id]},
                    )
                    response.raise_for_status()
                else:
                    response = await client.post(
                        f"{self.base_url}/interrupt",
                        json={"prompt_id": job_id},
                    )
                    response.raise_for_status()
        except httpx.HTTPError as exc:
            self.log_store.add(
                "error",
                "Failed to cancel ComfyUI job",
                "comfyui.adapter",
                job_id=job_id,
                details={
                    "error": str(exc),
                    "queue_status": queue_status.status if queue_status is not None else None,
                },
            )
            raise ValueError(f"Failed to cancel workflow run: {sanitize_text(str(exc))}") from exc

        progress = JobProgress(
            job_id=job_id, status="canceled", message="Cancel requested"
        )
        self.job_store.set_progress(progress)
        self.job_store.set_result(JobResult(job_id=job_id, status="canceled"))
        self._stop_event_listener(job_id)
        self._cleanup_staged_files(job_id)
        self.log_store.add(
            "info", "ComfyUI job canceled", "comfyui.adapter", job_id=job_id
        )
        self._terminal_log_job_ids.add(job_id)
        self._notify_terminal(job_id)
        return progress

    async def get_result(self, job_id: str) -> JobResult:
        history_entry = await self._get_history_entry(job_id)
        if history_entry is None:
            result = self.job_store.get_result(job_id)
            if result.status == "completed":
                await self._hydrate_missing_output_metadata(job_id, result)
                self.job_store.set_result(result)
            return result

        result = self._result_from_history(job_id, history_entry)
        if result.status == "completed":
            await self._hydrate_missing_output_metadata(job_id, result)
        self.job_store.set_result(result)
        progress = self._progress_from_history(job_id, history_entry)
        self.job_store.set_progress(progress)
        self._log_terminal_progress_once(progress)
        return result

    async def _hydrate_missing_output_metadata(self, job_id: str, result: JobResult) -> None:
        hydrated_count = 0
        for output_record in result.outputs:
            node_output = output_record.get("output")
            if not isinstance(node_output, dict):
                continue
            for bucket_name in _MEDIA_OUTPUT_BUCKETS:
                items = node_output.get(bucket_name)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict) or item.get("size") is not None:
                        continue
                    metadata = await self._probe_output_metadata(item)
                    if metadata is None:
                        continue
                    size, mime_type = metadata
                    item["size"] = size
                    if mime_type and item.get("mime_type") == "application/octet-stream":
                        item["mime_type"] = mime_type
                    hydrated_count += 1
        if hydrated_count:
            self.log_store.add(
                "debug",
                "Probed generated output metadata",
                "comfyui.adapter",
                job_id=job_id,
                details={"output_count": hydrated_count},
            )

    async def _probe_output_metadata(self, item: dict[str, Any]) -> tuple[int, str | None] | None:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream(
                    "GET",
                    f"{self.base_url}/view",
                    params={
                        "filename": str(item.get("filename") or ""),
                        "subfolder": str(item.get("subfolder") or ""),
                        "type": _engine_output_type_from_item(item),
                    },
                    headers={"Range": "bytes=0-0"},
                ) as response:
                    if response.status_code not in {200, 206}:
                        return None
                    content_range = response.headers.get("content-range", "")
                    total_match = re.search(r"/(\d+)$", content_range)
                    if total_match:
                        return int(total_match.group(1)), response.headers.get("content-type")
                    if response.status_code == 200:
                        content_length = response.headers.get("content-length")
                        if content_length and content_length.isdigit():
                            return int(content_length), response.headers.get("content-type")
        except httpx.HTTPError:
            return None
        return None

    async def list_available_models(self) -> list[ModelInfo]:
        api_models = await self._list_available_models_from_api()
        if api_models:
            return api_models
        return self._list_available_models_from_filesystem()

    async def upload_workflow_image(
        self,
        workflow_package: WorkflowPackage,
        filename: str,
        data: bytes,
        content_type: str,
    ) -> dict[str, str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/upload/image",
                files={"image": (filename, data, content_type)},
            )

        if response.status_code not in (200, 201):
            raise ValueError(f"ComfyUI upload failed with status {response.status_code}")

        result = response.json()
        uploaded_filename = result.get("name") if isinstance(result, dict) else None
        self.log_store.add(
            "info",
            "Uploaded workflow image",
            "comfyui.adapter",
            workflow_id=workflow_package.metadata.id,
            details={"filename": uploaded_filename or filename},
        )
        return {"filename": uploaded_filename or filename}

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        stream = await self.stream_output(job_id, filename, subfolder, output_type)
        return b"".join([chunk async for chunk in stream.body]), stream.media_type

    async def stream_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
        range_header: str | None = None,
    ) -> EngineOutputStream:
        await self._ensure_output_belongs_to_job(
            job_id=job_id,
            filename=filename,
            subfolder=subfolder,
            output_type=output_type,
        )
        client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None))
        try:
            request = client.build_request(
                "GET",
                f"{self.base_url}/view",
                params={"filename": filename, "subfolder": subfolder, "type": output_type},
                headers={"Range": range_header} if range_header else None,
            )
            response = await client.send(request, stream=True)
        except Exception:
            await client.aclose()
            raise

        if response.status_code not in {200, 206}:
            await response.aclose()
            await client.aclose()
            raise ValueError(f"ComfyUI output fetch failed with status {response.status_code}")

        async def body():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        forwarded_headers = {
            name: value
            for name in (
                "accept-ranges",
                "cache-control",
                "content-length",
                "content-range",
                "etag",
                "last-modified",
            )
            if (value := response.headers.get(name)) is not None
        }
        return EngineOutputStream(
            body=body(),
            media_type=response.headers.get("content-type", "application/octet-stream"),
            status_code=response.status_code,
            headers=forwarded_headers,
        )

    async def _ensure_output_belongs_to_job(
        self,
        *,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> None:
        result = self.job_store.get_result(job_id)
        if not self._result_contains_output(
            result,
            filename=filename,
            subfolder=subfolder,
            output_type=output_type,
        ):
            history_entry = await self._get_history_entry(job_id)
            if history_entry is not None:
                result = self._result_from_history(job_id, history_entry)
                self.job_store.set_result(result)
                self.job_store.set_progress(
                    self._progress_from_history(job_id, history_entry)
                )

        if not self._result_contains_output(
            self.job_store.get_result(job_id),
            filename=filename,
            subfolder=subfolder,
            output_type=output_type,
        ):
            self.log_store.add(
                "warning",
                "Blocked output fetch for file not produced by job",
                "comfyui.adapter",
                job_id=job_id,
                details={
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": output_type,
                },
            )
            raise ValueError("Requested output is not part of this workflow job.")

    def _result_contains_output(
        self,
        result: JobResult,
        *,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> bool:
        for output in result.outputs:
            payload = output.get("output")
            if not isinstance(payload, dict):
                continue
            for media_kind in _MEDIA_OUTPUT_BUCKETS:
                items = payload.get(media_kind)
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if (
                        str(item.get("filename") or "") == filename
                        and str(item.get("subfolder") or "") == subfolder
                        and _engine_output_type_from_item(item) == output_type
                    ):
                        return True
        return False

    async def _list_available_models_from_api(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                folders_response = await client.get(f"{self.base_url}/models")
                folders_response.raise_for_status()
                folders = folders_response.json()
        except httpx.HTTPError:
            self.log_store.add(
                "warning",
                "Could not list models from ComfyUI API; falling back to filesystem",
                "comfyui.adapter",
            )
            return []

        if not isinstance(folders, list):
            return []

        models: list[ModelInfo] = []
        async with httpx.AsyncClient(timeout=10) as client:
            for folder in sorted(
                folder for folder in folders if isinstance(folder, str)
            ):
                try:
                    models_response = await client.get(
                        f"{self.base_url}/models/{folder}"
                    )
                    models_response.raise_for_status()
                except httpx.HTTPError:
                    self.log_store.add(
                        "warning",
                        "Could not list ComfyUI model folder",
                        "comfyui.adapter",
                        details={"folder": folder},
                    )
                    continue

                filenames = models_response.json()
                if not isinstance(filenames, list):
                    continue

                for filename in sorted(
                    item for item in filenames if isinstance(item, str)
                ):
                    models.append(ModelInfo(folder=folder, filename=filename))
        return models

    def _list_available_models_from_filesystem(self) -> list[ModelInfo]:
        models: list[ModelInfo] = []
        seen: set[tuple[str, str]] = set()
        for root in self.model_roots:
            if not root.exists():
                continue
            for folder in sorted(path for path in root.iterdir() if path.is_dir()):
                for file_path in sorted(path for path in folder.iterdir() if path.is_file()):
                    if file_path.name.startswith("put_"):
                        continue
                    key = (folder.name, file_path.name)
                    if key in seen:
                        continue
                    seen.add(key)
                    models.append(
                        ModelInfo(
                            folder=folder.name,
                            filename=file_path.name,
                            path=str(file_path),
                        )
                    )
        return models

    async def _start_event_listener(
        self, job_id: str, client_id: str, connect_timeout: float
    ) -> None:
        self._stop_event_listener(job_id)
        ready_event = asyncio.Event()
        task = asyncio.create_task(
            self._listen_for_job_events(job_id, client_id, ready_event)
        )
        task.add_done_callback(
            lambda completed_task: (
                completed_task.exception() if not completed_task.cancelled() else None
            )
        )
        self._listener_tasks[job_id] = task
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ready_event.wait(), timeout=connect_timeout)
        if not ready_event.is_set():
            self.log_store.add(
                "warning",
                "Timed out waiting for ComfyUI WebSocket listener",
                "comfyui.adapter",
                job_id=job_id,
                details={"timeout_seconds": connect_timeout},
            )

    def _stop_event_listener(self, job_id: str) -> None:
        task = self._listener_tasks.pop(job_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _listen_for_job_events(
        self, job_id: str, client_id: str, ready_event: asyncio.Event
    ) -> None:
        ws_url = self._ws_url_for_client(client_id)
        try:
            async with websockets.connect(ws_url) as websocket:
                ready_event.set()
                self.log_store.add(
                    "debug",
                    "Connected to ComfyUI WebSocket",
                    "comfyui.adapter",
                    job_id=job_id,
                )
                async for raw_message in websocket:
                    if not isinstance(raw_message, str):
                        continue
                    should_stop = self._handle_ws_message(job_id, raw_message)
                    if should_stop:
                        return
        except Exception as exc:
            ready_event.set()
            current = self.job_store.get_progress(job_id)
            if current.status not in {"completed", "failed", "canceled"}:
                self.job_store.set_progress(
                    JobProgress(
                        job_id=job_id,
                        status=current.status,
                        value=current.value,
                        max=current.max,
                        current_node=current.current_node,
                        message=f"ComfyUI WebSocket listener stopped: {sanitize_text(str(exc))}",
                    )
                )
                self.log_store.add(
                    "warning",
                    "ComfyUI WebSocket listener stopped",
                    "comfyui.adapter",
                    job_id=job_id,
                    details={"error": str(exc)},
                )

    def _handle_ws_message(self, job_id: str, raw_message: str) -> bool:
        with contextlib.suppress(json.JSONDecodeError):
            message = json.loads(raw_message)
            progress = self._progress_from_ws_message(job_id, message)
            if progress is not None:
                self.job_store.set_progress(progress)
                if progress.status == "failed":
                    self.job_store.set_result(
                        JobResult(
                            job_id=job_id, status="failed", error=progress.message
                        )
                    )
                self._log_terminal_progress_once(progress)
                if progress.status in {"completed", "failed", "canceled"}:
                    return True

            result = self._result_from_ws_message(job_id, message)
            if result is not None:
                self.job_store.set_result(result)
                self.log_store.add(
                    "debug",
                    "ComfyUI node output received",
                    "comfyui.adapter",
                    job_id=job_id,
                    details={"output_count": len(result.outputs)},
                )
                return result.status in {"completed", "failed", "canceled"}
        return False

    def _progress_from_ws_message(
        self, job_id: str, message: dict[str, Any]
    ) -> JobProgress | None:
        message_type = message.get("type")
        data = message.get("data", {})
        if not isinstance(data, dict) or data.get("prompt_id") != job_id:
            return None

        if message_type == "execution_start":
            return JobProgress(
                job_id=job_id, status="running", message="Execution started"
            )

        if message_type == "executing":
            node = data.get("node")
            if node is None:
                return JobProgress(job_id=job_id, status="completed", value=1, max=1)
            return JobProgress(job_id=job_id, status="running", current_node=str(node))

        if message_type == "progress":
            node = data.get("node")
            return JobProgress(
                job_id=job_id,
                status="running",
                value=self._optional_int(data.get("value")),
                max=self._optional_int(data.get("max")),
                current_node=str(node) if node is not None else None,
            )

        if message_type == "progress_state":
            return self._progress_from_progress_state(job_id, data)

        if message_type == "execution_success":
            return JobProgress(
                job_id=job_id,
                status="completed",
                value=1,
                max=1,
                message="Execution completed",
            )

        if message_type == "execution_interrupted":
            return JobProgress(
                job_id=job_id, status="canceled", message="Execution interrupted"
            )

        if message_type == "execution_error":
            error_message = (
                data.get("exception_message")
                or data.get("message")
                or "ComfyUI execution error"
            )
            node = data.get("node_id") or data.get("node")
            return JobProgress(
                job_id=job_id,
                status="failed",
                current_node=str(node) if node is not None else None,
                message=sanitize_text(str(error_message)),
            )

        return None

    def _progress_from_progress_state(
        self, job_id: str, data: dict[str, Any]
    ) -> JobProgress | None:
        nodes = data.get("nodes", {})
        if not isinstance(nodes, dict):
            return None

        for node_id, node_state in nodes.items():
            if not isinstance(node_state, dict) or node_state.get("state") != "running":
                continue
            return JobProgress(
                job_id=job_id,
                status="running",
                value=self._optional_int(node_state.get("value")),
                max=self._optional_int(node_state.get("max")),
                current_node=str(node_state.get("node_id") or node_id),
            )
        return JobProgress(job_id=job_id, status="running")

    def _result_from_ws_message(
        self, job_id: str, message: dict[str, Any]
    ) -> JobResult | None:
        if message.get("type") != "executed":
            return None

        data = message.get("data", {})
        if not isinstance(data, dict) or data.get("prompt_id") != job_id:
            return None

        node = data.get("node")
        output = data.get("output", {})
        if node is None or not isinstance(output, dict):
            return None

        return JobResult(
            job_id=job_id,
            status="running",
            outputs=[
                {
                    "node_id": str(node),
                    "output": self._add_view_urls(job_id, str(node), output),
                }
            ],
        )

    async def _get_history_entry(self, job_id: str) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/history/{job_id}")
                response.raise_for_status()
        except httpx.HTTPError:
            return None

        history = response.json()
        if not isinstance(history, dict):
            return None
        entry = history.get(job_id)
        return entry if isinstance(entry, dict) else None

    async def _get_queue_status(self, job_id: str) -> JobProgress | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.base_url}/queue")
                response.raise_for_status()
        except httpx.HTTPError:
            return None

        queue = response.json()
        if self._queue_contains_job(queue.get("queue_running", []), job_id):
            return JobProgress(job_id=job_id, status="running")
        if self._queue_contains_job(queue.get("queue_pending", []), job_id):
            return JobProgress(job_id=job_id, status="queued")
        return None

    def _queue_contains_job(self, queue_items: list[Any], job_id: str) -> bool:
        for item in queue_items:
            if isinstance(item, list) and len(item) > 1 and item[1] == job_id:
                return True
        return False

    def _has_progress_detail(self, progress: JobProgress) -> bool:
        return (
            progress.value is not None
            or progress.max is not None
            or progress.current_node is not None
            or progress.message is not None
        )

    def _log_terminal_progress_once(self, progress: JobProgress) -> None:
        if progress.status not in {"completed", "failed", "canceled"}:
            return
        self._notify_terminal(progress.job_id)
        if progress.job_id in self._terminal_log_job_ids:
            return
        self._terminal_log_job_ids.add(progress.job_id)
        self._cleanup_staged_files(progress.job_id)

        if progress.status == "completed":
            self.log_store.add(
                "info",
                "ComfyUI execution completed",
                "comfyui.adapter",
                job_id=progress.job_id,
            )

        elif progress.status == "failed":
            self.log_store.add(
                "error",
                "ComfyUI execution failed",
                "comfyui.adapter",
                job_id=progress.job_id,
                details={"message": progress.message, "node": progress.current_node},
            )
        elif progress.status == "canceled":
            self.log_store.add(
                "warning",
                "ComfyUI execution interrupted",
                "comfyui.adapter",
                job_id=progress.job_id,
            )

    def _notify_terminal(self, job_id: str) -> None:
        if self._terminal_notifier is not None:
            self._terminal_notifier(job_id)

    def _cleanup_staged_files(self, job_id: str) -> None:
        for path in self._staged_files.pop(job_id, []):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _trusted_pre_staged_files(self, job_id: str, values: Any) -> list[Path]:
        if not isinstance(values, list):
            return []
        staging_dir = (self.comfyui_input_dir / "staging").resolve()
        trusted: list[Path] = []
        for value in values:
            if not isinstance(value, str) or not value:
                continue
            try:
                path = Path(value).resolve()
            except (OSError, RuntimeError):
                continue
            if path.parent == staging_dir and path.name.startswith(f"{job_id}_"):
                trusted.append(path)
        return trusted

    def _progress_from_history(
        self, job_id: str, history_entry: dict[str, Any]
    ) -> JobProgress:
        status = history_entry.get("status", {})
        status_str = status.get("status_str") if isinstance(status, dict) else None
        completed = bool(status.get("completed")) if isinstance(status, dict) else False

        if completed:
            return JobProgress(job_id=job_id, status="completed", value=1, max=1)
        if status_str == "error":
            return JobProgress(
                job_id=job_id,
                status="failed",
                message="ComfyUI reported execution error",
            )
        return JobProgress(job_id=job_id, status="running")

    def _result_from_history(
        self, job_id: str, history_entry: dict[str, Any]
    ) -> JobResult:
        status = history_entry.get("status", {})
        completed = bool(status.get("completed")) if isinstance(status, dict) else False
        status_str = status.get("status_str") if isinstance(status, dict) else None

        if status_str == "error":
            return JobResult(
                job_id=job_id,
                status="failed",
                outputs=[],
                error="ComfyUI reported execution error",
            )

        outputs = []
        for node_id, node_output in history_entry.get("outputs", {}).items():
            if isinstance(node_output, dict):
                outputs.append(
                    {
                        "node_id": node_id,
                        "output": self._add_view_urls(job_id, str(node_id), node_output),
                    }
                )

        return JobResult(
            job_id=job_id,
            status="completed" if completed else "running",
            outputs=outputs,
        )

    def _add_view_urls(
        self,
        job_id: str,
        node_id: str,
        node_output: dict[str, Any],
    ) -> dict[str, Any]:
        enriched = dict(node_output)
        declared_kind = self._output_kinds_by_job.get(job_id, {}).get(node_id)
        for bucket_name in _MEDIA_OUTPUT_BUCKETS:
            items = enriched.get(bucket_name)
            if not isinstance(items, list):
                continue
            enriched[bucket_name] = [
                (
                    self._enrich_media_output(
                        job_id,
                        item,
                        classify_media_kind(item, bucket_name, declared_kind),
                    )
                    if isinstance(item, dict)
                    else item
                )
                for item in items
            ]
        return enriched

    def _enrich_media_output(
        self,
        job_id: str,
        item: dict[str, Any],
        kind: str,
    ) -> dict[str, Any]:
        output_type = _engine_output_type_from_item(item)
        view_url = self._build_view_url(job_id, item)
        enriched = {
            **item,
            "kind": kind,
            "type": kind,
            "output_type": output_type,
            "url": view_url,
            "view_url": view_url,
            "mime_type": item.get("mime_type")
            or item.get("content_type")
            or mimetypes.guess_type(str(item.get("filename") or ""))[0]
            or ("audio/mpeg" if kind == "audio" else "application/octet-stream"),
            "size": item.get("size"),
        }
        suffix = Path(str(item.get("filename") or "")).suffix.lower()
        if suffix and "extension" not in enriched:
            enriched["extension"] = suffix
        thumbnail_url = _backend_owned_thumbnail_url(item.get("thumbnail_url"))
        if thumbnail_url is None:
            enriched.pop("thumbnail_url", None)
        else:
            enriched["thumbnail_url"] = thumbnail_url
        return enriched

    def _build_view_url(self, job_id: str, item: dict[str, Any]) -> str:
        query = urlencode(
            {
                "filename": item.get("filename", ""),
                "subfolder": item.get("subfolder", ""),
                "type": _engine_output_type_from_item(item),
            }
        )
        return f"/api/jobs/{job_id}/outputs/view?{query}"

    def _ws_url_for_client(self, client_id: str) -> str:
        separator = "&" if "?" in self.ws_url else "?"
        return f"{self.ws_url}{separator}{urlencode({'clientId': client_id})}"

    def _default_ws_url(self, base_url: str) -> str:
        parsed = urlparse(base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((scheme, parsed.netloc, "/ws", "", "", ""))

    def _stage_assets(
        self,
        workflow_package: WorkflowPackage,
        graph: dict[str, Any],
        job_id: str,
    ) -> tuple[dict[str, Any], list[Path]]:
        """Materialize bound dashboard media assets into ComfyUI input/staging/."""
        if self.dashboard_assets_dir is None:
            return graph, []

        staged: list[Path] = []
        staged_graph: dict[str, Any] | None = None
        cloned_inputs_by_node: dict[str, dict[str, Any]] = {}
        comfyui_input_dir = self.comfyui_input_dir / "staging"
        media_bindings = {
            (workflow_input.binding.node_id, workflow_input.binding.input_name): workflow_input
            for workflow_input in workflow_package.inputs
            if workflow_input.control in _DASHBOARD_MEDIA_CONTROLS
        }

        staged_paths_by_asset_id: dict[str, Path] = {}
        try:
            for (node_id, input_name), workflow_input in media_bindings.items():
                node_def = graph.get(node_id)
                if not isinstance(node_def, dict):
                    continue
                node_inputs = node_def.get("inputs", {})
                if not isinstance(node_inputs, dict):
                    continue
                value = node_inputs.get(input_name)
                if not isinstance(value, str) or not _ASSET_ID_RE.match(value):
                    continue
                asset_path = self.dashboard_assets_dir / value
                if not asset_path.exists():
                    self.log_store.add(
                        "warning",
                        "Dashboard asset not found; skipping staging",
                        "comfyui.adapter",
                        job_id=job_id,
                        details={"asset_id": value, "node_id": node_id, "input_name": input_name},
                    )
                    continue
                staged_path = staged_paths_by_asset_id.get(value)
                if staged_path is None:
                    comfyui_input_dir.mkdir(parents=True, exist_ok=True)
                    staged_name = f"{job_id}_{value}"
                    staged_path = comfyui_input_dir / staged_name
                    _stage_asset_file(asset_path, staged_path)
                    staged_paths_by_asset_id[value] = staged_path
                    staged.append(staged_path)
                else:
                    staged_name = staged_path.name
                if staged_graph is None:
                    staged_graph = dict(graph)
                if node_id not in cloned_inputs_by_node:
                    node_copy = dict(node_def)
                    node_inputs = dict(node_inputs)
                    node_copy["inputs"] = node_inputs
                    staged_graph[node_id] = node_copy
                    cloned_inputs_by_node[node_id] = node_inputs
                else:
                    node_inputs = cloned_inputs_by_node[node_id]
                # ComfyUI expects filename relative to its input/ root.
                node_inputs[input_name] = f"staging/{staged_name}"
                self.log_store.add(
                    "debug",
                    "Staged dashboard asset for ComfyUI",
                    "comfyui.adapter",
                    job_id=job_id,
                    details={
                        "asset_id": value,
                        "staged": staged_name,
                        "node_id": node_id,
                        "input_name": input_name,
                        "control": workflow_input.control,
                    },
                )
        except Exception:
            for staged_path in staged:
                with contextlib.suppress(OSError):
                    staged_path.unlink()
            raise

        return staged_graph or graph, staged

    def _optional_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


def _stage_asset_file(source: Path, target: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _unambiguous_output_kinds_by_node(workflow_package: WorkflowPackage) -> dict[str, str]:
    kinds_by_node: dict[str, set[str]] = {}
    for output in workflow_package.outputs:
        kind = output.kind or output.type
        if kind in _MEDIA_KINDS:
            kinds_by_node.setdefault(output.node_id, set()).add(kind)
    return {
        node_id: next(iter(kinds))
        for node_id, kinds in kinds_by_node.items()
        if len(kinds) == 1
    }


def _engine_output_type_from_item(item: dict[str, Any]) -> str:
    output_type = item.get("output_type")
    if isinstance(output_type, str) and output_type:
        return output_type
    legacy_type = item.get("type")
    if isinstance(legacy_type, str) and legacy_type and legacy_type not in _MEDIA_KINDS:
        return legacy_type
    return "output"


def _backend_owned_thumbnail_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value if value.startswith("/api/") else None
