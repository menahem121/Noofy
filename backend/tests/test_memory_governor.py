import subprocess
import json

import pytest
from pydantic import ValidationError

from app.diagnostics import LogStore
from app.runtime.memory import service as memory_service_module
from app.runtime.memory.memory_governor import (
    FallbackMemoryObserver,
    BackendAllocatorMemorySample,
    GpuProcessMemoryUsage,
    LocalMemoryEvidenceSummary,
    LocalMemoryLearningStore,
    LocalMemoryObservation,
    MachineMemorySnapshot,
    MemoryAdmissionRequest,
    MemoryAttributionQuality,
    MemoryBackend,
    MemoryDecisionAction,
    MemoryGovernorDecision,
    MemoryObservationOutcome,
    MemoryPressureLevel,
    MemoryReleaseStatus,
    MemoryRiskLevel,
    MemorySampleWindow,
    MemorySignalQuality,
    NvmlError,
    NvmlMemoryObserver,
    NvidiaSmiMemoryObserver,
    ProcessTreeMemoryObserver,
    RunnerMemoryTelemetryReader,
    RunnerMemorySnapshot,
    SystemMemoryObserver,
    UnavailableMemoryObserver,
    WindowsGpuMemoryObserver,
    WorkflowMemoryEstimate,
    WorkflowMemoryEstimateRequest,
    build_workflow_memory_estimate,
    conservative_memory_class,
    decide_memory_admission,
    eviction_candidates,
    estimate_evidence_rank,
    likely_memory_error,
    memory_requirement_for_decision,
    memory_requirement_from_error,
    memory_release_blocking_constraints,
    memory_release_satisfied,
    memory_pressure_from_free_ratio,
    preferred_memory_estimate,
    record_memory_governor_decision,
    retry_after_memory_cleanup_decision,
    summarize_local_memory_observations,
    wait_for_memory_release,
    wait_for_memory_release_async,
    memory_user_status_for_decision,
    _linux_system_ram_mb,
)
from app.runtime.runners.supervisor import (
    RunnerDescriptor,
    RunnerKind,
    RunnerMemoryClass,
    RunnerMemoryEstimateConfidence,
    RunnerMemoryEstimateSource,
    RunnerStatus,
)


def test_machine_memory_snapshot_validates_bounds_and_forbids_extra_fields() -> None:
    snapshot = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        device_name="NVIDIA A10G",
        total_vram_mb=24_000,
        free_vram_mb=18_000,
        total_ram_mb=64_000,
        free_ram_mb=50_000,
        memory_pressure=MemoryPressureLevel.LOW,
        signal_quality=MemorySignalQuality.BACKEND_API,
        signal_sources=["nvml", "system_ram"],
        pressure_reasons=["vram_free_ratio_medium"],
        runner_id="runner-a",
        job_id="job-a",
        workflow_id="workflow-a",
        runner_root_pid=100,
        runner_child_pids=[101, 102],
        sample_window=MemorySampleWindow.EXECUTION,
        attribution_quality=MemoryAttributionQuality.PROCESS_TREE,
        attribution_sources=["process_tree_rss"],
        attribution_reasons=["runner_process_tree_rss"],
        process_tree_ram_mb=2048,
    )

    assert snapshot.backend is MemoryBackend.CUDA
    assert snapshot.free_vram_mb == 18_000
    assert snapshot.signal_quality is MemorySignalQuality.BACKEND_API
    assert snapshot.signal_sources == ["nvml", "system_ram"]
    assert snapshot.pressure_reasons == ["vram_free_ratio_medium"]
    assert snapshot.runner_root_pid == 100
    assert snapshot.runner_child_pids == [101, 102]
    assert snapshot.sample_window is MemorySampleWindow.EXECUTION
    assert snapshot.attribution_quality is MemoryAttributionQuality.PROCESS_TREE
    assert snapshot.process_tree_ram_mb == 2048

    with pytest.raises(ValidationError):
        MachineMemorySnapshot(total_vram_mb=8_000, free_vram_mb=9_000)

    with pytest.raises(ValidationError):
        MachineMemorySnapshot(total_ram_mb=16_000, free_ram_mb=20_000)

    with pytest.raises(ValidationError):
        MachineMemorySnapshot(total_vram_mb=-1)

    with pytest.raises(ValidationError):
        MachineMemorySnapshot(unexpected=True)  # type: ignore[call-arg]


def test_memory_requirement_from_runtime_oom_detects_capacity_shortfall() -> None:
    requirement = memory_requirement_from_error(
        "Allocation on device 0 would exceed allowed memory. (out of memory)\n"
        "Currently allocated     : 20.99 GiB\n"
        "Requested               : 1.19 GiB\n"
        "Device limit            : 22.06 GiB\n"
        "Free (according to CUDA): 8.12 MiB"
    )

    assert requirement is not None
    assert requirement["required_vram_mb"] == 22_713
    assert requirement["total_vram_mb"] == 22_589
    assert requirement["capacity_exceeded"] is True
    assert requirement["freeing_memory_may_help"] is False


def test_memory_requirement_for_decision_reports_when_freeing_memory_may_help() -> None:
    decision = MemoryGovernorDecision(
        action=MemoryDecisionAction.BLOCKED_BY_MEMORY,
        reason_code="temporary_pressure",
        confidence=RunnerMemoryEstimateConfidence.HIGH,
        workflow_estimate=WorkflowMemoryEstimate(
            workflow_id="workflow-a",
            source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            confidence=RunnerMemoryEstimateConfidence.HIGH,
            estimated_peak_vram_mb=10_000,
        ),
        machine_snapshot=MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=24_000,
            free_vram_mb=5_000,
        ),
        required_vram_margin_mb=1_000,
    )

    requirement = memory_requirement_for_decision(decision)

    assert requirement["required_vram_mb"] == 11_000
    assert requirement["total_vram_mb"] == 24_000
    assert requirement["capacity_exceeded"] is False
    assert requirement["freeing_memory_may_help"] is True


def test_conservative_memory_class_treats_unknown_and_medium_as_heavy() -> None:
    assert conservative_memory_class(RunnerMemoryClass.UNKNOWN) is RunnerMemoryClass.GPU_HEAVY
    assert conservative_memory_class(RunnerMemoryClass.GPU_MEDIUM) is RunnerMemoryClass.GPU_HEAVY
    assert conservative_memory_class(RunnerMemoryClass.GPU_LIGHT) is RunnerMemoryClass.GPU_LIGHT
    assert conservative_memory_class(RunnerMemoryClass.CPU_ONLY) is RunnerMemoryClass.CPU_ONLY


def test_local_memory_evidence_summary_exposes_learning_flags() -> None:
    repeated_success = LocalMemoryEvidenceSummary(
        workflow_id="workflow-a",
        backend=MemoryBackend.CUDA,
        successful_runs=3,
        observed_peak_vram_mb=7200,
        observed_peak_ram_mb=3100,
    )

    assert repeated_success.has_local_evidence is True
    assert repeated_success.has_repeated_success is True
    assert repeated_success.has_memory_failure is False

    failed_once = LocalMemoryEvidenceSummary(workflow_id="workflow-a", memory_error_runs=1)

    assert failed_once.has_local_evidence is True
    assert failed_once.has_repeated_success is False
    assert failed_once.has_memory_failure is True


def test_workflow_memory_estimate_prefers_local_evidence_over_creator_observations() -> None:
    creator_estimate = WorkflowMemoryEstimate(
        workflow_id="workflow-a",
        memory_class=RunnerMemoryClass.GPU_HEAVY,
        confidence=RunnerMemoryEstimateConfidence.HIGH,
        source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
        creator_observed_peak_vram_mb=6400,
    )
    local_estimate = WorkflowMemoryEstimate(
        workflow_id="workflow-a",
        memory_class=RunnerMemoryClass.GPU_MEDIUM,
        confidence=RunnerMemoryEstimateConfidence.MEDIUM,
        source=RunnerMemoryEstimateSource.HEURISTIC,
        estimated_peak_vram_mb=7100,
        local_evidence=LocalMemoryEvidenceSummary(
            workflow_id="workflow-a",
            backend=MemoryBackend.CUDA,
            successful_runs=2,
            observed_peak_vram_mb=7100,
        ),
    )

    assert local_estimate.effective_source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    assert estimate_evidence_rank(local_estimate) > estimate_evidence_rank(creator_estimate)
    assert preferred_memory_estimate([creator_estimate, local_estimate]) == local_estimate
    assert local_estimate.conservative_memory_class is RunnerMemoryClass.GPU_HEAVY


def test_runner_memory_snapshot_can_be_derived_from_descriptor() -> None:
    descriptor = RunnerDescriptor(
        runner_id="runner-a",
        kind=RunnerKind.ISOLATED_COMFYUI,
        base_url="http://127.0.0.1:9001",
        ws_url="ws://127.0.0.1:9001/ws",
        fingerprint="sha256:" + ("a" * 64),
        status=RunnerStatus.IDLE_WARM,
        runner_process_compatibility_key="compat-a",
        memory_class=RunnerMemoryClass.GPU_LIGHT,
        memory_estimate_confidence=RunnerMemoryEstimateConfidence.MEDIUM,
        memory_estimate_source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
        observed_idle_vram_mb=900,
        observed_execution_peak_vram_mb=2400,
        recent_memory_error_count=1,
        open_workflow_lease_count=2,
        model_residency_signature="sha256:model-a",
        execution_profile_signature="sha256:execution-a",
    )

    snapshot = RunnerMemorySnapshot.from_descriptor(descriptor)

    assert snapshot.runner_id == "runner-a"
    assert snapshot.status is RunnerStatus.IDLE_WARM
    assert snapshot.memory_class is RunnerMemoryClass.GPU_LIGHT
    assert snapshot.observed_idle_vram_mb == 900
    assert snapshot.observed_execution_peak_vram_mb == 2400
    assert snapshot.open_workflow_lease_count == 2
    assert snapshot.recent_memory_error_count == 1
    assert snapshot.model_residency_signature == "sha256:model-a"
    assert snapshot.execution_profile_signature == "sha256:execution-a"


def test_memory_governor_decision_serializes_and_records_diagnostics() -> None:
    decision = MemoryGovernorDecision(
        action=MemoryDecisionAction.EVICT_THEN_START,
        risk_level=MemoryRiskLevel.MEDIUM,
        confidence=RunnerMemoryEstimateConfidence.MEDIUM,
        reason_code="insufficient_margin_for_co_residence",
        workflow_id="workflow-b",
        evict_runner_ids=["runner-a"],
        can_retry_after_cleanup=True,
        user_message="Noofy is freeing memory before starting this workflow.",
        workflow_estimate=WorkflowMemoryEstimate(
            workflow_id="workflow-b",
            memory_class=RunnerMemoryClass.GPU_HEAVY,
            estimated_peak_vram_mb=11_000,
            source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            confidence=RunnerMemoryEstimateConfidence.MEDIUM,
        ),
        machine_snapshot=MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            total_vram_mb=24_000,
            free_vram_mb=8_000,
            signal_quality=MemorySignalQuality.BACKEND_API,
            signal_sources=["nvml"],
            pressure_reasons=["vram_free_ratio_medium"],
        ),
    )

    dumped = decision.model_dump(mode="json")

    assert dumped["action"] == "evict_then_start"
    assert dumped["workflow_estimate"]["memory_class"] == "gpu_heavy"
    assert dumped["machine_snapshot"]["backend"] == "cuda"
    assert dumped["signal_quality"] == "unknown"
    assert dumped["machine_snapshot"]["signal_sources"] == ["nvml"]

    store = LogStore()
    event = record_memory_governor_decision(store, decision)

    assert event.source == "memory_governor"
    assert event.workflow_id == "workflow-b"
    assert event.message == "Noofy is freeing memory before starting this workflow."
    assert event.details["action"] == "evict_then_start"
    assert store.list_events().events[0].details["reason_code"] == "insufficient_margin_for_co_residence"

    with pytest.raises(ValidationError):
        MemoryGovernorDecision(
            action=MemoryDecisionAction.REUSE_RUNNER,
            reason_code="compatible_runner_warm",
            extra_field=True,  # type: ignore[call-arg]
        )


