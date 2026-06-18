"""Memory Governor observers, schemas, and decision records.

The Memory Governor keeps observation, estimate, local learning, and admission
decisions separate so platform-specific memory signals can improve decisions
without turning fallback margins into the main policy engine.
"""

from __future__ import annotations

import asyncio
import os
import contextlib
import json
import platform
import re
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.diagnostics import DiagnosticsSink
from app.runtime.runners.supervisor import (
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    RunnerMemoryEstimateConfidence,
    RunnerMemoryEstimateSource,
    RunnerStatus,
)
from app.runtime.memory.system_memory import (
    darwin_system_ram_mb,
    linux_system_ram_mb,
    parse_darwin_available_memory_bytes,
    system_ram_mb,
    windows_system_ram_mb,
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


class MemorySignalQuality(StrEnum):
    BACKEND_API = "backend_api"
    BACKEND_BUDGET = "backend_budget"
    ALLOCATOR = "allocator"
    PROCESS_SAMPLE = "process_sample"
    SYSTEM_PRESSURE = "system_pressure"
    SYSTEM_SAMPLE = "system_sample"
    HEURISTIC = "heuristic"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class MemoryAttributionQuality(StrEnum):
    PROCESS_EXACT = "process_exact"
    PROCESS_TREE = "process_tree"
    BACKEND_ALLOCATOR = "backend_allocator"
    ACTIVE_WINDOW_DELTA = "active_window_delta"
    SYSTEM_DELTA = "system_delta"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class MemorySampleWindow(StrEnum):
    STARTUP = "startup"
    RUNNER_STARTUP = "runner_startup"
    MODEL_LOAD = "model_load"
    MODEL_LOADING = "model_loading"
    BEFORE_SUBMIT = "before_submit"
    EXECUTION = "execution"
    WORKFLOW_EXECUTION = "workflow_execution"
    RETRY_AFTER_CLEANUP = "retry_after_cleanup"
    AFTER_COMPLETION = "after_completion"
    CLEANUP = "cleanup"
    RELEASE = "release"
    MEMORY_RELEASE = "memory_release"
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


class MemoryCleanupMode(StrEnum):
    PER_LORA_UNLOAD = "per_lora_unload"
    PER_MODEL_UNLOAD = "per_model_unload"
    RUNNER_FREE = "runner_free"
    ISOLATED_EVICTION = "isolated_eviction"
    CLEANUP_UNSUPPORTED = "cleanup_unsupported"


class MemoryObservationOutcome(StrEnum):
    SUCCESS = "success"
    MEMORY_ERROR = "memory_error"
    RUNTIME_ERROR = "runtime_error"
    CANCELED = "canceled"
    UNKNOWN = "unknown"


class MemoryReleaseStatus(StrEnum):
    RELEASED = "released"
    # Cleanup was observed, but another admission constraint remains unmet.
    # The cleaned runner's residency may be cleared, while the next workflow
    # must still remain blocked.
    RELEASED_INSUFFICIENT_MEMORY = "released_insufficient_memory"
    # `/free` was acknowledged but no RAM/VRAM drop could be observed. This is
    # NOT a proven release: it is only used to allow a narrow same-process
    # cautious-start, and the runner's residency must stay intact.
    ACKNOWLEDGED_UNCONFIRMED = "acknowledged_unconfirmed"
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
    signal_quality: MemorySignalQuality = MemorySignalQuality.UNKNOWN
    signal_sources: list[str] = Field(default_factory=list)
    pressure_reasons: list[str] = Field(default_factory=list)
    runner_id: str | None = None
    job_id: str | None = None
    workflow_id: str | None = None
    runner_root_pid: int | None = Field(default=None, ge=0)
    runner_child_pids: list[int] = Field(default_factory=list)
    sample_window: MemorySampleWindow = MemorySampleWindow.UNKNOWN
    attribution_quality: MemoryAttributionQuality = MemoryAttributionQuality.UNKNOWN
    attribution_sources: list[str] = Field(default_factory=list)
    attribution_reasons: list[str] = Field(default_factory=list)
    process_tree_ram_mb: int | None = Field(default=None, ge=0)
    process_tree_vram_mb: int | None = Field(default=None, ge=0)
    backend_allocator_current_vram_mb: int | None = Field(default=None, ge=0)
    backend_allocator_peak_vram_mb: int | None = Field(default=None, ge=0)
    backend_allocator_details: dict[str, Any] = Field(default_factory=dict)
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
        if (
            self.total_ram_mb is not None
            and self.free_ram_mb is not None
            and self.free_ram_mb > self.total_ram_mb
        ):
            raise ValueError("free_ram_mb cannot exceed total_ram_mb")
        return self


class RunnerMemorySnapshot(BaseModel):
    """Memory-relevant state for a resident runner."""

    model_config = ConfigDict(extra="forbid")

    runner_id: str = Field(min_length=1)
    kind: RunnerKind = RunnerKind.ISOLATED_COMFYUI
    runner_process_compatibility_key: str | None = None
    memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    memory_estimate_confidence: RunnerMemoryEstimateConfidence = (
        RunnerMemoryEstimateConfidence.UNKNOWN
    )
    memory_estimate_source: RunnerMemoryEstimateSource = (
        RunnerMemoryEstimateSource.UNKNOWN
    )
    status: RunnerStatus = RunnerStatus.UNKNOWN
    current_job_id: str | None = None
    current_workflow_id: str | None = None
    last_workflow_id: str | None = None
    model_residency_signature: str | None = None
    model_residency_payload: dict[str, Any] = Field(default_factory=dict)
    execution_profile_signature: str | None = None
    last_used_at: str | None = None
    open_workflow_lease_count: int = Field(default=0, ge=0)
    output_stream_lease_count: int = Field(default=0, ge=0)
    reservation_kind: str | None = None
    reservation_token_present: bool = False
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
            kind=descriptor.kind,
            runner_process_compatibility_key=descriptor.runner_process_compatibility_key,
            memory_class=descriptor.memory_class,
            memory_estimate_confidence=descriptor.memory_estimate_confidence,
            memory_estimate_source=descriptor.memory_estimate_source,
            status=descriptor.status,
            current_job_id=descriptor.current_job_id,
            current_workflow_id=descriptor.current_workflow_id,
            last_workflow_id=descriptor.last_workflow_id,
            model_residency_signature=descriptor.model_residency_signature,
            model_residency_payload=dict(descriptor.model_residency_payload),
            execution_profile_signature=descriptor.execution_profile_signature,
            last_used_at=descriptor.last_used_at,
            open_workflow_lease_count=descriptor.open_workflow_lease_count,
            output_stream_lease_count=descriptor.output_stream_lease_count,
            reservation_kind=descriptor.reservation_kind.value
            if descriptor.reservation_kind is not None
            else None,
            reservation_token_present=descriptor.reservation_token is not None,
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
    process_compatibility_signature: str | None = None
    model_residency_signature: str | None = None
    model_residency_payload: dict[str, Any] = Field(default_factory=dict)
    execution_profile_signature: str | None = None
    successful_runs: int = Field(default=0, ge=0)
    memory_error_runs: int = Field(default=0, ge=0)
    other_failed_runs: int = Field(default=0, ge=0)
    evictions_required: int = Field(default=0, ge=0)
    retries_required: int = Field(default=0, ge=0)
    observed_peak_vram_mb: int | None = Field(default=None, ge=0)
    observed_peak_ram_mb: int | None = Field(default=None, ge=0)
    process_tree_observed_peak_vram_mb: int | None = Field(default=None, ge=0)
    process_tree_observed_peak_ram_mb: int | None = Field(default=None, ge=0)
    system_observed_peak_delta_vram_mb: int | None = Field(default=None, ge=0)
    system_observed_peak_delta_ram_mb: int | None = Field(default=None, ge=0)
    backend_allocator_observed_peak_vram_mb: int | None = Field(default=None, ge=0)
    attribution_quality: MemoryAttributionQuality = MemoryAttributionQuality.UNKNOWN
    attribution_sources: list[str] = Field(default_factory=list)
    attribution_reasons: list[str] = Field(default_factory=list)
    last_success_at: str | None = None
    last_memory_error_at: str | None = None

    @property
    def has_local_evidence(self) -> bool:
        return (
            self.successful_runs > 0
            or self.memory_error_runs > 0
            or self.other_failed_runs > 0
        )

    @property
    def has_estimate_evidence(self) -> bool:
        return self.successful_runs > 0 or self.memory_error_runs > 0

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
    process_compatibility_signature: str | None = None
    model_residency_signature: str | None = None
    model_residency_payload: dict[str, Any] = Field(default_factory=dict)
    execution_profile_signature: str | None = None
    runner_id: str | None = None
    job_id: str | None = None
    runner_root_pid: int | None = Field(default=None, ge=0)
    runner_child_pids: list[int] = Field(default_factory=list)
    sample_window: MemorySampleWindow = MemorySampleWindow.UNKNOWN
    outcome: MemoryObservationOutcome = MemoryObservationOutcome.UNKNOWN
    memory_class: RunnerMemoryClass = RunnerMemoryClass.UNKNOWN
    peak_vram_mb: int | None = Field(default=None, ge=0)
    peak_ram_mb: int | None = Field(default=None, ge=0)
    process_tree_peak_vram_mb: int | None = Field(default=None, ge=0)
    process_tree_peak_ram_mb: int | None = Field(default=None, ge=0)
    system_peak_delta_vram_mb: int | None = Field(default=None, ge=0)
    system_peak_delta_ram_mb: int | None = Field(default=None, ge=0)
    backend_allocator_peak_vram_mb: int | None = Field(default=None, ge=0)
    backend_allocator_details: dict[str, Any] = Field(default_factory=dict)
    attribution_quality: MemoryAttributionQuality = MemoryAttributionQuality.UNKNOWN
    attribution_sources: list[str] = Field(default_factory=list)
    attribution_reasons: list[str] = Field(default_factory=list)
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
    custom_node_count: int = Field(default=0, ge=0)
    custom_node_types: list[str] = Field(default_factory=list)
    selected_model_count: int = Field(default=0, ge=0)
    selected_model_kinds: list[str] = Field(default_factory=list)
    lora_count: int = Field(default=0, ge=0)
    lora_strength_total: float = Field(default=0, ge=0)
    process_compatibility_signature: str | None = None
    model_residency_signature: str | None = None
    model_residency_payload: dict[str, Any] = Field(default_factory=dict)
    execution_profile_signature: str | None = None
    precision: str | None = None
    vram_mode: str | None = None
    reasons: list[str] = Field(default_factory=list)

    @property
    def has_local_evidence(self) -> bool:
        return (
            self.local_evidence is not None and self.local_evidence.has_local_evidence
        )

    @property
    def effective_source(self) -> RunnerMemoryEstimateSource:
        if self.has_local_evidence:
            return RunnerMemoryEstimateSource.LOCAL_OBSERVED
        return self.source

    @property
    def conservative_memory_class(self) -> RunnerMemoryClass:
        return conservative_memory_class(self.memory_class)

    @field_validator("precision", mode="before")
    @classmethod
    def _normalize_precision(cls, value: Any) -> Any:
        return _normalize_runtime_precision_value(value)

    @field_validator("vram_mode", mode="before")
    @classmethod
    def _normalize_vram_mode(cls, value: Any) -> Any:
        return _normalize_runtime_vram_mode_value(value)


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
    custom_node_count: int = Field(default=0, ge=0)
    custom_node_types: list[str] = Field(default_factory=list)
    selected_model_count: int = Field(default=0, ge=0)
    selected_model_kinds: list[str] = Field(default_factory=list)
    lora_count: int = Field(default=0, ge=0)
    lora_strength_total: float = Field(default=0, ge=0)
    process_compatibility_signature: str | None = None
    model_residency_signature: str | None = None
    model_residency_payload: dict[str, Any] = Field(default_factory=dict)
    execution_profile_signature: str | None = None
    precision: str | None = None
    vram_mode: str | None = None

    @field_validator("precision", mode="before")
    @classmethod
    def _normalize_precision(cls, value: Any) -> Any:
        return _normalize_runtime_precision_value(value)

    @field_validator("vram_mode", mode="before")
    @classmethod
    def _normalize_vram_mode(cls, value: Any) -> Any:
        return _normalize_runtime_vram_mode_value(value)


class MemoryGovernorDecision(BaseModel):
    """Serializable decision record for diagnostics and API state."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(
        default_factory=lambda: f"mg-{uuid.uuid4().hex}", min_length=1
    )
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
    signal_quality: MemorySignalQuality = MemorySignalQuality.UNKNOWN
    signal_sources: list[str] = Field(default_factory=list)
    pressure_reasons: list[str] = Field(default_factory=list)
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
    selected_runner: RunnerMemorySnapshot | None = None
    resident_runners: list[RunnerMemorySnapshot] = Field(default_factory=list)
    queued_model_residency_payloads: list[dict[str, Any]] = Field(default_factory=list)
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]] = Field(default_factory=dict)


class MemoryReleaseCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: MemoryReleaseStatus
    required_free_vram_mb: int | None = Field(default=None, ge=0)
    required_free_ram_mb: int | None = Field(default=None, ge=0)
    snapshots: list[MachineMemorySnapshot] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    reason_code: str
    # Measurement proof: free RAM and VRAM before cleanup (baseline), and the
    # last values observed before the release decision. Both metrics are
    # always recorded so a block can never silently rest on one of them.
    baseline_free_vram_mb: int | None = None
    baseline_free_ram_mb: int | None = None
    final_free_vram_mb: int | None = None
    final_free_ram_mb: int | None = None
    # Which requirements were still unmet when the check gave up. Empty for a
    # confirmed release.
    blocking_constraints: list[str] = Field(default_factory=list)


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


class NvmlError(RuntimeError):
    """Raised when NVML is present but cannot provide usable memory data."""


class NvmlMemoryApi(Protocol):
    def read_memory(self) -> tuple[str | None, int | None, int | None]:
        """Return device name, total VRAM MB, and free VRAM MB for the primary GPU."""

    def read_process_memory(self) -> list[GpuProcessMemoryUsage]:
        """Return best-effort per-process GPU memory for the primary GPU."""


class GpuProcessMemoryUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pid: int = Field(ge=0)
    used_vram_mb: int = Field(ge=0)


class GpuProcessMemorySample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    requested_pids: list[int] = Field(default_factory=list)
    matched_pids: list[int] = Field(default_factory=list)
    process_tree_vram_mb: int | None = Field(default=None, ge=0)
    attribution_quality: MemoryAttributionQuality = MemoryAttributionQuality.UNAVAILABLE
    attribution_sources: list[str] = Field(default_factory=list)
    attribution_reasons: list[str] = Field(default_factory=list)
    error: str | None = None


class ProcessTreeMemorySample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    root_pid: int | None = Field(default=None, ge=0)
    child_pids: list[int] = Field(default_factory=list)
    process_tree_ram_mb: int | None = Field(default=None, ge=0)
    attribution_quality: MemoryAttributionQuality = MemoryAttributionQuality.UNAVAILABLE
    attribution_sources: list[str] = Field(default_factory=list)
    attribution_reasons: list[str] = Field(default_factory=list)
    error: str | None = None


class BackendAllocatorMemorySample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    runner_id: str | None = None
    job_id: str | None = None
    pid: int | None = Field(default=None, ge=0)
    sample_window: MemorySampleWindow = MemorySampleWindow.UNKNOWN
    backend: MemoryBackend = MemoryBackend.UNKNOWN
    current_vram_mb: int | None = Field(default=None, ge=0)
    peak_vram_mb: int | None = Field(default=None, ge=0)
    budget_vram_mb: int | None = Field(default=None, ge=0)
    signal_quality: MemorySignalQuality = MemorySignalQuality.UNAVAILABLE
    attribution_quality: MemoryAttributionQuality = MemoryAttributionQuality.UNAVAILABLE
    attribution_sources: list[str] = Field(default_factory=list)
    attribution_reasons: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


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
            signal_quality=MemorySignalQuality.UNAVAILABLE,
            signal_sources=["unavailable_memory_observer"],
            observed_at=_now_iso(),
            error=self.error,
        )


class SystemMemoryObserver:
    """Best-effort RAM observer used for CPU, MPS, DirectML, and fallback paths."""

    def __init__(
        self,
        *,
        backend: MemoryBackend = MemoryBackend.CPU,
        linux_psi_reader: Callable[[], str | None] | None = None,
    ) -> None:
        self.backend = backend
        self._linux_psi_reader = linux_psi_reader

    def snapshot(self) -> MachineMemorySnapshot:
        total_ram_mb, free_ram_mb, pressure, sources, reasons = _system_ram_signals(
            linux_psi_reader=self._linux_psi_reader,
        )
        has_ram = total_ram_mb is not None or free_ram_mb is not None
        return MachineMemorySnapshot(
            available=has_ram or "linux_psi" in sources,
            backend=self.backend,
            total_ram_mb=total_ram_mb,
            free_ram_mb=free_ram_mb,
            memory_pressure=pressure,
            signal_quality=_system_signal_quality(
                has_ram=has_ram, has_psi="linux_psi" in sources
            ),
            signal_sources=sources,
            pressure_reasons=reasons,
            observed_at=_now_iso(),
            error=None if has_ram else "system_ram_unavailable",
        )


class ProcessTreeMemoryObserver:
    """Best-effort RSS observer for a runner root process and its children."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        platform_name: str | None = None,
        timeout_seconds: float = 2,
    ) -> None:
        self._command_runner = command_runner or self._run_command
        self._platform_name = platform_name or platform.system()
        self._timeout_seconds = timeout_seconds

    def sample(self, root_pid: int | None) -> ProcessTreeMemorySample:
        if root_pid is None:
            return _unavailable_process_tree_sample(None, "runner_pid_unavailable")
        try:
            if self._platform_name == "Windows":
                result = self._command_runner(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        _WINDOWS_PROCESS_TREE_SCRIPT,
                    ]
                )
                rows = (
                    _parse_windows_process_rows(result.stdout)
                    if result.returncode == 0
                    else None
                )
            else:
                result = self._command_runner(["ps", "-axo", "pid=,ppid=,rss="])
                rows = (
                    _parse_posix_process_rows(result.stdout)
                    if result.returncode == 0
                    else None
                )
        except FileNotFoundError:
            return _unavailable_process_tree_sample(
                root_pid, "process_observer_command_not_found"
            )
        except subprocess.TimeoutExpired:
            return _unavailable_process_tree_sample(
                root_pid, "process_observer_timeout"
            )
        except OSError as exc:
            return _unavailable_process_tree_sample(
                root_pid, f"process_observer_error:{exc}"
            )
        if rows is None:
            error = (
                result.stderr or result.stdout or "process_observer_failed"
            ).strip()
            return _unavailable_process_tree_sample(root_pid, error)
        return _process_tree_sample_from_rows(root_pid, rows)

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=self._timeout_seconds,
        )


