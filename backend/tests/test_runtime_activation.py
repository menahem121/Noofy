from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.runtime.runners.runtime_activation import (
    ComfyUIActivationError,
    WorkflowRuntimeActivationCoordinator,
)
from app.runtime.comfyui.comfyui_update_records import LocalComfyUIVersionRecord
from app.runtime.profiles import ActiveRuntimeProfileState, load_runtime_profile_catalog
from app.runtime.runners.runner_process import RunnerProcessStatus
from app.runtime.runners.supervisor import (
    CORE_RUNNER_ID,
    QueuedRunnerStartKind,
    QueuedRunnerStartStatus,
    RunnerDescriptor,
    RunnerKind,
    RunnerStatus,
    RunnerSupervisor,
)


class _Adapter:
    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        pass


class _Coordinator:
    def __init__(self, supervisor: RunnerSupervisor) -> None:
        self.supervisor = supervisor
        self.stop_all_calls = 0

    async def stop_all_runners(self) -> list[RunnerProcessStatus]:
        self.stop_all_calls += 1
        statuses: list[RunnerProcessStatus] = []
        for runner in self.supervisor.list_runners():
            if runner.kind is not RunnerKind.ISOLATED_COMFYUI:
                continue
            self.supervisor.update_runner_status(runner.runner_id, RunnerStatus.STOPPED)
            self.supervisor.unbind_runner(runner.runner_id)
            statuses.append(
                RunnerProcessStatus(
                    runner_id=runner.runner_id,
                    status=RunnerStatus.STOPPED,
                    base_url=runner.base_url,
                    ws_url=runner.ws_url or "",
                )
            )
        return statuses


def _profile_state(tmp_path: Path) -> ActiveRuntimeProfileState:
    source = tmp_path / "bundled"
    source.mkdir()
    (source / "main.py").write_text("", encoding="utf-8")
    return ActiveRuntimeProfileState(
        base_catalog=load_runtime_profile_catalog(
            Path("app/runtime/profile_catalog.json")
        ),
        source_dir=source,
    )


def _record(tmp_path: Path) -> LocalComfyUIVersionRecord:
    source = tmp_path / "updated"
    source.mkdir()
    (source / "main.py").write_text("", encoding="utf-8")
    return LocalComfyUIVersionRecord(
        tag="v9.9.9",
        source_hash="sha256:" + ("9" * 64),
        source_path=str(source),
        archive_url="https://example.test/v9.9.9.zip",
        locally_verified=True,
    )


def _supervisor(*, isolated_status: RunnerStatus) -> RunnerSupervisor:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url="http://127.0.0.1:8188",
            fingerprint="core",
        ),
        _Adapter(),
    )
    supervisor.upsert_runner(
        RunnerDescriptor(
            runner_id="isolated",
            kind=RunnerKind.ISOLATED_COMFYUI,
            base_url="http://127.0.0.1:8190",
            fingerprint="old",
            status=isolated_status,
        ),
        _Adapter(),
    )
    return supervisor


@pytest.mark.anyio
async def test_activation_stops_idle_runners_cancels_queue_and_commits_profile(
    tmp_path: Path,
) -> None:
    state = _profile_state(tmp_path)
    original = state.snapshot()
    supervisor = _supervisor(isolated_status=RunnerStatus.IDLE)
    queued = supervisor.enqueue_runner_start(
        workflow_id="workflow",
        kind=QueuedRunnerStartKind.PENDING_SWITCH,
        queued_behind_runner_id="isolated",
    )
    process_coordinator = _Coordinator(supervisor)
    activation = WorkflowRuntimeActivationCoordinator(
        runtime_profile_state=state,
        runner_supervisor=supervisor,
        runner_process_coordinator=process_coordinator,  # type: ignore[arg-type]
        log_store=LogStore(),
    )
    record = _record(tmp_path)

    await activation.prepare(record)

    assert process_coordinator.stop_all_calls == 1
    assert state.snapshot() == original
    assert supervisor.runtime_activation_in_progress()
    assert (
        supervisor.reserve_runner_for_submission(CORE_RUNNER_ID, workflow_id="workflow")
        is None
    )
    assert (
        supervisor.get_queued_runner_start(queued.queue_id).status
        is QueuedRunnerStartStatus.CANCELED
    )

    activation.commit(record)

    assert state.source_dir() == Path(record.source_path or "")
    assert not supervisor.runtime_activation_in_progress()
    assert state.catalog().profiles[0].comfyui_core_version == record.tag
    assert state.catalog().profiles[0].comfyui_core_source_hash == record.source_hash


