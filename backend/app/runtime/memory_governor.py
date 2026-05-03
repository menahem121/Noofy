"""Memory Governor schemas and decision records.

Phase 5f keeps the first Memory Governor layer intentionally pure: typed
snapshots, estimates, local evidence summaries, and serializable decisions.
The active runner supervisor can use these records before the full observer and
policy engine exist, and diagnostics can already persist the decisions.
"""

from __future__ import annotations

import os
import json
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.runtime.supervisor import (
    RunnerDescriptor,
    RunnerMemoryClass,
    RunnerMemoryEstimateConfidence,
    RunnerMemoryEstimateSource,
    RunnerStatus,
)

MEMORY_LEARNING_SCHEMA_VERSION = "0.1.0"


class MemoryBackend(StrEnum):
    CUDA = "cuda"
    MPS = "mps"
    DIRECTML = "directml"
    CPU = "cpu"
    UNKNOWN = "unknown"


class MemoryPressureLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class MemoryRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class MemoryDecisionAction(StrEnum):
    REUSE_RUNNER = "reuse_runner"
    START_CO_RESIDENT = "start_co_resident"
    EVICT_THEN_START = "evict_then_start"
    QUEUE_PENDING_SWITCH = "queue_pending_switch"
    QUEUE_PENDING_MEMORY = "queue_pending_memory"
    WAIT_FOR_MEMORY_RELEASE = "wait_for_memory_release"
    RETRY_AFTER_MEMORY_CLEANUP = "retry_after_memory_cleanup"
    BLOCKED_BY_MEMORY = "blocked_by_memory"


class MemoryObservationOutcome(StrEnum):
    SUCCESS = "success"
    MEMORY_ERROR = "memory_error"
    RUNTIME_ERROR = "runtime_error"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class MemoryReleaseStatus(StrEnum):
    RELEASED = "released"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class MachineMemorySnapshot(BaseModel):
    """Current RAM/VRAM view for the machine or active backend."""

    model_config = ConfigDict(extra="forbid")

    available: bool = True
    backend: MemoryBackend = MemoryBackend.UNKNOWN
    machine_profile_id: str | None = None
    device_name: str | None = None
    total_vram_mb: int | None = Field(default=None, ge=0)
    free_vram_mb: int | None = Field(default=None, ge=0)
    total_ram_mb: int | None = Field(default=None, ge=0)
    free_ram_mb: int | None = Field(default=None, ge=0)
    memory_pressure: MemoryPressureLevel = MemoryPressureLevel.UNKNOWN
    observed_at: str | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _free_memory_cannot_exceed_total(self) -> MachineMemorySnapshot:
        if (
            self.total_vram_mb is not None
            and self.free_vram_mb is not None
            and self.free_vram_mb > self.total_vram_mb
        ):
            raise ValueError("free_vram_mb cannot exceed total_vram_mb")
        if self.total_ram_mb is not None and self.free_ram_mb is not None and self.free_ram_mb > self.total_ram_mb:
            raise ValueError("free_ram_mb cannot exceed total_ram_mb")
        return self


class RunnerMemorySnapshot(BaseModel):
    """Memory-relevant state for a resident runner."""

    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(min_length=1)
    runner_process_compatibility_key: str | None = None
    memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    memory_estimate_confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.UNKNOWN
    memory_estimate_source: RunnerMemoryEstimateSource = RunnerMemoryEstimateSource.UNKNOWN
    status: RunnerStatus = RunnerStatus.UNKNOWN
    current_job_id: str | None = None
    open_workflow_lease_count: int = Field(default=0, ge=0)
    observed_idle_vram_mb: int | None = Field(default=None, ge=0)
    observed_idle_ram_mb: int | None = Field(default=None, ge=0)
    observed_load_peak_vram_mb: int | None = Field(default=None, ge=0)
    observed_load_peak_ram_mb: int | None = Field(default=None, ge=0)
    observed_execution_peak_vram_mb: int | None = Field(default=None, ge=0)
    observed_execution_peak_ram_mb: int | None = Field(default=None, ge=0)
    recent_memory_error_at: str | None = None
    recent_memory_error_count: int = Field(default=0, ge=0)

    @classmethod
    def from_descriptor(cls, descriptor: RunnerDescriptor) -> RunnerMemorySnapshot:
        return cls(
            runner_id=descriptor.runner_id,
            runner_process_compatibility_key=descriptor.runner_process_compatibility_key,
            memory_class=descriptor.memory_class,
            memory_estimate_confidence=descriptor.memory_estimate_confidence,
            memory_estimate_source=descriptor.memory_estimate_source,
            status=descriptor.status,
            current_job_id=descriptor.current_job_id,
            open_workflow_lease_count=descriptor.open_workflow_lease_count,
            observed_idle_vram_mb=descriptor.observed_idle_vram_mb,
            observed_idle_ram_mb=descriptor.observed_idle_ram_mb,
            observed_load_peak_vram_mb=descriptor.observed_load_peak_vram_mb,
            observed_load_peak_ram_mb=descriptor.observed_load_peak_ram_mb,
            observed_execution_peak_vram_mb=descriptor.observed_execution_peak_vram_mb,
            observed_execution_peak_ram_mb=descriptor.observed_execution_peak_ram_mb,
            recent_memory_error_at=descriptor.recent_memory_error_at,
            recent_memory_error_count=descriptor.recent_memory_error_count,
        )


