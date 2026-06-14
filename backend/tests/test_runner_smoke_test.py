import json
import os
from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.runtime.dependencies.isolation import CapsuleLock, SmokeStageResult, SmokeStageStatus
from app.runtime.runners.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.smoke_test import (
    RunnerSmokeTester,
    RunnerSmokeTestError,
    SmokeExecutionFixture,
    SmokePromptTimeoutError,
    _check_dependency_imports_with_runner,
    _execute_prompt_and_wait,
    _fetch_object_info,
)
from app.runtime.runners.supervisor import RunnerKind
from app.runtime.storage.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.storage.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)


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


def _capsule_lock(*, custom_nodes: list[dict] | None = None) -> CapsuleLock:
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
                "runtime_profile_id": "noofy-comfyui-v1-default",
                "runtime_profile_variant_id": "darwin-arm64-mps-dev",
                "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
                "runtime_profile_catalog_version": "0.1.0",
                "fingerprint_schema_version": "0.1.0",
                "dependency_env_fingerprint": "sha256:" + ("b" * 64),
                "runner_fingerprint": "sha256:" + ("c" * 64),
                "capsule_fingerprint": "sha256:" + ("d" * 64),
                "os": "darwin",
                "architecture": "arm64",
                "python_version": "3.11",
                "python_build_id": "cpython-3.11-noofy-dev",
                "gpu_backend": "mps",
                "dependency_lock_hash": "sha256:" + ("e" * 64),
                "runner_workspace_hash": "sha256:" + ("f" * 64),
            },
            "custom_nodes": custom_nodes or [],
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
        runner_workspace_store=RunnerWorkspaceManifestStore(
            tmp_path / "runner-workspaces"
        ),
        log_store=LogStore(),
    )
    return preparer.prepare(_capsule_lock())


def _prompt_node_types(prompt: dict[str, object]) -> set[str]:
    node_types: set[str] = set()
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str) and class_type:
            node_types.add(class_type)
    return node_types


@pytest.mark.anyio
async def test_real_comfyui_smoke_fixture_executes_when_enabled() -> None:
    if os.environ.get("NOOFY_REAL_COMFYUI_SMOKE") != "1":
        pytest.skip(
            "Set NOOFY_REAL_COMFYUI_SMOKE=1 to run the optional real ComfyUI smoke test."
        )

    prompt_path = os.environ.get("NOOFY_REAL_COMFYUI_SMOKE_PROMPT")
    if not prompt_path:
        pytest.fail(
            "Set NOOFY_REAL_COMFYUI_SMOKE_PROMPT to a small ComfyUI API prompt JSON file."
        )

    base_url = os.environ.get("NOOFY_REAL_COMFYUI_BASE_URL", "http://127.0.0.1:8188")
    timeout_seconds = float(os.environ.get("NOOFY_REAL_COMFYUI_SMOKE_TIMEOUT", "120"))
    minimum_outputs = int(os.environ.get("NOOFY_REAL_COMFYUI_SMOKE_MIN_OUTPUTS", "1"))
    prompt = json.loads(Path(prompt_path).read_text(encoding="utf-8"))
    assert isinstance(prompt, dict)

    object_info = await _fetch_object_info(base_url)
    missing_node_types = sorted(
        _prompt_node_types(prompt)
        - {str(node_type) for node_type in object_info.keys()}
    )
    assert missing_node_types == []

    result = await _execute_prompt_and_wait(base_url, prompt, timeout_seconds)

    assert result is not None
    assert isinstance(result.get("prompt_id"), str)
    assert result.get("output_node_count", 0) >= minimum_outputs


