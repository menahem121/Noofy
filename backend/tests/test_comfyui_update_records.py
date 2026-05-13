from __future__ import annotations

from app.core.paths import resolve_paths
from app.runtime.comfyui.comfyui_update_records import (
    ComfyUIVersionRecordStore,
    LocalComfyUIVersionRecord,
)


def test_record_store_preserves_previous_active_on_switch(tmp_path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    store = ComfyUIVersionRecordStore(paths)
    first = LocalComfyUIVersionRecord(tag="v1", installed=True, active=True)
    second = LocalComfyUIVersionRecord(tag="v2", installed=True, active=True)

    store.write_active_record(first)
    store.write_active_record(second)

    assert store.active_record() == second
    assert store.previous_active_record() == first


def test_record_store_marks_only_selected_record_active(tmp_path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    store = ComfyUIVersionRecordStore(paths)
    store.write_records(
        {
            "v1": LocalComfyUIVersionRecord(tag="v1", active=True),
            "v2": LocalComfyUIVersionRecord(tag="v2", active=False),
        }
    )

    store.mark_active("v2")
    records = store.read_records()

    assert records["v1"].active is False
    assert records["v2"].active is True