def test_nvml_memory_observer_parses_cuda_snapshot_without_hardware() -> None:
    class FakeNvmlApi:
        def read_memory(self) -> tuple[str | None, int | None, int | None]:
            return "NVIDIA RTX", 24_576, 20_480

        def read_process_memory(self) -> list[GpuProcessMemoryUsage]:
            return []

    snapshot = NvmlMemoryObserver(api=FakeNvmlApi()).snapshot()

    assert snapshot.available is True
    assert snapshot.backend is MemoryBackend.CUDA
    assert snapshot.device_name == "NVIDIA RTX"
    assert snapshot.total_vram_mb == 24_576
    assert snapshot.free_vram_mb == 20_480
    assert snapshot.signal_quality is MemorySignalQuality.BACKEND_API
    assert "nvml" in snapshot.signal_sources
    assert snapshot.error is None


def test_nvml_memory_observer_maps_process_gpu_memory_to_runner_pids() -> None:
    class FakeNvmlApi:
        def read_memory(self) -> tuple[str | None, int | None, int | None]:
            return "NVIDIA RTX", 24_576, 20_480

        def read_process_memory(self) -> list[GpuProcessMemoryUsage]:
            return [
                GpuProcessMemoryUsage(pid=100, used_vram_mb=1200),
                GpuProcessMemoryUsage(pid=101, used_vram_mb=700),
                GpuProcessMemoryUsage(pid=999, used_vram_mb=4096),
            ]

    sample = NvmlMemoryObserver(api=FakeNvmlApi()).sample_process_vram({100, 101, 102})

    assert sample.available is True
    assert sample.process_tree_vram_mb == 1900
    assert sample.matched_pids == [100, 101]
    assert sample.attribution_quality is MemoryAttributionQuality.PROCESS_EXACT
    assert sample.attribution_sources == ["nvml_process"]


def test_nvml_memory_observer_reports_weak_gpu_attribution_when_pids_do_not_match() -> None:
    class FakeNvmlApi:
        def read_memory(self) -> tuple[str | None, int | None, int | None]:
            return "NVIDIA RTX", 24_576, 20_480

        def read_process_memory(self) -> list[GpuProcessMemoryUsage]:
            return [GpuProcessMemoryUsage(pid=999, used_vram_mb=4096)]

    sample = NvmlMemoryObserver(api=FakeNvmlApi()).sample_process_vram({100, 101})

    assert sample.available is False
    assert sample.process_tree_vram_mb is None
    assert sample.attribution_quality is MemoryAttributionQuality.UNAVAILABLE
    assert "nvml_process_memory_no_matching_pid" in sample.attribution_reasons


def test_nvml_memory_observer_returns_unavailable_when_library_is_missing() -> None:
    class MissingNvmlApi:
        def read_memory(self) -> tuple[str | None, int | None, int | None]:
            raise FileNotFoundError("missing")

        def read_process_memory(self) -> list[GpuProcessMemoryUsage]:
            raise FileNotFoundError("missing")

    snapshot = NvmlMemoryObserver(api=MissingNvmlApi()).snapshot()

    assert snapshot.available is False
    assert snapshot.backend is MemoryBackend.CUDA
    assert snapshot.signal_quality is MemorySignalQuality.UNAVAILABLE
    assert snapshot.signal_sources == ["nvml"]
    assert snapshot.error == "nvml_not_found"


def test_nvml_memory_observer_returns_unavailable_on_nvml_errors() -> None:
    class ErrorNvmlApi:
        def read_memory(self) -> tuple[str | None, int | None, int | None]:
            raise NvmlError("init_failed:1")

        def read_process_memory(self) -> list[GpuProcessMemoryUsage]:
            raise NvmlError("process_failed:1")

    snapshot = NvmlMemoryObserver(api=ErrorNvmlApi()).snapshot()

    assert snapshot.available is False
    assert snapshot.signal_quality is MemorySignalQuality.UNAVAILABLE
    assert snapshot.error == "nvml_error:init_failed:1"


def test_nvidia_smi_memory_observer_parses_cuda_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.runtime.memory.memory_governor._system_ram_signals",
        lambda **_kwargs: (64_000, 50_000, MemoryPressureLevel.LOW, ["system_ram"], []),
    )

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command[0] == "nvidia-smi"
        return subprocess.CompletedProcess(command, 0, stdout="NVIDIA A10G, 23028, 17120\n", stderr="")

    snapshot = NvidiaSmiMemoryObserver(command_runner=runner).snapshot()

    assert snapshot.available is True
    assert snapshot.backend is MemoryBackend.CUDA
    assert snapshot.device_name == "NVIDIA A10G"
    assert snapshot.total_vram_mb == 23028
    assert snapshot.free_vram_mb == 17120
    assert snapshot.memory_pressure is MemoryPressureLevel.LOW
    assert snapshot.signal_quality is MemorySignalQuality.BACKEND_API
    assert "nvidia_smi" in snapshot.signal_sources
    assert snapshot.error is None


def test_nvidia_smi_memory_observer_returns_unavailable_snapshot_for_failures() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="driver unavailable")

    snapshot = NvidiaSmiMemoryObserver(command_runner=runner).snapshot()

    assert snapshot.available is False
    assert snapshot.backend is MemoryBackend.CUDA
    assert snapshot.total_vram_mb is None
    assert snapshot.signal_quality is MemorySignalQuality.UNAVAILABLE
    assert snapshot.signal_sources == ["nvidia_smi"]
    assert snapshot.error == "driver unavailable"


def test_nvidia_smi_memory_observer_allows_partial_data() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="NVIDIA A10G, 23028\n", stderr="")

    snapshot = NvidiaSmiMemoryObserver(command_runner=runner).snapshot()

    assert snapshot.available is True
    assert snapshot.device_name == "NVIDIA A10G"
    assert snapshot.total_vram_mb == 23028
    assert snapshot.free_vram_mb is None
    assert snapshot.signal_quality is MemorySignalQuality.BACKEND_API


def test_windows_gpu_memory_observer_parses_directml_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.runtime.memory.memory_governor._system_ram_signals",
        lambda **_kwargs: (64_000, 50_000, MemoryPressureLevel.LOW, ["system_ram"], []),
    )

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command[0] == "powershell"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"device_name":"AMD Radeon","total_vram_bytes":8589934592,"dedicated_used_bytes":2147483648}',
            stderr="",
        )

    snapshot = WindowsGpuMemoryObserver(command_runner=runner).snapshot()

    assert snapshot.available is True
    assert snapshot.backend is MemoryBackend.DIRECTML
    assert snapshot.device_name == "AMD Radeon"
    assert snapshot.total_vram_mb == 8192
    assert snapshot.free_vram_mb == 6144
    assert snapshot.memory_pressure is MemoryPressureLevel.LOW
    assert snapshot.signal_quality is MemorySignalQuality.SYSTEM_SAMPLE
    assert "windows_gpu_counters" in snapshot.signal_sources
    assert "win32_video_controller" in snapshot.signal_sources


def test_windows_gpu_memory_observer_falls_back_to_ram_when_counters_fail() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="counter unavailable")

    snapshot = WindowsGpuMemoryObserver(command_runner=runner).snapshot()

    assert snapshot.backend is MemoryBackend.DIRECTML
    assert snapshot.signal_quality in {MemorySignalQuality.SYSTEM_SAMPLE, MemorySignalQuality.UNAVAILABLE}
    assert snapshot.error == "counter unavailable"


def test_windows_gpu_memory_observer_maps_process_counters_to_runner_pids() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command[0] == "powershell"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                [
                    {"pid": 4242, "dedicated_used_bytes": 1048576000, "shared_used_bytes": 0},
                    {"pid": 4243, "dedicated_used_bytes": 0, "shared_used_bytes": 524288000},
                    {"pid": 9999, "dedicated_used_bytes": 999999999, "shared_used_bytes": 0},
                ]
            ),
            stderr="",
        )

    sample = WindowsGpuMemoryObserver(command_runner=runner).sample_process_vram({4242, 4243})

    assert sample.available is True
    assert sample.matched_pids == [4242, 4243]
    assert sample.process_tree_vram_mb == 1500
    assert sample.attribution_quality is MemoryAttributionQuality.PROCESS_EXACT
    assert sample.attribution_sources == ["windows_gpu_process_counters"]


def test_windows_gpu_memory_observer_reports_weak_when_process_counters_do_not_match() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps([{"pid": 9999, "dedicated_used_bytes": 1048576000}]),
            stderr="",
        )

    sample = WindowsGpuMemoryObserver(command_runner=runner).sample_process_vram({4242})

    assert sample.available is False
    assert sample.process_tree_vram_mb is None
    assert sample.attribution_quality is MemoryAttributionQuality.UNAVAILABLE
    assert "windows_gpu_process_memory_no_matching_pid" in sample.attribution_reasons


def test_process_tree_memory_observer_sums_root_and_children() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command[:2] == ["ps", "-axo"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="""
100 1 204800
101 100 102400
102 101 51200
999 1 409600
""",
            stderr="",
        )

    sample = ProcessTreeMemoryObserver(command_runner=runner, platform_name="Linux").sample(100)

    assert sample.available is True
    assert sample.root_pid == 100
    assert sample.child_pids == [101, 102]
    assert sample.process_tree_ram_mb == 350
    assert sample.attribution_quality is MemoryAttributionQuality.PROCESS_TREE
    assert sample.attribution_sources == ["process_tree_rss"]


def test_process_tree_memory_observer_handles_missing_process() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="999 1 409600\n", stderr="")

    sample = ProcessTreeMemoryObserver(command_runner=runner, platform_name="Linux").sample(100)

    assert sample.available is False
    assert sample.root_pid == 100
    assert sample.process_tree_ram_mb is None
    assert sample.error == "runner_process_not_found"