@pytest.mark.anyio
async def test_real_staged_comfyui_runner_smoke_executes_when_enabled(
    tmp_path: Path,
) -> None:
    if os.environ.get("NOOFY_REAL_STAGED_COMFYUI_SMOKE") != "1":
        pytest.skip(
            "Set NOOFY_REAL_STAGED_COMFYUI_SMOKE=1 to run the optional staged ComfyUI smoke test."
        )

    source_dir = os.environ.get("NOOFY_REAL_COMFYUI_SOURCE_DIR")
    python_executable = os.environ.get("NOOFY_REAL_COMFYUI_PYTHON")
    prompt_path = os.environ.get("NOOFY_REAL_COMFYUI_SMOKE_PROMPT")
    if not source_dir:
        pytest.fail("Set NOOFY_REAL_COMFYUI_SOURCE_DIR to a ComfyUI source checkout.")
    if not python_executable:
        pytest.fail(
            "Set NOOFY_REAL_COMFYUI_PYTHON to a Python executable with ComfyUI dependencies."
        )
    if not prompt_path:
        pytest.fail(
            "Set NOOFY_REAL_COMFYUI_SMOKE_PROMPT to a small ComfyUI API prompt JSON file."
        )

    prompt = json.loads(Path(prompt_path).read_text(encoding="utf-8"))
    assert isinstance(prompt, dict)
    timeout_seconds = float(os.environ.get("NOOFY_REAL_COMFYUI_SMOKE_TIMEOUT", "120"))
    minimum_outputs = int(os.environ.get("NOOFY_REAL_COMFYUI_SMOKE_MIN_OUTPUTS", "1"))
    capsule_lock = _capsule_lock()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(
            tmp_path / "runner-workspaces"
        ),
        comfyui_source_dir=Path(source_dir),
        log_store=LogStore(),
    )
    prepared = preparer.prepare(capsule_lock)
    smoke_tester = RunnerSmokeTester(
        process_supervisor=RunnerProcessSupervisor(
            startup_timeout_seconds=timeout_seconds,
            health_poll_interval_seconds=0.5,
        ),
        launch_spec_factory=lambda capsule_lock, prepared_workspace: RunnerLaunchSpec(
            runner_id="real-staged-smoke",
            kind=RunnerKind.ISOLATED_COMFYUI,
            fingerprint=capsule_lock.runtime.runner_fingerprint,
            python_executable=python_executable,
            working_dir=prepared_workspace.runner_workspace_path,
            dependency_env_path=prepared_workspace.dependency_env_path,
            runner_workspace_path=prepared_workspace.runner_workspace_path,
            extra_args=[
                "--base-directory",
                str(prepared_workspace.runner_workspace_path),
                "--disable-auto-launch",
                "--disable-all-custom-nodes",
            ],
        ),
        execution_fixture=SmokeExecutionFixture(
            name="real-staged-comfyui",
            prompt=prompt,
            required_node_types=sorted(_prompt_node_types(prompt)),
            timeout_seconds=timeout_seconds,
        ),
        log_store=LogStore(),
    )

    report = await smoke_tester.run(capsule_lock, prepared)

    assert report.dependency_env.status is SmokeStageStatus.PASSED
    assert report.runner_health.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.status is SmokeStageStatus.PASSED
    assert (
        report.workflow_execution.details.get("output_node_count", 0) >= minimum_outputs
    )


@pytest.mark.anyio
async def test_runner_smoke_tester_starts_health_checks_and_stops_runner(
    tmp_path: Path,
) -> None:
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
        log_store=LogStore(),
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

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert created[0][0][:2] == ["/opt/noofy/python", "main.py"]
    assert created[0][1]["cwd"] == prepared.runner_workspace_path
    assert process.terminated
    assert (await process_supervisor.status("smoke-1")).status.value == "stopped"
    assert report.runner_health.status is SmokeStageStatus.PASSED
    assert report.custom_node_import.status is SmokeStageStatus.SKIPPED
    assert report.workflow_execution.status is SmokeStageStatus.BLOCKED
    messages = [event.message for event in log_store.list_events().events]
    assert "Runner smoke test starting" in messages
    assert "Runner smoke test completed" in messages


