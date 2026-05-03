from pathlib import Path
from typing import Any
from datetime import UTC, datetime

import pytest

from app.engine.diagnostics import LogStore
from app.engine.models import EngineJob, JobProgress, JobResult, ModelInfo
from app.engine.service import EngineService
from app.runtime.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    DuplicateJobRegistrationError,
    JobRunnerNotFoundError,
    JobRunnerRegistry,
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
from app.workflows.validator import WorkflowPackageValidator


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
        self._next_job_id = next_job_id

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        self.endpoint_updates.append((base_url, ws_url))

    async def list_available_models(self) -> list[ModelInfo]:
        return self.models

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

    package = WorkflowPackageLoader(Path("app/workflows/packages")).get_package("text_to_image_v0")
    assert supervisor.acquire_runner(package).runner_id == "isolated-1"
    assert supervisor.runner_for_workflow("text_to_image_v0").runner_id == "isolated-1"


def test_supervisor_acquires_warm_bound_workflow_runner() -> None:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())
    descriptor = _isolated_descriptor(status=RunnerStatus.IDLE_WARM)
    supervisor.upsert_runner(descriptor, RecordingAdapter())
    supervisor.bind_workflow_runner("text_to_image_v0", "isolated-1")

    package = WorkflowPackageLoader(Path("app/workflows/packages")).get_package("text_to_image_v0")

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

    package = WorkflowPackageLoader(Path("app/workflows/packages")).get_package("text_to_image_v0")
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

    package = WorkflowPackageLoader(Path("app/workflows/packages")).get_package("text_to_image_v0")
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


def _build_service(adapter: RecordingAdapter) -> tuple[EngineService, RunnerSupervisor]:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_core_descriptor(), adapter)
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
    )
    return service, supervisor


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
