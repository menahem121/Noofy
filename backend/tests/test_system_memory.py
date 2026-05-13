from __future__ import annotations

from app.runtime.memory.system_memory import (
    linux_system_ram_mb,
    parse_darwin_available_memory_bytes,
)


def test_linux_system_ram_uses_memavailable() -> None:
    total, available = linux_system_ram_mb(
        lambda: "\n".join(
            [
                "MemTotal:       65536000 kB",
                "MemFree:         1024000 kB",
                "MemAvailable:   32768000 kB",
                "Buffers:          512000 kB",
                "Cached:          4096000 kB",
            ]
        )
    )

    assert total == 64_000
    assert available == 32_000


def test_parse_darwin_available_memory_bytes_counts_available_pages() -> None:
    available = parse_darwin_available_memory_bytes(
        "\n".join(
            [
                "Mach Virtual Memory Statistics: (page size of 4096 bytes)",
                "Pages free:                               10.",
                "Pages active:                             20.",
                "Pages inactive:                           30.",
                "Pages speculative:                        40.",
            ]
        )
    )

    assert available == 80 * 4096
