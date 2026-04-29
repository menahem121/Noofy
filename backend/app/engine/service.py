import asyncio
from copy import deepcopy
from typing import Any

from app.core.config import settings
from app.engine.adapter import EngineAdapter
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.engine.diagnostics import LogStore
from app.engine.models import (
    BackendHealthReport,
    DiagnosticLogResponse,
    JobProgress,
    JobResult,
    LogLevel,
    ModelInfo,
    RuntimeBootstrapResult,
    WorkflowHealthSummary,
    WorkflowValidationResult,
)
from app.runtime.environment import RuntimeEnvironment
from app.runtime.manager import RuntimeManager
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.validator import WorkflowPackageValidator


class EngineService:
    def __init__(
        self,
        workflow_loader: WorkflowPackageLoader,
        workflow_validator: WorkflowPackageValidator,
        engine_adapter: EngineAdapter,
        runtime_manager: RuntimeManager,
        log_store: LogStore,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.workflow_validator = workflow_validator
        self.engine_adapter = engine_adapter
        self.runtime_manager = runtime_manager
        self.log_store = log_store

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
        validation = await self._validate_package(package)
        if validation.valid:
            self.log_store.add("info", "Workflow validation passed", "engine.service", workflow_id=workflow_id)
        else:
            self.log_store.add(
                "warning",
                "Workflow validation failed",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "missing_models": [model.model_dump() for model in validation.missing_models],
                    "errors": validation.errors,
                },
            )
        return validation

    async def run_workflow(self, workflow_id: str, inputs: dict[str, Any], options: dict[str, Any]):
        package = self.workflow_loader.get_package(workflow_id)
        validation = await self._validate_package(package)
        if not validation.valid:
            self.log_store.add(
                "warning",
                "Workflow run blocked by validation failure",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "missing_models": [model.model_dump() for model in validation.missing_models],
                    "errors": validation.errors,
                },
            )
            return validation

        graph = self._apply_input_bindings(package, inputs)
        self.log_store.add(
            "info",
            "Submitting workflow run",
            "engine.service",
            workflow_id=workflow_id,
            details={"input_keys": sorted(inputs.keys())},
        )
        job = await self.engine_adapter.run_workflow(package, graph, inputs, options)
        self.log_store.add(
            "info",
            "Workflow run queued",
            "engine.service",
            job_id=job.job_id,
            workflow_id=workflow_id,
        )
        return job

    async def get_progress(self, job_id: str) -> JobProgress:
        return await self.engine_adapter.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.log_store.add("info", "Cancel requested", "engine.service", job_id=job_id)
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

    def list_logs(self, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.log_store.list_events(level=level, limit=limit)

    def list_job_logs(self, job_id: str, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.log_store.list_events(job_id=job_id, level=level, limit=limit)

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

        comfyui_status = await self.runtime_manager.status()
        status = "ok" if comfyui_status.reachable and all(item.valid for item in workflow_summaries) else "degraded"

        return BackendHealthReport(
            status=status,
            comfyui=comfyui_status,
            workflow_package_count=len(packages),
            workflows=workflow_summaries,
            latest_error=self.log_store.latest_error(),
        )

    async def runtime_status(self):
        return await self.runtime_manager.status()

    async def start_comfyui(self):
        result = await self.runtime_manager.start()
        self._configure_adapter_endpoint()
        return result

    async def stop_comfyui(self):
        return await self.runtime_manager.stop()

    async def bootstrap_comfyui_runtime(self) -> RuntimeBootstrapResult:
        return await self.runtime_manager.bootstrap_environment()

    async def shutdown(self) -> None:
        if self.runtime_manager.mode == "managed":
            await self.runtime_manager.stop()

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

    def _configure_adapter_endpoint(self) -> None:
        configure_endpoint = getattr(self.engine_adapter, "configure_endpoint", None)
        if configure_endpoint is not None:
            configure_endpoint(self.runtime_manager.base_url, self.runtime_manager.ws_url)


def create_default_engine_service() -> EngineService:
    loader = WorkflowPackageLoader(settings.workflows_dir)
    validator = WorkflowPackageValidator()
    log_store = LogStore()
    runtime_environment = RuntimeEnvironment(
        repo_dir=settings.comfyui_repo_dir,
        runtime_dir=settings.runtime_dir,
        bootstrap_python_executable=settings.comfyui_bootstrap_python_executable,
        python_executable_override=settings.comfyui_python_executable,
        torch_cuda_index_url=settings.comfyui_torch_cuda_index_url,
        torch_cpu_index_url=settings.comfyui_torch_cpu_index_url,
        log_store=log_store,
    )
    runtime_manager = RuntimeManager(
        mode=settings.comfyui_runtime_mode,
        external_base_url=settings.comfyui_base_url,
        external_ws_url=settings.comfyui_ws_url,
        repo_dir=settings.comfyui_repo_dir,
        python_executable=runtime_environment.python_executable,
        managed_host=settings.comfyui_managed_host,
        managed_port=settings.comfyui_managed_port,
        startup_timeout_seconds=settings.comfyui_startup_timeout_seconds,
        health_poll_interval_seconds=settings.comfyui_health_poll_interval_seconds,
        log_store=log_store,
        environment=runtime_environment,
    )
    adapter = ComfyUIEngineAdapter(
        runtime_manager.base_url,
        settings.comfyui_models_dir,
        runtime_manager.ws_url,
        log_store=log_store,
    )
    log_store.add(
        "info",
        "Backend engine service initialized",
        "engine.service",
        details={"runtime_mode": runtime_manager.mode},
    )
    return EngineService(loader, validator, adapter, runtime_manager, log_store)
