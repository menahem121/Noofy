from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.runtime.memory.memory_governor import (
    LocalMemoryEvidenceSummary,
    LocalMemoryLearningStore,
    MachineMemoryObserver,
    MachineMemorySnapshot,
    MemoryBackend,
    MemoryPressureLevel,
    MemorySignalQuality,
    WorkflowMemoryEstimate,
    WorkflowMemoryEstimateRequest,
    build_workflow_memory_estimate,
)
from app.runtime.runners.supervisor import (
    RunnerMemoryEstimateConfidence,
    RunnerMemoryEstimateSource,
)
from app.workflows.model_grouping import total_required_model_size_bytes
from app.workflows.package import WorkflowPackage

LOCAL_MEMORY_ERROR_MAX_AGE = timedelta(days=30)


class HardwareWarningSeverity(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"


class HardwareWarningConfidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class HardwareWarningReasonCode(StrEnum):
    LOCAL_MEMORY_ERROR = "local_memory_error"
    LOCAL_MEMORY_ERROR_SETTINGS_MISMATCH = "local_memory_error_settings_mismatch"
    LOCAL_SUCCESS_SETTINGS_MISMATCH = "local_success_settings_mismatch"
    TEMPORARY_LOW_FREE_MEMORY = "temporary_low_free_memory"
    MEMORY_PRESSURE_HIGH = "memory_pressure_high"
    ESTIMATED_VRAM_SHORTFALL = "estimated_vram_shortfall"
    ESTIMATED_RAM_SHORTFALL = "estimated_ram_shortfall"
    ESTIMATED_VRAM_CAPACITY_RISK = "estimated_vram_capacity_risk"
    ESTIMATED_RAM_CAPACITY_RISK = "estimated_ram_capacity_risk"
    CREATOR_OBSERVED_MEMORY_HINT = "creator_observed_memory_hint"
    MODEL_SIZE_HEURISTIC = "model_size_heuristic"


class WorkflowHardwareWarningEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimated_peak_vram_mb: int | None = Field(default=None, ge=0)
    estimated_peak_ram_mb: int | None = Field(default=None, ge=0)
    source: RunnerMemoryEstimateSource
    confidence: RunnerMemoryEstimateConfidence | None = None


class WorkflowHardwareWarningMachineSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: MemoryBackend
    memory_pressure: MemoryPressureLevel
    total_vram_mb: int | None = Field(default=None, ge=0)
    free_vram_mb: int | None = Field(default=None, ge=0)
    total_ram_mb: int | None = Field(default=None, ge=0)
    free_ram_mb: int | None = Field(default=None, ge=0)
    signal_quality: MemorySignalQuality


class WorkflowHardwareWarningEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_successful_runs: int = Field(default=0, ge=0)
    local_memory_error_runs: int = Field(default=0, ge=0)
    local_input_profile_match: Literal["matching", "mismatched", "none"] = "none"
    creator_observation_available: bool = False
    model_size_heuristic_available: bool = False
    required_model_size_mb: int | None = Field(default=None, ge=0)


class WorkflowHardwareWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: HardwareWarningSeverity
    confidence: HardwareWarningConfidence
    exceeds_machine_capacity: bool = False
    reason_codes: list[HardwareWarningReasonCode]
    estimate: WorkflowHardwareWarningEstimate
    machine_signal: WorkflowHardwareWarningMachineSignal | None = None
    evidence: WorkflowHardwareWarningEvidence
    developer_details: dict[str, object] = Field(default_factory=dict)


def evaluate_workflow_hardware_warning(
    package: WorkflowPackage,
    *,
    memory_observer: MachineMemoryObserver | None = None,
    memory_learning_store: LocalMemoryLearningStore | None = None,
    machine_snapshot: MachineMemorySnapshot | None = None,
    local_summaries: list[LocalMemoryEvidenceSummary] | None = None,
    input_profile_fingerprint: str | None = None,
) -> WorkflowHardwareWarning | None:
    """Build a lightweight advisory hardware warning for library cards.

    This reads only package metadata, local learning summaries, and the current
    best-effort memory snapshot. It must not prepare runners, validate the
    graph, or contact ComfyUI.
    """

    if machine_snapshot is None:
        machine_snapshot = _safe_machine_snapshot(memory_observer)
    local_evidence, local_profile_match = _local_evidence(
        memory_learning_store,
        workflow_id=package.metadata.id,
        machine_snapshot=machine_snapshot,
        input_profile_fingerprint=input_profile_fingerprint,
        summaries=local_summaries,
    )
    required_model_size_mb = _required_model_size_mb_from_package(package)
    creator_peak_vram_mb = _observed_hardware_int(package, "observed_peak_vram_mb") or _observed_hardware_int(
        package, "recommended_vram_mb"
    )
    creator_peak_ram_mb = _observed_hardware_int(package, "observed_peak_ram_mb") or _observed_hardware_int(
        package, "recommended_ram_mb"
    )
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id=package.metadata.id,
            local_evidence=local_evidence if local_profile_match == "matching" else None,
            creator_observed_peak_vram_mb=creator_peak_vram_mb,
            creator_observed_peak_ram_mb=creator_peak_ram_mb,
            required_model_size_mb=required_model_size_mb,
        )
    )
    evidence = WorkflowHardwareWarningEvidence(
        local_successful_runs=local_evidence.successful_runs if local_evidence is not None else 0,
        local_memory_error_runs=local_evidence.memory_error_runs if local_evidence is not None else 0,
        local_input_profile_match=local_profile_match,
        creator_observation_available=creator_peak_vram_mb is not None or creator_peak_ram_mb is not None,
        model_size_heuristic_available=required_model_size_mb is not None,
        required_model_size_mb=required_model_size_mb,
    )
    decision = _warning_decision(
        estimate=estimate,
        machine_snapshot=machine_snapshot,
        evidence=evidence,
    )
    if decision is None:
        return None
    severity, confidence, reason_codes = decision
    exceeds_machine_capacity = _trusted_estimate_exceeds_capacity(estimate, machine_snapshot)
    return WorkflowHardwareWarning(
        severity=severity,
        confidence=confidence,
        exceeds_machine_capacity=exceeds_machine_capacity,
        reason_codes=reason_codes,
        estimate=_warning_estimate(estimate),
        machine_signal=_machine_signal(machine_snapshot),
        evidence=evidence,
        developer_details=_developer_details(
            estimate=estimate,
            machine_snapshot=machine_snapshot,
            evidence=evidence,
            reason_codes=reason_codes,
            exceeds_machine_capacity=exceeds_machine_capacity,
        ),
    )


