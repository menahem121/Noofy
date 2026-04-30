"""Runner selection and per-job routing.

Phase 2 of the runtime isolation plan introduces an explicit `RunnerSupervisor`
that owns the set of runner processes the backend can talk to and the mapping
between an in-flight job and the runner that accepted it. Today the supervisor
exposes the single core ComfyUI runner that already exists; later phases will
add isolated, per-capsule runner workspaces and use the same surface to switch
endpoints per workflow.

The runtime side intentionally stays minimal here: the supervisor does not start
or stop runner processes. That responsibility still belongs to `RuntimeManager`
for the core runner. The supervisor only tracks descriptors, adapters and the
job -> runner registry the engine service uses to route progress, cancel, and
result lookups.
"""

from __future__ import annotations

import threading
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.engine.adapter import EngineAdapter

CORE_RUNNER_ID = "core"
CORE_RUNNER_FINGERPRINT = "core"


class RunnerStatus(StrEnum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    UNREACHABLE = "unreachable"


class RunnerKind(StrEnum):
    CORE_COMFYUI = "core_comfyui"
    ISOLATED_COMFYUI = "isolated_comfyui"


class RunnerDescriptor(BaseModel):
    """Serializable view of a runner managed by the supervisor."""

    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(min_length=1)
    kind: RunnerKind
    base_url: str = Field(min_length=1)
    ws_url: str | None = None
    fingerprint: str = Field(min_length=1)
    status: RunnerStatus = RunnerStatus.UNKNOWN


class RunnerNotFoundError(LookupError):
    """Raised when a runner_id is not known to the supervisor."""


class JobRunnerNotFoundError(LookupError):
    """Raised when a job has not been routed to any runner."""


class DuplicateJobRegistrationError(RuntimeError):
    """Raised when a job id is registered more than once."""


class JobRunnerRegistry:
    """Maps job ids to the runner that accepted them.

    The engine service registers a job after submitting it to a runner, then
    looks the runner back up when progress, cancel, or result calls arrive on
    the API. Keeping this in its own object makes it easy to replace the
    in-memory implementation with persistent storage later.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_job: dict[str, str] = {}

    def register(self, job_id: str, runner_id: str) -> None:
        with self._lock:
            if job_id in self._by_job:
                raise DuplicateJobRegistrationError(f"Job is already registered: {job_id}")
            self._by_job[job_id] = runner_id

    def runner_for(self, job_id: str) -> str | None:
        with self._lock:
            return self._by_job.get(job_id)

    def unregister(self, job_id: str) -> None:
        with self._lock:
            self._by_job.pop(job_id, None)

    def snapshot(self) -> dict[str, str]:
        with self._lock:
            return dict(self._by_job)


class RunnerSupervisor:
    """Selects and tracks runner processes for the engine service.

    Phase 2 only exposes the core ComfyUI runner that already exists, but the
    surface (`acquire_runner`, `register_job`, `adapter_for_job`) is what later
    phases need to route a workflow at runtime to its own isolated runner.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._descriptors: dict[str, RunnerDescriptor] = {}
        self._adapters: dict[str, EngineAdapter] = {}
        self._core_runner_id: str | None = None
        self._workflow_runners: dict[str, str] = {}
        self._registry = JobRunnerRegistry()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_core_runner(self, descriptor: RunnerDescriptor, adapter: EngineAdapter) -> None:
        with self._lock:
            if self._core_runner_id is not None:
                raise RuntimeError("Core runner is already registered")
            if descriptor.kind is not RunnerKind.CORE_COMFYUI:
                raise ValueError("Core runner must use kind=core_comfyui")
            self._descriptors[descriptor.runner_id] = descriptor
            self._adapters[descriptor.runner_id] = adapter
            self._core_runner_id = descriptor.runner_id

    def upsert_runner(self, descriptor: RunnerDescriptor, adapter: EngineAdapter) -> None:
        """Register or replace a non-core runner descriptor and adapter."""
        if descriptor.kind is RunnerKind.CORE_COMFYUI:
            raise ValueError("Core runner must be registered with register_core_runner")
        with self._lock:
            if descriptor.runner_id == self._core_runner_id:
                raise ValueError("Cannot replace the core runner through upsert_runner")
            self._descriptors[descriptor.runner_id] = descriptor
            self._adapters[descriptor.runner_id] = adapter

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @property
    def job_registry(self) -> JobRunnerRegistry:
        return self._registry

    def list_runners(self) -> list[RunnerDescriptor]:
        with self._lock:
            return list(self._descriptors.values())

    def get_runner(self, runner_id: str) -> RunnerDescriptor:
        with self._lock:
            descriptor = self._descriptors.get(runner_id)
        if descriptor is None:
            raise RunnerNotFoundError(f"Unknown runner: {runner_id}")
        return descriptor

    def get_adapter(self, runner_id: str) -> EngineAdapter:
        with self._lock:
            adapter = self._adapters.get(runner_id)
        if adapter is None:
            raise RunnerNotFoundError(f"No adapter registered for runner: {runner_id}")
        return adapter

    def core_runner(self) -> RunnerDescriptor:
        with self._lock:
            runner_id = self._core_runner_id
        if runner_id is None:
            raise RunnerNotFoundError("Core runner has not been registered")
        return self.get_runner(runner_id)

    def acquire_runner(self, workflow_package: object) -> RunnerDescriptor:
        """Return the runner that should host `workflow_package`.

        A workflow can be explicitly bound to a ready runner. If no binding
        exists, or the bound runner is not ready, the core runner remains the
        conservative fallback so existing behavior stays unchanged.
        """
        workflow_id = self._workflow_id(workflow_package)
        if workflow_id is not None:
            bound_runner = self.runner_for_workflow(workflow_id)
            if bound_runner is not None and bound_runner.status is RunnerStatus.READY:
                return bound_runner
        return self.core_runner()

    def runner_for_workflow(self, workflow_id: str) -> RunnerDescriptor | None:
        with self._lock:
            runner_id = self._workflow_runners.get(workflow_id)
        if runner_id is None:
            return None
        return self.get_runner(runner_id)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def update_runner_endpoint(
        self,
        runner_id: str,
        base_url: str,
        ws_url: str | None = None,
    ) -> RunnerDescriptor:
        """Reconfigure a runner's endpoint (and its adapter) in lock-step."""
        descriptor = self.get_runner(runner_id)
        adapter = self.get_adapter(runner_id)
        new_descriptor = descriptor.model_copy(update={"base_url": base_url, "ws_url": ws_url})
        with self._lock:
            self._descriptors[runner_id] = new_descriptor
        adapter.configure_endpoint(base_url, ws_url)
        return new_descriptor

    def update_runner_status(self, runner_id: str, status: RunnerStatus) -> RunnerDescriptor:
        descriptor = self.get_runner(runner_id)
        new_descriptor = descriptor.model_copy(update={"status": status})
        with self._lock:
            self._descriptors[runner_id] = new_descriptor
        return new_descriptor

    def bind_workflow_runner(self, workflow_id: str, runner_id: str) -> RunnerDescriptor:
        descriptor = self.get_runner(runner_id)
        with self._lock:
            self._workflow_runners[workflow_id] = runner_id
        return descriptor

    def unbind_workflow_runner(self, workflow_id: str) -> None:
        with self._lock:
            self._workflow_runners.pop(workflow_id, None)

    def unbind_runner(self, runner_id: str) -> None:
        with self._lock:
            self._workflow_runners = {
                workflow_id: bound_runner_id
                for workflow_id, bound_runner_id in self._workflow_runners.items()
                if bound_runner_id != runner_id
            }

    # ------------------------------------------------------------------
    # Job routing
    # ------------------------------------------------------------------

    def register_job(self, job_id: str, runner_id: str) -> None:
        # Force a lookup so unknown runners surface immediately.
        self.get_runner(runner_id)
        self._registry.register(job_id, runner_id)

    def runner_for_job(self, job_id: str) -> RunnerDescriptor:
        runner_id = self._registry.runner_for(job_id)
        if runner_id is None:
            raise JobRunnerNotFoundError(f"No runner registered for job: {job_id}")
        return self.get_runner(runner_id)

    def adapter_for_job(self, job_id: str) -> EngineAdapter:
        descriptor = self.runner_for_job(job_id)
        return self.get_adapter(descriptor.runner_id)

    def forget_job(self, job_id: str) -> None:
        self._registry.unregister(job_id)

    @staticmethod
    def _workflow_id(workflow_package: object) -> str | None:
        metadata = getattr(workflow_package, "metadata", None)
        workflow_id = getattr(metadata, "id", None)
        return workflow_id if isinstance(workflow_id, str) else None