def test_runner_memory_telemetry_reader_preserves_allocator_and_dxgi_quality(tmp_path) -> None:
    path = tmp_path / "runner-memory.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "runner_id": "runner-a",
                        "pid": 4242,
                        "sample_window": "workflow_execution",
                        "observed_at": "2026-05-06T10:00:00+00:00",
                        "backend": "cuda",
                        "cuda": {
                            "allocated_current_bytes": 1073741824,
                            "reserved_current_bytes": 2147483648,
                            "allocated_peak_bytes": 3221225472,
                            "reserved_peak_bytes": 4294967296,
                            "oom_count": 0,
                            "alloc_retry_count": 1,
                        },
                    }
                ),
                json.dumps(
                    {
                        "runner_id": "runner-a",
                        "pid": 4242,
                        "sample_window": "workflow_execution",
                        "observed_at": "2026-05-06T10:00:01+00:00",
                        "backend": "directml",
                        "dxgi": {
                            "current_usage_bytes": 5368709120,
                            "budget_bytes": 8589934592,
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    sample = RunnerMemoryTelemetryReader().sample(path, runner_id="runner-a")

    assert sample.available is True
    assert sample.pid == 4242
    assert sample.sample_window is MemorySampleWindow.WORKFLOW_EXECUTION
    assert sample.current_vram_mb == 5120
    assert sample.peak_vram_mb == 5120
    assert sample.budget_vram_mb == 8192
    assert sample.signal_quality is MemorySignalQuality.ALLOCATOR
    assert sample.attribution_quality is MemoryAttributionQuality.BACKEND_ALLOCATOR
    assert "pytorch_cuda_allocator" in sample.attribution_sources
    assert "dxgi_query_video_memory_info" in sample.attribution_sources
    assert sample.details["cuda"]["alloc_retry_count"] == 1

    filtered = RunnerMemoryTelemetryReader().sample(
        path,
        runner_id="runner-a",
        observed_after="2026-05-06T10:00:00+00:00",
    )
    assert filtered.current_vram_mb == 5120
    assert filtered.details.get("cuda") is None


def test_runner_memory_telemetry_reader_is_gracefully_unavailable(tmp_path) -> None:
    sample = RunnerMemoryTelemetryReader().sample(tmp_path / "missing.jsonl", runner_id="runner-a")

    assert sample.available is False
    assert sample.attribution_quality is MemoryAttributionQuality.UNAVAILABLE
    assert sample.error == "runner_memory_telemetry_file_missing"


def test_system_and_unavailable_memory_observers_return_structured_snapshots() -> None:
    system_snapshot = SystemMemoryObserver().snapshot()
    unavailable_snapshot = UnavailableMemoryObserver(backend=MemoryBackend.MPS, error="not implemented yet").snapshot()

    assert system_snapshot.backend is MemoryBackend.CPU
    assert system_snapshot.observed_at is not None
    assert system_snapshot.signal_quality in {
        MemorySignalQuality.SYSTEM_SAMPLE,
        MemorySignalQuality.UNAVAILABLE,
    }
    assert unavailable_snapshot.available is False
    assert unavailable_snapshot.backend is MemoryBackend.MPS
    assert unavailable_snapshot.signal_quality is MemorySignalQuality.UNAVAILABLE
    assert unavailable_snapshot.error == "not implemented yet"


def test_system_memory_observer_uses_linux_psi_when_available() -> None:
    psi_text = """
some avg10=12.50 avg60=3.00 avg300=0.20 total=1234
full avg10=0.00 avg60=0.00 avg300=0.00 total=42
"""

    snapshot = SystemMemoryObserver(linux_psi_reader=lambda: psi_text).snapshot()

    assert snapshot.memory_pressure is MemoryPressureLevel.HIGH
    assert "linux_psi" in snapshot.signal_sources
    assert "linux_psi_some_high" in snapshot.pressure_reasons


def test_system_memory_observer_ignores_unavailable_linux_psi() -> None:
    snapshot = SystemMemoryObserver(linux_psi_reader=lambda: None).snapshot()

    assert "linux_psi" not in snapshot.signal_sources
    assert snapshot.signal_quality in {
        MemorySignalQuality.SYSTEM_SAMPLE,
        MemorySignalQuality.UNAVAILABLE,
    }


def test_system_memory_observer_reports_invalid_linux_psi_without_failing() -> None:
    snapshot = SystemMemoryObserver(linux_psi_reader=lambda: "not psi data").snapshot()

    assert "linux_psi" in snapshot.signal_sources
    assert "linux_psi_parse_failed" in snapshot.pressure_reasons


def test_linux_system_ram_uses_memavailable_instead_of_immediately_free_pages() -> None:
    total, available = _linux_system_ram_mb(
        lambda: """
MemTotal:       16000000 kB
MemFree:         1000000 kB
MemAvailable:  12000000 kB
Buffers:          100000 kB
Cached:         11000000 kB
"""
    )

    assert total == 15625
    assert available == 11718


def test_linux_system_ram_falls_back_to_reclaimable_file_cache_when_memavailable_is_missing() -> None:
    total, available = _linux_system_ram_mb(
        lambda: """
MemTotal:       16000000 kB
MemFree:         1000000 kB
Buffers:          250000 kB
Cached:          2000000 kB
"""
    )

    assert total == 15625
    assert available == 3173


def test_fallback_memory_observer_uses_ram_snapshot_when_cuda_is_unavailable() -> None:
    class StaticObserver:
        def __init__(self, snapshot: MachineMemorySnapshot) -> None:
            self.snapshot_value = snapshot

        def snapshot(self) -> MachineMemorySnapshot:
            return self.snapshot_value

    observer = FallbackMemoryObserver(
        UnavailableMemoryObserver(backend=MemoryBackend.CUDA, error="nvidia_smi_not_found"),
        StaticObserver(
            MachineMemorySnapshot(
                backend=MemoryBackend.CPU,
                total_ram_mb=32_000,
                free_ram_mb=24_000,
                memory_pressure=MemoryPressureLevel.LOW,
            )
        ),
    )

    snapshot = observer.snapshot()

    assert snapshot.available is True
    assert snapshot.backend is MemoryBackend.CPU
    assert snapshot.free_ram_mb == 24_000


def test_memory_pressure_from_free_ratio() -> None:
    assert memory_pressure_from_free_ratio(24_000, 18_000) is MemoryPressureLevel.LOW
    assert memory_pressure_from_free_ratio(24_000, 4_000) is MemoryPressureLevel.MEDIUM
    assert memory_pressure_from_free_ratio(24_000, 2_000) is MemoryPressureLevel.HIGH
    assert memory_pressure_from_free_ratio(None, 2_000) is MemoryPressureLevel.UNKNOWN


def test_memory_decision_carries_snapshot_signal_metadata() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-a",
                RunnerMemoryClass.GPU_LIGHT,
                1200,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
            ),
            machine_snapshot=MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=9_000,
                memory_pressure=MemoryPressureLevel.LOW,
                signal_quality=MemorySignalQuality.BACKEND_API,
                signal_sources=["nvml", "system_ram"],
                pressure_reasons=[],
            ),
        )
    )

    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.signal_quality is MemorySignalQuality.BACKEND_API
    assert decision.signal_sources == ["nvml", "system_ram"]


def test_wait_for_memory_release_succeeds_after_bounded_polling() -> None:
    observer = _SequenceMemoryObserver(
        [
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=1_000,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=8_000,
                memory_pressure=MemoryPressureLevel.LOW,
            ),
        ]
    )

    result = wait_for_memory_release(
        observer,
        required_free_vram_mb=6_000,
        max_checks=3,
        interval_seconds=0,
        sleeper=lambda _: None,
    )

    assert result.status is MemoryReleaseStatus.RELEASED
    assert result.reason_code == "memory_released"
    assert len(result.snapshots) == 2


def test_wait_for_memory_release_times_out_and_handles_unavailable_snapshots() -> None:
    timeout = wait_for_memory_release(
        _SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=1_000,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    total_vram_mb=12_000,
                    free_vram_mb=2_000,
                    memory_pressure=MemoryPressureLevel.MEDIUM,
                ),
            ]
        ),
        required_free_vram_mb=6_000,
        max_checks=2,
        interval_seconds=0,
        sleeper=lambda _: None,
    )
    unavailable = wait_for_memory_release(
        _SequenceMemoryObserver([MachineMemorySnapshot(available=False, backend=MemoryBackend.CUDA)]),
        required_free_vram_mb=6_000,
        max_checks=2,
        interval_seconds=0,
        sleeper=lambda _: None,
    )

    assert timeout.status is MemoryReleaseStatus.TIMEOUT
    assert timeout.reason_code == "memory_release_timeout"
    assert unavailable.status is MemoryReleaseStatus.UNAVAILABLE
    assert unavailable.reason_code == "memory_snapshot_unavailable"


@pytest.mark.anyio
async def test_wait_for_memory_release_async_records_pending_drop_and_unavailable_timeline() -> None:
    released = await wait_for_memory_release_async(
        _SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    free_vram_mb=1_000,
                    memory_pressure=MemoryPressureLevel.HIGH,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    free_vram_mb=2_000,
                    memory_pressure=MemoryPressureLevel.MEDIUM,
                ),
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    free_vram_mb=7_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
        required_free_vram_mb=6_000,
        timeout_seconds=0.1,
        initial_poll_interval_seconds=0.001,
        max_poll_interval_seconds=0.001,
    )
    unavailable = await wait_for_memory_release_async(
        _SequenceMemoryObserver(
            [MachineMemorySnapshot(available=False, backend=MemoryBackend.CUDA, error="nvml unavailable")]
        ),
        required_free_vram_mb=6_000,
        timeout_seconds=0.1,
    )

    assert released.status is MemoryReleaseStatus.RELEASED
    assert [event["state"] for event in released.timeline] == [
        "release_requested",
        "release_pending",
        "still_reserved_or_allocated",
        "release_pending",
        "partial_release",
        "observed_memory_drop",
    ]
    assert unavailable.status is MemoryReleaseStatus.UNAVAILABLE
    assert unavailable.timeline[-1]["state"] == "observer_unavailable"


@pytest.mark.anyio
async def test_wait_for_memory_release_async_requires_observed_drop_when_requested() -> None:
    baseline = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        free_vram_mb=8_000,
        memory_pressure=MemoryPressureLevel.LOW,
    )
    result = await wait_for_memory_release_async(
        _SequenceMemoryObserver([baseline]),
        required_free_vram_mb=6_000,
        baseline_snapshot=baseline,
        require_observed_drop=True,
        timeout_seconds=0,
    )

    assert result.status is MemoryReleaseStatus.TIMEOUT
    assert result.reason_code == "memory_release_timeout"
    assert result.timeline[-1]["state"] == "timeout"
    assert not any(
        event["state"] == "observed_memory_drop"
        for event in result.timeline
    )


@pytest.mark.anyio
async def test_wait_for_memory_release_async_counts_ram_only_drop_as_observed() -> None:
    """A core `/free` that releases RAM-cached models must confirm even when
    VRAM never moves. Before the fix, the observed-drop check short-circuited
    to VRAM whenever VRAM stats existed, so a RAM-only release timed out with
    memory_cleanup_failed despite both free-memory thresholds being satisfied.
    """
    baseline = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        free_vram_mb=8_000,
        free_ram_mb=2_000,
        memory_pressure=MemoryPressureLevel.LOW,
    )
    after_cleanup = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        free_vram_mb=8_000,  # unchanged: nothing was resident on the GPU
        free_ram_mb=9_000,  # RAM-cached models were released
        memory_pressure=MemoryPressureLevel.LOW,
    )

    result = await wait_for_memory_release_async(
        _SequenceMemoryObserver([after_cleanup]),
        required_free_vram_mb=6_000,
        required_free_ram_mb=8_000,
        baseline_snapshot=baseline,
        require_observed_drop=True,
        timeout_seconds=0.1,
        initial_poll_interval_seconds=0.001,
    )

    assert result.status is MemoryReleaseStatus.RELEASED
    assert result.baseline_free_vram_mb == 8_000
    assert result.baseline_free_ram_mb == 2_000
    assert result.final_free_vram_mb == 8_000
    assert result.final_free_ram_mb == 9_000
    assert result.blocking_constraints == []


