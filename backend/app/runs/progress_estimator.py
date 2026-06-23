from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.diagnostics import DiagnosticsSink
from app.engine.models import JobProgress, JobProgressEstimate, JobResult
from app.runtime.runners.supervisor import RunnerDescriptor

PROGRESS_SCALE = 1000
PREPARING_MAX = 50
WARM_EXECUTION_START = 80
COLD_LOADING_START = 50
COLD_EXECUTION_START = 350
EXECUTION_MAX = 950
SAVING_MAX = 980

DEFAULT_PREPARING_SECONDS = 2.0
DEFAULT_LOADING_SECONDS = 30.0
DEFAULT_RUNNING_SECONDS = 45.0
SLOW_OVERRUN_FACTOR = 1.25

EstimateSource = Literal[
    "no_history",
    "loading_history",
    "running_history",
    "real_engine_progress",
]
ProgressPhase = Literal["preparing", "loading_model", "executing", "saving_result"]


class ProgressTimingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    loading_durations_seconds: list[float] = Field(default_factory=list)
    running_durations_seconds: list[float] = Field(default_factory=list)


@dataclass(frozen=True)
class ProgressTimingKey:
    workflow_id: str
    engine: str
    runner_kind: str
    runner_process_key: str
    machine_profile_id: str | None = None
    model_residency_signature: str | None = None
    execution_profile_signature: str | None = None

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "workflow_id": self.workflow_id,
            "engine": self.engine,
            "runner_kind": self.runner_kind,
            "runner_process_key": self.runner_process_key,
            "machine_profile_id": self.machine_profile_id,
            "model_residency_signature": self.model_residency_signature,
            "execution_profile_signature": self.execution_profile_signature,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.payload(),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