class NvmlMemoryObserver:
    """Direct NVIDIA GPU observer backed by NVML when the host provides it."""

    def __init__(self, *, api: NvmlMemoryApi | None = None) -> None:
        self._api = api or _CtypesNvmlApi()

    def snapshot(self) -> MachineMemorySnapshot:
        try:
            device_name, total_vram_mb, free_vram_mb = self._api.read_memory()
        except FileNotFoundError:
            return _unavailable_cuda_snapshot("nvml_not_found", source="nvml")
        except NvmlError as exc:
            return _unavailable_cuda_snapshot(f"nvml_error:{exc}", source="nvml")
        except OSError as exc:
            return _unavailable_cuda_snapshot(f"nvml_error:{exc}", source="nvml")
        except Exception as exc:
            return _unavailable_cuda_snapshot(f"nvml_error:{exc}", source="nvml")

        total_ram_mb, free_ram_mb, ram_pressure, ram_sources, ram_reasons = (
            _system_ram_signals()
        )
        vram_pressure = memory_pressure_from_free_ratio(total_vram_mb, free_vram_mb)
        available = total_vram_mb is not None or free_vram_mb is not None
        return MachineMemorySnapshot(
            available=available,
            backend=MemoryBackend.CUDA,
            device_name=device_name,
            total_vram_mb=total_vram_mb,
            free_vram_mb=free_vram_mb,
            total_ram_mb=total_ram_mb,
            free_ram_mb=free_ram_mb,
            memory_pressure=_max_pressure_level(vram_pressure, ram_pressure),
            signal_quality=(
                MemorySignalQuality.BACKEND_API
                if available
                else MemorySignalQuality.UNAVAILABLE
            ),
            signal_sources=(["nvml"] if available else []) + ram_sources,
            pressure_reasons=_free_ratio_pressure_reasons("vram", vram_pressure)
            + ram_reasons,
            observed_at=_now_iso(),
            error=None if available else "nvml_memory_unavailable",
        )

    def sample_process_vram(self, pids: Iterable[int]) -> GpuProcessMemorySample:
        requested = sorted({pid for pid in pids if pid >= 0})
        if not requested:
            return GpuProcessMemorySample(
                requested_pids=[],
                attribution_sources=["nvml"],
                error="no_process_pids",
            )
        try:
            usages = self._api.read_process_memory()
        except FileNotFoundError:
            return _unavailable_gpu_process_sample(requested, "nvml_not_found")
        except NvmlError as exc:
            return _unavailable_gpu_process_sample(
                requested, f"nvml_process_error:{exc}"
            )
        except OSError as exc:
            return _unavailable_gpu_process_sample(
                requested, f"nvml_process_error:{exc}"
            )
        except Exception as exc:
            return _unavailable_gpu_process_sample(
                requested, f"nvml_process_error:{exc}"
            )
        matched = [usage for usage in usages if usage.pid in requested]
        if not matched:
            return GpuProcessMemorySample(
                requested_pids=requested,
                attribution_sources=["nvml_process"],
                attribution_reasons=["nvml_process_memory_no_matching_pid"],
                error="nvml_process_memory_no_matching_pid",
            )
        return GpuProcessMemorySample(
            available=True,
            requested_pids=requested,
            matched_pids=sorted({usage.pid for usage in matched}),
            process_tree_vram_mb=sum(usage.used_vram_mb for usage in matched),
            attribution_quality=MemoryAttributionQuality.PROCESS_EXACT,
            attribution_sources=["nvml_process"],
            attribution_reasons=["nvml_process_memory_matched_runner_pid"],
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
            return _unavailable_cuda_snapshot(
                "nvidia_smi_not_found", source="nvidia_smi"
            )
        except subprocess.TimeoutExpired:
            return _unavailable_cuda_snapshot("nvidia_smi_timeout", source="nvidia_smi")
        except OSError as exc:
            return _unavailable_cuda_snapshot(
                f"nvidia_smi_error:{exc}", source="nvidia_smi"
            )

        if result.returncode != 0:
            error = (result.stderr or result.stdout or "nvidia_smi_failed").strip()
            return _unavailable_cuda_snapshot(error, source="nvidia_smi")

        device_name, total_vram_mb, free_vram_mb = _parse_nvidia_smi_memory_row(
            result.stdout
        )
        total_ram_mb, free_ram_mb, ram_pressure, ram_sources, ram_reasons = (
            _system_ram_signals()
        )
        vram_pressure = memory_pressure_from_free_ratio(total_vram_mb, free_vram_mb)
        available = total_vram_mb is not None or free_vram_mb is not None
        return MachineMemorySnapshot(
            available=available,
            backend=MemoryBackend.CUDA,
            device_name=device_name,
            total_vram_mb=total_vram_mb,
            free_vram_mb=free_vram_mb,
            total_ram_mb=total_ram_mb,
            free_ram_mb=free_ram_mb,
            memory_pressure=_max_pressure_level(vram_pressure, ram_pressure),
            signal_quality=(
                MemorySignalQuality.BACKEND_API
                if available
                else MemorySignalQuality.UNAVAILABLE
            ),
            signal_sources=(["nvidia_smi"] if available else []) + ram_sources,
            pressure_reasons=_free_ratio_pressure_reasons("vram", vram_pressure)
            + ram_reasons,
            observed_at=_now_iso(),
            error=None if available else "nvidia_smi_parse_failed",
        )

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=self._timeout_seconds,
        )


