from pathlib import Path

from app.runtime.install_state import (
    INSTALL_STATE_SCHEMA_VERSION,
    InstallStateStore,
    user_facing_install_message,
)
from app.runtime.isolation import InstallStatus, SmokeTestStatus


def test_get_or_create_persists_pending_record(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)

    state = store.get_or_create("phase3-fp")

    assert state.status is InstallStatus.PENDING
    assert state.smoke_test_status is SmokeTestStatus.NOT_RUN
    assert state.schema_version == INSTALL_STATE_SCHEMA_VERSION
    # Round-trips through disk:
    assert store.get("phase3-fp") is not None


def test_update_round_trip(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)
    store.get_or_create("phase3-fp")

    updated = store.update(
        "phase3-fp",
        status=InstallStatus.READY,
        installed_at="2026-04-30T12:00:00+00:00",
        smoke_test_status=SmokeTestStatus.PASSED,
        last_error=None,
    )

    assert updated.status is InstallStatus.READY
    assert updated.installed_at == "2026-04-30T12:00:00+00:00"
    assert updated.smoke_test_status is SmokeTestStatus.PASSED
    assert updated.last_error is None
    # Persisted on disk:
    reloaded = InstallStateStore(tmp_path).get("phase3-fp")
    assert reloaded is not None and reloaded.status is InstallStatus.READY


def test_update_distinguishes_unset_from_explicit_none(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)
    store.update("phase3-fp", status=InstallStatus.FAILED, last_error="boom")

    # Without passing last_error, it should remain "boom":
    after_status_change = store.update("phase3-fp", status=InstallStatus.PREPARING)
    assert after_status_change.last_error == "boom"

    # Passing last_error=None explicitly clears it:
    cleared = store.update("phase3-fp", status=InstallStatus.PREPARING, last_error=None)
    assert cleared.last_error is None


def test_failed_record_never_appears_as_ready(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)
    store.update(
        "phase3-fp",
        status=InstallStatus.FAILED,
        last_error="model verification failed",
    )

    state = store.get("phase3-fp")
    assert state is not None
    assert state.status is InstallStatus.FAILED
    assert state.last_error == "model verification failed"


def test_user_facing_install_message_covers_every_status() -> None:
    for status in InstallStatus:
        message = user_facing_install_message(status)
        assert message and not any(token in message.lower() for token in ("pip", "venv", "site-packages"))


def test_list_states_returns_persisted_records(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)
    store.update("alpha-fp", status=InstallStatus.READY)
    store.update("beta-fp", status=InstallStatus.PREPARING)

    fingerprints = {state.capsule_fingerprint for state in store.list_states()}

    assert fingerprints == {"alpha-fp", "beta-fp"}
