from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


def system_ram_mb() -> tuple[int | None, int | None]:
    if os.name == "nt":
        return windows_system_ram_mb()
    if platform.system() == "Linux":
        linux = linux_system_ram_mb()
        if linux != (None, None):
            return linux
    if platform.system() == "Darwin":
        darwin = darwin_system_ram_mb()
        if darwin != (None, None):
            return darwin
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


def linux_system_ram_mb(
    reader: Callable[[], str] | None = None,
) -> tuple[int | None, int | None]:
    try:
        text = (
            reader()
            if reader is not None
            else Path("/proc/meminfo").read_text(encoding="utf-8")
        )
    except OSError:
        return None, None
    values_kb: dict[str, int] = {}
    for raw_line in text.splitlines():
        key, separator, rest = raw_line.partition(":")
        if separator != ":":
            continue
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            values_kb[key] = int(parts[0])
        except ValueError:
            continue
    total_kb = values_kb.get("MemTotal")
    available_kb = values_kb.get("MemAvailable")
    if available_kb is None:
        free_kb = values_kb.get("MemFree")
        if free_kb is not None:
            available_kb = (
                free_kb + values_kb.get("Buffers", 0) + values_kb.get("Cached", 0)
            )
    return (
        int(total_kb / 1024) if total_kb is not None else None,
        int(available_kb / 1024) if available_kb is not None else None,
    )


def darwin_system_ram_mb() -> tuple[int | None, int | None]:
    try:
        total_result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
        vm_result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            check=False,
            text=True,
            timeout=1,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None, None
    if total_result.returncode != 0 or vm_result.returncode != 0:
        return None, None
    total_bytes = coerce_int(total_result.stdout.strip())
    if total_bytes is None:
        return None, None
    free_bytes = parse_darwin_available_memory_bytes(vm_result.stdout)
    total_ram_mb = int(total_bytes / (1024 * 1024))
    free_ram_mb = int(free_bytes / (1024 * 1024)) if free_bytes is not None else None
    return total_ram_mb, free_ram_mb


def parse_darwin_available_memory_bytes(vm_stat_output: str) -> int | None:
    page_size = None
    page_counts: dict[str, int] = {}
    for raw_line in vm_stat_output.splitlines():
        line = raw_line.strip()
        if "page size of" in line:
            tokens = [
                token for token in line.replace(")", "").split() if token.isdigit()
            ]
            page_size = coerce_int(tokens[-1]) if tokens else None
            continue
        label, separator, value = line.partition(":")
        if separator != ":":
            continue
        count = coerce_int(value.strip().strip("."))
        if count is not None:
            page_counts[label.lower()] = count
    if page_size is None:
        return None
    available_pages = (
        page_counts.get("pages free", 0)
        + page_counts.get("pages inactive", 0)
        + page_counts.get("pages speculative", 0)
    )
    return page_size * available_pages


def windows_system_ram_mb() -> tuple[int | None, int | None]:
    try:
        import ctypes
    except ImportError:
        return None, None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    try:
        success = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return None, None
    if not success:
        return None, None
    total_ram_mb = int(status.ullTotalPhys / (1024 * 1024))
    free_ram_mb = int(status.ullAvailPhys / (1024 * 1024))
    return total_ram_mb, free_ram_mb


def coerce_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
