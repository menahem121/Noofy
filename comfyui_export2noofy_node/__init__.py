from __future__ import annotations

import asyncio
import logging
import sys
import time
import uuid
from pathlib import Path
from typing import Any


WEB_DIRECTORY = "web"
NODE_CLASS_MAPPINGS: dict[str, Any] = {}
NODE_DISPLAY_NAME_MAPPINGS: dict[str, str] = {}
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

try:
    from aiohttp import web
    from server import PromptServer

    import execution
    import folder_paths
    import nodes
except ModuleNotFoundError as exc:
    if exc.name != "server":
        raise
    web = None
    PromptServer = None
    execution = None
    folder_paths = None
    nodes = None


if PromptServer is not None:
    from noofy_exporter import (  # noqa: E402
        build_export_filename,
        build_package_documents,
        collect_history_output_paths,
        collect_runtime_metadata,
        create_memory_sampler,
        create_thumbnail_bytes,
        detect_custom_nodes,
        detect_model_references,
        flatten_warnings,
        output_export_path,
        prepare_graph_for_export,
        write_noofy_package,
    )

    class NoofyExportError(Exception):
        def __init__(self, message: str, status: int = 400, details: Any | None = None) -> None:
            super().__init__(message)
            self.message = message
            self.status = status
            self.details = details

    def _comfyui_version() -> str:
        try:
            from comfyui_version import __version__

            return __version__
        except Exception:
            return "unknown"

    def _model_management() -> Any | None:
        try:
            import comfy.model_management as model_management

            return model_management
        except Exception:
            return None

    async def _sample_memory_until_stopped(sampler: Any, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            sampler.sample()
            await asyncio.sleep(0.5)
        sampler.sample()

    async def _run_prompt_once(
        *,
        prompt: dict[str, Any],
        workflow: dict[str, Any] | None,
        client_id: str | None,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        server = PromptServer.instance
        prompt_id = uuid.uuid4().hex
        validation = await execution.validate_prompt(prompt_id, prompt, None)
        if not validation[0]:
            raise NoofyExportError(
                "Noofy export test run could not start because workflow validation failed.",
                details={"error": validation[1], "node_errors": validation[3]},
            )

        extra_data: dict[str, Any] = {"extra_pnginfo": {}}
        if workflow is not None:
            extra_data["extra_pnginfo"]["workflow"] = workflow
        if client_id:
            extra_data["client_id"] = client_id

        queue_number = time.time()
        server.prompt_queue.put((queue_number, prompt_id, prompt, extra_data, validation[2], {}))

        deadline = time.monotonic() + timeout_seconds
        while True:
            history = server.prompt_queue.get_history(prompt_id)
            if history:
                prompt_history = history[prompt_id]
                status = prompt_history.get("status") or {}
                if status.get("status_str") != "success" or status.get("completed") is not True:
                    raise NoofyExportError(
                        "Noofy export test run failed. No package was created.",
                        details={"prompt_id": prompt_id, "status": status},
                    )
                return history

            if time.monotonic() > deadline:
                raise NoofyExportError(
                    "Noofy export timed out before the workflow finished. No package was created.",
                    status=504,
                    details={"prompt_id": prompt_id, "timeout_seconds": timeout_seconds},
                )

            await asyncio.sleep(0.5)

    @PromptServer.instance.routes.post("/noofy/export")
    async def export_to_noofy(request: web.Request) -> web.StreamResponse:
        started_at = time.monotonic()
        stop_event: asyncio.Event | None = None
        sampler_task: asyncio.Task[Any] | None = None

        try:
            body = await request.json()
            prompt = body.get("prompt")
            if not isinstance(prompt, dict):
                raise NoofyExportError("Request body must include a ComfyUI API prompt object.")

            workflow = body.get("workflow")
            if workflow is not None and not isinstance(workflow, dict):
                workflow = None

            workflow_name = body.get("workflow_name")
            if workflow_name is not None and not isinstance(workflow_name, str):
                workflow_name = None

            timeout_seconds = int(body.get("timeout_seconds") or 24 * 60 * 60)
            client_id = body.get("client_id")
            if client_id is not None and not isinstance(client_id, str):
                client_id = None

            started_at_iso = body.get("started_at")
            if not isinstance(started_at_iso, str):
                from noofy_exporter import utc_now_iso

                started_at_iso = utc_now_iso()

            graph, adjustments = prepare_graph_for_export(prompt)

            model_management = _model_management()
            runtime = collect_runtime_metadata(_comfyui_version(), model_management)
            sampler = create_memory_sampler(model_management)
            stop_event = asyncio.Event()
            sampler_task = asyncio.create_task(_sample_memory_until_stopped(sampler, stop_event))

            history = await _run_prompt_once(
                prompt=graph,
                workflow=workflow,
                client_id=client_id,
                timeout_seconds=timeout_seconds,
            )

            stop_event.set()
            await sampler_task
            sampler_task = None

            output_paths = collect_history_output_paths(
                history,
                lambda directory_type: folder_paths.get_directory_by_type(directory_type),
            )
            thumbnail_bytes = create_thumbnail_bytes(output_paths[0] if output_paths else None)

            custom_nodes = detect_custom_nodes(graph, nodes)
            models = detect_model_references(
                graph,
                lambda folder, filename: folder_paths.get_full_path(folder, filename),
            )
            duration_seconds = time.monotonic() - started_at

            from noofy_exporter import utc_now_iso

            finished_at_iso = utc_now_iso()
            warnings = flatten_warnings(custom_nodes, [])
            hardware = sampler.observation(runtime)
            documents = build_package_documents(
                graph=graph,
                workflow_name=workflow_name,
                runtime=runtime,
                custom_nodes=custom_nodes,
                models=models,
                hardware=hardware,
                started_at=started_at_iso,
                finished_at=finished_at_iso,
                duration_seconds=duration_seconds,
                graph_adjustments=adjustments,
                warnings=warnings,
            )
            filename = build_export_filename(documents["package_id"])
            target_path = output_export_path(folder_paths.get_output_directory(), filename)
            write_noofy_package(
                target_path=target_path,
                graph=graph,
                documents=documents,
                custom_nodes=custom_nodes,
                thumbnail_bytes=thumbnail_bytes,
            )

            headers = {
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Noofy-Export-Path": str(target_path),
            }
            return web.FileResponse(target_path, headers=headers)
        except NoofyExportError as exc:
            logging.warning("[Noofy Export] %s", exc.message)
            return web.json_response(
                {"error": exc.message, "details": exc.details},
                status=exc.status,
            )
        except Exception as exc:
            logging.exception("[Noofy Export] unexpected export failure")
            return web.json_response(
                {
                    "error": "Noofy export failed unexpectedly. No package was created.",
                    "details": str(exc),
                },
                status=500,
            )
        finally:
            if stop_event is not None and not stop_event.is_set():
                stop_event.set()
            if sampler_task is not None:
                try:
                    await sampler_task
                except Exception:
                    logging.debug("[Noofy Export] memory sampler cleanup failed", exc_info=True)
