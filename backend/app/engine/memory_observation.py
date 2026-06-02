from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any

from app.diagnostics import DiagnosticsStore
from app.engine.models import JobResult
from app.runtime.memory.memory_governor import (
    GpuProcessMemorySample,
    LocalMemoryLearningStore,
    LocalMemoryObservation,
    MachineMemoryObserver,
    MachineMemorySnapshot,
    MemoryAttributionQuality,
    MemoryBackend,
    MemoryObservationOutcome,
    MemorySampleWindow,
    ProcessTreeMemoryObserver,
    RunnerMemoryTelemetryReader,
    likely_memory_error,
)
from app.runtime.memory.input_features import extract_model_selection_features
from app.runtime.runners.supervisor import (
    JobRunnerNotFoundError,
    RunnerMemoryClass,
    RunnerSupervisor,
)


class MemoryObservationCoordinator:
    """Owns best-effort job memory sampling and local observation records."""

    def __init__(
        self,
        *,
        runner_supervisor: RunnerSupervisor,
        log_store: DiagnosticsStore,
        memory_observer: MachineMemoryObserver | None,
        process_tree_memory_observer: ProcessTreeMemoryObserver,
        runner_memory_telemetry_reader: RunnerMemoryTelemetryReader,
        memory_learning_store: LocalMemoryLearningStore | None,
        record_metric: Callable[[str], None],
    ) -> None:
        self.runner_supervisor = runner_supervisor
        self.log_store = log_store
        self.memory_observer = memory_observer
        self.process_tree_memory_observer = process_tree_memory_observer
        self.runner_memory_telemetry_reader = runner_memory_telemetry_reader
        self.memory_learning_store = memory_learning_store
        self.record_metric = record_metric
        self._sampling_tasks: dict[str, asyncio.Task[None]] = {}
        self._sampling_stop_events: dict[str, asyncio.Event] = {}
        self._sampling_snapshots: dict[str, list[MachineMemorySnapshot]] = {}
        self._job_attribution: dict[str, dict[str, Any]] = {}
        self._recorded_job_ids: set[str] = set()

    def start_job_sampling(
        self,
        *,
        job_id: str,
        workflow_id: str,
        runner_id: str,
        initial_snapshot: MachineMemorySnapshot | None = None,
        retry_after_cleanup: bool = False,
        telemetry_observed_after: str | None = None,
    ) -> None:
        if self.memory_observer is None or job_id in self._sampling_tasks:
            return
        stop_event = asyncio.Event()
        snapshots: list[MachineMemorySnapshot] = []
        if initial_snapshot is not None:
            snapshots.append(
                self._attribute_memory_snapshot(
                    initial_snapshot,
                    job_id=job_id,
                    workflow_id=workflow_id,
                    runner_id=runner_id,
                    sample_window=MemorySampleWindow.BEFORE_SUBMIT,
                    telemetry_observed_after=telemetry_observed_after,
                )
            )
        self._sampling_stop_events[job_id] = stop_event
        self._sampling_snapshots[job_id] = snapshots
        execution_window = (
            MemorySampleWindow.RETRY_AFTER_CLEANUP
            if retry_after_cleanup
            else MemorySampleWindow.WORKFLOW_EXECUTION
        )

        async def _sample() -> None:
            while not stop_event.is_set():
                snapshots.append(
                    self._attribute_memory_snapshot(
                        self.memory_observer.snapshot(),
                        job_id=job_id,
                        workflow_id=workflow_id,
                        runner_id=runner_id,
                        sample_window=execution_window,
                        telemetry_observed_after=telemetry_observed_after,
                    )
                )
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=0.5)
                except TimeoutError:
                    continue
            snapshots.append(
                self._attribute_memory_snapshot(
                    self.memory_observer.snapshot(),
                    job_id=job_id,
                    workflow_id=workflow_id,
                    runner_id=runner_id,
                    sample_window=MemorySampleWindow.AFTER_COMPLETION,
                    telemetry_observed_after=telemetry_observed_after,
                )
            )

        self._sampling_tasks[job_id] = asyncio.create_task(_sample())
        self.log_store.add(
            "info",
            "Started best-effort job memory sampling",
            "memory_governor",
            job_id=job_id,
            workflow_id=workflow_id,
            details={"runner_id": runner_id},
        )

    async def finish_job_sampling(
        self,
        job_id: str,
        *,
        workflow_id: str | None = None,
    ) -> None:
        stop_event = self._sampling_stop_events.pop(job_id, None)
        task = self._sampling_tasks.pop(job_id, None)
        if stop_event is not None:
            stop_event.set()
        if task is not None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(task, timeout=1)
        snapshots = self._sampling_snapshots.pop(job_id, [])
        if not snapshots:
            return
        system_peak_vram_mb, system_peak_ram_mb = _peak_used_memory_delta_from_snapshots(snapshots)
        process_peak_ram_mb = _peak_optional(snapshot.process_tree_ram_mb for snapshot in snapshots)
        process_peak_vram_mb = _peak_optional(snapshot.process_tree_vram_mb for snapshot in snapshots)
        allocator_peak_vram_mb = _peak_optional(
            snapshot.backend_allocator_peak_vram_mb or snapshot.backend_allocator_current_vram_mb
            for snapshot in snapshots
        )
        peak_vram_mb = (
            process_peak_vram_mb
            if process_peak_vram_mb is not None
            else allocator_peak_vram_mb
            if allocator_peak_vram_mb is not None
            else system_peak_vram_mb
        )
        peak_ram_mb = process_peak_ram_mb if process_peak_ram_mb is not None else system_peak_ram_mb
        attribution_quality = _best_service_attribution_quality(
            snapshot.attribution_quality for snapshot in snapshots
        )
        if attribution_quality is MemoryAttributionQuality.UNKNOWN:
            attribution_quality = (
                MemoryAttributionQuality.SYSTEM_DELTA
                if system_peak_vram_mb is not None or system_peak_ram_mb is not None
                else MemoryAttributionQuality.UNAVAILABLE
            )
        attribution_sources = _unique_service_values(
            source
            for snapshot in snapshots
            for source in snapshot.attribution_sources
        )
        attribution_reasons = _unique_service_values(
            reason
            for snapshot in snapshots
            for reason in snapshot.attribution_reasons
        )
        if process_peak_vram_mb is None and system_peak_vram_mb is not None:
            attribution_sources.append("system_memory_delta")
            attribution_reasons.append("system_vram_delta_active_job_window")
        if allocator_peak_vram_mb is not None:
            attribution_sources.append("runner_backend_allocator")
            attribution_reasons.append("runner_backend_allocator_peak")
        if process_peak_ram_mb is None and system_peak_ram_mb is not None:
            attribution_sources.append("system_memory_delta")
            attribution_reasons.append("system_ram_delta_active_job_window")
        attribution_sources = _unique_service_values(attribution_sources)
        attribution_reasons = _unique_service_values(attribution_reasons)
        try:
            runner = self.runner_supervisor.runner_for_job(job_id)
        except JobRunnerNotFoundError:
            runner = None
        runner_root_pid = runner.pid if runner is not None else None
        runner_child_pids = _unique_int_values(
            pid
            for snapshot in snapshots
            for pid in snapshot.runner_child_pids
        )
        sample_windows_observed = _unique_service_values(snapshot.sample_window.value for snapshot in snapshots)
        self._job_attribution[job_id] = {
            "runner_root_pid": runner_root_pid,
            "runner_child_pids": runner_child_pids,
            "sample_window": _sample_window_for_selected_peak(
                snapshots,
                selected_vram_mb=peak_vram_mb,
                selected_ram_mb=peak_ram_mb,
            ),
            "process_tree_peak_vram_mb": process_peak_vram_mb,
            "process_tree_peak_ram_mb": process_peak_ram_mb,
            "system_peak_delta_vram_mb": system_peak_vram_mb,
            "system_peak_delta_ram_mb": system_peak_ram_mb,
            "backend_allocator_peak_vram_mb": allocator_peak_vram_mb,
            "selected_peak_vram_mb": peak_vram_mb,
            "selected_peak_ram_mb": peak_ram_mb,
            "backend_allocator_details": _merge_service_dicts(
                snapshot.backend_allocator_details for snapshot in snapshots
            ),
            "attribution_quality": attribution_quality,
            "attribution_sources": attribution_sources,
            "attribution_reasons": attribution_reasons,
            "sample_windows_observed": sample_windows_observed,
        }
        if runner is not None:
            self.runner_supervisor.fill_runner_memory_observation(
                runner.runner_id,
                observed_execution_peak_vram_mb=peak_vram_mb,
                observed_execution_peak_ram_mb=peak_ram_mb,
            )
        self.log_store.add(
            "info",
            "Finished best-effort job memory sampling",
            "memory_governor",
            job_id=job_id,
            workflow_id=workflow_id,
            details={
                "sample_count": len(snapshots),
                "process_tree_peak_vram_mb": process_peak_vram_mb,
                "process_tree_peak_ram_mb": process_peak_ram_mb,
                "selected_peak_vram_mb": peak_vram_mb,
                "selected_peak_ram_mb": peak_ram_mb,
                "system_peak_delta_vram_mb": system_peak_vram_mb,
                "system_peak_delta_ram_mb": system_peak_ram_mb,
                "backend_allocator_peak_vram_mb": allocator_peak_vram_mb,
                "sample_window": self._job_attribution[job_id]["sample_window"].value,
                "sample_windows_observed": sample_windows_observed,
                "runner_id": runner.runner_id if runner is not None else None,
                "runner_root_pid": runner_root_pid,
                "runner_child_pids": runner_child_pids,
                "attribution_quality": attribution_quality.value,
                "attribution_sources": attribution_sources,
                "attribution_reasons": attribution_reasons,
            },
        )

    def record_result_observation(
        self,
        result: JobResult,
        *,
        workflow_id: str | None,
        run_request: tuple[str, dict[str, Any], dict[str, Any]] | None,
        input_profile_fingerprint: str | None = None,
    ) -> None:
        if self.memory_learning_store is None or workflow_id is None:
            return
        if result.status not in {"completed", "failed", "canceled"}:
            return
        if result.job_id in self._recorded_job_ids:
            return
        try:
            runner = self.runner_supervisor.runner_for_job(result.job_id)
        except JobRunnerNotFoundError:
            runner = None
        attribution = self._job_attribution.get(result.job_id)
        has_job_window_attribution = attribution is not None
        attribution = attribution or {}
        selected_peak_vram_mb = attribution.get("selected_peak_vram_mb")
        selected_peak_ram_mb = attribution.get("selected_peak_ram_mb")
        peak_vram_mb = _peak_for_local_observation(
            selected_peak=selected_peak_vram_mb,
            descriptor_peak=runner.observed_execution_peak_vram_mb if runner is not None else None,
            has_job_window_attribution=has_job_window_attribution,
        )
        peak_ram_mb = _peak_for_local_observation(
            selected_peak=selected_peak_ram_mb,
            descriptor_peak=runner.observed_execution_peak_ram_mb if runner is not None else None,
            has_job_window_attribution=has_job_window_attribution,
        )
        machine_snapshot = self.memory_observer.snapshot() if self.memory_observer is not None else None
        if result.status == "completed":
            outcome = MemoryObservationOutcome.SUCCESS
        elif result.status == "canceled":
            outcome = MemoryObservationOutcome.CANCELED
        elif likely_memory_error(result.error):
            outcome = MemoryObservationOutcome.MEMORY_ERROR
        else:
            outcome = MemoryObservationOutcome.RUNTIME_ERROR
        summary = self.memory_learning_store.record(
            LocalMemoryObservation(
                workflow_id=workflow_id,
                runner_process_compatibility_key=runner.runner_process_compatibility_key if runner is not None else None,
                machine_profile_id=machine_snapshot.machine_profile_id if machine_snapshot is not None else None,
                backend=machine_snapshot.backend if machine_snapshot is not None else MemoryBackend.UNKNOWN,
                input_profile_fingerprint=input_profile_fingerprint
                or (
                    memory_input_profile_fingerprint(run_request[1], run_request[2])
                    if run_request is not None
                    else None
                ),
                runner_id=runner.runner_id if runner is not None else None,
                job_id=result.job_id,
                runner_root_pid=attribution.get("runner_root_pid"),
                runner_child_pids=attribution.get("runner_child_pids", []),
                sample_window=attribution.get("sample_window", MemorySampleWindow.UNKNOWN),
                outcome=outcome,
                memory_class=runner.memory_class if runner is not None else RunnerMemoryClass.UNKNOWN,
                peak_vram_mb=peak_vram_mb,
                peak_ram_mb=peak_ram_mb,
                process_tree_peak_vram_mb=attribution.get("process_tree_peak_vram_mb"),
                process_tree_peak_ram_mb=attribution.get("process_tree_peak_ram_mb"),
                system_peak_delta_vram_mb=attribution.get("system_peak_delta_vram_mb"),
                system_peak_delta_ram_mb=attribution.get("system_peak_delta_ram_mb"),
                backend_allocator_peak_vram_mb=attribution.get("backend_allocator_peak_vram_mb"),
                backend_allocator_details=attribution.get("backend_allocator_details", {}),
                attribution_quality=attribution.get("attribution_quality", MemoryAttributionQuality.UNKNOWN),
                attribution_sources=attribution.get("attribution_sources", []),
                attribution_reasons=attribution.get("attribution_reasons", []),
                retry_required=outcome is MemoryObservationOutcome.MEMORY_ERROR,
            )
        )
        self._recorded_job_ids.add(result.job_id)
        self.log_store.add(
            "info",
            "Recorded local workflow memory observation",
            "memory_governor",
            job_id=result.job_id,
            workflow_id=workflow_id,
            details={
                "outcome": outcome.value,
                "successful_runs": summary.successful_runs,
                "memory_error_runs": summary.memory_error_runs,
                "observed_peak_vram_mb": summary.observed_peak_vram_mb,
                "observed_peak_ram_mb": summary.observed_peak_ram_mb,
                "attribution_quality": summary.attribution_quality.value,
                "attribution_sources": summary.attribution_sources,
            },
        )
        self.record_metric(f"local_observation_{outcome.value}")
        self._job_attribution.pop(result.job_id, None)

    def _attribute_memory_snapshot(
        self,
        snapshot: MachineMemorySnapshot,
        *,
        job_id: str,
        workflow_id: str,
        runner_id: str,
        sample_window: MemorySampleWindow,
        telemetry_observed_after: str | None = None,
    ) -> MachineMemorySnapshot:
        try:
            runner = self.runner_supervisor.get_runner(runner_id)
        except Exception:
            runner = None
        root_pid = runner.pid if runner is not None else None
        process_sample = self.process_tree_memory_observer.sample(root_pid)
        process_pids = {pid for pid in [process_sample.root_pid, *process_sample.child_pids] if pid is not None}
        gpu_sample = _sample_gpu_process_memory(self.memory_observer, process_pids)
        allocator_sample = self.runner_memory_telemetry_reader.sample(
            runner.memory_telemetry_path if runner is not None else None,
            runner_id=runner_id,
            job_id=job_id,
            sample_window=sample_window,
            observed_after=telemetry_observed_after,
        )
        attribution_quality = _best_service_attribution_quality(
            [
                process_sample.attribution_quality,
                gpu_sample.attribution_quality,
                allocator_sample.attribution_quality,
            ]
        )
        attribution_sources = _unique_service_values(
            [
                *process_sample.attribution_sources,
                *gpu_sample.attribution_sources,
                *allocator_sample.attribution_sources,
            ]
        )
        attribution_reasons = _unique_service_values(
            [
                *process_sample.attribution_reasons,
                *gpu_sample.attribution_reasons,
                *allocator_sample.attribution_reasons,
            ]
        )
        return snapshot.model_copy(
            update={
                "runner_id": runner_id,
                "job_id": job_id,
                "workflow_id": workflow_id,
                "runner_root_pid": root_pid,
                "runner_child_pids": process_sample.child_pids,
                "sample_window": sample_window,
                "attribution_quality": attribution_quality,
                "attribution_sources": attribution_sources,
                "attribution_reasons": attribution_reasons,
                "process_tree_ram_mb": process_sample.process_tree_ram_mb,
                "process_tree_vram_mb": gpu_sample.process_tree_vram_mb,
                "backend_allocator_current_vram_mb": allocator_sample.current_vram_mb,
                "backend_allocator_peak_vram_mb": allocator_sample.peak_vram_mb,
                "backend_allocator_details": allocator_sample.details,
            }
        )