@pytest.mark.anyio
async def test_wait_for_memory_release_async_confirms_drop_only_on_ram_release() -> None:
    """Narrow same-core `/free` confirmation accepts a RAM-only drop."""
    baseline = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        free_vram_mb=8_000,
        free_ram_mb=2_000,
        memory_pressure=MemoryPressureLevel.LOW,
    )
    after_cleanup = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        free_vram_mb=8_000,
        free_ram_mb=3_500,
        memory_pressure=MemoryPressureLevel.LOW,
    )

    result = await wait_for_memory_release_async(
        _SequenceMemoryObserver([after_cleanup]),
        baseline_snapshot=baseline,
        require_observed_drop=True,
        confirm_on_drop_only=True,
        timeout_seconds=0.1,
        initial_poll_interval_seconds=0.001,
    )

    assert result.status is MemoryReleaseStatus.RELEASED


@pytest.mark.anyio
async def test_wait_for_memory_release_async_records_ram_and_vram_proof_on_block() -> None:
    """Before a run is blocked, the release check must prove both RAM and VRAM
    were measured before cleanup (baseline), after cleanup (snapshots), and at
    the blocking decision (finals + named unmet constraints).
    """
    baseline = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        free_vram_mb=2_000,
        free_ram_mb=2_000,
        memory_pressure=MemoryPressureLevel.MEDIUM,
    )
    after_cleanup = MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        free_vram_mb=7_000,  # VRAM released and above its requirement
        free_ram_mb=2_500,  # RAM retained by the process: below requirement
        memory_pressure=MemoryPressureLevel.MEDIUM,
    )

    result = await wait_for_memory_release_async(
        _SequenceMemoryObserver([after_cleanup]),
        required_free_vram_mb=6_000,
        required_free_ram_mb=8_000,
        baseline_snapshot=baseline,
        require_observed_drop=True,
        timeout_seconds=0,
    )

    assert result.status is MemoryReleaseStatus.RELEASED_INSUFFICIENT_MEMORY
    assert result.reason_code == "memory_released_insufficient_memory"
    assert result.baseline_free_vram_mb == 2_000
    assert result.baseline_free_ram_mb == 2_000
    assert result.final_free_vram_mb == 7_000
    assert result.final_free_ram_mb == 2_500
    assert result.blocking_constraints == ["ram_below_required"]
    assert result.timeline[-1]["state"] == "released_insufficient_memory"
    assert result.timeline[-1]["blocking_constraints"] == ["ram_below_required"]


def test_sync_wait_for_memory_release_records_finals_and_constraints() -> None:
    result = wait_for_memory_release(
        _SequenceMemoryObserver(
            [
                MachineMemorySnapshot(
                    backend=MemoryBackend.CUDA,
                    free_vram_mb=1_000,
                    free_ram_mb=9_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
            ]
        ),
        required_free_vram_mb=6_000,
        required_free_ram_mb=4_000,
        max_checks=2,
        interval_seconds=0,
        sleeper=lambda _: None,
    )

    assert result.status is MemoryReleaseStatus.TIMEOUT
    assert result.final_free_vram_mb == 1_000
    assert result.final_free_ram_mb == 9_000
    assert result.blocking_constraints == ["vram_below_required"]


def test_memory_release_blocking_constraints_names_every_unmet_requirement() -> None:
    constraints = memory_release_blocking_constraints(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            free_vram_mb=1_000,
            free_ram_mb=1_000,
            memory_pressure=MemoryPressureLevel.HIGH,
        ),
        required_free_vram_mb=6_000,
        required_free_ram_mb=8_000,
        require_observed_drop=True,
        memory_drop_observed=False,
    )

    assert constraints == [
        "memory_pressure_high",
        "vram_below_required",
        "ram_below_required",
        "no_observed_memory_drop",
    ]


def test_memory_release_satisfied_requires_margin_and_low_pressure() -> None:
    assert (
        memory_release_satisfied(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                free_vram_mb=8_000,
                free_ram_mb=16_000,
                memory_pressure=MemoryPressureLevel.LOW,
            ),
            required_free_vram_mb=6_000,
            required_free_ram_mb=8_000,
        )
        is True
    )
    assert (
        memory_release_satisfied(
            MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                free_vram_mb=8_000,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            required_free_vram_mb=6_000,
        )
        is False
    )


def test_retry_after_memory_cleanup_decision_allows_single_memory_retry() -> None:
    estimate = _estimate(
        "workflow-a",
        RunnerMemoryClass.GPU_HEAVY,
        7_000,
        confidence=RunnerMemoryEstimateConfidence.MEDIUM,
    )
    decision = retry_after_memory_cleanup_decision(
        workflow_estimate=estimate,
        machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=8_000),
        error_message="CUDA out of memory. Tried to allocate 1.2 GiB.",
    )

    assert likely_memory_error("CUDA out of memory") is True
    assert decision.action is MemoryDecisionAction.RETRY_AFTER_MEMORY_CLEANUP
    assert decision.can_retry_after_cleanup is True
    assert decision.reason_code == "memory_error_retry_after_cleanup"


def test_retry_after_memory_cleanup_decision_blocks_after_retry_or_non_memory_error() -> None:
    estimate = _estimate("workflow-a", RunnerMemoryClass.GPU_HEAVY, 7_000)
    retried = retry_after_memory_cleanup_decision(
        workflow_estimate=estimate,
        machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=8_000),
        error_message="CUDA out of memory",
        retry_already_attempted=True,
    )
    unrelated = retry_after_memory_cleanup_decision(
        workflow_estimate=estimate,
        machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=8_000),
        error_message="custom node import failed",
    )

    assert retried.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert retried.reason_code == "retry_already_attempted"
    assert unrelated.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert unrelated.reason_code == "not_a_memory_error"
    assert likely_memory_error("custom node import failed") is False


def test_build_workflow_memory_estimate_uses_repeated_local_success() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            input_profile_fingerprint="settings-a",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                input_profile_fingerprint="settings-a",
                successful_runs=3,
                observed_peak_vram_mb=7200,
                observed_peak_ram_mb=2900,
            ),
            creator_observed_peak_vram_mb=6400,
        )
    )

    assert estimate.source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    assert estimate.confidence is RunnerMemoryEstimateConfidence.HIGH
    assert estimate.estimated_peak_vram_mb == 7200
    assert estimate.memory_class is RunnerMemoryClass.GPU_MEDIUM
    assert "repeated_local_success" in estimate.reasons


def test_build_workflow_memory_estimate_lowers_confidence_for_local_memory_failure() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                successful_runs=1,
                memory_error_runs=1,
                observed_peak_vram_mb=9300,
            ),
        )
    )

    assert estimate.source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    assert estimate.confidence is RunnerMemoryEstimateConfidence.LOW
    assert estimate.recent_memory_error is True
    assert "local_memory_failure" in estimate.reasons


def test_build_workflow_memory_estimate_ignores_generic_failed_peak_when_creator_hint_exists() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                other_failed_runs=1,
                observed_peak_vram_mb=21_774,
            ),
            creator_observed_peak_vram_mb=23_995,
        )
    )

    assert estimate.source is RunnerMemoryEstimateSource.CREATOR_OBSERVED
    assert estimate.estimated_peak_vram_mb == 23_995
    assert estimate.local_evidence is None
    assert "creator_observed_memory_hint" in estimate.reasons


def test_build_workflow_memory_estimate_lowers_confidence_for_changed_settings() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            input_profile_fingerprint="settings-b",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                input_profile_fingerprint="settings-a",
                successful_runs=4,
                observed_peak_vram_mb=5200,
            ),
        )
    )

    assert estimate.source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    assert estimate.confidence is RunnerMemoryEstimateConfidence.LOW
    assert "local_evidence_settings_mismatch" in estimate.reasons


def test_compatible_local_evidence_fallback_filters_by_model_residency_signature() -> None:
    summaries = [
        LocalMemoryEvidenceSummary(
            workflow_id="workflow-a",
            runner_process_compatibility_key="compat-a",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-a",
            model_residency_signature="sha256:model-a",
            successful_runs=3,
            observed_peak_vram_mb=6000,
        ),
        LocalMemoryEvidenceSummary(
            workflow_id="workflow-a",
            runner_process_compatibility_key="compat-a",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-b",
            model_residency_signature="sha256:model-b",
            successful_runs=1,
            observed_peak_vram_mb=9000,
        ),
    ]

    matching = memory_service_module._best_compatible_success_evidence(
        summaries,
        workflow_id="workflow-a",
        runner_process_compatibility_key="compat-a",
        machine_profile_id="machine-a",
        backend=MemoryBackend.CUDA,
        input_profile_fingerprint="settings-c",
        model_residency_signature="sha256:model-b",
    )
    missing = memory_service_module._best_compatible_success_evidence(
        summaries,
        workflow_id="workflow-a",
        runner_process_compatibility_key="compat-a",
        machine_profile_id="machine-a",
        backend=MemoryBackend.CUDA,
        input_profile_fingerprint="settings-c",
        model_residency_signature="sha256:model-c",
    )

    assert matching is not None
    assert matching.model_residency_signature == "sha256:model-b"
    assert matching.observed_peak_vram_mb == 9000
    assert missing is None


def test_build_workflow_memory_estimate_falls_back_through_creator_declared_heuristic_unknown() -> None:
    creator = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(workflow_id="workflow-a", creator_observed_peak_vram_mb=6200)
    )
    declared = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(workflow_id="workflow-b", declared_peak_vram_mb=3200)
    )
    heuristic = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-c",
            required_model_size_mb=5000,
            resolution_width=1024,
            resolution_height=1024,
        )
    )
    unknown = build_workflow_memory_estimate(WorkflowMemoryEstimateRequest(workflow_id="workflow-d"))

    assert creator.source is RunnerMemoryEstimateSource.CREATOR_OBSERVED
    assert creator.confidence is RunnerMemoryEstimateConfidence.MEDIUM
    assert declared.source is RunnerMemoryEstimateSource.DECLARED
    assert declared.confidence is RunnerMemoryEstimateConfidence.MEDIUM
    assert heuristic.source is RunnerMemoryEstimateSource.HEURISTIC
    assert heuristic.confidence is RunnerMemoryEstimateConfidence.LOW
    assert unknown.source is RunnerMemoryEstimateSource.UNKNOWN
    assert unknown.confidence is RunnerMemoryEstimateConfidence.UNKNOWN
    assert unknown.conservative_memory_class is RunnerMemoryClass.GPU_HEAVY


