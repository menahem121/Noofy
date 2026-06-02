import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from datetime import UTC, datetime

import pytest

from app.diagnostics import LogStore
from app.engine.memory_observation import memory_input_profile_fingerprint
from app.engine.models import EngineJob, JobProgress, JobResult, ModelInfo
from app.engine.service import EngineService
from app.runtime.memory import service as memory_service_module
from app.runtime.memory.memory_governor import (
    BackendAllocatorMemorySample,
    GpuProcessMemorySample,
    LocalMemoryLearningStore,
    MachineMemorySnapshot,
    MemoryAttributionQuality,
    MemoryBackend,
    MemoryDecisionAction,
    MemoryGovernorDecision,
    MemoryPressureLevel,
    MemoryReleaseStatus,
    MemorySampleWindow,
    ProcessTreeMemorySample,
    WorkflowMemoryEstimate,
)
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    DuplicateJobRegistrationError,
    JobRunnerNotFoundError,
    JobRunnerRegistry,
    QueuedRunnerStartKind,
    QueuedRunnerStartStatus,
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    RunnerMemoryEstimateConfidence,
    RunnerMemoryEstimateSource,
    RunnerNotFoundError,
    RunnerSelectionAction,
    RunnerStatus,
    RunnerSupervisor,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.package import WorkflowCustomNodeRecord, WorkflowInput
from app.workflows.validator import WorkflowPackageValidator

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "app/workflows/packages"


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class RecordingAdapter:
    """In-memory adapter that records routed calls per job_id."""

    def __init__(self, models: list[ModelInfo] | None = None, *, next_job_id: str = "job-1") -> None:
        self.models = models or []
        self.endpoint_updates: list[tuple[str, str | None]] = []
        self.run_calls: list[tuple[str, dict[str, Any]]] = []
        self.progress_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.result_calls: list[str] = []
        self.upload_calls: list[tuple[str, str, bytes, str]] = []
        self.fetch_output_calls: list[tuple[str, str, str, str]] = []
        self.release_memory_calls = 0
        self._next_job_id = next_job_id

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        self.endpoint_updates.append((base_url, ws_url))

    async def list_available_models(self) -> list[ModelInfo]:
        return self.models

    async def release_memory(self) -> None:
        self.release_memory_calls += 1

    async def run_workflow(self, workflow_package, graph, inputs, options) -> EngineJob:
        del graph, options
        job_id = self._next_job_id
        self.run_calls.append((job_id, dict(inputs)))
        return EngineJob(
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )

    async def get_progress(self, job_id: str) -> JobProgress:
        self.progress_calls.append(job_id)
        return JobProgress(job_id=job_id, status="running")

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.cancel_calls.append(job_id)
        return JobProgress(job_id=job_id, status="canceled")

    async def get_result(self, job_id: str) -> JobResult:
        self.result_calls.append(job_id)
        return JobResult(job_id=job_id, status="completed")

    async def upload_workflow_image(
        self, workflow_package, filename: str, data: bytes, content_type: str
    ) -> dict[str, str]:
        self.upload_calls.append(
            (workflow_package.metadata.id, filename, data, content_type)
        )
        return {"filename": f"uploaded-{filename}"}

    async def fetch_output(
        self,
        job_id: str,
        filename: str,
        subfolder: str,
        output_type: str,
    ) -> tuple[bytes, str]:
        self.fetch_output_calls.append((job_id, filename, subfolder, output_type))
        return b"output-bytes", "image/png"


class MemoryRetryAdapter(RecordingAdapter):
    def __init__(self, models: list[ModelInfo]) -> None:
        super().__init__(models=models)
        self._job_counter = 0

    async def run_workflow(self, workflow_package, graph, inputs, options) -> EngineJob:
        del graph, options
        self._job_counter += 1
        job_id = f"job-{self._job_counter}"
        self.run_calls.append((job_id, dict(inputs)))
        return EngineJob(
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )

    async def get_result(self, job_id: str) -> JobResult:
        self.result_calls.append(job_id)
        return JobResult(job_id=job_id, status="failed", error="CUDA out of memory")


class SuccessfulIncrementingAdapter(RecordingAdapter):
    def __init__(self, models: list[ModelInfo]) -> None:
        super().__init__(models=models)
        self._job_counter = 0

    async def run_workflow(self, workflow_package, graph, inputs, options) -> EngineJob:
        del graph, options
        self._job_counter += 1
        job_id = f"job-{self._job_counter}"
        self.run_calls.append((job_id, dict(inputs)))
        return EngineJob(
            job_id=job_id,
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )


class BlockingSubmissionAdapter(RecordingAdapter):
    def __init__(self, models: list[ModelInfo], *, next_job_id: str) -> None:
        super().__init__(models=models, next_job_id=next_job_id)
        self.submission_started = asyncio.Event()
        self.allow_submission = asyncio.Event()

    async def run_workflow(self, workflow_package, graph, inputs, options) -> EngineJob:
        del graph, options
        self.submission_started.set()
        await self.allow_submission.wait()
        self.run_calls.append((self._next_job_id, dict(inputs)))
        return EngineJob(
            job_id=self._next_job_id,
            workflow_id=workflow_package.metadata.id,
            engine="comfyui",
            status="queued",
        )


class FailingGalleryCapture:
    def register_completed_run(self, *args, **kwargs) -> None:
        return None

    def schedule_auto_saves(self, *args, **kwargs) -> None:
        raise RuntimeError("gallery disk unavailable")


def _core_descriptor() -> RunnerDescriptor:
    return RunnerDescriptor(
        runner_id=CORE_RUNNER_ID,
        kind=RunnerKind.CORE_COMFYUI,
        base_url="http://127.0.0.1:8188",
        ws_url="ws://127.0.0.1:8188/ws",
        fingerprint=CORE_RUNNER_FINGERPRINT,
    )


def _isolated_descriptor(
    runner_id: str = "isolated-1",
    *,
    compatibility_key: str = "runner-key-a",
    status: RunnerStatus = RunnerStatus.READY,
    memory_class: RunnerMemoryClass = RunnerMemoryClass.GPU_HEAVY,
    current_job_id: str | None = None,
    last_used_at: str | None = None,
) -> RunnerDescriptor:
    return RunnerDescriptor(
        runner_id=runner_id,
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=status,
        runner_process_compatibility_key=compatibility_key,
        memory_class=memory_class,
        current_job_id=current_job_id,
        last_used_at=last_used_at,
    )


class AutoPrepareLifecycle:
    def __init__(self, supervisor: RunnerSupervisor, adapter: RecordingAdapter) -> None:
        self.supervisor = supervisor
        self.adapter = adapter
        self.install_status = "pending"
        self.prepare_calls: list[str] = []
        self.start_calls: list[str] = []

    def preparable_capsule_lock(self, workflow_id: str):
        return SimpleNamespace(
            custom_nodes=[SimpleNamespace(package_id="custom-node")],
            runtime=SimpleNamespace(capsule_fingerprint=f"{workflow_id}-fp"),
        )

    def imported_workflow_without_preparable_capsule(self, package) -> str | None:
        del package
        return None

    def get_install_state(self, workflow_id: str) -> dict[str, object]:
        return {
            "workflow_id": workflow_id,
            "status": self.install_status,
            "user_facing_message": "Not started",
        }

    async def prepare_workflow(self, workflow_id: str) -> dict[str, object]:
        self.prepare_calls.append(workflow_id)
        self.install_status = "ready"
        return {"workflow_id": workflow_id, "status": "ready"}

    async def start_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        self.start_calls.append(workflow_id)
        descriptor = _isolated_descriptor(status=RunnerStatus.READY)
        self.supervisor.upsert_runner(descriptor, self.adapter)
        self.supervisor.bind_workflow_runner(workflow_id, descriptor.runner_id)
        return {
            "workflow_id": workflow_id,
            "status": "ready",
            "runner": descriptor.model_dump(mode="json"),
            "pid": descriptor.pid,
            "install_status": "ready",
            "error": None,
        }


class QueuedAutoPrepareLifecycle(AutoPrepareLifecycle):
    def __init__(self, supervisor: RunnerSupervisor, adapter: RecordingAdapter) -> None:
        super().__init__(supervisor, adapter)
        self.install_status = "ready"
        self.runner_ready = False

    async def start_workflow_runner(self, workflow_id: str) -> dict[str, object]:
        self.start_calls.append(workflow_id)
        if not self.runner_ready:
            return {
                "workflow_id": workflow_id,
                "status": RunnerStatus.QUEUED_PENDING_SWITCH.value,
                "queue_id": "runner-start-queue-1",
                "runner": None,
                "pid": None,
                "install_status": "ready",
                "error": None,
            }
        descriptor = _isolated_descriptor(status=RunnerStatus.READY)
        self.supervisor.upsert_runner(descriptor, self.adapter)
        self.supervisor.bind_workflow_runner(workflow_id, descriptor.runner_id)
        return {
            "workflow_id": workflow_id,
            "status": "ready",
            "runner": descriptor.model_dump(mode="json"),
            "pid": descriptor.pid,
            "install_status": "ready",
            "error": None,
        }


# ----------------------------------------------------------------------
# JobRunnerRegistry
# ----------------------------------------------------------------------


def test_job_runner_registry_round_trip() -> None:
    registry = JobRunnerRegistry()
    registry.register("job-1", "core")

    assert registry.runner_for("job-1") == "core"
    assert registry.runner_for("missing") is None

    registry.unregister("job-1")
    assert registry.runner_for("job-1") is None


def test_job_runner_registry_snapshot_is_a_copy() -> None:
    registry = JobRunnerRegistry()
    registry.register("job-1", "core")
    registry.register("job-2", "isolated")

    snapshot = registry.snapshot()
    snapshot["job-3"] = "spurious"

    assert registry.runner_for("job-3") is None
    assert registry.snapshot() == {"job-1": "core", "job-2": "isolated"}


def test_job_runner_registry_rejects_duplicate_job_ids() -> None:
    registry = JobRunnerRegistry()
    registry.register("job-1", "core")

    with pytest.raises(DuplicateJobRegistrationError):
        registry.register("job-1", "isolated")

    assert registry.runner_for("job-1") == "core"


