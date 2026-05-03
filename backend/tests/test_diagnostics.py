from app.engine.diagnostics import LogStore
from app.engine.service import _diagnostic_event_payload


def test_log_store_filters_by_job_and_latest_error() -> None:
    store = LogStore()
    store.add("info", "global event", "test")
    store.add("error", "job failed", "test", job_id="job-1")
    store.add("warning", "other job warning", "test", job_id="job-2")

    job_logs = store.list_events(job_id="job-1")

    assert len(job_logs.events) == 1
    assert job_logs.events[0].message == "job failed"
    assert store.latest_error() is not None
    assert store.latest_error().job_id == "job-1"


def test_diagnostic_payload_redacts_secrets_and_hides_developer_details_by_default() -> None:
    store = LogStore()
    event = store.add(
        "error",
        "runner failed",
        "runtime.runner_process",
        job_id="job-1",
        workflow_id="workflow-1",
        details={
            "runner_id": "runner-1",
            "authorization": "Bearer secret-token",
            "nested": {"signed_url": "https://example.invalid/model?token=secret"},
        },
    )

    default_payload = _diagnostic_event_payload(event, include_developer_details=False)
    developer_payload = _diagnostic_event_payload(event, include_developer_details=True)

    assert default_payload["correlation_ids"] == {
        "workflow_id": "workflow-1",
        "job_id": "job-1",
        "runner_id": "runner-1",
    }
    assert "developer_details" not in default_payload
    assert developer_payload["developer_details"]["authorization"] == "[redacted]"
    assert developer_payload["developer_details"]["nested"]["signed_url"] == "[redacted]"
