import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from app.diagnostics import LogStore
from app.engine.models import EngineJob, JobProgress, JobResult
from app.gallery import RunSubmissionSnapshot
from app.runs.job_service import RunJobService
from app.runs.lifecycle_service import RunLifecycleService
from app.runs.queue_service import WorkflowRunQueueService, WorkflowRunQueueStatus
from app.runs.result_service import RunResultService
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerSupervisor,
)


def _snapshot() -> RunSubmissionSnapshot:
    return RunSubmissionSnapshot(
        workflow_id="wf",
        workflow_title="Workflow",
        dashboard_version="1",
    )


class _Adapter:
    def __init__(self) -> None:
        self.progress_calls: list[str] = []
        self.cancel_calls: list[str] = []
        self.result_calls: list[str] = []

    async def get_progress(
        self,
        job_id: str,
        since_preview_sequence: int | None = None,
    ) -> JobProgress:
        del since_preview_sequence
        self.progress_calls.append(job_id)
        return JobProgress(job_id=job_id, status="running")

    async def cancel_job(self, job_id: str) -> JobProgress:
        self.cancel_calls.append(job_id)
        return JobProgress(job_id=job_id, status="canceled")

    async def get_result(self, job_id: str) -> JobResult:
        self.result_calls.append(job_id)
        return JobResult(job_id=job_id, status="completed")


def _supervisor(adapter: _Adapter) -> RunnerSupervisor:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url="http://127.0.0.1:8188",
            fingerprint=CORE_RUNNER_FINGERPRINT,
        ),
        adapter,
    )
    supervisor.register_job("job-1", CORE_RUNNER_ID)
    return supervisor


def _queued_run(queue: WorkflowRunQueueService, workflow_id: str = "wf"):
    return queue.enqueue(
        workflow_id=workflow_id,
        inputs={},
        options={},
        run_submission_snapshot=_snapshot(),
    )


def test_workflow_queue_uses_uuid_alias_and_same_id_requeue() -> None:
    queue = WorkflowRunQueueService()
    record = queue.enqueue(
        workflow_id="wf",
        inputs={"prompt": "one"},
        options={},
        run_submission_snapshot=_snapshot(),
        reason="active_workflow",
    )
    uuid.UUID(record.queue_id.removeprefix("workflow-run-queue-"))

    claimed = queue.claim_next(dispatch_epoch=1)
    assert claimed is not None
    queue.set_reservation(claimed.queue_id, "runner-reservation-1")
    requeued = queue.requeue(claimed.queue_id, reason="active_workflow", transient=False)
    assert requeued is not None
    assert requeued.queue_id == record.queue_id
    assert requeued.reservation_token is None

    queue.set_reservation(record.queue_id, "runner-reservation-2")
    submitted = queue.mark_submitted(record.queue_id, job_id="job-1")
    assert submitted is not None
    assert submitted.reservation_token is None
    assert queue.resolve(record.queue_id).job_id == "job-1"
    assert queue.resolve("job-1").queue_id == record.queue_id
    queue.mark_terminal("job-1")
    assert queue.resolve(record.queue_id).job_id == "job-1"


def test_queued_run_progress_reads_as_queue_state_not_memory_wait() -> None:
    """Runs queued behind the workflow's own active run (or starting runner)
    must not report a misleading memory-wait message."""
    queue = WorkflowRunQueueService()
    expectations = {
        "runner_submission_reservation_unavailable": (
            "This run is queued and will start when the current run finishes."
        ),
        "queued_behind_active_run": (
            "This run is queued and will start when the current run finishes."
        ),
        "waiting_for_active_workflow": (
            "Noofy will start this workflow after the active run finishes."
        ),
        "starting": "This run will start when the workflow's runner is ready.",
        "queued_pending_memory": "Waiting for enough memory to start this workflow.",
        None: "Waiting for enough memory to start this workflow.",
    }
    for reason, message in expectations.items():
        record = queue.enqueue(
            workflow_id="wf",
            inputs={},
            options={},
            run_submission_snapshot=_snapshot(),
            reason=reason,
        )
        progress = queue.progress(record.queue_id)
        assert progress is not None
        assert progress.status == "queued_pending_memory"
        assert progress.message == message, reason