@pytest.mark.anyio
async def test_runner_smoke_tester_executes_fixture_after_node_metadata_check(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    executed: list[tuple[str, dict[str, object], float]] = []

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        assert base_url == "http://127.0.0.1:9191"
        return {"NoOp": {}, "PreviewImage": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        executed.append((base_url, prompt, timeout_seconds))
        return {"prompt_id": "smoke-prompt", "output_node_count": 1}

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        execution_fixture=SmokeExecutionFixture(
            name="noop-fixture",
            prompt={"1": {"class_type": "NoOp", "inputs": {}}},
            required_node_types=("NoOp",),
            expected_output_node_count=1,
            timeout_seconds=7,
        ),
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert process.terminated
    assert executed == [
        ("http://127.0.0.1:9191", {"1": {"class_type": "NoOp", "inputs": {}}}, 7)
    ]
    assert report.dependency_env.status is SmokeStageStatus.PASSED
    assert report.runner_health.status is SmokeStageStatus.PASSED
    assert report.custom_node_import.status is SmokeStageStatus.SKIPPED
    assert report.workflow_execution.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.details["prompt_id"] == "smoke-prompt"


@pytest.mark.anyio
async def test_runner_smoke_tester_fails_when_execution_output_count_mismatches(
    tmp_path: Path,
) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"NoOp": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        return {
            "prompt_id": "smoke-prompt",
            "output_node_count": 0,
            "output_node_ids": [],
        }

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        execution_fixture=SmokeExecutionFixture(
            name="noop-fixture",
            prompt={"1": {"class_type": "NoOp", "inputs": {}}},
            required_node_types=("NoOp",),
            expected_output_node_count=1,
        ),
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert report.workflow_execution.status is SmokeStageStatus.FAILED
    assert report.workflow_execution.details["expected_output_node_count"] == 1
    assert report.workflow_execution.details["actual_output_node_count"] == 0


@pytest.mark.anyio
async def test_runner_smoke_tester_fails_when_expected_output_node_id_is_missing(
    tmp_path: Path,
) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"NoOp": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        return {
            "prompt_id": "smoke-prompt",
            "output_node_count": 1,
            "output_node_ids": ["9"],
        }

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        execution_fixture=SmokeExecutionFixture(
            name="noop-fixture",
            prompt={"1": {"class_type": "NoOp", "inputs": {}}},
            required_node_types=("NoOp",),
            expected_output_node_ids=("10",),
        ),
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert report.workflow_execution.status is SmokeStageStatus.FAILED
    assert report.workflow_execution.details["missing_output_node_ids"] == ["10"]


@pytest.mark.anyio
async def test_runner_smoke_tester_reports_execution_timeout_with_prompt_id(
    tmp_path: Path,
) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"NoOp": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        raise SmokePromptTimeoutError(
            prompt_id="smoke-prompt", timeout_seconds=timeout_seconds
        )

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        execution_fixture=SmokeExecutionFixture(
            name="noop-fixture",
            prompt={"1": {"class_type": "NoOp", "inputs": {}}},
            required_node_types=("NoOp",),
            timeout_seconds=2,
        ),
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert report.workflow_execution.status is SmokeStageStatus.FAILED
    assert report.workflow_execution.message == "Workflow execution smoke timed out."
    assert report.workflow_execution.details == {
        "fixture": "noop-fixture",
        "timeout_seconds": 2,
        "prompt_id": "smoke-prompt",
    }


@pytest.mark.anyio
async def test_runner_smoke_tester_fails_execution_when_fixture_node_is_missing(
    tmp_path: Path,
) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"OtherNode": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        raise AssertionError(
            "prompt executor should not run when required nodes are absent"
        )

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        execution_fixture=SmokeExecutionFixture(
            name="noop-fixture",
            prompt={"1": {"class_type": "NoOp", "inputs": {}}},
            required_node_types=("NoOp",),
        ),
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert process.terminated
    assert report.workflow_execution.status is SmokeStageStatus.FAILED
    assert report.workflow_execution.details["missing_node_types"] == ["NoOp"]


@pytest.mark.anyio
async def test_runner_smoke_tester_uses_per_capsule_fixture_resolver(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    executed: list[tuple[str, dict[str, object], float]] = []

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"FixtureNode": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        executed.append((base_url, prompt, timeout_seconds))
        return {"prompt_id": "resolved-fixture"}

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
    prepared = _prepared_workspace(tmp_path)

    def fixture_resolver(capsule_lock, prepared_workspace):
        assert capsule_lock.workflow.package_id == "text_to_image_v0"
        assert prepared_workspace == prepared
        return SmokeExecutionFixture(
            name="resolved",
            prompt={"1": {"class_type": "FixtureNode", "inputs": {}}},
            required_node_types=("FixtureNode",),
            timeout_seconds=3,
        )

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
        execution_fixture_resolver=fixture_resolver,
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert report.workflow_execution.status is SmokeStageStatus.PASSED
    assert executed == [
        ("http://127.0.0.1:9191", {"1": {"class_type": "FixtureNode", "inputs": {}}}, 3)
    ]


@pytest.mark.anyio
async def test_runner_smoke_tester_verifies_custom_node_registration(
    tmp_path: Path,
) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"CustomSamplerNode": {}, "PreviewImage": {}}

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        object_info_fetcher=object_info,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(
        _capsule_lock(
            custom_nodes=[
                {
                    "package_id": "custom-sampler",
                    "source": "https://example.invalid/custom-sampler.git",
                    "trust_level": "quarantined_community",
                    "node_types": ["CustomSamplerNode"],
                }
            ]
        ),
        prepared,
    )

    assert report.custom_node_import.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.status is SmokeStageStatus.BLOCKED


@pytest.mark.anyio
async def test_runner_smoke_tester_requires_custom_node_fixture_to_exercise_custom_node(
    tmp_path: Path,
) -> None:
    process = FakeProcess()
    prompt_called = False

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"CustomSamplerNode": {}, "NoOp": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        nonlocal prompt_called
        prompt_called = True
        return {
            "prompt_id": "smoke-prompt",
            "output_node_count": 0,
            "output_node_ids": [],
        }

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        execution_fixture=SmokeExecutionFixture(
            name="core-only-fixture",
            prompt={"1": {"class_type": "NoOp", "inputs": {}}},
            required_node_types=("NoOp",),
        ),
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(
        _capsule_lock(
            custom_nodes=[
                {
                    "package_id": "custom-sampler",
                    "source": "https://example.invalid/custom-sampler.git",
                    "trust_level": "quarantined_community",
                    "node_types": ["CustomSamplerNode"],
                }
            ]
        ),
        prepared,
    )

    assert not prompt_called
    assert report.custom_node_import.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.status is SmokeStageStatus.FAILED
    assert report.workflow_execution.message == (
        "Workflow execution smoke fixture does not exercise declared custom node types."
    )
    assert report.workflow_execution.details["declared_custom_node_types"] == [
        "CustomSamplerNode"
    ]
    assert report.workflow_execution.details["fixture_node_types"] == ["NoOp"]


@pytest.mark.anyio
async def test_runner_smoke_tester_records_custom_node_types_exercised_by_fixture(
    tmp_path: Path,
) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"CustomSamplerNode": {}}

    async def prompt_executor(
        base_url: str, prompt: dict[str, object], timeout_seconds: float
    ):
        return {
            "prompt_id": "smoke-prompt",
            "output_node_count": 1,
            "output_node_ids": ["1"],
        }

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        execution_fixture=SmokeExecutionFixture(
            name="custom-fixture",
            prompt={"1": {"class_type": "CustomSamplerNode", "inputs": {}}},
            required_node_types=("CustomSamplerNode",),
            expected_output_node_count=1,
            expected_output_node_ids=("1",),
        ),
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(
        _capsule_lock(
            custom_nodes=[
                {
                    "package_id": "custom-sampler",
                    "source": "https://example.invalid/custom-sampler.git",
                    "trust_level": "quarantined_community",
                    "node_types": ["CustomSamplerNode"],
                }
            ]
        ),
        prepared,
    )

    assert report.custom_node_import.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.details["exercised_custom_node_types"] == [
        "CustomSamplerNode"
    ]


@pytest.mark.anyio
async def test_runner_smoke_tester_reports_missing_custom_node_registration(
    tmp_path: Path,
) -> None:
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {"PreviewImage": {}}

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        object_info_fetcher=object_info,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(
        _capsule_lock(
            custom_nodes=[
                {
                    "package_id": "custom-sampler",
                    "source": "https://example.invalid/custom-sampler.git",
                    "trust_level": "quarantined_community",
                    "node_types": ["CustomSamplerNode"],
                }
            ]
        ),
        prepared,
    )

    assert report.custom_node_import.status is SmokeStageStatus.FAILED
    assert report.custom_node_import.details["missing_node_types"] == [
        "CustomSamplerNode"
    ]
    assert report.workflow_execution.status is SmokeStageStatus.BLOCKED


@pytest.mark.anyio
async def test_dependency_import_smoke_runs_declared_non_core_wheels(
    tmp_path: Path,
) -> None:
    prepared = _prepared_workspace(tmp_path)
    python_path = prepared.dependency_env_path / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("# fake python\n", encoding="utf-8")
    (prepared.dependency_env_path / "noofy-dependency-lock.json").write_text(
        json.dumps(
            {
                "wheels": [
                    {
                        "name": "demo-package",
                        "relationship": "direct",
                        "import_names": ["demo_import"],
                    },
                    {"name": "core-package", "relationship": "core"},
                ]
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    async def command_runner(command: list[str], prepared_workspace):
        commands.append(command)
        return 0, ""

    result = await _check_dependency_imports_with_runner(
        prepared, command_runner=command_runner
    )

    assert result.status is SmokeStageStatus.PASSED
    assert result.details["import_targets"] == [
        {"package_name": "demo-package", "import_names": ["demo_import"]}
    ]
    assert commands[0][0] == str(python_path)
    assert "demo-package" in commands[0][2]
    assert "demo_import" in commands[0][2]
    assert "core-package" not in commands[0][2]


@pytest.mark.anyio
async def test_dependency_import_smoke_uses_runtime_python_with_dependency_overlay(
    tmp_path: Path,
) -> None:
    prepared = _prepared_workspace(tmp_path)
    runtime_python = tmp_path / "runtime" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("# fake runtime python\n", encoding="utf-8")
    site_packages = (
        prepared.dependency_env_path / "venv" / "lib" / "python3.13" / "site-packages"
    )
    site_packages.mkdir(parents=True)
    (prepared.dependency_env_path / "noofy-dependency-lock.json").write_text(
        json.dumps(
            {
                "wheels": [
                    {
                        "name": "overlay-package",
                        "relationship": "direct",
                        "import_names": ["overlay_import"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    async def command_runner(command: list[str], prepared_workspace):
        commands.append(command)
        return 0, ""

    result = await _check_dependency_imports_with_runner(
        prepared,
        command_runner=command_runner,
        runtime_python_executable=str(runtime_python),
    )

    assert result.status is SmokeStageStatus.PASSED
    assert commands[0][0] == str(runtime_python)
    assert str(site_packages) in commands[0][2]
    assert "sys.path.append(path)" in commands[0][2]


@pytest.mark.anyio
async def test_dependency_import_smoke_reports_import_failure(tmp_path: Path) -> None:
    prepared = _prepared_workspace(tmp_path)
    python_path = prepared.dependency_env_path / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("# fake python\n", encoding="utf-8")
    (prepared.dependency_env_path / "noofy-dependency-lock.json").write_text(
        json.dumps(
            {
                "wheels": [
                    {
                        "name": "broken-package",
                        "relationship": "direct",
                        "import_names": ["broken_package"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    async def command_runner(command: list[str], prepared_workspace):
        return 1, "ModuleNotFoundError: No module named 'broken_package'"

    result = await _check_dependency_imports_with_runner(
        prepared, command_runner=command_runner
    )

    assert result.status is SmokeStageStatus.FAILED
    assert result.message == "Dependency environment import smoke failed."
    assert result.details["import_targets"] == [
        {"package_name": "broken-package", "import_names": ["broken_package"]}
    ]
    assert "ModuleNotFoundError" in result.details["output"]


@pytest.mark.anyio
async def test_dependency_import_smoke_reports_unsupported_accelerator_friendly(
    tmp_path: Path,
) -> None:
    prepared = _prepared_workspace(tmp_path)
    python_path = prepared.dependency_env_path / "venv" / "bin" / "python"
    python_path.parent.mkdir(parents=True)
    python_path.write_text("# fake python\n", encoding="utf-8")
    (prepared.dependency_env_path / "noofy-dependency-lock.json").write_text(
        json.dumps(
            {
                "wheels": [
                    {
                        "name": "needs-accel",
                        "relationship": "direct",
                        "import_names": ["needs_accel"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    async def command_runner(command: list[str], prepared_workspace):
        return 1, (
            "Traceback (most recent call last):\n"
            "ModuleNotFoundError: No module named 'xformers.ops'"
        )

    result = await _check_dependency_imports_with_runner(
        prepared, command_runner=command_runner
    )

    assert result.status is SmokeStageStatus.FAILED
    assert "accelerator package 'xformers'" in result.message
    assert "not supported by Noofy's stable runtime" in result.message
    assert result.details["unsupported_accelerator"] == "xformers"
    assert "ModuleNotFoundError" in result.details["output"]


@pytest.mark.anyio
async def test_runner_smoke_tester_does_not_start_runner_when_dependency_smoke_fails(
    tmp_path: Path,
) -> None:
    started = False

    async def process_factory(command: list[str], **kwargs):
        nonlocal started
        started = True
        return FakeProcess()

    async def healthy(base_url: str):
        return True, None

    async def dependency_import_checker(prepared_workspace):
        return SmokeStageResult(
            status=SmokeStageStatus.FAILED,
            message="Dependency import failed",
        )

    process_supervisor = RunnerProcessSupervisor(
        process_factory=process_factory,
        health_check=healthy,
        startup_timeout_seconds=0.1,
        health_poll_interval_seconds=0.001,
        log_store=LogStore(),
    )
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
        dependency_import_checker=dependency_import_checker,
        log_store=LogStore(),
    )

    report = await smoke_tester.run(_capsule_lock(), prepared)

    assert not started
    assert report.dependency_env.status is SmokeStageStatus.FAILED
    assert report.runner_health.status is SmokeStageStatus.NOT_RUN
    assert report.workflow_execution.status is SmokeStageStatus.NOT_RUN


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
        log_store=LogStore(),
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

    with pytest.raises(
        RunnerSmokeTestError, match="Runner smoke test failed"
    ) as exc_info:
        await smoke_tester.run(_capsule_lock(), prepared)

    assert process.terminated
    assert exc_info.value.report.runner_health.status is SmokeStageStatus.FAILED
    assert exc_info.value.report.workflow_execution.status is SmokeStageStatus.NOT_RUN
    latest_error = log_store.latest_error()
    assert latest_error is not None
    assert latest_error.message == "Runner smoke test failed"
    assert latest_error.details["error"]
