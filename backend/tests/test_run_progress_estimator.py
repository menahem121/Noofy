from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.engine.models import JobProgress, JobResult
from app.runtime.runners.supervisor import RunnerDescriptor, RunnerKind
from app.runs.job_service import RunJobService
from app.runs.progress_estimator import (
    PROGRESS_SCALE,
    ProgressTimingKey,
    ProgressTimingStore,
    WorkflowProgressEstimator,
)
from app.runs.result_service import RunResultService


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_progress_timing_store_bounds_history_and_recovers_from_corrupt_file(tmp_path: Path) -> None:
    log_store = LogStore()
    store = ProgressTimingStore(tmp_path, log_store=log_store)
    key = _key()

    for duration in [1, 2, 3, 4]:
        store.record_loading_duration(key, duration)
    for duration in [10, 11, 12, 13, 14, 15]:
        store.record_running_duration(key, duration)

    record = store.read(key)
    assert record.loading_durations_seconds == [2.0, 3.0, 4.0]
    assert record.running_durations_seconds == [11.0, 12.0, 13.0, 14.0, 15.0]

    path = next(tmp_path.glob("*.json"))
    path.write_text("{not-json", encoding="utf-8")

    recovered = store.read(key)
    assert recovered.loading_durations_seconds == []
    assert recovered.running_durations_seconds == []
    assert any(
        event.message == "Workflow progress timing history could not be read"
        for event in log_store.list_events().events
    )


def test_progress_timing_key_uses_profile_signals_without_prompt_text() -> None:
    key = _key(
        workflow_id="workflow-a",
        model_residency_signature="sha256:model",
        execution_profile_signature="sha256:execution",
    )

    payload = key.payload()

    assert "prompt" not in json.dumps(payload).lower()
    assert payload["workflow_id"] == "workflow-a"
    assert payload["model_residency_signature"] == "sha256:model"
    assert payload["execution_profile_signature"] == "sha256:execution"
    assert key.digest.startswith("sha256:")


def test_cold_run_uses_loading_history_and_does_not_pollute_running_history(tmp_path: Path) -> None:
    clock = FakeClock()
    store = ProgressTimingStore(tmp_path)
    key = _key()
    store.record_loading_duration(key, 9)
    store.record_loading_duration(key, 12)
    estimator = WorkflowProgressEstimator(timing_store=store, now=clock)
    estimator.register_job(
        job_id="job-1",
        workflow_id=key.workflow_id,
        engine=key.engine,
        runner=_runner(),
        machine_profile_id=key.machine_profile_id,
        model_residency_signature=key.model_residency_signature,
        execution_profile_signature=key.execution_profile_signature,
        warm_model_expected=False,
    )

    clock.advance(6)
    loading = estimator.decorate(JobProgress(job_id="job-1", status="running"))

    assert loading.estimate is not None
    assert loading.estimate.phase == "loading_model"
    assert loading.estimate.source == "loading_history"
    assert loading.estimate.history_count == 2
    assert loading.value is not None and loading.value > 50

    clock.advance(2)
    real = estimator.decorate(JobProgress(job_id="job-1", status="running", value=1, max=10))

    assert real.estimate is not None
    assert real.estimate.phase == "executing"
    assert real.estimate.source == "real_engine_progress"

    clock.advance(10)
    estimator.finalize_result(JobResult(job_id="job-1", status="completed"))

    record = store.read(key)
    assert record.loading_durations_seconds[-1] == 8.0
    assert record.running_durations_seconds == []


def test_warm_run_uses_and_records_running_history(tmp_path: Path) -> None:
    clock = FakeClock()
    store = ProgressTimingStore(tmp_path)
    key = _key()
    for duration in [20, 25, 30]:
        store.record_running_duration(key, duration)
    estimator = WorkflowProgressEstimator(timing_store=store, now=clock)
    estimator.register_job(
        job_id="job-1",
        workflow_id=key.workflow_id,
        engine=key.engine,
        runner=_runner(model_residency_signature="sha256:model"),
        machine_profile_id=key.machine_profile_id,
        model_residency_signature=key.model_residency_signature,
        execution_profile_signature=key.execution_profile_signature,
        warm_model_expected=True,
    )

    clock.advance(10)
    progress = estimator.decorate(JobProgress(job_id="job-1", status="running"))

    assert progress.estimate is not None
    assert progress.estimate.phase == "executing"
    assert progress.estimate.source == "running_history"
    assert progress.estimate.history_count == 3
    assert progress.value is not None and progress.value >= 80

    clock.advance(22)
    estimator.finalize_result(JobResult(job_id="job-1", status="completed"))

    record = store.read(key)
    assert record.loading_durations_seconds == []
    assert record.running_durations_seconds[-1] == 32.0