_ALWAYS_MEMORY_NEUTRAL_INPUT_CONTROLS = {
    "note",
    "seed_widget",
    "api_credential",
}
_TEXT_INPUT_CONTROLS = {"textarea", "string_field"}
_TEXT_BINDING_INPUT_NAMES = {
    "caption",
    "negative",
    "negative_prompt",
    "positive",
    "positive_prompt",
    "prompt",
    "system_prompt",
    "text",
    "user_prompt",
}


def memory_input_profile_fingerprint(
    inputs: dict[str, Any],
    options: dict[str, Any],
    *,
    package: Any | None = None,
) -> str:
    profile_inputs = _memory_relevant_inputs(inputs, package)
    payload = {
        "inputs": profile_inputs,
        "options": options,
    }
    if package is not None:
        model_selections = extract_model_selection_features(package, inputs)
        if not model_selections.empty:
            payload["model_selections"] = model_selections.profile_payload()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _memory_relevant_inputs(
    inputs: dict[str, Any],
    package: Any | None,
) -> dict[str, Any]:
    if package is None:
        return inputs
    neutral_input_ids = _memory_neutral_input_ids(package)
    return {
        key: value
        for key, value in inputs.items()
        if key not in neutral_input_ids
    }


def _memory_neutral_input_ids(package: Any) -> set[str]:
    neutral: set[str] = set()
    for workflow_input in getattr(package, "inputs", []) or []:
        input_id = getattr(workflow_input, "id", None)
        control = getattr(workflow_input, "control", None)
        if isinstance(input_id, str) and _input_is_memory_neutral(workflow_input, control):
            neutral.add(input_id)

    dashboard = getattr(package, "dashboard", None)
    for dashboard_input in getattr(dashboard, "inputs", []) or []:
        input_id = getattr(dashboard_input, "id", None)
        control = getattr(dashboard_input, "control", None)
        if isinstance(input_id, str) and _input_is_memory_neutral(dashboard_input, control):
            neutral.add(input_id)
    for section in getattr(dashboard, "sections", []) or []:
        for control in getattr(section, "controls", []) or []:
            input_id = getattr(control, "input_id", None)
            control_type = getattr(control, "type", None)
            if (
                isinstance(input_id, str)
                and control_type in _ALWAYS_MEMORY_NEUTRAL_INPUT_CONTROLS
            ):
                neutral.add(input_id)
    return neutral