def test_workflow_queue_fails_after_eight_transient_handoff_failures() -> None:
    queue = WorkflowRunQueueService()
    record = queue.enqueue(
        workflow_id="wf",
        inputs={},
        options={},
        run_submission_snapshot=_snapshot(),
    )

    for attempt in range(8):
        record = queue.requeue(record.queue_id, reason=f"race-{attempt}", transient=True)
        assert record is not None

    assert record.status is WorkflowRunQueueStatus.FAILED
    assert record.transient_failure_count == 8


@pytest.mark.anyio
async def test_job_service_routes_queue_alias_to_submitted_job() -> None:
    adapter = _Adapter()
    supervisor = _supervisor(adapter)
    queue = WorkflowRunQueueService()
    queued = queue.enqueue(
        workflow_id="wf",
        inputs={},
        options={},
        run_submission_snapshot=_snapshot(),
    )
    queue.mark_submitted(queued.queue_id, job_id="job-1")
    service = RunJobService(
        runner_supervisor=supervisor,
        log_store=LogStore(),
        workflow_run_queue_service=queue,
    )

    progress = await service.get_progress(queued.queue_id)
    canceled = await service.cancel_job(queued.queue_id)
    progress_after_cancel = await service.get_progress(queued.queue_id)

    assert progress.job_id == "job-1"
    assert progress.queue_id == queued.queue_id
    assert canceled.job_id == "job-1"
    assert canceled.queue_id == queued.queue_id
    assert queue.is_terminal(queued.queue_id)
    assert progress_after_cancel.job_id == queued.queue_id
    assert progress_after_cancel.queue_id == queued.queue_id
    assert progress_after_cancel.status == "canceled"
    assert adapter.progress_calls == ["job-1"]
    assert adapter.cancel_calls == ["job-1"]


@pytest.mark.anyio
async def test_job_service_returns_user_safe_memory_failure_progress() -> None:
    class MemoryFailureProgressAdapter(_Adapter):
        async def get_progress(
            self,
            job_id: str,
            since_preview_sequence: int | None = None,
        ) -> JobProgress:
            del since_preview_sequence
            self.progress_calls.append(job_id)
            return JobProgress(
                job_id=job_id,
                status="failed",
                current_node="1",
                message="MPS backend out of memory",
            )

    adapter = MemoryFailureProgressAdapter()
    service = RunJobService(runner_supervisor=_supervisor(adapter), log_store=LogStore())

    progress = await service.get_progress("job-1")

    assert progress.status == "failed"
    assert progress.error_code == "memory_oom"
    assert progress.message == (
        "Your computer does not have enough available RAM or GPU memory for this workflow right now."
    )
    assert progress.developer_details["original_error"] == "MPS backend out of memory"


@pytest.mark.anyio
async def test_result_service_routes_submitted_queue_alias_to_real_job() -> None:
    adapter = _Adapter()
    supervisor = _supervisor(adapter)
    queue = WorkflowRunQueueService()
    queued = queue.enqueue(
        workflow_id="wf",
        inputs={},
        options={},
        run_submission_snapshot=_snapshot(),
    )
    queue.mark_submitted(queued.queue_id, job_id="job-1")
    job_service = RunJobService(
        runner_supervisor=supervisor,
        log_store=LogStore(),
        workflow_run_queue_service=queue,
    )

    async def finish_sampling(job_id: str) -> None:
        return None

    def record_observation(result: JobResult) -> None:
        return None

    async def maybe_retry(result: JobResult):
        return None

    result_service = RunResultService(
        job_service=job_service,
        log_store=LogStore(),
        job_workflows={"job-1": "wf"},
        job_started_at={"job-1": datetime.now(UTC)},
        job_run_snapshots={"job-1": _snapshot()},
        finish_memory_sampling=finish_sampling,
        record_memory_observation=record_observation,
        maybe_retry_after_memory_cleanup=maybe_retry,
        workflow_run_queue_service=queue,
    )

    result = await result_service.get_result(queued.queue_id)

    assert result.job_id == "job-1"
    assert result.queue_id == queued.queue_id
    assert adapter.result_calls == ["job-1"]


