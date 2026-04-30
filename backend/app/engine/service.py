import asyncio
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

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
from app.runtime.capsule_installer import CapsuleInstaller, CapsuleInstallError
from app.runtime.environment import RuntimeEnvironment
from app.runtime.install_state import (
    InstallStateStore,
    user_facing_install_message,
)
from app.runtime.isolation import (
    CapsuleLock,
    DependencyEnvManifest,
    InstallState,
    InstallStatus,
    RunnerWorkspaceManifest,
    SmokeTestStatus,
    TrustLevel,
)
from app.runtime.manager import RuntimeManager
from app.runtime.model_store import ModelStore, http_streaming_downloader
from app.runtime.runner_coordinator import RunnerProcessCoordinator, comfyui_adapter_factory
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.smoke_test import RunnerSmokeTester
from app.runtime.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    JobRunnerNotFoundError,
    RunnerDescriptor,
    RunnerKind,
    RunnerStatus,
    RunnerSupervisor,
)
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowPackage
from app.workflows.validator import WorkflowPackageValidator


class EngineService:
    def __init__(
        self,
        workflow_loader: WorkflowPackageLoader,
        workflow_validator: WorkflowPackageValidator,
        runner_supervisor: RunnerSupervisor,
        runtime_manager: RuntimeManager,
        log_store: LogStore,
        capsule_loader: CapsuleLockLoader | None = None,
        capsule_installer: CapsuleInstaller | None = None,
        runner_process_coordinator: RunnerProcessCoordinator | None = None,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.workflow_validator = workflow_validator
        self.runner_supervisor = runner_supervisor
        self.runtime_manager = runtime_manager
        self.log_store = log_store
        self.capsule_loader = capsule_loader
        self.capsule_installer = capsule_installer
        self.runner_process_coordinator = runner_process_coordinator

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

    def list_runners(self) -> list[RunnerDescriptor]:
        return self.runner_supervisor.list_runners()

    # ------------------------------------------------------------------
    # Capsule install pipeline (Phase 3)
    # ------------------------------------------------------------------

    def get_install_state(self, workflow_id: str) -> dict[str, object]:
        """Return the user-facing install state for a workflow.

        Workflows that ship a Noofy Verified capsule lock surface an
        InstallState record; workflows without a lock return an
        unsupported-shaped payload so the UI can render gracefully.
        """
        if self.capsule_loader is None or self.capsule_installer is None:
            return self._unsupported_install_payload(workflow_id)
        capsule_lock = self._phase3_verified_capsule_lock(workflow_id)
        if capsule_lock is None:
            return self._unsupported_install_payload(workflow_id)

        state = self.capsule_installer.get_state(capsule_lock)
        return self._install_payload(workflow_id, state)

    async def prepare_workflow(self, workflow_id: str) -> dict[str, object]:
        if self.capsule_loader is None or self.capsule_installer is None:
            self.log_store.add(
                "warning",
                "Workflow prepare requested but capsule installer is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_install_payload(workflow_id)

        capsule_lock = self._phase3_verified_capsule_lock(workflow_id)
        if capsule_lock is None:
            self.log_store.add(
                "warning",
                "Workflow prepare requested but no verified bundled capsule is available",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_install_payload(workflow_id)

        try:
            state = await self.capsule_installer.prepare(capsule_lock)
        except CapsuleInstallError as exc:
            self.log_store.add(
                "error",
                "Capsule preparation failed",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                },
            )
            return self._install_payload(workflow_id, exc.state)
        return self._install_payload(workflow_id, state)

    async def start_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        """Start and bind an isolated runner for a prepared verified workflow."""
        if self.runner_process_coordinator is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but runner coordinator is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "runner_coordinator_not_configured")
        if self.capsule_installer is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but capsule installer is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "capsule_installer_not_configured")

        capsule_lock = self._phase3_verified_capsule_lock(workflow_id)
        if capsule_lock is None:
            self.log_store.add(
                "warning",
                "Workflow runner start requested but no verified bundled capsule is available",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "verified_capsule_not_available")

        install_state = self.capsule_installer.get_state(capsule_lock)
        if install_state.status is not InstallStatus.READY:
            self.log_store.add(
                "warning",
                "Workflow runner start blocked because workflow is not ready",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "install_status": install_state.status.value,
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": "install_not_ready",
                "runner": None,
                "pid": None,
                "install_status": install_state.status.value,
                "error": install_state.last_error,
            }

        try:
            spec = self._runner_launch_spec(capsule_lock, install_state)
        except ValueError as exc:
            self.log_store.add(
                "error",
                "Workflow runner start blocked by missing runtime artifacts",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runner": None,
                "pid": None,
                "install_status": install_state.status.value,
                "error": str(exc),
            }
        try:
            handle = await self.runner_process_coordinator.start_runner(spec, workflow_id=workflow_id)
        except Exception as exc:
            self.log_store.add(
                "error",
                "Workflow runner start failed",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "runner_id": spec.runner_id,
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                },
            )
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runner": None,
                "pid": None,
                "install_status": InstallStatus.READY.value,
                "error": str(exc),
            }

        self.log_store.add(
            "info",
            "Workflow runner started and bound",
            "engine.service",
            workflow_id=workflow_id,
            details={
                "runner_id": handle.runner_id,
                "base_url": handle.descriptor.base_url,
                "fingerprint": handle.descriptor.fingerprint,
            },
        )
        return {
            "workflow_id": workflow_id,
            "status": handle.descriptor.status.value,
            "runner": handle.descriptor.model_dump(),
            "pid": handle.pid,
            "install_status": InstallStatus.READY.value,
            "error": None,
        }

    async def stop_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        """Stop the isolated runner currently bound to a workflow."""
        if self.runner_process_coordinator is None:
            self.log_store.add(
                "warning",
                "Workflow runner stop requested but runner coordinator is not configured",
                "engine.service",
                workflow_id=workflow_id,
            )
            return self._unsupported_runner_payload(workflow_id, "runner_coordinator_not_configured")

        descriptor = self.runner_supervisor.runner_for_workflow(workflow_id)
        if descriptor is None:
            return {
                "workflow_id": workflow_id,
                "status": "not_running",
                "runner": None,
                "pid": None,
                "error": None,
            }
        if descriptor.kind is RunnerKind.CORE_COMFYUI:
            self.log_store.add(
                "warning",
                "Refusing to stop core runner through workflow runner endpoint",
                "engine.service",
                workflow_id=workflow_id,
                details={"runner_id": descriptor.runner_id},
            )
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runner": descriptor.model_dump(),
                "pid": None,
                "error": "workflow is bound to the core runner",
            }

        status = await self.runner_process_coordinator.stop_runner(descriptor.runner_id)
        self.runner_supervisor.unbind_workflow_runner(workflow_id)
        self.log_store.add(
            "info",
            "Workflow runner stopped and unbound",
            "engine.service",
            workflow_id=workflow_id,
            details={"runner_id": descriptor.runner_id, "status": status.status.value},
        )
        return {
            "workflow_id": workflow_id,
            "status": status.status.value,
            "runner": {
                "runner_id": status.runner_id,
                "kind": descriptor.kind.value,
                "base_url": status.base_url,
                "ws_url": status.ws_url,
                "fingerprint": descriptor.fingerprint,
                "status": status.status.value,
            },
            "pid": status.pid,
            "error": status.error,
        }

    async def validate_workflow(self, workflow_id: str) -> WorkflowValidationResult:
        package = self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)
        validation = await self._validate_package(package, adapter)
        if validation.valid:
            self.log_store.add(
                "info",
                "Workflow validation passed",
                "engine.service",
                workflow_id=workflow_id,
                details={"runner_id": runner.runner_id},
            )
        else:
            self.log_store.add(
                "warning",
                "Workflow validation failed",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "runner_id": runner.runner_id,
                    "missing_models": [model.model_dump() for model in validation.missing_models],
                    "errors": validation.errors,
                },
            )
        return validation

    async def run_workflow(self, workflow_id: str, inputs: dict[str, Any], options: dict[str, Any]):
        package = self.workflow_loader.get_package(workflow_id)
        runner = self.runner_supervisor.acquire_runner(package)
        adapter = self.runner_supervisor.get_adapter(runner.runner_id)

        validation = await self._validate_package(package, adapter)
        if not validation.valid:
            self.log_store.add(
                "warning",
                "Workflow run blocked by validation failure",
                "engine.service",
                workflow_id=workflow_id,
                details={
                    "runner_id": runner.runner_id,
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
            details={"runner_id": runner.runner_id, "input_keys": sorted(inputs.keys())},
        )
        job = await adapter.run_workflow(package, graph, inputs, options)
        self.runner_supervisor.register_job(job.job_id, runner.runner_id)
        self.log_store.add(
            "info",
            "Workflow run queued",
            "engine.service",
            job_id=job.job_id,
            workflow_id=workflow_id,
            details={"runner_id": runner.runner_id},
        )
        return job

    async def get_progress(self, job_id: str) -> JobProgress:
        adapter = self._adapter_for_job(job_id)
        return await adapter.get_progress(job_id)

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.log_store.add("info", "Cancel requested", "engine.service", job_id=job_id)
        adapter = self._adapter_for_job(job_id)
        return await adapter.cancel_job(job_id)

    async def get_result(self, job_id: str) -> JobResult:
        adapter = self._adapter_for_job(job_id)
        return await adapter.get_result(job_id)

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
        adapter = self._core_adapter()
        return await adapter.list_available_models()

    def list_logs(self, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.log_store.list_events(level=level, limit=limit)

    def list_job_logs(self, job_id: str, *, level: LogLevel | None = None, limit: int = 200) -> DiagnosticLogResponse:
        return self.log_store.list_events(job_id=job_id, level=level, limit=limit)

    async def health(self) -> BackendHealthReport:
        packages = self.workflow_loader.list_packages()
        workflow_summaries: list[WorkflowHealthSummary] = []

        for package in packages:
            runner = self.runner_supervisor.acquire_runner(package)
            adapter = self.runner_supervisor.get_adapter(runner.runner_id)
            validation = await self._validate_package(package, adapter)
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
        self._reconfigure_core_runner_endpoint()
        return result

    async def stop_comfyui(self):
        return await self.runtime_manager.stop()

    async def bootstrap_comfyui_runtime(self) -> RuntimeBootstrapResult:
        return await self.runtime_manager.bootstrap_environment()

    async def shutdown(self) -> None:
        if self.runner_process_coordinator is not None:
            await self.runner_process_coordinator.stop_all_runners()
        if self.runtime_manager.mode == "managed":
            await self.runtime_manager.stop()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _validate_package(
        self,
        package: WorkflowPackage,
        adapter: EngineAdapter,
    ) -> WorkflowValidationResult:
        structure_result = self.workflow_validator.validate_structure(package)
        if not structure_result.valid:
            return structure_result

        available_models = self._available_model_keys(await adapter.list_available_models())
        missing_models = self.workflow_validator.validate_models(package, available_models)
        return self.workflow_validator.combine(package, structure_result, missing_models)

    def _install_payload(self, workflow_id: str, state: InstallState) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "capsule_fingerprint": state.capsule_fingerprint,
            "status": state.status.value,
            "user_facing_message": user_facing_install_message(state.status),
            "installed_at": state.installed_at,
            "last_used_at": state.last_used_at,
            "dependency_env_path": state.dependency_env_path,
            "runner_workspace_path": state.runner_workspace_path,
            "smoke_test_status": state.smoke_test_status.value,
            "last_error": state.last_error,
        }

    def _phase3_verified_capsule_lock(self, workflow_id: str) -> CapsuleLock | None:
        if self.capsule_loader is None:
            return None
        try:
            capsule_lock = self.capsule_loader.get_bundled_capsule_lock(workflow_id)
        except KeyError:
            return None
        if capsule_lock.workflow.package_id != workflow_id:
            return None
        if capsule_lock.workflow.trust_level is not TrustLevel.NOOFY_VERIFIED:
            return None
        if capsule_lock.trust.level is not TrustLevel.NOOFY_VERIFIED:
            return None
        if capsule_lock.custom_nodes:
            return None
        return capsule_lock

    def _unsupported_install_payload(self, workflow_id: str) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "capsule_fingerprint": None,
            "status": InstallStatus.UNSUPPORTED.value,
            "user_facing_message": user_facing_install_message(InstallStatus.UNSUPPORTED),
            "installed_at": None,
            "last_used_at": None,
            "dependency_env_path": None,
            "runner_workspace_path": None,
            "smoke_test_status": "not_run",
            "last_error": None,
        }

    def _unsupported_runner_payload(self, workflow_id: str, reason: str) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "status": "unsupported",
            "runner": None,
            "pid": None,
            "install_status": InstallStatus.UNSUPPORTED.value,
            "error": reason,
        }

    def _adapter_for_job(self, job_id: str) -> EngineAdapter:
        try:
            return self.runner_supervisor.adapter_for_job(job_id)
        except JobRunnerNotFoundError:
            # The job was either submitted before the registry existed or the
            # registry was reset. Fall back to the core runner so existing API
            # responses keep working while later phases tighten this contract.
            return self._core_adapter()

    def _core_adapter(self) -> EngineAdapter:
        descriptor = self.runner_supervisor.core_runner()
        return self.runner_supervisor.get_adapter(descriptor.runner_id)

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

    def _reconfigure_core_runner_endpoint(self) -> None:
        try:
            descriptor = self.runner_supervisor.core_runner()
        except LookupError:
            return
        self.runner_supervisor.update_runner_endpoint(
            descriptor.runner_id,
            self.runtime_manager.base_url,
            self.runtime_manager.ws_url,
        )

    def _runner_launch_spec(self, capsule_lock: CapsuleLock, install_state: InstallState) -> RunnerLaunchSpec:
        dependency_env_path, runner_workspace_path = self._prepared_runtime_paths(install_state, capsule_lock)
        if install_state.smoke_test_status is not SmokeTestStatus.PASSED:
            raise ValueError(
                "Prepared runtime smoke test has not passed: "
                f"{install_state.smoke_test_status.value}"
            )
        return _workflow_runner_launch_spec(
            capsule_lock,
            dependency_env_path=dependency_env_path,
            runner_workspace_path=runner_workspace_path,
            runtime_manager=self.runtime_manager,
        )

    def _prepared_runtime_paths(self, install_state: InstallState, capsule_lock: CapsuleLock) -> tuple[Path, Path]:
        if not install_state.dependency_env_path or not install_state.runner_workspace_path:
            raise ValueError("Prepared runtime artifact paths are missing; prepare the workflow again.")

        dependency_env_path = Path(install_state.dependency_env_path)
        runner_workspace_path = Path(install_state.runner_workspace_path)
        missing: list[str] = []
        if not (dependency_env_path / "manifest.json").exists():
            missing.append("dependency environment manifest")
        if not (runner_workspace_path / "manifest.json").exists():
            missing.append("runner workspace manifest")
        if not (runner_workspace_path / "main.py").exists():
            missing.append("runner workspace entrypoint")
        if missing:
            raise ValueError(f"Prepared runtime artifact is missing: {', '.join(missing)}")

        dependency_manifest = _read_dependency_manifest(dependency_env_path / "manifest.json")
        runner_manifest = _read_runner_workspace_manifest(runner_workspace_path / "manifest.json")
        not_ready: list[str] = []
        if dependency_manifest.status is not InstallStatus.READY:
            not_ready.append(f"dependency environment manifest status {dependency_manifest.status.value}")
        if runner_manifest.status is not InstallStatus.READY:
            not_ready.append(f"runner workspace manifest status {runner_manifest.status.value}")
        if dependency_manifest.fingerprint != capsule_lock.runtime.dependency_env_fingerprint:
            not_ready.append("dependency environment manifest fingerprint mismatch")
        if runner_manifest.fingerprint != capsule_lock.runtime.runner_fingerprint:
            not_ready.append("runner workspace manifest fingerprint mismatch")
        if runner_manifest.dependency_env_fingerprint != dependency_manifest.fingerprint:
            not_ready.append("runner workspace dependency environment mismatch")
        if not_ready:
            raise ValueError(f"Prepared runtime artifact is not ready: {', '.join(not_ready)}")
        return dependency_env_path, runner_workspace_path

    @staticmethod
    def _runner_id_for_capsule(capsule_lock: CapsuleLock) -> str:
        raw = capsule_lock.runtime.runner_fingerprint
        safe = "".join(char if char.isalnum() else "-" for char in raw.lower()).strip("-")
        return f"workflow-{capsule_lock.workflow.package_id}-{safe}"


