from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.isolation import CapsuleLock
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.smoke_test import RunnerSmokeTester
from app.runtime.supervisor import RunnerKind
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 9901
        self.returncode = None
        self.stdout = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _capsule_lock() -> CapsuleLock:
    return CapsuleLock.model_validate(
        {
            "schema_version": "0.1.0",
            "workflow": {
                "publisher_id": "noofy",
                "package_id": "text_to_image_v0",
                "version": "0.1.0",
                "trust_level": "noofy_verified",
                "source": "bundled",
            },
            "engine": {
                "type": "comfyui",
                "comfyui_version": "milestone-1",
                "core_source_hash": "sha256:" + ("a" * 64),
            },
            "runtime": {
                "dependency_env_fingerprint": "sha256:" + ("b" * 64),
                "runner_fingerprint": "sha256:" + ("c" * 64),
                "capsule_fingerprint": "sha256:" + ("d" * 64),
                "os": "darwin",
                "architecture": "arm64",
                "python_version": "3.11",
                "gpu_backend": "mps",
                "dependency_lock_hash": "sha256:" + ("e" * 64),
                "runner_workspace_hash": "sha256:" + ("f" * 64),
            },
            "custom_nodes": [],
            "dependencies": {
                "lock_file": "core.lock",
                "install_policy": "core_only_no_community",
            },
            "models": [],
            "trust": {
                "level": "noofy_verified",
                "publisher": "Noofy",
            },
        }
    )


def _prepared_workspace(tmp_path: Path):
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=LogStore(),
    )
    return preparer.prepare(_capsule_lock())


@pytest.mark.anyio
async def test_runner_smoke_tester_starts_health_checks_and_stops_runner(tmp_path: Path) -> None:
    process = FakeProcess()
    created: list[tuple[list[str], dict]] = []

    async def process_factory(command: list[str], **kwargs):
        created.append((command, kwargs))
        return process

    async def healthy(base_url: str):
        return True, None

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
    )
    log_store = LogStore()
    prepared = _prepared_workspace(tmp_path)
    smoke_tester = RunnerSmokeTester(
        process_supervisor=process_supervisor,
        launch_spec_factory=lambda capsule_lock, prepared_workspace: RunnerLaunchSpec(
            runner_id="smoke-1",
            kind=RunnerKind.ISOLATED_COMFYUI,
            fingerprint=capsule_lock.runtime.runner_fingerprint,
            python_executable="/opt/noofy/python",
            working_dir=prepared_workspace.runner_workspace_path,
            dependency_env_path=prepared_workspace.dependency_env_path,
            runner_workspace_path=prepared_workspace.runner_workspace_path,
            port=9191,
        ),
        log_store=log_store,
    )

    await smoke_tester.run(_capsule_lock(), prepared)

    assert created[0][0][:2] == ["/opt/noofy/python", "main.py"]
    assert created[0][1]["cwd"] == prepared.runner_workspace_path
    assert process.terminated
    assert (await process_supervisor.status("smoke-1")).status.value == "stopped"
    messages = [event.message for event in log_store.list_events().events]
    assert "Runner smoke test starting" in messages
    assert "Runner smoke test passed" in messages


@pytest.mark.anyio
async def test_runner_smoke_tester_reports_startup_failure(tmp_path: Path) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def unhealthy(base_url: str):
        return False, "not ready"

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=unhealthy,
        startup_timeout_seconds=0.01,
        health_poll_interval_seconds=0.001,
    )
    log_store = LogStore()
    prepared = _prepared_workspace(tmp_path)
    smoke_tester = RunnerSmokeTester(
        process_supervisor=process_supervisor,
        launch_spec_factory=lambda capsule_lock, prepared_workspace: RunnerLaunchSpec(
            runner_id="smoke-1",
            kind=RunnerKind.ISOLATED_COMFYUI,
            fingerprint=capsule_lock.runtime.runner_fingerprint,
            python_executable="/opt/noofy/python",
            working_dir=prepared_workspace.runner_workspace_path,
            port=9191,
        ),
        log_store=log_store,
    )

    with pytest.raises(RuntimeError, match="Runner smoke test failed"):
        await smoke_tester.run(_capsule_lock(), prepared)

    assert process.terminated
    latest_error = log_store.latest_error()
    assert latest_error is not None
    assert latest_error.message == "Runner smoke test failed"
    assert latest_error.details["error"]