class ProgressTimingStore:
    def __init__(
        self,
        root_dir: Path,
        *,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.root_dir = root_dir
        self.log_store = log_store
        self._lock = threading.RLock()

    def read(self, key: ProgressTimingKey) -> ProgressTimingRecord:
        path = self._path_for_key(key)
        with self._lock:
            if not path.exists():
                return ProgressTimingRecord()
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return ProgressTimingRecord.model_validate(data)
            except Exception as exc:
                self._log_store_failure(
                    "Workflow progress timing history could not be read",
                    key,
                    exc,
                )
                return ProgressTimingRecord()

    def record_loading_duration(
        self,
        key: ProgressTimingKey,
        duration_seconds: float,
    ) -> ProgressTimingRecord:
        return self._update(key, loading_duration=duration_seconds)

    def record_running_duration(
        self,
        key: ProgressTimingKey,
        duration_seconds: float,
    ) -> ProgressTimingRecord:
        return self._update(key, running_duration=duration_seconds)

    def _update(
        self,
        key: ProgressTimingKey,
        *,
        loading_duration: float | None = None,
        running_duration: float | None = None,
    ) -> ProgressTimingRecord:
        path = self._path_for_key(key)
        with self._lock:
            record = self.read(key)
            loading = list(record.loading_durations_seconds)
            running = list(record.running_durations_seconds)
            if (
                loading_duration is not None
                and math.isfinite(loading_duration)
                and loading_duration > 0
            ):
                loading = [*loading, round(float(loading_duration), 3)][-3:]
            if (
                running_duration is not None
                and math.isfinite(running_duration)
                and running_duration > 0
            ):
                running = [*running, round(float(running_duration), 3)][-5:]
            updated = ProgressTimingRecord(
                loading_durations_seconds=loading,
                running_durations_seconds=running,
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".json.tmp")
            tmp_path.write_text(
                json.dumps(updated.model_dump(mode="json"), indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_path.replace(path)
            return updated

    def _path_for_key(self, key: ProgressTimingKey) -> Path:
        safe_digest = key.digest.removeprefix("sha256:")
        return self.root_dir / f"{safe_digest}.json"

    def _log_store_failure(
        self,
        message: str,
        key: ProgressTimingKey,
        exc: Exception,
    ) -> None:
        if self.log_store is None:
            return
        self.log_store.add(
            "warning",
            message,
            "runs.progress_estimator",
            workflow_id=key.workflow_id,
            details={"timing_key_hash": key.digest, "error": str(exc)},
        )


@dataclass
class _ProgressContext:
    job_id: str
    workflow_id: str
    key: ProgressTimingKey
    warm_model_expected: bool
    started_at: float
    timing_record: ProgressTimingRecord
    execution_started_at: float | None = None
    final_result_ready: bool = False
    last_value: int = 0
    last_updated_at: float = field(default_factory=time.monotonic)
    logged_sources: set[str] = field(default_factory=set)
    logged_slow_phases: set[str] = field(default_factory=set)
    last_phase: str | None = None


class WorkflowProgressEstimator:
    def __init__(
        self,
        *,
        timing_store: ProgressTimingStore,
        log_store: DiagnosticsSink | None = None,
        now: Callable[[], float] | None = None,
        terminal_context_limit: int = 256,
    ) -> None:
        self.timing_store = timing_store
        self.log_store = log_store
        self._now = now or time.monotonic
        self.terminal_context_limit = terminal_context_limit
        self._lock = threading.RLock()
        self._contexts: dict[str, _ProgressContext] = {}
        self._terminal_job_ids: deque[str] = deque()
        self._terminal_job_id_set: set[str] = set()

    def register_job(
        self,
        *,
        job_id: str,
        workflow_id: str,
        engine: str,
        runner: RunnerDescriptor,
        machine_profile_id: str | None,
        model_residency_signature: str | None,
        execution_profile_signature: str | None,
        warm_model_expected: bool,
    ) -> None:
        runner_process_key = (
            runner.runner_process_compatibility_key
            or runner.fingerprint
            or runner.runner_id
        )
        key = ProgressTimingKey(
            workflow_id=workflow_id,
            engine=engine,
            runner_kind=runner.kind.value,
            runner_process_key=runner_process_key,
            machine_profile_id=machine_profile_id,
            model_residency_signature=model_residency_signature,
            execution_profile_signature=execution_profile_signature,
        )
        timing_record = self.timing_store.read(key)
        now = self._now()
        with self._lock:
            self._contexts[job_id] = _ProgressContext(
                job_id=job_id,
                workflow_id=workflow_id,
                key=key,
                warm_model_expected=warm_model_expected,
                started_at=now,
                timing_record=timing_record,
                last_updated_at=now,
            )
        self._log(
            "info",
            "Workflow progress estimator started",
            job_id=job_id,
            workflow_id=workflow_id,
            details={
                "timing_key_hash": key.digest,
                "warm_model_expected": warm_model_expected,
                "loading_history_count": len(timing_record.loading_durations_seconds),
                "running_history_count": len(timing_record.running_durations_seconds),
            },
        )

    def decorate(
        self,
        progress: JobProgress,
        *,
        final_result_ready: bool = False,
    ) -> JobProgress:
        with self._lock:
            context = self._contexts.get(progress.job_id)
            if context is None:
                return progress
            if final_result_ready:
                context.final_result_ready = True
            return self._decorate_locked(progress, context)

    def finalize_result(self, result: JobResult) -> None:
        with self._lock:
            context = self._contexts.get(result.job_id)
            if context is None:
                return
            if result.status != "completed":
                self._discard_context_locked(result.job_id)
                return
            now = self._now()
            context.final_result_ready = True
            samples: list[tuple[str, float, int]] = []
            execution_started_at = context.execution_started_at
            if not context.warm_model_expected:
                if execution_started_at is None:
                    self._log(
                        "debug",
                        "Workflow progress timing sample skipped",
                        job_id=result.job_id,
                        workflow_id=context.workflow_id,
                        details={
                            "timing_key_hash": context.key.digest,
                            "reason": "no_execution_transition_observed",
                        },
                    )
                    return
                loading_duration = execution_started_at - context.started_at
                updated = self.timing_store.record_loading_duration(
                    context.key,
                    loading_duration,
                )
                context.timing_record = updated
                samples.append(
                    (
                        "loading",
                        loading_duration,
                        len(updated.loading_durations_seconds),
                    )
                )
            elif execution_started_at is None:
                execution_started_at = context.started_at

            if execution_started_at is None:
                self._log(
                    "debug",
                    "Workflow progress timing sample skipped",
                    job_id=result.job_id,
                    workflow_id=context.workflow_id,
                    details={
                        "timing_key_hash": context.key.digest,
                        "reason": "no_execution_transition_observed",
                    },
                )
                return
            running_duration = now - execution_started_at
            updated = self.timing_store.record_running_duration(
                context.key,
                running_duration,
            )
            context.timing_record = updated
            samples.append(
                (
                    "running",
                    running_duration,
                    len(updated.running_durations_seconds),
                )
            )
            self._retain_terminal_context_locked(result.job_id)
            for sample_kind, duration, history_count in samples:
                self._log(
                    "info",
                    "Workflow progress timing sample persisted",
                    job_id=result.job_id,
                    workflow_id=context.workflow_id,
                    details={
                        "timing_key_hash": context.key.digest,
                        "sample_kind": sample_kind,
                        "duration_seconds": round(duration, 3),
                        "history_count": history_count,
                    },
                )

    def discard_job(self, job_id: str) -> None:
        with self._lock:
            self._discard_context_locked(job_id)

    def _decorate_locked(
        self,
        progress: JobProgress,
        context: _ProgressContext,
    ) -> JobProgress:
        now = self._now()
        elapsed = max(0.0, now - context.started_at)

        if progress.status == "completed":
            if context.final_result_ready:
                return progress.model_copy(
                    update={
                        "value": PROGRESS_SCALE,
                        "max": PROGRESS_SCALE,
                        "message": progress.message or "Result saved by the local workflow.",
                        "estimate": self._estimate(
                            context,
                            phase="saving_result",
                            source="real_engine_progress",
                            elapsed=elapsed,
                            estimated_seconds=None,
                            history_count=0,
                            slower_than_expected=False,
                        ),
                    }
                )
            value = self._monotonic_value(context, SAVING_MAX, now, terminal_pending=True)
            return progress.model_copy(
                update={
                    "status": "running",
                    "value": value,
                    "max": PROGRESS_SCALE,
                    "message": "Saving result...",
                    "estimate": self._estimate(
                        context,
                        phase="saving_result",
                        source="real_engine_progress",
                        elapsed=elapsed,
                        estimated_seconds=None,
                        history_count=0,
                        slower_than_expected=False,
                    ),
                }
            )

        if progress.status in {
            "failed",
            "canceled",
            "blocked_by_memory",
            "missing_models",
            "unknown",
        }:
            return progress

        if self._has_engine_execution_signal(progress) and context.execution_started_at is None:
            context.execution_started_at = now

        if self._has_real_engine_progress(progress):
            value = self._real_engine_value(progress, context)
            value = self._monotonic_value(context, value, now)
            return progress.model_copy(
                update={
                    "status": "running",
                    "value": value,
                    "max": PROGRESS_SCALE,
                    "message": progress.message or "Generating...",
                    "estimate": self._estimate(
                        context,
                        phase="executing",
                        source="real_engine_progress",
                        elapsed=elapsed,
                        estimated_seconds=None,
                        history_count=0,
                        slower_than_expected=False,
                    ),
                }
            )

        phase, source, estimated_seconds, history_count, value, message, slower = (
            self._estimated_active_progress(context, progress.status, elapsed)
        )
        value = self._monotonic_value(context, value, now)
        if slower and phase not in context.logged_slow_phases:
            context.logged_slow_phases.add(phase)
            self._log(
                "info",
                "Workflow progress estimate slower than expected",
                job_id=context.job_id,
                workflow_id=context.workflow_id,
                details={
                    "timing_key_hash": context.key.digest,
                    "phase": phase,
                    "source": source,
                    "elapsed_seconds": round(elapsed, 3),
                    "estimated_seconds": estimated_seconds,
                },
            )
        return progress.model_copy(
            update={
                "status": (
                    "running"
                    if progress.status in {"queued", "running"}
                    else progress.status
                ),
                "value": value,
                "max": PROGRESS_SCALE,
                "message": progress.message or message,
                "estimate": self._estimate(
                    context,
                    phase=phase,
                    source=source,
                    elapsed=elapsed,
                    estimated_seconds=estimated_seconds,
                    history_count=history_count,
                    slower_than_expected=slower,
                ),
            }
        )

    def _estimated_active_progress(
        self,
        context: _ProgressContext,
        raw_status: str,
        elapsed: float,
    ) -> tuple[ProgressPhase, EstimateSource, float, int, int, str, bool]:
        if raw_status == "queued" or elapsed < DEFAULT_PREPARING_SECONDS:
            estimated = DEFAULT_PREPARING_SECONDS
            ratio, slower = _phase_ratio(elapsed, estimated)
            value = round(ratio * PREPARING_MAX)
            return (
                "preparing",
                "no_history",
                estimated,
                0,
                value,
                "Preparing workflow...",
                slower,
            )

        if not context.warm_model_expected and context.execution_started_at is None:
            history = context.timing_record.loading_durations_seconds
            source: EstimateSource = "loading_history" if history else "no_history"
            estimated = _average(history) or DEFAULT_LOADING_SECONDS
            ratio, slower = _phase_ratio(elapsed, estimated)
            value = COLD_LOADING_START + round(
                ratio * (COLD_EXECUTION_START - COLD_LOADING_START)
            )
            return (
                "loading_model",
                source,
                estimated,
                len(history),
                value,
                "Loading model..."
                if not slower
                else "Loading model... The local engine is still working.",
                slower,
            )

        execution_elapsed = (
            max(0.0, elapsed - (context.execution_started_at - context.started_at))
            if context.execution_started_at is not None
            else elapsed
        )
        history = context.timing_record.running_durations_seconds
        source = "running_history" if history else "no_history"
        estimated = _average(history) or DEFAULT_RUNNING_SECONDS
        ratio, slower = _phase_ratio(execution_elapsed, estimated)
        start = WARM_EXECUTION_START if context.warm_model_expected else COLD_EXECUTION_START
        value = start + round(ratio * (EXECUTION_MAX - start))
        return (
            "executing",
            source,
            estimated,
            len(history),
            value,
            "Generating..."
            if not slower
            else "Generating... The local engine is still working.",
            slower,
        )

    def _real_engine_value(self, progress: JobProgress, context: _ProgressContext) -> int:
        assert progress.value is not None and progress.max
        fraction = min(1.0, max(0.0, progress.value / progress.max))
        start = WARM_EXECUTION_START if context.warm_model_expected else COLD_EXECUTION_START
        return start + round(fraction * (EXECUTION_MAX - start))

    def _monotonic_value(
        self,
        context: _ProgressContext,
        candidate: int,
        now: float,
        *,
        terminal_pending: bool = False,
    ) -> int:
        cap = SAVING_MAX if terminal_pending else EXECUTION_MAX
        candidate = min(cap, max(0, candidate))
        previous = context.last_value
        if candidate < previous:
            candidate = previous
        elif previous > 0 and candidate > previous:
            elapsed_since_last = max(0.0, now - context.last_updated_at)
            max_step = max(75, round(elapsed_since_last * 180))
            candidate = min(candidate, previous + max_step)
        if candidate != context.last_value:
            context.last_value = candidate
            context.last_updated_at = now
        return candidate

    def _estimate(
        self,
        context: _ProgressContext,
        *,
        phase: ProgressPhase,
        source: EstimateSource,
        elapsed: float,
        estimated_seconds: float | None,
        history_count: int,
        slower_than_expected: bool,
    ) -> JobProgressEstimate:
        if phase != context.last_phase:
            context.last_phase = phase
            self._log_phase_transition(context, phase, self._now())
        if source not in context.logged_sources:
            context.logged_sources.add(source)
            self._log(
                "info",
                "Workflow progress estimate source selected",
                job_id=context.job_id,
                workflow_id=context.workflow_id,
                details={
                    "timing_key_hash": context.key.digest,
                    "phase": phase,
                    "source": source,
                    "history_count": history_count,
                    "warm_model_expected": context.warm_model_expected,
                },
            )
        return JobProgressEstimate(
            phase=phase,
            source=source,
            elapsed_seconds=round(elapsed, 3),
            estimated_seconds=round(estimated_seconds, 3)
            if estimated_seconds is not None
            else None,
            history_count=history_count,
            warm_model_expected=context.warm_model_expected,
            slower_than_expected=slower_than_expected,
            timing_key_hash=context.key.digest,
        )

    def _log_phase_transition(
        self,
        context: _ProgressContext,
        phase: str,
        now: float,
    ) -> None:
        self._log(
            "debug",
            "Workflow progress estimate phase updated",
            job_id=context.job_id,
            workflow_id=context.workflow_id,
            details={
                "timing_key_hash": context.key.digest,
                "phase": phase,
                "elapsed_seconds": round(max(0.0, now - context.started_at), 3),
            },
        )

    def _log(
        self,
        level: str,
        message: str,
        *,
        job_id: str | None = None,
        workflow_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self.log_store is None:
            return
        self.log_store.add(
            level,
            message,
            "runs.progress_estimator",
            job_id=job_id,
            workflow_id=workflow_id,
            details=details or {},
        )

    def _retain_terminal_context_locked(self, job_id: str) -> None:
        if job_id not in self._terminal_job_id_set:
            self._terminal_job_ids.append(job_id)
            self._terminal_job_id_set.add(job_id)
        while len(self._terminal_job_ids) > self.terminal_context_limit:
            old_job_id = self._terminal_job_ids.popleft()
            self._terminal_job_id_set.discard(old_job_id)
            self._contexts.pop(old_job_id, None)

    def _discard_context_locked(self, job_id: str) -> None:
        self._contexts.pop(job_id, None)
        self._terminal_job_id_set.discard(job_id)

    @staticmethod
    def _has_real_engine_progress(progress: JobProgress) -> bool:
        return (
            progress.status == "running"
            and progress.value is not None
            and progress.max is not None
            and progress.max > 0
        )

    @staticmethod
    def _has_engine_execution_signal(progress: JobProgress) -> bool:
        if progress.status != "running":
            return False
        if WorkflowProgressEstimator._has_real_engine_progress(progress):
            return True
        return progress.current_node is not None or progress.message == "Execution started"


def progress_ready_for_result_fetch(progress: JobProgress) -> bool:
    if progress.status in {"completed", "failed", "canceled"}:
        return True
    estimate = progress.estimate
    return (
        progress.status == "running"
        and estimate is not None
        and estimate.phase == "saving_result"
        and estimate.source == "real_engine_progress"
    )


def _phase_ratio(elapsed: float, estimated: float) -> tuple[float, bool]:
    estimated = max(0.1, estimated)
    if elapsed <= estimated:
        return min(1.0, elapsed / estimated), False
    overrun = (elapsed - estimated) / estimated
    return (
        min(0.985, 1.0 - (0.12 / (1.0 + overrun))),
        elapsed > estimated * SLOW_OVERRUN_FACTOR,
    )


def _average(values: list[float]) -> float | None:
    positive = [value for value in values if value > 0 and math.isfinite(value)]
    if not positive:
        return None
    return sum(positive) / len(positive)