# ----------------------------------------------------------------------
# RunnerSupervisor lookup and updates
# ----------------------------------------------------------------------


def test_supervisor_exposes_registered_core_runner() -> None:
    supervisor = RunnerSupervisor()
    adapter = RecordingAdapter()

    supervisor.register_core_runner(_core_descriptor(), adapter)

    runners = supervisor.list_runners()
    assert [runner.runner_id for runner in runners] == ["core"]
    assert supervisor.core_runner().fingerprint == CORE_RUNNER_FINGERPRINT
    assert supervisor.get_adapter("core") is adapter


def test_supervisor_acquire_runner_returns_core_for_any_workflow() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())

    runner = supervisor.acquire_runner(workflow_package=object())

    assert runner.runner_id == CORE_RUNNER_ID


def test_supervisor_rejects_double_core_registration() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())

    with pytest.raises(RuntimeError):
        supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())


def test_supervisor_rejects_non_core_kind_for_core_slot() -> None:
    supervisor = RunnerSupervisor()
    descriptor = _core_descriptor().model_copy(update={"kind": RunnerKind.ISOLATED_COMFYUI})

    with pytest.raises(ValueError):
        supervisor.register_core_runner(descriptor, RecordingAdapter())


def test_supervisor_get_runner_raises_for_unknown_runner() -> None:
    supervisor = RunnerSupervisor()

    with pytest.raises(RunnerNotFoundError):
        supervisor.get_runner("missing")


def test_supervisor_update_endpoint_propagates_to_adapter_and_descriptor() -> None:
    supervisor = RunnerSupervisor()
    adapter = RecordingAdapter()
    supervisor.register_core_runner(_core_descriptor(), adapter)

    updated = supervisor.update_runner_endpoint(
        CORE_RUNNER_ID, "http://127.0.0.1:9999", "ws://127.0.0.1:9999/ws"
    )

    assert updated.base_url == "http://127.0.0.1:9999"
    assert updated.ws_url == "ws://127.0.0.1:9999/ws"
    assert supervisor.get_runner(CORE_RUNNER_ID).base_url == "http://127.0.0.1:9999"
    assert adapter.endpoint_updates == [("http://127.0.0.1:9999", "ws://127.0.0.1:9999/ws")]


def test_supervisor_update_status_returns_new_descriptor() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())

    updated = supervisor.update_runner_status(CORE_RUNNER_ID, RunnerStatus.READY)

    assert updated.status is RunnerStatus.READY
    assert supervisor.get_runner(CORE_RUNNER_ID).status is RunnerStatus.READY


def test_supervisor_upserts_isolated_runner() -> None:
    supervisor = RunnerSupervisor()
    adapter = RecordingAdapter()
    descriptor = RunnerDescriptor(
        runner_id="isolated-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=RunnerStatus.READY,
    )

    supervisor.upsert_runner(descriptor, adapter)

    assert supervisor.get_runner("isolated-1") == descriptor
    assert supervisor.get_adapter("isolated-1") is adapter


def test_runner_descriptor_exposes_memory_governor_observation_fields() -> None:
    descriptor = _isolated_descriptor().model_copy(
        update={
            "memory_class": RunnerMemoryClass.GPU_MEDIUM,
            "memory_estimate_confidence": RunnerMemoryEstimateConfidence.MEDIUM,
            "memory_estimate_source": RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            "observed_idle_vram_mb": 1200,
            "observed_idle_ram_mb": 900,
            "observed_load_peak_vram_mb": 3200,
            "observed_load_peak_ram_mb": 1800,
            "observed_execution_peak_vram_mb": 7600,
            "observed_execution_peak_ram_mb": 2600,
            "recent_memory_error_at": "2026-05-03T12:00:00+00:00",
            "recent_memory_error_count": 1,
        }
    )

    assert descriptor.memory_class is RunnerMemoryClass.GPU_MEDIUM
    assert descriptor.memory_estimate_confidence is RunnerMemoryEstimateConfidence.MEDIUM
    assert descriptor.memory_estimate_source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    assert descriptor.observed_execution_peak_vram_mb == 7600
    assert descriptor.recent_memory_error_count == 1


def test_supervisor_upsert_rejects_core_runner_kind() -> None:
    supervisor = RunnerSupervisor()

    with pytest.raises(ValueError):
        supervisor.upsert_runner(_core_descriptor(), RecordingAdapter())


def test_supervisor_acquires_ready_bound_workflow_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    descriptor = RunnerDescriptor(
        runner_id="isolated-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=RunnerStatus.READY,
    )
    supervisor.upsert_runner(descriptor, RecordingAdapter())

    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    package = WorkflowPackageLoader(PACKAGE_DIR).get_package("text_to_image_v0")
    assert supervisor.acquire_runner(package).runner_id == "isolated-1"
    assert supervisor.runner_for_workflow("text_to_image_v0").runner_id == "isolated-1"


def test_supervisor_acquires_warm_bound_workflow_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    descriptor = _isolated_descriptor(status=RunnerStatus.IDLE_WARM)
    supervisor.upsert_runner(descriptor, RecordingAdapter())
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    package = WorkflowPackageLoader(PACKAGE_DIR).get_package("text_to_image_v0")

    assert supervisor.acquire_runner(package).runner_id == "isolated-1"


@pytest.mark.parametrize(
    "status",
    [RunnerStatus.RESERVING, RunnerStatus.SUBMITTING, RunnerStatus.RUNNING],
)
def test_supervisor_keeps_busy_workflow_bound_to_isolated_runner(status: RunnerStatus) -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    descriptor = _isolated_descriptor(status=status)
    supervisor.upsert_runner(descriptor, RecordingAdapter())
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    package = WorkflowPackageLoader(PACKAGE_DIR).get_package("text_to_image_v0")

    assert supervisor.acquire_runner(package).runner_id == "isolated-1"


def test_supervisor_falls_back_to_core_when_bound_runner_is_not_ready() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    descriptor = RunnerDescriptor(
        runner_id="isolated-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=RunnerStatus.STOPPED,
    )
    supervisor.upsert_runner(descriptor, RecordingAdapter())
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    package = WorkflowPackageLoader(PACKAGE_DIR).get_package("text_to_image_v0")
    assert supervisor.acquire_runner(package).runner_id == CORE_RUNNER_ID


def test_supervisor_unbind_workflow_runner_restores_core_selection() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    descriptor = RunnerDescriptor(
        runner_id="isolated-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=RunnerStatus.READY,
    )
    supervisor.upsert_runner(descriptor, RecordingAdapter())
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")
    supervisor.unbind_workflow_runner("text_to_image_v0")

    package = WorkflowPackageLoader(PACKAGE_DIR).get_package("text_to_image_v0")
    assert supervisor.acquire_runner(package).runner_id == CORE_RUNNER_ID


def test_supervisor_unbind_runner_removes_all_workflow_bindings_for_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    descriptor = RunnerDescriptor(
        runner_id="isolated-1",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=RunnerStatus.READY,
    )
    supervisor.upsert_runner(descriptor, RecordingAdapter())
    supervisor.bind_workflow_runner("workflow-a", "isolated-1")
    supervisor.bind_workflow_runner("workflow-b", "isolated-1")

    supervisor.unbind_runner("isolated-1")

    assert supervisor.runner_for_workflow("workflow-a") is None
    assert supervisor.runner_for_workflow("workflow-b") is None


# ----------------------------------------------------------------------
# Phase 5f runner lifecycle policy
# ----------------------------------------------------------------------


def test_runner_selection_reuses_compatible_resident_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(
        _isolated_descriptor(status=RunnerStatus.IDLE_WARM),
        RecordingAdapter(),
    )

    decision = supervisor.runner_selection_for(runner_process_compatibility_key="runner-key-a")

    assert decision.action is RunnerSelectionAction.REUSE
    assert decision.runner_id == "isolated-1"
    assert decision.reason == "compatible_runner_resident"


def test_runner_selection_queues_when_incompatible_gpu_runner_is_busy() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(
        _isolated_descriptor(
            compatibility_key="runner-key-a",
            status=RunnerStatus.RUNNING,
            current_job_id="job-1",
        ),
        RecordingAdapter(),
    )

    decision = supervisor.runner_selection_for(runner_process_compatibility_key="runner-key-b")

    assert decision.action is RunnerSelectionAction.QUEUE_PENDING_SWITCH
    assert decision.queued_behind_runner_id == "isolated-1"
    assert decision.reason == "incompatible_gpu_runner_running"


def test_runner_selection_switches_idle_incompatible_gpu_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(
        _isolated_descriptor(
            compatibility_key="runner-key-a",
            status=RunnerStatus.IDLE,
        ),
        RecordingAdapter(),
    )

    decision = supervisor.runner_selection_for(runner_process_compatibility_key="runner-key-b")

    assert decision.action is RunnerSelectionAction.SWITCH
    assert decision.evict_runner_id == "isolated-1"
    assert decision.reason == "evict_idle_incompatible_gpu_runner"


def test_runner_selection_allows_cpu_only_runner_to_start_beside_gpu_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(
        _isolated_descriptor(
            compatibility_key="runner-key-a",
            status=RunnerStatus.RUNNING,
            current_job_id="job-1",
        ),
        RecordingAdapter(),
    )

    decision = supervisor.runner_selection_for(
        runner_process_compatibility_key="runner-key-b",
        memory_class=RunnerMemoryClass.CPU_ONLY,
    )

    assert decision.action is RunnerSelectionAction.START_NEW
    assert decision.reason == "no_compatible_runner"


def test_runner_selection_does_not_evict_core_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        _core_descriptor().model_copy(update={"status": RunnerStatus.READY}),
        RecordingAdapter(),
    )

    decision = supervisor.runner_selection_for(runner_process_compatibility_key="runner-key-a")

    assert decision.action is RunnerSelectionAction.START_NEW
    assert decision.evict_runner_id is None


