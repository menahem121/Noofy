from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class AggregateDownloadSpeedTracker:
    """Estimate aggregate download throughput over a rolling time window."""

    def __init__(
        self,
        *,
        window_seconds: float = 5.0,
        minimum_sample_seconds: float = 0.5,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.window_seconds = window_seconds
        self.minimum_sample_seconds = minimum_sample_seconds
        self.clock = clock
        self._bytes_by_key: dict[str, int] = {}
        self._total_bytes = 0
        self._samples: deque[tuple[float, int]] = deque()

    def update(self, key: str, bytes_downloaded: int) -> float | None:
        previous = self._bytes_by_key.get(key, 0)
        if bytes_downloaded > previous:
            self._total_bytes += bytes_downloaded - previous
            self._bytes_by_key[key] = bytes_downloaded

        now = self.clock()
        self._samples.append((now, self._total_bytes))
        cutoff = now - self.window_seconds
        while len(self._samples) > 1 and self._samples[1][0] <= cutoff:
            self._samples.popleft()

        sample_time, sample_bytes = self._samples[0]
        elapsed = now - sample_time
        if elapsed < self.minimum_sample_seconds:
            return None
        return max(0.0, (self._total_bytes - sample_bytes) / elapsed)

    @property
    def total_bytes(self) -> int:
        return self._total_bytes
