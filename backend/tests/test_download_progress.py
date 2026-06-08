from __future__ import annotations

import pytest

from app.models.download_progress import AggregateDownloadSpeedTracker


def test_aggregate_download_speed_tracks_parallel_progress_over_rolling_window() -> None:
    now = 0.0
    tracker = AggregateDownloadSpeedTracker(
        window_seconds=5.0,
        clock=lambda: now,
    )

    assert tracker.update("model-a", 0) is None

    now = 1.0
    assert tracker.update("model-a", 100) == pytest.approx(100.0)

    now = 2.0
    assert tracker.update("model-b", 300) == pytest.approx(200.0)

    now = 6.0
    assert tracker.update("model-a", 500) == pytest.approx(140.0)
    assert tracker.total_bytes == 800


def test_aggregate_download_speed_does_not_double_count_repeated_progress() -> None:
    now = 0.0
    tracker = AggregateDownloadSpeedTracker(clock=lambda: now)

    tracker.update("model", 100)
    now = 1.0
    speed = tracker.update("model", 100)

    assert speed == 0
    assert tracker.total_bytes == 100


def test_aggregate_download_speed_waits_for_stable_initial_sample() -> None:
    now = 0.0
    tracker = AggregateDownloadSpeedTracker(clock=lambda: now)

    tracker.update("model", 0)
    now = 0.1

    assert tracker.update("model", 100) is None