def test_gpu_medium_is_conservative_until_memory_governor_can_prove_margin() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(
        _isolated_descriptor(
            compatibility_key="runner-key-a",
            status=RunnerStatus.IDLE,
            memory_class=RunnerMemoryClass.GPU_MEDIUM,
        ),
        RecordingAdapter(),
    )

    decision = supervisor.runner_selection_for(
        runner_process_compatibility_key="runner-key-b",
        memory_class=RunnerMemoryClass.GPU_MEDIUM,
    )

    assert decision.action is RunnerSelectionAction.SWITCH
    assert decision.evict_runner_id == "isolated-1"


def test_unknown_memory_class_is_treated_as_gpu_heavy_for_switching() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(
        _isolated_descriptor(
            compatibility_key="runner-key-a",
            status=RunnerStatus.IDLE,
            memory_class=RunnerMemoryClass.UNKNOWN,
        ),
        RecordingAdapter(),
    )

    decision = supervisor.runner_selection_for(runner_process_compatibility_key="runner-key-b")

    assert decision.action is RunnerSelectionAction.SWITCH
    assert decision.evict_runner_id == "isolated-1"


def test_workflow_lease_keeps_runner_warm_until_closed_view_cooldown() -> None:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    supervisor = RunnerSupervisor(closed_view_cooldown_seconds=30, now=lambda: now)
    supervisor.upsert_runner(
        _isolated_descriptor(status=RunnerStatus.READY),
        RecordingAdapter(),
    )

    lease_id = supervisor.open_workflow_lease(
        "text_to_image_v0",
        "isolated-1",
        lease_id="lease-1",
    )
    leased = supervisor.get_runner("isolated-1")

    assert lease_id == "lease-1"
    assert leased.status is RunnerStatus.IDLE_WARM
    assert leased.open_workflow_lease_count == 1
    assert leased.open_workflow_lease_ids == ["lease-1"]
    assert leased.closed_view_cooldown_expires_at is None

    closed = supervisor.close_workflow_lease("lease-1")

    assert closed is not None
    assert closed.status is RunnerStatus.IDLE
    assert closed.open_workflow_lease_count == 0
    assert closed.open_workflow_lease_ids == []
    assert closed.closed_view_cooldown_expires_at == "2026-05-03T12:00:30+00:00"


def test_runner_job_markers_track_active_job_and_warm_status() -> None:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    supervisor = RunnerSupervisor(now=lambda: now)
    supervisor.upsert_runner(
        _isolated_descriptor(status=RunnerStatus.READY),
        RecordingAdapter(),
    )
    supervisor.open_workflow_lease("text_to_image_v0", "isolated-1", lease_id="lease-1")

    running = supervisor.mark_runner_job_started("isolated-1", "job-1")
    finished = supervisor.mark_runner_job_finished("isolated-1", "job-1")

    assert running.status is RunnerStatus.RUNNING
    assert running.current_job_id == "job-1"
    assert running.last_used_at == "2026-05-03T12:00:00+00:00"
    assert finished.status is RunnerStatus.IDLE_WARM
    assert finished.current_job_id is None


def test_supervisor_runner_start_queue_round_trip_and_cancel() -> None:
    now = datetime(2026, 5, 3, 12, 0, tzinfo=UTC)
    supervisor = RunnerSupervisor(now=lambda: now)
    supervisor.upsert_runner(_isolated_descriptor(runner_id="runner-a"), RecordingAdapter())

    queued = supervisor.enqueue_runner_start(
        workflow_id="workflow-b",
        kind=QueuedRunnerStartKind.PENDING_MEMORY,
        queued_behind_runner_id="runner-a",
        reason="memory_pressure_high",
        queue_id="queue-1",
    )

    assert queued.status is QueuedRunnerStartStatus.QUEUED
    assert queued.created_at == "2026-05-03T12:00:00+00:00"
    assert supervisor.get_queued_runner_start("queue-1") == queued
    assert supervisor.list_queued_runner_starts() == [queued]

    canceled = supervisor.cancel_queued_runner_start("queue-1")

    assert canceled is not None
    assert canceled.status is QueuedRunnerStartStatus.CANCELED
    assert canceled.canceled_at == "2026-05-03T12:00:00+00:00"
    assert supervisor.list_queued_runner_starts() == []
    assert supervisor.list_queued_runner_starts(status=None) == [canceled]


def test_supervisor_pops_next_queued_runner_start_after_released_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(_isolated_descriptor(runner_id="runner-a"), RecordingAdapter())
    supervisor.upsert_runner(_isolated_descriptor(runner_id="runner-b"), RecordingAdapter())
    queue_a = supervisor.enqueue_runner_start(
        workflow_id="workflow-a",
        kind=QueuedRunnerStartKind.PENDING_SWITCH,
        queued_behind_runner_id="runner-a",
        queue_id="queue-a",
    )
    supervisor.enqueue_runner_start(
        workflow_id="workflow-b",
        kind=QueuedRunnerStartKind.PENDING_SWITCH,
        queued_behind_runner_id="runner-b",
        queue_id="queue-b",
    )

    popped = supervisor.pop_next_queued_runner_start(released_runner_id="runner-a")

    assert popped == queue_a
    assert supervisor.get_queued_runner_start("queue-a") is None
    assert [item.queue_id for item in supervisor.list_queued_runner_starts()] == ["queue-b"]


# ----------------------------------------------------------------------
# Job routing through the supervisor
# ----------------------------------------------------------------------


def test_supervisor_register_job_rejects_unknown_runner() -> None:
    supervisor = RunnerSupervisor()

    with pytest.raises(RunnerNotFoundError):
        supervisor.register_job("job-1", "missing")


def test_supervisor_routes_jobs_to_their_runner() -> None:
    supervisor = RunnerSupervisor()
    adapter = RecordingAdapter()
    supervisor.register_core_runner(_core_descriptor(), adapter)

    supervisor.register_job("job-1", CORE_RUNNER_ID)

    assert supervisor.runner_for_job("job-1").runner_id == CORE_RUNNER_ID
    assert supervisor.adapter_for_job("job-1") is adapter


def test_supervisor_runner_for_unknown_job_raises() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())

    with pytest.raises(JobRunnerNotFoundError):
        supervisor.runner_for_job("missing-job")


def test_supervisor_forget_job_removes_routing() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    supervisor.register_job("job-1", CORE_RUNNER_ID)

    supervisor.forget_job("job-1")

    with pytest.raises(JobRunnerNotFoundError):
        supervisor.runner_for_job("job-1")


# ----------------------------------------------------------------------
# Engine service routes through the supervisor end-to-end
# ----------------------------------------------------------------------


class StaticMemoryObserver:
    def __init__(self, snapshot: MachineMemorySnapshot) -> None:
        self.snapshot_value = snapshot

    def snapshot(self) -> MachineMemorySnapshot:
        return self.snapshot_value


class ReleaseAwareMemoryObserver:
    def __init__(self, adapter: RecordingAdapter) -> None:
        self.adapter = adapter

    def snapshot(self) -> MachineMemorySnapshot:
        return MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=12_000,
            free_vram_mb=10_000 if self.adapter.release_memory_calls else 8_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )


class SequenceMemoryObserver:
    def __init__(self, snapshots: list[MachineMemorySnapshot]) -> None:
        self.snapshots = snapshots
        self.index = 0

    def snapshot(self) -> MachineMemorySnapshot:
        snapshot = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return snapshot


class RecordingStopCoordinator:
    def __init__(self, supervisor: RunnerSupervisor) -> None:
        self.supervisor = supervisor
        self.stopped_runner_ids: list[str] = []

    async def stop_runner(self, runner_id: str):
        self.stopped_runner_ids.append(runner_id)
        self.supervisor.update_runner_status(runner_id, RunnerStatus.STOPPED)
        return SimpleNamespace(status=RunnerStatus.STOPPED)

    async def stop_all_runners(self) -> list[Any]:
        return []


class AttributingMemoryObserver(SequenceMemoryObserver):
    def sample_process_vram(self, pids: set[int]) -> GpuProcessMemorySample:
        matched = sorted(pid for pid in pids if pid in {4242, 4243})
        if not matched:
            return GpuProcessMemorySample(
                requested_pids=sorted(pids),
                attribution_sources=["nvml_process"],
                error="nvml_process_memory_no_matching_pid",
            )
        return GpuProcessMemorySample(
            available=True,
            requested_pids=sorted(pids),
            matched_pids=matched,
            process_tree_vram_mb=1800,
            attribution_quality=MemoryAttributionQuality.PROCESS_EXACT,
            attribution_sources=["nvml_process"],
            attribution_reasons=["nvml_process_memory_matched_runner_pid"],
        )


class FakeProcessTreeMemoryObserver:
    def sample(self, root_pid: int | None) -> ProcessTreeMemorySample:
        if root_pid != 4242:
            return ProcessTreeMemorySample(
                root_pid=root_pid,
                attribution_sources=["process_tree_rss"],
                error="runner_process_not_found",
            )
        return ProcessTreeMemorySample(
            available=True,
            root_pid=4242,
            child_pids=[4243],
            process_tree_ram_mb=2200,
            attribution_quality=MemoryAttributionQuality.PROCESS_TREE,
            attribution_sources=["process_tree_rss"],
            attribution_reasons=["runner_process_tree_rss"],
        )


class FakeRunnerMemoryTelemetryReader:
    def sample(
        self,
        telemetry_path,
        *,
        runner_id=None,
        job_id=None,
        sample_window=MemorySampleWindow.UNKNOWN,
        observed_after=None,
    ):
        del telemetry_path
        del observed_after
        return BackendAllocatorMemorySample(
            available=True,
            runner_id=runner_id,
            job_id=job_id,
            pid=4242,
            sample_window=sample_window,
            backend=MemoryBackend.CUDA,
            current_vram_mb=2300,
            peak_vram_mb=3600,
            attribution_quality=MemoryAttributionQuality.BACKEND_ALLOCATOR,
            attribution_sources=["pytorch_cuda_allocator"],
            attribution_reasons=["runner_side_cuda_allocator_stats"],
            details={"cuda": {"reserved_peak_bytes": 3774873600}},
        )