def test_build_workflow_memory_estimate_marks_custom_node_uncertainty() -> None:
    creator = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-custom-creator",
            creator_observed_peak_vram_mb=6200,
            custom_node_count=1,
            custom_node_types=["ImpactWildcard"],
        )
    )
    declared = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-custom-declared",
            declared_peak_vram_mb=3200,
            custom_node_count=1,
            custom_node_types=["ImpactWildcard"],
        )
    )
    base_heuristic = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-base",
            required_model_size_mb=5000,
            resolution_width=1024,
            resolution_height=1024,
        )
    )
    custom_heuristic = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-custom-heuristic",
            required_model_size_mb=5000,
            resolution_width=1024,
            resolution_height=1024,
            custom_node_count=1,
            custom_node_types=["ImpactWildcard"],
        )
    )
    local = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-custom-local",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-custom-local",
                successful_runs=3,
                observed_peak_vram_mb=7200,
            ),
            custom_node_count=1,
            custom_node_types=["ImpactWildcard"],
        )
    )

    assert creator.confidence is RunnerMemoryEstimateConfidence.LOW
    assert declared.confidence is RunnerMemoryEstimateConfidence.LOW
    assert custom_heuristic.estimated_peak_vram_mb is not None
    assert base_heuristic.estimated_peak_vram_mb is not None
    assert custom_heuristic.estimated_peak_vram_mb > base_heuristic.estimated_peak_vram_mb
    assert local.confidence is RunnerMemoryEstimateConfidence.HIGH
    assert "custom_node_memory_uncertain" in creator.reasons
    assert "custom_node_memory_uncertain" in custom_heuristic.reasons
    assert "custom_node_memory_uncertain" in local.reasons
    assert creator.custom_node_count == 1
    assert creator.custom_node_types == ["ImpactWildcard"]


def test_build_workflow_memory_estimate_adjusts_heuristic_by_workflow_type() -> None:
    base = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-base",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            workflow_type="txt2img",
        )
    )
    controlnet = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-controlnet",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            workflow_type="controlnet",
        )
    )
    upscale = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-upscale",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            workflow_type="upscale",
        )
    )
    video = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-video",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            workflow_type="video",
        )
    )

    assert base.estimated_peak_vram_mb is not None
    assert controlnet.estimated_peak_vram_mb is not None
    assert upscale.estimated_peak_vram_mb is not None
    assert video.estimated_peak_vram_mb is not None
    assert controlnet.estimated_peak_vram_mb > base.estimated_peak_vram_mb
    assert video.estimated_peak_vram_mb > base.estimated_peak_vram_mb
    assert upscale.estimated_peak_vram_mb < base.estimated_peak_vram_mb


def test_build_workflow_memory_estimate_adjusts_heuristic_by_runtime_memory_options() -> None:
    base = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-base",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            precision="fp16",
            vram_mode="normal",
        )
    )
    fp32 = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-fp32",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            precision="float32",
            vram_mode="normal",
        )
    )
    high_vram = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-highvram",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            precision="fp16",
            vram_mode="high_vram",
        )
    )
    compact = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-compact",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            precision="fp8",
            vram_mode="lowvram",
        )
    )

    assert base.estimated_peak_vram_mb is not None
    assert fp32.estimated_peak_vram_mb is not None
    assert high_vram.estimated_peak_vram_mb is not None
    assert compact.estimated_peak_vram_mb is not None
    assert fp32.estimated_peak_vram_mb > base.estimated_peak_vram_mb
    assert high_vram.estimated_peak_vram_mb > base.estimated_peak_vram_mb
    assert compact.estimated_peak_vram_mb < base.estimated_peak_vram_mb
    assert fp32.precision == "fp32"
    assert fp32.vram_mode == "normal"
    assert "precision_memory_option" in fp32.reasons
    assert "vram_mode_memory_option" in fp32.reasons


def test_build_workflow_memory_estimate_adjusts_heuristic_by_model_and_lora_selections() -> None:
    base = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-base",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            selected_model_count=1,
            selected_model_kinds=["checkpoint"],
        )
    )
    enriched = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-enriched",
            required_model_size_mb=4000,
            resolution_width=1024,
            resolution_height=1024,
            selected_model_count=5,
            selected_model_kinds=[
                "checkpoint",
                "controlnet",
                "encoder",
                "ipadapter",
                "vae",
            ],
            lora_count=2,
            lora_strength_total=2.5,
        )
    )

    assert base.estimated_peak_vram_mb is not None
    assert enriched.estimated_peak_vram_mb is not None
    assert enriched.estimated_peak_vram_mb > base.estimated_peak_vram_mb
    assert enriched.selected_model_count == 5
    assert enriched.selected_model_kinds == [
        "checkpoint",
        "controlnet",
        "encoder",
        "ipadapter",
        "vae",
    ]
    assert enriched.lora_count == 2
    assert enriched.lora_strength_total == 2.5
    assert "selected_model_memory_heuristic" in enriched.reasons
    assert "lora_memory_heuristic" in enriched.reasons


def test_summarize_local_memory_observations_records_success_failure_and_peaks() -> None:
    summary = summarize_local_memory_observations(
        [
            LocalMemoryObservation(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                input_profile_fingerprint="settings-a",
                runner_id="runner-a",
                job_id="job-a",
                runner_root_pid=100,
                runner_child_pids=[101],
                sample_window=MemorySampleWindow.EXECUTION,
                outcome=MemoryObservationOutcome.SUCCESS,
                peak_vram_mb=7000,
                peak_ram_mb=3000,
                process_tree_peak_ram_mb=2600,
                backend_allocator_peak_vram_mb=6400,
                backend_allocator_details={"cuda": {"reserved_peak_bytes": 6710886400}},
                attribution_quality=MemoryAttributionQuality.PROCESS_TREE,
                attribution_sources=["process_tree_rss"],
                attribution_reasons=["runner_process_tree_rss"],
                observed_at="2026-05-03T10:00:00+00:00",
            ),
            LocalMemoryObservation(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                input_profile_fingerprint="settings-a",
                outcome=MemoryObservationOutcome.MEMORY_ERROR,
                peak_vram_mb=9200,
                system_peak_delta_vram_mb=9200,
                attribution_quality=MemoryAttributionQuality.SYSTEM_DELTA,
                attribution_sources=["system_memory_delta"],
                attribution_reasons=["system_vram_delta_active_job_window"],
                retry_required=True,
                eviction_required=True,
                observed_at="2026-05-03T11:00:00+00:00",
            ),
        ]
    )

    assert summary.successful_runs == 1
    assert summary.memory_error_runs == 1
    assert summary.observed_peak_vram_mb == 9200
    assert summary.observed_peak_ram_mb == 3000
    assert summary.process_tree_observed_peak_ram_mb == 2600
    assert summary.system_observed_peak_delta_vram_mb == 9200
    assert summary.backend_allocator_observed_peak_vram_mb == 6400
    assert summary.attribution_quality is MemoryAttributionQuality.PROCESS_TREE
    assert summary.attribution_sources == ["process_tree_rss", "system_memory_delta"]
    assert summary.evictions_required == 1
    assert summary.retries_required == 1
    assert summary.last_success_at == "2026-05-03T10:00:00+00:00"
    assert summary.last_memory_error_at == "2026-05-03T11:00:00+00:00"

    with pytest.raises(ValueError):
        summarize_local_memory_observations([])


def test_local_memory_learning_store_persists_machine_local_evidence(tmp_path) -> None:
    store = LocalMemoryLearningStore(tmp_path)
    first = store.record(
        LocalMemoryObservation(
            workflow_id="workflow-a",
            runner_process_compatibility_key="compat-a",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-a",
            process_compatibility_signature="sha256:process-a",
            model_residency_signature="sha256:model-a",
            execution_profile_signature="sha256:execution-a",
            runner_id="runner-a",
            job_id="job-a",
            runner_root_pid=100,
            runner_child_pids=[101],
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=6800,
            peak_ram_mb=2800,
            process_tree_peak_ram_mb=2400,
            backend_allocator_peak_vram_mb=6600,
            attribution_quality=MemoryAttributionQuality.PROCESS_TREE,
            attribution_sources=["process_tree_rss"],
            observed_at="2026-05-03T10:00:00+00:00",
        )
    )
    second = store.record(
        LocalMemoryObservation(
            workflow_id="workflow-a",
            runner_process_compatibility_key="compat-a",
            machine_profile_id="machine-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="settings-a",
            process_compatibility_signature="sha256:process-a",
            model_residency_signature="sha256:model-a",
            execution_profile_signature="sha256:execution-a",
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=7100,
            peak_ram_mb=3100,
            observed_at="2026-05-03T11:00:00+00:00",
        )
    )

    loaded = LocalMemoryLearningStore(tmp_path).summary_for(
        workflow_id="workflow-a",
        runner_process_compatibility_key="compat-a",
        machine_profile_id="machine-a",
        backend=MemoryBackend.CUDA,
        input_profile_fingerprint="settings-a",
    )

    assert first.successful_runs == 1
    assert second.successful_runs == 2
    assert second.has_repeated_success is True
    assert loaded is not None
    assert loaded.successful_runs == 2
    assert loaded.observed_peak_vram_mb == 7100
    assert loaded.process_compatibility_signature == "sha256:process-a"
    assert loaded.model_residency_signature == "sha256:model-a"
    assert loaded.execution_profile_signature == "sha256:execution-a"
    assert loaded.process_tree_observed_peak_ram_mb == 2400
    assert loaded.backend_allocator_observed_peak_vram_mb == 6600
    assert loaded.attribution_quality is MemoryAttributionQuality.PROCESS_TREE
    assert loaded.attribution_sources == ["process_tree_rss"]
    assert len(store.list_summaries()) == 1
    assert store.summary_for(workflow_id="workflow-a", backend=MemoryBackend.MPS) is None


def test_local_memory_learning_store_deduplicates_non_null_job_ids_across_buckets(tmp_path) -> None:
    store = LocalMemoryLearningStore(tmp_path)
    store.record(
        LocalMemoryObservation(
            workflow_id="workflow-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="small",
            job_id="job-a",
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=1000,
        )
    )
    duplicate = store.record(
        LocalMemoryObservation(
            workflow_id="workflow-a",
            backend=MemoryBackend.CUDA,
            input_profile_fingerprint="large",
            job_id="job-a",
            outcome=MemoryObservationOutcome.SUCCESS,
            peak_vram_mb=9000,
        )
    )

    assert duplicate.successful_runs == 1
    assert len(store.list_summaries()) == 1
    assert store.summary_for(
        workflow_id="workflow-a",
        backend=MemoryBackend.CUDA,
        input_profile_fingerprint="large",
    ) is None


def test_memory_admission_denies_heavy_heavy_without_large_gpu_local_confidence() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-heavy-b",
                RunnerMemoryClass.GPU_HEAVY,
                9000,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
            ),
            machine_snapshot=_machine(total_vram_mb=16_000, free_vram_mb=12_500),
            resident_runners=[
                _runner(
                    "runner-heavy-a",
                    RunnerMemoryClass.GPU_HEAVY,
                    confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                    source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
                    idle_vram_mb=7000,
                )
            ],
        )
    )

    assert decision.action is MemoryDecisionAction.EVICT_THEN_START
    assert decision.risk_level is MemoryRiskLevel.HIGH
    assert decision.reason_code == "heavy_heavy_requires_large_gpu_and_high_confidence"
    assert decision.evict_runner_ids == ["runner-heavy-a"]


def test_memory_admission_allows_heavy_light_with_margin() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-heavy",
                RunnerMemoryClass.GPU_HEAVY,
                9000,
                confidence=RunnerMemoryEstimateConfidence.HIGH,
            ),
            machine_snapshot=_machine(total_vram_mb=24_000, free_vram_mb=16_000),
            resident_runners=[
                _runner(
                    "runner-light",
                    RunnerMemoryClass.GPU_LIGHT,
                    confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                    idle_vram_mb=900,
                )
            ],
        )
    )

    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.reason_code == "co_residence_margin_available"
    assert decision.predicted_free_vram_after_mb == 7000
    assert decision.required_vram_margin_mb == 3600


