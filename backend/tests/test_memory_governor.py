import subprocess
import json

import pytest
from pydantic import ValidationError

from app.engine.diagnostics import LogStore
from app.runtime.memory_governor import (
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
    memory_release_satisfied,
    memory_pressure_from_free_ratio,
    preferred_memory_estimate,
    record_memory_governor_decision,
    retry_after_memory_cleanup_decision,
    summarize_local_memory_observations,
    wait_for_memory_release,
)
from app.runtime.supervisor import (
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
    )

    snapshot = RunnerMemorySnapshot.from_descriptor(descriptor)

    assert snapshot.runner_id == "runner-a"
    assert snapshot.status is RunnerStatus.IDLE_WARM
    assert snapshot.memory_class is RunnerMemoryClass.GPU_LIGHT
    assert snapshot.observed_idle_vram_mb == 900
    assert snapshot.observed_execution_peak_vram_mb == 2400
    assert snapshot.open_workflow_lease_count == 2
    assert snapshot.recent_memory_error_count == 1


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
            extra_field=True,
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


def test_nvidia_smi_memory_observer_parses_cuda_snapshot() -> None:
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


def test_windows_gpu_memory_observer_parses_directml_snapshot() -> None:
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

    assert base.estimated_peak_vram_mb is not None
    assert controlnet.estimated_peak_vram_mb is not None
    assert upscale.estimated_peak_vram_mb is not None
    assert controlnet.estimated_peak_vram_mb > base.estimated_peak_vram_mb
    assert upscale.estimated_peak_vram_mb < base.estimated_peak_vram_mb


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
    assert loaded.process_tree_observed_peak_ram_mb == 2400
    assert loaded.backend_allocator_observed_peak_vram_mb == 6600
    assert loaded.attribution_quality is MemoryAttributionQuality.PROCESS_TREE
    assert loaded.attribution_sources == ["process_tree_rss"]
    assert len(store.list_summaries()) == 1
    assert store.summary_for(workflow_id="workflow-a", backend=MemoryBackend.MPS) is None


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
    assert idle_decision.evict_runner_ids == ["runner-big", "runner-small"]
    assert active_decision.action is MemoryDecisionAction.QUEUE_PENDING_MEMORY
    assert active_decision.queued_behind_runner_id == "runner-active"


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

    assert decision.action is MemoryDecisionAction.BLOCKED_BY_MEMORY
    assert decision.reason_code == "insufficient_ram_margin"
    assert decision.required_vram_margin_mb == 0


def test_eviction_candidates_prefers_idle_unused_large_runners() -> None:
    candidates = eviction_candidates(
        [
            _runner("runner-small-open", RunnerMemoryClass.GPU_LIGHT, idle_vram_mb=500, lease_count=1),
            _runner("runner-large-idle", RunnerMemoryClass.GPU_HEAVY, idle_vram_mb=7000),
            _runner("runner-active", RunnerMemoryClass.GPU_HEAVY, status=RunnerStatus.RUNNING, current_job_id="job"),
        ]
    )

    assert [runner.runner_id for runner in candidates] == ["runner-large-idle", "runner-small-open"]


def _estimate(
    workflow_id: str,
    memory_class: RunnerMemoryClass,
    peak_vram_mb: int | None,
    *,
    confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.MEDIUM,
    source: RunnerMemoryEstimateSource = RunnerMemoryEstimateSource.LOCAL_OBSERVED,
) -> WorkflowMemoryEstimate:
    return WorkflowMemoryEstimate(
        workflow_id=workflow_id,
        memory_class=memory_class,
        confidence=confidence,
        source=source,
        estimated_peak_vram_mb=peak_vram_mb,
    )


def _runner(
    runner_id: str,
    memory_class: RunnerMemoryClass,
    *,
    confidence: RunnerMemoryEstimateConfidence = RunnerMemoryEstimateConfidence.MEDIUM,
    source: RunnerMemoryEstimateSource = RunnerMemoryEstimateSource.LOCAL_OBSERVED,
    status: RunnerStatus = RunnerStatus.IDLE_WARM,
    current_job_id: str | None = None,
    idle_vram_mb: int | None = None,
    lease_count: int = 0,
) -> RunnerMemorySnapshot:
    return RunnerMemorySnapshot(
        runner_id=runner_id,
        memory_class=memory_class,
        memory_estimate_confidence=confidence,
        memory_estimate_source=source,
        status=status,
        current_job_id=current_job_id,
        observed_idle_vram_mb=idle_vram_mb,
        open_workflow_lease_count=lease_count,
    )


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