def _build_service(
    adapter: RecordingAdapter,
    *,
    memory_learning_store: LocalMemoryLearningStore | None = None,
    memory_observer: StaticMemoryObserver | SequenceMemoryObserver | None = None,
    process_tree_memory_observer: FakeProcessTreeMemoryObserver | None = None,
    runner_memory_telemetry_reader: FakeRunnerMemoryTelemetryReader | None = None,
    runner_process_coordinator: Any | None = None,
    runner_process_coordinator_factory: Any | None = None,
) -> tuple[EngineService, RunnerSupervisor]:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), adapter)
    if runner_process_coordinator_factory is not None:
        runner_process_coordinator = runner_process_coordinator_factory(supervisor)
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(PACKAGE_DIR),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        runner_process_coordinator=runner_process_coordinator,
        memory_learning_store=memory_learning_store,
        memory_observer=memory_observer,
        process_tree_memory_observer=process_tree_memory_observer,
        runner_memory_telemetry_reader=runner_memory_telemetry_reader,
    )
    return service, supervisor


def test_existing_workflow_queue_wait_does_not_request_immediate_redispatch() -> None:
    service, supervisor = _build_service(RecordingAdapter())
    reasons: list[str] = []
    service.run_orchestrator.request_run_dispatch = reasons.append
    package = WorkflowPackageLoader(PACKAGE_DIR).get_package("text_to_image_v0")
    snapshot = service.run_orchestrator._run_submission_snapshot(
        package=package,
        inputs={},
        output_preferences_snapshot=None,
        run_submission_snapshot=None,
    )
    runner = supervisor.core_runner()

    queued = service.run_orchestrator._runner_reservation_wait_job(
        workflow_id="text_to_image_v0",
        inputs={},
        options={},
        runner=runner,
        run_submission_snapshot=snapshot,
        queue_id=None,
    )
    assert reasons == ["submission_reservation_busy"]

    reasons.clear()
    service.run_orchestrator._runner_reservation_wait_job(
        workflow_id="text_to_image_v0",
        inputs={},
        options={},
        runner=runner,
        run_submission_snapshot=snapshot,
        queue_id=queued.queue_id,
    )
    assert reasons == []


def test_memory_input_profile_ignores_prompt_and_seed_but_keeps_memory_changing_inputs() -> None:
    package = WorkflowPackageLoader(PACKAGE_DIR).get_package("text_to_image_v0")

    base = memory_input_profile_fingerprint(
        {
            "prompt": "a lake",
            "seed": 1,
            "width": 512,
            "height": 512,
            "batch_size": 1,
            "model": "model-a",
            "lora": "none",
            "frame_count": 8,
            "source_image": "image-a.png",
        },
        {"vram_mode": "normal", "precision": "fp16"},
        package=package,
    )
    prompt_changed = memory_input_profile_fingerprint(
        {
            "prompt": "a forest",
            "seed": 999,
            "width": 512,
            "height": 512,
            "batch_size": 1,
            "model": "model-a",
            "lora": "none",
            "frame_count": 8,
            "source_image": "image-a.png",
        },
        {"vram_mode": "normal", "precision": "fp16"},
        package=package,
    )

    assert prompt_changed == base
    for changed_inputs, changed_options in [
        ({"width": 768}, {}),
        ({"batch_size": 2}, {}),
        ({"model": "model-b"}, {}),
        ({"lora": "detail-lora"}, {}),
        ({"frame_count": 24}, {}),
        ({"source_image": "image-b.png"}, {}),
        ({}, {"vram_mode": "lowvram"}),
        ({}, {"precision": "fp8"}),
    ]:
        inputs = {
            "prompt": "a forest",
            "seed": 999,
            "width": 512,
            "height": 512,
            "batch_size": 1,
            "model": "model-a",
            "lora": "none",
            "frame_count": 8,
            "source_image": "image-a.png",
            **changed_inputs,
        }
        options = {"vram_mode": "normal", "precision": "fp16", **changed_options}
        assert memory_input_profile_fingerprint(inputs, options, package=package) != base


def test_memory_input_profile_keeps_string_fields_that_are_not_prompt_text() -> None:
    package = SimpleNamespace(
        inputs=[
            SimpleNamespace(
                id="model",
                control="string_field",
                binding=SimpleNamespace(node_id="4", input_name="ckpt_name"),
            ),
            SimpleNamespace(
                id="prompt",
                control="string_field",
                binding=SimpleNamespace(node_id="6", input_name="text"),
            ),
        ],
        dashboard=SimpleNamespace(inputs=[], sections=[]),
    )

    base = memory_input_profile_fingerprint(
        {"model": "model-a", "prompt": "a lake"},
        {},
        package=package,
    )
    prompt_changed = memory_input_profile_fingerprint(
        {"model": "model-a", "prompt": "a forest"},
        {},
        package=package,
    )
    model_changed = memory_input_profile_fingerprint(
        {"model": "model-b", "prompt": "a forest"},
        {},
        package=package,
    )

    assert prompt_changed == base
    assert model_changed != base


@pytest.mark.anyio
async def test_workflow_run_memory_estimate_uses_runtime_dimensions_and_graph_batch_defaults() -> None:
    model = ModelInfo(
        folder="checkpoints",
        filename="v1-5-pruned-emaonly-fp16.safetensors",
    )
    memory_snapshot = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=24_000,
            free_vram_mb=20_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )
    small_service, _ = _build_service(
        RecordingAdapter(models=[model]),
        memory_observer=memory_snapshot,
    )
    large_service, _ = _build_service(
        RecordingAdapter(models=[model]),
        memory_observer=memory_snapshot,
    )

    small = await small_service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a lake", "width": 512, "height": 512},
        options={},
    )
    large = await large_service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a lake", "width": 1024, "height": 1024},
        options={},
    )

    assert isinstance(small, EngineJob)
    assert isinstance(large, EngineJob)
    assert small.memory_decision is not None
    assert large.memory_decision is not None
    small_estimate = small.memory_decision["workflow_estimate"]
    large_estimate = large.memory_decision["workflow_estimate"]
    small_features = small.memory_decision["developer_details"]["runtime_estimate_features"]

    assert small_estimate["source"] == "heuristic"
    assert large_estimate["source"] == "heuristic"
    assert large_estimate["estimated_peak_vram_mb"] > small_estimate["estimated_peak_vram_mb"]
    assert small_features["resolution_width"] == 512
    assert small_features["resolution_height"] == 512
    assert small_features["batch_size"] == 1
    assert small_features["workflow_type"] == "txt2img"
    assert small_features["sources"]["resolution_width"] == "input:width"
    assert small_features["sources"]["resolution_height"] == "input:height"
    assert small_features["sources"]["batch_size"] == "graph:5.batch_size"


@pytest.mark.anyio
async def test_workflow_run_memory_estimate_uses_runtime_precision_and_vram_mode() -> None:
    model = ModelInfo(
        folder="checkpoints",
        filename="v1-5-pruned-emaonly-fp16.safetensors",
    )
    memory_snapshot = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=24_000,
            free_vram_mb=20_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )
    normal_service, _ = _build_service(
        RecordingAdapter(models=[model]),
        memory_observer=memory_snapshot,
    )
    high_service, _ = _build_service(
        RecordingAdapter(models=[model]),
        memory_observer=memory_snapshot,
    )

    normal = await normal_service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a lake", "width": 512, "height": 512},
        options={"precision": "fp16", "vram_mode": "normal"},
    )
    high = await high_service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a lake", "width": 512, "height": 512},
        options={"precision": "float32", "vram_mode": "high_vram"},
    )

    assert normal.memory_decision is not None
    assert high.memory_decision is not None
    normal_estimate = normal.memory_decision["workflow_estimate"]
    high_estimate = high.memory_decision["workflow_estimate"]
    high_features = high.memory_decision["developer_details"]["runtime_estimate_features"]

    assert high_estimate["estimated_peak_vram_mb"] > normal_estimate["estimated_peak_vram_mb"]
    assert high_estimate["precision"] == "fp32"
    assert high_estimate["vram_mode"] == "highvram"
    assert "precision_memory_option" in high_estimate["reasons"]
    assert "vram_mode_memory_option" in high_estimate["reasons"]
    assert high_features["precision"] == "fp32"
    assert high_features["vram_mode"] == "highvram"
    assert high_features["sources"]["precision"] == "option:precision"
    assert high_features["sources"]["vram_mode"] == "option:vram_mode"


@pytest.mark.anyio
async def test_workflow_run_memory_estimate_exposes_selected_models_and_loras() -> None:
    model = ModelInfo(
        folder="checkpoints",
        filename="v1-5-pruned-emaonly-fp16.safetensors",
    )
    service, _ = _build_service(
        RecordingAdapter(models=[model]),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=24_000,
                free_vram_mb=20_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )
    package = service.run_orchestrator.workflow_loader.get_package("text_to_image_v0")
    custom_package = package.model_copy(
        update={
            "comfyui_graph": {
                **package.comfyui_graph,
                "30": {
                    "class_type": "ControlNetLoader",
                    "inputs": {"control_net_name": "pose-controlnet.safetensors"},
                },
                "31": {
                    "class_type": "IPAdapterModelLoader",
                    "inputs": {"ipadapter_file": "ip-adapter-plus.safetensors"},
                },
                "32": {
                    "class_type": "LoraLoader",
                    "inputs": {
                        "lora_name": "style-default.safetensors",
                        "strength_model": 1.0,
                        "strength_clip": 0.5,
                    },
                },
            },
            "inputs": [
                *package.inputs,
                WorkflowInput(
                    id="style_lora",
                    label="Style LoRA",
                    control="lora_loader",
                    binding={"node_id": "32", "input_name": "lora_name"},
                    default="style-default.safetensors",
                ),
                WorkflowInput(
                    id="style_strength",
                    label="Style strength",
                    control="slider",
                    binding={"node_id": "32", "input_name": "strength_model"},
                    default=1.0,
                ),
            ],
        }
    )
    service.run_orchestrator.workflow_loader = SimpleNamespace(
        get_package=lambda workflow_id: custom_package
    )

    job = await service.run_workflow(
        "text_to_image_v0",
        inputs={
            "prompt": "a lake",
            "width": 512,
            "height": 512,
            "style_lora": "detail-style.safetensors",
            "style_strength": 0.75,
        },
        options={},
    )

    assert job.memory_decision is not None
    estimate = job.memory_decision["workflow_estimate"]
    features = job.memory_decision["developer_details"]["runtime_estimate_features"]
    signatures = job.memory_decision["developer_details"]["memory_signatures"]

    assert estimate["selected_model_count"] == 3
    assert estimate["selected_model_kinds"] == [
        "checkpoint",
        "controlnet",
        "ipadapter",
    ]
    assert estimate["lora_count"] == 1
    assert estimate["lora_strength_total"] == 0.75
    assert estimate["process_compatibility_signature"] == signatures[
        "process_compatibility_signature"
    ]
    assert estimate["model_residency_signature"] == signatures[
        "model_residency_signature"
    ]
    assert estimate["execution_profile_signature"] == signatures[
        "execution_profile_signature"
    ]
    assert signatures["payloads"]["model_residency"]["selected_models"]
    assert signatures["payloads"]["execution_profile"]["resolution_width"] == 512
    assert "selected_model_memory_heuristic" in estimate["reasons"]
    assert "lora_memory_heuristic" in estimate["reasons"]
    assert features["selected_model_count"] == 3
    assert features["lora_count"] == 1
    assert features["selected_loras"][0]["selection"] == "detail-style.safetensors"
    assert features["selected_loras"][0]["source"] == "input:style_lora"
    assert features["selected_loras"][0]["strength_model"] == 0.75
    assert features["selected_loras"][0]["strength_clip"] == 0.5