class LocalMemoryEvidenceSummary(BaseModel):
    """Aggregated local learning for one workflow/settings/machine shape."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    runner_process_compatibility_key: str | None = None
    machine_profile_id: str | None = None
    backend: MemoryBackend = MemoryBackend.UNKNOWN
    input_profile_fingerprint: str | None = None
    successful_runs: int = Field(default=0, ge=0)
    memory_error_runs: int = Field(default=0, ge=0)
    other_failed_runs: int = Field(default=0, ge=0)
    evictions_required: int = Field(default=0, ge=0)
    retries_required: int = Field(default=0, ge=0)
    observed_peak_vram_mb: int | None = Field(default=None, ge=0)
    observed_peak_ram_mb: int | None = Field(default=None, ge=0)
    last_success_at: str | None = None
    last_memory_error_at: str | None = None

    @property
    def has_local_evidence(self) -> bool:
        return self.successful_runs > 0 or self.memory_error_runs > 0 or self.other_failed_runs > 0

    @property
    def has_repeated_success(self) -> bool:
        return self.successful_runs >= 2 and self.memory_error_runs == 0

    @property
    def has_memory_failure(self) -> bool:
        return self.memory_error_runs > 0


class LocalMemoryObservation(BaseModel):
    """One local workflow run observation for the learning store."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = MEMORY_LEARNING_SCHEMA_VERSION
    workflow_id: str = Field(min_length=1)
    runner_process_compatibility_key: str | None = None
    machine_profile_id: str | None = None
    backend: MemoryBackend = MemoryBackend.UNKNOWN
    input_profile_fingerprint: str | None = None
    outcome: MemoryObservationOutcome = MemoryObservationOutcome.UNKNOWN
    memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    peak_vram_mb: int | None = Field(default=None, ge=0)
    peak_ram_mb: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    eviction_required: bool = False
    retry_required: bool = False
    observed_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class WorkflowMemoryEstimate(BaseModel):
    """Best current estimate for a workflow before launch."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    runner_process_compatibility_key: str | None = None
    memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.UNKNOWN
    source: RunnerMemoryEstimateSource = RunnerMemoryEstimateSource.UNKNOWN
    estimated_idle_vram_mb: int | None = Field(default=None, ge=0)
    estimated_idle_ram_mb: int | None = Field(default=None, ge=0)
    estimated_peak_vram_mb: int | None = Field(default=None, ge=0)
    estimated_peak_ram_mb: int | None = Field(default=None, ge=0)
    creator_observed_peak_vram_mb: int | None = Field(default=None, ge=0)
    creator_observed_peak_ram_mb: int | None = Field(default=None, ge=0)
    local_evidence: LocalMemoryEvidenceSummary | None = None
    recent_memory_error: bool = False
    reasons: list[str] = Field(default_factory=list)

    @property
    def has_local_evidence(self) -> bool:
        return self.local_evidence is not None and self.local_evidence.has_local_evidence

    @property
    def effective_source(self) -> RunnerMemoryEstimateSource:
        if self.has_local_evidence:
            return RunnerMemoryEstimateSource.LOCAL_OBSERVED
        return self.source

    @property
    def conservative_memory_class(self) -> RunnerMemoryClass:
        return conservative_memory_class(self.memory_class)


class WorkflowMemoryEstimateRequest(BaseModel):
    """Signals available before launching a workflow."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(min_length=1)
    runner_process_compatibility_key: str | None = None
    declared_memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    input_profile_fingerprint: str | None = None
    local_evidence: LocalMemoryEvidenceSummary | None = None
    creator_observed_peak_vram_mb: int | None = Field(default=None, ge=0)
    creator_observed_peak_ram_mb: int | None = Field(default=None, ge=0)
    declared_peak_vram_mb: int | None = Field(default=None, ge=0)
    declared_peak_ram_mb: int | None = Field(default=None, ge=0)
    required_model_size_mb: int | None = Field(default=None, ge=0)
    resolution_width: int | None = Field(default=None, ge=1)
    resolution_height: int | None = Field(default=None, ge=1)
    batch_size: int = Field(default=1, ge=1)
    workflow_type: str | None = None


