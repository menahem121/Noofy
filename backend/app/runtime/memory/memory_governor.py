"""Memory Governor observers, schemas, and decision records.

The Memory Governor keeps observation, estimate, local learning, and admission
decisions separate so platform-specific memory signals can improve decisions
without turning fallback margins into the main policy engine.
"""

from __future__ import annotations

import os
import contextlib
import json
import platform
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

from app.diagnostics import DiagnosticsSink
from app.runtime.runners.supervisor import (
    RunnerDescriptor,
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
            confidence=RunnerMemoryEstimateConfidence.MEDIUM,
            source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            estimated_peak_vram_mb=request.creator_observed_peak_vram_mb,
            estimated_peak_ram_mb=request.creator_observed_peak_ram_mb,
            creator_observed_peak_vram_mb=request.creator_observed_peak_vram_mb,
            creator_observed_peak_ram_mb=request.creator_observed_peak_ram_mb,
            reasons=["creator_observed_memory_hint"],
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
            memory_class=_estimate_memory_class(
                request.declared_memory_class, heuristic_peak_vram_mb
            ),
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
    runners = list(request.resident_runners)
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
) -> list[RunnerMemorySnapshot]:
    return sorted(
        [runner for runner in runners if not _runner_snapshot_is_active(runner)],
        key=lambda runner: (
            runner.open_workflow_lease_count,
            -(
                runner.observed_idle_vram_mb
                or runner.observed_execution_peak_vram_mb
                or 0
            ),
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
        signal_quality=machine.signal_quality,
        signal_sources=machine.signal_sources,
        pressure_reasons=machine.pressure_reasons,
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

    candidates = eviction_candidates(idle_runners)
    if candidates:
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
            evict_runner_ids=[runner.runner_id for runner in candidates],
            can_retry_after_cleanup=True,
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
    if not components:
        return None
    return max(
        int(sum(components) * _workflow_type_heuristic_factor(request.workflow_type)),
        512,
    )


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
