"""Tests for WorkflowUserState persistence."""
from pathlib import Path

from app.workflows.user_state import UserStateService, WorkflowUserState


def test_get_returns_default_when_missing(tmp_path: Path) -> None:
    svc = UserStateService(tmp_path / "state")
    result = svc.get("wf-1")
    assert result.workflow_id == "wf-1"
    assert result.values == {}
    assert result.layout_overrides == {}
    assert result.presentation_overrides.action_bar is None
    assert result.output_preferences == {}


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
    from app.workflows.user_state import OutputPreference, UserStateLayoutOverride
    state = WorkflowUserState(
        workflow_id="wf-1",
        values={"prompt": "dog"},
        layout_overrides={"ctrl-1": UserStateLayoutOverride(x=0, y=0, w=4, h=2)},
        output_preferences={"result": OutputPreference(auto_save=True)},
    )
    svc.save(state)
    cleared = svc.clear_values("wf-1")
    assert cleared.values == {}
    assert "ctrl-1" in cleared.layout_overrides
    assert cleared.output_preferences["result"].auto_save is True


def test_clear_layout_empties_overrides_but_keeps_values(tmp_path: Path) -> None:
    svc = UserStateService(tmp_path / "state")
    from app.workflows.user_state import (
        OutputPreference,
        UserStateActionBarPosition,
        UserStateLayoutOverride,
        UserStatePresentationOverrides,
    )
    state = WorkflowUserState(
        workflow_id="wf-1",
        values={"seed": 42},
        layout_overrides={"ctrl-1": UserStateLayoutOverride(x=0, y=0, w=4, h=2)},
        presentation_overrides=UserStatePresentationOverrides(
            action_bar=UserStateActionBarPosition(x=12, y=18),
        ),
        output_preferences={"result": OutputPreference(auto_save=True)},
    )
    svc.save(state)
    cleared = svc.clear_layout("wf-1")
    assert cleared.layout_overrides == {}
    assert cleared.presentation_overrides.action_bar is None
    assert cleared.values == {"seed": 42}
    assert cleared.output_preferences["result"].auto_save is True


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


def test_save_sanitizes_schema_api_credential_values(tmp_path: Path) -> None:
    svc = UserStateService(tmp_path / "state")

    saved = svc.save(
        WorkflowUserState(
            workflow_id="wf-api",
            values={
                "prompt": "dog",
                "token_count": 77,
                "comfy_account_key": "raw-secret-should-not-persist",
                "credential_control": {
                    "kind": "api_key_ref",
                    "provider": "comfy_org",
                    "secret_ref": "api-key:comfy_org",
                    "configured": True,
                    "last_four": "1234",
                    "raw": "raw-secret-should-not-persist",
                },
            },
        ),
        credential_input_ids={"comfy_account_key"},
    )

    text = (tmp_path / "state" / "wf-api.json").read_text(encoding="utf-8")
    assert "raw-secret-should-not-persist" not in text
    assert saved.values["prompt"] == "dog"
    assert saved.values["token_count"] == 77
    assert "comfy_account_key" not in saved.values
    assert saved.values["credential_control"] == {
        "kind": "api_key_ref",
        "provider": "comfy_org",
        "secret_ref": "api-key:comfy_org",
        "configured": True,
        "last_four": "1234",
    }