class MemoryGovernorDecision(BaseModel):
    """Serializable decision record for diagnostics and API state."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(default_factory=lambda: f"mg-{uuid.uuid4().hex}", min_length=1)
    action: MemoryDecisionAction
    risk_level: MemoryRiskLevel = MemoryRiskLevel.UNKNOWN
    confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.UNKNOWN
    reason_code: str = Field(min_length=1)
    reason_summary: str | None = None
    workflow_id: str | None = None
    selected_runner_id: str | None = None
    evict_runner_ids: list[str] = Field(default_factory=list)
    queued_behind_runner_id: str | None = None
    machine_snapshot: MachineMemorySnapshot | None = None
    workflow_estimate: WorkflowMemoryEstimate | None = None
    runner_snapshots: list[RunnerMemorySnapshot] = Field(default_factory=list)
    required_vram_margin_mb: int | None = Field(default=None, ge=0)
    required_ram_margin_mb: int | None = Field(default=None, ge=0)
    predicted_free_vram_after_mb: int | None = Field(default=None, ge=0)
    predicted_free_ram_after_mb: int | None = Field(default=None, ge=0)
    can_retry_after_cleanup: bool = False
    user_message: str | None = None
    developer_details: dict[str, Any] = Field(default_factory=dict)
    created_at: str | None = None

    def diagnostic_details(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class MemoryAdmissionRequest(BaseModel):
    """Inputs for deciding whether a workflow can start beside warm runners."""

    model_config = ConfigDict(extra="forbid")

    workflow_estimate: WorkflowMemoryEstimate
    machine_snapshot: MachineMemorySnapshot
    resident_runners: list[RunnerMemorySnapshot] = Field(default_factory=list)


class MemoryReleaseCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MemoryReleaseStatus
    required_free_vram_mb: int | None = Field(default=None, ge=0)
    required_free_ram_mb: int | None = Field(default=None, ge=0)
    snapshots: list[MachineMemorySnapshot] = Field(default_factory=list)
    reason_code: str


class MemoryUserStatus(BaseModel):
    """Small user-facing memory state carried by API responses.

    Detailed estimates and raw machine readings stay in `memory_decision`.
    This shape is intentionally compact so the UI can explain what is happening
    without leaking backend internals into beginner-facing copy.
    """

    model_config = ConfigDict(extra="forbid")

    state: str = Field(min_length=1)
    message: str = Field(min_length=1)
    risk_level: MemoryRiskLevel = MemoryRiskLevel.UNKNOWN
    queue_id: str | None = None
    can_cancel: bool = True
    can_retry_after_cleanup: bool = False


class MachineMemoryObserver(Protocol):
    """Reads the current machine memory state for a backend."""

    def snapshot(self) -> MachineMemorySnapshot:
        """Return a best-effort snapshot without raising for unavailable data."""


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class UnavailableMemoryObserver:
    def __init__(
        self,
        *,
        backend: MemoryBackend = MemoryBackend.UNKNOWN,
        error: str = "memory_observer_unavailable",
    ) -> None:
        self.backend = backend
        self.error = error

    def snapshot(self) -> MachineMemorySnapshot:
        return MachineMemorySnapshot(
            available=False,
            backend=self.backend,
            memory_pressure=MemoryPressureLevel.UNKNOWN,
            observed_at=_now_iso(),
            error=self.error,
        )


class SystemMemoryObserver:
    """Best-effort RAM observer used for CPU, MPS, DirectML, and fallback paths."""

    def __init__(self, *, backend: MemoryBackend = MemoryBackend.CPU) -> None:
        self.backend = backend

    def snapshot(self) -> MachineMemorySnapshot:
        total_ram_mb, free_ram_mb = _system_ram_mb()
        return MachineMemorySnapshot(
            available=total_ram_mb is not None or free_ram_mb is not None,
            backend=self.backend,
            total_ram_mb=total_ram_mb,
            free_ram_mb=free_ram_mb,
            memory_pressure=memory_pressure_from_free_ratio(total_ram_mb, free_ram_mb),
            observed_at=_now_iso(),
            error=None if total_ram_mb is not None or free_ram_mb is not None else "system_ram_unavailable",
        )


class NvidiaSmiMemoryObserver:
    """CUDA VRAM observer backed by `nvidia-smi`.

    The observer is deliberately tolerant: unavailable commands, non-zero exit
    codes, and partial rows become structured snapshots instead of exceptions.
    """

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        timeout_seconds: float = 3,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._command_runner = command_runner or self._run_command

    def snapshot(self) -> MachineMemorySnapshot:
        command = [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.free",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = self._command_runner(command)
        except FileNotFoundError:
            return _unavailable_cuda_snapshot("nvidia_smi_not_found")
        except subprocess.TimeoutExpired:
            return _unavailable_cuda_snapshot("nvidia_smi_timeout")
        except OSError as exc:
            return _unavailable_cuda_snapshot(f"nvidia_smi_error:{exc}")

        if result.returncode != 0:
            error = (result.stderr or result.stdout or "nvidia_smi_failed").strip()
            return _unavailable_cuda_snapshot(error)

        device_name, total_vram_mb, free_vram_mb = _parse_nvidia_smi_memory_row(result.stdout)
        return MachineMemorySnapshot(
            available=total_vram_mb is not None or free_vram_mb is not None,
            backend=MemoryBackend.CUDA,
            device_name=device_name,
            total_vram_mb=total_vram_mb,
            free_vram_mb=free_vram_mb,
            memory_pressure=memory_pressure_from_free_ratio(total_vram_mb, free_vram_mb),
            observed_at=_now_iso(),
            error=None if total_vram_mb is not None or free_vram_mb is not None else "nvidia_smi_parse_failed",
        )

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=self._timeout_seconds,
        )


class LocalMemoryLearningStore:
    """File-backed local Memory Governor evidence store.

    The store is intentionally app-local and mutable. It aggregates local run
    observations into summaries used by future estimates, without modifying the
    imported `.noofy` package or capsule lock.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self._lock = threading.Lock()

    def record(self, observation: LocalMemoryObservation) -> LocalMemoryEvidenceSummary:
        key = _learning_key_for(
            workflow_id=observation.workflow_id,
            runner_process_compatibility_key=observation.runner_process_compatibility_key,
            machine_profile_id=observation.machine_profile_id,
            backend=observation.backend,
            input_profile_fingerprint=observation.input_profile_fingerprint,
        )
        path = self._path_for_key(key)
        with self._lock:
            observations = self._read_observations(path)
            observations.append(observation)
            self._write_observations(path, observations)
        return summarize_local_memory_observations(observations)

    def summary_for(
        self,
        *,
        workflow_id: str,
        runner_process_compatibility_key: str | None = None,
        machine_profile_id: str | None = None,
        backend: MemoryBackend = MemoryBackend.UNKNOWN,
        input_profile_fingerprint: str | None = None,
    ) -> LocalMemoryEvidenceSummary | None:
        key = _learning_key_for(
            workflow_id=workflow_id,
            runner_process_compatibility_key=runner_process_compatibility_key,
            machine_profile_id=machine_profile_id,
            backend=backend,
            input_profile_fingerprint=input_profile_fingerprint,
        )
        path = self._path_for_key(key)
        observations = self._read_observations(path)
        if not observations:
            return None
        return summarize_local_memory_observations(observations)

    def list_summaries(self) -> list[LocalMemoryEvidenceSummary]:
        if not self.root_dir.exists():
            return []
        summaries: list[LocalMemoryEvidenceSummary] = []
        for path in sorted(self.root_dir.glob("*.json")):
            observations = self._read_observations(path)
            if observations:
                summaries.append(summarize_local_memory_observations(observations))
        return summaries

    def _path_for_key(self, key: str) -> Path:
        return self.root_dir / f"{_safe_learning_key(key)}.json"

    def _read_observations(self, path: Path) -> list[LocalMemoryObservation]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return [LocalMemoryObservation.model_validate(item) for item in data.get("observations", [])]

    def _write_observations(self, path: Path, observations: list[LocalMemoryObservation]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": MEMORY_LEARNING_SCHEMA_VERSION,
            "observations": [observation.model_dump(mode="json") for observation in observations],
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)


def memory_pressure_from_free_ratio(total_mb: int | None, free_mb: int | None) -> MemoryPressureLevel:
    if total_mb is None or free_mb is None or total_mb <= 0:
        return MemoryPressureLevel.UNKNOWN
    ratio = free_mb / total_mb
    if ratio <= 0.10:
        return MemoryPressureLevel.HIGH
    if ratio <= 0.25:
        return MemoryPressureLevel.MEDIUM
    return MemoryPressureLevel.LOW


def conservative_memory_class(memory_class: RunnerMemoryClass) -> RunnerMemoryClass:
    """Fallback class before the Governor has enough proof to be opportunistic."""
    if memory_class in {RunnerMemoryClass.UNKNOWN, RunnerMemoryClass.GPU_MEDIUM}:
        return RunnerMemoryClass.GPU_HEAVY
    return memory_class


def estimate_evidence_rank(estimate: WorkflowMemoryEstimate) -> int:
    """Rank estimate sources for deterministic v1 confidence decisions."""
    if estimate.has_local_evidence or estimate.source is RunnerMemoryEstimateSource.LOCAL_OBSERVED:
        return 4
    if estimate.source is RunnerMemoryEstimateSource.CREATOR_OBSERVED:
        return 3
    if estimate.source is RunnerMemoryEstimateSource.DECLARED:
        return 2
    if estimate.source is RunnerMemoryEstimateSource.HEURISTIC:
        return 1
    return 0


