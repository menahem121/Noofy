from __future__ import annotations

import os
import platform
import re
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime

from app.engine.models import MachineResourceSnapshot, ResourceMetric
from app.runtime.memory.memory_governor import MachineMemorySnapshot

CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class SystemResourceObserver:
    """Compact machine resource observer for always-visible app chrome."""

    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
        platform_name: str | None = None,
        proc_stat_reader: Callable[[], str] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._command_runner = command_runner or self._run_command
        self._platform_name = platform_name or platform.system()
        self._proc_stat_reader = proc_stat_reader
        self._sleep = sleep

    def cpu_metric(self) -> ResourceMetric:
        if self._platform_name == "Linux":
            metric = self._linux_cpu_metric()
            if metric.available:
                return metric
        if self._platform_name == "Darwin":
            metric = self._darwin_cpu_metric()
            if metric.available:
                return metric
        if self._platform_name == "Windows":
            metric = self._windows_cpu_metric()
            if metric.available:
                return metric
        return self._load_average_cpu_metric()

    def _linux_cpu_metric(self) -> ResourceMetric:
        try:
            first = _parse_proc_stat_cpu(self._read_proc_stat())
            self._sleep(0.05)
            second = _parse_proc_stat_cpu(self._read_proc_stat())
        except OSError as exc:
            return _unavailable_metric("proc_stat", f"proc_stat_error:{exc}")
        if first is None or second is None:
            return _unavailable_metric("proc_stat", "proc_stat_parse_failed")
        first_idle, first_total = first
        second_idle, second_total = second
        total_delta = second_total - first_total
        idle_delta = second_idle - first_idle
        if total_delta <= 0:
            return _unavailable_metric("proc_stat", "proc_stat_no_delta")
        percent = ((total_delta - idle_delta) / total_delta) * 100
        return ResourceMetric(available=True, percent=_clamp_percent(percent), source="proc_stat")

    def _darwin_cpu_metric(self) -> ResourceMetric:
        try:
            result = self._command_runner(["top", "-l", "1", "-n", "0"])
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return _unavailable_metric("top", f"top_error:{exc}")
        if result.returncode != 0:
            return _unavailable_metric("top", (result.stderr or "top_failed").strip())
        match = re.search(r"CPU usage:\s*([\d.]+)% user,\s*([\d.]+)% sys", result.stdout)
        if match is None:
            return _unavailable_metric("top", "top_cpu_parse_failed")
        percent = float(match.group(1)) + float(match.group(2))
        return ResourceMetric(available=True, percent=_clamp_percent(percent), source="top")

    def _windows_cpu_metric(self) -> ResourceMetric:
        script = "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"
        try:
            result = self._command_runner(["powershell", "-NoProfile", "-Command", script])
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            return _unavailable_metric("windows_processor", f"windows_cpu_error:{exc}")
        if result.returncode != 0:
            return _unavailable_metric("windows_processor", (result.stderr or "windows_cpu_failed").strip())
        try:
            percent = float(result.stdout.strip())
        except ValueError:
            return _unavailable_metric("windows_processor", "windows_cpu_parse_failed")
        return ResourceMetric(available=True, percent=_clamp_percent(percent), source="windows_processor")

    def _load_average_cpu_metric(self) -> ResourceMetric:
        try:
            load_1m = os.getloadavg()[0]
        except (AttributeError, OSError) as exc:
            return _unavailable_metric("load_average", f"load_average_error:{exc}")
        cpu_count = os.cpu_count() or 1
        percent = (load_1m / cpu_count) * 100
        return ResourceMetric(available=True, percent=_clamp_percent(percent), source="load_average")

    def _read_proc_stat(self) -> str:
        if self._proc_stat_reader is not None:
            return self._proc_stat_reader()
        with open("/proc/stat", encoding="utf-8") as file:
            return file.read()

    def _run_command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )


def build_resource_snapshot(
    memory_snapshot: MachineMemorySnapshot,
    *,
    cpu_metric: ResourceMetric,
) -> MachineResourceSnapshot:
    return MachineResourceSnapshot(
        observed_at=memory_snapshot.observed_at or _now_iso(),
        cpu=cpu_metric,
        ram=_memory_metric(
            total_mb=memory_snapshot.total_ram_mb,
            free_mb=memory_snapshot.free_ram_mb,
            source=_first_source(memory_snapshot.signal_sources, fallback="system_memory"),
            error=None if memory_snapshot.total_ram_mb is not None or memory_snapshot.free_ram_mb is not None else memory_snapshot.error,
        ),
        vram=_memory_metric(
            total_mb=memory_snapshot.total_vram_mb,
            free_mb=memory_snapshot.free_vram_mb,
            source=_first_gpu_source(memory_snapshot.signal_sources),
            error=None if memory_snapshot.total_vram_mb is not None or memory_snapshot.free_vram_mb is not None else "vram_unavailable",
        ),
        backend=str(memory_snapshot.backend),
        device_name=memory_snapshot.device_name,
        memory_pressure=str(memory_snapshot.memory_pressure),
    )


def _memory_metric(
    *,
    total_mb: int | None,
    free_mb: int | None,
    source: str | None,
    error: str | None,
) -> ResourceMetric:
    used_mb = total_mb - free_mb if total_mb is not None and free_mb is not None else None
    percent = (used_mb / total_mb) * 100 if used_mb is not None and total_mb else None
    return ResourceMetric(
        available=total_mb is not None or free_mb is not None,
        percent=_clamp_percent(percent) if percent is not None else None,
        used_mb=used_mb,
        total_mb=total_mb,
        free_mb=free_mb,
        source=source,
        error=error,
    )


def _parse_proc_stat_cpu(text: str) -> tuple[int, int] | None:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    parts = first_line.split()
    if not parts or parts[0] != "cpu" or len(parts) < 5:
        return None
    try:
        values = [int(value) for value in parts[1:]]
    except ValueError:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def _first_source(sources: list[str], *, fallback: str) -> str:
    return sources[0] if sources else fallback


def _first_gpu_source(sources: list[str]) -> str | None:
    for source in sources:
        if source in {"nvml", "nvidia_smi", "windows_gpu_counters", "win32_video_controller"}:
            return source
    return None


def _unavailable_metric(source: str, error: str) -> ResourceMetric:
    return ResourceMetric(available=False, source=source, error=error)


def _clamp_percent(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
