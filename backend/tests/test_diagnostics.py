import inspect
import re
from pathlib import Path

from app.diagnostics import DiagnosticsStore, LogStore
from app.engine.process_manager import ComfyUIProcessManager
from app.engine.service import EngineService, _diagnostic_event_payload, _install_developer_details
from app.engine.comfyui_adapter import ComfyUIEngineAdapter
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.comfyui.comfyui_updates import ComfyUIUpdateService
from app.runtime.dependencies.dependency_env import UvDependencyEnvironmentInstaller
from app.runtime.dependencies.dependency_resolver import UvDependencyLockResolver
from app.runtime.environment import RuntimeEnvironment
from app.runtime.install_transactions import InstallTransactionStore
from app.runtime.dependencies.isolation import InstallState, InstallStatus, SmokeTestStatus
from app.runtime.manager import RuntimeManager
from app.runtime.models.model_store import ModelStore
from app.runtime.node_registry import CustomNodeSourceCache, NodeRegistryResolver
from app.runtime.runners.runner_coordinator import RunnerProcessCoordinator
from app.runtime.runners.runner_process import RunnerProcessSupervisor
from app.runtime.smoke_test import RunnerSmokeTester
from app.runtime.storage.storage_gc import RuntimeStorageGarbageCollector
from app.runtime.storage.workspace_preparer import RuntimeWorkspacePreparer
from app.workflows.authoring import DashboardAuthoringService
from app.workflows.importer import ImportedWorkflowPackageStore


def test_log_store_filters_by_job_and_latest_error() -> None:
    store = LogStore()
    store.add("info", "global event", "test")
    store.add("error", "job failed", "test", job_id="job-1")
    store.add("warning", "other job warning", "test", job_id="job-2")

    job_logs = store.list_events(job_id="job-1")

    assert len(job_logs.events) == 1
    assert job_logs.events[0].message == "job failed"
    assert store.latest_error() is not None
    assert store.latest_error().job_id == "job-1"