def preferred_memory_estimate(estimates: list[WorkflowMemoryEstimate]) -> WorkflowMemoryEstimate | None:
    if not estimates:
        return None
    return sorted(
        estimates,
        key=lambda estimate: (
            estimate_evidence_rank(estimate),
            _confidence_rank(estimate.confidence),
            estimate.estimated_peak_vram_mb or -1,
            estimate.workflow_id,
        ),
        reverse=True,
    )[0]


def build_workflow_memory_estimate(request: WorkflowMemoryEstimateRequest) -> WorkflowMemoryEstimate:
    """Build the best available v1 estimate from ordered evidence.

    Local observations are preferred when they match the current request shape,
    but they still produce confidence rather than guarantees. A changed input
    profile keeps the local source visible while lowering confidence.
    """
    local = request.local_evidence
    if local is not None and local.has_local_evidence:
        settings_match = _local_evidence_matches_request(request, local)
        confidence = RunnerMemoryEstimateConfidence.LOW
        reasons = ["local_memory_evidence"]
        if not settings_match:
            reasons.append("local_evidence_settings_mismatch")
        elif local.has_memory_failure:
            reasons.append("local_memory_failure")
        elif local.has_repeated_success:
            confidence = RunnerMemoryEstimateConfidence.HIGH
            reasons.append("repeated_local_success")
        else:
            confidence = RunnerMemoryEstimateConfidence.MEDIUM
            reasons.append("single_local_observation")

        return WorkflowMemoryEstimate(
            workflow_id=request.workflow_id,
            runner_process_compatibility_key=request.runner_process_compatibility_key,
            memory_class=_estimate_memory_class(request.declared_memory_class, local.observed_peak_vram_mb),
            confidence=confidence,
            source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            estimated_peak_vram_mb=local.observed_peak_vram_mb,
            estimated_peak_ram_mb=local.observed_peak_ram_mb,
            creator_observed_peak_vram_mb=request.creator_observed_peak_vram_mb,
            creator_observed_peak_ram_mb=request.creator_observed_peak_ram_mb,
            local_evidence=local,
            recent_memory_error=local.has_memory_failure,
            reasons=reasons,
        )

    if request.creator_observed_peak_vram_mb is not None or request.creator_observed_peak_ram_mb is not None:
        return WorkflowMemoryEstimate(
            workflow_id=request.workflow_id,
            runner_process_compatibility_key=request.runner_process_compatibility_key,
            memory_class=_estimate_memory_class(request.declared_memory_class, request.creator_observed_peak_vram_mb),
            confidence=RunnerMemoryEstimateConfidence.MEDIUM,
            source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            estimated_peak_vram_mb=request.creator_observed_peak_vram_mb,
            estimated_peak_ram_mb=request.creator_observed_peak_ram_mb,
            creator_observed_peak_vram_mb=request.creator_observed_peak_vram_mb,
            creator_observed_peak_ram_mb=request.creator_observed_peak_ram_mb,
            reasons=["creator_observed_memory_hint"],
        )

    if request.declared_peak_vram_mb is not None or request.declared_peak_ram_mb is not None:
        return WorkflowMemoryEstimate(
            workflow_id=request.workflow_id,
            runner_process_compatibility_key=request.runner_process_compatibility_key,
            memory_class=_estimate_memory_class(request.declared_memory_class, request.declared_peak_vram_mb),
            confidence=RunnerMemoryEstimateConfidence.MEDIUM,
            source=RunnerMemoryEstimateSource.DECLARED,
            estimated_peak_vram_mb=request.declared_peak_vram_mb,
            estimated_peak_ram_mb=request.declared_peak_ram_mb,
            reasons=["declared_memory_requirement"],
        )

    heuristic_peak_vram_mb = _heuristic_peak_vram_mb(request)
    if heuristic_peak_vram_mb is not None:
        return WorkflowMemoryEstimate(
            workflow_id=request.workflow_id,
            runner_process_compatibility_key=request.runner_process_compatibility_key,
            memory_class=_estimate_memory_class(request.declared_memory_class, heuristic_peak_vram_mb),
            confidence=RunnerMemoryEstimateConfidence.LOW,
            source=RunnerMemoryEstimateSource.HEURISTIC,
            estimated_peak_vram_mb=heuristic_peak_vram_mb,
            reasons=["model_and_input_heuristic"],
        )

    return WorkflowMemoryEstimate(
        workflow_id=request.workflow_id,
        runner_process_compatibility_key=request.runner_process_compatibility_key,
        memory_class=conservative_memory_class(request.declared_memory_class),
        confidence=RunnerMemoryEstimateConfidence.UNKNOWN,
        source=RunnerMemoryEstimateSource.UNKNOWN,
        reasons=["no_memory_estimate_available"],
    )


def summarize_local_memory_observations(
    observations: list[LocalMemoryObservation],
) -> LocalMemoryEvidenceSummary:
    if not observations:
        raise ValueError("Cannot summarize an empty local memory observation set")
    first = observations[0]
    successful_runs = sum(1 for observation in observations if observation.outcome is MemoryObservationOutcome.SUCCESS)
    memory_error_runs = sum(1 for observation in observations if observation.outcome is MemoryObservationOutcome.MEMORY_ERROR)
    other_failed_runs = sum(
        1
        for observation in observations
        if observation.outcome
        in {
            MemoryObservationOutcome.RUNTIME_ERROR,
            MemoryObservationOutcome.CANCELED,
            MemoryObservationOutcome.UNKNOWN,
        }
    )
    successful_observations = [
        observation for observation in observations if observation.outcome is MemoryObservationOutcome.SUCCESS
    ]
    memory_error_observations = [
        observation for observation in observations if observation.outcome is MemoryObservationOutcome.MEMORY_ERROR
    ]
    return LocalMemoryEvidenceSummary(
        workflow_id=first.workflow_id,
        runner_process_compatibility_key=first.runner_process_compatibility_key,
        machine_profile_id=first.machine_profile_id,
        backend=first.backend,
        input_profile_fingerprint=first.input_profile_fingerprint,
        successful_runs=successful_runs,
        memory_error_runs=memory_error_runs,
        other_failed_runs=other_failed_runs,
        evictions_required=sum(1 for observation in observations if observation.eviction_required),
        retries_required=sum(1 for observation in observations if observation.retry_required),
        observed_peak_vram_mb=_max_optional(observation.peak_vram_mb for observation in observations),
        observed_peak_ram_mb=_max_optional(observation.peak_ram_mb for observation in observations),
        last_success_at=_latest_observed_at(successful_observations),
        last_memory_error_at=_latest_observed_at(memory_error_observations),
    )