@pytest.mark.anyio
async def test_workflow_run_memory_estimate_exposes_custom_node_uncertainty() -> None:
    model = ModelInfo(
        folder="checkpoints",
        filename="v1-5-pruned-emaonly-fp16.safetensors",
    )
    memory_snapshot = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=24_000,
            free_vram_mb=20_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )
    service, _ = _build_service(
        RecordingAdapter(models=[model]),
        memory_observer=memory_snapshot,
    )
    package = service.run_orchestrator.workflow_loader.get_package("text_to_image_v0")
    custom_package = package.model_copy(
        update={
            "custom_nodes": [
                WorkflowCustomNodeRecord(
                    id="impact-pack",
                    folder_name="ComfyUI-Impact-Pack",
                    source="https://example.invalid/impact-pack.git",
                    node_types=["ImpactWildcard"],
                )
            ]
        }
    )
    service.run_orchestrator.workflow_loader = SimpleNamespace(
        get_package=lambda workflow_id: custom_package
    )

    job = await service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a lake", "width": 512, "height": 512},
        options={},
    )

    assert job.memory_decision is not None
    estimate = job.memory_decision["workflow_estimate"]
    custom_node_details = job.memory_decision["developer_details"][
        "custom_node_memory_uncertainty"
    ]

    assert estimate["source"] == "heuristic"
    assert estimate["confidence"] == "low"
    assert estimate["custom_node_count"] == 1
    assert estimate["custom_node_types"] == ["ImpactWildcard"]
    assert "custom_node_memory_uncertain" in estimate["reasons"]
    assert custom_node_details["custom_node_count"] == 1
    assert custom_node_details["custom_node_ids"] == ["impact-pack"]


def test_engine_service_runner_lease_round_trip_uses_bound_runner() -> None:
    service, supervisor = _build_service(RecordingAdapter())
    supervisor.upsert_runner(
        _isolated_descriptor(status=RunnerStatus.READY),
        RecordingAdapter(),
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    opened = service.open_workflow_runner_lease("text_to_image_v0")
    closed = service.close_workflow_runner_lease("text_to_image_v0", opened["lease_id"])

    assert opened["status"] == RunnerStatus.IDLE_WARM.value
    assert opened["runner"]["open_workflow_lease_count"] == 1
    assert closed["status"] == RunnerStatus.IDLE.value
    assert closed["runner"]["open_workflow_lease_count"] == 0
    assert closed["runner"]["closed_view_cooldown_expires_at"] is not None


def test_engine_service_runner_lease_reports_no_bound_runner() -> None:
    service, _ = _build_service(RecordingAdapter())

    result = service.open_workflow_runner_lease("text_to_image_v0")

    assert result == {
        "workflow_id": "text_to_image_v0",
        "status": "no_runner",
        "lease_id": None,
        "runner": None,
    }


def test_engine_service_uses_constructor_dashboard_dependencies() -> None:
    class DashboardAuthoring:
        def get_bindable_inputs(self, workflow_id: str, **kwargs) -> dict[str, object]:
            del kwargs
            return {"workflow_id": workflow_id, "inputs": []}

    class Exporter:
        def export_archive(self, workflow_id: str) -> tuple[bytes, str]:
            return b"archive", f"{workflow_id}.noofy"

    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(PACKAGE_DIR),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        dashboard_authoring=DashboardAuthoring(),
        workflow_exporter=Exporter(),
    )

    assert service.get_bindable_inputs("text_to_image_v0") == {
        "workflow_id": "text_to_image_v0",
        "inputs": [],
    }
    assert service.export_workflow_archive("text_to_image_v0") == (
        b"archive",
        "text_to_image_v0.noofy",
    )


@pytest.mark.anyio
async def test_upload_workflow_image_uses_bound_runner_adapter() -> None:
    service, supervisor = _build_service(RecordingAdapter())
    isolated_adapter = RecordingAdapter()
    supervisor.upsert_runner(
        _isolated_descriptor(status=RunnerStatus.READY),
        isolated_adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    result = await service.upload_workflow_image(
        "text_to_image_v0",
        "input.png",
        b"image-bytes",
        "image/png",
    )

    assert result == {"filename": "uploaded-input.png"}
    assert isolated_adapter.upload_calls == [
        ("text_to_image_v0", "input.png", b"image-bytes", "image/png")
    ]


@pytest.mark.anyio
async def test_fetch_output_uses_job_bound_runner_adapter() -> None:
    core_adapter = RecordingAdapter(next_job_id="core-job")
    service, supervisor = _build_service(core_adapter)
    isolated_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="isolated-job",
    )
    supervisor.upsert_runner(
        _isolated_descriptor(status=RunnerStatus.READY),
        isolated_adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    content, media_type = await service.fetch_output(
        job.job_id,
        "result.png",
        "preview",
        "output",
    )

    assert content == b"output-bytes"
    assert media_type == "image/png"
    assert isolated_adapter.fetch_output_calls == [
        ("isolated-job", "result.png", "preview", "output")
    ]
    assert core_adapter.fetch_output_calls == []


@pytest.mark.anyio
async def test_run_workflow_registers_job_against_acquired_runner() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(adapter)

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert isinstance(job, EngineJob)
    assert supervisor.runner_for_job(job.job_id).runner_id == CORE_RUNNER_ID
    assert adapter.run_calls and adapter.run_calls[0][0] == job.job_id


@pytest.mark.anyio
async def test_run_workflow_auto_prepares_custom_node_runner_before_submit() -> None:
    core_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="core-job",
    )
    isolated_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="isolated-job",
    )
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), core_adapter)
    lifecycle = AutoPrepareLifecycle(supervisor, isolated_adapter)
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(PACKAGE_DIR),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        workflow_runner_lifecycle_service=lifecycle,
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert isinstance(job, EngineJob)
    assert lifecycle.prepare_calls == ["text_to_image_v0"]
    assert lifecycle.start_calls == ["text_to_image_v0"]
    assert supervisor.runner_for_job(job.job_id).runner_id == "isolated-1"
    assert isolated_adapter.run_calls == [("isolated-job", {})]


@pytest.mark.anyio
async def test_custom_node_run_click_survives_queued_runner_start() -> None:
    core_adapter = RecordingAdapter()
    isolated_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="isolated-job",
    )
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), core_adapter)
    lifecycle = QueuedAutoPrepareLifecycle(supervisor, isolated_adapter)
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(PACKAGE_DIR),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        workflow_runner_lifecycle_service=lifecycle,
    )

    queued = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    lifecycle.runner_ready = True
    service.run_lifecycle_service.request_dispatch("runner_ready")
    await asyncio.sleep(0.05)

    assert queued.status == "queued_pending_memory"
    assert queued.queue_id == queued.job_id
    assert service.workflow_run_queue_service.resolve(queued.job_id).job_id == "isolated-job"
    assert isolated_adapter.run_calls == [("isolated-job", {})]
    assert core_adapter.run_calls == []


@pytest.mark.anyio
async def test_run_workflow_auto_prepare_failure_reports_root_cause() -> None:
    core_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
    )
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), core_adapter)
    lifecycle = AutoPrepareLifecycle(supervisor, RecordingAdapter())

    async def fail_prepare(workflow_id: str) -> dict[str, object]:
        lifecycle.prepare_calls.append(workflow_id)
        lifecycle.install_status = "failed"
        return {
            "workflow_id": workflow_id,
            "status": "failed",
            "user_facing_message": "Cannot prepare automatically",
            "last_error": "Node package dependency could not be resolved.",
        }

    lifecycle.prepare_workflow = fail_prepare  # type: ignore[method-assign]
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(PACKAGE_DIR),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        workflow_runner_lifecycle_service=lifecycle,
    )

    result = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert hasattr(result, "valid") and not result.valid
    assert result.errors == ["Node package dependency could not be resolved."]
    assert lifecycle.prepare_calls == ["text_to_image_v0"]
    assert lifecycle.start_calls == []
    assert core_adapter.run_calls == []