def _warning_decision(
    *,
    estimate: WorkflowMemoryEstimate,
    machine_snapshot: MachineMemorySnapshot | None,
    evidence: WorkflowHardwareWarningEvidence,
) -> tuple[HardwareWarningSeverity, HardwareWarningConfidence, list[HardwareWarningReasonCode]] | None:
    reason_codes: list[HardwareWarningReasonCode] = []
    local_memory_error = evidence.local_input_profile_match == "matching" and evidence.local_memory_error_runs > 0
    related_local_memory_error = (
        evidence.local_input_profile_match == "mismatched" and evidence.local_memory_error_runs > 0
    )
    matching_local_success = (
        evidence.local_input_profile_match == "matching"
        and evidence.local_successful_runs > 0
        and evidence.local_memory_error_runs == 0
    )
    if local_memory_error:
        reason_codes.append(HardwareWarningReasonCode.LOCAL_MEMORY_ERROR)
    elif related_local_memory_error:
        reason_codes.append(HardwareWarningReasonCode.LOCAL_MEMORY_ERROR_SETTINGS_MISMATCH)

    if evidence.local_input_profile_match == "mismatched" and evidence.local_successful_runs > 0:
        reason_codes.append(HardwareWarningReasonCode.LOCAL_SUCCESS_SETTINGS_MISMATCH)

    if _creator_estimate_present(estimate):
        reason_codes.append(HardwareWarningReasonCode.CREATOR_OBSERVED_MEMORY_HINT)
    if estimate.source is RunnerMemoryEstimateSource.HEURISTIC:
        reason_codes.append(HardwareWarningReasonCode.MODEL_SIZE_HEURISTIC)

    if machine_snapshot is None:
        if local_memory_error:
            return HardwareWarningSeverity.HIGH, HardwareWarningConfidence.HIGH, _unique_reasons(reason_codes)
        if related_local_memory_error:
            return HardwareWarningSeverity.MEDIUM, HardwareWarningConfidence.LOW, _unique_reasons(reason_codes)
        return None

    pressure_high = machine_snapshot.memory_pressure is MemoryPressureLevel.HIGH
    temporary_low = pressure_high or _low_free_memory(machine_snapshot)
    if pressure_high:
        reason_codes.append(HardwareWarningReasonCode.MEMORY_PRESSURE_HIGH)
    elif temporary_low:
        reason_codes.append(HardwareWarningReasonCode.TEMPORARY_LOW_FREE_MEMORY)

    vram_capacity_risk = _capacity_risk(estimate.estimated_peak_vram_mb, machine_snapshot.total_vram_mb)
    ram_capacity_risk = _capacity_risk(estimate.estimated_peak_ram_mb, machine_snapshot.total_ram_mb)
    vram_free_shortfall = _free_shortfall(estimate.estimated_peak_vram_mb, machine_snapshot.free_vram_mb)
    ram_free_shortfall = _free_shortfall(estimate.estimated_peak_ram_mb, machine_snapshot.free_ram_mb)
    if vram_capacity_risk:
        reason_codes.append(HardwareWarningReasonCode.ESTIMATED_VRAM_CAPACITY_RISK)
    elif vram_free_shortfall:
        reason_codes.append(HardwareWarningReasonCode.ESTIMATED_VRAM_SHORTFALL)
    if ram_capacity_risk:
        reason_codes.append(HardwareWarningReasonCode.ESTIMATED_RAM_CAPACITY_RISK)
    elif ram_free_shortfall:
        reason_codes.append(HardwareWarningReasonCode.ESTIMATED_RAM_SHORTFALL)

    if local_memory_error:
        return HardwareWarningSeverity.HIGH, HardwareWarningConfidence.HIGH, _unique_reasons(reason_codes)

    if matching_local_success:
        return None

    if _strong_capacity_risk(
        estimate=estimate,
        machine_snapshot=machine_snapshot,
        vram_capacity_risk=vram_capacity_risk,
        ram_capacity_risk=ram_capacity_risk,
    ):
        severity = (
            HardwareWarningSeverity.HIGH
            if _trusted_estimate_exceeds_capacity(estimate, machine_snapshot)
            else HardwareWarningSeverity.MEDIUM
        )
        return severity, _advisory_confidence_for_estimate(estimate), _unique_reasons(reason_codes)

    if (
        related_local_memory_error
        or temporary_low
        or vram_free_shortfall
        or ram_free_shortfall
        or vram_capacity_risk
        or ram_capacity_risk
    ):
        confidence = _advisory_confidence_for_estimate(estimate)
        return HardwareWarningSeverity.MEDIUM, confidence, _unique_reasons(reason_codes)

    return None


