import json
from pathlib import Path

from app.runtime.install_state import (
    INSTALL_STATE_SCHEMA_VERSION,
    InstallStateStore,
    user_facing_install_message,
)
from app.runtime.isolation import (
    AssetOwnership,
    InstalledModelReference,
    InstallStatus,
    ModelVerificationLevel,
    SmokeTestStatus,
)


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
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_catalog_version="0.1.0",
        dependency_env_fingerprint="sha256:" + ("a" * 64),
        runner_workspace_fingerprint="sha256:" + ("b" * 64),
        runner_process_compatibility_key="runner-key",
    )

    assert updated.status is InstallStatus.READY
    assert updated.installed_at == "2026-04-30T12:00:00+00:00"
    assert updated.smoke_test_status is SmokeTestStatus.PASSED
    assert updated.last_error is None
    assert updated.runtime_profile_variant_id == "darwin-arm64-mps-dev"
    assert updated.runtime_profile_manifest_hash == "sha256:" + ("9" * 64)
    assert updated.runtime_profile_catalog_version == "0.1.0"
    assert updated.dependency_env_fingerprint == "sha256:" + ("a" * 64)
    assert updated.runner_workspace_fingerprint == "sha256:" + ("b" * 64)
    assert updated.runner_process_compatibility_key == "runner-key"
    # Persisted on disk:
    reloaded = InstallStateStore(tmp_path).get("phase3-fp")
    assert reloaded is not None and reloaded.status is InstallStatus.READY
    assert reloaded.runtime_profile_variant_id == "darwin-arm64-mps-dev"


def test_update_distinguishes_unset_from_explicit_none(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)
    store.update("phase3-fp", status=InstallStatus.FAILED, last_error="boom")

    # Without passing last_error, it should remain "boom":
    after_status_change = store.update("phase3-fp", status=InstallStatus.PREPARING)
    assert after_status_change.last_error == "boom"

    # Passing last_error=None explicitly clears it:
    cleared = store.update("phase3-fp", status=InstallStatus.PREPARING, last_error=None)
    assert cleared.last_error is None


def test_update_round_trips_model_references(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)
    model_ref = InstalledModelReference(
        requirement_id="checkpoints/model.safetensors",
        comfyui_folder="checkpoints",
        filename="model.safetensors",
        sha256="sha256:" + ("c" * 64),
        size_bytes=456,
        verification_level=ModelVerificationLevel.SHA256_SIZE,
        asset_ownership=AssetOwnership.NOOFY_IMPORTED,
        store_ref="model-ref",
    )

    updated = store.update(
        "phase3-fp",
        status=InstallStatus.READY,
        model_references=[model_ref],
    )

    assert updated.model_references == [model_ref]
    reloaded = InstallStateStore(tmp_path).get("phase3-fp")
    assert reloaded is not None
    assert reloaded.model_references[0].asset_ownership is AssetOwnership.NOOFY_IMPORTED


def test_existing_install_state_without_model_references_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "phase3-fp.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": INSTALL_STATE_SCHEMA_VERSION,
                "capsule_fingerprint": "phase3-fp",
                "status": "ready",
                "smoke_test_status": "passed",
            }
        ),
        encoding="utf-8",
    )

    state = InstallStateStore(tmp_path).get("phase3-fp")

    assert state is not None
    assert state.model_references == []


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


def test_stale_temp_write_does_not_block_reads_and_can_be_swept(tmp_path: Path) -> None:
    store = InstallStateStore(tmp_path)
    state = store.update("phase3-fp", status=InstallStatus.READY)
    tmp_path_for_state = tmp_path / "phase3-fp.json.tmp"
    tmp_path_for_state.write_text("{not-valid-json", encoding="utf-8")

    reloaded = store.get("phase3-fp")
    removed = store.remove_stale_temp_files()

    assert reloaded is not None
    assert reloaded.status is state.status
    assert removed == 1
    assert not tmp_path_for_state.exists()