def test_log_store_add_is_thread_safe_under_concurrent_writers() -> None:
    # Parallel verification writes diagnostics from multiple worker threads at once;
    # ids must stay unique and no event may be lost or corrupt the deque.
    import threading

    store = LogStore(max_events=10_000)
    writers = 8
    per_writer = 250
    start = threading.Barrier(writers)

    def writer(index: int) -> None:
        start.wait()
        for n in range(per_writer):
            store.add("info", f"event-{index}-{n}", "test")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(writers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    events = store.list_events(limit=10_000).events
    assert len(events) == writers * per_writer
    ids = [event.id for event in events]
    assert len(set(ids)) == len(ids)  # no duplicate ids from the id counter race


def test_diagnostic_payload_redacts_secrets_and_hides_developer_details_by_default() -> (
    None
):
    store = LogStore()
    event = store.add(
        "error",
        "runner failed",
        "runtime.runner_process",
        job_id="job-1",
        workflow_id="workflow-1",
        details={
            "runner_id": "runner-1",
            "authorization": "Bearer secret-token",
            "nested": {"signed_url": "https://example.invalid/model?token=secret"},
            "path": "/Users/alice/private/model.safetensors",
        },
    )

    default_payload = _diagnostic_event_payload(event, include_developer_details=False)
    developer_payload = _diagnostic_event_payload(event, include_developer_details=True)

    assert default_payload["correlation_ids"] == {
        "workflow_id": "workflow-1",
        "job_id": "job-1",
        "runner_id": "runner-1",
    }
    assert "developer_details" not in default_payload
    assert developer_payload["developer_details"]["authorization"] == "[redacted]"
    assert (
        developer_payload["developer_details"]["nested"]["signed_url"] == "[redacted]"
    )
    assert developer_payload["developer_details"]["path"] == "[local-path-redacted]"


def test_diagnostic_payload_redacts_private_paths_from_default_message() -> None:
    store = LogStore()
    event = store.add(
        "error",
        "Failed while reading /Users/alice/private/workflow/capsule.lock.json",
        "engine.service",
        workflow_id="workflow-1",
    )

    payload = _diagnostic_event_payload(event, include_developer_details=False)

    assert "/Users/alice" not in payload["message"]
    assert "[local-path-redacted]" in payload["message"]


def test_log_store_redacts_prompt_like_details_and_truncates_large_payloads() -> None:
    store = LogStore()
    event = store.add(
        "warning",
        "payload was too large",
        "test",
        details={
            "prompt": "a private full user prompt",
            "prompt_id": "job-1",
            "items": list(range(60)),
            "large_text": "x" * 2500,
        },
    )

    assert event.details["prompt"] == "[redacted]"
    assert event.details["prompt_id"] == "job-1"
    assert len(event.details["items"]) == 51
    assert event.details["items"][-1] == "[truncated 10 more items]"
    assert "[truncated 500 chars]" in event.details["large_text"]


def test_install_developer_details_redacts_private_paths() -> None:
    state = InstallState(
        schema_version="0.1.0",
        capsule_fingerprint="capsule",
        status=InstallStatus.FAILED,
        smoke_test_status=SmokeTestStatus.FAILED,
        last_error="Import failed from /Users/alice/private/custom_nodes/node.py",
        dependency_env_path="/Users/alice/noofy/runtime-store/envs/dep-env-a",
    )

    details = _install_developer_details(state)

    assert "/Users/alice" not in details["last_error"]
    assert details["dependency_env_path"] == "[local-path-redacted]"


def test_diagnostic_emitters_require_explicit_sink() -> None:
    emitters = [
        CapsuleInstaller,
        ComfyUIProcessManager,
        ComfyUIEngineAdapter,
        ComfyUIUpdateService,
        CustomNodeSourceCache,
        DashboardAuthoringService,
        ImportedWorkflowPackageStore,
        InstallTransactionStore,
        ModelStore,
        NodeRegistryResolver,
        RunnerProcessCoordinator,
        RunnerProcessSupervisor,
        RunnerSmokeTester,
        RuntimeEnvironment,
        RuntimeManager,
        RuntimeStorageGarbageCollector,
        RuntimeWorkspacePreparer,
        UvDependencyEnvironmentInstaller,
        UvDependencyLockResolver,
    ]

    for emitter in emitters:
        parameter = inspect.signature(emitter).parameters["log_store"]
        assert parameter.default is inspect.Signature.empty, emitter


def test_engine_service_uses_read_write_diagnostics_protocol() -> None:
    parameter = inspect.signature(EngineService).parameters["log_store"]

    assert parameter.annotation is DiagnosticsStore


def test_child_component_diagnostics_are_visible_through_service_logs(tmp_path: Path) -> None:
    log_store = LogStore()
    service = EngineService(
        workflow_loader=object(),
        workflow_validator=object(),
        runner_supervisor=object(),
        runtime_manager=object(),
        log_store=log_store,
    )
    adapter = ComfyUIEngineAdapter(
        "http://127.0.0.1:8188",
        tmp_path / "models",
        log_store=log_store,
    )

    adapter.log_store.add(
        "warning",
        "Child component diagnostic",
        "comfyui.adapter",
        job_id="job-1",
    )

    logs = service.list_logs()
    assert logs.events[-1].message == "Child component diagnostic"
    assert logs.events[-1].job_id == "job-1"


def test_production_diagnostics_do_not_create_private_log_store_fallbacks() -> None:
    app_dir = Path(__file__).resolve().parents[1] / "app"
    allowed_construction_files = {
        Path("engine/factory.py"),
        Path("engine/service.py"),
    }
    construction_pattern = re.compile(r"\bLogStore\s*\(")

    unexpected_constructions: list[str] = []
    private_fallbacks: list[str] = []
    for path in app_dir.rglob("*.py"):
        relative_path = path.relative_to(app_dir)
        text = path.read_text(encoding="utf-8")
        if re.search(r"log_store\s+(?:or|\|\|)\s+LogStore\s*\(", text):
            private_fallbacks.append(str(relative_path))
        if construction_pattern.search(text) and relative_path not in allowed_construction_files:
            unexpected_constructions.append(str(relative_path))

    assert private_fallbacks == []
    assert unexpected_constructions == []