@pytest.mark.anyio
async def test_run_workflow_repairs_stale_ready_runtime_artifacts_before_submit() -> None:
    core_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="core-job",
    )
    isolated_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="isolated-job",
    )
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), core_adapter)
    lifecycle = AutoPrepareLifecycle(supervisor, isolated_adapter)
    lifecycle.install_status = "ready"
    original_start = lifecycle.start_workflow_runner

    async def stale_once_start(workflow_id: str) -> dict[str, object]:
        if not lifecycle.prepare_calls:
            lifecycle.start_calls.append(workflow_id)
            return {
                "workflow_id": workflow_id,
                "status": "failed",
                "runner": None,
                "pid": None,
                "install_status": "ready",
                "error": "Prepared runtime artifact is not ready: dependency environment manifest fingerprint mismatch, runner workspace manifest fingerprint mismatch",
            }
        return await original_start(workflow_id)

    lifecycle.start_workflow_runner = stale_once_start  # type: ignore[method-assign]
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(PACKAGE_DIR),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
        workflow_runner_lifecycle_service=lifecycle,
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert isinstance(job, EngineJob)
    assert lifecycle.prepare_calls == ["text_to_image_v0"]
    assert lifecycle.start_calls == ["text_to_image_v0", "text_to_image_v0"]
    assert supervisor.runner_for_job(job.job_id).runner_id == "isolated-1"
    assert isolated_adapter.run_calls == [("isolated-job", {})]
    assert core_adapter.run_calls == []


@pytest.mark.anyio
async def test_core_runner_run_uses_memory_governor_cautious_admission() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        adapter,
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=10_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert isinstance(job, EngineJob)
    assert job.status == "queued"
    assert job.memory_decision is not None
    assert job.memory_decision["action"] == "start_co_resident"
    assert job.memory_decision["reason_code"] == "no_resident_runners"
    assert job.memory_status is not None
    assert job.memory_status["state"] == "ready_warm_co_resident"
    assert supervisor.runner_for_job(job.job_id).runner_id == CORE_RUNNER_ID


@pytest.mark.anyio
async def test_progress_cancel_result_route_through_registry() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, _ = _build_service(adapter)
    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    await service.get_progress(job.job_id)
    await service.cancel_job(job.job_id)
    await service.get_result(job.job_id)

    assert adapter.progress_calls == [job.job_id]
    assert adapter.cancel_calls == [job.job_id]
    assert adapter.result_calls == [job.job_id]


@pytest.mark.anyio
async def test_get_result_records_successful_local_observation_without_reusing_stale_descriptor_peak(
    tmp_path: Path,
) -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    learning_store = LocalMemoryLearningStore(tmp_path / "memory-learning")
    service, supervisor = _build_service(
        adapter,
        memory_learning_store=learning_store,
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                machine_profile_id="machine-a",
                total_vram_mb=12_000,
                free_vram_mb=8_000,
                total_ram_mb=64_000,
                free_ram_mb=50_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(
            update={
                "observed_execution_peak_vram_mb": 2400,
                "observed_execution_peak_ram_mb": 1200,
            }
        ),
        adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    await service.get_result(job.job_id)
    summary = learning_store.list_summaries()[0]

    assert summary is not None
    assert summary.input_profile_fingerprint is not None
    assert summary.successful_runs == 1
    assert summary.memory_error_runs == 0
    assert summary.observed_peak_vram_mb is None
    assert job.memory_decision is not None
    estimate = job.memory_decision["workflow_estimate"]
    assert summary.process_compatibility_signature == estimate[
        "process_compatibility_signature"
    ]
    assert summary.model_residency_signature == estimate[
        "model_residency_signature"
    ]
    assert summary.execution_profile_signature == estimate[
        "execution_profile_signature"
    ]


@pytest.mark.anyio
async def test_prompt_and_seed_edit_reruns_reuse_warm_runner_instead_of_blocking_memory(tmp_path: Path) -> None:
    adapter = SuccessfulIncrementingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        adapter,
        memory_learning_store=LocalMemoryLearningStore(tmp_path / "memory-learning"),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=23_028,
                free_vram_mb=20_000,
                total_ram_mb=64_000,
                free_ram_mb=50_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )

    first = await service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a lake", "seed": 1, "width": 512, "height": 512},
        options={},
    )
    await service.get_result(first.job_id)
    service.memory_observer = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=23_028,
            free_vram_mb=8_617,
            total_ram_mb=64_000,
            free_ram_mb=50_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )

    second = await service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a forest", "seed": 1, "width": 512, "height": 512},
        options={},
    )
    await service.get_result(second.job_id)
    third = await service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "a forest", "seed": 999, "width": 512, "height": 512},
        options={},
    )

    assert isinstance(second, EngineJob)
    assert second.status == "queued"
    assert second.memory_decision is not None
    assert second.memory_decision["action"] == "reuse_runner"
    assert second.memory_decision["selected_runner_id"] == CORE_RUNNER_ID
    assert second.memory_decision["developer_details"]["memory_ownership"]["same_warm_runner_id"] == CORE_RUNNER_ID
    assert isinstance(third, EngineJob)
    assert third.status == "queued"
    assert third.memory_decision is not None
    assert third.memory_decision["action"] == "reuse_runner"
    assert third.memory_decision["selected_runner_id"] == CORE_RUNNER_ID
    assert supervisor.core_runner().last_workflow_id == "text_to_image_v0"
    assert adapter.run_calls == [
        ("job-1", {"prompt": "a lake", "seed": 1, "width": 512, "height": 512}),
        ("job-2", {"prompt": "a forest", "seed": 1, "width": 512, "height": 512}),
        ("job-3", {"prompt": "a forest", "seed": 999, "width": 512, "height": 512}),
    ]


@pytest.mark.anyio
async def test_workflow_run_releases_incompatible_warm_core_memory_then_submits() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        adapter,
        memory_observer=SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=10_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=10_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
    )
    supervisor.mark_runner_job_started(CORE_RUNNER_ID, "old-job", workflow_id="workflow-a")
    supervisor.mark_runner_job_finished(CORE_RUNNER_ID, "old-job")
    supervisor.fill_runner_memory_observation(
        CORE_RUNNER_ID,
        observed_execution_peak_vram_mb=6000,
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert isinstance(job, EngineJob)
    assert job.status == "queued"
    assert job.memory_decision is not None
    assert job.memory_decision["action"] == "evict_then_start"
    assert job.memory_decision["evict_runner_ids"] == [CORE_RUNNER_ID]
    assert adapter.release_memory_calls == 1
    assert len(adapter.run_calls) == 1
    assert supervisor.core_runner().last_workflow_id == "text_to_image_v0"


@pytest.mark.anyio
async def test_core_residency_is_retained_when_cleanup_observer_becomes_unavailable() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        adapter,
        memory_observer=SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    available=False,
                    backend=MemoryBackend.CUDA,
                    error="nvml unavailable",
                ),
            ]
        ),
    )
    supervisor.mark_runner_job_started(CORE_RUNNER_ID, "old-job", workflow_id="workflow-a")
    supervisor.mark_runner_job_finished(CORE_RUNNER_ID, "old-job")
    supervisor.fill_runner_memory_observation(
        CORE_RUNNER_ID,
        observed_execution_peak_vram_mb=6000,
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert isinstance(job, EngineJob)
    assert job.status == "blocked_by_memory"
    assert job.memory_status is not None
    assert job.memory_status["state"] == "memory_cleanup_failed"
    assert adapter.release_memory_calls == 1
    assert adapter.run_calls == []
    assert supervisor.core_runner().status is RunnerStatus.RELEASE_FAILED
    assert supervisor.core_runner().last_workflow_id == "workflow-a"


@pytest.mark.anyio
async def test_mixed_cleanup_refreshes_core_baseline_after_isolated_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = RecordingAdapter()
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingStopCoordinator:
        nonlocal coordinator
        coordinator = RecordingStopCoordinator(supervisor)
        return coordinator

    service, supervisor = _build_service(
        adapter,
        runner_process_coordinator_factory=coordinator_factory,
        memory_observer=SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=8_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=8_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
    )
    supervisor.mark_runner_job_started(CORE_RUNNER_ID, "old-job", workflow_id="workflow-a")
    supervisor.mark_runner_job_finished(CORE_RUNNER_ID, "old-job")
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="idle-heavy",
            status=RunnerStatus.IDLE_WARM,
        ),
        RecordingAdapter(),
    )
    decision = MemoryGovernorDecision(
        action=MemoryDecisionAction.EVICT_THEN_START,
        reason_code="mixed_cleanup_validation",
        workflow_id="workflow-b",
        evict_runner_ids=[CORE_RUNNER_ID, "idle-heavy"],
        workflow_estimate=WorkflowMemoryEstimate(
            workflow_id="workflow-b",
            estimated_peak_vram_mb=100,
            estimated_peak_ram_mb=100,
        ),
    )
    monkeypatch.setattr(
        memory_service_module,
        "settings",
        replace(memory_service_module.settings, memory_release_timeout_seconds=0),
    )

    cleaned_up = await service.memory_service.cleanup_idle_runners_for_memory_decision(
        decision,
        metric_name="mixed_cleanup_test",
        log_source="test",
        log_message="test cleanup",
    )
    release = await service.memory_service.wait_for_memory_release_after_cleanup(decision)

    assert coordinator is not None
    assert coordinator.stopped_runner_ids == ["idle-heavy"]
    assert cleaned_up is True
    assert adapter.release_memory_calls == 1
    assert release.status is MemoryReleaseStatus.TIMEOUT
    assert release.timeline[0]["baseline_free_vram_mb"] == 8_000
    assert supervisor.core_runner().status is RunnerStatus.RELEASE_FAILED


@pytest.mark.anyio
async def test_workflow_run_evicts_idle_isolated_runner_waits_for_release_then_submits() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingStopCoordinator:
        nonlocal coordinator
        coordinator = RecordingStopCoordinator(supervisor)
        return coordinator

    service, supervisor = _build_service(
        adapter,
        runner_process_coordinator_factory=coordinator_factory,
        memory_observer=SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=500,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=10_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=10_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="idle-heavy",
            status=RunnerStatus.IDLE_WARM,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
        ).model_copy(update={"observed_idle_vram_mb": 7000}),
        RecordingAdapter(),
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert coordinator is not None
    assert isinstance(job, EngineJob)
    assert job.status == "queued"
    assert job.memory_decision is not None
    assert job.memory_decision["action"] == "evict_then_start"
    assert coordinator.stopped_runner_ids == ["idle-heavy"]
    assert len(adapter.run_calls) == 1