def test_selected_core_runner_excluded_from_co_residence_peers() -> None:
    # core ran workflow-a (UNKNOWN class, never classified) and now runs a
    # different workflow with no useful overlap. The selected core runner is the
    # one the workflow will run on (reuse/replace), not a co-resident peer, so
    # its UNKNOWN class must not trigger unknown_memory_class_denies_co_residence.
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-b",
                RunnerMemoryClass.GPU_MEDIUM,
                5_000,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
                model_payload=_model_payload(models=[("checkpoint", "anima.safetensors")]),
            ),
            machine_snapshot=_machine(total_vram_mb=24_000, free_vram_mb=16_000),
            selected_runner=_runner(
                "core",
                RunnerMemoryClass.UNKNOWN,
                kind=RunnerKind.CORE_COMFYUI,
                confidence=RunnerMemoryEstimateConfidence.UNKNOWN,
                source=RunnerMemoryEstimateSource.UNKNOWN,
                status=RunnerStatus.IDLE_WARM,
                idle_vram_mb=6_000,
                model_payload=_model_payload(models=[("checkpoint", "chroma.safetensors")]),
            ),
            resident_runners=[],
        )
    )

    assert decision.reason_code != "unknown_memory_class_denies_co_residence"
    assert decision.action is not MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT


def test_memory_admission_denies_unknown_memory_class() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-unknown",
                RunnerMemoryClass.UNKNOWN,
                1500,
                confidence=RunnerMemoryEstimateConfidence.UNKNOWN,
                source=RunnerMemoryEstimateSource.UNKNOWN,
            ),
            machine_snapshot=_machine(total_vram_mb=24_000, free_vram_mb=20_000),
            resident_runners=[_runner("runner-light", RunnerMemoryClass.GPU_LIGHT)],
        )
    )

    assert decision.action is MemoryDecisionAction.EVICT_THEN_START
    assert decision.reason_code == "gpu_estimate_uncertain"
    assert decision.risk_level is MemoryRiskLevel.HIGH


def test_repeated_memory_failure_avoids_same_optimistic_admission() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                successful_runs=1,
                memory_error_runs=2,
                observed_peak_vram_mb=7200,
            ),
        )
    )
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            machine_snapshot=_machine(total_vram_mb=24_000, free_vram_mb=18_000),
            resident_runners=[],
        )
    )

    assert estimate.recent_memory_error is True
    assert estimate.confidence is RunnerMemoryEstimateConfidence.LOW
    assert decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert decision.reason_code == "recent_memory_error_requires_cleanup_before_retry"


def test_memory_admission_allows_large_gpu_high_confidence_heavy_pair() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-heavy-b",
                RunnerMemoryClass.GPU_HEAVY,
                9000,
                confidence=RunnerMemoryEstimateConfidence.HIGH,
                source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            ),
            machine_snapshot=_machine(total_vram_mb=32_000, free_vram_mb=18_000),
            resident_runners=[
                _runner(
                    "runner-heavy-a",
                    RunnerMemoryClass.GPU_HEAVY,
                    confidence=RunnerMemoryEstimateConfidence.HIGH,
                    source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
                    idle_vram_mb=8000,
                )
            ],
        )
    )

    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.risk_level is MemoryRiskLevel.MEDIUM
    assert decision.reason_code == "co_residence_margin_available"
    assert decision.evict_runner_ids == []


def test_memory_admission_eviction_for_memory_pressure_and_queue_for_active_runner() -> None:
    idle_decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate("workflow-light", RunnerMemoryClass.GPU_LIGHT, 1200),
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            resident_runners=[
                _runner("runner-big", RunnerMemoryClass.GPU_HEAVY, idle_vram_mb=6200),
                _runner("runner-small", RunnerMemoryClass.GPU_LIGHT, idle_vram_mb=800, lease_count=1),
            ],
        )
    )
    active_decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate("workflow-light", RunnerMemoryClass.GPU_LIGHT, 1200),
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            resident_runners=[
                _runner(
                    "runner-active",
                    RunnerMemoryClass.GPU_HEAVY,
                    status=RunnerStatus.RUNNING,
                    current_job_id="job-1",
                )
            ],
        )
    )

    assert idle_decision.action is MemoryDecisionAction.EVICT_THEN_START
    assert idle_decision.reason_code == "memory_pressure_high"
    assert idle_decision.evict_runner_ids == ["runner-big"]
    pressure = idle_decision.developer_details["residency_pressure"]
    assert pressure["selected_cleanup_modes"] == ["isolated_eviction"]
    assert pressure["selected_runner_ids"] == ["runner-big"]
    assert pressure["kept_warm_runner_ids"] == ["runner-small"]
    assert idle_decision.developer_details["memory_ownership"]["reclaimable_idle_runner_ids"] == [
        "runner-big",
        "runner-small",
    ]
    assert active_decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY
    assert active_decision.queued_behind_runner_id == "runner-active"
    assert active_decision.developer_details["memory_ownership"]["active_noofy_runner_ids"] == [
        "runner-active"
    ]


def test_memory_admission_queues_active_noofy_job_even_when_margin_is_available() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate("workflow-light", RunnerMemoryClass.GPU_LIGHT, 1200),
            machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=10_000),
            resident_runners=[
                _runner(
                    "runner-active",
                    RunnerMemoryClass.GPU_LIGHT,
                    status=RunnerStatus.RUNNING,
                    current_job_id="job-1",
                )
            ],
        )
    )

    assert decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY
    assert decision.reason_code == "active_noofy_job_queues_run"
    assert decision.queued_behind_runner_id == "runner-active"


def test_memory_admission_reuses_same_runner_model_residency_for_changed_execution_profile() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            declared_memory_class=RunnerMemoryClass.GPU_HEAVY,
            input_profile_fingerprint="settings-b",
            model_residency_signature="sha256:model-a",
            execution_profile_signature="sha256:execution-b",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                input_profile_fingerprint="settings-a",
                model_residency_signature="sha256:model-a",
                execution_profile_signature="sha256:execution-a",
                successful_runs=1,
                observed_peak_vram_mb=6000,
            ),
        )
    )
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=1000),
            selected_runner=RunnerMemorySnapshot(
                runner_id="core",
                status=RunnerStatus.IDLE,
                last_workflow_id="workflow-a",
                model_residency_signature="sha256:model-a",
                execution_profile_signature="sha256:execution-a",
                observed_idle_vram_mb=6000,
            ),
            resident_runners=[],
        )
    )

    assert "local_evidence_settings_mismatch" in estimate.reasons
    assert decision.action is MemoryDecisionAction.REUSE_RUNNER
    assert decision.reason_code == "same_runner_model_residency_reuse"
    assert decision.developer_details["execution_profile_changed"] is True
    ownership = decision.developer_details["memory_ownership"]
    assert ownership["same_warm_runner_id"] == "core"
    assert ownership["same_warm_runner_model_residency_signature"] == "sha256:model-a"


def test_memory_admission_reuses_same_runner_model_residency_even_under_high_pressure() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            declared_memory_class=RunnerMemoryClass.GPU_HEAVY,
            creator_observed_peak_vram_mb=6000,
            model_residency_signature="sha256:model-a",
            execution_profile_signature="sha256:execution-a",
        )
    )
    warm_core = RunnerMemorySnapshot(
        runner_id="core",
        kind=RunnerKind.CORE_COMFYUI,
        memory_class=RunnerMemoryClass.GPU_HEAVY,
        status=RunnerStatus.IDLE_WARM,
        last_workflow_id="workflow-a",
        model_residency_signature="sha256:model-a",
        execution_profile_signature="sha256:execution-a",
        observed_idle_vram_mb=6000,
    )

    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            selected_runner=warm_core,
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.REUSE_RUNNER
    assert decision.reason_code == "same_runner_model_residency_reuse"
    assert decision.evict_runner_ids == []
    assert decision.developer_details["same_runner_incremental_estimated_vram_mb"] == 0


def test_memory_admission_accounts_for_incremental_execution_growth_on_same_runner() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            declared_memory_class=RunnerMemoryClass.GPU_HEAVY,
            creator_observed_peak_vram_mb=10_000,
            model_residency_signature="sha256:model-a",
            execution_profile_signature="sha256:execution-large",
        )
    )
    warm_core = RunnerMemorySnapshot(
        runner_id="core",
        kind=RunnerKind.CORE_COMFYUI,
        memory_class=RunnerMemoryClass.GPU_HEAVY,
        status=RunnerStatus.IDLE,
        last_workflow_id="workflow-a",
        model_residency_signature="sha256:model-a",
        execution_profile_signature="sha256:execution-small",
        observed_idle_vram_mb=3000,
    )

    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=6000),
            selected_runner=warm_core,
            resident_runners=[warm_core],
        )
    )

    assert decision.action is not MemoryDecisionAction.REUSE_RUNNER


def test_memory_admission_does_not_reuse_runner_for_changed_model_residency() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            declared_memory_class=RunnerMemoryClass.GPU_HEAVY,
            input_profile_fingerprint="settings-b",
            model_residency_signature="sha256:model-b",
            execution_profile_signature="sha256:execution-a",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                input_profile_fingerprint="settings-a",
                model_residency_signature="sha256:model-a",
                execution_profile_signature="sha256:execution-a",
                successful_runs=1,
                observed_peak_vram_mb=6000,
            ),
        )
    )
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=1000),
            selected_runner=RunnerMemorySnapshot(
                runner_id="core",
                status=RunnerStatus.IDLE,
                last_workflow_id="workflow-a",
                model_residency_signature="sha256:model-a",
                execution_profile_signature="sha256:execution-a",
                observed_idle_vram_mb=6000,
            ),
            resident_runners=[],
        )
    )

    assert decision.action is not MemoryDecisionAction.REUSE_RUNNER
    assert decision.developer_details["memory_ownership"]["same_warm_runner_id"] is None


def test_memory_admission_high_pressure_warm_runner_uses_cleanup_path() -> None:
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            declared_memory_class=RunnerMemoryClass.GPU_HEAVY,
            input_profile_fingerprint="settings-a",
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                input_profile_fingerprint="settings-a",
                successful_runs=1,
                observed_peak_vram_mb=6000,
            ),
        )
    )
    warm_core = RunnerMemorySnapshot(
        runner_id="core",
        kind=RunnerKind.CORE_COMFYUI,
        status=RunnerStatus.IDLE,
        last_workflow_id="workflow-a",
        observed_idle_vram_mb=5000,
    )
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            selected_runner=warm_core,
            resident_runners=[warm_core],
        )
    )

    assert decision.action is MemoryDecisionAction.EVICT_THEN_START
    assert decision.evict_runner_ids == ["core"]
    assert memory_user_status_for_decision(decision).state == "freeing_previous_models"