def decide_memory_admission(request: MemoryAdmissionRequest) -> MemoryGovernorDecision:
    """Decide whether the requested workflow can start with current residents.

    This is the v1 MG4 policy core. It is intentionally deterministic and
    conservative: uncertain GPU estimates deny co-residence, active jobs are
    queued instead of killed, and idle runners are evicted before surfacing a
    memory block when that could plausibly make room.
    """
    estimate = request.workflow_estimate
    machine = request.machine_snapshot
    runners = list(request.resident_runners)
    active_runners = [runner for runner in runners if _runner_snapshot_is_active(runner)]
    idle_runners = [runner for runner in runners if not _runner_snapshot_is_active(runner)]
    required_vram_margin_mb = required_vram_margin(machine, estimate)
    required_ram_margin_mb = required_ram_margin(machine, estimate)
    predicted_free_vram_after_mb = _subtract_optional(machine.free_vram_mb, estimate.estimated_peak_vram_mb)
    predicted_free_ram_after_mb = _subtract_optional(machine.free_ram_mb, estimate.estimated_peak_ram_mb)
    runner_ids = [runner.runner_id for runner in runners]

    if _request_is_cpu_only(estimate):
        if _ram_margin_ok(machine, estimate, required_ram_margin_mb):
            return _decision(
                MemoryDecisionAction.START_CO_RESIDENT,
                MemoryRiskLevel.LOW,
                "cpu_only_co_residence_allowed",
                "This workflow can run while the other one stays ready.",
                estimate,
                machine,
                runners,
                required_vram_margin_mb,
                required_ram_margin_mb,
                predicted_free_vram_after_mb,
                predicted_free_ram_after_mb,
            )
        return _memory_shortfall_decision(
            estimate,
            machine,
            runners,
            idle_runners,
            active_runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            reason_code="insufficient_ram_margin",
        )

    if machine.memory_pressure is MemoryPressureLevel.HIGH:
        return _memory_shortfall_decision(
            estimate,
            machine,
            runners,
            idle_runners,
            active_runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            reason_code="memory_pressure_high",
        )

    if _gpu_estimate_is_uncertain(estimate):
        return _memory_shortfall_decision(
            estimate,
            machine,
            runners,
            idle_runners,
            active_runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            reason_code="gpu_estimate_uncertain",
        )

    compatibility = _co_residence_compatibility(estimate, runners, machine)
    if compatibility is not None:
        reason_code, risk_level = compatibility
        return _memory_shortfall_decision(
            estimate,
            machine,
            runners,
            idle_runners,
            active_runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            reason_code=reason_code,
            risk_level=risk_level,
        )

    if not _vram_margin_ok(machine, estimate, required_vram_margin_mb):
        return _memory_shortfall_decision(
            estimate,
            machine,
            runners,
            idle_runners,
            active_runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            reason_code="insufficient_vram_margin",
        )

    if not _ram_margin_ok(machine, estimate, required_ram_margin_mb):
        return _memory_shortfall_decision(
            estimate,
            machine,
            runners,
            idle_runners,
            active_runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            reason_code="insufficient_ram_margin",
        )

    return _decision(
        MemoryDecisionAction.START_CO_RESIDENT,
        _co_residence_risk_level(estimate, runners),
        "co_residence_margin_available" if runner_ids else "no_resident_runners",
        "Noofy can keep the recent workflow ready while starting this one.",
        estimate,
        machine,
        runners,
        required_vram_margin_mb,
        required_ram_margin_mb,
        predicted_free_vram_after_mb,
        predicted_free_ram_after_mb,
    )


def wait_for_memory_release(
    observer: MachineMemoryObserver,
    *,
    required_free_vram_mb: int | None = None,
    required_free_ram_mb: int | None = None,
    max_checks: int = 3,
    interval_seconds: float = 0.1,
    sleeper: Callable[[float], None] | None = None,
) -> MemoryReleaseCheckResult:
    """Poll memory snapshots until required free memory appears or timeout."""
    sleeper = sleeper or time.sleep
    snapshots: list[MachineMemorySnapshot] = []
    for index in range(max(1, max_checks)):
        snapshot = observer.snapshot()
        snapshots.append(snapshot)
        if not snapshot.available:
            return MemoryReleaseCheckResult(
                status=MemoryReleaseStatus.UNAVAILABLE,
                required_free_vram_mb=required_free_vram_mb,
                required_free_ram_mb=required_free_ram_mb,
                snapshots=snapshots,
                reason_code="memory_snapshot_unavailable",
            )
        if memory_release_satisfied(
            snapshot,
            required_free_vram_mb=required_free_vram_mb,
            required_free_ram_mb=required_free_ram_mb,
        ):
            return MemoryReleaseCheckResult(
                status=MemoryReleaseStatus.RELEASED,
                required_free_vram_mb=required_free_vram_mb,
                required_free_ram_mb=required_free_ram_mb,
                snapshots=snapshots,
                reason_code="memory_released",
            )
        if index < max_checks - 1:
            sleeper(interval_seconds)
    return MemoryReleaseCheckResult(
        status=MemoryReleaseStatus.TIMEOUT,
        required_free_vram_mb=required_free_vram_mb,
        required_free_ram_mb=required_free_ram_mb,
        snapshots=snapshots,
        reason_code="memory_release_timeout",
    )


def memory_release_satisfied(
    snapshot: MachineMemorySnapshot,
    *,
    required_free_vram_mb: int | None = None,
    required_free_ram_mb: int | None = None,
) -> bool:
    if snapshot.memory_pressure is MemoryPressureLevel.HIGH:
        return False
    if required_free_vram_mb is not None:
        if snapshot.free_vram_mb is None or snapshot.free_vram_mb < required_free_vram_mb:
            return False
    if required_free_ram_mb is not None:
        if snapshot.free_ram_mb is None or snapshot.free_ram_mb < required_free_ram_mb:
            return False
    return True


def likely_memory_error(message: str | None) -> bool:
    if not message:
        return False
    normalized = message.lower()
    markers = [
        "cuda out of memory",
        "outofmemoryerror",
        "out of memory",
        "oom",
        "mps backend out of memory",
        "hip out of memory",
        "not enough memory",
    ]
    return any(marker in normalized for marker in markers)