def _workflow_runner_launch_spec(
    capsule_lock: CapsuleLock,
    *,
    dependency_env_path: Path,
    runner_workspace_path: Path,
    runtime_manager: RuntimeManager,
    runner_id_suffix: str | None = None,
) -> RunnerLaunchSpec:
    runner_id = EngineService._runner_id_for_capsule(capsule_lock)
    if runner_id_suffix:
        runner_id = f"{runner_id}-{runner_id_suffix}"
    return RunnerLaunchSpec(
        runner_id=runner_id,
        kind=RunnerKind.ISOLATED_COMFYUI,
        fingerprint=capsule_lock.runtime.runner_fingerprint,
        python_executable=_runtime_python_executable(runtime_manager),
        working_dir=runner_workspace_path,
        dependency_env_path=dependency_env_path,
        runner_workspace_path=runner_workspace_path,
        host=runtime_manager.managed_host,
        extra_args=[
            "--base-directory",
            str(runner_workspace_path),
            "--disable-auto-launch",
            "--disable-all-custom-nodes",
        ],
        env={
            "NOOFY_CAPSULE_FINGERPRINT": capsule_lock.runtime.capsule_fingerprint,
            "NOOFY_DEPENDENCY_ENV_PATH": str(dependency_env_path),
            "NOOFY_RUNNER_WORKSPACE_PATH": str(runner_workspace_path),
            "NOOFY_WORKFLOW_ID": capsule_lock.workflow.package_id,
        },
    )


