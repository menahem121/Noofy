from app.engine.diagnostics import LogStore


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