@pytest.mark.anyio
async def test_cancel_workflow_active_and_queued_cancels_only_requested_workflow() -> None:
    adapter = _Adapter()
    supervisor = _supervisor(adapter)
    supervisor.mark_runner_job_started(CORE_RUNNER_ID, "job-1", workflow_id="wf")
    queue = WorkflowRunQueueService()
    queued_a = _queued_run(queue, "wf")
    queued_b = _queued_run(queue, "wf")
    other = _queued_run(queue, "other-wf")
    terminal = _queued_run(queue, "wf")
    queue.mark_terminal(terminal.queue_id, status=WorkflowRunQueueStatus.CANCELED)
    service = RunJobService(
        runner_supervisor=supervisor,
        log_store=LogStore(),
        workflow_run_queue_service=queue,
    )

    assert service.workflow_active_and_queued_summary("wf") == {
        "active_count": 1,
        "queued_count": 2,
        "total_count": 3,
    }

    summary = await service.cancel_workflow_active_and_queued("wf")

    assert summary == {
        "canceled_active_count": 1,
        "canceled_queued_count": 2,
        "already_terminal_count": 1,
        "failed_to_cancel_count": 0,
    }
    assert adapter.cancel_calls == ["job-1"]
    assert queue.get(queued_a.queue_id).status is WorkflowRunQueueStatus.CANCELED
    assert queue.get(queued_b.queue_id).status is WorkflowRunQueueStatus.CANCELED
    assert queue.get(other.queue_id).status is WorkflowRunQueueStatus.QUEUED


@pytest.mark.anyio
async def test_cancel_workflow_active_and_queued_cancels_submitted_queue_once() -> None:
    adapter = _Adapter()
    supervisor = _supervisor(adapter)
    supervisor.mark_runner_job_started(CORE_RUNNER_ID, "job-1", workflow_id="wf")
    queue = WorkflowRunQueueService()
    queued = _queued_run(queue, "wf")
    queue.mark_submitted(queued.queue_id, job_id="job-1")
    service = RunJobService(
        runner_supervisor=supervisor,
        log_store=LogStore(),
        workflow_run_queue_service=queue,
    )

    assert service.workflow_active_and_queued_summary("wf") == {
        "active_count": 1,
        "queued_count": 0,
        "total_count": 1,
    }

    summary = await service.cancel_workflow_active_and_queued("wf")

    assert summary == {
        "canceled_active_count": 1,
        "canceled_queued_count": 0,
        "already_terminal_count": 0,
        "failed_to_cancel_count": 0,
    }
    assert adapter.cancel_calls == ["job-1"]


@pytest.mark.anyio
async def test_run_result_service_finalizes_concurrent_terminal_reads_once() -> None:
    adapter = _Adapter()
    supervisor = _supervisor(adapter)
    job_service = RunJobService(runner_supervisor=supervisor, log_store=LogStore())
    calls = {"sampling": 0, "observation": 0, "retry": 0}

    async def finish_sampling(job_id: str) -> None:
        calls["sampling"] += 1
        await asyncio.sleep(0)

    def record_observation(result: JobResult) -> None:
        calls["observation"] += 1

    async def maybe_retry(result: JobResult):
        calls["retry"] += 1
        return None

    service = RunResultService(
        job_service=job_service,
        log_store=LogStore(),
        job_workflows={"job-1": "wf"},
        job_started_at={"job-1": datetime.now(UTC)},
        job_run_snapshots={"job-1": _snapshot()},
        finish_memory_sampling=finish_sampling,
        record_memory_observation=record_observation,
        maybe_retry_after_memory_cleanup=maybe_retry,
    )
    job_service.terminal_job_progress = service.terminal_progress

    first, second = await asyncio.gather(service.get_result("job-1"), service.get_result("job-1"))
    canceled = await job_service.cancel_job("job-1")

    assert first.status == second.status == "completed"
    assert calls == {"sampling": 1, "observation": 1, "retry": 1}
    assert adapter.result_calls == ["job-1", "job-1"]
    assert adapter.cancel_calls == []
    assert canceled.status == "completed"