def test_estimated_progress_is_monotonic_and_completion_is_capped_until_result_ready(tmp_path: Path) -> None:
    clock = FakeClock()
    estimator = WorkflowProgressEstimator(
        timing_store=ProgressTimingStore(tmp_path),
        now=clock,
    )
    key = _key()
    estimator.register_job(
        job_id="job-1",
        workflow_id=key.workflow_id,
        engine=key.engine,
        runner=_runner(),
        machine_profile_id=key.machine_profile_id,
        model_residency_signature=key.model_residency_signature,
        execution_profile_signature=key.execution_profile_signature,
        warm_model_expected=True,
    )

    clock.advance(5)
    first = estimator.decorate(JobProgress(job_id="job-1", status="running", value=8, max=10))
    clock.advance(1)
    second = estimator.decorate(JobProgress(job_id="job-1", status="running", value=1, max=10))
    completed_before_result = estimator.decorate(JobProgress(job_id="job-1", status="completed"))

    assert first.value is not None
    assert second.value is not None
    assert second.value >= first.value
    assert completed_before_result.status == "running"
    assert completed_before_result.value is not None
    assert completed_before_result.value < PROGRESS_SCALE
    assert completed_before_result.message == "Saving result..."

    estimator.finalize_result(JobResult(job_id="job-1", status="completed"))
    completed_after_result = estimator.decorate(
        JobProgress(job_id="job-1", status="completed"),
        final_result_ready=True,
    )

    assert completed_after_result.status == "completed"
    assert completed_after_result.value == PROGRESS_SCALE
    assert completed_after_result.max == PROGRESS_SCALE


def test_slow_overrun_keeps_progress_moving_with_clear_message(tmp_path: Path) -> None:
    clock = FakeClock()
    store = ProgressTimingStore(tmp_path)
    key = _key()
    store.record_loading_duration(key, 4)
    estimator = WorkflowProgressEstimator(timing_store=store, now=clock)
    estimator.register_job(
        job_id="job-1",
        workflow_id=key.workflow_id,
        engine=key.engine,
        runner=_runner(),
        machine_profile_id=key.machine_profile_id,
        model_residency_signature=key.model_residency_signature,
        execution_profile_signature=key.execution_profile_signature,
        warm_model_expected=False,
    )

    clock.advance(8)
    progress = estimator.decorate(JobProgress(job_id="job-1", status="running"))

    assert progress.estimate is not None
    assert progress.estimate.slower_than_expected is True
    assert progress.value is not None and progress.value > 50
    assert progress.message == "Loading model... The local engine is still working."


def test_terminal_contexts_are_bounded_and_failed_jobs_are_discarded(tmp_path: Path) -> None:
    clock = FakeClock()
    key = _key()
    estimator = WorkflowProgressEstimator(
        timing_store=ProgressTimingStore(tmp_path),
        now=clock,
        terminal_context_limit=1,
    )
    estimator.register_job(
        job_id="job-failed",
        workflow_id=key.workflow_id,
        engine=key.engine,
        runner=_runner(),
        machine_profile_id=key.machine_profile_id,
        model_residency_signature=key.model_residency_signature,
        execution_profile_signature=key.execution_profile_signature,
        warm_model_expected=True,
    )

    estimator.finalize_result(JobResult(job_id="job-failed", status="failed"))
    failed = estimator.decorate(JobProgress(job_id="job-failed", status="completed"))

    assert failed.value is None
    assert failed.estimate is None

    for job_id in ["job-1", "job-2"]:
        estimator.register_job(
            job_id=job_id,
            workflow_id=key.workflow_id,
            engine=key.engine,
            runner=_runner(),
            machine_profile_id=key.machine_profile_id,
            model_residency_signature=key.model_residency_signature,
            execution_profile_signature=key.execution_profile_signature,
            warm_model_expected=True,
        )
        clock.advance(1)
        estimator.finalize_result(JobResult(job_id=job_id, status="completed"))

    pruned = estimator.decorate(JobProgress(job_id="job-1", status="completed"))
    retained = estimator.decorate(JobProgress(job_id="job-2", status="completed"))

    assert pruned.value is None
    assert pruned.estimate is None
    assert retained.value == PROGRESS_SCALE
    assert retained.estimate is not None


