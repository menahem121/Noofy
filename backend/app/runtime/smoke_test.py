"""Smoke-test helpers for prepared runtime workspaces.

The smoke test is intentionally narrow for Phase 4: start the prepared runner,
wait for the process supervisor's health check to pass, then stop the process.
It proves the materialized workspace can boot without registering the endpoint
for workflow execution.
"""

from __future__ import annotations

from collections.abc import Callable

from app.engine.diagnostics import LogStore
from app.runtime.isolation import CapsuleLock
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.workspace_preparer import PreparedRuntimeWorkspace

RunnerSmokeLaunchSpecFactory = Callable[[CapsuleLock, PreparedRuntimeWorkspace], RunnerLaunchSpec]


class RunnerSmokeTester:
    def __init__(
        self,
        *,
        process_supervisor: RunnerProcessSupervisor,
        launch_spec_factory: RunnerSmokeLaunchSpecFactory,
        log_store: LogStore | None = None,
    ) -> None:
        self.process_supervisor = process_supervisor
        self.launch_spec_factory = launch_spec_factory
        self.log_store = log_store or LogStore()

    async def run(
        self,
        capsule_lock: CapsuleLock,
        prepared_workspace: PreparedRuntimeWorkspace,
    ) -> None:
        spec = self.launch_spec_factory(capsule_lock, prepared_workspace)
        self.log_store.add(
            "info",
            "Runner smoke test starting",
            "runtime.smoke_test",
            workflow_id=capsule_lock.workflow.package_id,
            details={
                "runner_id": spec.runner_id,
                "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                "runner_workspace_path": str(prepared_workspace.runner_workspace_path),
            },
        )
        try:
            await self.process_supervisor.start(spec)
        except Exception as exc:
            self.log_store.add(
                "error",
                "Runner smoke test failed",
                "runtime.smoke_test",
                workflow_id=capsule_lock.workflow.package_id,
                details={
                    "runner_id": spec.runner_id,
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                },
            )
            raise RuntimeError(f"Runner smoke test failed: {exc}") from exc
        finally:
            await self.process_supervisor.stop(spec.runner_id)

        self.log_store.add(
            "info",
            "Runner smoke test passed",
            "runtime.smoke_test",
            workflow_id=capsule_lock.workflow.package_id,
            details={
                "runner_id": spec.runner_id,
                "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
            },
        )