def test_memory_admission_preserves_useful_base_residency_when_lora_is_obsolete() -> None:
    requested_payload = _model_payload(
        models=[("checkpoint", "base.safetensors"), ("vae", "main.vae.safetensors")],
        loras=["new-style.safetensors"],
    )
    warm_base_with_old_lora = _runner(
        "runner-base",
        RunnerMemoryClass.GPU_HEAVY,
        idle_vram_mb=6000,
        model_payload=_model_payload(
            models=[("checkpoint", "base.safetensors"), ("vae", "main.vae.safetensors")],
            loras=["old-style.safetensors"],
        ),
    )
    unrelated = _runner(
        "runner-unrelated",
        RunnerMemoryClass.GPU_HEAVY,
        idle_vram_mb=5000,
        model_payload=_model_payload(models=[("checkpoint", "other.safetensors")]),
    )

    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=WorkflowMemoryEstimate(
                workflow_id="workflow-b",
                memory_class=RunnerMemoryClass.GPU_HEAVY,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
                estimated_peak_vram_mb=2500,
                model_residency_payload=requested_payload,
            ),
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            resident_runners=[warm_base_with_old_lora, unrelated],
        )
    )

    assert decision.action is MemoryDecisionAction.EVICT_THEN_START
    assert decision.evict_runner_ids == ["runner-unrelated"]
    pressure = decision.developer_details["residency_pressure"]
    candidates = {
        candidate["runner_id"]: candidate
        for candidate in pressure["candidate_scores"]
    }
    assert candidates["runner-base"]["selected_for_cleanup"] is False
    assert "same_checkpoint" in candidates["runner-base"]["reasons"]
    assert "same_vae" in candidates["runner-base"]["reasons"]
    assert "obsolete_lora_pressure_signal" in candidates["runner-base"]["reasons"]
    assert "per_lora_cleanup_unsupported" in candidates["runner-base"]["reasons"]
    assert candidates["runner-base"]["useful_overlap_score"] > candidates["runner-unrelated"]["useful_overlap_score"]
    assert pressure["precise_cleanup"]["per_lora_unload"] == "unsupported_by_current_adapter"


def test_same_runner_lora_change_delegates_intra_runner_reuse_to_comfyui() -> None:
    requested_payload = _model_payload(
        models=[("checkpoint", "base.safetensors"), ("vae", "main.vae.safetensors")],
        loras=["new-style.safetensors"],
    )
    warm_runner = _runner(
        "core",
        RunnerMemoryClass.GPU_HEAVY,
        kind=RunnerKind.CORE_COMFYUI,
        status=RunnerStatus.IDLE_WARM,
        idle_vram_mb=6000,
        model_payload=_model_payload(
            models=[("checkpoint", "base.safetensors"), ("vae", "main.vae.safetensors")],
            loras=["old-style.safetensors"],
        ),
    )

    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=WorkflowMemoryEstimate(
                workflow_id="workflow-b",
                memory_class=RunnerMemoryClass.GPU_HEAVY,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
                estimated_peak_vram_mb=5000,
                model_residency_signature="sha256:new-lora",
                model_residency_payload=requested_payload,
            ),
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            selected_runner=warm_runner,
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.REUSE_RUNNER
    assert decision.reason_code == "same_runner_comfyui_managed_model_reuse"
    assert decision.evict_runner_ids == []
    details = decision.developer_details
    assert details["comfyui_delegated_intra_runner_model_reuse"] is True
    assert details["same_runner_model_residency_reuse"] is False
    assert details["useful_overlap_reasons"] == ["same_checkpoint", "same_vae"]
    assert details["custom_node_memory_uncertain"] is False


def test_same_runner_changed_model_without_useful_overlap_uses_cleanup_policy() -> None:
    requested_payload = _model_payload(
        models=[("checkpoint", "new-base.safetensors")],
        loras=["new-style.safetensors"],
    )
    warm_runner = _runner(
        "core",
        RunnerMemoryClass.GPU_HEAVY,
        kind=RunnerKind.CORE_COMFYUI,
        status=RunnerStatus.IDLE_WARM,
        idle_vram_mb=6000,
        model_payload=_model_payload(
            models=[("checkpoint", "old-base.safetensors")],
            loras=["old-style.safetensors"],
        ),
    )

    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=WorkflowMemoryEstimate(
                workflow_id="workflow-b",
                memory_class=RunnerMemoryClass.GPU_HEAVY,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
                estimated_peak_vram_mb=5000,
                model_residency_signature="sha256:new-base",
                model_residency_payload=requested_payload,
            ),
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            selected_runner=warm_runner,
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.EVICT_THEN_START
    assert decision.reason_code == "memory_pressure_high"
    assert decision.evict_runner_ids == ["core"]
    assert "same_runner_comfyui_managed_reuse" not in decision.developer_details
    pressure = decision.developer_details["residency_pressure"]
    assert pressure["selected_cleanup_modes"] == ["runner_free"]


def test_memory_admission_queued_demand_adds_reuse_value_before_cleanup() -> None:
    queued_payload = _model_payload(models=[("checkpoint", "queued.safetensors")])
    queued_needed = _runner(
        "runner-queued-needed",
        RunnerMemoryClass.GPU_HEAVY,
        idle_vram_mb=5000,
        model_payload=queued_payload,
    )
    low_reuse = _runner(
        "runner-low-reuse",
        RunnerMemoryClass.GPU_HEAVY,
        idle_vram_mb=5000,
        model_payload=_model_payload(models=[("checkpoint", "unused.safetensors")]),
    )

    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate("workflow-new", RunnerMemoryClass.GPU_HEAVY, 2500),
            machine_snapshot=_machine(
                total_vram_mb=12_000,
                free_vram_mb=500,
                memory_pressure=MemoryPressureLevel.HIGH,
            ),
            resident_runners=[queued_needed, low_reuse],
            queued_model_residency_payloads=[queued_payload],
        )
    )

    assert decision.evict_runner_ids == ["runner-low-reuse"]
    pressure = decision.developer_details["residency_pressure"]
    queued_candidate = next(
        candidate
        for candidate in pressure["candidate_scores"]
        if candidate["runner_id"] == "runner-queued-needed"
    )
    assert queued_candidate["selected_for_cleanup"] is False
    assert queued_candidate["queued_demand"] is True
    assert "queued_same_checkpoint" in queued_candidate["reasons"]


def test_eviction_candidates_excludes_reserved_submitting_and_waiting_release_runners() -> None:
    candidates = eviction_candidates(
        [
            _runner("runner-reserving", RunnerMemoryClass.GPU_HEAVY, status=RunnerStatus.RESERVING),
            _runner("runner-submitting", RunnerMemoryClass.GPU_HEAVY, status=RunnerStatus.SUBMITTING),
            _runner(
                "runner-waiting-release",
                RunnerMemoryClass.GPU_HEAVY,
                status=RunnerStatus.WAITING_FOR_MEMORY_RELEASE,
            ),
            _runner("runner-idle", RunnerMemoryClass.GPU_HEAVY, idle_vram_mb=3000),
        ]
    )

    assert [runner.runner_id for runner in candidates] == ["runner-idle"]


def test_memory_status_reports_isolated_runner_eviction_as_unloading_previous_workflow() -> None:
    decision = MemoryGovernorDecision(
        action=MemoryDecisionAction.EVICT_THEN_START,
        risk_level=MemoryRiskLevel.MEDIUM,
        reason_code="insufficient_margin_for_co_residence",
        workflow_id="workflow-b",
        evict_runner_ids=["isolated-a"],
        runner_snapshots=[
            RunnerMemorySnapshot(
                runner_id="isolated-a",
                kind=RunnerKind.ISOLATED_COMFYUI,
                status=RunnerStatus.IDLE,
                last_workflow_id="workflow-a",
            )
        ],
    )

    status = memory_user_status_for_decision(decision)

    assert status.state == "unloading_previous_workflow"


def test_memory_admission_cautious_starts_on_unattributed_pressure_with_advisory_estimate() -> None:
    # Creator-observed estimate, single runner, no Noofy-reclaimable memory, and
    # a low free margin from *unattributed* pressure (no external_process tag and
    # within physical capacity). The advisory estimate must not hard-block: Noofy
    # cautious-starts and lets ComfyUI try, while still reporting the ownership
    # accounting so the warning surface can explain the pressure.
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-heavy",
                RunnerMemoryClass.GPU_HEAVY,
                9000,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            ),
            machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=500),
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.reason_code == "insufficient_vram_margin_cautious_start"
    assert decision.developer_details["advisory_estimate_allowed_to_run"] is True
    ownership = decision.developer_details["memory_ownership"]
    assert ownership["free_vram_mb"] == 500
    assert ownership["reclaimable_idle_runner_ids"] == []
    assert ownership["active_noofy_runner_ids"] == []
    assert ownership["unattributed_or_external_used_vram_mb"] == 11_500
    assert memory_user_status_for_decision(decision).state == "memory_warning"


def test_memory_user_status_reports_external_pressure_only_with_explicit_evidence() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-heavy",
                RunnerMemoryClass.GPU_HEAVY,
                9_000,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            ),
            machine_snapshot=MachineMemorySnapshot(
                backend=MemoryBackend.CUDA,
                total_vram_mb=12_000,
                free_vram_mb=500,
                pressure_reasons=["external_process_vram_pressure"],
            ),
        )
    )

    assert decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert memory_user_status_for_decision(decision).state == "blocked_external_pressure"


def test_memory_user_status_reports_capacity_shortfall_separately() -> None:
    # Trusted local evidence whose peak alone exceeds total maps to the
    # capacity-shortfall state (distinct from unattributed/external pressure).
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-too-large",
                RunnerMemoryClass.GPU_HEAVY,
                20_000,
                source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            ),
            machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=11_000),
        )
    )

    assert decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert memory_user_status_for_decision(decision).state == "blocked_exceeds_capacity"


def test_mps_unified_memory_uses_ram_margin_instead_of_requiring_vram() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-mps",
                RunnerMemoryClass.GPU_HEAVY,
                6_000,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            ),
            machine_snapshot=MachineMemorySnapshot(
                backend=MemoryBackend.MPS,
                total_ram_mb=32_000,
                free_ram_mb=20_000,
                memory_pressure=MemoryPressureLevel.LOW,
            ),
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.required_vram_margin_mb == 0
    assert decision.required_ram_margin_mb == 8_000
    assert decision.predicted_free_ram_after_mb == 14_000


def test_cpu_backend_uses_vram_estimate_as_ram_pressure_proxy() -> None:
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-cpu",
                RunnerMemoryClass.GPU_HEAVY,
                12_000,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            ),
            machine_snapshot=MachineMemorySnapshot(
                backend=MemoryBackend.CPU,
                total_ram_mb=16_000,
                free_ram_mb=13_000,
                memory_pressure=MemoryPressureLevel.LOW,
            ),
            resident_runners=[],
        )
    )

    # The CPU backend still charges the VRAM estimate against RAM (proxy), so the
    # 12_000 estimate against 13_000 free RAM is a free-margin shortfall. With an
    # advisory creator estimate, a single runner, and capacity not exceeded
    # (12_000 + 2_048 < 16_000), Noofy cautious-starts instead of blocking, but
    # the proxy is still visible in the predicted free RAM.
    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.reason_code == "insufficient_ram_margin_cautious_start"
    assert decision.required_vram_margin_mb == 0
    assert decision.predicted_free_ram_after_mb == 1_000