@pytest.mark.anyio
async def test_run_result_service_returns_user_safe_memory_failure_and_preserves_details() -> None:
    class MemoryFailureAdapter(_Adapter):
        async def get_result(self, job_id: str) -> JobResult:
            self.result_calls.append(job_id)
            return JobResult(
                job_id=job_id,
                status="failed",
                error="CUDA out of memory. Tried to allocate 1.19 GiB.",
            )

    adapter = MemoryFailureAdapter()
    supervisor = _supervisor(adapter)
    job_service = RunJobService(runner_supervisor=supervisor, log_store=LogStore())

    async def finish_sampling(job_id: str) -> None:
        return None

    def record_observation(result: JobResult) -> None:
        assert result.error == "CUDA out of memory. Tried to allocate 1.19 GiB."

    async def maybe_retry(result: JobResult):
        assert result.error == "CUDA out of memory. Tried to allocate 1.19 GiB."
        return None

    service = RunResultService(
        job_service=job_service,
        log_store=LogStore(),
        job_workflows={"job-1": "wf"},
        job_started_at={"job-1": datetime.now(UTC)},
        job_run_snapshots={"job-1": _snapshot()},
        finish_memory_sampling=finish_sampling,
        record_memory_observation=record_observation,
        maybe_retry_after_memory_cleanup=maybe_retry,
    )
    job_service.terminal_job_progress = service.terminal_progress

    result = await service.get_result("job-1")
    progress = service.terminal_progress("job-1")

    assert isinstance(result, JobResult)
    assert result.error_code == "memory_oom"
    assert result.error == "Not enough memory to run this workflow"
    assert result.user_message == (
        "Your computer does not have enough available RAM or GPU memory for this workflow right now."
    )
    assert result.developer_details == {
        "error_code": "memory_oom",
        "original_error": "CUDA out of memory. Tried to allocate 1.19 GiB.",
        "job_id": "job-1",
        "workflow_id": "wf",
        "memory_status": {
            "state": "runtime_memory_oom",
            "message": "Your computer does not have enough available RAM or GPU memory for this workflow right now.",
        },
        "memory_decision": None,
    }
    assert progress is not None
    assert progress.error_code == "memory_oom"
    assert progress.message == result.user_message
    assert progress.developer_details["original_error"] == (
        "CUDA out of memory. Tried to allocate 1.19 GiB."
    )


@pytest.mark.anyio
async def test_run_result_service_keeps_non_memory_failure_unchanged() -> None:
    class RuntimeFailureAdapter(_Adapter):
        async def get_result(self, job_id: str) -> JobResult:
            self.result_calls.append(job_id)
            return JobResult(job_id=job_id, status="failed", error="Custom node failed")

    adapter = RuntimeFailureAdapter()
    supervisor = _supervisor(adapter)
    job_service = RunJobService(runner_supervisor=supervisor, log_store=LogStore())

    async def finish_sampling(job_id: str) -> None:
        return None

    service = RunResultService(
        job_service=job_service,
        log_store=LogStore(),
        job_workflows={"job-1": "wf"},
        job_started_at={"job-1": datetime.now(UTC)},
        job_run_snapshots={"job-1": _snapshot()},
        finish_memory_sampling=finish_sampling,
        record_memory_observation=lambda result: None,
        maybe_retry_after_memory_cleanup=lambda result: _no_retry(),
    )

    result = await service.get_result("job-1")

    assert isinstance(result, JobResult)
    assert result.error == "Custom node failed"
    assert result.error_code is None
    assert result.user_message is None
    assert result.developer_details == {}


async def _no_retry():
    return None


@pytest.mark.anyio
async def test_run_lifecycle_requeues_under_same_id_until_state_change() -> None:
    queue = WorkflowRunQueueService()
    record = queue.enqueue(
        workflow_id="wf",
        inputs={},
        options={},
        run_submission_snapshot=_snapshot(),
    )
    lifecycle = RunLifecycleService(queue_service=queue, log_store=LogStore())
    calls = 0

    async def submit(queued):
        nonlocal calls
        calls += 1
        if calls == 1:
            return EngineJob(
                job_id=queued.queue_id,
                queue_id=queued.queue_id,
                workflow_id="wf",
                engine="noofy",
                status="queued_pending_memory",
                memory_status={"state": "waiting_for_active_workflow"},
            )
        return EngineJob(job_id="job-1", workflow_id="wf", engine="comfyui", status="queued")

    lifecycle.submit_queued_run = submit
    lifecycle.request_dispatch("initial")
    await asyncio.sleep(0.01)
    waiting = queue.get(record.queue_id)
    assert waiting is not None
    assert waiting.status is WorkflowRunQueueStatus.REQUEUED
    assert calls == 1

    lifecycle.request_dispatch("runner_finished")
    await asyncio.sleep(0.01)
    submitted = queue.get(record.queue_id)
    assert submitted is not None
    assert submitted.status is WorkflowRunQueueStatus.SUBMITTED
    assert submitted.submitted_job_id == "job-1"
    assert calls == 2
    await lifecycle.shutdown()