def _safe_machine_snapshot(memory_observer: MachineMemoryObserver | None) -> MachineMemorySnapshot | None:
    if memory_observer is None:
        return None
    try:
        return memory_observer.snapshot()
    except Exception:
        return None


def _local_evidence(
    memory_learning_store: LocalMemoryLearningStore | None,
    *,
    workflow_id: str,
    machine_snapshot: MachineMemorySnapshot | None,
    input_profile_fingerprint: str | None,
    summaries: list[LocalMemoryEvidenceSummary] | None,
) -> tuple[LocalMemoryEvidenceSummary | None, Literal["matching", "mismatched", "none"]]:
    if summaries is None:
        if memory_learning_store is None:
            return None, "none"
        try:
            summaries = memory_learning_store.list_summaries()
        except Exception:
            return None, "none"
    matching: list[LocalMemoryEvidenceSummary] = []
    related: list[LocalMemoryEvidenceSummary] = []
    for summary in summaries:
        if summary.workflow_id != workflow_id:
            continue
        if machine_snapshot is not None and not _summary_matches_machine(summary, machine_snapshot):
            continue
        if input_profile_fingerprint is None or summary.input_profile_fingerprint == input_profile_fingerprint:
            matching.append(summary)
        else:
            related.append(summary)
    if matching:
        return _combine_local_summaries(matching, input_profile_fingerprint=input_profile_fingerprint), "matching"
    if related:
        return _combine_local_summaries(related, input_profile_fingerprint=None), "mismatched"
    return None, "none"


def _summary_matches_machine(
    summary: LocalMemoryEvidenceSummary,
    machine_snapshot: MachineMemorySnapshot,
) -> bool:
    if summary.backend is not MemoryBackend.UNKNOWN and machine_snapshot.backend is not MemoryBackend.UNKNOWN:
        if summary.backend != machine_snapshot.backend:
            return False
    if summary.machine_profile_id is not None and machine_snapshot.machine_profile_id is not None:
        return summary.machine_profile_id == machine_snapshot.machine_profile_id
    return True


