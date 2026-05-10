from __future__ import annotations

import pytest

from app.runtime.comfyui_update_smoke import (
    assert_no_runtime_dirs_in_source,
    required_route_status_usable,
)


def test_required_route_status_usable_allows_missing_view_asset() -> None:
    assert required_route_status_usable("/system_stats", 200) is True
    assert required_route_status_usable("/system_stats", 404) is False
    assert required_route_status_usable("/view", 404) is True
    assert required_route_status_usable("/view", 405) is False
    assert required_route_status_usable("/view", 500) is False


def test_assert_no_runtime_dirs_in_source_rejects_runtime_state(tmp_path) -> None:
    (tmp_path / "main.py").write_text("", encoding="utf-8")
    (tmp_path / "models").mkdir()

    with pytest.raises(RuntimeError, match="models"):
        assert_no_runtime_dirs_in_source(tmp_path)