@pytest.mark.anyio
async def test_activation_rejects_busy_runner_without_changing_profile(
    tmp_path: Path,
) -> None:
    state = _profile_state(tmp_path)
    original = state.snapshot()
    supervisor = _supervisor(isolated_status=RunnerStatus.RUNNING)
    process_coordinator = _Coordinator(supervisor)
    activation = WorkflowRuntimeActivationCoordinator(
        runtime_profile_state=state,
        runner_supervisor=supervisor,
        runner_process_coordinator=process_coordinator,  # type: ignore[arg-type]
        log_store=LogStore(),
    )

    with pytest.raises(ComfyUIActivationError, match="workflow runtime work is active"):
        await activation.prepare(_record(tmp_path))

    assert process_coordinator.stop_all_calls == 0
    assert state.snapshot() == original
    assert not supervisor.runtime_activation_in_progress()


@pytest.mark.anyio
async def test_activation_rejects_runner_process_start_in_progress(
    tmp_path: Path,
) -> None:
    state = _profile_state(tmp_path)
    supervisor = _supervisor(isolated_status=RunnerStatus.STOPPED)
    activation = WorkflowRuntimeActivationCoordinator(
        runtime_profile_state=state,
        runner_supervisor=supervisor,
        runner_process_coordinator=_Coordinator(supervisor),  # type: ignore[arg-type]
        log_store=LogStore(),
    )
    assert supervisor.begin_runner_start("starting-runner")

    with pytest.raises(ComfyUIActivationError, match="starting-runner"):
        await activation.prepare(_record(tmp_path))

    supervisor.end_runner_start("starting-runner")
    assert not supervisor.runtime_activation_in_progress()


@pytest.mark.anyio
async def test_activation_rejects_workflow_preparation_in_progress(
    tmp_path: Path,
) -> None:
    state = _profile_state(tmp_path)
    supervisor = _supervisor(isolated_status=RunnerStatus.STOPPED)
    activation = WorkflowRuntimeActivationCoordinator(
        runtime_profile_state=state,
        runner_supervisor=supervisor,
        runner_process_coordinator=_Coordinator(supervisor),  # type: ignore[arg-type]
        log_store=LogStore(),
    )
    assert supervisor.begin_workflow_preparation("workflow")

    with pytest.raises(ComfyUIActivationError, match="prepare:workflow"):
        await activation.prepare(_record(tmp_path))

    supervisor.end_workflow_preparation("workflow")
    assert not supervisor.runtime_activation_in_progress()


@pytest.mark.anyio
async def test_activation_abort_reopens_workflow_submissions(tmp_path: Path) -> None:
    state = _profile_state(tmp_path)
    supervisor = _supervisor(isolated_status=RunnerStatus.IDLE)
    activation = WorkflowRuntimeActivationCoordinator(
        runtime_profile_state=state,
        runner_supervisor=supervisor,
        runner_process_coordinator=_Coordinator(supervisor),  # type: ignore[arg-type]
        log_store=LogStore(),
    )
    record = _record(tmp_path)

    await activation.prepare(record)
    activation.abort(record)

    assert not supervisor.runtime_activation_in_progress()
    assert (
        supervisor.reserve_runner_for_submission(CORE_RUNNER_ID, workflow_id="workflow")
        is not None
    )