def retry_after_memory_cleanup_decision(
    *,
    workflow_estimate: WorkflowMemoryEstimate,
    machine_snapshot: MachineMemorySnapshot,
    error_message: str | None,
    retry_already_attempted: bool = False,
) -> MemoryGovernorDecision:
    if retry_already_attempted:
        return _decision(
            MemoryDecisionAction.BLOCKED_BY_MEMORY,
            MemoryRiskLevel.HIGH,
            "retry_already_attempted",
            "Noofy freed memory, but this workflow still needs more memory than is safely available.",
            workflow_estimate,
            machine_snapshot,
            [],
            required_vram_margin(machine_snapshot, workflow_estimate),
            required_ram_margin(machine_snapshot, workflow_estimate),
            _subtract_optional(machine_snapshot.free_vram_mb, workflow_estimate.estimated_peak_vram_mb),
            _subtract_optional(machine_snapshot.free_ram_mb, workflow_estimate.estimated_peak_ram_mb),
        )
    if not likely_memory_error(error_message):
        return _decision(
            MemoryDecisionAction.BLOCKED_BY_MEMORY,
            MemoryRiskLevel.UNKNOWN,
            "not_a_memory_error",
            "This workflow failed for a reason that memory cleanup cannot safely fix.",
            workflow_estimate,
            machine_snapshot,
            [],
            required_vram_margin(machine_snapshot, workflow_estimate),
            required_ram_margin(machine_snapshot, workflow_estimate),
            _subtract_optional(machine_snapshot.free_vram_mb, workflow_estimate.estimated_peak_vram_mb),
            _subtract_optional(machine_snapshot.free_ram_mb, workflow_estimate.estimated_peak_ram_mb),
        )
    return _decision(
        MemoryDecisionAction.RETRY_AFTER_MEMORY_CLEANUP,
        MemoryRiskLevel.MEDIUM,
        "memory_error_retry_after_cleanup",
        "Noofy is freeing memory and will try this workflow one more time.",
        workflow_estimate,
        machine_snapshot,
        [],
        required_vram_margin(machine_snapshot, workflow_estimate),
        required_ram_margin(machine_snapshot, workflow_estimate),
        _subtract_optional(machine_snapshot.free_vram_mb, workflow_estimate.estimated_peak_vram_mb),
        _subtract_optional(machine_snapshot.free_ram_mb, workflow_estimate.estimated_peak_ram_mb),
        can_retry_after_cleanup=True,
    )


def record_memory_governor_decision(
    log_store: Any,
    decision: MemoryGovernorDecision,
    *,
    level: str = "info",
) -> Any:
    """Persist a decision in the existing diagnostic event stream."""
    message = decision.user_message or decision.reason_summary or f"Memory Governor decision: {decision.action}"
    return log_store.add(
        level,
        message,
        "memory_governor",
        workflow_id=decision.workflow_id,
        details=decision.diagnostic_details(),
    )


def memory_user_status_for_decision(
    decision: MemoryGovernorDecision,
    *,
    queue_id: str | None = None,
) -> MemoryUserStatus:
    if decision.action is MemoryDecisionAction.START_CO_RESIDENT:
        return MemoryUserStatus(
            state="ready_warm_co_resident",
            message="This workflow can stay ready while another workflow is warm.",
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=decision.can_retry_after_cleanup,
        )
    if decision.action is MemoryDecisionAction.REUSE_RUNNER:
        return MemoryUserStatus(
            state="ready_reusing_runner",
            message="This workflow is ready to run quickly.",
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=decision.can_retry_after_cleanup,
        )
    if decision.action is MemoryDecisionAction.EVICT_THEN_START:
        return MemoryUserStatus(
            state="freeing_memory",
            message="Noofy is freeing memory before starting this workflow.",
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=decision.can_retry_after_cleanup,
        )
    if decision.action in {
        MemoryDecisionAction.QUEUE_PENDING_MEMORY,
        MemoryDecisionAction.QUEUE_PENDING_SWITCH,
    }:
        return MemoryUserStatus(
            state="waiting_for_gpu",
            message="This workflow is waiting until the current GPU work finishes.",
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=decision.can_retry_after_cleanup,
        )
    if decision.action is MemoryDecisionAction.WAIT_FOR_MEMORY_RELEASE:
        return MemoryUserStatus(
            state="waiting_for_memory_release",
            message="Noofy is waiting for memory to become available.",
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=decision.can_retry_after_cleanup,
        )
    if decision.action is MemoryDecisionAction.RETRY_AFTER_MEMORY_CLEANUP:
        return MemoryUserStatus(
            state="retrying_after_memory_cleanup",
            message="Noofy freed memory and is trying this workflow one more time.",
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=True,
        )
    if decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY:
        return MemoryUserStatus(
            state="blocked_by_memory",
            message=decision.user_message or "This workflow needs more memory than Noofy can safely use right now.",
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=False,
        )
    return MemoryUserStatus(
        state="memory_status_unknown",
        message=decision.user_message or "Noofy is checking memory for this workflow.",
        risk_level=decision.risk_level,
        queue_id=queue_id,
        can_retry_after_cleanup=decision.can_retry_after_cleanup,
    )


def _confidence_rank(confidence: RunnerMemoryEstimateConfidence) -> int:
    if confidence is RunnerMemoryEstimateConfidence.HIGH:
        return 3
    if confidence is RunnerMemoryEstimateConfidence.MEDIUM:
        return 2
    if confidence is RunnerMemoryEstimateConfidence.LOW:
        return 1
    return 0


def _local_evidence_matches_request(
    request: WorkflowMemoryEstimateRequest,
    local_evidence: LocalMemoryEvidenceSummary,
) -> bool:
    if request.input_profile_fingerprint is None or local_evidence.input_profile_fingerprint is None:
        return True
    return request.input_profile_fingerprint == local_evidence.input_profile_fingerprint


def required_vram_margin(machine: MachineMemorySnapshot, estimate: WorkflowMemoryEstimate) -> int:
    if _request_is_cpu_only(estimate):
        return 0
    if machine.backend is not MemoryBackend.CUDA or machine.total_vram_mb is None:
        return 4096
    total = machine.total_vram_mb
    if total <= 8192:
        return max(2048, int(total * 0.25))
    if total <= 12288:
        return max(2560, int(total * 0.20))
    if total <= 24576:
        return max(3072, int(total * 0.15))
    return max(4096, int(total * 0.12))