def _runtime_python_executable(runtime_manager: RuntimeManager) -> str:
    environment = getattr(runtime_manager, "environment", None)
    if environment is not None:
        return environment.python_executable
    return runtime_manager.python_executable


def _read_dependency_manifest(path: Path) -> DependencyEnvManifest:
    try:
        return DependencyEnvManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is unreadable: {path}") from exc
    except ValidationError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is invalid: {path}") from exc


def _read_runner_workspace_manifest(path: Path) -> RunnerWorkspaceManifest:
    try:
        return RunnerWorkspaceManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is unreadable: {path}") from exc
    except ValidationError as exc:
        raise ValueError(f"Prepared runtime artifact manifest is invalid: {path}") from exc


def create_default_engine_service() -> EngineService:
    paths = settings.paths
    paths.ensure_directories()

    loader = WorkflowPackageLoader(
        settings.workflows_dir,
        user_packages_dir=paths.user_workflows_dir,
    )
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
        logs_dir=paths.logs_dir,
        cache_dir=paths.cache_dir,
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
        max_restart_attempts=settings.comfyui_max_restart_attempts,
        restart_backoff_base_seconds=settings.comfyui_restart_backoff_base,
        log_store=log_store,
        environment=runtime_environment,
        pid_dir=paths.runtime_dir,
    )
    adapter = ComfyUIEngineAdapter(
        runtime_manager.base_url,
        settings.comfyui_models_dir,
        runtime_manager.ws_url,
        log_store=log_store,
    )

    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url=runtime_manager.base_url,
            ws_url=runtime_manager.ws_url,
            fingerprint=CORE_RUNNER_FINGERPRINT,
            status=RunnerStatus.UNKNOWN,
        ),
        adapter,
    )

    capsule_loader = CapsuleLockLoader(
        settings.workflows_dir,
        user_packages_dir=paths.user_workflows_dir,
    )
    install_state_store = InstallStateStore(paths.workflow_store_dir / "install-state")
    model_store = ModelStore(
        blobs_dir=paths.model_blobs_dir,
        refs_dir=paths.model_refs_dir,
        materialized_dir=paths.model_materialized_dir,
        transactions_dir=paths.install_transactions_dir,
        log_store=log_store,
        downloader=http_streaming_downloader,
    )
    runner_process_supervisor = RunnerProcessSupervisor(
        log_store=log_store,
        startup_timeout_seconds=settings.comfyui_startup_timeout_seconds,
        health_poll_interval_seconds=settings.comfyui_health_poll_interval_seconds,
    )
    runner_smoke_tester = RunnerSmokeTester(
        process_supervisor=runner_process_supervisor,
        launch_spec_factory=lambda capsule_lock, prepared_workspace: _workflow_runner_launch_spec(
            capsule_lock,
            dependency_env_path=prepared_workspace.dependency_env_path,
            runner_workspace_path=prepared_workspace.runner_workspace_path,
            runtime_manager=runtime_manager,
            runner_id_suffix="smoke",
        ),
        log_store=log_store,
    )
    capsule_installer = CapsuleInstaller(
        install_state_store=install_state_store,
        model_store=model_store,
        workspace_preparer=RuntimeWorkspacePreparer(
            dependency_env_store=DependencyEnvManifestStore(paths.dependency_envs_dir),
            runner_workspace_store=RunnerWorkspaceManifestStore(paths.runner_workspaces_dir),
            comfyui_source_dir=settings.comfyui_repo_dir,
            model_view_dir=paths.model_materialized_dir,
            log_store=log_store,
        ),
        workspace_smoke_test=runner_smoke_tester.run,
        log_store=log_store,
    )
    runner_process_coordinator = RunnerProcessCoordinator(
        runner_supervisor=supervisor,
        process_supervisor=runner_process_supervisor,
        adapter_factory=comfyui_adapter_factory(
            models_dir=settings.comfyui_models_dir,
            log_store=log_store,
        ),
        log_store=log_store,
    )

    # Wire the on_restart callback so the supervisor (and its adapter) learn
    # the new URL after a crash-restart that picked a new port.
    def _reconfigure_adapter() -> None:
        supervisor.update_runner_endpoint(
            CORE_RUNNER_ID,
            runtime_manager.base_url,
            runtime_manager.ws_url,
        )

    runtime_manager._on_restart = _reconfigure_adapter

    log_store.add(
        "info",
        "Backend engine service initialized",
        "engine.service",
        details={
            "runtime_mode": runtime_manager.mode,
            "data_dir": str(paths.data_dir),
            "core_runner_id": CORE_RUNNER_ID,
        },
    )
    return EngineService(
        loader,
        validator,
        supervisor,
        runtime_manager,
        log_store,
        capsule_loader=capsule_loader,
        capsule_installer=capsule_installer,
        runner_process_coordinator=runner_process_coordinator,
    )