def test_eviction_candidates_prefer_closed_view_runner_over_open_view_runner() -> None:
    # When memory is needed before the closed-view cooldown ends, the runner
    # whose workflow views are all closed is released first; a runner still
    # protected by an open workflow view lease ranks behind it.
    candidates = eviction_candidates(
        [
            _runner("runner-open-view", RunnerMemoryClass.GPU_HEAVY, idle_vram_mb=6000, lease_count=1),
            _runner("runner-closed-view", RunnerMemoryClass.GPU_HEAVY, idle_vram_mb=6000),
        ]
    )

    assert [runner.runner_id for runner in candidates] == [
        "runner-closed-view",
        "runner-open-view",
    ]


def test_eviction_candidates_prefers_idle_unused_large_runners() -> None:
    candidates = eviction_candidates(
        [
            _runner("runner-small-open", RunnerMemoryClass.GPU_LIGHT, idle_vram_mb=500, lease_count=1),
            _runner("runner-large-idle", RunnerMemoryClass.GPU_HEAVY, idle_vram_mb=7000),
            _runner("runner-active", RunnerMemoryClass.GPU_HEAVY, status=RunnerStatus.RUNNING, current_job_id="job"),
        ]
    )

    assert [runner.runner_id for runner in candidates] == ["runner-large-idle", "runner-small-open"]


def test_creator_observed_low_free_margin_single_runner_does_not_block() -> None:
    # Regression for the gemma txt2txt false positive: an A10G-class box, a
    # creator/export-time observation, first run (no local evidence), and a
    # momentarily low free margin in *both* VRAM and RAM. The workflow fits well
    # within physical capacity, so Noofy must cautious-start instead of returning
    # BLOCKED_BY_MEMORY and let ComfyUI try.
    estimate = WorkflowMemoryEstimate(
        workflow_id="txt2txt_gemma4-e4b-it",
        memory_class=RunnerMemoryClass.GPU_MEDIUM,
        confidence=RunnerMemoryEstimateConfidence.MEDIUM,
        source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
        estimated_peak_vram_mb=8_462,
        estimated_peak_ram_mb=5_615,
    )
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            # free_vram 9000 - 8462 = 538 < 3388 margin, and free_ram
            # 7000 - 5615 = 1385 < 2048 margin: a shortfall in both dimensions.
            machine_snapshot=_machine(
                total_vram_mb=22_590,
                free_vram_mb=9_000,
                total_ram_mb=15_783,
                free_ram_mb=7_000,
            ),
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.reason_code.endswith("_cautious_start")
    assert decision.developer_details["advisory_estimate_allowed_to_run"] is True
    assert decision.developer_details["estimate_source"] == "creator_observed"
    status = memory_user_status_for_decision(decision)
    assert status.state == "memory_warning"


def test_advisory_capacity_shortfall_cautious_starts_not_blocks() -> None:
    # The safety margin is a policy buffer, not proof. A creator/heuristic
    # estimate must not hard-block on capacity: neither ``peak + margin > total``
    # nor even ``peak > total`` blocks an advisory estimate, because we do not
    # trust an off-machine number's magnitude for this machine. ComfyUI is
    # allowed to try, and a real failure becomes local evidence.
    def _decide(peak_vram_mb: int) -> MemoryGovernorDecision:
        return decide_memory_admission(
            MemoryAdmissionRequest(
                workflow_estimate=_estimate(
                    "workflow-advisory",
                    RunnerMemoryClass.GPU_HEAVY,
                    peak_vram_mb,
                    confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                    source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
                ),
                machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=11_500),
                resident_runners=[],
            )
        )

    # peak + margin > total but peak < total
    margin_case = _decide(11_000)
    assert margin_case.action is MemoryDecisionAction.START_CO_RESIDENT
    assert margin_case.reason_code.endswith("_cautious_start")
    assert margin_case.developer_details["advisory_estimate_allowed_to_run"] is True

    # peak alone > total: still advisory, still cautious-start (untrusted magnitude)
    over_total_case = _decide(20_000)
    assert over_total_case.action is MemoryDecisionAction.START_CO_RESIDENT
    assert over_total_case.reason_code.endswith("_cautious_start")


def test_trusted_local_evidence_blocks_when_peak_exceeds_total() -> None:
    # Strong, this-machine evidence that the workflow cannot physically fit (the
    # estimated peak alone exceeds total VRAM) is the one capacity case that may
    # still hard-block, and it surfaces as a capacity shortfall.
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-too-large",
                RunnerMemoryClass.GPU_HEAVY,
                20_000,
                source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
            ),
            machine_snapshot=_machine(total_vram_mb=12_000, free_vram_mb=11_000),
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert decision.reason_code == "insufficient_vram_margin"
    assert memory_user_status_for_decision(decision).state == "blocked_exceeds_capacity"


def test_local_memory_failure_keeps_single_runner_conservative() -> None:
    # A recorded local memory failure for this profile keeps Noofy conservative:
    # a free-margin shortfall then hard-blocks. The identical shape without the
    # failure cautious-starts. (CPU backend exercises the RAM-pressure proxy and
    # reaches the shortfall path before the uncertain-estimate branch.)
    def _decide(recent_failure: bool) -> MemoryGovernorDecision:
        return decide_memory_admission(
            MemoryAdmissionRequest(
                workflow_estimate=WorkflowMemoryEstimate(
                    workflow_id="workflow-cpu",
                    memory_class=RunnerMemoryClass.CPU_ONLY,
                    confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                    source=RunnerMemoryEstimateSource.LOCAL_OBSERVED,
                    estimated_peak_ram_mb=12_000,
                    recent_memory_error=recent_failure,
                ),
                machine_snapshot=MachineMemorySnapshot(
                    backend=MemoryBackend.CPU,
                    total_ram_mb=16_000,
                    free_ram_mb=13_000,
                    memory_pressure=MemoryPressureLevel.LOW,
                ),
                resident_runners=[],
            )
        )

    blocked = _decide(recent_failure=True)
    assert blocked.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert blocked.reason_code == "insufficient_ram_margin"

    allowed = _decide(recent_failure=False)
    assert allowed.action is MemoryDecisionAction.START_CO_RESIDENT
    assert allowed.reason_code == "insufficient_ram_margin_cautious_start"


def test_local_success_overrides_creator_observation_and_avoids_block() -> None:
    # Two local successes on this machine produce a LOCAL_OBSERVED estimate that
    # overrides a larger creator observation, so a machine that would shortfall
    # against the creator peak comfortably fits the proven local peak and starts.
    estimate = build_workflow_memory_estimate(
        WorkflowMemoryEstimateRequest(
            workflow_id="workflow-a",
            creator_observed_peak_vram_mb=15_000,
            local_evidence=LocalMemoryEvidenceSummary(
                workflow_id="workflow-a",
                backend=MemoryBackend.CUDA,
                successful_runs=2,
                observed_peak_vram_mb=7_000,
            ),
        )
    )
    assert estimate.effective_source is RunnerMemoryEstimateSource.LOCAL_OBSERVED
    assert estimate.estimated_peak_vram_mb == 7_000

    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=estimate,
            machine_snapshot=_machine(total_vram_mb=16_000, free_vram_mb=12_000),
            resident_runners=[],
        )
    )

    assert decision.action is MemoryDecisionAction.START_CO_RESIDENT
    assert decision.reason_code in {"co_residence_margin_available", "no_resident_runners"}


def test_advisory_shortfall_still_blocks_when_idle_runner_cannot_be_evicted() -> None:
    # The cautious-start downgrade is scoped to the genuine single-runner case.
    # When another runner holds memory that cannot be reclaimed, heavy/heavy
    # co-residence stays protected and Noofy still blocks rather than risking the
    # machine by co-residing two heavy workflows.
    decision = decide_memory_admission(
        MemoryAdmissionRequest(
            workflow_estimate=_estimate(
                "workflow-heavy-b",
                RunnerMemoryClass.GPU_HEAVY,
                9_000,
                confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
            ),
            machine_snapshot=_machine(total_vram_mb=16_000, free_vram_mb=12_500),
            resident_runners=[
                _runner(
                    "runner-heavy-a",
                    RunnerMemoryClass.GPU_HEAVY,
                    confidence=RunnerMemoryEstimateConfidence.MEDIUM,
                    source=RunnerMemoryEstimateSource.CREATOR_OBSERVED,
                    idle_vram_mb=7_000,
                )
            ],
            runner_cleanup_capabilities={"runner-heavy-a": []},
        )
    )

    assert decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert decision.reason_code == "heavy_heavy_requires_large_gpu_and_high_confidence"


def _estimate(
    workflow_id: str,
    memory_class: RunnerMemoryClass,
    peak_vram_mb: int | None,
    *,
    confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.MEDIUM,
    source: RunnerMemoryEstimateSource = RunnerMemoryEstimateSource.LOCAL_OBSERVED,
    model_payload: dict[str, object] | None = None,
) -> WorkflowMemoryEstimate:
    return WorkflowMemoryEstimate(
        workflow_id=workflow_id,
        memory_class=memory_class,
        confidence=confidence,
        source=source,
        estimated_peak_vram_mb=peak_vram_mb,
        model_residency_payload=dict(model_payload or {}),
    )


def _runner(
    runner_id: str,
    memory_class: RunnerMemoryClass,
    *,
    kind: RunnerKind = RunnerKind.ISOLATED_COMFYUI,
    confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.MEDIUM,
    source: RunnerMemoryEstimateSource = RunnerMemoryEstimateSource.LOCAL_OBSERVED,
    status: RunnerStatus = RunnerStatus.IDLE_WARM,
    current_job_id: str | None = None,
    idle_vram_mb: int | None = None,
    lease_count: int = 0,
    model_payload: dict[str, object] | None = None,
) -> RunnerMemorySnapshot:
    return RunnerMemorySnapshot(
        runner_id=runner_id,
        kind=kind,
        memory_class=memory_class,
        memory_estimate_confidence=confidence,
        memory_estimate_source=source,
        status=status,
        current_job_id=current_job_id,
        observed_idle_vram_mb=idle_vram_mb,
        open_workflow_lease_count=lease_count,
        model_residency_payload=dict(model_payload or {}),
    )


def _model_payload(
    *,
    models: list[tuple[str, str]] | None = None,
    loras: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "test",
        "selected_models": [
            {"kind": kind, "selection": selection}
            for kind, selection in (models or [])
        ],
        "selected_loras": [
            {
                "kind": "lora",
                "selection": selection,
                "strength_model": 1.0,
                "strength_clip": 1.0,
                "active": True,
            }
            for selection in (loras or [])
        ],
    }


def _machine(
    *,
    total_vram_mb: int,
    free_vram_mb: int,
    total_ram_mb: int = 64_000,
    free_ram_mb: int = 50_000,
    memory_pressure: MemoryPressureLevel = MemoryPressureLevel.LOW,
) -> MachineMemorySnapshot:
    return MachineMemorySnapshot(
        backend=MemoryBackend.CUDA,
        total_vram_mb=total_vram_mb,
        free_vram_mb=free_vram_mb,
        total_ram_mb=total_ram_mb,
        free_ram_mb=free_ram_mb,
        memory_pressure=memory_pressure,
    )


class _SequenceMemoryObserver:
    def __init__(self, snapshots: list[MachineMemorySnapshot]) -> None:
        self.snapshots = snapshots
        self.index = 0

    def snapshot(self) -> MachineMemorySnapshot:
        snapshot = self.snapshots[min(self.index, len(self.snapshots) - 1)]
        self.index += 1
        return snapshot