@pytest.mark.anyio
async def test_workflow_run_blocks_only_after_idle_runner_release_times_out() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    coordinator = None

    def coordinator_factory(supervisor: RunnerSupervisor) -> RecordingStopCoordinator:
        nonlocal coordinator
        coordinator = RecordingStopCoordinator(supervisor)
        return coordinator

    service, supervisor = _build_service(
        adapter,
        runner_process_coordinator_factory=coordinator_factory,
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="idle-heavy",
            status=RunnerStatus.IDLE_WARM,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
        ).model_copy(update={"observed_idle_vram_mb": 7000}),
        RecordingAdapter(),
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert coordinator is not None
    assert isinstance(job, EngineJob)
    assert job.status == "blocked_by_memory"
    assert job.memory_status is not None
    assert job.memory_status["state"] == "memory_cleanup_failed"
    assert job.memory_decision is not None
    assert job.memory_decision["developer_details"]["memory_cleanup_failure"]["reason_code"] == "memory_release_timeout"
    assert coordinator.stopped_runner_ids == ["idle-heavy"]
    assert adapter.run_calls == []


@pytest.mark.anyio
async def test_get_result_fills_missing_runner_peak_from_best_effort_sampling() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        adapter,
        memory_observer=SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=7_000,
                    total_ram_mb=64_000,
                    free_ram_mb=60_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=7_000,
                    total_ram_mb=64_000,
                    free_ram_mb=60_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=4_500,
                    total_ram_mb=64_000,
                    free_ram_mb=57_500,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ),
        adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    await service.get_result(job.job_id)
    runner = supervisor.get_runner("isolated-1")

    assert runner.observed_execution_peak_vram_mb == 2500
    assert runner.observed_execution_peak_ram_mb == 2500


@pytest.mark.anyio
async def test_get_result_prefers_process_tree_and_nvml_process_attribution(tmp_path: Path) -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    learning_store = LocalMemoryLearningStore(tmp_path / "memory-learning")
    service, supervisor = _build_service(
        adapter,
        memory_learning_store=learning_store,
        memory_observer=AttributingMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=7_000,
                    total_ram_mb=64_000,
                    free_ram_mb=60_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=4_000,
                    total_ram_mb=64_000,
                    free_ram_mb=55_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
        process_tree_memory_observer=FakeProcessTreeMemoryObserver(),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"pid": 4242}),
        adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    await service.get_result(job.job_id)
    runner = supervisor.get_runner("isolated-1")
    summary = learning_store.list_summaries()[0]
    events = service.log_store.list_events().events
    finish_event = next(event for event in events if event.message == "Finished best-effort job memory sampling")

    assert runner.observed_execution_peak_vram_mb == 1800
    assert runner.observed_execution_peak_ram_mb == 2200
    assert summary.observed_peak_vram_mb == 1800
    assert summary.observed_peak_ram_mb == 2200
    assert summary.process_tree_observed_peak_vram_mb == 1800
    assert summary.process_tree_observed_peak_ram_mb == 2200
    assert summary.attribution_quality is MemoryAttributionQuality.PROCESS_EXACT
    assert "nvml_process" in summary.attribution_sources
    assert "process_tree_rss" in summary.attribution_sources
    assert finish_event.details["runner_root_pid"] == 4242
    assert finish_event.details["runner_child_pids"] == [4243]
    assert finish_event.details["attribution_quality"] == "process_exact"
    assert MemorySampleWindow.BEFORE_SUBMIT.value in finish_event.details["sample_windows_observed"]
    assert MemorySampleWindow.AFTER_COMPLETION.value in finish_event.details["sample_windows_observed"]


@pytest.mark.anyio
async def test_get_result_records_backend_allocator_telemetry_and_peak_window(tmp_path: Path) -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    learning_store = LocalMemoryLearningStore(tmp_path / "memory-learning")
    service, supervisor = _build_service(
        adapter,
        memory_learning_store=learning_store,
        memory_observer=SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=8_000,
                    total_ram_mb=64_000,
                    free_ram_mb=60_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=7_500,
                    total_ram_mb=64_000,
                    free_ram_mb=59_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
        runner_memory_telemetry_reader=FakeRunnerMemoryTelemetryReader(),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"pid": 4242, "memory_telemetry_path": str(tmp_path / "telemetry.jsonl")}),
        adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    await service.get_result(job.job_id)
    runner = supervisor.get_runner("isolated-1")
    summary = learning_store.list_summaries()[0]
    events = service.log_store.list_events().events
    finish_event = next(event for event in events if event.message == "Finished best-effort job memory sampling")

    assert runner.observed_execution_peak_vram_mb == 3600
    assert summary.backend_allocator_observed_peak_vram_mb == 3600
    assert summary.attribution_quality is MemoryAttributionQuality.BACKEND_ALLOCATOR
    assert "pytorch_cuda_allocator" in summary.attribution_sources
    assert finish_event.details["backend_allocator_peak_vram_mb"] == 3600
    assert finish_event.details["sample_window"] == MemorySampleWindow.BEFORE_SUBMIT.value
    assert MemorySampleWindow.BEFORE_SUBMIT.value in finish_event.details["sample_windows_observed"]


@pytest.mark.anyio
async def test_get_result_records_memory_error_observation(tmp_path: Path) -> None:
    class FailingResultAdapter(RecordingAdapter):
        def __init__(self, models: list[ModelInfo]) -> None:
            super().__init__(models=models)
            self._job_counter = 0

        async def run_workflow(self, workflow_package, graph, inputs, options) -> EngineJob:
            del graph, options
            self._job_counter += 1
            job_id = f"job-{self._job_counter}"
            self.run_calls.append((job_id, dict(inputs)))
            return EngineJob(
                job_id=job_id,
                workflow_id=workflow_package.metadata.id,
                engine="comfyui",
                status="queued",
            )

        async def get_result(self, job_id: str) -> JobResult:
            self.result_calls.append(job_id)
            return JobResult(job_id=job_id, status="failed", error="CUDA out of memory")

    adapter = FailingResultAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    learning_store = LocalMemoryLearningStore(tmp_path / "memory-learning")
    service, supervisor = _build_service(
        adapter,
        memory_learning_store=learning_store,
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=10_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    await service.get_result(job.job_id)
    summary = learning_store.list_summaries()[0]

    assert summary.memory_error_runs == 1
    assert summary.successful_runs == 0
    assert summary.has_memory_failure is True


@pytest.mark.anyio
async def test_get_result_retries_once_after_memory_cleanup(tmp_path: Path) -> None:
    adapter = MemoryRetryAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, _ = _build_service(
        adapter,
        memory_learning_store=LocalMemoryLearningStore(tmp_path / "memory-learning"),
        memory_observer=ReleaseAwareMemoryObserver(adapter),
    )

    job = await service.run_workflow("text_to_image_v0", inputs={"prompt": "hello"}, options={})
    retry_job = await service.get_result(job.job_id)

    assert isinstance(retry_job, EngineJob)
    assert retry_job.job_id == "job-2"
    assert retry_job.status == "queued"
    assert retry_job.memory_decision is not None
    assert retry_job.memory_decision["action"] == "retry_after_memory_cleanup"
    retry_estimate = retry_job.memory_decision["workflow_estimate"]
    assert job.memory_decision is not None
    initial_estimate = job.memory_decision["workflow_estimate"]
    assert retry_estimate["process_compatibility_signature"] == initial_estimate[
        "process_compatibility_signature"
    ]
    assert retry_estimate["model_residency_signature"] == initial_estimate[
        "model_residency_signature"
    ]
    assert retry_estimate["execution_profile_signature"] == initial_estimate[
        "execution_profile_signature"
    ]
    assert retry_job.memory_status is not None
    assert retry_job.memory_status["state"] == "retrying_after_memory_cleanup"
    assert adapter.run_calls == [("job-1", {"prompt": "hello"}), ("job-2", {"prompt": "hello"})]
    assert adapter.release_memory_calls == 1
    assert service.memory_governor_metrics()["memory_retry_attempted"] == 1


@pytest.mark.anyio
async def test_get_result_does_not_repeat_memory_retry(tmp_path: Path) -> None:
    adapter = MemoryRetryAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, _ = _build_service(
        adapter,
        memory_learning_store=LocalMemoryLearningStore(tmp_path / "memory-learning"),
        memory_observer=ReleaseAwareMemoryObserver(adapter),
    )

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    retry_job = await service.get_result(job.job_id)
    repeated_result = await service.get_result(retry_job.job_id)

    assert isinstance(retry_job, EngineJob)
    assert isinstance(repeated_result, JobResult)
    assert repeated_result.status == "failed"
    assert adapter.run_calls == [("job-1", {}), ("job-2", {})]
    assert service.memory_governor_metrics()["memory_retry_attempted"] == 1
    assert service.memory_governor_metrics()["memory_retry_blocked"] == 1


@pytest.mark.anyio
async def test_progress_for_unknown_job_falls_back_to_core_runner() -> None:
    """Existing API behavior must keep working when a job has no registry entry."""
    adapter = RecordingAdapter()
    service, _ = _build_service(adapter)

    progress = await service.get_progress("never-registered")

    assert progress.status == "running"
    assert adapter.progress_calls == ["never-registered"]


@pytest.mark.anyio
async def test_run_workflow_blocked_by_validation_does_not_register_job() -> None:
    adapter = RecordingAdapter(models=[])  # missing models -> validation fails
    service, supervisor = _build_service(adapter)

    result = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    # Validation blocked submission, so no job exists and nothing was routed.
    assert hasattr(result, "valid") and not result.valid
    assert adapter.run_calls == []
    assert supervisor.job_registry.snapshot() == {}


@pytest.mark.anyio
async def test_run_workflow_queues_behind_active_noofy_job_even_when_margin_is_available() -> None:
    selected_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        RecordingAdapter(),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=10_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="selected-runner",
            compatibility_key="selected-key",
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"observed_execution_peak_vram_mb": 1200}),
        selected_adapter,
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="active-runner",
            compatibility_key="active-key",
            status=RunnerStatus.RUNNING,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-active",
        ),
        RecordingAdapter(),
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "selected-runner")

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    await asyncio.sleep(0.05)

    assert isinstance(job, EngineJob)
    assert job.status == "queued_pending_memory"
    assert job.queue_id == job.job_id
    assert job.memory_decision is not None
    assert job.memory_decision["action"] == "queue_pending_memory"
    assert job.memory_decision["reason_code"] == "active_noofy_job_queues_run"
    assert job.memory_status is not None
    assert job.memory_status["state"] == "waiting_for_active_workflow"
    assert selected_adapter.run_calls == []
    assert supervisor.job_registry.snapshot() == {}
    queued = service.workflow_run_queue_service.get(job.queue_id)
    assert queued is not None
    assert queued.attempt_count == 0