def _input_is_memory_neutral(workflow_input: Any, control: Any) -> bool:
    if control in _ALWAYS_MEMORY_NEUTRAL_INPUT_CONTROLS:
        return True
    if control not in _TEXT_INPUT_CONTROLS:
        return False
    binding = getattr(workflow_input, "binding", None)
    input_name = getattr(binding, "input_name", None)
    if not isinstance(input_name, str):
        return False
    return input_name.lower() in _TEXT_BINDING_INPUT_NAMES


def _peak_used_memory_delta_from_snapshots(snapshots: list[MachineMemorySnapshot]) -> tuple[int | None, int | None]:
    return (
        _peak_used_delta_mb((snapshot.total_vram_mb, snapshot.free_vram_mb) for snapshot in snapshots),
        _peak_used_delta_mb((snapshot.total_ram_mb, snapshot.free_ram_mb) for snapshot in snapshots),
    )


def _peak_for_local_observation(
    *,
    selected_peak: int | None,
    descriptor_peak: int | None,
    has_job_window_attribution: bool,
) -> int | None:
    if selected_peak is not None:
        return selected_peak
    if has_job_window_attribution:
        return None
    return descriptor_peak


def _peak_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _peak_used_delta_mb(values: Iterable[tuple[int | None, int | None]]) -> int | None:
    used_values = [
        total_mb - free_mb
        for total_mb, free_mb in values
        if total_mb is not None and free_mb is not None and total_mb >= free_mb
    ]
    if not used_values:
        return None
    delta_mb = max(used_values) - used_values[0]
    return delta_mb if delta_mb > 0 else None


