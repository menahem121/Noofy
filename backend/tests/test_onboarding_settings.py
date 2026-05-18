from pathlib import Path

from fastapi.testclient import TestClient

from app.diagnostics import LogStore
from app.main import create_app
from app.settings.onboarding import (
    OnboardingSettingsService,
    OnboardingSettingsStore,
)


class FakeEngineService:
    async def shutdown(self) -> None:
        return None


def _service(tmp_path: Path, log_store: LogStore | None = None) -> OnboardingSettingsService:
    return OnboardingSettingsService(
        store=OnboardingSettingsStore(tmp_path / "settings" / "onboarding.json"),
        log_store=log_store,
    )


def test_onboarding_state_defaults_to_incomplete(tmp_path: Path) -> None:
    state = _service(tmp_path).state()

    assert state.schema_version == "1"
    assert state.completed is False
    assert state.completed_at is None


def test_onboarding_mark_complete_persists_timestamp(tmp_path: Path) -> None:
    service = _service(tmp_path)

    result = service.mark_complete()
    state = service.state()

    assert result.status == "completed"
    assert state.completed is True
    assert state.completed_at is not None
    assert service.mark_complete().status == "already_completed"


def test_onboarding_mark_complete_records_safe_diagnostic(tmp_path: Path) -> None:
    log_store = LogStore()
    service = _service(tmp_path, log_store)

    service.mark_complete()

    payload = log_store.list_events().model_dump(mode="json")
    assert payload["events"][-1]["source"] == "settings.onboarding"
    assert payload["events"][-1]["details"]["completed_at"]


def test_onboarding_routes_read_and_complete_state(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with TestClient(
        create_app(
            engine_service=FakeEngineService(),
            onboarding_service=service,
        )
    ) as client:
        initial = client.get("/api/settings/onboarding")
        completed = client.put("/api/settings/onboarding", json={})
        after = client.get("/api/settings/onboarding")

    assert initial.status_code == 200
    assert initial.json()["completed"] is False
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["onboarding"]["completed"] is True
    assert after.json()["completed"] is True
