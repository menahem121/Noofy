"""Tests for WorkflowUserState persistence."""
from pathlib import Path

from app.workflows.user_state import UserStateService, WorkflowUserState


def test_get_returns_default_when_missing(tmp_path: Path) -> None:
    svc = UserStateService(tmp_path / "state")
    result = svc.get("wf-1")
    assert result.workflow_id == "wf-1"
    assert result.values == {}
    assert result.layout_overrides == {}


def test_save_and_get_roundtrip(tmp_path: Path) -> None:
    svc = UserStateService(tmp_path / "state")
    state = WorkflowUserState(
        workflow_id="wf-1",
        dashboard_version="1.0",
        values={"prompt": "a cat"},
        layout_overrides={},
    )
    saved = svc.save(state)
    loaded = svc.get("wf-1")
    assert loaded.workflow_id == "wf-1"
    assert loaded.dashboard_version == "1.0"
    assert loaded.values == {"prompt": "a cat"}
    assert saved.values == loaded.values


def test_save_creates_directory(tmp_path: Path) -> None:
    state_dir = tmp_path / "nested" / "state"
    svc = UserStateService(state_dir)
    svc.save(WorkflowUserState(workflow_id="wf-x"))
    assert state_dir.exists()


def test_clear_values_empties_values_but_keeps_layout(tmp_path: Path) -> None:
    svc = UserStateService(tmp_path / "state")
    from app.workflows.user_state import UserStateLayoutOverride
    state = WorkflowUserState(
        workflow_id="wf-1",
        values={"prompt": "dog"},
        layout_overrides={"ctrl-1": UserStateLayoutOverride(x=0, y=0, w=4, h=2)},
    )
    svc.save(state)
    cleared = svc.clear_values("wf-1")
    assert cleared.values == {}
    assert "ctrl-1" in cleared.layout_overrides


def test_clear_layout_empties_overrides_but_keeps_values(tmp_path: Path) -> None:
    svc = UserStateService(tmp_path / "state")
    from app.workflows.user_state import UserStateLayoutOverride
    state = WorkflowUserState(
        workflow_id="wf-1",
        values={"seed": 42},
        layout_overrides={"ctrl-1": UserStateLayoutOverride(x=0, y=0, w=4, h=2)},
    )
    svc.save(state)
    cleared = svc.clear_layout("wf-1")
    assert cleared.layout_overrides == {}
    assert cleared.values == {"seed": 42}


def test_get_returns_default_on_corrupt_file(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "wf-bad.json").write_text("NOT JSON {{{")
    svc = UserStateService(state_dir)
    result = svc.get("wf-bad")
    assert result.workflow_id == "wf-bad"
    assert result.values == {}


def test_workflow_id_with_special_chars_is_safe(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    svc = UserStateService(state_dir)
    svc.save(WorkflowUserState(workflow_id="../../etc/passwd"))
    files = list(state_dir.iterdir())
    # All written files must be direct children of the state dir (no subdirs created).
    for f in files:
        assert "/" not in f.name
        assert f.parent == state_dir
