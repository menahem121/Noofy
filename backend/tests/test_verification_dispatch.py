from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.diagnostics import LogStore
from app.engine.models import RequiredModelAvailability
from app.workflows.model_availability import VerifyHashMetrics, requirement_id_for
from app.workflows.package import RequiredModel, WorkflowMetadata, WorkflowPackage
from app.workflows.verification_dispatch import (
    log_verification_concurrency,
    log_verification_metrics,
    order_by_package,
    run_parallel_model_verification,
)


def _run(models, *, verify, concurrency, on_start=None, on_result=None):
    results: list = []

    async def main() -> None:
        await run_parallel_model_verification(
            models,
            verify=verify,
            on_start=on_start or (lambda index, model: None),
            on_result=on_result or results.append,
            concurrency=concurrency,
        )

    asyncio.run(main())
    return results


def test_parallel_verification_returns_all_results_and_bounds_concurrency() -> None:
    concurrency = 2
    lock = threading.Lock()
    active = 0
    peak = 0

    def verify(model: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return model

    results = _run(list(range(8)), verify=verify, concurrency=concurrency)

    assert sorted(results) == list(range(8))
    # Safety property: never exceed the cap.
    assert peak <= concurrency
    # And it really did overlap work (not silently serial).
    assert peak >= 2


def test_serial_concurrency_never_overlaps() -> None:
    lock = threading.Lock()
    active = 0
    peak = 0

    def verify(model: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return model

    results = _run(list(range(5)), verify=verify, concurrency=1)

    assert sorted(results) == list(range(5))
    assert peak == 1


def test_parallel_verification_propagates_first_error() -> None:
    def verify(model: str) -> str:
        if model == "bad":
            raise RuntimeError("boom")
        time.sleep(0.05)
        return model

    with pytest.raises(RuntimeError, match="boom"):
        _run(["bad", "a", "b", "c"], verify=verify, concurrency=4)


def test_on_start_and_on_result_run_for_every_model() -> None:
    started: list[int] = []
    finished: list[int] = []

    def verify(model: int) -> int:
        return model * 10

    _run(
        [1, 2, 3],
        verify=verify,
        concurrency=2,
        on_start=lambda index, model: started.append(model),
        on_result=finished.append,
    )

    assert sorted(started) == [1, 2, 3]
    assert sorted(finished) == [10, 20, 30]


def test_empty_model_list_is_a_noop() -> None:
    def verify(_model: int) -> int:  # pragma: no cover - must never run
        raise AssertionError("verify should not be called for an empty list")

    assert _run([], verify=verify, concurrency=3) == []


def test_log_verification_metrics_emits_completion_record() -> None:
    log_store = LogStore()
    metrics = VerifyHashMetrics(cache_hits=2, cache_misses=1, bytes_hashed=4096)

    log_verification_metrics(
        log_store,
        workflow_id="wf-123",
        duration_ms=87,
        model_count=3,
        metrics=metrics,
        selected_concurrency=2,
        downgrade_reason="none",
    )

    events = [
        event
        for event in log_store.list_events().events
        if event.message == "Model verification completed"
    ]
    assert len(events) == 1
    event = events[0]
    assert event.workflow_id == "wf-123"
    assert event.details == {
        "duration_ms": 87,
        "model_count": 3,
        "file_count": 3,
        "cache_hits": 2,
        "cache_misses": 1,
        "bytes_hashed": 4096,
        "selected_concurrency": 2,
        "downgrade_reason": "none",
    }


@pytest.mark.parametrize("reason", ["network_fs", "rotational"])
def test_log_verification_concurrency_warns_on_auto_clamp(reason: str) -> None:
    log_store = LogStore()
    log_verification_concurrency(
        log_store,
        workflow_id="wf",
        model_count=3,
        selected_concurrency=1,
        downgrade_reason=reason,
    )
    warnings = [
        event
        for event in log_store.list_events().events
        if event.message.startswith("Model verification running serially")
    ]
    assert len(warnings) == 1
    assert warnings[0].level == "warning"
    assert warnings[0].details["downgrade_reason"] == reason
    assert warnings[0].details["selected_concurrency"] == 1


@pytest.mark.parametrize("reason", ["none", "single_model", "config_override"])
def test_log_verification_concurrency_is_silent_when_not_auto_clamped(reason: str) -> None:
    # Expected/deliberate cases (parallel, trivial single model, or an intentional config
    # override) must not produce warning noise; the completion diagnostic still records them.
    log_store = LogStore()
    log_verification_concurrency(
        log_store,
        workflow_id="wf",
        model_count=2,
        selected_concurrency=1,
        downgrade_reason=reason,
    )
    assert log_store.list_events().events == []


def _required_model(node_id: str, filename: str) -> RequiredModel:
    return RequiredModel(
        node_id=node_id,
        input_name="ckpt_name",
        folder="checkpoints",
        filename=filename,
        verification_level=ModelVerificationLevel.SHA256_SIZE,
    )


def _availability(model: RequiredModel) -> RequiredModelAvailability:
    return RequiredModelAvailability(
        requirement_id=requirement_id_for(model),
        node_id=model.node_id,
        input_name=model.input_name,
        filename=model.filename,
        folder=model.folder,
        verification_level=ModelVerificationLevel.SHA256_SIZE,
        status="available",
        status_label="Available",
        asset_ownership=AssetOwnership.USER_LOCAL,
    )


def test_order_by_package_restores_declared_order() -> None:
    models = [
        _required_model("node-a", "a.safetensors"),
        _required_model("node-b", "b.safetensors"),
        _required_model("node-c", "c.safetensors"),
    ]
    package = WorkflowPackage(
        metadata=WorkflowMetadata(id="wf", name="WF", version="0.1.0"),
        engine="comfyui",
        required_models=models,
        comfyui_graph={},
    )
    # Simulate completion order differing from declared order (c, a, b).
    completion_ordered = [_availability(models[2]), _availability(models[0]), _availability(models[1])]

    ordered = order_by_package(package, completion_ordered)

    assert [item.requirement_id for item in ordered] == [
        requirement_id_for(models[0]),
        requirement_id_for(models[1]),
        requirement_id_for(models[2]),
    ]


def test_order_by_package_keeps_unknown_ids_at_a_stable_tail() -> None:
    models = [_required_model("node-a", "a.safetensors"), _required_model("node-b", "b.safetensors")]
    package = WorkflowPackage(
        metadata=WorkflowMetadata(id="wf", name="WF", version="0.1.0"),
        engine="comfyui",
        required_models=models,
        comfyui_graph={},
    )
    stray = _availability(_required_model("node-z", "z.safetensors"))
    ordered = order_by_package(
        package, [stray, _availability(models[1]), _availability(models[0])]
    )

    # Known models come back in declared order; the unmatched one is kept (not dropped),
    # at the tail.
    assert [item.requirement_id for item in ordered] == [
        requirement_id_for(models[0]),
        requirement_id_for(models[1]),
        stray.requirement_id,
    ]