def _sample_window_for_selected_peak(
    snapshots: list[MachineMemorySnapshot],
    *,
    selected_vram_mb: int | None,
    selected_ram_mb: int | None,
) -> MemorySampleWindow:
    if selected_vram_mb is not None:
        for snapshot in snapshots:
            if snapshot.process_tree_vram_mb == selected_vram_mb:
                return snapshot.sample_window
        for snapshot in snapshots:
            if snapshot.backend_allocator_peak_vram_mb == selected_vram_mb:
                return snapshot.sample_window
        for snapshot in snapshots:
            if snapshot.backend_allocator_current_vram_mb == selected_vram_mb:
                return snapshot.sample_window
    if selected_ram_mb is not None:
        for snapshot in snapshots:
            if snapshot.process_tree_ram_mb == selected_ram_mb:
                return snapshot.sample_window
    return MemorySampleWindow.UNKNOWN


def _merge_service_dicts(values: Iterable[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if isinstance(item, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **item}
            else:
                merged[key] = item
    return merged


def _sample_gpu_process_memory(
    observer: MachineMemoryObserver | None,
    pids: set[int],
) -> GpuProcessMemorySample:
    if observer is None:
        return GpuProcessMemorySample(
            requested_pids=sorted(pids),
            attribution_sources=["gpu_process_attribution_unavailable"],
            error="memory_observer_unavailable",
        )
    sampler = getattr(observer, "sample_process_vram", None)
    if sampler is None:
        return GpuProcessMemorySample(
            requested_pids=sorted(pids),
            attribution_sources=["gpu_process_attribution_unavailable"],
            error="gpu_process_attribution_unavailable",
        )
    return sampler(pids)


def _best_service_attribution_quality(values: Iterable[MemoryAttributionQuality]) -> MemoryAttributionQuality:
    present = list(values)
    if not present:
        return MemoryAttributionQuality.UNKNOWN
    return max(present, key=_service_attribution_quality_rank)


def _service_attribution_quality_rank(quality: MemoryAttributionQuality) -> int:
    if quality is MemoryAttributionQuality.PROCESS_EXACT:
        return 6
    if quality is MemoryAttributionQuality.BACKEND_ALLOCATOR:
        return 5
    if quality is MemoryAttributionQuality.PROCESS_TREE:
        return 4
    if quality is MemoryAttributionQuality.ACTIVE_WINDOW_DELTA:
        return 3
    if quality is MemoryAttributionQuality.SYSTEM_DELTA:
        return 2
    if quality is MemoryAttributionQuality.UNAVAILABLE:
        return 1
    return 0


def _unique_service_values(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _unique_int_values(values: Iterable[int]) -> list[int]:
    return sorted(set(values))