def _combine_local_summaries(
    summaries: list[LocalMemoryEvidenceSummary],
    *,
    input_profile_fingerprint: str | None,
) -> LocalMemoryEvidenceSummary:
    first = summaries[0]
    last_success_at = max(
        (summary.last_success_at for summary in summaries if summary.last_success_at),
        default=None,
    )
    last_memory_error_at = max(
        (summary.last_memory_error_at for summary in summaries if summary.last_memory_error_at),
        default=None,
    )
    memory_error_runs = sum(_recent_memory_error_runs(summary) for summary in summaries)
    if _timestamp_at_or_after(last_success_at, last_memory_error_at):
        memory_error_runs = 0
    return LocalMemoryEvidenceSummary(
        workflow_id=first.workflow_id,
        runner_process_compatibility_key=first.runner_process_compatibility_key,
        machine_profile_id=first.machine_profile_id,
        backend=first.backend,
        input_profile_fingerprint=input_profile_fingerprint,
        successful_runs=sum(summary.successful_runs for summary in summaries),
        memory_error_runs=memory_error_runs,
        other_failed_runs=sum(summary.other_failed_runs for summary in summaries),
        evictions_required=sum(summary.evictions_required for summary in summaries),
        retries_required=sum(summary.retries_required for summary in summaries),
        observed_peak_vram_mb=_max_optional(summary.observed_peak_vram_mb for summary in summaries),
        observed_peak_ram_mb=_max_optional(summary.observed_peak_ram_mb for summary in summaries),
        process_tree_observed_peak_vram_mb=_max_optional(
            summary.process_tree_observed_peak_vram_mb for summary in summaries
        ),
        process_tree_observed_peak_ram_mb=_max_optional(
            summary.process_tree_observed_peak_ram_mb for summary in summaries
        ),
        system_observed_peak_delta_vram_mb=_max_optional(
            summary.system_observed_peak_delta_vram_mb for summary in summaries
        ),
        system_observed_peak_delta_ram_mb=_max_optional(
            summary.system_observed_peak_delta_ram_mb for summary in summaries
        ),
        backend_allocator_observed_peak_vram_mb=_max_optional(
            summary.backend_allocator_observed_peak_vram_mb for summary in summaries
        ),
        attribution_quality=first.attribution_quality,
        attribution_sources=_unique_strings(source for summary in summaries for source in summary.attribution_sources),
        attribution_reasons=_unique_strings(reason for summary in summaries for reason in summary.attribution_reasons),
        last_success_at=last_success_at,
        last_memory_error_at=last_memory_error_at,
    )


def _warning_estimate(estimate: WorkflowMemoryEstimate) -> WorkflowHardwareWarningEstimate:
    return WorkflowHardwareWarningEstimate(
        estimated_peak_vram_mb=estimate.estimated_peak_vram_mb,
        estimated_peak_ram_mb=estimate.estimated_peak_ram_mb,
        source=estimate.source.value,
        confidence=None if estimate.confidence is RunnerMemoryEstimateConfidence.UNKNOWN else estimate.confidence.value,
    )


def _recent_memory_error_runs(summary: LocalMemoryEvidenceSummary) -> int:
    if summary.memory_error_runs == 0 or summary.last_memory_error_at is None:
        return 0
    observed_at = _parse_timestamp(summary.last_memory_error_at)
    if observed_at is None:
        return 0
    success_at = _parse_timestamp(summary.last_success_at)
    if success_at is not None and success_at >= observed_at:
        return 0
    return summary.memory_error_runs if datetime.now(UTC) - observed_at <= LOCAL_MEMORY_ERROR_MAX_AGE else 0


def _timestamp_at_or_after(left: str | None, right: str | None) -> bool:
    left_at = _parse_timestamp(left)
    right_at = _parse_timestamp(right)
    return left_at is not None and right_at is not None and left_at >= right_at


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _machine_signal(machine_snapshot: MachineMemorySnapshot | None) -> WorkflowHardwareWarningMachineSignal | None:
    if machine_snapshot is None:
        return None
    return WorkflowHardwareWarningMachineSignal(
        backend=machine_snapshot.backend.value,
        memory_pressure=machine_snapshot.memory_pressure.value,
        total_vram_mb=machine_snapshot.total_vram_mb,
        free_vram_mb=machine_snapshot.free_vram_mb,
        total_ram_mb=machine_snapshot.total_ram_mb,
        free_ram_mb=machine_snapshot.free_ram_mb,
        signal_quality=machine_snapshot.signal_quality.value,
    )