def required_ram_margin(machine: MachineMemorySnapshot, estimate: WorkflowMemoryEstimate) -> int:
    del estimate
    if machine.backend is MemoryBackend.MPS:
        return max(4096, int((machine.total_ram_mb or 0) * 0.20))
    if machine.total_ram_mb is None:
        return 2048
    return max(2048, int(machine.total_ram_mb * 0.10))


def eviction_candidates(runners: list[RunnerMemorySnapshot]) -> list[RunnerMemorySnapshot]:
    return sorted(
        [runner for runner in runners if not _runner_snapshot_is_active(runner)],
        key=lambda runner: (
            runner.open_workflow_lease_count,
            -(runner.observed_idle_vram_mb or runner.observed_execution_peak_vram_mb or 0),
            runner.runner_id,
        ),
    )


def _estimate_memory_class(
    declared_memory_class: RunnerMemoryClass,
    estimated_peak_vram_mb: int | None,
) -> RunnerMemoryClass:
    if declared_memory_class is not RunnerMemoryClass.UNKNOWN:
        return declared_memory_class
    if estimated_peak_vram_mb is None:
        return RunnerMemoryClass.UNKNOWN
    if estimated_peak_vram_mb >= 10_000:
        return RunnerMemoryClass.GPU_HEAVY
    if estimated_peak_vram_mb >= 4_000:
        return RunnerMemoryClass.GPU_MEDIUM
    if estimated_peak_vram_mb > 0:
        return RunnerMemoryClass.GPU_LIGHT
    return RunnerMemoryClass.UNKNOWN


def _decision(
    action: MemoryDecisionAction,
    risk_level: MemoryRiskLevel,
    reason_code: str,
    user_message: str,
    estimate: WorkflowMemoryEstimate,
    machine: MachineMemorySnapshot,
    runners: list[RunnerMemorySnapshot],
    required_vram_margin_mb: int | None,
    required_ram_margin_mb: int | None,
    predicted_free_vram_after_mb: int | None,
    predicted_free_ram_after_mb: int | None,
    *,
    evict_runner_ids: list[str] | None = None,
    queued_behind_runner_id: str | None = None,
    can_retry_after_cleanup: bool = False,
    developer_details: dict[str, Any] | None = None,
) -> MemoryGovernorDecision:
    return MemoryGovernorDecision(
        action=action,
        risk_level=risk_level,
        confidence=estimate.confidence,
        reason_code=reason_code,
        workflow_id=estimate.workflow_id,
        evict_runner_ids=evict_runner_ids or [],
        queued_behind_runner_id=queued_behind_runner_id,
        machine_snapshot=machine,
        workflow_estimate=estimate,
        runner_snapshots=runners,
        required_vram_margin_mb=required_vram_margin_mb,
        required_ram_margin_mb=required_ram_margin_mb,
        predicted_free_vram_after_mb=predicted_free_vram_after_mb,
        predicted_free_ram_after_mb=predicted_free_ram_after_mb,
        can_retry_after_cleanup=can_retry_after_cleanup,
        user_message=user_message,
        developer_details=developer_details or {},
    )


def _memory_shortfall_decision(
    estimate: WorkflowMemoryEstimate,
    machine: MachineMemorySnapshot,
    runners: list[RunnerMemorySnapshot],
    idle_runners: list[RunnerMemorySnapshot],
    active_runners: list[RunnerMemorySnapshot],
    required_vram_margin_mb: int | None,
    required_ram_margin_mb: int | None,
    predicted_free_vram_after_mb: int | None,
    predicted_free_ram_after_mb: int | None,
    *,
    reason_code: str,
    risk_level: MemoryRiskLevel = MemoryRiskLevel.HIGH,
) -> MemoryGovernorDecision:
    if active_runners:
        active_runner = sorted(active_runners, key=lambda runner: runner.runner_id)[0]
        return _decision(
            MemoryDecisionAction.QUEUE_PENDING_MEMORY,
            risk_level,
            reason_code,
            "This workflow is waiting until the current GPU work finishes.",
            estimate,
            machine,
            runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            queued_behind_runner_id=active_runner.runner_id,
            can_retry_after_cleanup=True,
        )

    candidates = eviction_candidates(idle_runners)
    if candidates:
        return _decision(
            MemoryDecisionAction.EVICT_THEN_START,
            risk_level,
            reason_code,
            "Noofy is freeing memory before starting this workflow.",
            estimate,
            machine,
            runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            evict_runner_ids=[runner.runner_id for runner in candidates],
            can_retry_after_cleanup=True,
        )

    return _decision(
        MemoryDecisionAction.BLOCKED_BY_MEMORY,
        risk_level,
        reason_code,
        "This workflow needs more memory than Noofy can safely use right now.",
        estimate,
        machine,
        runners,
        required_vram_margin_mb,
        required_ram_margin_mb,
        predicted_free_vram_after_mb,
        predicted_free_ram_after_mb,
    )


def _request_is_cpu_only(estimate: WorkflowMemoryEstimate) -> bool:
    return estimate.memory_class is RunnerMemoryClass.CPU_ONLY


def _runner_snapshot_is_active(runner: RunnerMemorySnapshot) -> bool:
    return runner.current_job_id is not None or runner.status in {
        RunnerStatus.RUNNING,
        RunnerStatus.LOADING_MODEL,
        RunnerStatus.RETRYING_AFTER_MEMORY_CLEANUP,
    }


def _gpu_estimate_is_uncertain(estimate: WorkflowMemoryEstimate) -> bool:
    if estimate.memory_class is RunnerMemoryClass.CPU_ONLY:
        return False
    if estimate.recent_memory_error:
        return True
    if estimate.conservative_memory_class is RunnerMemoryClass.GPU_HEAVY:
        return estimate.confidence not in {
            RunnerMemoryEstimateConfidence.MEDIUM,
            RunnerMemoryEstimateConfidence.HIGH,
        }
    return estimate.confidence is RunnerMemoryEstimateConfidence.UNKNOWN


