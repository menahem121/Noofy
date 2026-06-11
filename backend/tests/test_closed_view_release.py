"""Closed-view cooldown release of isolated workflow runners.

Closing the last workflow view (frontend tab lease) starts the supervisor's
closed-view cooldown. After the cooldown the runner lifecycle service may stop
the idle isolated runner — never the core runner, and never while work is
active, queued, or the workflow view was reopened.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.diagnostics import LogStore
from app.runtime.runners.lifecycle_service import WorkflowRunnerLifecycleService
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    QueuedRunnerStartKind,
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    RunnerStatus,
    RunnerSupervisor,
)

COOLDOWN_SECONDS = 30


class RecordingAdapter:
    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        del base_url, ws_url


class FakeRunnerCoordinator:
    """Mimics RunnerProcessCoordinator.stop_runner registry side effects."""

    def __init__(
        self,
        supervisor: RunnerSupervisor,
        *,
        stop_status: RunnerStatus = RunnerStatus.STOPPED,
        stop_error: Exception | None = None,
    ) -> None:
        self.supervisor = supervisor
        self.stop_status = stop_status
        self.stop_error = stop_error
        self.stop_calls: list[str] = []

    async def stop_runner(self, runner_id: str) -> SimpleNamespace:
        self.stop_calls.append(runner_id)
        if self.stop_error is not None:
            raise self.stop_error
        self.supervisor.update_runner_status(runner_id, self.stop_status)
        if self.stop_status is RunnerStatus.STOPPED:
            self.supervisor.unbind_runner(runner_id)
        return SimpleNamespace(
            runner_id=runner_id,
            status=self.stop_status,
            base_url="http://127.0.0.1:9001",
            ws_url=None,
            pid=None,
            error=None,
        )


class RecordingMemoryService:
    def __init__(self) -> None:
        self.metrics: list[str] = []

    def record_metric(self, name: str) -> None:
        self.metrics.append(name)


def _isolated_descriptor(
    runner_id: str = "isolated-1",
    *,
    status: RunnerStatus = RunnerStatus.READY,
    current_job_id: str | None = None,
) -> RunnerDescriptor:
    return RunnerDescriptor(
        runner_id=runner_id,
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=status,
        runner_process_compatibility_key="runner-key-a",
        memory_class=RunnerMemoryClass.GPU_HEAVY,
        current_job_id=current_job_id,
    )


def _core_descriptor() -> RunnerDescriptor:
    return RunnerDescriptor(
        runner_id=CORE_RUNNER_ID,
        kind=RunnerKind.CORE_COMFYUI,
        base_url="http://127.0.0.1:8188",
        ws_url="ws://127.0.0.1:8188/ws",
        fingerprint=CORE_RUNNER_FINGERPRINT,
        status=RunnerStatus.IDLE,
    )


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


def _workflow_loader() -> SimpleNamespace:
    return SimpleNamespace(
        get_package=lambda workflow_id: SimpleNamespace(
            metadata=SimpleNamespace(id=workflow_id)
        )
    )


def _build(
    *,
    clock: _Clock | None = None,
    cooldown_seconds: float = COOLDOWN_SECONDS,
    stop_status: RunnerStatus = RunnerStatus.STOPPED,
    stop_error: Exception | None = None,
    has_pending_workflow_runs=None,
    auto_release_enabled: bool | None = True,
    retry_seconds: float = 10.0,
    real_clock: bool = False,
    lease_ttl_seconds: float | None = None,
    lease_sweep_interval_seconds: float | None = None,
) -> tuple[WorkflowRunnerLifecycleService, RunnerSupervisor, FakeRunnerCoordinator, _Clock]:
    clock = clock or _Clock()
    supervisor = RunnerSupervisor(
        closed_view_cooldown_seconds=cooldown_seconds,
        now=None if real_clock else clock,
    )
    coordinator = FakeRunnerCoordinator(
        supervisor, stop_status=stop_status, stop_error=stop_error
    )
    service = WorkflowRunnerLifecycleService(
        workflow_loader=_workflow_loader(),
        runner_supervisor=supervisor,
        log_store=LogStore(),
        runner_process_coordinator=coordinator,
        memory_service=RecordingMemoryService(),
        has_pending_workflow_runs=has_pending_workflow_runs,
        closed_view_auto_release_enabled=auto_release_enabled,
        closed_view_release_retry_seconds=retry_seconds,
        workflow_lease_ttl_seconds=lease_ttl_seconds,
        workflow_lease_sweep_interval_seconds=lease_sweep_interval_seconds,
    )
    return service, supervisor, coordinator, clock


def _open_and_close_lease(
    service: WorkflowRunnerLifecycleService,
    supervisor: RunnerSupervisor,
    *,
    workflow_id: str = "workflow-a",
    runner_id: str = "isolated-1",
) -> None:
    supervisor.upsert_runner(_isolated_descriptor(runner_id), RecordingAdapter())
    supervisor.bind_workflow_runner(workflow_id, runner_id)
    opened = service.open_workflow_runner_lease(workflow_id)
    assert opened["lease_id"] is not None
    closed = service.close_workflow_runner_lease(workflow_id, opened["lease_id"])
    assert closed["runner"]["open_workflow_lease_count"] == 0
    assert closed["runner"]["closed_view_cooldown_expires_at"] is not None


# ----------------------------------------------------------------------
# Idle close: no immediate release
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_closing_last_view_does_not_release_before_cooldown() -> None:
    service, supervisor, coordinator, _clock = _build()

    _open_and_close_lease(service, supervisor)

    # Closing only schedules a deferred check; nothing is stopped now.
    assert coordinator.stop_calls == []
    assert supervisor.get_runner("isolated-1").status is RunnerStatus.IDLE
    assert len(service._closed_view_release_tasks) == 1

    # An early sweep finds no expired candidate either.
    assert await service.release_closed_view_runners() == []
    assert coordinator.stop_calls == []

    await service.shutdown()
    assert service._closed_view_release_tasks == {}


def test_closing_last_view_without_event_loop_keeps_runner() -> None:
    service, supervisor, coordinator, _clock = _build()

    _open_and_close_lease(service, supervisor)

    assert coordinator.stop_calls == []
    assert supervisor.get_runner("isolated-1").status is RunnerStatus.IDLE
    # No loop: deferred release could not be scheduled; eviction remains
    # opportunistic through the Memory Governor.
    assert service._closed_view_release_tasks == {}


# ----------------------------------------------------------------------
# Release after cooldown
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_isolated_runner_released_after_cooldown_expires() -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results == [
        {
            "runner_id": "isolated-1",
            "status": "released",
            "reason": "closed_view_cooldown_expired",
        }
    ]
    assert coordinator.stop_calls == ["isolated-1"]
    released = supervisor.get_runner("isolated-1")
    assert released.status is RunnerStatus.EVICTED_AFTER_COOLDOWN
    assert released.closed_view_cooldown_expires_at is None
    assert released.model_residency_signature is None
    assert supervisor.workflows_bound_to_runner("isolated-1") == []
    assert (
        "idle_runner_released_after_closed_view_cooldown"
        in service.memory_service.metrics
    )

    # A later sweep has nothing left to do.
    assert await service.release_closed_view_runners() == []
    assert coordinator.stop_calls == ["isolated-1"]


@pytest.mark.anyio
async def test_scheduled_release_stops_runner_end_to_end() -> None:
    # Real-clock variant proving the close-lease call alone leads to release.
    service, supervisor, coordinator, _clock = _build(
        cooldown_seconds=0.05, real_clock=True
    )
    _open_and_close_lease(service, supervisor)

    await asyncio.sleep(0.4)

    assert coordinator.stop_calls == ["isolated-1"]
    assert (
        supervisor.get_runner("isolated-1").status
        is RunnerStatus.EVICTED_AFTER_COOLDOWN
    )
    await service.shutdown()


@pytest.mark.anyio
async def test_scheduled_release_retries_after_active_job_finishes() -> None:
    service, supervisor, coordinator, _clock = _build(
        cooldown_seconds=0.01,
        retry_seconds=0.05,
        real_clock=True,
    )
    _open_and_close_lease(service, supervisor)
    supervisor.mark_runner_job_started(
        "isolated-1", "job-active", workflow_id="workflow-a"
    )

    await asyncio.sleep(0.12)
    assert coordinator.stop_calls == []

    supervisor.mark_runner_job_finished("isolated-1", "job-active")
    await asyncio.sleep(0.2)

    assert coordinator.stop_calls == ["isolated-1"]
    assert (
        supervisor.get_runner("isolated-1").status
        is RunnerStatus.EVICTED_AFTER_COOLDOWN
    )
    await service.shutdown()


@pytest.mark.anyio
async def test_scheduled_release_retries_after_output_stream_closes() -> None:
    service, supervisor, coordinator, _clock = _build(
        cooldown_seconds=0.01,
        retry_seconds=0.05,
        real_clock=True,
    )
    _open_and_close_lease(service, supervisor)
    supervisor.acquire_output_stream_lease("isolated-1")

    await asyncio.sleep(0.12)
    assert coordinator.stop_calls == []

    supervisor.release_output_stream_lease("isolated-1")
    await asyncio.sleep(0.2)

    assert coordinator.stop_calls == ["isolated-1"]
    await service.shutdown()


@pytest.mark.anyio
async def test_release_hands_off_queued_runner_start() -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)
    supervisor.enqueue_runner_start(
        workflow_id="workflow-waiting",
        kind=QueuedRunnerStartKind.PENDING_MEMORY,
        queued_behind_runner_id="isolated-1",
        reason="memory_pressure",
    )

    handoffs: list[str | None] = []

    async def record_handoff(*, released_runner_id: str | None = None):
        handoffs.append(released_runner_id)
        return None

    service.handoff_next_queued_runner_start = record_handoff

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results[0]["status"] == "released"
    assert coordinator.stop_calls == ["isolated-1"]
    assert handoffs == ["isolated-1"]


# ----------------------------------------------------------------------
# Protection: reopened views, active and queued work
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_reopened_view_prevents_release() -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)

    reopened = service.open_workflow_runner_lease("workflow-a")
    assert reopened["lease_id"] is not None

    clock.advance(COOLDOWN_SECONDS + 1)
    assert await service.release_closed_view_runners() == []
    assert coordinator.stop_calls == []
    assert supervisor.get_runner("isolated-1").status is RunnerStatus.IDLE_WARM


@pytest.mark.anyio
async def test_active_job_prevents_release() -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)
    supervisor.mark_runner_job_started("isolated-1", "job-9", workflow_id="workflow-a")

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results == [
        {"runner_id": "isolated-1", "status": "skipped", "reason": "active_job"}
    ]
    assert coordinator.stop_calls == []
    assert supervisor.get_runner("isolated-1").current_job_id == "job-9"


@pytest.mark.anyio
async def test_queued_runner_start_for_bound_workflow_prevents_release() -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)
    supervisor.enqueue_runner_start(
        workflow_id="workflow-a",
        kind=QueuedRunnerStartKind.PENDING_SWITCH,
        reason="switch_pending",
    )

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results == [
        {
            "runner_id": "isolated-1",
            "status": "skipped",
            "reason": "queued_runner_start_pending",
        }
    ]
    assert coordinator.stop_calls == []


@pytest.mark.anyio
async def test_queued_workflow_run_prevents_release() -> None:
    service, supervisor, coordinator, clock = _build(
        has_pending_workflow_runs=lambda workflow_id: workflow_id == "workflow-a",
    )
    _open_and_close_lease(service, supervisor)

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results == [
        {
            "runner_id": "isolated-1",
            "status": "skipped",
            "reason": "queued_workflow_run_pending",
        }
    ]
    assert coordinator.stop_calls == []


@pytest.mark.anyio
async def test_output_stream_lease_prevents_release() -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)
    supervisor.acquire_output_stream_lease("isolated-1")

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results == [
        {
            "runner_id": "isolated-1",
            "status": "skipped",
            "reason": "output_stream_active",
        }
    ]
    assert coordinator.stop_calls == []


@pytest.mark.anyio
async def test_live_safety_recheck_rolls_back_when_output_stream_starts_during_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)
    clock.advance(COOLDOWN_SECONDS + 1)

    reserve = supervisor.reserve_runner_for_eviction

    def reserve_then_open_output_stream(runner_id: str):
        reservation = reserve(runner_id)
        assert reservation is not None
        supervisor.acquire_output_stream_lease(runner_id)
        return reservation

    monkeypatch.setattr(
        supervisor,
        "reserve_runner_for_eviction",
        reserve_then_open_output_stream,
    )

    results = await service.release_closed_view_runners()

    assert results == [
        {
            "runner_id": "isolated-1",
            "status": "skipped",
            "reason": "output_stream_active",
        }
    ]
    assert coordinator.stop_calls == []
    protected = supervisor.get_runner("isolated-1")
    assert protected.reservation_token is None
    assert protected.status is RunnerStatus.IDLE


@pytest.mark.anyio
async def test_runtime_activation_defers_release() -> None:
    service, supervisor, coordinator, clock = _build()
    _open_and_close_lease(service, supervisor)
    assert supervisor.begin_runtime_activation() == []

    clock.advance(COOLDOWN_SECONDS + 1)
    assert await service.release_closed_view_runners() == []
    assert coordinator.stop_calls == []


# ----------------------------------------------------------------------
# Core runner safety
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_core_runner_is_never_released_by_closed_view_cooldown() -> None:
    service, supervisor, coordinator, clock = _build()
    supervisor.register_core_runner(_core_descriptor(), RecordingAdapter())

    # Even with a lease opened and closed directly against the core runner,
    # the cooldown sweep must ignore it.
    lease_id = supervisor.open_workflow_lease("workflow-core", CORE_RUNNER_ID)
    supervisor.close_workflow_lease(lease_id)
    assert (
        supervisor.get_runner(CORE_RUNNER_ID).closed_view_cooldown_expires_at
        is not None
    )

    clock.advance(COOLDOWN_SECONDS + 1)
    assert supervisor.expired_closed_view_runners() == []
    assert await service.release_closed_view_runners() == []
    assert coordinator.stop_calls == []

    # Direct attempts are refused as well.
    outcome = await service._try_release_closed_view_runner(
        supervisor.get_runner(CORE_RUNNER_ID)
    )
    assert outcome["status"] == "skipped"
    assert outcome["reason"] == "not_isolated_runner"
    assert coordinator.stop_calls == []


# ----------------------------------------------------------------------
# Failure handling and configuration
# ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_failed_stop_marks_release_failed_and_keeps_cooldown() -> None:
    service, supervisor, coordinator, clock = _build(stop_status=RunnerStatus.FAILED)
    _open_and_close_lease(service, supervisor)

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results == [
        {
            "runner_id": "isolated-1",
            "status": "failed",
            "reason": "runner_stop_status_failed",
        }
    ]
    failed = supervisor.get_runner("isolated-1")
    assert failed.status is RunnerStatus.RELEASE_FAILED
    # The cooldown stays expired so a later sweep (or memory pressure) retries.
    assert failed.closed_view_cooldown_expires_at is not None

    # RELEASE_FAILED is retryable: a follow-up sweep with a working stop path
    # releases the runner.
    coordinator.stop_status = RunnerStatus.STOPPED
    retry = await service.release_closed_view_runners()
    assert retry[0]["status"] == "released"
    assert (
        supervisor.get_runner("isolated-1").status
        is RunnerStatus.EVICTED_AFTER_COOLDOWN
    )


@pytest.mark.anyio
async def test_scheduled_release_retries_after_stop_failure() -> None:
    service, supervisor, coordinator, _clock = _build(
        cooldown_seconds=0.01,
        retry_seconds=0.05,
        stop_status=RunnerStatus.FAILED,
        real_clock=True,
    )
    _open_and_close_lease(service, supervisor)

    await asyncio.sleep(0.12)
    assert coordinator.stop_calls
    assert supervisor.get_runner("isolated-1").status is RunnerStatus.RELEASE_FAILED

    coordinator.stop_status = RunnerStatus.STOPPED
    await asyncio.sleep(0.2)

    assert (
        supervisor.get_runner("isolated-1").status
        is RunnerStatus.EVICTED_AFTER_COOLDOWN
    )
    await service.shutdown()


@pytest.mark.anyio
async def test_scheduled_release_stops_retrying_for_unreleasable_runner() -> None:
    service, supervisor, coordinator, _clock = _build(
        cooldown_seconds=0.01,
        retry_seconds=0.05,
        real_clock=True,
    )
    _open_and_close_lease(service, supervisor)
    supervisor.update_runner_status("isolated-1", RunnerStatus.FAILED)

    await asyncio.sleep(0.3)

    # A crashed runner can never be claimed by the eviction reservation, so
    # the deferred release task ends instead of polling forever.
    assert coordinator.stop_calls == []
    assert service._closed_view_release_tasks == {}
    assert supervisor.get_runner("isolated-1").status is RunnerStatus.FAILED


@pytest.mark.anyio
async def test_stop_error_marks_release_failed() -> None:
    service, supervisor, coordinator, clock = _build(
        stop_error=RuntimeError("process refused to exit"),
    )
    _open_and_close_lease(service, supervisor)

    clock.advance(COOLDOWN_SECONDS + 1)
    results = await service.release_closed_view_runners()

    assert results[0]["status"] == "failed"
    assert results[0]["reason"] == "runner_stop_error"
    assert results[0]["error"] == "process refused to exit"
    assert supervisor.get_runner("isolated-1").status is RunnerStatus.RELEASE_FAILED


@pytest.mark.anyio
async def test_auto_release_disabled_keeps_runner() -> None:
    service, supervisor, coordinator, clock = _build(auto_release_enabled=False)
    _open_and_close_lease(service, supervisor)

    assert service._closed_view_release_tasks == {}
    clock.advance(COOLDOWN_SECONDS + 1)
    assert await service.release_closed_view_runners() == []
    assert coordinator.stop_calls == []


@pytest.mark.anyio
async def test_workflow_lease_sweeper_expires_missing_heartbeat_and_starts_cooldown() -> None:
    service, supervisor, _coordinator, _clock = _build(
        real_clock=True,
        lease_ttl_seconds=0.01,
        lease_sweep_interval_seconds=0.01,
    )
    supervisor.upsert_runner(_isolated_descriptor(), RecordingAdapter())
    supervisor.bind_workflow_runner("workflow-a", "isolated-1")

    opened = service.open_workflow_runner_lease("workflow-a")
    assert opened["lease_id"]
    assert service._workflow_lease_sweeper_task is not None

    await asyncio.sleep(0.08)

    runner = supervisor.get_runner("isolated-1")
    assert runner.open_workflow_lease_count == 0
    assert runner.closed_view_cooldown_expires_at is not None
    assert any(
        event.message == "Workflow view lease expired without heartbeat"
        for event in service.log_store.list_events().events
    )

    reopened = service.open_workflow_runner_lease("workflow-a")
    assert reopened["lease_id"]
    await asyncio.sleep(0.08)

    reopened_runner = supervisor.get_runner("isolated-1")
    assert reopened_runner.open_workflow_lease_count == 0
    expiry_events = [
        event
        for event in service.log_store.list_events().events
        if event.message == "Workflow view lease expired without heartbeat"
    ]
    assert len(expiry_events) == 2
    await service.shutdown()
    assert service._workflow_lease_sweeper_task is None


# ----------------------------------------------------------------------
# Supervisor primitives
# ----------------------------------------------------------------------


def test_expired_closed_view_runners_respects_cooldown_and_leases() -> None:
    clock = _Clock()
    supervisor = RunnerSupervisor(
        closed_view_cooldown_seconds=COOLDOWN_SECONDS, now=clock
    )
    supervisor.upsert_runner(_isolated_descriptor(), RecordingAdapter())
    lease_id = supervisor.open_workflow_lease("workflow-a", "isolated-1")

    assert supervisor.expired_closed_view_runners() == []

    supervisor.close_workflow_lease(lease_id)
    assert supervisor.expired_closed_view_runners() == []
    assert supervisor.closed_view_cooldown_remaining_seconds("isolated-1") == float(
        COOLDOWN_SECONDS
    )

    clock.advance(COOLDOWN_SECONDS + 1)
    expired = supervisor.expired_closed_view_runners()
    assert [runner.runner_id for runner in expired] == ["isolated-1"]
    assert supervisor.closed_view_cooldown_remaining_seconds("isolated-1") == 0.0


def test_workflows_bound_to_runner_lists_bindings() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(_isolated_descriptor(), RecordingAdapter())
    supervisor.bind_workflow_runner("workflow-b", "isolated-1")
    supervisor.bind_workflow_runner("workflow-a", "isolated-1")

    assert supervisor.workflows_bound_to_runner("isolated-1") == [
        "workflow-a",
        "workflow-b",
    ]
    supervisor.unbind_workflow_runner("workflow-a")
    assert supervisor.workflows_bound_to_runner("isolated-1") == ["workflow-b"]


def test_workflow_lease_cannot_be_closed_through_another_workflow() -> None:
    supervisor = RunnerSupervisor()
    supervisor.upsert_runner(_isolated_descriptor(), RecordingAdapter())
    lease_id = supervisor.open_workflow_lease("workflow-a", "isolated-1")

    assert (
        supervisor.close_workflow_lease(lease_id, workflow_id="workflow-b") is None
    )
    protected = supervisor.get_runner("isolated-1")
    assert protected.open_workflow_lease_count == 1
    assert protected.open_workflow_lease_ids == [lease_id]


def test_workflow_lease_heartbeat_service_self_heals_unknown_leases() -> None:
    service, supervisor, _coordinator, _clock = _build()
    supervisor.upsert_runner(_isolated_descriptor(), RecordingAdapter())
    supervisor.bind_workflow_runner("workflow-a", "isolated-1")
    opened = service.open_workflow_runner_lease("workflow-a")

    active = service.heartbeat_workflow_runner_lease("workflow-a", opened["lease_id"])
    unknown = service.heartbeat_workflow_runner_lease("workflow-b", opened["lease_id"])

    assert active["status"] == "active"
    assert unknown == {
        "workflow_id": "workflow-b",
        "status": "lease_not_found",
        "lease_id": opened["lease_id"],
        "runner": None,
    }