@pytest.mark.anyio
async def test_run_job_service_caps_raw_completion_until_terminal_result_is_cached(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = _Adapter()
    supervisor = _supervisor(adapter)
    estimator = WorkflowProgressEstimator(
        timing_store=ProgressTimingStore(tmp_path),
        now=clock,
    )
    key = _key()
    estimator.register_job(
        job_id="job-1",
        workflow_id=key.workflow_id,
        engine=key.engine,
        runner=_runner(),
        machine_profile_id=key.machine_profile_id,
        model_residency_signature=key.model_residency_signature,
        execution_profile_signature=key.execution_profile_signature,
        warm_model_expected=True,
    )
    supervisor.register_job("job-1", "core")
    job_service = RunJobService(
        runner_supervisor=supervisor,
        log_store=LogStore(),
        progress_estimator=estimator,
    )
    adapter.progress = JobProgress(job_id="job-1", status="completed")

    capped = await job_service.get_progress("job-1")

    assert capped.status == "running"
    assert capped.value is not None and capped.value < PROGRESS_SCALE
    assert capped.message == "Saving result..."
    assert capped.estimate is not None
    assert capped.estimate.source == "real_engine_progress"

    job_service.terminal_job_progress = lambda job_id: JobProgress(
        job_id=job_id,
        status="completed",
    )
    terminal = await job_service.get_progress("job-1")

    assert terminal.status == "completed"
    assert terminal.value == PROGRESS_SCALE


@pytest.mark.anyio
async def test_progress_event_stream_fetches_result_from_capped_saving_phase(tmp_path: Path) -> None:
    clock = FakeClock()
    adapter = _Adapter()
    supervisor = _supervisor(adapter)
    estimator = WorkflowProgressEstimator(
        timing_store=ProgressTimingStore(tmp_path),
        now=clock,
    )
    key = _key()
    estimator.register_job(
        job_id="job-1",
        workflow_id=key.workflow_id,
        engine=key.engine,
        runner=_runner(),
        machine_profile_id=key.machine_profile_id,
        model_residency_signature=key.model_residency_signature,
        execution_profile_signature=key.execution_profile_signature,
        warm_model_expected=True,
    )
    supervisor.register_job("job-1", "core")
    job_service = RunJobService(
        runner_supervisor=supervisor,
        log_store=LogStore(),
        progress_estimator=estimator,
    )
    result_service = RunResultService(
        job_service=job_service,
        log_store=LogStore(),
        job_workflows={"job-1": key.workflow_id},
        job_started_at={},
        job_run_snapshots={},
        finish_memory_sampling=_noop_finish_memory_sampling,
        record_memory_observation=lambda result: None,
        maybe_retry_after_memory_cleanup=_no_retry_after_memory_cleanup,
        progress_estimator=estimator,
    )
    adapter.progress = JobProgress(job_id="job-1", status="completed")
    adapter.result = JobResult(job_id="job-1", status="completed", outputs=[{"kind": "image"}])

    stream = result_service.stream_progress_events("job-1")
    progress_event = await anext(stream)
    result_event = await anext(stream)

    assert "event: progress" in progress_event
    assert '"status":"running"' in progress_event
    assert "Saving result..." in progress_event
    assert "event: result" in result_event
    assert '"status":"completed"' in result_event


async def _noop_finish_memory_sampling(job_id: str) -> None:
    del job_id


async def _no_retry_after_memory_cleanup(result: JobResult):
    del result
    return None


def _key(
    *,
    workflow_id: str = "workflow-a",
    model_residency_signature: str = "sha256:model",
    execution_profile_signature: str = "sha256:execution",
) -> ProgressTimingKey:
    return ProgressTimingKey(
        workflow_id=workflow_id,
        engine="comfyui",
        runner_kind=RunnerKind.CORE_COMFYUI.value,
        runner_process_key="core",
        machine_profile_id="machine-a",
        model_residency_signature=model_residency_signature,
        execution_profile_signature=execution_profile_signature,
    )


def _runner(*, model_residency_signature: str | None = None) -> RunnerDescriptor:
    return RunnerDescriptor(
        runner_id="core",
        kind=RunnerKind.CORE_COMFYUI,
        base_url="http://127.0.0.1:8188",
        fingerprint="core",
        runner_process_compatibility_key="core",
        model_residency_signature=model_residency_signature,
    )


def _supervisor(adapter: object):
    from app.runtime.runners.supervisor import RunnerSupervisor

    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(_runner(), adapter)
    return supervisor


class _Adapter:
    def __init__(self) -> None:
        self.progress = JobProgress(job_id="job-1", status="running")
        self.result = JobResult(job_id="job-1", status="running")

    async def get_progress(
        self,
        job_id: str,
        since_preview_sequence: int | None = None,
    ) -> JobProgress:
        return self.progress.model_copy(update={"job_id": job_id})

    async def get_result(self, job_id: str) -> JobResult:
        return self.result.model_copy(update={"job_id": job_id})