def _developer_details(
    *,
    estimate: WorkflowMemoryEstimate,
    machine_snapshot: MachineMemorySnapshot | None,
    evidence: WorkflowHardwareWarningEvidence,
    reason_codes: list[HardwareWarningReasonCode],
    exceeds_machine_capacity: bool,
) -> dict[str, object]:
    return {
        "reason_codes": [reason.value for reason in reason_codes],
        "estimate_source": estimate.source.value,
        "estimate_reasons": list(estimate.reasons),
        "machine_signal_available": machine_snapshot is not None and machine_snapshot.available,
        "machine_signal_quality": machine_snapshot.signal_quality.value if machine_snapshot is not None else None,
        "memory_pressure": machine_snapshot.memory_pressure.value if machine_snapshot is not None else None,
        "local_input_profile_match": evidence.local_input_profile_match,
        "local_successful_runs": evidence.local_successful_runs,
        "local_memory_error_runs": evidence.local_memory_error_runs,
        "exceeds_machine_capacity": exceeds_machine_capacity,
    }


def _creator_estimate_present(estimate: WorkflowMemoryEstimate) -> bool:
    return (
        estimate.source is RunnerMemoryEstimateSource.CREATOR_OBSERVED
        or estimate.creator_observed_peak_vram_mb is not None
        or estimate.creator_observed_peak_ram_mb is not None
    )


def _advisory_confidence_for_estimate(estimate: WorkflowMemoryEstimate) -> HardwareWarningConfidence:
    if estimate.confidence in {
        RunnerMemoryEstimateConfidence.MEDIUM,
        RunnerMemoryEstimateConfidence.HIGH,
    }:
        return HardwareWarningConfidence.MEDIUM
    return HardwareWarningConfidence.LOW


def _low_free_memory(machine_snapshot: MachineMemorySnapshot) -> bool:
    return _free_ratio_at_or_below(machine_snapshot.free_vram_mb, machine_snapshot.total_vram_mb, 0.20) or (
        machine_snapshot.total_vram_mb is None
        and _free_ratio_at_or_below(machine_snapshot.free_ram_mb, machine_snapshot.total_ram_mb, 0.15)
    )


def _capacity_risk(estimated_mb: int | None, total_mb: int | None) -> bool:
    return estimated_mb is not None and total_mb is not None and estimated_mb > int(total_mb * 0.9)


def _strong_capacity_risk(
    *,
    estimate: WorkflowMemoryEstimate,
    machine_snapshot: MachineMemorySnapshot,
    vram_capacity_risk: bool,
    ram_capacity_risk: bool,
) -> bool:
    if estimate.source is RunnerMemoryEstimateSource.CREATOR_OBSERVED:
        return False
    if estimate.source is RunnerMemoryEstimateSource.HEURISTIC:
        return _exceeds_total(estimate.estimated_peak_vram_mb, machine_snapshot.total_vram_mb) or _exceeds_total(
            estimate.estimated_peak_ram_mb,
            machine_snapshot.total_ram_mb,
        )
    return vram_capacity_risk or ram_capacity_risk


def _trusted_estimate_exceeds_capacity(
    estimate: WorkflowMemoryEstimate,
    machine_snapshot: MachineMemorySnapshot | None,
) -> bool:
    if machine_snapshot is None or estimate.effective_source not in {
        RunnerMemoryEstimateSource.LOCAL_OBSERVED,
        RunnerMemoryEstimateSource.DECLARED,
    }:
        return False
    return _exceeds_total(estimate.estimated_peak_vram_mb, machine_snapshot.total_vram_mb) or _exceeds_total(
        estimate.estimated_peak_ram_mb,
        machine_snapshot.total_ram_mb,
    )


def _exceeds_total(estimated_mb: int | None, total_mb: int | None) -> bool:
    return estimated_mb is not None and total_mb is not None and estimated_mb > total_mb


def _free_shortfall(estimated_mb: int | None, free_mb: int | None) -> bool:
    return estimated_mb is not None and free_mb is not None and estimated_mb > free_mb


def _free_ratio_at_or_below(free_mb: int | None, total_mb: int | None, threshold: float) -> bool:
    if free_mb is None or total_mb is None or total_mb <= 0:
        return False
    return free_mb / total_mb <= threshold


def _required_model_size_mb_from_package(package: WorkflowPackage) -> int | None:
    total_size_bytes = total_required_model_size_bytes(package.required_models)
    if total_size_bytes <= 0:
        return None
    return max(1, total_size_bytes // (1024 * 1024))


def _observed_hardware_int(package: WorkflowPackage, key: str) -> int | None:
    value = package.observed_hardware.get(key)
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _unique_reasons(
    reason_codes: list[HardwareWarningReasonCode],
) -> list[HardwareWarningReasonCode]:
    unique: list[HardwareWarningReasonCode] = []
    for reason in reason_codes:
        if reason not in unique:
            unique.append(reason)
    return unique


def _unique_strings(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if value not in unique:
            unique.append(value)
    return unique


def _max_optional(values: Iterable[int | None]) -> int | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None
