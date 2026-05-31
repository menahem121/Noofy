"""Shared helpers for bounded-parallel model verification jobs.

Both the import verification job ([import_orchestrator]) and the runtime pre-run
verification job ([library_service]) hash required model files to confirm identity.
Hashing is the slow part, so these helpers run several models concurrently (bounded)
and emit a single structured completion diagnostic. The full-file SHA-256 compare is
unchanged — this only overlaps independent work and reports what happened.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from typing import TypeVar

from app.diagnostics import DiagnosticsSink
from app.engine.models import RequiredModelAvailability
from app.workflows.model_availability import VerifyHashMetrics, requirement_id_for
from app.workflows.package import WorkflowPackage

ModelT = TypeVar("ModelT")
ResultT = TypeVar("ResultT")

# Downgrade reasons where Noofy auto-disabled parallel verification based on filesystem
# detection (surprising to the user and worth a visible warning). A deliberate
# config_override, a single model, or no downgrade are expected and not warned about.
SERIAL_DOWNGRADE_REASONS = frozenset({"network_fs", "rotational"})


def log_verification_concurrency(
    log_store: DiagnosticsSink,
    *,
    workflow_id: str,
    model_count: int,
    selected_concurrency: int,
    downgrade_reason: str,
) -> None:
    """Warn (up front) when filesystem detection forced verification to run serially.

    The selected concurrency is always recorded on the completion diagnostic; this only
    surfaces the surprising auto-clamp case prominently so a serial downgrade on
    network/rotational storage is easy to spot (and points at the override).
    """
    if downgrade_reason not in SERIAL_DOWNGRADE_REASONS:
        return
    log_store.add(
        "warning",
        "Model verification running serially (parallelism auto-disabled for this storage)",
        "workflow.models",
        workflow_id=workflow_id,
        details={
            "model_count": model_count,
            "selected_concurrency": selected_concurrency,
            "downgrade_reason": downgrade_reason,
        },
    )


def order_by_package(
    package: WorkflowPackage,
    availabilities: list[RequiredModelAvailability],
) -> list[RequiredModelAvailability]:
    """Return ``availabilities`` in the package's required-model order.

    Parallel verification records results in completion order, so final summaries and
    log lists are normalized back to the deterministic package order for stable UI and
    debugging. Any availability whose id is not in the package keeps a stable trailing
    position rather than being dropped.
    """
    order = {
        requirement_id_for(model): index
        for index, model in enumerate(package.required_models)
    }
    trailing = len(order)
    return sorted(
        availabilities, key=lambda item: order.get(item.requirement_id, trailing)
    )


async def run_parallel_model_verification(
    models: Iterable[ModelT],
    *,
    verify: Callable[[ModelT], ResultT],
    on_start: Callable[[int, ModelT], None],
    on_result: Callable[[ResultT], None],
    concurrency: int,
) -> None:
    """Verify ``models`` with bounded parallelism, preserving failure semantics.

    Each model is verified in a worker thread (``verify`` runs via ``asyncio.to_thread``)
    under an ``asyncio.Semaphore``. ``on_start`` and ``on_result`` mutate job state and
    run on the event loop, so the caller needs no locking. Results are delivered in
    completion order. If any worker raises, the remaining tasks are cancelled and the
    exception propagates so the caller marks the job failed.
    """
    model_list = list(models)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(index: int, model: ModelT) -> ResultT:
        async with semaphore:
            on_start(index, model)
            return await asyncio.to_thread(verify, model)

    tasks = [
        asyncio.create_task(run_one(index, model))
        for index, model in enumerate(model_list, start=1)
    ]
    try:
        for completed in asyncio.as_completed(tasks):
            on_result(await completed)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def log_verification_metrics(
    log_store: DiagnosticsSink,
    *,
    workflow_id: str,
    duration_ms: int,
    model_count: int,
    metrics: VerifyHashMetrics,
    selected_concurrency: int,
    downgrade_reason: str,
) -> None:
    """Emit one structured "Model verification completed" diagnostic for a finished job."""
    log_store.add(
        "info",
        "Model verification completed",
        "workflow.models",
        workflow_id=workflow_id,
        details={
            "duration_ms": duration_ms,
            "model_count": model_count,
            "file_count": model_count,
            "cache_hits": metrics.cache_hits,
            "cache_misses": metrics.cache_misses,
            "bytes_hashed": metrics.bytes_hashed,
            "selected_concurrency": selected_concurrency,
            "downgrade_reason": downgrade_reason,
        },
    )
