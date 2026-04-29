import asyncio
from copy import deepcopy
from typing import Any

from app.core.config import settings
from app.engine.adapter import EngineAdapter
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.engine.models import BackendHealthReport, JobProgress, JobResult, ModelInfo, WorkflowHealthSummary, WorkflowValidationResult
from app.engine.process_manager import ComfyUIProcessManager
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.validator import WorkflowPackageValidator


class EngineService:
    def __init__(
        self,
        workflow_loader: WorkflowPackageLoader,
        workflow_validator: WorkflowPackageValidator,
        engine_adapter: EngineAdapter,
        process_manager: ComfyUIProcessManager,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.workflow_validator = workflow_validator
        self.engine_adapter = engine_adapter
        self.process_manager = process_manager

    def list_workflows(self) -> list[dict[str, str]]:
        return [
            {
                "id": package.metadata.id,
                "name": package.metadata.name,
                "version": package.metadata.version,
                "description": package.metadata.description,
            }
            for package in self.workflow_loader.list_packages()
        ]

    async def validate_workflow(self, workflow_id: str) -> WorkflowValidationResult:
        package = self.workflow_loader.get_package(workflow_id)
        return await self._validate_package(package)

    async def run_workflow(self, workflow_id: str, inputs: dict[str, Any], options: dict[str, Any]):
        package = self.workflow_loader.get_package(workflow_id)
        validation = await self._validate_package(package)
        if not validation.valid:
            return validation

        graph = self._apply_input_bindings(package, inputs)
        return await self.engine_adapter.run_workflow(package, graph, inputs, options)

    async def get_progress(self, job_id: str) -> JobProgress:
        return await self.engine_adapter.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        return await self.engine_adapter.cancel_job(job_id)

    async def get_result(self, job_id: str) -> JobResult:
        return await self.engine_adapter.get_result(job_id)

    async def stream_progress_events(self, job_id: str):
        while True:
            progress = await self.get_progress(job_id)
            yield f"event: progress\ndata: {progress.model_dump_json()}\n\n"

            if progress.status in {"completed", "failed", "canceled"}:
                result = await self.get_result(job_id)
                yield f"event: result\ndata: {result.model_dump_json()}\n\n"
                return

            await asyncio.sleep(1)

    async def list_available_models(self):
        return await self.engine_adapter.list_available_models()

    async def health(self) -> BackendHealthReport:
        packages = self.workflow_loader.list_packages()
        workflow_summaries: list[WorkflowHealthSummary] = []

        for package in packages:
            validation = await self._validate_package(package)
            workflow_summaries.append(
                WorkflowHealthSummary(
                    workflow_id=package.metadata.id,
                    valid=validation.valid,
                    missing_model_count=len(validation.missing_models),
                    error_count=len(validation.errors),
                )
            )

        comfyui_status = await self.process_manager.status()
        status = "ok" if comfyui_status.reachable and all(item.valid for item in workflow_summaries) else "degraded"

        return BackendHealthReport(
            status=status,
            comfyui=comfyui_status,
            workflow_package_count=len(packages),
            workflows=workflow_summaries,
        )

    async def start_comfyui(self):
        return await self.process_manager.start()

    async def stop_comfyui(self):
        return await self.process_manager.stop()

    async def _validate_package(self, package: WorkflowPackage) -> WorkflowValidationResult:
        structure_result = self.workflow_validator.validate_structure(package)
        if not structure_result.valid:
            return structure_result

        available_models = self._available_model_keys(await self.engine_adapter.list_available_models())
        missing_models = self.workflow_validator.validate_models(package, available_models)
        return self.workflow_validator.combine(package, structure_result, missing_models)

    def _available_model_keys(self, models: list[ModelInfo]) -> set[tuple[str, str]]:
        return {(model.folder, model.filename) for model in models}

    def _apply_input_bindings(self, package: WorkflowPackage, inputs: dict[str, Any]) -> dict[str, Any]:
        graph = deepcopy(package.comfyui_graph)
        for exposed_input in package.inputs:
            if exposed_input.id not in inputs:
                continue

            node_id = exposed_input.binding.node_id
            input_name = exposed_input.binding.input_name
            if node_id not in graph:
                raise ValueError(f"Input binding references unknown node: {node_id}")

            node_inputs = graph[node_id].setdefault("inputs", {})
            node_inputs[input_name] = inputs[exposed_input.id]
        return graph


def create_default_engine_service() -> EngineService:
    loader = WorkflowPackageLoader(settings.workflows_dir)
    validator = WorkflowPackageValidator()
    adapter = ComfyUIEngineAdapter(settings.comfyui_base_url, settings.comfyui_models_dir, settings.comfyui_ws_url)
    process_manager = ComfyUIProcessManager(
        base_url=settings.comfyui_base_url,
        repo_dir=settings.comfyui_repo_dir,
        python_executable=settings.comfyui_python_executable,
        host=settings.comfyui_host,
        port=settings.comfyui_port,
    )
    return EngineService(loader, validator, adapter, process_manager)