@pytest.mark.anyio
async def test_cancel_queued_workflow_run_removes_memory_queue_without_adapter_cancel() -> None:
    selected_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        RecordingAdapter(),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="selected-runner",
            compatibility_key="selected-key",
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"observed_execution_peak_vram_mb": 1200}),
        selected_adapter,
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="active-runner",
            compatibility_key="active-key",
            status=RunnerStatus.RUNNING,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-active",
        ),
        RecordingAdapter(),
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "selected-runner")
    queued = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    progress = await service.get_progress(queued.job_id)
    canceled = await service.cancel_job(queued.job_id)

    assert progress.status == "queued_pending_memory"
    assert canceled.status == "canceled"
    assert selected_adapter.cancel_calls == []
    assert await service.handoff_queued_workflow_run(queued.job_id) is None


@pytest.mark.anyio
async def test_handoff_queued_workflow_run_submits_after_memory_is_safe() -> None:
    selected_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="job-after-memory",
    )
    service, supervisor = _build_service(
        RecordingAdapter(),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="selected-runner",
            compatibility_key="selected-key",
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"observed_execution_peak_vram_mb": 1200}),
        selected_adapter,
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="active-runner",
            compatibility_key="active-key",
            status=RunnerStatus.RUNNING,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-active",
        ),
        RecordingAdapter(),
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "selected-runner")
    queued = await service.run_workflow("text_to_image_v0", inputs={"prompt": "hello"}, options={})

    supervisor.mark_runner_job_finished("active-runner", "job-active")
    service.memory_observer = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=12_000,
            free_vram_mb=8_000,
            total_ram_mb=64_000,
            free_ram_mb=50_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )
    handed_off = await service.handoff_queued_workflow_run(queued.queue_id)

    assert isinstance(handed_off, EngineJob)
    assert handed_off.status == "queued"
    assert handed_off.job_id == "job-after-memory"
    assert handed_off.queue_id == queued.queue_id
    assert selected_adapter.run_calls == [("job-after-memory", {"prompt": "hello"})]
    assert supervisor.runner_for_job("job-after-memory").runner_id == "selected-runner"
    assert await service.handoff_queued_workflow_run(queued.queue_id) is None


@pytest.mark.anyio
async def test_queued_workflow_run_submits_automatically_after_active_runner_finishes() -> None:
    selected_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="job-auto-handoff",
    )
    service, supervisor = _build_service(
        RecordingAdapter(),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="selected-runner",
            compatibility_key="selected-key",
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"observed_execution_peak_vram_mb": 1200}),
        selected_adapter,
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="active-runner",
            compatibility_key="active-key",
            status=RunnerStatus.RUNNING,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-active",
        ),
        RecordingAdapter(),
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "selected-runner")
    queued = await service.run_workflow("text_to_image_v0", inputs={"prompt": "hello"}, options={})
    service.memory_observer = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=12_000,
            free_vram_mb=8_000,
            total_ram_mb=64_000,
            free_ram_mb=50_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )

    supervisor.mark_runner_job_finished("active-runner", "job-active")
    await asyncio.sleep(0.05)

    assert service.workflow_run_queue_service.resolve(queued.queue_id).job_id == "job-auto-handoff"
    assert selected_adapter.run_calls == [("job-auto-handoff", {"prompt": "hello"})]


@pytest.mark.anyio
async def test_cancel_during_adapter_submission_registers_alias_then_cancels_canonical_job() -> None:
    selected_adapter = BlockingSubmissionAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="job-after-cancel-race",
    )
    core_adapter = RecordingAdapter()
    service, supervisor = _build_service(
        core_adapter,
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="selected-runner",
            compatibility_key="selected-key",
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"observed_execution_peak_vram_mb": 1200}),
        selected_adapter,
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="active-runner",
            compatibility_key="active-key",
            status=RunnerStatus.RUNNING,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-active",
        ),
        RecordingAdapter(),
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "selected-runner")
    queued = await service.run_workflow("text_to_image_v0", inputs={}, options={})
    service.memory_observer = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=12_000,
            free_vram_mb=8_000,
            total_ram_mb=64_000,
            free_ram_mb=50_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )
    supervisor.mark_runner_job_finished("active-runner", "job-active")
    await selected_adapter.submission_started.wait()

    canceled = await service.cancel_job(queued.queue_id)
    selected_adapter.allow_submission.set()
    await asyncio.sleep(0.05)

    assert canceled.status == "canceled"
    assert service.workflow_run_queue_service.resolve(queued.queue_id).job_id == "job-after-cancel-race"
    assert selected_adapter.cancel_calls == ["job-after-cancel-race"]
    assert core_adapter.cancel_calls == []


@pytest.mark.anyio
async def test_handoff_queued_workflow_run_preserves_original_run_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    selected_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="job-after-memory",
    )
    service, supervisor = _build_service(
        RecordingAdapter(),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="selected-runner",
            compatibility_key="selected-key",
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.GPU_LIGHT,
        ).model_copy(update={"observed_execution_peak_vram_mb": 1200}),
        selected_adapter,
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="active-runner",
            compatibility_key="active-key",
            status=RunnerStatus.RUNNING,
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            current_job_id="job-active",
        ),
        RecordingAdapter(),
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "selected-runner")

    queued = await service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "submitted prompt"},
        options={},
        output_preferences_snapshot={"result": {"auto_save": True}},
    )
    original_package = service.workflow_loader.get_package("text_to_image_v0")
    mutated_package = original_package.model_copy(
        update={"outputs": [original_package.outputs[0].model_copy(update={"node_id": "999"})]},
        deep=True,
    )
    monkeypatch.setattr(
        service.workflow_loader,
        "get_package",
        lambda workflow_id: mutated_package if workflow_id == "text_to_image_v0" else original_package,
    )

    supervisor.mark_runner_job_finished("active-runner", "job-active")
    service.memory_observer = StaticMemoryObserver(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=12_000,
            free_vram_mb=8_000,
            total_ram_mb=64_000,
            free_ram_mb=50_000,
            memory_pressure=MemoryPressureLevel.LOW,
        )
    )
    handed_off = await service.handoff_queued_workflow_run(queued.queue_id)

    assert isinstance(handed_off, EngineJob)
    snapshot = service._job_run_snapshots["job-after-memory"]
    assert snapshot.values["prompt"] == "submitted prompt"
    assert snapshot.output_preferences["result"].auto_save is True
    assert snapshot.output_widgets[0].node_id == "9"


@pytest.mark.anyio
async def test_gallery_capture_failure_does_not_break_completed_result() -> None:
    adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="job-gallery-fails",
    )
    service, _ = _build_service(adapter)
    service.gallery_capture_service = FailingGalleryCapture()

    job = await service.run_workflow(
        "text_to_image_v0",
        inputs={"prompt": "hello"},
        options={},
        output_preferences_snapshot={"result": {"auto_save": True}},
    )
    result = await service.get_result(job.job_id)

    assert isinstance(result, JobResult)
    assert result.status == "completed"
    logs = service.list_logs(level="error").events
    assert any(event.message == "Gallery Auto Save could not be scheduled" for event in logs)


@pytest.mark.anyio
async def test_run_workflow_allows_uncertain_memory_estimate_without_idle_cleanup() -> None:
    selected_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ]
    )
    service, supervisor = _build_service(
        RecordingAdapter(),
        memory_observer=StaticMemoryObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=10_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )
    supervisor.upsert_runner(
        _isolated_descriptor(
            runner_id="selected-runner",
            compatibility_key="selected-key",
            status=RunnerStatus.READY,
            memory_class=RunnerMemoryClass.UNKNOWN,
        ),
        selected_adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "selected-runner")

    job = await service.run_workflow("text_to_image_v0", inputs={}, options={})

    assert isinstance(job, EngineJob)
    assert job.status == "queued"
    assert job.memory_decision is not None
    assert job.memory_decision["action"] == "start_co_resident"
    assert job.memory_decision["reason_code"] == "no_resident_runners"
    assert job.memory_status is not None
    assert job.memory_status["state"] == "ready_warm_co_resident"
    assert selected_adapter.run_calls == [("job-1", {})]
    assert supervisor.job_registry.snapshot() == {"job-1": "selected-runner"}


@pytest.mark.anyio
async def test_engine_service_routes_bound_workflow_to_isolated_runner() -> None:
    core_adapter = RecordingAdapter(models=[])
    isolated_adapter = RecordingAdapter(
        models=[
            ModelInfo(
                folder="checkpoints",
                filename="v1-5-pruned-emaonly-fp16.safetensors",
            )
        ],
        next_job_id="job-isolated",
    )
    service, supervisor = _build_service(core_adapter)
    supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="isolated-1",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:9001",
            ws_url="ws://127.0.0.1:9001/ws",
            fingerprint="sha256:" + ("a" * 64),
            status=RunnerStatus.READY,
        ),
        isolated_adapter,
    )
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    validation = await service.validate_workflow("text_to_image_v0")
    job = await service.run_workflow("text_to_image_v0", inputs={"prompt": "hello"}, options={})
    await service.get_progress(job.job_id)

    assert validation.valid
    assert job.job_id == "job-isolated"
    assert isolated_adapter.run_calls == [("job-isolated", {"prompt": "hello"})]
    assert isolated_adapter.progress_calls == ["job-isolated"]
    assert core_adapter.run_calls == []
    assert supervisor.runner_for_job("job-isolated").runner_id == "isolated-1"
