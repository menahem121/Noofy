import subprocess

from app.engine.models import ResourceMetric
from app.runtime.memory.memory_governor import MachineMemorySnapshot, MemoryBackend, MemoryPressureLevel
from app.runtime.memory.resource_monitor import SystemResourceObserver, build_resource_snapshot


def test_linux_cpu_metric_reads_proc_stat_delta() -> None:
    samples = iter(
        [
            "cpu  100 0 100 800 0 0 0 0 0 0\n",
            "cpu  120 0 130 850 0 0 0 0 0 0\n",
        ]
    )
    observer = SystemResourceObserver(
        platform_name="Linux",
        proc_stat_reader=lambda: next(samples),
        sleep=lambda _: None,
    )

    metric = observer.cpu_metric()

    assert metric.available is True
    assert metric.percent == 50.0
    assert metric.source == "proc_stat"


def test_darwin_cpu_metric_parses_top_output() -> None:
    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        assert command[:3] == ["top", "-l", "1"]
        return subprocess.CompletedProcess(command, 0, stdout="CPU usage: 12.5% user, 4.5% sys, 83.0% idle")

    observer = SystemResourceObserver(platform_name="Darwin", command_runner=runner)

    metric = observer.cpu_metric()

    assert metric.available is True
    assert metric.percent == 17.0
    assert metric.source == "top"


def test_resource_snapshot_adapts_memory_governor_snapshot() -> None:
    snapshot = build_resource_snapshot(
        MachineMemorySnapshot(
            backend=MemoryBackend.CUDA,
            device_name="Test GPU",
            total_ram_mb=32_768,
            free_ram_mb=21_504,
            total_vram_mb=12_288,
            free_vram_mb=6_144,
            memory_pressure=MemoryPressureLevel.LOW,
            signal_sources=["nvml", "system_ram"],
            observed_at="2026-05-08T10:00:00+00:00",
        ),
        cpu_metric=ResourceMetric(available=True, percent=23.0, source="test"),
    )

    assert snapshot.cpu.percent == 23.0
    assert snapshot.ram.used_mb == 11_264
    assert snapshot.ram.percent == 34.4
    assert snapshot.vram.used_mb == 6_144
    assert snapshot.vram.percent == 50.0
    assert snapshot.device_name == "Test GPU"