class WindowsGpuMemoryObserver:
    """Best-effort Windows GPU memory observer for DirectML-class fallback.

    Windows does not expose one portable DirectML memory API for every vendor.
    This observer uses PowerShell performance counters and Win32 video
    controller metadata when available, and returns structured unavailable or
    partial snapshots when those signals are missing.
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
        try:
            result = self._command_runner(
                ["powershell", "-NoProfile", "-Command", _WINDOWS_GPU_MEMORY_SCRIPT]
            )
        except FileNotFoundError:
            return _unavailable_directml_snapshot("powershell_not_found")
        except subprocess.TimeoutExpired:
            return _unavailable_directml_snapshot("windows_gpu_observer_timeout")
        except OSError as exc:
            return _unavailable_directml_snapshot(f"windows_gpu_observer_error:{exc}")

        if result.returncode != 0:
            error = (
                result.stderr or result.stdout or "windows_gpu_observer_failed"
            ).strip()
            return _unavailable_directml_snapshot(error)

        device_name, total_vram_mb, free_vram_mb, error = (
            _parse_windows_gpu_memory_json(result.stdout)
        )
        total_ram_mb, free_ram_mb, ram_pressure, ram_sources, ram_reasons = (
            _system_ram_signals()
        )
        available = any(
            value is not None
            for value in (total_vram_mb, free_vram_mb, total_ram_mb, free_ram_mb)
        )
        vram_pressure = memory_pressure_from_free_ratio(total_vram_mb, free_vram_mb)
        gpu_sources = []
        if (
            device_name is not None
            or total_vram_mb is not None
            or free_vram_mb is not None
        ):
            gpu_sources.extend(["windows_gpu_counters", "win32_video_controller"])
        return MachineMemorySnapshot(
            available=available,
            backend=MemoryBackend.DIRECTML,
            device_name=device_name,
            total_vram_mb=total_vram_mb,
            free_vram_mb=free_vram_mb,
            total_ram_mb=total_ram_mb,
            free_ram_mb=free_ram_mb,
            memory_pressure=_max_pressure_level(vram_pressure, ram_pressure),
            signal_quality=(
                MemorySignalQuality.SYSTEM_SAMPLE
                if available
                else MemorySignalQuality.UNAVAILABLE
            ),
            signal_sources=gpu_sources + ram_sources,
            pressure_reasons=_free_ratio_pressure_reasons("vram", vram_pressure)
            + ram_reasons,
            observed_at=_now_iso(),
            error=error,
        )

    def sample_process_vram(self, pids: Iterable[int]) -> GpuProcessMemorySample:
        requested = sorted({pid for pid in pids if pid >= 0})
        if not requested:
            return GpuProcessMemorySample(
                requested_pids=[],
                attribution_sources=["windows_gpu_process_counters"],
                error="no_process_pids",
            )
        try:
            result = self._command_runner(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    _WINDOWS_GPU_PROCESS_MEMORY_SCRIPT,
                ]
            )
        except FileNotFoundError:
            return _unavailable_gpu_process_sample(requested, "powershell_not_found")
        except subprocess.TimeoutExpired:
            return _unavailable_gpu_process_sample(
                requested, "windows_gpu_process_observer_timeout"
            )
        except OSError as exc:
            return _unavailable_gpu_process_sample(
                requested, f"windows_gpu_process_observer_error:{exc}"
            )

        if result.returncode != 0:
            error = (
                result.stderr or result.stdout or "windows_gpu_process_observer_failed"
            ).strip()
            return _unavailable_gpu_process_sample(requested, error)

        usages, error = _parse_windows_gpu_process_memory_json(result.stdout)
        if error is not None:
            return _unavailable_gpu_process_sample(requested, error)
        matched = [usage for usage in usages if usage.pid in requested]
        if not matched:
            return GpuProcessMemorySample(
                requested_pids=requested,
                attribution_sources=["windows_gpu_process_counters"],
                attribution_reasons=["windows_gpu_process_memory_no_matching_pid"],
                error="windows_gpu_process_memory_no_matching_pid",
            )
        return GpuProcessMemorySample(
            available=True,
            requested_pids=requested,
            matched_pids=sorted({usage.pid for usage in matched}),
            process_tree_vram_mb=sum(usage.used_vram_mb for usage in matched),
            attribution_quality=MemoryAttributionQuality.PROCESS_EXACT,
            attribution_sources=["windows_gpu_process_counters"],
            attribution_reasons=["windows_gpu_process_memory_matched_runner_pid"],
        )

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=self._timeout_seconds,
        )


class FallbackMemoryObserver:
    """Use a precise backend observer when available, otherwise fall back to RAM."""

    def __init__(
        self, primary: MachineMemoryObserver, fallback: MachineMemoryObserver
    ) -> None:
        self.primary = primary
        self.fallback = fallback

    def snapshot(self) -> MachineMemorySnapshot:
        primary_snapshot = self.primary.snapshot()
        if primary_snapshot.available:
            return primary_snapshot
        fallback_snapshot = self.fallback.snapshot()
        if fallback_snapshot.available:
            return fallback_snapshot
        return primary_snapshot

    def sample_process_vram(self, pids: Iterable[int]) -> GpuProcessMemorySample:
        primary_sampler = getattr(self.primary, "sample_process_vram", None)
        if primary_sampler is not None:
            primary_sample = primary_sampler(pids)
            if primary_sample.available:
                return primary_sample
        fallback_sampler = getattr(self.fallback, "sample_process_vram", None)
        if fallback_sampler is not None:
            fallback_sample = fallback_sampler(pids)
            if fallback_sample.available:
                return fallback_sample
            return fallback_sample
        if primary_sampler is not None:
            return primary_sample
        return GpuProcessMemorySample(
            requested_pids=sorted({pid for pid in pids if pid >= 0}),
            attribution_sources=["gpu_process_attribution_unavailable"],
            error="gpu_process_attribution_unavailable",
        )


class RunnerMemoryTelemetryReader:
    """Reads Noofy-owned runner-side allocator telemetry JSONL files."""

    def sample(
        self,
        telemetry_path: str | Path | None,
        *,
        runner_id: str | None = None,
        job_id: str | None = None,
        sample_window: MemorySampleWindow = MemorySampleWindow.UNKNOWN,
        observed_after: str | None = None,
    ) -> BackendAllocatorMemorySample:
        if telemetry_path is None:
            return BackendAllocatorMemorySample(
                error="runner_memory_telemetry_path_unavailable"
            )
        path = Path(telemetry_path)
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return BackendAllocatorMemorySample(
                error="runner_memory_telemetry_file_missing"
            )
        except OSError as exc:
            return BackendAllocatorMemorySample(
                error=f"runner_memory_telemetry_read_error:{exc}"
            )
        payloads: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if runner_id is not None and parsed.get("runner_id") not in {
                None,
                runner_id,
            }:
                continue
            if job_id is not None and parsed.get("job_id") not in {None, job_id}:
                continue
            if observed_after is not None:
                observed_at = parsed.get("observed_at")
                if isinstance(observed_at, str) and observed_at <= observed_after:
                    continue
            payloads.append(parsed)
        if not payloads:
            return BackendAllocatorMemorySample(
                runner_id=runner_id,
                job_id=job_id,
                sample_window=sample_window,
                error="runner_memory_telemetry_empty",
            )
        return _backend_allocator_sample_from_payloads(
            payloads,
            runner_id=runner_id,
            job_id=job_id,
            fallback_sample_window=sample_window,
        )


def default_memory_observer() -> MachineMemoryObserver:
    """Return the product default best-effort memory observer for this host."""
    if platform.system() == "Darwin":
        machine = platform.machine().lower()
        backend = (
            MemoryBackend.MPS if machine in {"arm64", "aarch64"} else MemoryBackend.CPU
        )
        return SystemMemoryObserver(backend=backend)
    if platform.system() == "Windows":
        return FallbackMemoryObserver(
            NvmlMemoryObserver(),
            FallbackMemoryObserver(
                NvidiaSmiMemoryObserver(),
                FallbackMemoryObserver(
                    WindowsGpuMemoryObserver(),
                    SystemMemoryObserver(backend=MemoryBackend.DIRECTML),
                ),
            ),
        )
    return FallbackMemoryObserver(
        NvmlMemoryObserver(),
        FallbackMemoryObserver(
            NvidiaSmiMemoryObserver(),
            SystemMemoryObserver(backend=MemoryBackend.CPU),
        ),
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
            if observation.job_id is not None:
                existing = self._observations_for_job_id(observation.job_id)
                if existing:
                    return summarize_local_memory_observations(existing)
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

    def _observations_for_job_id(self, job_id: str) -> list[LocalMemoryObservation]:
        if not self.root_dir.exists():
            return []
        for path in sorted(self.root_dir.glob("*.json")):
            observations = self._read_observations(path)
            if any(observation.job_id == job_id for observation in observations):
                return observations
        return []

    def _read_observations(self, path: Path) -> list[LocalMemoryObservation]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return [
            LocalMemoryObservation.model_validate(item)
            for item in data.get("observations", [])
        ]

    def _write_observations(
        self, path: Path, observations: list[LocalMemoryObservation]
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": MEMORY_LEARNING_SCHEMA_VERSION,
            "observations": [
                observation.model_dump(mode="json") for observation in observations
            ],
        }
        tmp_path = path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(path)


def memory_pressure_from_free_ratio(
    total_mb: int | None, free_mb: int | None
) -> MemoryPressureLevel:
    if total_mb is None or free_mb is None or total_mb <= 0:
        return MemoryPressureLevel.UNKNOWN
    ratio = free_mb / total_mb
    if ratio <= 0.10:
        return MemoryPressureLevel.HIGH
    if ratio <= 0.25:
        return MemoryPressureLevel.MEDIUM
    return MemoryPressureLevel.LOW


def _system_ram_signals(
    *,
    linux_psi_reader: Callable[[], str | None] | None = None,
) -> tuple[int | None, int | None, MemoryPressureLevel, list[str], list[str]]:
    total_ram_mb, free_ram_mb = _system_ram_mb()
    ram_pressure = memory_pressure_from_free_ratio(total_ram_mb, free_ram_mb)
    sources = (
        ["system_ram"] if total_ram_mb is not None or free_ram_mb is not None else []
    )
    reasons = _free_ratio_pressure_reasons("ram", ram_pressure)
    psi_text = _read_linux_memory_psi(linux_psi_reader)
    if psi_text is not None:
        psi_pressure, psi_reasons = _parse_linux_psi_memory_pressure(psi_text)
        sources.append("linux_psi")
        reasons.extend(psi_reasons)
        ram_pressure = _max_pressure_level(ram_pressure, psi_pressure)
    return total_ram_mb, free_ram_mb, ram_pressure, sources, reasons


def _system_signal_quality(*, has_ram: bool, has_psi: bool) -> MemorySignalQuality:
    if has_ram:
        return MemorySignalQuality.SYSTEM_SAMPLE
    if has_psi:
        return MemorySignalQuality.SYSTEM_PRESSURE
    return MemorySignalQuality.UNAVAILABLE


def _max_pressure_level(*levels: MemoryPressureLevel) -> MemoryPressureLevel:
    present = [level for level in levels if level is not MemoryPressureLevel.UNKNOWN]
    if not present:
        return MemoryPressureLevel.UNKNOWN
    return max(present, key=_pressure_rank)


def _pressure_rank(level: MemoryPressureLevel) -> int:
    if level is MemoryPressureLevel.HIGH:
        return 3
    if level is MemoryPressureLevel.MEDIUM:
        return 2
    if level is MemoryPressureLevel.LOW:
        return 1
    return 0


def _free_ratio_pressure_reasons(scope: str, level: MemoryPressureLevel) -> list[str]:
    if level is MemoryPressureLevel.HIGH:
        return [f"{scope}_free_ratio_high"]
    if level is MemoryPressureLevel.MEDIUM:
        return [f"{scope}_free_ratio_medium"]
    return []


def conservative_memory_class(memory_class: RunnerMemoryClass) -> RunnerMemoryClass:
    """Fallback class before the Governor has enough proof to be opportunistic."""
    if memory_class in {RunnerMemoryClass.UNKNOWN, RunnerMemoryClass.GPU_MEDIUM}:
        return RunnerMemoryClass.GPU_HEAVY
    return memory_class


def estimate_evidence_rank(estimate: WorkflowMemoryEstimate) -> int:
    """Rank estimate sources for deterministic v1 confidence decisions."""
    if (
        estimate.has_local_evidence
        or estimate.source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    ):
        return 4
    if estimate.source is RunnerMemoryEstimateSource.CREATOR_OBSERVED:
        return 3
    if estimate.source is RunnerMemoryEstimateSource.DECLARED:
        return 2
    if estimate.source is RunnerMemoryEstimateSource.HEURISTIC:
        return 1
    return 0


def preferred_memory_estimate(
    estimates: list[WorkflowMemoryEstimate],
) -> WorkflowMemoryEstimate | None:
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


def build_workflow_memory_estimate(
    request: WorkflowMemoryEstimateRequest,
) -> WorkflowMemoryEstimate:
    """Build the best available v1 estimate from ordered evidence.

    Local observations are preferred when they match the current request shape,
    but they still produce confidence rather than guarantees. A changed input
    profile keeps the local source visible while lowering confidence.
    """
    estimate_reasons = [
        *_runtime_memory_option_reasons(request),
        *_model_selection_reasons(request),
        *_custom_node_memory_reasons(request),
    ]
    estimate_fields = _estimate_request_fields(request)
    local = request.local_evidence
    if local is not None and local.has_estimate_evidence:
        settings_match = _local_evidence_matches_request(request, local)
        confidence = RunnerMemoryEstimateConfidence.LOW
        reasons = ["local_memory_evidence", *estimate_reasons]
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
            memory_class=_estimate_memory_class(
                request.declared_memory_class, local.observed_peak_vram_mb
            ),
            confidence=confidence,
            source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            estimated_peak_vram_mb=local.observed_peak_vram_mb,
            estimated_peak_ram_mb=local.observed_peak_ram_mb,
            creator_observed_peak_vram_mb=request.creator_observed_peak_vram_mb,
            creator_observed_peak_ram_mb=request.creator_observed_peak_ram_mb,
            local_evidence=local,
            recent_memory_error=local.has_memory_failure,
            **estimate_fields,
            reasons=reasons,
        )

    if (
        request.creator_observed_peak_vram_mb is not None
        or request.creator_observed_peak_ram_mb is not None
    ):
        return WorkflowMemoryEstimate(
            workflow_id=request.workflow_id,
            runner_process_compatibility_key=request.runner_process_compatibility_key,
            memory_class=_estimate_memory_class(
                request.declared_memory_class, request.creator_observed_peak_vram_mb
            ),
            confidence=_non_local_confidence_after_custom_node_uncertainty(
                RunnerMemoryEstimateConfidence.MEDIUM,
                request,
            ),
            source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            estimated_peak_vram_mb=request.creator_observed_peak_vram_mb,
            estimated_peak_ram_mb=request.creator_observed_peak_ram_mb,
            creator_observed_peak_vram_mb=request.creator_observed_peak_vram_mb,
            creator_observed_peak_ram_mb=request.creator_observed_peak_ram_mb,
            **estimate_fields,
            reasons=["creator_observed_memory_hint", *estimate_reasons],
        )

    if (
        request.declared_peak_vram_mb is not None
        or request.declared_peak_ram_mb is not None
    ):
        return WorkflowMemoryEstimate(
            workflow_id=request.workflow_id,
            runner_process_compatibility_key=request.runner_process_compatibility_key,
            memory_class=_estimate_memory_class(
                request.declared_memory_class, request.declared_peak_vram_mb
            ),
            confidence=_non_local_confidence_after_custom_node_uncertainty(
                RunnerMemoryEstimateConfidence.MEDIUM,
                request,
            ),
            source=RunnerMemoryEstimateSource.DECLARED,
            estimated_peak_vram_mb=request.declared_peak_vram_mb,
            estimated_peak_ram_mb=request.declared_peak_ram_mb,
            **estimate_fields,
            reasons=["declared_memory_requirement", *estimate_reasons],
        )

    heuristic_peak_vram_mb = _heuristic_peak_vram_mb(request)
    if heuristic_peak_vram_mb is not None:
        return WorkflowMemoryEstimate(
            workflow_id=request.workflow_id,
            runner_process_compatibility_key=request.runner_process_compatibility_key,
            memory_class=_estimate_memory_class(
                request.declared_memory_class, heuristic_peak_vram_mb
            ),
            confidence=RunnerMemoryEstimateConfidence.LOW,
            source=RunnerMemoryEstimateSource.HEURISTIC,
            estimated_peak_vram_mb=heuristic_peak_vram_mb,
            **estimate_fields,
            reasons=["model_and_input_heuristic", *estimate_reasons],
        )

    return WorkflowMemoryEstimate(
        workflow_id=request.workflow_id,
        runner_process_compatibility_key=request.runner_process_compatibility_key,
        memory_class=conservative_memory_class(request.declared_memory_class),
        confidence=RunnerMemoryEstimateConfidence.UNKNOWN,
        source=RunnerMemoryEstimateSource.UNKNOWN,
        **estimate_fields,
        reasons=["no_memory_estimate_available", *estimate_reasons],
    )


def summarize_local_memory_observations(
    observations: list[LocalMemoryObservation],
) -> LocalMemoryEvidenceSummary:
    if not observations:
        raise ValueError("Cannot summarize an empty local memory observation set")
    first = observations[0]
    successful_runs = sum(
        1
        for observation in observations
        if observation.outcome is MemoryObservationOutcome.SUCCESS
    )
    memory_error_runs = sum(
        1
        for observation in observations
        if observation.outcome is MemoryObservationOutcome.MEMORY_ERROR
    )
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
        observation
        for observation in observations
        if observation.outcome is MemoryObservationOutcome.SUCCESS
    ]
    memory_error_observations = [
        observation
        for observation in observations
        if observation.outcome is MemoryObservationOutcome.MEMORY_ERROR
    ]
    return LocalMemoryEvidenceSummary(
        workflow_id=first.workflow_id,
        runner_process_compatibility_key=first.runner_process_compatibility_key,
        machine_profile_id=first.machine_profile_id,
        backend=first.backend,
        input_profile_fingerprint=first.input_profile_fingerprint,
        process_compatibility_signature=first.process_compatibility_signature,
        model_residency_signature=first.model_residency_signature,
        execution_profile_signature=first.execution_profile_signature,
        successful_runs=successful_runs,
        memory_error_runs=memory_error_runs,
        other_failed_runs=other_failed_runs,
        evictions_required=sum(
            1 for observation in observations if observation.eviction_required
        ),
        retries_required=sum(
            1 for observation in observations if observation.retry_required
        ),
        observed_peak_vram_mb=_max_optional(
            observation.peak_vram_mb for observation in observations
        ),
        observed_peak_ram_mb=_max_optional(
            observation.peak_ram_mb for observation in observations
        ),
        process_tree_observed_peak_vram_mb=_max_optional(
            observation.process_tree_peak_vram_mb for observation in observations
        ),
        process_tree_observed_peak_ram_mb=_max_optional(
            observation.process_tree_peak_ram_mb for observation in observations
        ),
        system_observed_peak_delta_vram_mb=_max_optional(
            observation.system_peak_delta_vram_mb for observation in observations
        ),
        system_observed_peak_delta_ram_mb=_max_optional(
            observation.system_peak_delta_ram_mb for observation in observations
        ),
        backend_allocator_observed_peak_vram_mb=_max_optional(
            observation.backend_allocator_peak_vram_mb for observation in observations
        ),
        attribution_quality=_best_attribution_quality(
            observation.attribution_quality for observation in observations
        ),
        attribution_sources=_unique_preserving_order(
            source
            for observation in observations
            for source in observation.attribution_sources
        ),
        attribution_reasons=_unique_preserving_order(
            reason
            for observation in observations
            for reason in observation.attribution_reasons
        ),
        last_success_at=_latest_observed_at(successful_observations),
        last_memory_error_at=_latest_observed_at(memory_error_observations),
    )


def decide_memory_admission(request: MemoryAdmissionRequest) -> MemoryGovernorDecision:
    """Decide whether the requested workflow can start with current residents.

    This v1 policy is deterministic, pressure-aware, and intentionally reluctant
    to block: active jobs are queued, idle runners are evicted when useful, and
    weak evidence becomes a cautious start unless stronger evidence or repeated
    cleanup failure justifies refusal.
    """
    estimate = request.workflow_estimate
    machine = request.machine_snapshot
    selected_runner = request.selected_runner
    runners = list(request.resident_runners)
    if (
        selected_runner is not None
        and selected_runner.runner_id not in {runner.runner_id for runner in runners}
        and _runner_snapshot_may_hold_reclaimable_memory(selected_runner)
    ):
        runners.append(selected_runner)
    active_runners = [
        runner for runner in runners if _runner_snapshot_is_active(runner)
    ]
    idle_runners = [
        runner for runner in runners if not _runner_snapshot_is_active(runner)
    ]
    required_vram_margin_mb = required_vram_margin(machine, estimate)
    required_ram_margin_mb = required_ram_margin(machine, estimate)
    predicted_free_vram_after_mb = _subtract_optional(
        machine.free_vram_mb,
        _estimated_vram_pressure_mb(machine, estimate),
    )
    predicted_free_ram_after_mb = _subtract_optional(
        machine.free_ram_mb,
        _estimated_ram_pressure_mb(machine, estimate),
    )
    runner_ids = [runner.runner_id for runner in runners]

    if (
        not active_runners
        and _selected_runner_reuse_allowed(selected_runner, estimate)
        and _same_runner_reuse_incremental_margin_ok(
            machine,
            selected_runner,
            estimate,
            required_vram_margin_mb,
            required_ram_margin_mb,
        )
    ):
        execution_profile_changed = (
            selected_runner is not None
            and selected_runner.execution_profile_signature is not None
            and estimate.execution_profile_signature is not None
            and selected_runner.execution_profile_signature
            != estimate.execution_profile_signature
        )
        return _decision(
            MemoryDecisionAction.REUSE_RUNNER,
            _warm_runner_reuse_risk_level(machine, estimate),
            "same_runner_model_residency_reuse",
            "This workflow is ready to run quickly.",
            estimate,
            machine,
            runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            selected_runner=selected_runner,
            developer_details={
                "warm_runner_reuse": True,
                "same_runner_model_residency_reuse": True,
                "execution_profile_changed": execution_profile_changed,
                "selected_runner_last_workflow_id": selected_runner.last_workflow_id
                if selected_runner is not None
                else None,
                "selected_runner_model_residency_signature": (
                    selected_runner.model_residency_signature
                    if selected_runner is not None
                    else None
                ),
                "selected_runner_execution_profile_signature": (
                    selected_runner.execution_profile_signature
                    if selected_runner is not None
                    else None
                ),
                "same_runner_incremental_estimated_vram_mb": _same_runner_incremental_vram_mb(
                    machine,
                    selected_runner,
                    estimate,
                ),
                "same_runner_incremental_estimated_ram_mb": _same_runner_incremental_ram_mb(
                    machine,
                    selected_runner,
                    estimate,
                ),
            },
        )

    comfyui_delegated_reuse = _same_runner_comfyui_managed_reuse_details(
        selected_runner,
        estimate,
        machine,
    )
    if not active_runners and comfyui_delegated_reuse is not None:
        return _decision(
            MemoryDecisionAction.REUSE_RUNNER,
            MemoryRiskLevel.MEDIUM
            if machine.memory_pressure is MemoryPressureLevel.HIGH
            else MemoryRiskLevel.LOW,
            "same_runner_comfyui_managed_model_reuse",
            "This workflow can reuse useful models already loaded in the same runner.",
            estimate,
            machine,
            runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            selected_runner=selected_runner,
            can_retry_after_cleanup=True,
            developer_details={
                "warm_runner_reuse": True,
                "same_runner_comfyui_managed_reuse": True,
                "same_runner_model_residency_reuse": False,
                "comfyui_delegated_intra_runner_model_reuse": True,
                **comfyui_delegated_reuse,
            },
        )

    if active_runners:
        active_runner = sorted(active_runners, key=lambda runner: runner.runner_id)[0]
        return _decision(
            MemoryDecisionAction.QUEUE_PENDING_MEMORY,
            MemoryRiskLevel.MEDIUM,
            "active_noofy_job_queues_run",
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
            queued_model_residency_payloads=request.queued_model_residency_payloads,
            runner_cleanup_capabilities=request.runner_cleanup_capabilities,
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
            queued_model_residency_payloads=request.queued_model_residency_payloads,
            runner_cleanup_capabilities=request.runner_cleanup_capabilities,
            reason_code="memory_pressure_high",
        )

    if _gpu_estimate_is_uncertain(estimate):
        return _uncertain_gpu_decision(
            estimate,
            machine,
            runners,
            idle_runners,
            active_runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            queued_model_residency_payloads=request.queued_model_residency_payloads,
            runner_cleanup_capabilities=request.runner_cleanup_capabilities,
            reason_code="gpu_estimate_uncertain",
        )

    # Co-residence is about whether the new workflow can run *beside* other warm
    # runners. The selected runner is the one the workflow will run on (reuse /
    # replace / same-process swap), not a peer it co-resides with, so it must be
    # excluded from the peer-compatibility check. It stays in `runners` for
    # cleanup-plan eligibility and ownership accounting.
    co_resident_runners = [
        runner
        for runner in runners
        if selected_runner is None or runner.runner_id != selected_runner.runner_id
    ]
    compatibility = _co_residence_compatibility(estimate, co_resident_runners, machine)
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
            queued_model_residency_payloads=request.queued_model_residency_payloads,
            runner_cleanup_capabilities=request.runner_cleanup_capabilities,
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
            queued_model_residency_payloads=request.queued_model_residency_payloads,
            runner_cleanup_capabilities=request.runner_cleanup_capabilities,
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
            queued_model_residency_payloads=request.queued_model_residency_payloads,
            runner_cleanup_capabilities=request.runner_cleanup_capabilities,
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


def memory_release_blocking_constraints(
    snapshot: MachineMemorySnapshot,
    *,
    required_free_vram_mb: int | None = None,
    required_free_ram_mb: int | None = None,
    require_observed_drop: bool = False,
    memory_drop_observed: bool = False,
) -> list[str]:
    """Name the requirements that were still unmet when a release check ended."""
    constraints: list[str] = []
    if snapshot.memory_pressure is MemoryPressureLevel.HIGH:
        constraints.append("memory_pressure_high")
    if required_free_vram_mb is not None and (
        snapshot.free_vram_mb is None or snapshot.free_vram_mb < required_free_vram_mb
    ):
        constraints.append("vram_below_required")
    if required_free_ram_mb is not None and (
        snapshot.free_ram_mb is None or snapshot.free_ram_mb < required_free_ram_mb
    ):
        constraints.append("ram_below_required")
    if require_observed_drop and not memory_drop_observed:
        constraints.append("no_observed_memory_drop")
    return constraints


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
    snapshot: MachineMemorySnapshot | None = None
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
                final_free_vram_mb=snapshot.free_vram_mb,
                final_free_ram_mb=snapshot.free_ram_mb,
            )
        if index < max_checks - 1:
            sleeper(interval_seconds)
    return MemoryReleaseCheckResult(
        status=MemoryReleaseStatus.TIMEOUT,
        required_free_vram_mb=required_free_vram_mb,
        required_free_ram_mb=required_free_ram_mb,
        snapshots=snapshots,
        reason_code="memory_release_timeout",
        final_free_vram_mb=snapshot.free_vram_mb if snapshot is not None else None,
        final_free_ram_mb=snapshot.free_ram_mb if snapshot is not None else None,
        blocking_constraints=memory_release_blocking_constraints(
            snapshot,
            required_free_vram_mb=required_free_vram_mb,
            required_free_ram_mb=required_free_ram_mb,
        )
        if snapshot is not None
        else [],
    )


async def wait_for_memory_release_async(
    observer: MachineMemoryObserver,
    *,
    required_free_vram_mb: int | None = None,
    required_free_ram_mb: int | None = None,
    baseline_snapshot: MachineMemorySnapshot | None = None,
    require_observed_drop: bool = False,
    confirm_on_drop_only: bool = False,
    timeout_seconds: float = 8,
    initial_poll_interval_seconds: float = 0.1,
    max_poll_interval_seconds: float = 1.0,
) -> MemoryReleaseCheckResult:
    """Adaptively poll after cleanup acknowledgment without blocking the loop.

    When ``confirm_on_drop_only`` is set, success requires only an observed
    memory drop from the baseline (proof that `/free` released something) and
    ignores absolute free-memory thresholds and pressure. This is for
    same-process core `/free`, where the reused process keeps its own RAM floor
    and could never reach ``next_workflow_peak + margin`` free RAM.
    """
    snapshots: list[MachineMemorySnapshot] = []
    timeline: list[dict[str, Any]] = [
        {
            "state": "release_requested",
            "baseline_free_vram_mb": baseline_snapshot.free_vram_mb
            if baseline_snapshot is not None
            else None,
            "baseline_free_ram_mb": baseline_snapshot.free_ram_mb
            if baseline_snapshot is not None
            else None,
            "require_observed_drop": require_observed_drop,
        }
    ]
    if require_observed_drop and (
        baseline_snapshot is None or not baseline_snapshot.available
    ):
        timeline.append(
            {
                "state": "observer_unavailable",
                "error": "memory_release_baseline_unavailable",
            }
        )
        return MemoryReleaseCheckResult(
            status=MemoryReleaseStatus.UNAVAILABLE,
            required_free_vram_mb=required_free_vram_mb,
            required_free_ram_mb=required_free_ram_mb,
            snapshots=snapshots,
            timeline=timeline,
            reason_code="memory_release_baseline_unavailable",
            baseline_free_vram_mb=baseline_snapshot.free_vram_mb
            if baseline_snapshot is not None
            else None,
            baseline_free_ram_mb=baseline_snapshot.free_ram_mb
            if baseline_snapshot is not None
            else None,
        )
    started = time.monotonic()
    interval = max(0.001, initial_poll_interval_seconds)
    previous_free_vram_mb: int | None = None
    previous_free_ram_mb: int | None = None
    while True:
        snapshot = observer.snapshot()
        snapshots.append(snapshot)
        if not snapshot.available:
            timeline.append({"state": "observer_unavailable", "error": snapshot.error})
            return MemoryReleaseCheckResult(
                status=MemoryReleaseStatus.UNAVAILABLE,
                required_free_vram_mb=required_free_vram_mb,
                required_free_ram_mb=required_free_ram_mb,
                snapshots=snapshots,
                timeline=timeline,
                reason_code="memory_snapshot_unavailable",
                baseline_free_vram_mb=baseline_snapshot.free_vram_mb
                if baseline_snapshot is not None
                else None,
                baseline_free_ram_mb=baseline_snapshot.free_ram_mb
                if baseline_snapshot is not None
                else None,
            )
        memory_drop_observed = _free_memory_increased(
            snapshot,
            baseline_snapshot=baseline_snapshot,
            previous_free_vram_mb=previous_free_vram_mb,
            previous_free_ram_mb=previous_free_ram_mb,
        )
        safe_memory_observed = memory_release_satisfied(
            snapshot,
            required_free_vram_mb=required_free_vram_mb,
            required_free_ram_mb=required_free_ram_mb,
        )
        if confirm_on_drop_only:
            release_confirmed = memory_drop_observed
        else:
            release_confirmed = safe_memory_observed and (
                not require_observed_drop or memory_drop_observed
            )
        if release_confirmed:
            timeline.append(
                {
                    "state": "observed_memory_drop"
                    if memory_drop_observed
                    else "observed_safe_memory",
                    "free_vram_mb": snapshot.free_vram_mb,
                    "free_ram_mb": snapshot.free_ram_mb,
                }
            )
            return MemoryReleaseCheckResult(
                status=MemoryReleaseStatus.RELEASED,
                required_free_vram_mb=required_free_vram_mb,
                required_free_ram_mb=required_free_ram_mb,
                snapshots=snapshots,
                timeline=timeline,
                reason_code="memory_released",
                baseline_free_vram_mb=baseline_snapshot.free_vram_mb
                if baseline_snapshot is not None
                else None,
                baseline_free_ram_mb=baseline_snapshot.free_ram_mb
                if baseline_snapshot is not None
                else None,
                final_free_vram_mb=snapshot.free_vram_mb,
                final_free_ram_mb=snapshot.free_ram_mb,
            )
        elapsed = time.monotonic() - started
        if elapsed >= max(0, timeout_seconds):
            blocking_constraints = memory_release_blocking_constraints(
                snapshot,
                required_free_vram_mb=required_free_vram_mb
                if not confirm_on_drop_only
                else None,
                required_free_ram_mb=required_free_ram_mb
                if not confirm_on_drop_only
                else None,
                require_observed_drop=require_observed_drop or confirm_on_drop_only,
                memory_drop_observed=memory_drop_observed,
            )
            cleanup_observed_but_insufficient = (
                require_observed_drop and memory_drop_observed
            )
            timeline.append(
                {
                    "state": (
                        "released_insufficient_memory"
                        if cleanup_observed_but_insufficient
                        else "timeout"
                    ),
                    "free_vram_mb": snapshot.free_vram_mb,
                    "free_ram_mb": snapshot.free_ram_mb,
                    "blocking_constraints": blocking_constraints,
                }
            )
            return MemoryReleaseCheckResult(
                status=(
                    MemoryReleaseStatus.RELEASED_INSUFFICIENT_MEMORY
                    if cleanup_observed_but_insufficient
                    else MemoryReleaseStatus.TIMEOUT
                ),
                required_free_vram_mb=required_free_vram_mb,
                required_free_ram_mb=required_free_ram_mb,
                snapshots=snapshots,
                timeline=timeline,
                reason_code=(
                    "memory_released_insufficient_memory"
                    if cleanup_observed_but_insufficient
                    else "memory_release_timeout"
                ),
                baseline_free_vram_mb=baseline_snapshot.free_vram_mb
                if baseline_snapshot is not None
                else None,
                baseline_free_ram_mb=baseline_snapshot.free_ram_mb
                if baseline_snapshot is not None
                else None,
                final_free_vram_mb=snapshot.free_vram_mb,
                final_free_ram_mb=snapshot.free_ram_mb,
                blocking_constraints=blocking_constraints,
            )
        timeline.append(
            {
                "state": "release_pending",
                "free_vram_mb": snapshot.free_vram_mb,
                "free_ram_mb": snapshot.free_ram_mb,
            }
        )
        timeline.append(
            {
                "state": "partial_release"
                if memory_drop_observed
                else "still_reserved_or_allocated",
                "free_vram_mb": snapshot.free_vram_mb,
                "free_ram_mb": snapshot.free_ram_mb,
            }
        )
        previous_free_vram_mb = snapshot.free_vram_mb
        previous_free_ram_mb = snapshot.free_ram_mb
        await asyncio.sleep(min(interval, max(0, timeout_seconds - elapsed)))
        interval = min(max_poll_interval_seconds, interval * 2)


def _free_memory_increased(
    snapshot: MachineMemorySnapshot,
    *,
    baseline_snapshot: MachineMemorySnapshot | None,
    previous_free_vram_mb: int | None,
    previous_free_ram_mb: int | None,
) -> bool:
    """True when free VRAM or free RAM rose above its reference value.

    VRAM and RAM are judged independently and either increase counts: a core
    `/free` may release RAM-cached models without moving VRAM (idle GPU), or
    release VRAM while the Python allocator retains process RSS. The existence
    of VRAM stats must never prevent RAM from being checked. Each metric
    compares against the pre-cleanup baseline when that baseline value exists,
    otherwise against the previous poll.
    """
    vram_reference = (
        baseline_snapshot.free_vram_mb
        if baseline_snapshot is not None and baseline_snapshot.free_vram_mb is not None
        else previous_free_vram_mb
    )
    ram_reference = (
        baseline_snapshot.free_ram_mb
        if baseline_snapshot is not None and baseline_snapshot.free_ram_mb is not None
        else previous_free_ram_mb
    )
    vram_increased = (
        snapshot.free_vram_mb is not None
        and vram_reference is not None
        and snapshot.free_vram_mb > vram_reference
    )
    ram_increased = (
        snapshot.free_ram_mb is not None
        and ram_reference is not None
        and snapshot.free_ram_mb > ram_reference
    )
    return vram_increased or ram_increased


def memory_release_satisfied(
    snapshot: MachineMemorySnapshot,
    *,
    required_free_vram_mb: int | None = None,
    required_free_ram_mb: int | None = None,
) -> bool:
    if snapshot.memory_pressure is MemoryPressureLevel.HIGH:
        return False
    if required_free_vram_mb is not None:
        if (
            snapshot.free_vram_mb is None
            or snapshot.free_vram_mb < required_free_vram_mb
        ):
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


def memory_requirement_for_decision(decision: MemoryGovernorDecision) -> dict[str, Any]:
    estimate = decision.workflow_estimate
    machine = decision.machine_snapshot
    unified_memory = machine is not None and machine.backend in {MemoryBackend.MPS, MemoryBackend.CPU}
    required_vram_mb = (
        (estimate.estimated_peak_vram_mb or 0) + (decision.required_vram_margin_mb or 0)
        if not unified_memory and estimate is not None and estimate.estimated_peak_vram_mb is not None
        else None
    )
    estimated_ram_mb = (
        estimate.estimated_peak_ram_mb
        if estimate is not None and estimate.estimated_peak_ram_mb is not None
        else estimate.estimated_peak_vram_mb if unified_memory and estimate is not None else None
    )
    required_ram_mb = (
        estimated_ram_mb + (decision.required_ram_margin_mb or 0)
        if estimated_ram_mb is not None
        else None
    )
    return _memory_requirement_payload(
        required_vram_mb=required_vram_mb,
        total_vram_mb=machine.total_vram_mb if machine is not None else None,
        available_vram_mb=machine.free_vram_mb if machine is not None else None,
        required_ram_mb=required_ram_mb,
        total_ram_mb=machine.total_ram_mb if machine is not None else None,
        available_ram_mb=machine.free_ram_mb if machine is not None else None,
        source="memory_governor_decision",
        confidence=decision.confidence.value,
    )


def memory_requirement_from_error(message: str | None) -> dict[str, Any] | None:
    if not likely_memory_error(message):
        return None
    allocated_mb = _parse_labeled_memory_mb(message, "currently allocated")
    requested_mb = _parse_labeled_memory_mb(message, "requested")
    device_limit_mb = _parse_labeled_memory_mb(message, "device limit")
    free_vram_mb = _parse_labeled_memory_mb(message, "free (according to cuda)")
    required_vram_mb = (
        allocated_mb + requested_mb
        if allocated_mb is not None and requested_mb is not None
        else None
    )
    return _memory_requirement_payload(
        required_vram_mb=required_vram_mb,
        total_vram_mb=device_limit_mb,
        available_vram_mb=free_vram_mb,
        required_ram_mb=None,
        total_ram_mb=None,
        available_ram_mb=None,
        source="runtime_oom",
        confidence="high" if required_vram_mb is not None and device_limit_mb is not None else "unknown",
    )


def _memory_requirement_payload(
    *,
    required_vram_mb: int | None,
    total_vram_mb: int | None,
    available_vram_mb: int | None,
    required_ram_mb: int | None,
    total_ram_mb: int | None,
    available_ram_mb: int | None,
    source: str,
    confidence: str,
) -> dict[str, Any]:
    capacity_checks = [
        required > total
        for required, total in (
            (required_vram_mb, total_vram_mb),
            (required_ram_mb, total_ram_mb),
        )
        if required is not None and total is not None
    ]
    free_shortfall_checks = [
        required > available
        for required, available in (
            (required_vram_mb, available_vram_mb),
            (required_ram_mb, available_ram_mb),
        )
        if required is not None and available is not None
    ]
    capacity_exceeded = any(capacity_checks) if capacity_checks else None
    freeing_memory_may_help = (
        not capacity_exceeded and any(free_shortfall_checks)
        if capacity_exceeded is not None and free_shortfall_checks
        else None
    )
    return {
        "required_vram_mb": required_vram_mb,
        "total_vram_mb": total_vram_mb,
        "available_vram_mb": available_vram_mb,
        "required_ram_mb": required_ram_mb,
        "total_ram_mb": total_ram_mb,
        "available_ram_mb": available_ram_mb,
        "capacity_exceeded": capacity_exceeded,
        "freeing_memory_may_help": freeing_memory_may_help,
        "source": source,
        "confidence": confidence,
    }


def _parse_labeled_memory_mb(message: str | None, label: str) -> int | None:
    if not message:
        return None
    match = re.search(
        rf"{re.escape(label)}\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*([kmgt]i?b)",
        message,
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    multipliers = {
        "kb": 1 / 1000,
        "kib": 1 / 1024,
        "mb": 1,
        "mib": 1,
        "gb": 1000,
        "gib": 1024,
        "tb": 1_000_000,
        "tib": 1024 * 1024,
    }
    return round(value * multipliers[unit])


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
            _subtract_optional(
                machine_snapshot.free_vram_mb, workflow_estimate.estimated_peak_vram_mb
            ),
            _subtract_optional(
                machine_snapshot.free_ram_mb, workflow_estimate.estimated_peak_ram_mb
            ),
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
            _subtract_optional(
                machine_snapshot.free_vram_mb, workflow_estimate.estimated_peak_vram_mb
            ),
            _subtract_optional(
                machine_snapshot.free_ram_mb, workflow_estimate.estimated_peak_ram_mb
            ),
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
        _subtract_optional(
            machine_snapshot.free_vram_mb, workflow_estimate.estimated_peak_vram_mb
        ),
        _subtract_optional(
            machine_snapshot.free_ram_mb, workflow_estimate.estimated_peak_ram_mb
        ),
        can_retry_after_cleanup=True,
    )


def record_memory_governor_decision(
    log_store: DiagnosticsSink,
    decision: MemoryGovernorDecision,
    *,
    level: str = "info",
) -> Any:
    """Persist a decision in the existing diagnostic event stream."""
    message = (
        decision.user_message
        or decision.reason_summary
        or f"Memory Governor decision: {decision.action}"
    )
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
        if decision.risk_level is MemoryRiskLevel.HIGH:
            return MemoryUserStatus(
                state="memory_warning",
                message=decision.user_message
                or "Noofy will try this workflow and watch memory closely.",
                risk_level=decision.risk_level,
                queue_id=queue_id,
                can_retry_after_cleanup=decision.can_retry_after_cleanup,
            )
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
        cleanup_state = _evict_then_start_state(decision)
        return MemoryUserStatus(
            state=cleanup_state,
            message=(
                "Noofy is unloading a previous workflow before starting this one."
                if cleanup_state == "unloading_previous_workflow"
                else "Noofy is freeing memory before starting this workflow."
            ),
            risk_level=decision.risk_level,
            queue_id=queue_id,
            can_retry_after_cleanup=decision.can_retry_after_cleanup,
        )
    if decision.action in {
        MemoryDecisionAction.QUEUE_PENDING_MEMORY,
        MemoryDecisionAction.QUEUE_PENDING_SWITCH,
    }:
        return MemoryUserStatus(
            state="waiting_for_active_workflow",
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
            state=_blocked_memory_state(decision),
            message=decision.user_message
            or "This workflow needs more memory than Noofy can safely use right now.",
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


def _evict_then_start_state(decision: MemoryGovernorDecision) -> str:
    evict_runner_ids = set(decision.evict_runner_ids)
    for runner in decision.runner_snapshots:
        if runner.runner_id in evict_runner_ids and runner.kind is RunnerKind.ISOLATED_COMFYUI:
            return "unloading_previous_workflow"
    return "freeing_previous_models"


def _blocked_memory_state(decision: MemoryGovernorDecision) -> str:
    estimate = decision.workflow_estimate
    machine = decision.machine_snapshot
    if estimate is not None and machine is not None:
        required_vram_mb = (estimate.estimated_peak_vram_mb or 0) + (decision.required_vram_margin_mb or 0)
        required_ram_mb = (estimate.estimated_peak_ram_mb or 0) + (decision.required_ram_margin_mb or 0)
        if (
            machine.total_vram_mb is not None
            and required_vram_mb > machine.total_vram_mb
        ) or (
            machine.total_ram_mb is not None
            and required_ram_mb > machine.total_ram_mb
        ):
            return "blocked_exceeds_capacity"
        if any(reason.startswith("external_process") for reason in machine.pressure_reasons):
            return "blocked_external_pressure"
    return "blocked_unattributed_pressure"


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
    if (
        request.input_profile_fingerprint is None
        or local_evidence.input_profile_fingerprint is None
    ):
        return True
    return request.input_profile_fingerprint == local_evidence.input_profile_fingerprint


def required_vram_margin(
    machine: MachineMemorySnapshot, estimate: WorkflowMemoryEstimate
) -> int:
    if _request_is_cpu_only(estimate):
        return 0
    if _accelerator_memory_uses_system_ram(machine):
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


def required_ram_margin(
    machine: MachineMemorySnapshot, estimate: WorkflowMemoryEstimate
) -> int:
    del estimate
    if machine.backend is MemoryBackend.MPS:
        if machine.total_ram_mb is None:
            return 4096
        return min(8192, int(machine.total_ram_mb * 0.25))
    if machine.total_ram_mb is None:
        return 2048
    return max(2048, int(machine.total_ram_mb * 0.10))


def eviction_candidates(
    runners: list[RunnerMemorySnapshot],
    *,
    workflow_estimate: WorkflowMemoryEstimate | None = None,
    queued_model_residency_payloads: list[dict[str, Any]] | None = None,
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]] | None = None,
) -> list[RunnerMemorySnapshot]:
    ranked = _ranked_cleanup_candidates(
        runners,
        workflow_estimate=workflow_estimate,
        queued_model_residency_payloads=queued_model_residency_payloads or [],
        runner_cleanup_capabilities=runner_cleanup_capabilities or {},
    )
    return [candidate.runner for candidate in ranked if candidate.cleanup_supported]


@dataclass(frozen=True)
class _CleanupCandidate:
    runner: RunnerMemorySnapshot
    cleanup_mode: MemoryCleanupMode
    cleanup_supported: bool
    reuse_score: int
    useful_overlap_score: int
    reasons: tuple[str, ...]
    skipped_reason: str | None = None
    reclaimable_vram_mb: int | None = None
    reclaimable_ram_mb: int | None = None
    reload_cost_estimate_mb: int | None = None
    obsolete_lora_count: int = 0

    def diagnostic_details(self, *, selected: bool) -> dict[str, Any]:
        return {
            "runner_id": self.runner.runner_id,
            "runner_kind": self.runner.kind.value,
            "runner_status": self.runner.status.value,
            "selected_for_cleanup": selected,
            "cleanup_mode": self.cleanup_mode.value,
            "cleanup_supported": self.cleanup_supported,
            "skipped_reason": self.skipped_reason,
            "reuse_score": self.reuse_score,
            "useful_overlap_score": self.useful_overlap_score,
            "reasons": list(self.reasons),
            "open_workflow_lease_count": self.runner.open_workflow_lease_count,
            "queued_demand": "queued_demand" in self.reasons,
            "recent_use": any(reason.startswith("recent_use") for reason in self.reasons),
            "last_used_at": self.runner.last_used_at,
            "reload_cost_estimate_mb": self.reload_cost_estimate_mb,
            "reclaimable_vram_mb": self.reclaimable_vram_mb,
            "reclaimable_ram_mb": self.reclaimable_ram_mb,
            "obsolete_lora_count": self.obsolete_lora_count,
            "model_residency_signature": self.runner.model_residency_signature,
        }


@dataclass(frozen=True)
class _ModelResidencySets:
    models_by_kind: dict[str, set[str]]
    active_loras: set[str]

    @property
    def empty(self) -> bool:
        return not self.models_by_kind and not self.active_loras


def _ranked_cleanup_candidates(
    runners: list[RunnerMemorySnapshot],
    *,
    workflow_estimate: WorkflowMemoryEstimate | None,
    queued_model_residency_payloads: list[dict[str, Any]],
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]],
) -> list[_CleanupCandidate]:
    candidates = [
        _cleanup_candidate(
            runner,
            workflow_estimate=workflow_estimate,
            queued_model_residency_payloads=queued_model_residency_payloads,
            runner_cleanup_capabilities=runner_cleanup_capabilities,
        )
        for runner in runners
    ]
    return sorted(
        candidates,
        key=lambda candidate: (
            not candidate.cleanup_supported,
            candidate.reuse_score,
            candidate.runner.open_workflow_lease_count,
            candidate.reload_cost_estimate_mb or 0,
            -candidate.obsolete_lora_count,
            -(candidate.reclaimable_vram_mb or 0),
            candidate.runner.last_used_at or "",
            candidate.runner.runner_id,
        ),
    )


def _cleanup_candidate(
    runner: RunnerMemorySnapshot,
    *,
    workflow_estimate: WorkflowMemoryEstimate | None,
    queued_model_residency_payloads: list[dict[str, Any]],
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]],
) -> _CleanupCandidate:
    reclaimable_vram_mb = _runner_resident_vram_mb(runner)
    reclaimable_ram_mb = _runner_resident_ram_mb(runner)
    reload_cost_estimate_mb = max(
        reclaimable_vram_mb or 0,
        reclaimable_ram_mb or 0,
    ) or None
    if _runner_snapshot_is_active(runner):
        return _CleanupCandidate(
            runner=runner,
            cleanup_mode=MemoryCleanupMode.CLEANUP_UNSUPPORTED,
            cleanup_supported=False,
            reuse_score=10_000,
            useful_overlap_score=0,
            reasons=("protected_runner_state",),
            skipped_reason="active_or_reserved_runner",
            reclaimable_vram_mb=reclaimable_vram_mb,
            reclaimable_ram_mb=reclaimable_ram_mb,
            reload_cost_estimate_mb=reload_cost_estimate_mb,
        )

    supported_modes = _cleanup_modes_for_runner(runner, runner_cleanup_capabilities)
    cleanup_mode = _runner_cleanup_mode(runner, supported_modes)
    cleanup_supported = cleanup_mode is not MemoryCleanupMode.CLEANUP_UNSUPPORTED
    score, overlap_score, reasons, obsolete_lora_count = _runner_reuse_score(
        runner,
        workflow_estimate=workflow_estimate,
        queued_model_residency_payloads=queued_model_residency_payloads,
        precise_lora_cleanup_supported=MemoryCleanupMode.PER_LORA_UNLOAD
        in supported_modes,
    )
    if not cleanup_supported:
        reasons.append("cleanup_unsupported")
    return _CleanupCandidate(
        runner=runner,
        cleanup_mode=cleanup_mode,
        cleanup_supported=cleanup_supported,
        reuse_score=score,
        useful_overlap_score=overlap_score,
        reasons=tuple(reasons),
        skipped_reason=None if cleanup_supported else "cleanup_unsupported",
        reclaimable_vram_mb=reclaimable_vram_mb,
        reclaimable_ram_mb=reclaimable_ram_mb,
        reload_cost_estimate_mb=reload_cost_estimate_mb,
        obsolete_lora_count=obsolete_lora_count,
    )


def _cleanup_modes_for_runner(
    runner: RunnerMemorySnapshot,
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]],
) -> set[MemoryCleanupMode]:
    configured = runner_cleanup_capabilities.get(runner.runner_id)
    if configured is not None:
        return set(configured)
    if runner.kind is RunnerKind.CORE_COMFYUI:
        return {MemoryCleanupMode.RUNNER_FREE}
    if runner.kind is RunnerKind.ISOLATED_COMFYUI:
        return {MemoryCleanupMode.ISOLATED_EVICTION}
    return set()


def _runner_cleanup_mode(
    runner: RunnerMemorySnapshot,
    supported_modes: set[MemoryCleanupMode],
) -> MemoryCleanupMode:
    if MemoryCleanupMode.PER_LORA_UNLOAD in supported_modes:
        return MemoryCleanupMode.PER_LORA_UNLOAD
    if MemoryCleanupMode.PER_MODEL_UNLOAD in supported_modes:
        return MemoryCleanupMode.PER_MODEL_UNLOAD
    if (
        runner.kind is RunnerKind.CORE_COMFYUI
        and MemoryCleanupMode.RUNNER_FREE in supported_modes
    ):
        return MemoryCleanupMode.RUNNER_FREE
    if (
        runner.kind is RunnerKind.ISOLATED_COMFYUI
        and MemoryCleanupMode.ISOLATED_EVICTION in supported_modes
    ):
        return MemoryCleanupMode.ISOLATED_EVICTION
    return MemoryCleanupMode.CLEANUP_UNSUPPORTED


def _runner_reuse_score(
    runner: RunnerMemorySnapshot,
    *,
    workflow_estimate: WorkflowMemoryEstimate | None,
    queued_model_residency_payloads: list[dict[str, Any]],
    precise_lora_cleanup_supported: bool,
) -> tuple[int, int, list[str], int]:
    score = 0
    useful_overlap_score = 0
    reasons: list[str] = []
    runner_sets = _model_residency_sets(runner.model_residency_payload)
    requested_sets = _model_residency_sets(
        workflow_estimate.model_residency_payload if workflow_estimate is not None else {}
    )
    queued_sets = [
        _model_residency_sets(payload)
        for payload in queued_model_residency_payloads
        if payload
    ]

    if (
        workflow_estimate is not None
        and runner.model_residency_signature is not None
        and runner.model_residency_signature
        == workflow_estimate.model_residency_signature
    ):
        score += 100
        useful_overlap_score += 100
        reasons.append("same_model_residency_signature")

    overlap_score, overlap_reasons = _useful_overlap_score(runner_sets, requested_sets)
    score += overlap_score
    useful_overlap_score += overlap_score
    reasons.extend(overlap_reasons)

    queued_overlap_score = 0
    for queued in queued_sets:
        partial, partial_reasons = _useful_overlap_score(runner_sets, queued)
        queued_overlap_score = max(queued_overlap_score, partial)
        if partial > 0:
            reasons.extend(
                f"queued_{reason}" for reason in partial_reasons if f"queued_{reason}" not in reasons
            )
    if queued_overlap_score > 0:
        score += min(queued_overlap_score, 60)
        reasons.append("queued_demand")

    if runner.open_workflow_lease_count > 0:
        lease_score = min(runner.open_workflow_lease_count * 20, 60)
        score += lease_score
        reasons.append("open_workflow_lease")

    recency_score, recency_reason = _recent_use_score(runner.last_used_at)
    if recency_score > 0:
        score += recency_score
        reasons.append(recency_reason)

    reload_cost_mb = max(_runner_resident_vram_mb(runner) or 0, _runner_resident_ram_mb(runner) or 0)
    if reload_cost_mb >= 8_000:
        score += 24
        reasons.append("high_reload_cost_estimate")
    elif reload_cost_mb >= 3_000:
        score += 12
        reasons.append("medium_reload_cost_estimate")
    elif reload_cost_mb > 0:
        score += 4
        reasons.append("low_reload_cost_estimate")

    needed_loras = set(requested_sets.active_loras)
    for queued in queued_sets:
        needed_loras.update(queued.active_loras)
    obsolete_loras = runner_sets.active_loras - needed_loras
    obsolete_lora_count = len(obsolete_loras)
    if obsolete_lora_count > 0 and runner.open_workflow_lease_count == 0:
        if precise_lora_cleanup_supported:
            reasons.append("obsolete_lora_precise_cleanup_candidate")
            score -= min(obsolete_lora_count * 16, 48)
        else:
            reasons.append("obsolete_lora_pressure_signal")
            reasons.append("per_lora_cleanup_unsupported")
            score -= min(obsolete_lora_count * 6, 18)

    if not reasons:
        reasons.append("no_known_reuse_value")

    return max(0, score), useful_overlap_score, reasons, obsolete_lora_count


def _useful_overlap_score(
    runner_sets: _ModelResidencySets,
    requested_sets: _ModelResidencySets,
) -> tuple[int, list[str]]:
    if runner_sets.empty or requested_sets.empty:
        return 0, []
    weights = {
        "checkpoint": 45,
        "model": 45,
        "refiner": 30,
        "vae": 18,
        "encoder": 18,
        "controlnet": 22,
        "ipadapter": 20,
    }
    score = 0
    reasons: list[str] = []
    for kind, requested_values in requested_sets.models_by_kind.items():
        overlap = runner_sets.models_by_kind.get(kind, set()) & requested_values
        if not overlap:
            continue
        score += weights.get(kind, 10) * len(overlap)
        reasons.append(f"same_{kind}")
    same_loras = runner_sets.active_loras & requested_sets.active_loras
    if same_loras:
        score += min(8 * len(same_loras), 32)
        reasons.append("same_lora_set" if same_loras == requested_sets.active_loras else "same_lora")
    return score, reasons


def _model_residency_sets(payload: dict[str, Any] | None) -> _ModelResidencySets:
    models_by_kind: dict[str, set[str]] = {}
    active_loras: set[str] = set()
    if not isinstance(payload, dict):
        return _ModelResidencySets(models_by_kind={}, active_loras=set())
    for item in payload.get("selected_models") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        selection = str(item.get("selection") or "").strip()
        if not kind or not selection:
            continue
        models_by_kind.setdefault(kind, set()).add(selection)
    for item in payload.get("selected_loras") or []:
        if not isinstance(item, dict):
            continue
        if item.get("active") is False:
            continue
        selection = str(item.get("selection") or "").strip()
        if selection:
            active_loras.add(selection)
    return _ModelResidencySets(models_by_kind=models_by_kind, active_loras=active_loras)


def _recent_use_score(last_used_at: str | None) -> tuple[int, str]:
    if not last_used_at:
        return 0, "not_recently_used"
    try:
        parsed = datetime.fromisoformat(last_used_at)
    except ValueError:
        return 1, "recent_use_unknown_age"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    age_seconds = max(0, (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds())
    if age_seconds <= 5 * 60:
        return 16, "recent_use_under_5m"
    if age_seconds <= 30 * 60:
        return 8, "recent_use_under_30m"
    if age_seconds <= 2 * 60 * 60:
        return 3, "recent_use_under_2h"
    return 0, "not_recently_used"


def _cleanup_plan(
    estimate: WorkflowMemoryEstimate,
    machine: MachineMemorySnapshot,
    idle_runners: list[RunnerMemorySnapshot],
    required_vram_margin_mb: int | None,
    required_ram_margin_mb: int | None,
    *,
    queued_model_residency_payloads: list[dict[str, Any]],
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]],
) -> dict[str, Any]:
    ranked = _ranked_cleanup_candidates(
        idle_runners,
        workflow_estimate=estimate,
        queued_model_residency_payloads=queued_model_residency_payloads,
        runner_cleanup_capabilities=runner_cleanup_capabilities,
    )
    needed_vram_mb, needed_ram_mb = _cleanup_shortfall_mb(
        estimate,
        machine,
        required_vram_margin_mb,
        required_ram_margin_mb,
    )
    selected: list[_CleanupCandidate] = []
    reclaimed_vram_mb = 0
    reclaimed_ram_mb = 0
    for candidate in ranked:
        if not candidate.cleanup_supported:
            continue
        if _cleanup_shortfall_satisfied(
            needed_vram_mb,
            needed_ram_mb,
            reclaimed_vram_mb,
            reclaimed_ram_mb,
        ) and selected:
            break
        selected.append(candidate)
        reclaimed_vram_mb += candidate.reclaimable_vram_mb or 0
        reclaimed_ram_mb += candidate.reclaimable_ram_mb or 0

    selected_runner_ids = [candidate.runner.runner_id for candidate in selected]
    candidate_details = [
        candidate.diagnostic_details(
            selected=candidate.runner.runner_id in selected_runner_ids
        )
        for candidate in ranked
    ]
    selected_modes = sorted({candidate.cleanup_mode.value for candidate in selected})
    precise_unsupported = any(
        "per_lora_cleanup_unsupported" in candidate.reasons
        or "cleanup_unsupported" in candidate.reasons
        for candidate in ranked
    )
    return {
        "requested_model_residency_signature": estimate.model_residency_signature,
        "requested_model_residency_payload": estimate.model_residency_payload,
        "queued_model_residency_payload_count": len(queued_model_residency_payloads),
        "required_cleanup_vram_mb": needed_vram_mb,
        "required_cleanup_ram_mb": needed_ram_mb,
        "selected_runner_ids": selected_runner_ids,
        "kept_warm_runner_ids": [
            candidate.runner.runner_id
            for candidate in ranked
            if candidate.runner.runner_id not in selected_runner_ids
            and candidate.cleanup_supported
        ],
        "selected_cleanup_modes": selected_modes,
        "candidate_scores": candidate_details,
        "precise_cleanup": {
            "per_lora_unload": "unsupported_by_current_adapter"
            if precise_unsupported
            else "not_needed",
            "per_model_unload": "future_adapter_capability",
        },
    }


def _cleanup_shortfall_mb(
    estimate: WorkflowMemoryEstimate,
    machine: MachineMemorySnapshot,
    required_vram_margin_mb: int | None,
    required_ram_margin_mb: int | None,
) -> tuple[int | None, int | None]:
    needed_vram_mb: int | None = None
    if not _accelerator_memory_uses_system_ram(machine):
        estimated_vram_mb = estimate.estimated_peak_vram_mb
        if machine.free_vram_mb is not None and estimated_vram_mb is not None:
            needed_vram_mb = max(
                0,
                estimated_vram_mb
                + (required_vram_margin_mb or 0)
                - machine.free_vram_mb,
            )
    estimated_ram_mb = _estimated_ram_pressure_mb(machine, estimate)
    needed_ram_mb: int | None = None
    if machine.free_ram_mb is not None and estimated_ram_mb is not None:
        needed_ram_mb = max(
            0,
            estimated_ram_mb + (required_ram_margin_mb or 0) - machine.free_ram_mb,
        )
    return needed_vram_mb, needed_ram_mb


def _cleanup_shortfall_satisfied(
    needed_vram_mb: int | None,
    needed_ram_mb: int | None,
    reclaimed_vram_mb: int,
    reclaimed_ram_mb: int,
) -> bool:
    if needed_vram_mb is not None and reclaimed_vram_mb < needed_vram_mb:
        return False
    if needed_ram_mb is not None and reclaimed_ram_mb < needed_ram_mb:
        return False
    return True


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
    selected_runner: RunnerMemorySnapshot | None = None,
    evict_runner_ids: list[str] | None = None,
    queued_behind_runner_id: str | None = None,
    can_retry_after_cleanup: bool = False,
    developer_details: dict[str, Any] | None = None,
) -> MemoryGovernorDecision:
    details = {
        "memory_ownership": _memory_ownership_details(
            machine,
            runners,
            workflow_estimate=estimate,
            selected_runner=selected_runner,
        ),
        **(developer_details or {}),
    }
    return MemoryGovernorDecision(
        action=action,
        risk_level=risk_level,
        confidence=estimate.confidence,
        reason_code=reason_code,
        workflow_id=estimate.workflow_id,
        selected_runner_id=selected_runner.runner_id if selected_runner is not None else None,
        evict_runner_ids=evict_runner_ids or [],
        queued_behind_runner_id=queued_behind_runner_id,
        machine_snapshot=machine,
        workflow_estimate=estimate,
        runner_snapshots=runners,
        required_vram_margin_mb=required_vram_margin_mb,
        required_ram_margin_mb=required_ram_margin_mb,
        predicted_free_vram_after_mb=predicted_free_vram_after_mb,
        predicted_free_ram_after_mb=predicted_free_ram_after_mb,
        signal_quality=machine.signal_quality,
        signal_sources=machine.signal_sources,
        pressure_reasons=machine.pressure_reasons,
        can_retry_after_cleanup=can_retry_after_cleanup,
        user_message=user_message,
        developer_details=details,
    )


def _estimate_capacity_block_is_trusted(estimate: WorkflowMemoryEstimate) -> bool:
    """Whether the estimate is strong enough to refuse a run on capacity grounds.

    Only direct, this-machine evidence is trusted for that: a local observation
    from a prior run on this machine, or a declared peak measured from a real
    execution here. Creator/export-time observations and heuristic guesses
    describe other machines or are inferred, so their magnitude is advisory and
    must not by itself produce a hard block.
    """
    return estimate.effective_source in {
        RunnerMemoryEstimateSource.LOCAL_OBSERVED,
        RunnerMemoryEstimateSource.DECLARED,
    }


def _estimate_peak_exceeds_total(
    estimate: WorkflowMemoryEstimate, machine: MachineMemorySnapshot
) -> bool:
    """Whether the estimated peak ALONE exceeds physical total memory.

    The safety margin is a policy buffer, not proof, so it is deliberately not
    added here: only a peak that cannot physically fit counts as "cannot fit".
    """
    vram_pressure_mb = _estimated_vram_pressure_mb(machine, estimate)
    if (
        vram_pressure_mb is not None
        and machine.total_vram_mb is not None
        and vram_pressure_mb > machine.total_vram_mb
    ):
        return True
    ram_pressure_mb = _estimated_ram_pressure_mb(machine, estimate)
    if (
        ram_pressure_mb is not None
        and machine.total_ram_mb is not None
        and ram_pressure_mb > machine.total_ram_mb
    ):
        return True
    return False


def _shortfall_requires_hard_block(
    estimate: WorkflowMemoryEstimate,
    machine: MachineMemorySnapshot,
) -> bool:
    """Decide whether a margin/pressure shortfall still warrants a hard block.

    Reached only on the single-runner path where there is no active runner to
    queue behind and no idle runner to reclaim. In that situation a creator/
    export observation or a heuristic estimate is advisory, not proof, so we
    refuse to run only for cases backed by evidence we trust for this machine:

    * a recorded local memory failure for this profile on this machine,
    * positive evidence that another process is actively holding the memory we
      would need (attributed external pressure), or
    * strong local/direct evidence that the workflow cannot physically fit
      (the estimated peak alone exceeds total memory).

    A free-margin shortfall against an advisory estimate — including the policy
    case of ``peak + margin > total`` — is not a block: Noofy cautious-starts so
    ComfyUI can try, and a real failure then becomes local evidence that
    strengthens future decisions.
    """
    if estimate.recent_memory_error:
        return True
    if any(
        reason.startswith("external_process") for reason in machine.pressure_reasons
    ):
        return True
    if _estimate_capacity_block_is_trusted(estimate) and _estimate_peak_exceeds_total(
        estimate, machine
    ):
        return True
    return False


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
    queued_model_residency_payloads: list[dict[str, Any]],
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]],
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

    cleanup_plan = _cleanup_plan(
        estimate,
        machine,
        idle_runners,
        required_vram_margin_mb,
        required_ram_margin_mb,
        queued_model_residency_payloads=queued_model_residency_payloads,
        runner_cleanup_capabilities=runner_cleanup_capabilities,
    )
    if cleanup_plan["selected_runner_ids"]:
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
            evict_runner_ids=cleanup_plan["selected_runner_ids"],
            can_retry_after_cleanup=True,
            developer_details={"residency_pressure": cleanup_plan},
        )

    # No active runner to queue behind and no idle runner to reclaim. With no
    # other warm runner to protect, a non-local estimate must not hard-block on
    # a free-margin shortfall alone: cautious-start and let ComfyUI try, unless
    # the run would be a guaranteed OOM, a known external process owns the
    # memory, or this machine already failed this profile for memory.
    if (
        not active_runners
        and not idle_runners
        and not _shortfall_requires_hard_block(estimate, machine)
    ):
        return _decision(
            MemoryDecisionAction.START_CO_RESIDENT,
            risk_level,
            f"{reason_code}_cautious_start",
            "Noofy will try this workflow and watch memory closely.",
            estimate,
            machine,
            runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            developer_details={
                "advisory_estimate_allowed_to_run": True,
                "shortfall_reason_code": reason_code,
                "estimate_source": estimate.effective_source.value,
                "estimate_confidence": estimate.confidence.value,
                "residency_pressure": cleanup_plan,
            },
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
        developer_details={"residency_pressure": cleanup_plan},
    )


def _uncertain_gpu_decision(
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
    queued_model_residency_payloads: list[dict[str, Any]],
    runner_cleanup_capabilities: dict[str, list[MemoryCleanupMode]],
    reason_code: str,
) -> MemoryGovernorDecision:
    if active_runners:
        active_runner = sorted(active_runners, key=lambda runner: runner.runner_id)[0]
        return _decision(
            MemoryDecisionAction.QUEUE_PENDING_MEMORY,
            MemoryRiskLevel.HIGH,
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

    cleanup_plan = _cleanup_plan(
        estimate,
        machine,
        idle_runners,
        required_vram_margin_mb,
        required_ram_margin_mb,
        queued_model_residency_payloads=queued_model_residency_payloads,
        runner_cleanup_capabilities=runner_cleanup_capabilities,
    )
    if cleanup_plan["selected_runner_ids"]:
        return _decision(
            MemoryDecisionAction.EVICT_THEN_START,
            MemoryRiskLevel.HIGH,
            reason_code,
            "Noofy is freeing memory before starting this workflow.",
            estimate,
            machine,
            runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            evict_runner_ids=cleanup_plan["selected_runner_ids"],
            can_retry_after_cleanup=True,
            developer_details={"residency_pressure": cleanup_plan},
        )

    if estimate.recent_memory_error:
        return _decision(
            MemoryDecisionAction.BLOCKED_BY_MEMORY,
            MemoryRiskLevel.HIGH,
            "recent_memory_error_requires_cleanup_before_retry",
            "This workflow needed more memory before, and there is no extra memory cleanup Noofy can do right now.",
            estimate,
            machine,
            runners,
            required_vram_margin_mb,
            required_ram_margin_mb,
            predicted_free_vram_after_mb,
            predicted_free_ram_after_mb,
            developer_details={"residency_pressure": cleanup_plan},
        )

    return _decision(
        MemoryDecisionAction.START_CO_RESIDENT,
        MemoryRiskLevel.HIGH,
        f"{reason_code}_cautious_start",
        "Noofy will try this workflow and watch memory closely.",
        estimate,
        machine,
        runners,
        required_vram_margin_mb,
        required_ram_margin_mb,
        predicted_free_vram_after_mb,
        predicted_free_ram_after_mb,
        developer_details={"uncertain_estimate_allowed_to_run": True},
    )


def _request_is_cpu_only(estimate: WorkflowMemoryEstimate) -> bool:
    return estimate.memory_class is RunnerMemoryClass.CPU_ONLY


def _runner_snapshot_is_active(runner: RunnerMemorySnapshot) -> bool:
    return (
        runner.current_job_id is not None
        or runner.output_stream_lease_count > 0
        or runner.reservation_token_present
        or runner.status
        in {
            RunnerStatus.RUNNING,
            RunnerStatus.LOADING_MODEL,
            RunnerStatus.RETRYING_AFTER_MEMORY_CLEANUP,
            RunnerStatus.RESERVING,
            RunnerStatus.SUBMITTING,
            RunnerStatus.EVICTING_RUNNER,
            RunnerStatus.WAITING_FOR_MEMORY_RELEASE,
            RunnerStatus.STOPPING,
        }
    )


def _runner_snapshot_may_hold_reclaimable_memory(runner: RunnerMemorySnapshot) -> bool:
    if _runner_snapshot_is_active(runner):
        return True
    return (
        runner.last_workflow_id is not None
        or runner.model_residency_signature is not None
        or bool(runner.model_residency_payload)
        or _runner_resident_vram_mb(runner) is not None
        or _runner_resident_ram_mb(runner) is not None
    )


def _selected_runner_reuse_allowed(
    runner: RunnerMemorySnapshot | None,
    estimate: WorkflowMemoryEstimate,
) -> bool:
    if runner is None or _runner_snapshot_is_active(runner):
        return False
    if runner.status not in {
        RunnerStatus.READY,
        RunnerStatus.IDLE,
        RunnerStatus.IDLE_WARM,
        RunnerStatus.CO_RESIDENT,
    }:
        return False
    if not _same_runner_model_residency_matches(runner, estimate):
        return False
    if estimate.local_evidence is not None:
        if estimate.local_evidence.memory_error_runs > 0:
            return False
        if estimate.local_evidence.successful_runs > 0:
            return True
    return _runner_resident_vram_mb(runner) is not None


def _same_runner_comfyui_managed_reuse_details(
    runner: RunnerMemorySnapshot | None,
    estimate: WorkflowMemoryEstimate,
    machine: MachineMemorySnapshot,
) -> dict[str, Any] | None:
    if runner is None or _runner_snapshot_is_active(runner):
        return None
    if runner.status not in {
        RunnerStatus.READY,
        RunnerStatus.IDLE,
        RunnerStatus.IDLE_WARM,
        RunnerStatus.CO_RESIDENT,
    }:
        return None
    if estimate.recent_memory_error:
        return None
    if not runner.model_residency_payload or not estimate.model_residency_payload:
        return None
    if _same_runner_model_residency_matches(runner, estimate):
        return None
    runner_sets = _model_residency_sets(runner.model_residency_payload)
    requested_sets = _model_residency_sets(estimate.model_residency_payload)
    overlap_score, overlap_reasons = _useful_overlap_score(
        runner_sets,
        requested_sets,
    )
    model_overlap_reasons = [
        reason
        for reason in overlap_reasons
        if reason not in {"same_lora", "same_lora_set"}
    ]
    if not model_overlap_reasons:
        return None
    capacity_reason = _same_runner_delegated_reuse_capacity_blocker(
        runner,
        estimate,
        machine,
    )
    if capacity_reason is not None:
        return None
    return {
        "useful_overlap_score": overlap_score,
        "useful_overlap_reasons": overlap_reasons,
        "model_overlap_reasons": model_overlap_reasons,
        "runner_model_residency_signature": runner.model_residency_signature,
        "requested_model_residency_signature": estimate.model_residency_signature,
        "runner_model_residency_payload": runner.model_residency_payload,
        "requested_model_residency_payload": estimate.model_residency_payload,
        "runner_resident_vram_mb": _runner_resident_vram_mb(runner),
        "runner_resident_ram_mb": _runner_resident_ram_mb(runner),
        "custom_node_count": estimate.custom_node_count,
        "custom_node_types": list(estimate.custom_node_types),
        "custom_node_memory_uncertain": estimate.custom_node_count > 0,
        "capacity_blocker": capacity_reason,
        "boundary": (
            "Noofy manages runner-level pressure; same-runner model and LoRA "
            "reuse is delegated to ComfyUI's prompt cache and model manager."
        ),
    }


def _same_runner_delegated_reuse_capacity_blocker(
    runner: RunnerMemorySnapshot,
    estimate: WorkflowMemoryEstimate,
    machine: MachineMemorySnapshot,
) -> str | None:
    resident_vram_mb = _runner_resident_vram_mb(runner) or 0
    if (
        machine.total_vram_mb is not None
        and estimate.estimated_peak_vram_mb is not None
        and estimate.estimated_peak_vram_mb
        > machine.total_vram_mb + resident_vram_mb
    ):
        return "estimated_vram_exceeds_total_plus_resident_overlap"
    resident_ram_mb = _runner_resident_ram_mb(runner) or 0
    if (
        machine.total_ram_mb is not None
        and estimate.estimated_peak_ram_mb is not None
        and estimate.estimated_peak_ram_mb
        > machine.total_ram_mb + resident_ram_mb
    ):
        return "estimated_ram_exceeds_total_plus_resident_overlap"
    return None


def _same_runner_model_residency_matches(
    runner: RunnerMemorySnapshot,
    estimate: WorkflowMemoryEstimate,
) -> bool:
    if (
        runner.model_residency_signature is not None
        and estimate.model_residency_signature is not None
    ):
        return runner.model_residency_signature == estimate.model_residency_signature
    if (
        runner.model_residency_signature is not None
        or estimate.model_residency_signature is not None
    ):
        return False
    return runner.last_workflow_id == estimate.workflow_id


def _same_runner_reuse_incremental_margin_ok(
    machine: MachineMemorySnapshot,
    runner: RunnerMemorySnapshot | None,
    estimate: WorkflowMemoryEstimate,
    required_vram_margin_mb: int,
    required_ram_margin_mb: int,
) -> bool:
    incremental_vram_mb = _same_runner_incremental_vram_mb(machine, runner, estimate)
    if (
        incremental_vram_mb is not None
        and incremental_vram_mb > 0
        and machine.free_vram_mb is not None
        and machine.free_vram_mb - incremental_vram_mb < required_vram_margin_mb
    ):
        return False
    incremental_ram_mb = _same_runner_incremental_ram_mb(machine, runner, estimate)
    if (
        incremental_ram_mb is not None
        and incremental_ram_mb > 0
        and machine.free_ram_mb is not None
        and machine.free_ram_mb - incremental_ram_mb < required_ram_margin_mb
    ):
        return False
    return True


def _same_runner_incremental_vram_mb(
    machine: MachineMemorySnapshot,
    runner: RunnerMemorySnapshot | None,
    estimate: WorkflowMemoryEstimate,
) -> int | None:
    estimated_vram_mb = _estimated_vram_pressure_mb(machine, estimate)
    if runner is None or estimated_vram_mb is None:
        return estimated_vram_mb
    resident_vram_mb = _runner_resident_vram_mb(runner)
    if resident_vram_mb is None:
        return estimated_vram_mb
    return max(0, estimated_vram_mb - resident_vram_mb)


def _same_runner_incremental_ram_mb(
    machine: MachineMemorySnapshot,
    runner: RunnerMemorySnapshot | None,
    estimate: WorkflowMemoryEstimate,
) -> int | None:
    estimated_ram_mb = _estimated_ram_pressure_mb(machine, estimate)
    if runner is None or estimated_ram_mb is None:
        return estimated_ram_mb
    resident_ram_mb = _runner_resident_ram_mb(runner)
    if resident_ram_mb is None:
        return estimated_ram_mb
    return max(0, estimated_ram_mb - resident_ram_mb)


def _warm_runner_reuse_risk_level(
    machine: MachineMemorySnapshot,
    estimate: WorkflowMemoryEstimate,
) -> MemoryRiskLevel:
    if machine.memory_pressure is MemoryPressureLevel.HIGH:
        return MemoryRiskLevel.HIGH
    return MemoryRiskLevel.LOW


def _memory_ownership_details(
    machine: MachineMemorySnapshot,
    runners: list[RunnerMemorySnapshot],
    *,
    workflow_estimate: WorkflowMemoryEstimate,
    selected_runner: RunnerMemorySnapshot | None,
) -> dict[str, Any]:
    known_runners = {runner.runner_id: runner for runner in runners}
    if selected_runner is not None:
        known_runners.setdefault(selected_runner.runner_id, selected_runner)
    active_runner_ids = sorted(
        runner.runner_id
        for runner in known_runners.values()
        if _runner_snapshot_is_active(runner)
    )
    reclaimable_idle_runner_ids = sorted(
        runner.runner_id
        for runner in runners
        if not _runner_snapshot_is_active(runner)
    )
    known_noofy_runner_vram_mb = sum(
        _runner_resident_vram_mb(runner) or 0
        for runner in known_runners.values()
    )
    unattributed_or_external_used_vram_mb = None
    if machine.total_vram_mb is not None and machine.free_vram_mb is not None:
        unattributed_or_external_used_vram_mb = max(
            0,
            machine.total_vram_mb
            - machine.free_vram_mb
            - known_noofy_runner_vram_mb,
        )
    same_warm_runner = (
        selected_runner
        if selected_runner is not None
        and not _runner_snapshot_is_active(selected_runner)
        and _same_runner_model_residency_matches(selected_runner, workflow_estimate)
        else None
    )
    return {
        "free_vram_mb": machine.free_vram_mb,
        "same_warm_runner_id": same_warm_runner.runner_id if same_warm_runner is not None else None,
        "same_warm_runner_vram_mb": _runner_resident_vram_mb(same_warm_runner)
        if same_warm_runner is not None
        else None,
        "same_warm_runner_model_residency_signature": (
            same_warm_runner.model_residency_signature
            if same_warm_runner is not None
            else None
        ),
        "same_warm_runner_execution_profile_signature": (
            same_warm_runner.execution_profile_signature
            if same_warm_runner is not None
            else None
        ),
        "reclaimable_idle_runner_ids": reclaimable_idle_runner_ids,
        "active_noofy_runner_ids": active_runner_ids,
        "known_noofy_runner_vram_mb": known_noofy_runner_vram_mb,
        "unattributed_or_external_used_vram_mb": unattributed_or_external_used_vram_mb,
    }


def _runner_resident_vram_mb(runner: RunnerMemorySnapshot) -> int | None:
    return runner.observed_idle_vram_mb or runner.observed_execution_peak_vram_mb


def _runner_resident_ram_mb(runner: RunnerMemorySnapshot) -> int | None:
    return runner.observed_idle_ram_mb or runner.observed_execution_peak_ram_mb


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
        if (
            requested is RunnerMemoryClass.GPU_HEAVY
            and existing is RunnerMemoryClass.GPU_HEAVY
        ):
            if _large_gpu_high_confidence_heavy_pair_allowed(estimate, runner, machine):
                continue
            return (
                "heavy_heavy_requires_large_gpu_and_high_confidence",
                MemoryRiskLevel.HIGH,
            )
        if (
            requested is RunnerMemoryClass.GPU_HEAVY
            and existing is RunnerMemoryClass.GPU_MEDIUM
        ):
            return "heavy_medium_requires_eviction_in_v1", MemoryRiskLevel.MEDIUM
        if (
            requested is RunnerMemoryClass.GPU_MEDIUM
            and existing is RunnerMemoryClass.GPU_HEAVY
        ):
            return "heavy_medium_requires_eviction_in_v1", MemoryRiskLevel.MEDIUM
        if (
            requested is RunnerMemoryClass.GPU_MEDIUM
            and existing is RunnerMemoryClass.GPU_MEDIUM
        ):
            if (
                estimate.confidence is RunnerMemoryEstimateConfidence.HIGH
                and runner.memory_estimate_confidence
                is RunnerMemoryEstimateConfidence.HIGH
            ):
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
    if _accelerator_memory_uses_system_ram(machine):
        return True
    estimated_vram_mb = _estimated_vram_pressure_mb(machine, estimate)
    if machine.free_vram_mb is None or estimated_vram_mb is None:
        return False
    return machine.free_vram_mb - estimated_vram_mb >= required_vram_margin_mb


def _ram_margin_ok(
    machine: MachineMemorySnapshot,
    estimate: WorkflowMemoryEstimate,
    required_ram_margin_mb: int,
) -> bool:
    estimated_ram_mb = _estimated_ram_pressure_mb(machine, estimate)
    if machine.free_ram_mb is None or estimated_ram_mb is None:
        return True
    return machine.free_ram_mb - estimated_ram_mb >= required_ram_margin_mb


def _accelerator_memory_uses_system_ram(machine: MachineMemorySnapshot) -> bool:
    return machine.backend in {MemoryBackend.MPS, MemoryBackend.CPU}


def _estimated_vram_pressure_mb(
    machine: MachineMemorySnapshot, estimate: WorkflowMemoryEstimate
) -> int | None:
    if _accelerator_memory_uses_system_ram(machine):
        return None
    return estimate.estimated_peak_vram_mb


def _estimated_ram_pressure_mb(
    machine: MachineMemorySnapshot, estimate: WorkflowMemoryEstimate
) -> int | None:
    if estimate.estimated_peak_ram_mb is not None:
        return estimate.estimated_peak_ram_mb
    if _accelerator_memory_uses_system_ram(machine):
        return estimate.estimated_peak_vram_mb
    return None


def _co_residence_risk_level(
    estimate: WorkflowMemoryEstimate,
    runners: list[RunnerMemorySnapshot],
) -> MemoryRiskLevel:
    if not runners or estimate.memory_class in {
        RunnerMemoryClass.CPU_ONLY,
        RunnerMemoryClass.GPU_LIGHT,
    }:
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
        megapixels = (
            request.resolution_width * request.resolution_height * request.batch_size
        ) / 1_000_000
        components.append(int(megapixels * 900))
    if (
        request.required_model_size_mb is None
        and (request.selected_model_count > 0 or request.lora_count > 0)
    ):
        # Selection names do not reveal exact file sizes. This is only a
        # conservative coarse floor until package metadata or local learning
        # provides stronger evidence.
        components.append(
            768
            + max(0, request.selected_model_count - 1) * 192
            + request.lora_count * 64
        )
    if not components:
        return None
    return max(
        int(
            sum(components)
            * _workflow_type_heuristic_factor(request.workflow_type)
            * _runtime_precision_heuristic_factor(request.precision)
            * _runtime_vram_mode_heuristic_factor(request.vram_mode)
            * _model_selection_heuristic_factor(request)
            * _custom_node_heuristic_factor(request)
        ),
        512,
    )


def _estimate_request_fields(request: WorkflowMemoryEstimateRequest) -> dict[str, Any]:
    return {
        "custom_node_count": request.custom_node_count,
        "custom_node_types": list(request.custom_node_types),
        "selected_model_count": request.selected_model_count,
        "selected_model_kinds": list(request.selected_model_kinds),
        "lora_count": request.lora_count,
        "lora_strength_total": request.lora_strength_total,
        "process_compatibility_signature": request.process_compatibility_signature,
        "model_residency_signature": request.model_residency_signature,
        "model_residency_payload": dict(request.model_residency_payload),
        "execution_profile_signature": request.execution_profile_signature,
        "precision": request.precision,
        "vram_mode": request.vram_mode,
    }


def _runtime_memory_option_reasons(request: WorkflowMemoryEstimateRequest) -> list[str]:
    reasons: list[str] = []
    if request.precision is not None:
        reasons.append("precision_memory_option")
    if request.vram_mode is not None:
        reasons.append("vram_mode_memory_option")
    return reasons


def _custom_node_memory_reasons(request: WorkflowMemoryEstimateRequest) -> list[str]:
    if request.custom_node_count <= 0:
        return []
    return ["custom_node_memory_uncertain"]


def _model_selection_reasons(request: WorkflowMemoryEstimateRequest) -> list[str]:
    reasons: list[str] = []
    if request.selected_model_count > 0:
        reasons.append("selected_model_memory_heuristic")
    if request.lora_count > 0:
        reasons.append("lora_memory_heuristic")
    return reasons


def _non_local_confidence_after_custom_node_uncertainty(
    confidence: RunnerMemoryEstimateConfidence,
    request: WorkflowMemoryEstimateRequest,
) -> RunnerMemoryEstimateConfidence:
    if request.custom_node_count <= 0:
        return confidence
    if confidence is RunnerMemoryEstimateConfidence.HIGH:
        return RunnerMemoryEstimateConfidence.MEDIUM
    if confidence is RunnerMemoryEstimateConfidence.MEDIUM:
        return RunnerMemoryEstimateConfidence.LOW
    return confidence


def _custom_node_heuristic_factor(request: WorkflowMemoryEstimateRequest) -> float:
    if request.custom_node_count <= 0:
        return 1.0
    return 1.15


def _model_selection_heuristic_factor(request: WorkflowMemoryEstimateRequest) -> float:
    kinds = set(request.selected_model_kinds)
    factor = 1.0
    factor += min(max(0, request.selected_model_count - 1) * 0.025, 0.15)
    if "refiner" in kinds:
        factor += 0.12
    if "controlnet" in kinds:
        factor += 0.10
    if "ipadapter" in kinds:
        factor += 0.08
    if "encoder" in kinds:
        factor += 0.04
    if "vae" in kinds:
        factor += 0.03
    factor += min(request.lora_strength_total * 0.025, 0.15)
    return factor


def _runtime_precision_heuristic_factor(precision: str | None) -> float:
    normalized = _normalize_runtime_precision_value(precision)
    if normalized is None:
        return 1.0
    if normalized == "fp32":
        return 1.35
    if normalized in {"fp8", "int8", "int4", "quantized"}:
        return 0.9
    return 1.0


def _runtime_vram_mode_heuristic_factor(vram_mode: str | None) -> float:
    normalized = _normalize_runtime_vram_mode_value(vram_mode)
    if normalized is None:
        return 1.0
    if normalized == "highvram":
        return 1.1
    if normalized == "lowvram":
        return 0.9
    if normalized == "novram":
        return 0.65
    if normalized == "cpu":
        return 0.35
    return 1.0


def _normalize_runtime_precision_value(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return None
    if normalized in {"auto", "default"}:
        return "auto"
    if normalized in {"fp32", "float32", "float", "full", "full_precision", "no_half"}:
        return "fp32"
    if normalized in {"fp16", "float16", "half", "half_precision"}:
        return "fp16"
    if normalized in {"bf16", "bfloat16"}:
        return "bf16"
    if normalized in {"fp8", "float8"}:
        return "fp8"
    if normalized in {"int8", "8bit", "q8"}:
        return "int8"
    if normalized in {"int4", "4bit", "q4"}:
        return "int4"
    if normalized in {"quantized", "quantization"}:
        return "quantized"
    return normalized


def _normalize_runtime_vram_mode_value(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return None
    if normalized in {"auto", "default"}:
        return "auto"
    if normalized in {"normal", "normalvram", "normal_vram"}:
        return "normal"
    if normalized in {"high", "highvram", "high_vram"}:
        return "highvram"
    if normalized in {"low", "lowvram", "low_vram"}:
        return "lowvram"
    if normalized in {"none", "no", "novram", "no_vram"}:
        return "novram"
    if normalized in {"cpu", "cpu_only"}:
        return "cpu"
    return normalized


def _workflow_type_heuristic_factor(workflow_type: str | None) -> float:
    if workflow_type is None:
        return 1.0
    normalized = workflow_type.lower().replace("-", "_")
    if "controlnet" in normalized or "img2img" in normalized:
        return 1.2
    if "video" in normalized or "animate" in normalized:
        return 1.25
    if "upscale" in normalized or "post" in normalized:
        return 0.8
    return 1.0


def _max_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _best_attribution_quality(
    values: Iterable[MemoryAttributionQuality],
) -> MemoryAttributionQuality:
    present = list(values)
    if not present:
        return MemoryAttributionQuality.UNKNOWN
    return max(present, key=_attribution_quality_rank)


def _attribution_quality_rank(quality: MemoryAttributionQuality) -> int:
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


def _unique_preserving_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _latest_observed_at(observations: list[LocalMemoryObservation]) -> str | None:
    values = [
        observation.observed_at
        for observation in observations
        if observation.observed_at
    ]
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


class _CtypesNvmlApi:
    def __init__(self, *, library_path: str | None = None) -> None:
        self._library_path = library_path

    def read_memory(self) -> tuple[str | None, int | None, int | None]:
        import ctypes
        import ctypes.util

        library_path = self._library_path or ctypes.util.find_library("nvidia-ml")
        if library_path is None:
            library_path = "nvml.dll" if os.name == "nt" else "libnvidia-ml.so.1"
        try:
            library = ctypes.CDLL(library_path)
        except OSError as exc:
            raise FileNotFoundError("NVML library not found") from exc

        class NvmlMemoryInfo(ctypes.Structure):
            _fields_ = [
                ("total", ctypes.c_ulonglong),
                ("free", ctypes.c_ulonglong),
                ("used", ctypes.c_ulonglong),
            ]

        try:
            library.nvmlInit_v2.restype = ctypes.c_int
            library.nvmlShutdown.restype = ctypes.c_int
            library.nvmlDeviceGetCount_v2.argtypes = [ctypes.POINTER(ctypes.c_uint)]
            library.nvmlDeviceGetCount_v2.restype = ctypes.c_int
            library.nvmlDeviceGetHandleByIndex_v2.argtypes = [
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            library.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
            library.nvmlDeviceGetMemoryInfo.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(NvmlMemoryInfo),
            ]
            library.nvmlDeviceGetMemoryInfo.restype = ctypes.c_int
            library.nvmlDeviceGetName.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            library.nvmlDeviceGetName.restype = ctypes.c_int
        except AttributeError as exc:
            raise NvmlError("missing_symbol") from exc

        initialized = False
        try:
            _check_nvml(library.nvmlInit_v2(), "init")
            initialized = True
            count = ctypes.c_uint()
            _check_nvml(
                library.nvmlDeviceGetCount_v2(ctypes.byref(count)), "device_count"
            )
            if count.value <= 0:
                raise NvmlError("no_devices")
            handle = ctypes.c_void_p()
            _check_nvml(
                library.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)),
                "device_handle",
            )
            memory = NvmlMemoryInfo()
            _check_nvml(
                library.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)),
                "memory_info",
            )
            name_buffer = ctypes.create_string_buffer(96)
            name_result = library.nvmlDeviceGetName(
                handle, name_buffer, len(name_buffer)
            )
            device_name = (
                name_buffer.value.decode("utf-8", errors="replace")
                if name_result == 0
                else None
            )
            return (
                device_name,
                max(0, int(memory.total / (1024 * 1024))),
                max(0, int(memory.free / (1024 * 1024))),
            )
        finally:
            if initialized:
                library.nvmlShutdown()

    def read_process_memory(self) -> list[GpuProcessMemoryUsage]:
        import ctypes
        import ctypes.util

        library_path = self._library_path or ctypes.util.find_library("nvidia-ml")
        if library_path is None:
            library_path = "nvml.dll" if os.name == "nt" else "libnvidia-ml.so.1"
        try:
            library = ctypes.CDLL(library_path)
        except OSError as exc:
            raise FileNotFoundError("NVML library not found") from exc

        class NvmlProcessInfoV3(ctypes.Structure):
            _fields_ = [
                ("pid", ctypes.c_uint),
                ("usedGpuMemory", ctypes.c_ulonglong),
                ("gpuInstanceId", ctypes.c_uint),
                ("computeInstanceId", ctypes.c_uint),
            ]

        try:
            library.nvmlInit_v2.restype = ctypes.c_int
            library.nvmlShutdown.restype = ctypes.c_int
            library.nvmlDeviceGetCount_v2.argtypes = [ctypes.POINTER(ctypes.c_uint)]
            library.nvmlDeviceGetCount_v2.restype = ctypes.c_int
            library.nvmlDeviceGetHandleByIndex_v2.argtypes = [
                ctypes.c_uint,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            library.nvmlDeviceGetHandleByIndex_v2.restype = ctypes.c_int
            library.nvmlDeviceGetComputeRunningProcesses_v3.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint),
                ctypes.POINTER(NvmlProcessInfoV3),
            ]
            library.nvmlDeviceGetComputeRunningProcesses_v3.restype = ctypes.c_int
        except AttributeError as exc:
            raise NvmlError("process_memory_missing_symbol") from exc

        initialized = False
        try:
            _check_nvml(library.nvmlInit_v2(), "init")
            initialized = True
            count = ctypes.c_uint()
            _check_nvml(
                library.nvmlDeviceGetCount_v2(ctypes.byref(count)), "device_count"
            )
            if count.value <= 0:
                raise NvmlError("no_devices")
            handle = ctypes.c_void_p()
            _check_nvml(
                library.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle)),
                "device_handle",
            )
            process_count = ctypes.c_uint(0)
            first_result = library.nvmlDeviceGetComputeRunningProcesses_v3(
                handle, ctypes.byref(process_count), None
            )
            if first_result not in {0, 7}:  # 7 is NVML_ERROR_INSUFFICIENT_SIZE.
                _check_nvml(first_result, "process_memory_count")
            if process_count.value <= 0:
                return []
            infos = (NvmlProcessInfoV3 * process_count.value)()
            _check_nvml(
                library.nvmlDeviceGetComputeRunningProcesses_v3(
                    handle, ctypes.byref(process_count), infos
                ),
                "process_memory",
            )
            return [
                GpuProcessMemoryUsage(
                    pid=int(info.pid),
                    used_vram_mb=max(0, int(info.usedGpuMemory / (1024 * 1024))),
                )
                for info in list(infos)[: process_count.value]
                if info.usedGpuMemory > 0
            ]
        finally:
            if initialized:
                library.nvmlShutdown()


def _check_nvml(return_code: int, operation: str) -> None:
    if return_code != 0:
        raise NvmlError(f"{operation}_failed:{return_code}")


def _read_linux_memory_psi(
    reader: Callable[[], str | None] | None = None,
) -> str | None:
    if reader is not None:
        try:
            return reader()
        except OSError:
            return None
    if platform.system() != "Linux":
        return None
    try:
        return Path("/proc/pressure/memory").read_text(encoding="utf-8")
    except OSError:
        return None


def _parse_linux_psi_memory_pressure(
    text: str,
) -> tuple[MemoryPressureLevel, list[str]]:
    metrics: dict[str, dict[str, float]] = {}
    for raw_line in text.splitlines():
        parts = raw_line.strip().split()
        if not parts:
            continue
        scope = parts[0]
        if scope not in {"some", "full"}:
            continue
        values: dict[str, float] = {}
        for item in parts[1:]:
            key, separator, value = item.partition("=")
            if separator != "=":
                continue
            try:
                values[key] = float(value)
            except ValueError:
                continue
        metrics[scope] = values

    if not metrics:
        return MemoryPressureLevel.UNKNOWN, ["linux_psi_parse_failed"]

    full_avg10 = metrics.get("full", {}).get("avg10", 0.0)
    full_avg60 = metrics.get("full", {}).get("avg60", 0.0)
    some_avg10 = metrics.get("some", {}).get("avg10", 0.0)
    some_avg60 = metrics.get("some", {}).get("avg60", 0.0)

    if full_avg10 >= 1.0 or full_avg60 >= 0.5:
        return MemoryPressureLevel.HIGH, ["linux_psi_full_high"]
    if some_avg10 >= 10.0 or some_avg60 >= 5.0:
        return MemoryPressureLevel.HIGH, ["linux_psi_some_high"]
    if full_avg10 >= 0.1 or full_avg60 >= 0.05:
        return MemoryPressureLevel.MEDIUM, ["linux_psi_full_medium"]
    if some_avg10 >= 2.0 or some_avg60 >= 1.0:
        return MemoryPressureLevel.MEDIUM, ["linux_psi_some_medium"]
    return MemoryPressureLevel.LOW, []


def _parse_posix_process_rows(output: str) -> dict[int, tuple[int, int]]:
    rows: dict[int, tuple[int, int]] = {}
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        pid = _coerce_int(parts[0])
        ppid = _coerce_int(parts[1])
        rss_kb = _coerce_int(parts[2])
        if pid is None or ppid is None or rss_kb is None:
            continue
        rows[pid] = (ppid, rss_kb)
    return rows


def _parse_windows_process_rows(output: str) -> dict[int, tuple[int, int]]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return {}
    rows: dict[int, tuple[int, int]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        pid = _coerce_int(item.get("ProcessId"))
        ppid = _coerce_int(item.get("ParentProcessId"))
        working_set_bytes = _coerce_int(item.get("WorkingSetSize"))
        if pid is None or ppid is None or working_set_bytes is None:
            continue
        rows[pid] = (ppid, max(0, int(working_set_bytes / 1024)))
    return rows


def _process_tree_sample_from_rows(
    root_pid: int, rows: dict[int, tuple[int, int]]
) -> ProcessTreeMemorySample:
    if root_pid not in rows:
        return _unavailable_process_tree_sample(root_pid, "runner_process_not_found")
    children_by_parent: dict[int, list[int]] = {}
    for pid, (ppid, _rss_kb) in rows.items():
        children_by_parent.setdefault(ppid, []).append(pid)
    selected: list[int] = []
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in selected:
            continue
        selected.append(pid)
        stack.extend(children_by_parent.get(pid, []))
    rss_kb = sum(rows[pid][1] for pid in selected if pid in rows)
    return ProcessTreeMemorySample(
        available=True,
        root_pid=root_pid,
        child_pids=sorted(pid for pid in selected if pid != root_pid),
        process_tree_ram_mb=max(1, int(rss_kb / 1024)) if rss_kb > 0 else 0,
        attribution_quality=MemoryAttributionQuality.PROCESS_TREE,
        attribution_sources=["process_tree_rss"],
        attribution_reasons=["runner_process_tree_rss"],
    )


def _unavailable_process_tree_sample(
    root_pid: int | None, error: str
) -> ProcessTreeMemorySample:
    return ProcessTreeMemorySample(
        root_pid=root_pid,
        attribution_sources=["process_tree_rss"],
        attribution_reasons=[error],
        error=error,
    )


def _unavailable_gpu_process_sample(
    requested_pids: list[int], error: str
) -> GpuProcessMemorySample:
    return GpuProcessMemorySample(
        requested_pids=requested_pids,
        attribution_sources=["nvml_process"],
        attribution_reasons=[error],
        error=error,
    )


def _parse_nvidia_smi_memory_row(
    output: str,
) -> tuple[str | None, int | None, int | None]:
    line = next(
        (candidate.strip() for candidate in output.splitlines() if candidate.strip()),
        "",
    )
    if not line:
        return None, None, None
    parts = [part.strip() for part in line.split(",")]
    device_name = parts[0] if parts and parts[0] else None
    total_vram_mb = _parse_int(parts[1]) if len(parts) > 1 else None
    free_vram_mb = _parse_int(parts[2]) if len(parts) > 2 else None
    return device_name, total_vram_mb, free_vram_mb


def _parse_windows_gpu_memory_json(
    output: str,
) -> tuple[str | None, int | None, int | None, str | None]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None, None, None, "windows_gpu_observer_parse_failed"
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return None, None, None, "windows_gpu_observer_payload_invalid"

    device_name = (
        payload.get("device_name")
        if isinstance(payload.get("device_name"), str)
        else None
    )
    total_vram_mb = _coerce_memory_mb(
        payload.get("total_vram_mb"), payload.get("total_vram_bytes")
    )
    dedicated_used_mb = _coerce_memory_mb(
        payload.get("dedicated_used_mb"), payload.get("dedicated_used_bytes")
    )
    free_vram_mb = None
    if total_vram_mb is not None and dedicated_used_mb is not None:
        free_vram_mb = max(0, total_vram_mb - dedicated_used_mb)
    error = (
        None
        if total_vram_mb is not None or free_vram_mb is not None
        else "windows_gpu_observer_partial_data"
    )
    return device_name, total_vram_mb, free_vram_mb, error


def _parse_windows_gpu_process_memory_json(
    output: str,
) -> tuple[list[GpuProcessMemoryUsage], str | None]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return [], "windows_gpu_process_observer_parse_failed"
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        return [], "windows_gpu_process_observer_payload_invalid"
    by_pid: dict[int, int] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        pid = _coerce_int(item.get("pid"))
        if pid is None:
            continue
        dedicated_mb = (
            _coerce_memory_mb(
                item.get("dedicated_used_mb"), item.get("dedicated_used_bytes")
            )
            or 0
        )
        shared_mb = (
            _coerce_memory_mb(item.get("shared_used_mb"), item.get("shared_used_bytes"))
            or 0
        )
        used_mb = dedicated_mb + shared_mb
        if used_mb <= 0:
            continue
        by_pid[pid] = by_pid.get(pid, 0) + used_mb
    return [
        GpuProcessMemoryUsage(pid=pid, used_vram_mb=used_mb)
        for pid, used_mb in sorted(by_pid.items())
    ], None


def _backend_allocator_sample_from_payloads(
    payloads: list[dict[str, Any]],
    *,
    runner_id: str | None,
    job_id: str | None,
    fallback_sample_window: MemorySampleWindow,
) -> BackendAllocatorMemorySample:
    latest = payloads[-1]
    sources: list[str] = []
    reasons: list[str] = []
    details: dict[str, Any] = {}
    current_candidates: list[int] = []
    peak_candidates: list[int] = []
    budget_candidates: list[int] = []
    backend = _memory_backend_from_value(latest.get("backend"))
    has_allocator_signal = False
    has_budget_signal = False

    for payload in payloads:
        payload_sources = payload.get("signal_sources")
        if isinstance(payload_sources, list):
            sources.extend(str(source) for source in payload_sources if source)
        payload_reasons = payload.get("attribution_reasons")
        if isinstance(payload_reasons, list):
            reasons.extend(str(reason) for reason in payload_reasons if reason)
        cuda = payload.get("cuda") if isinstance(payload.get("cuda"), dict) else {}
        if cuda:
            has_allocator_signal = True
            sources.append("pytorch_cuda_allocator")
            reasons.append("runner_side_cuda_allocator_stats")
            _append_memory_candidate(
                current_candidates,
                cuda.get("reserved_current_mb"),
                cuda.get("reserved_current_bytes"),
            )
            _append_memory_candidate(
                current_candidates,
                cuda.get("allocated_current_mb"),
                cuda.get("allocated_current_bytes"),
            )
            _append_memory_candidate(
                peak_candidates,
                cuda.get("reserved_peak_mb"),
                cuda.get("reserved_peak_bytes"),
            )
            _append_memory_candidate(
                peak_candidates,
                cuda.get("allocated_peak_mb"),
                cuda.get("allocated_peak_bytes"),
            )
            details["cuda"] = _merge_dict(details.get("cuda"), cuda)
        mps = payload.get("mps") if isinstance(payload.get("mps"), dict) else {}
        if mps:
            has_allocator_signal = True
            sources.append("pytorch_mps_allocator")
            reasons.append("runner_side_mps_allocator_stats")
            _append_memory_candidate(
                current_candidates,
                mps.get("current_allocated_mb"),
                mps.get("current_allocated_bytes"),
            )
            _append_memory_candidate(
                current_candidates,
                mps.get("driver_allocated_mb"),
                mps.get("driver_allocated_bytes"),
            )
            _append_memory_candidate(
                peak_candidates,
                mps.get("driver_allocated_mb"),
                mps.get("driver_allocated_bytes"),
            )
            _append_memory_candidate(
                budget_candidates,
                mps.get("recommended_max_mb"),
                mps.get("recommended_max_bytes"),
            )
            details["mps"] = _merge_dict(details.get("mps"), mps)
        dxgi = payload.get("dxgi") if isinstance(payload.get("dxgi"), dict) else {}
        if dxgi:
            has_budget_signal = True
            sources.append("dxgi_query_video_memory_info")
            reasons.append("runner_side_dxgi_video_memory_info")
            _append_memory_candidate(
                current_candidates,
                dxgi.get("current_usage_mb"),
                dxgi.get("current_usage_bytes"),
            )
            _append_memory_candidate(
                peak_candidates,
                dxgi.get("current_usage_mb"),
                dxgi.get("current_usage_bytes"),
            )
            _append_memory_candidate(
                budget_candidates, dxgi.get("budget_mb"), dxgi.get("budget_bytes")
            )
            details["dxgi"] = _merge_dict(details.get("dxgi"), dxgi)

    current_vram_mb = max(current_candidates) if current_candidates else None
    peak_vram_mb = (
        max(peak_candidates or current_candidates)
        if peak_candidates or current_candidates
        else None
    )
    budget_vram_mb = max(budget_candidates) if budget_candidates else None
    sample_window = (
        _memory_sample_window_from_value(latest.get("sample_window"))
        or fallback_sample_window
    )
    available = (
        peak_vram_mb is not None
        or current_vram_mb is not None
        or budget_vram_mb is not None
    )
    return BackendAllocatorMemorySample(
        available=available,
        runner_id=runner_id or _string_or_none(latest.get("runner_id")),
        job_id=job_id or _string_or_none(latest.get("job_id")),
        pid=_coerce_int(latest.get("pid")),
        sample_window=sample_window,
        backend=backend,
        current_vram_mb=current_vram_mb,
        peak_vram_mb=peak_vram_mb,
        budget_vram_mb=budget_vram_mb,
        signal_quality=(
            MemorySignalQuality.ALLOCATOR
            if has_allocator_signal
            else (
                MemorySignalQuality.BACKEND_BUDGET
                if has_budget_signal
                else MemorySignalQuality.UNAVAILABLE
            )
        ),
        attribution_quality=(
            MemoryAttributionQuality.BACKEND_ALLOCATOR
            if available
            else MemoryAttributionQuality.UNAVAILABLE
        ),
        attribution_sources=_unique_preserving_order(sources),
        attribution_reasons=_unique_preserving_order(reasons),
        details=details,
        error=None if available else "runner_backend_allocator_telemetry_unavailable",
    )


def _append_memory_candidate(
    candidates: list[int], mb_value: Any, bytes_value: Any = None
) -> None:
    value = _coerce_memory_mb(mb_value, bytes_value)
    if value is not None:
        candidates.append(value)


def _memory_backend_from_value(value: Any) -> MemoryBackend:
    if isinstance(value, MemoryBackend):
        return value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return MemoryBackend(value)
    return MemoryBackend.UNKNOWN


def _memory_sample_window_from_value(value: Any) -> MemorySampleWindow | None:
    if isinstance(value, MemorySampleWindow):
        return value
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return MemorySampleWindow(value)
    return None


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _merge_dict(previous: Any, current: dict[str, Any]) -> dict[str, Any]:
    merged = dict(previous) if isinstance(previous, dict) else {}
    merged.update(current)
    return merged


def _coerce_memory_mb(mb_value: Any, bytes_value: Any = None) -> int | None:
    parsed_mb = _coerce_int(mb_value)
    if parsed_mb is not None:
        return parsed_mb
    parsed_bytes = _coerce_int(bytes_value)
    if parsed_bytes is None:
        return None
    return max(0, int(parsed_bytes / (1024 * 1024)))


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _unavailable_cuda_snapshot(error: str, *, source: str) -> MachineMemorySnapshot:
    return MachineMemorySnapshot(
        available=False,
        backend=MemoryBackend.CUDA,
        memory_pressure=MemoryPressureLevel.UNKNOWN,
        signal_quality=MemorySignalQuality.UNAVAILABLE,
        signal_sources=[source],
        observed_at=_now_iso(),
        error=error,
    )


def _unavailable_directml_snapshot(error: str) -> MachineMemorySnapshot:
    total_ram_mb, free_ram_mb, pressure, sources, reasons = _system_ram_signals()
    available = total_ram_mb is not None or free_ram_mb is not None
    return MachineMemorySnapshot(
        available=available,
        backend=MemoryBackend.DIRECTML,
        total_ram_mb=total_ram_mb,
        free_ram_mb=free_ram_mb,
        memory_pressure=pressure,
        signal_quality=(
            MemorySignalQuality.SYSTEM_SAMPLE
            if available
            else MemorySignalQuality.UNAVAILABLE
        ),
        signal_sources=sources or ["windows_gpu_counters"],
        pressure_reasons=reasons,
        observed_at=_now_iso(),
        error=error,
    )


_WINDOWS_GPU_MEMORY_SCRIPT = r"""
$controller = Get-CimInstance Win32_VideoController | Select-Object -First 1 Name, AdapterRAM
$dedicated = $null
$shared = $null
try {
  $samples = (Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage','\GPU Adapter Memory(*)\Shared Usage' -ErrorAction Stop).CounterSamples
  $dedicated = ($samples | Where-Object { $_.Path -like '*dedicated usage' } | Measure-Object -Property CookedValue -Sum).Sum
  $shared = ($samples | Where-Object { $_.Path -like '*shared usage' } | Measure-Object -Property CookedValue -Sum).Sum
} catch {}
[PSCustomObject]@{
  device_name = $controller.Name
  total_vram_bytes = $controller.AdapterRAM
  dedicated_used_bytes = $dedicated
  shared_used_bytes = $shared
} | ConvertTo-Json -Compress
"""


_WINDOWS_GPU_PROCESS_MEMORY_SCRIPT = r"""
$rows = @()
try {
  $samples = (Get-Counter '\GPU Process Memory(*)\Dedicated Usage','\GPU Process Memory(*)\Shared Usage' -ErrorAction Stop).CounterSamples
  foreach ($sample in $samples) {
    $path = [string]$sample.Path
    $pid = $null
    if ($path -match 'pid_([0-9]+)') {
      $pid = [int]$Matches[1]
    }
    if ($null -eq $pid) {
      continue
    }
    $rows += [PSCustomObject]@{
      pid = $pid
      dedicated_used_bytes = if ($path -like '*dedicated usage') { [int64]$sample.CookedValue } else { 0 }
      shared_used_bytes = if ($path -like '*shared usage') { [int64]$sample.CookedValue } else { 0 }
    }
  }
} catch {}
$rows | ConvertTo-Json -Compress
"""


_WINDOWS_PROCESS_TREE_SCRIPT = r"""
Get-CimInstance Win32_Process |
  Select-Object ProcessId, ParentProcessId, WorkingSetSize |
  ConvertTo-Json -Compress
"""


def _system_ram_mb() -> tuple[int | None, int | None]:
    return system_ram_mb()


def _linux_system_ram_mb(
    reader: Callable[[], str] | None = None,
) -> tuple[int | None, int | None]:
    return linux_system_ram_mb(reader)


def _darwin_system_ram_mb() -> tuple[int | None, int | None]:
    return darwin_system_ram_mb()


def _parse_darwin_available_memory_bytes(vm_stat_output: str) -> int | None:
    return parse_darwin_available_memory_bytes(vm_stat_output)


def _windows_system_ram_mb() -> tuple[int | None, int | None]:
    return windows_system_ram_mb()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