def _co_residence_compatibility(
    estimate: WorkflowMemoryEstimate,
    runners: list[RunnerMemorySnapshot],
    machine: MachineMemorySnapshot,
) -> tuple[str, MemoryRiskLevel] | None:
    requested = estimate.memory_class
    for runner in runners:
        existing = runner.memory_class
        if existing is RunnerMemoryClass.CPU_ONLY:
            continue
        if requested is RunnerMemoryClass.CPU_ONLY:
            continue
        if RunnerMemoryClass.UNKNOWN in {estimate.memory_class, runner.memory_class}:
            return "unknown_memory_class_denies_co_residence", MemoryRiskLevel.HIGH
        if requested is RunnerMemoryClass.GPU_HEAVY and existing is RunnerMemoryClass.GPU_HEAVY:
            if _large_gpu_high_confidence_heavy_pair_allowed(estimate, runner, machine):
                continue
            return "heavy_heavy_requires_large_gpu_and_high_confidence", MemoryRiskLevel.HIGH
        if requested is RunnerMemoryClass.GPU_HEAVY and existing is RunnerMemoryClass.GPU_MEDIUM:
            return "heavy_medium_requires_eviction_in_v1", MemoryRiskLevel.MEDIUM
        if requested is RunnerMemoryClass.GPU_MEDIUM and existing is RunnerMemoryClass.GPU_HEAVY:
            return "heavy_medium_requires_eviction_in_v1", MemoryRiskLevel.MEDIUM
        if requested is RunnerMemoryClass.GPU_MEDIUM and existing is RunnerMemoryClass.GPU_MEDIUM:
            if estimate.confidence is RunnerMemoryEstimateConfidence.HIGH and runner.memory_estimate_confidence is RunnerMemoryEstimateConfidence.HIGH:
                continue
            return "medium_medium_requires_high_confidence", MemoryRiskLevel.MEDIUM
    return None


def _large_gpu_high_confidence_heavy_pair_allowed(
    estimate: WorkflowMemoryEstimate,
    runner: RunnerMemorySnapshot,
    machine: MachineMemorySnapshot,
) -> bool:
    return (
        machine.total_vram_mb is not None
        and machine.total_vram_mb >= 24_000
        and estimate.confidence is RunnerMemoryEstimateConfidence.HIGH
        and runner.memory_estimate_confidence is RunnerMemoryEstimateConfidence.HIGH
        and runner.memory_estimate_source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
        and estimate.effective_source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    )


def _vram_margin_ok(
    machine: MachineMemorySnapshot,
    estimate: WorkflowMemoryEstimate,
    required_vram_margin_mb: int,
) -> bool:
    if _request_is_cpu_only(estimate):
        return True
    if machine.free_vram_mb is None or estimate.estimated_peak_vram_mb is None:
        return False
    return machine.free_vram_mb - estimate.estimated_peak_vram_mb >= required_vram_margin_mb


def _ram_margin_ok(
    machine: MachineMemorySnapshot,
    estimate: WorkflowMemoryEstimate,
    required_ram_margin_mb: int,
) -> bool:
    if machine.free_ram_mb is None or estimate.estimated_peak_ram_mb is None:
        return True
    return machine.free_ram_mb - estimate.estimated_peak_ram_mb >= required_ram_margin_mb


def _co_residence_risk_level(
    estimate: WorkflowMemoryEstimate,
    runners: list[RunnerMemorySnapshot],
) -> MemoryRiskLevel:
    if not runners or estimate.memory_class in {RunnerMemoryClass.CPU_ONLY, RunnerMemoryClass.GPU_LIGHT}:
        return MemoryRiskLevel.LOW
    if estimate.memory_class is RunnerMemoryClass.GPU_HEAVY:
        return MemoryRiskLevel.MEDIUM
    return MemoryRiskLevel.LOW


def _subtract_optional(left: int | None, right: int | None) -> int | None:
    if left is None or right is None:
        return None
    return max(0, left - right)


def _heuristic_peak_vram_mb(request: WorkflowMemoryEstimateRequest) -> int | None:
    components: list[int] = []
    if request.required_model_size_mb is not None:
        components.append(int(request.required_model_size_mb * 1.35))
    if request.resolution_width is not None and request.resolution_height is not None:
        megapixels = (request.resolution_width * request.resolution_height * request.batch_size) / 1_000_000
        components.append(int(megapixels * 900))
    if not components:
        return None
    return max(int(sum(components) * _workflow_type_heuristic_factor(request.workflow_type)), 512)


def _workflow_type_heuristic_factor(workflow_type: str | None) -> float:
    if workflow_type is None:
        return 1.0
    normalized = workflow_type.lower().replace("-", "_")
    if "controlnet" in normalized or "img2img" in normalized:
        return 1.2
    if "upscale" in normalized or "post" in normalized:
        return 0.8
    return 1.0


def _max_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _latest_observed_at(observations: list[LocalMemoryObservation]) -> str | None:
    values = [observation.observed_at for observation in observations if observation.observed_at]
    return max(values) if values else None


def _learning_key_for(
    *,
    workflow_id: str,
    runner_process_compatibility_key: str | None,
    machine_profile_id: str | None,
    backend: MemoryBackend,
    input_profile_fingerprint: str | None,
) -> str:
    return "::".join(
        [
            workflow_id,
            runner_process_compatibility_key or "no-runner-key",
            machine_profile_id or "no-machine-profile",
            backend.value,
            input_profile_fingerprint or "no-input-profile",
        ]
    )


def _safe_learning_key(key: str) -> str:
    return (
        key.replace("sha256:", "")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
        .replace(" ", "_")
    )


def _parse_nvidia_smi_memory_row(output: str) -> tuple[str | None, int | None, int | None]:
    line = next((candidate.strip() for candidate in output.splitlines() if candidate.strip()), "")
    if not line:
        return None, None, None
    parts = [part.strip() for part in line.split(",")]
    device_name = parts[0] if parts and parts[0] else None
    total_vram_mb = _parse_int(parts[1]) if len(parts) > 1 else None
    free_vram_mb = _parse_int(parts[2]) if len(parts) > 2 else None
    return device_name, total_vram_mb, free_vram_mb


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _unavailable_cuda_snapshot(error: str) -> MachineMemorySnapshot:
    return MachineMemorySnapshot(
        available=False,
        backend=MemoryBackend.CUDA,
        memory_pressure=MemoryPressureLevel.UNKNOWN,
        observed_at=_now_iso(),
        error=error,
    )


def _system_ram_mb() -> tuple[int | None, int | None]:
    if os.name != "posix":
        return None, None
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        total_pages = os.sysconf("SC_PHYS_PAGES")
        available_pages = os.sysconf("SC_AVPHYS_PAGES")
    except (ValueError, OSError):
        return None, None
    total_ram_mb = int(page_size * total_pages / (1024 * 1024))
    free_ram_mb = int(page_size * available_pages / (1024 * 1024))
    return total_ram_mb, free_ram_mb


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
