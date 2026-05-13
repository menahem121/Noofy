"""Smoke-test helpers for prepared runtime workspaces.

Phase 5 smoke testing reports staged results separately. This runner-level
helper proves the prepared runner can boot, exposes expected node metadata,
and can optionally execute a small workflow fixture. Real workflow execution
is still opt-in so development machines without suitable hardware do not mark
large imported workflows ready by accident.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx

from app.diagnostics import DiagnosticsSink
from app.runtime.dependencies.isolation import (
    CapsuleLock,
    SmokeStageResult,
    SmokeStageStatus,
    SmokeTestReport,
)
from app.runtime.runners.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.storage.workspace_preparer import PreparedRuntimeWorkspace

RunnerSmokeLaunchSpecFactory = Callable[
    [CapsuleLock, PreparedRuntimeWorkspace], RunnerLaunchSpec
]
RunnerObjectInfoFetcher = Callable[[str], Awaitable[Mapping[str, object]]]
RunnerPromptExecutor = Callable[
    [str, dict[str, object], float], Awaitable[Mapping[str, object] | None]
]
DependencyImportChecker = Callable[
    [PreparedRuntimeWorkspace], Awaitable[SmokeStageResult]
]
DependencyImportCommandRunner = Callable[
    [list[str], PreparedRuntimeWorkspace], Awaitable[tuple[int, str]]
]


@dataclass(frozen=True)
class SmokeExecutionFixture:
    """A tiny ComfyUI prompt used only to prove the staged runner executes work."""

    name: str
    prompt: dict[str, object]
    required_node_types: Sequence[str] = ()
    expected_output_node_count: int | None = None
    expected_output_node_ids: Sequence[str] = ()
    timeout_seconds: float = 30


class SmokePromptTimeoutError(TimeoutError):
    def __init__(self, *, prompt_id: str | None, timeout_seconds: float) -> None:
        super().__init__(
            f"Timed out waiting for ComfyUI smoke prompt after {timeout_seconds:g} seconds."
        )
        self.prompt_id = prompt_id
        self.timeout_seconds = timeout_seconds


class RunnerSmokeTestError(RuntimeError):
    def __init__(self, message: str, *, report: SmokeTestReport) -> None:
        super().__init__(message)
        self.report = report


SmokeExecutionFixtureResolver = Callable[
    [CapsuleLock, PreparedRuntimeWorkspace], SmokeExecutionFixture | None
]


class RunnerSmokeTester:
    def __init__(
        self,
        *,
        process_supervisor: RunnerProcessSupervisor,
        launch_spec_factory: RunnerSmokeLaunchSpecFactory,
        log_store: DiagnosticsSink,
        execution_fixture: SmokeExecutionFixture | None = None,
        execution_fixture_resolver: SmokeExecutionFixtureResolver | None = None,
        dependency_import_checker: DependencyImportChecker | None = None,
        object_info_fetcher: RunnerObjectInfoFetcher | None = None,
        prompt_executor: RunnerPromptExecutor | None = None,
    ) -> None:
        self.process_supervisor = process_supervisor
        self.launch_spec_factory = launch_spec_factory
        self.execution_fixture = execution_fixture
        self.execution_fixture_resolver = execution_fixture_resolver
        self.dependency_import_checker = (
            dependency_import_checker or _check_dependency_imports
        )
        self.object_info_fetcher = object_info_fetcher or _fetch_object_info
        self.prompt_executor = prompt_executor or _execute_prompt_and_wait
        self.log_store = log_store

    async def run(
        self,
        capsule_lock: CapsuleLock,
        prepared_workspace: PreparedRuntimeWorkspace,
    ) -> SmokeTestReport:
        spec = self.launch_spec_factory(capsule_lock, prepared_workspace)
        handle = None
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
        dependency_env = await self.dependency_import_checker(prepared_workspace)
        if dependency_env.status is not SmokeStageStatus.PASSED:
            report = SmokeTestReport(
                dependency_env=dependency_env,
                custom_node_import=SmokeStageResult(status=SmokeStageStatus.NOT_RUN),
                runner_health=SmokeStageResult(status=SmokeStageStatus.NOT_RUN),
                workflow_execution=SmokeStageResult(status=SmokeStageStatus.NOT_RUN),
            )
            self.log_store.add(
                (
                    "error"
                    if dependency_env.status is SmokeStageStatus.FAILED
                    else "warning"
                ),
                "Runner smoke test blocked by dependency environment",
                "runtime.smoke_test",
                workflow_id=capsule_lock.workflow.package_id,
                details={
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "smoke_report": report.model_dump(mode="json"),
                },
            )
            return report

        try:
            handle = await self.process_supervisor.start(spec)
        except Exception as exc:
            report = SmokeTestReport(
                dependency_env=dependency_env,
                custom_node_import=SmokeStageResult(
                    status=SmokeStageStatus.SKIPPED,
                    message="No custom node import check was run.",
                ),
                runner_health=SmokeStageResult(
                    status=SmokeStageStatus.FAILED,
                    message=str(exc),
                ),
                workflow_execution=SmokeStageResult(status=SmokeStageStatus.NOT_RUN),
            )
            self.log_store.add(
                "error",
                "Runner smoke test failed",
                "runtime.smoke_test",
                workflow_id=capsule_lock.workflow.package_id,
                details={
                    "runner_id": spec.runner_id,
                    "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                    "error": str(exc),
                    "smoke_report": report.model_dump(mode="json"),
                },
            )
            raise RunnerSmokeTestError(
                f"Runner smoke test failed: {exc}", report=report
            ) from exc
        finally:
            if handle is None:
                await self.process_supervisor.stop(spec.runner_id)

        try:
            report = await self._run_started_runner_checks(
                capsule_lock,
                handle.descriptor.base_url,
                dependency_env=dependency_env,
                execution_fixture=self._execution_fixture(
                    capsule_lock, prepared_workspace
                ),
            )
        finally:
            await self.process_supervisor.stop(spec.runner_id)

        self.log_store.add(
            "info",
            "Runner smoke test completed",
            "runtime.smoke_test",
            workflow_id=capsule_lock.workflow.package_id,
            details={
                "runner_id": spec.runner_id,
                "capsule_fingerprint": capsule_lock.runtime.capsule_fingerprint,
                "smoke_report": report.model_dump(mode="json"),
            },
        )
        return report

    async def _run_started_runner_checks(
        self,
        capsule_lock: CapsuleLock,
        base_url: str,
        *,
        dependency_env: SmokeStageResult,
        execution_fixture: SmokeExecutionFixture | None,
    ) -> SmokeTestReport:
        runner_health = SmokeStageResult(status=SmokeStageStatus.PASSED)
        custom_node_import = _custom_node_not_required_stage(capsule_lock)
        workflow_execution = SmokeStageResult(
            status=SmokeStageStatus.BLOCKED,
            message="No workflow execution smoke fixture is configured.",
        )

        object_info: Mapping[str, object] | None = None
        required_node_types = _required_object_info_node_types(
            capsule_lock, execution_fixture
        )
        if required_node_types:
            try:
                object_info = await self.object_info_fetcher(base_url)
            except Exception as exc:
                return SmokeTestReport(
                    dependency_env=dependency_env,
                    custom_node_import=_object_info_failure_stage(
                        capsule_lock, str(exc)
                    ),
                    runner_health=runner_health,
                    workflow_execution=(
                        SmokeStageResult(
                            status=SmokeStageStatus.FAILED,
                            message=f"Could not read runner node metadata: {exc}",
                        )
                        if execution_fixture is not None
                        else workflow_execution
                    ),
                )

        if capsule_lock.custom_nodes:
            custom_node_import = _custom_node_import_stage(capsule_lock, object_info)
            if custom_node_import.status is SmokeStageStatus.FAILED:
                return SmokeTestReport(
                    dependency_env=dependency_env,
                    custom_node_import=custom_node_import,
                    runner_health=runner_health,
                    workflow_execution=workflow_execution,
                )

        if execution_fixture is None:
            return SmokeTestReport(
                dependency_env=dependency_env,
                custom_node_import=custom_node_import,
                runner_health=runner_health,
                workflow_execution=workflow_execution,
            )

        missing_fixture_nodes = _missing_node_types(
            object_info or {}, execution_fixture.required_node_types
        )
        if missing_fixture_nodes:
            return SmokeTestReport(
                dependency_env=dependency_env,
                custom_node_import=custom_node_import,
                runner_health=runner_health,
                workflow_execution=SmokeStageResult(
                    status=SmokeStageStatus.FAILED,
                    message="Workflow execution smoke fixture node types are missing.",
                    details={
                        "fixture": execution_fixture.name,
                        "missing_node_types": missing_fixture_nodes,
                    },
                ),
            )

        custom_node_execution_failure, custom_node_execution_details = (
            _custom_node_execution_details(
                capsule_lock,
                execution_fixture,
            )
        )
        if custom_node_execution_failure is not None:
            return SmokeTestReport(
                dependency_env=dependency_env,
                custom_node_import=custom_node_import,
                runner_health=runner_health,
                workflow_execution=custom_node_execution_failure,
            )

        try:
            execution_details = await self.prompt_executor(
                base_url,
                execution_fixture.prompt,
                execution_fixture.timeout_seconds,
            )
        except SmokePromptTimeoutError as exc:
            workflow_execution = SmokeStageResult(
                status=SmokeStageStatus.FAILED,
                message="Workflow execution smoke timed out.",
                details={
                    "fixture": execution_fixture.name,
                    "timeout_seconds": exc.timeout_seconds,
                    "prompt_id": exc.prompt_id,
                },
            )
        except Exception as exc:
            workflow_execution = SmokeStageResult(
                status=SmokeStageStatus.FAILED,
                message=f"Workflow execution smoke failed: {exc}",
                details={"fixture": execution_fixture.name},
            )
        else:
            workflow_execution = _workflow_execution_stage_result(
                execution_fixture,
                {
                    **dict(execution_details or {}),
                    **custom_node_execution_details,
                },
            )

        return SmokeTestReport(
            dependency_env=dependency_env,
            custom_node_import=custom_node_import,
            runner_health=runner_health,
            workflow_execution=workflow_execution,
        )

    def _execution_fixture(
        self,
        capsule_lock: CapsuleLock,
        prepared_workspace: PreparedRuntimeWorkspace,
    ) -> SmokeExecutionFixture | None:
        if self.execution_fixture_resolver is not None:
            return self.execution_fixture_resolver(capsule_lock, prepared_workspace)
        return self.execution_fixture


def _custom_node_not_required_stage(capsule_lock: CapsuleLock) -> SmokeStageResult:
    if not capsule_lock.custom_nodes:
        return SmokeStageResult(
            status=SmokeStageStatus.SKIPPED,
            message="Workflow has no custom nodes.",
        )
    return SmokeStageResult(
        status=SmokeStageStatus.NOT_RUN,
        message="Custom node import smoke has not run.",
    )


def _custom_node_import_stage(
    capsule_lock: CapsuleLock,
    object_info: Mapping[str, object] | None,
) -> SmokeStageResult:
    required = _custom_node_types(capsule_lock)
    if not required:
        return SmokeStageResult(
            status=SmokeStageStatus.BLOCKED,
            message="Custom node packages do not declare node types for smoke validation.",
        )
    missing = _missing_node_types(object_info or {}, required)
    if missing:
        return SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message="Custom node types are missing from runner metadata.",
            details={"missing_node_types": missing},
        )
    return SmokeStageResult(
        status=SmokeStageStatus.PASSED,
        message="Custom node types are registered by the staged runner.",
        details={"node_types": sorted(set(required))},
    )


def _object_info_failure_stage(
    capsule_lock: CapsuleLock, error: str
) -> SmokeStageResult:
    if capsule_lock.custom_nodes:
        return SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message=f"Could not verify custom node registration: {error}",
        )
    return _custom_node_not_required_stage(capsule_lock)


def _required_object_info_node_types(
    capsule_lock: CapsuleLock,
    execution_fixture: SmokeExecutionFixture | None,
) -> list[str]:
    required = _custom_node_types(capsule_lock)
    if execution_fixture is not None:
        required.extend(
            str(node_type) for node_type in execution_fixture.required_node_types
        )
    return sorted(set(required))


def _custom_node_types(capsule_lock: CapsuleLock) -> list[str]:
    return [
        node_type
        for custom_node in capsule_lock.custom_nodes
        for node_type in custom_node.node_types
    ]


def _missing_node_types(
    object_info: Mapping[str, object],
    required_node_types: Sequence[str],
) -> list[str]:
    available = {str(node_type) for node_type in object_info.keys()}
    return sorted(
        {
            str(node_type)
            for node_type in required_node_types
            if str(node_type) not in available
        }
    )


def _custom_node_execution_details(
    capsule_lock: CapsuleLock,
    fixture: SmokeExecutionFixture,
) -> tuple[SmokeStageResult | None, dict[str, object]]:
    custom_node_types = set(_custom_node_types(capsule_lock))
    if not custom_node_types:
        return None, {}
    fixture_node_types = _fixture_prompt_node_types(fixture.prompt)
    exercised = sorted(custom_node_types & fixture_node_types)
    if exercised:
        return None, {"exercised_custom_node_types": exercised}
    return (
        SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message="Workflow execution smoke fixture does not exercise declared custom node types.",
            details={
                "fixture": fixture.name,
                "declared_custom_node_types": sorted(custom_node_types),
                "fixture_node_types": sorted(fixture_node_types),
            },
        ),
        {},
    )


def _fixture_prompt_node_types(prompt: Mapping[str, object]) -> set[str]:
    node_types: set[str] = set()
    for node in prompt.values():
        if not isinstance(node, Mapping):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type:
            node_types.add(class_type)
    return node_types


def _workflow_execution_stage_result(
    fixture: SmokeExecutionFixture,
    execution_details: Mapping[str, object],
) -> SmokeStageResult:
    details = {
        "fixture": fixture.name,
        **dict(execution_details),
    }
    output_node_count = _optional_int(execution_details.get("output_node_count"))
    if (
        fixture.expected_output_node_count is not None
        and output_node_count != fixture.expected_output_node_count
    ):
        return SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message="Workflow execution smoke output count did not match fixture expectation.",
            details={
                **details,
                "expected_output_node_count": fixture.expected_output_node_count,
                "actual_output_node_count": output_node_count,
            },
        )

    if fixture.expected_output_node_ids:
        actual_ids = _string_set(execution_details.get("output_node_ids"))
        missing_ids = sorted(
            set(str(node_id) for node_id in fixture.expected_output_node_ids)
            - actual_ids
        )
        if missing_ids:
            return SmokeStageResult(
                status=SmokeStageStatus.FAILED,
                message="Workflow execution smoke outputs are missing expected node ids.",
                details={
                    **details,
                    "expected_output_node_ids": sorted(
                        str(node_id) for node_id in fixture.expected_output_node_ids
                    ),
                    "missing_output_node_ids": missing_ids,
                },
            )

    return SmokeStageResult(
        status=SmokeStageStatus.PASSED,
        message="Workflow execution smoke fixture completed.",
        details=details,
    )


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return set()
    return {str(item) for item in value}


async def _fetch_object_info(base_url: str) -> Mapping[str, object]:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(f"{base_url.rstrip('/')}/object_info")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("ComfyUI /object_info returned a non-object payload.")
    return payload


async def _check_dependency_imports(
    prepared_workspace: PreparedRuntimeWorkspace,
) -> SmokeStageResult:
    return await _check_dependency_imports_with_runner(
        prepared_workspace,
        command_runner=_run_dependency_import_command,
    )


async def _check_dependency_imports_with_runner(
    prepared_workspace: PreparedRuntimeWorkspace,
    *,
    command_runner: DependencyImportCommandRunner,
) -> SmokeStageResult:
    lock_path = prepared_workspace.dependency_env_path / "noofy-dependency-lock.json"
    if not lock_path.exists():
        return SmokeStageResult(
            status=SmokeStageStatus.PASSED,
            message="No dependency lock file is present; no dependency imports were required.",
        )

    try:
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message=f"Dependency lock file is unreadable: {exc}",
        )

    import_targets = _dependency_import_targets(lock_payload)
    if not import_targets:
        return SmokeStageResult(
            status=SmokeStageStatus.PASSED,
            message="Dependency lock has no third-party wheels to import-check.",
            details={"dependency_lock_path": str(lock_path)},
        )

    python_path = _dependency_env_python_path(prepared_workspace.dependency_env_path)
    if not python_path.exists():
        return SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message="Dependency environment Python executable is missing.",
            details={
                "python_path": str(python_path),
                "import_targets": import_targets,
            },
        )

    script = (
        "import importlib\n"
        "import importlib.metadata\n"
        f"targets = {import_targets!r}\n"
        "for target in targets:\n"
        "    package = target['package_name']\n"
        "    names = target.get('import_names') or []\n"
        "    if not names:\n"
        "        dist = importlib.metadata.distribution(package)\n"
        "        top_level = dist.read_text('top_level.txt')\n"
        "        names = [line.strip() for line in top_level.splitlines() if line.strip()] if top_level else [package.replace('-', '_')]\n"
        "    for name in names:\n"
        "        importlib.import_module(name)\n"
    )
    returncode, output = await command_runner(
        [str(python_path), "-c", script], prepared_workspace
    )
    if returncode != 0:
        return SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message="Dependency environment import smoke failed.",
            details={
                "import_targets": import_targets,
                "output": output[-2000:],
            },
        )

    return SmokeStageResult(
        status=SmokeStageStatus.PASSED,
        message="Dependency environment imports succeeded.",
        details={"import_targets": import_targets},
    )


async def _run_dependency_import_command(
    command: list[str],
    prepared_workspace: PreparedRuntimeWorkspace,
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=prepared_workspace.dependency_env_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output_bytes, _ = await process.communicate()
    output = output_bytes.decode(errors="replace") if output_bytes else ""
    return process.returncode or 0, output


def _dependency_import_targets(
    lock_payload: Mapping[str, object],
) -> list[dict[str, object]]:
    wheels = lock_payload.get("wheels", [])
    if not isinstance(wheels, list):
        return []
    targets: dict[str, set[str]] = {}
    for wheel in wheels:
        if not isinstance(wheel, dict):
            continue
        relationship = wheel.get("relationship")
        if relationship == "core":
            continue
        name = wheel.get("name")
        if isinstance(name, str) and name:
            raw_import_names = wheel.get("import_names", [])
            targets[name] = (
                {str(item) for item in raw_import_names if isinstance(item, str)}
                if isinstance(raw_import_names, list)
                else set()
            )
    return [
        {
            "package_name": package_name,
            "import_names": sorted(import_names),
        }
        for package_name, import_names in sorted(targets.items())
    ]


def _dependency_env_python_path(dependency_env_path: Any) -> Any:
    venv_dir = dependency_env_path / "venv"
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


async def _execute_prompt_and_wait(
    base_url: str,
    prompt: dict[str, object],
    timeout_seconds: float,
) -> Mapping[str, object] | None:
    prompt_id = f"noofy-smoke-{uuid4()}"
    client_id = f"noofy-smoke-{uuid4()}"
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/prompt",
                json={
                    "prompt": prompt,
                    "prompt_id": prompt_id,
                    "client_id": client_id,
                    "extra_data": {"noofy_smoke_test": True},
                },
            )
            response.raise_for_status()
            submitted = response.json()
            if isinstance(submitted, dict) and submitted.get("prompt_id"):
                prompt_id = str(submitted["prompt_id"])

            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                history_response = await client.get(
                    f"{base_url.rstrip('/')}/history/{prompt_id}"
                )
                history_response.raise_for_status()
                history = history_response.json()
                if isinstance(history, dict) and prompt_id in history:
                    entry = history[prompt_id]
                    if isinstance(entry, dict):
                        status = entry.get("status", {})
                        if (
                            isinstance(status, dict)
                            and status.get("completed") is False
                        ):
                            raise RuntimeError(
                                str(
                                    status.get("messages")
                                    or "ComfyUI smoke prompt failed."
                                )
                            )
                        outputs = entry.get("outputs", {})
                        output_node_ids = (
                            sorted(str(node_id) for node_id in outputs.keys())
                            if isinstance(outputs, dict)
                            else []
                        )
                        return {
                            "prompt_id": prompt_id,
                            "output_node_count": len(output_node_ids),
                            "output_node_ids": output_node_ids,
                        }
                await asyncio.sleep(0.25)
    except httpx.TimeoutException as exc:
        raise SmokePromptTimeoutError(
            prompt_id=prompt_id, timeout_seconds=timeout_seconds
        ) from exc
    raise SmokePromptTimeoutError(prompt_id=prompt_id, timeout_seconds=timeout_seconds)
