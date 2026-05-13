import asyncio
import contextlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from uuid import uuid4

import httpx
import websockets

from app.engine.job_store import JobStore
from app.diagnostics import DiagnosticsSink
from app.engine.models import EngineJob, JobProgress, JobResult, ModelInfo
from app.workflows.package import WorkflowPackage

_ASSET_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.(jpg|jpeg|png|webp|gif)$",
    re.IGNORECASE,
)


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

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url or self._default_ws_url(self.base_url)

    def configure_model_roots(self, model_roots: list[Path]) -> None:
        self.model_roots = model_roots or [self.models_dir]
        self.models_dir = self.model_roots[0]

    async def run_workflow(
        self,
        workflow_package: WorkflowPackage,
        graph: dict[str, Any],
        _inputs: dict[str, Any],
        options: dict[str, Any],
    ) -> EngineJob:
        job_id = str(uuid4())
        client_id = options.get("client_id") or f"local-ai-workflow-{uuid4()}"
        job = EngineJob(
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )
        self.job_store.add_job(job)
        self.log_store.add(
            "info",
            "Created ComfyUI job",
            "comfyui.adapter",
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
            details={"client_id": client_id},
        )

        if self.dashboard_assets_dir is not None:
            graph, self._staged_files[job_id] = self._stage_assets(graph, job_id)

        if options.get("listen_for_events", True):
            await self._start_event_listener(
                job_id,
                client_id,
                connect_timeout=float(options.get("ws_connect_timeout", 2)),
            )

        payload = {
            "prompt": graph,
            "prompt_id": job_id,
            "client_id": client_id,
            "extra_data": {
                "workflow_id": workflow_package.metadata.id,
                "workflow_version": workflow_package.metadata.version,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{self.base_url}/prompt", json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            self._stop_event_listener(job_id)
            self._cleanup_staged_files(job_id)
            self.job_store.set_progress(
                JobProgress(job_id=job_id, status="failed", message=str(exc))
            )
            self.job_store.set_result(
                JobResult(job_id=job_id, status="failed", error=str(exc))
            )
            self.log_store.add(
                "error",
                "Failed to submit workflow to ComfyUI",
                "comfyui.adapter",
                job_id=job_id,
                workflow_id=workflow_package.metadata.id,
                details={"error": str(exc)},
            )
            raise

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
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(f"{self.base_url}/interrupt", json={"prompt_id": job_id})

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
        return progress

    async def get_result(self, job_id: str) -> JobResult:
        history_entry = await self._get_history_entry(job_id)
        if history_entry is None:
            return self.job_store.get_result(job_id)

        result = self._result_from_history(job_id, history_entry)
        self.job_store.set_result(result)
        progress = self._progress_from_history(job_id, history_entry)
        self.job_store.set_progress(progress)
        self._log_terminal_progress_once(progress)
        return result

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
        del job_id
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/view",
                params={
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": output_type,
                },
            )

        if response.status_code != 200:
            raise ValueError(f"ComfyUI output fetch failed with status {response.status_code}")
        return (
            response.content,
            response.headers.get("content-type", "application/octet-stream"),
        )

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
                        message=f"ComfyUI WebSocket listener stopped: {exc}",
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
                message=str(error_message),
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
                    "output": self._add_view_urls(job_id, output),
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

    def _cleanup_staged_files(self, job_id: str) -> None:
        for path in self._staged_files.pop(job_id, []):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

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
                        "output": self._add_view_urls(job_id, node_output),
                    }
                )

        return JobResult(
            job_id=job_id,
            status="completed" if completed else "running",
            outputs=outputs,
        )

    def _add_view_urls(self, job_id: str, node_output: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(node_output)
        for output_type in ("images", "audio", "video"):
            items = enriched.get(output_type)
            if not isinstance(items, list):
                continue
            enriched[output_type] = [
                (
                    {
                        **item,
                        "view_url": self._build_view_url(job_id, item),
                    }
                    if isinstance(item, dict)
                    else item
                )
                for item in items
            ]
        return enriched

    def _build_view_url(self, job_id: str, item: dict[str, Any]) -> str:
        query = urlencode(
            {
                "filename": item.get("filename", ""),
                "subfolder": item.get("subfolder", ""),
                "type": item.get("type", "output"),
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
        graph: dict[str, Any],
        job_id: str,
    ) -> tuple[dict[str, Any], list[Path]]:
        """Copy dashboard asset files into ComfyUI's input/staging/ dir.

        For each input whose value looks like an asset_id (UUID + image ext),
        the asset file is copied to ComfyUI input/staging/ and the graph node
        value is replaced with the staged filename so ComfyUI can load it.
        """
        if self.dashboard_assets_dir is None:
            return graph, []

        staged: list[Path] = []
        staged_graph: dict[str, Any] | None = None
        cloned_inputs_by_node: dict[str, dict[str, Any]] = {}

        comfyui_input_dir = self.comfyui_input_dir / "staging"

        for node_id, node_def in graph.items():
            if not isinstance(node_def, dict):
                continue
            node_inputs = node_def.get("inputs", {})
            if not isinstance(node_inputs, dict):
                continue
            for input_name, value in list(node_inputs.items()):
                if not isinstance(value, str) or not _ASSET_ID_RE.match(value):
                    continue
                asset_path = self.dashboard_assets_dir / value
                if not asset_path.exists():
                    self.log_store.add(
                        "warning",
                        "Dashboard asset not found; skipping staging",
                        "comfyui.adapter",
                        job_id=job_id,
                        details={"asset_id": value, "node_id": node_id},
                    )
                    continue
                comfyui_input_dir.mkdir(parents=True, exist_ok=True)
                staged_name = f"{job_id}_{value}"
                staged_path = comfyui_input_dir / staged_name
                import shutil as _shutil

                _shutil.copy2(asset_path, staged_path)
                staged.append(staged_path)
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
                # ComfyUI expects filename relative to its input/ root
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
                    },
                )

        return staged_graph or graph, staged

    def _optional_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
