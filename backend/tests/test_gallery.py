from __future__ import annotations

import base64
import json
import sqlite3
import shutil
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.engine.models import EngineOutputStream, JobResult
from app.gallery import (
    CapturedGalleryOutput,
    GalleryCaptureService,
    GalleryInputSnapshot,
    GalleryOutputWidgetSnapshot,
    GalleryStore,
    OutputPreference,
    RunSubmissionSnapshot,
    _matching_output_items,
    build_run_submission_snapshot,
)
from app.main import create_app
from app.runs.job_service import _RunnerLeasedOutputBody
from app.workflows.package import (
    DashboardControl,
    DashboardSchema,
    DashboardSection,
    InputBinding,
    WorkflowInput,
    WorkflowMetadata,
    WorkflowOutput,
    WorkflowPackage,
)

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@pytest.mark.parametrize(
    ("kind", "filename", "mime_type", "data"),
    [
        ("image", "output.png", "image/png", PNG_1X1),
        ("video", "clip.webm", "video/webm", b"\x1aE\xdf\xa3video"),
        ("audio", "speech.wav", "audio/wav", b"RIFF\x00\x00\x00\x00WAVE"),
        ("file", "captions.srt", "application/x-subrip", b"1\n00:00:00,000 --> 00:00:01,000\nHello\n"),
    ],
)
def test_gallery_store_persists_mixed_media(tmp_path: Path, kind: str, filename: str, mime_type: str, data: bytes) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_staged_output(_captured_output(store, kind=kind, filename=filename, mime_type=mime_type, data=data))

    assert item.kind == kind
    assert item.type == kind
    assert item.content_rel_path.startswith("media/")
    assert item.filename == filename
    assert item.size_bytes == len(data)
    assert store.content_path(item.id).read_bytes() == data
    assert (item.thumbnail_rel_path is not None) is (kind == "image")
    assert "content_rel_path" not in item.model_dump(mode="json")
    assert "thumbnail_rel_path" not in item.model_dump(mode="json")


def test_gallery_matching_prefers_unambiguous_dashboard_output_kind() -> None:
    widget = GalleryOutputWidgetSnapshot(
        control_id="result", output_id="video", node_id="9", widget_title="Result", media_kind="video"
    )
    result = JobResult(job_id="job", status="completed", outputs=[
        {"node_id": "9", "output": {"images": [{"filename": "upstream-preview.bin"}]}}
    ])

    assert _matching_output_items(result, widget) == []
    assert _matching_output_items(result, widget, prefer_declared_kind=True) == [
        ("images", {"filename": "upstream-preview.bin"})
    ]


def test_gallery_store_is_idempotent_and_tracks_favorite(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    first = store.save_staged_output(_captured_output(store, idempotency_key="same"))
    second = store.save_staged_output(_captured_output(store, idempotency_key="same"))

    assert second.id == first.id
    assert store.list_items().total == 1
    assert store.set_favorite(first.id, True).favorite is True


def test_gallery_store_migrates_legacy_images_transactionally(tmp_path: Path) -> None:
    root = tmp_path / "gallery"
    (root / "images").mkdir(parents=True)
    (root / "thumbnails").mkdir()
    (root / "images" / "old.png").write_bytes(PNG_1X1)
    conn = sqlite3.connect(root / "gallery.db")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (1)")
    conn.execute(
        """CREATE TABLE gallery_items (
        id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
        workflow_id TEXT NOT NULL, workflow_title TEXT NOT NULL, job_id TEXT NOT NULL,
        control_id TEXT NOT NULL, output_id TEXT NOT NULL, node_id TEXT NOT NULL,
        widget_title TEXT NOT NULL, image_rel_path TEXT NOT NULL, thumbnail_rel_path TEXT,
        mime_type TEXT, width INTEGER, height INTEGER, favorite INTEGER NOT NULL DEFAULT 0,
        file_state TEXT NOT NULL DEFAULT 'available', generation_settings_json TEXT NOT NULL,
        technical_metadata_json TEXT, schema_version INTEGER NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO gallery_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, 'available', '{}', '{}', 1)",
        ("legacy", "legacy-key", "2026-05-01T00:00:00+00:00", "wf", "Workflow", "job", "control", "output", "9", "Result", "images/old.png", "image/png", 1, 1),
    )
    conn.commit()
    conn.close()

    item = GalleryStore(root).get_item("legacy")
    assert item is not None
    assert item.kind == "image"
    assert item.content_rel_path == "images/old.png"
    assert item.filename == "old.png"


@pytest.mark.anyio
async def test_gallery_capture_streams_enabled_mixed_media_and_reuses_saved_item(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    calls: list[str] = []

    async def stream_output(job_id: str, filename: str, subfolder: str, output_type: str, range_header: str | None):
        calls.append(filename)

        async def body():
            yield b"RIFF"
            yield b"\x00\x00\x00\x00WAVE"

        return EngineOutputStream(body=body(), media_type="audio/wav", headers={"content-length": "12"})

    capture = GalleryCaptureService(store, stream_output=stream_output)
    snapshot = _snapshot("audio")
    result = JobResult(job_id="job-audio", status="completed", outputs=[
        {"node_id": "9", "output": {"audio": [{"filename": "speech.wav", "kind": "audio", "output_type": "output"}]}}
    ])
    capture.register_completed_run(result, snapshot)
    capture.schedule_output_save(result.job_id, "result", result=result)
    await _wait_for_request(capture, result.job_id, "result")

    first = capture.job_status(result.job_id).outputs[0]
    assert first.status == "saved"
    assert len(first.item_ids) == 1
    assert store.get_item(first.item_ids[0]).kind == "audio"
    capture.schedule_output_save(result.job_id, "result", result=result)
    assert calls == ["speech.wav"]


@pytest.mark.anyio
async def test_gallery_capture_marks_expired_output_unavailable(tmp_path: Path) -> None:
    async def missing_result(job_id: str):
        return JobResult(job_id=job_id, status="unknown", outputs=[])

    capture = GalleryCaptureService(GalleryStore(tmp_path / "gallery"), resolve_result=missing_result)
    result = JobResult(job_id="job-expired", status="completed", outputs=[])
    capture.register_completed_run(result, _snapshot("file"))
    capture.schedule_output_save(result.job_id, "result")
    request = await _wait_for_request(capture, result.job_id, "result")
    assert request.status == "unavailable"


@pytest.mark.anyio
async def test_gallery_capture_closes_stream_when_disk_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = False

    class Body:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise AssertionError("Disk failure must stop before streaming.")

        async def aclose(self):
            nonlocal closed
            closed = True

    async def stream_output(job_id: str, filename: str, subfolder: str, output_type: str, range_header: str | None):
        return EngineOutputStream(body=Body(), media_type="audio/wav", headers={"content-length": "12"})

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    capture = GalleryCaptureService(GalleryStore(tmp_path / "gallery"), stream_output=stream_output)
    result = JobResult(job_id="job-disk-full", status="completed", outputs=[
        {"node_id": "9", "output": {"audio": [{"filename": "speech.wav", "kind": "audio"}]}}
    ])
    capture.register_completed_run(result, _snapshot("audio"))
    capture.schedule_output_save(result.job_id, "result", result=result)
    request = await _wait_for_request(capture, result.job_id, "result")
    assert request.status == "failed"
    assert closed is True


@pytest.mark.anyio
async def test_gallery_capture_checks_disk_before_writing_unknown_size_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = False

    async def body():
        nonlocal closed
        try:
            yield b"first chunk"
        finally:
            closed = True

    async def stream_output(job_id: str, filename: str, subfolder: str, output_type: str, range_header: str | None):
        return EngineOutputStream(body=body(), media_type="audio/wav", headers={})

    monkeypatch.setattr(shutil, "disk_usage", lambda _path: SimpleNamespace(free=0))
    store = GalleryStore(tmp_path / "gallery")
    capture = GalleryCaptureService(store, stream_output=stream_output)
    result = JobResult(job_id="job-unknown-size-disk-full", status="completed", outputs=[
        {"node_id": "9", "output": {"audio": [{"filename": "speech.wav", "kind": "audio"}]}}
    ])
    capture.register_completed_run(result, _snapshot("audio"))
    capture.schedule_output_save(result.job_id, "result", result=result)
    request = await _wait_for_request(capture, result.job_id, "result")
    assert request.status == "failed"
    assert closed is True
    assert not list(store.media_dir.glob("*.tmp"))


@pytest.mark.anyio
async def test_output_stream_lease_releases_even_before_iteration() -> None:
    released: list[bool] = []

    async def body():
        yield b"unused"

    leased = _RunnerLeasedOutputBody(body(), release=lambda: released.append(True))
    await leased.aclose()
    assert released == [True]


def test_gallery_content_api_serves_backend_owned_download(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_staged_output(_captured_output(store, kind="file", filename="captions.srt", mime_type="application/x-subrip", data=b"hello"))
    with TestClient(create_app(engine_service=_FakeEngineService(), gallery_store=store)) as client:
        response = client.get(f"/api/gallery/{item.id}/content?download=true")
        ranged = client.get(f"/api/gallery/{item.id}/content", headers={"range": "bytes=1-3"})
    assert response.status_code == 200
    assert response.content == b"hello"
    assert "captions.srt" in response.headers["content-disposition"]
    assert ranged.status_code == 206
    assert ranged.content == b"ell"


def test_gallery_content_api_accepts_query_token_for_native_media(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_staged_output(_captured_output(store, kind="audio", filename="speech.wav", mime_type="audio/wav", data=b"RIFF"))
    with TestClient(create_app(engine_service=_FakeEngineService(), gallery_store=store)) as client:
        missing = client.get(f"/api/gallery/{item.id}/content")
        allowed = client.get(f"/api/gallery/{item.id}/content?token=secret-token")
    assert missing.status_code == 401
    assert allowed.status_code == 200


def test_gallery_delete_rolls_back_when_file_staging_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_staged_output(_captured_output(store))
    content_path = store.content_path(item.id)
    original_replace = Path.replace

    def fail_replace(self: Path, target: Path):
        if self == content_path:
            raise OSError("permission denied")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(ValueError):
        store.delete_item(item.id)
    assert store.get_item(item.id) is not None
    assert content_path.exists()


def test_gallery_save_cleans_image_temps_when_filename_allocation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = GalleryStore(tmp_path / "gallery")
    captured = _captured_output(store)

    def fail_filename(*_args):
        raise ValueError("full")

    monkeypatch.setattr(store, "_unique_filename", fail_filename)

    with pytest.raises(ValueError):
        store.save_staged_output(captured)

    assert not list(store.media_dir.glob("*.tmp"))
    assert not list(store.thumbnails_dir.glob("*.tmp"))


def test_gallery_delete_marks_saved_request_retryable(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_staged_output(_captured_output(store))
    store.put_save_request("job", "result", "saved", item_ids=[item.id])

    store.delete_item(item.id)

    request = store.save_request("job", "result")
    assert request is not None
    assert request.status == "interrupted"
    assert request.item_ids == []


def test_build_snapshot_redacts_secrets_and_preserves_all_output_preferences() -> None:
    package = _privacy_package()
    snapshot = build_run_submission_snapshot(
        package=package,
        inputs={
            "prompt": "normal prompt",
            "image": "/Users/me/private/input.png",
            "settings": {"apiKey": "secret", "credential": "nested-secret"},
            "note": "Authorization: Bearer abc123",
            "credential": "raw-secret",
        },
        output_preferences_snapshot={"result-image": OutputPreference(auto_save=True), "result-file": OutputPreference(auto_save=True)},
    )
    assert snapshot.values["image"] == {"filename": "input.png", "redacted": "local_path"}
    assert snapshot.values["settings"]["apiKey"] == "[redacted]"
    assert snapshot.values["settings"]["credential"] == "[redacted]"
    assert snapshot.values["note"] == "Authorization: Bearer [redacted]"
    assert snapshot.values["credential"] == "[redacted]"
    assert next(item for item in snapshot.inputs if item.input_id == "credential").value == "[redacted]"
    assert set(snapshot.output_preferences) == {"result-image", "result-file"}


def test_gallery_favorite_api_rejects_non_boolean_payload(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_staged_output(_captured_output(store))
    with TestClient(create_app(engine_service=_FakeEngineService(), gallery_store=store)) as client:
        response = client.put(f"/api/gallery/{item.id}/favorite", json={"favorite": "false"})
    assert response.status_code == 422


async def _wait_for_request(capture: GalleryCaptureService, job_id: str, control_id: str):
    for _ in range(50):
        request = capture.store.save_request(job_id, control_id)
        if request and request.status not in {"queued", "saving"}:
            return request
        await __import__("asyncio").sleep(0.01)
    raise AssertionError("Gallery save did not finish")


def _captured_output(
    store: GalleryStore,
    *,
    idempotency_key: str = "job|result|0|file.png",
    kind: str = "image",
    filename: str = "output.png",
    mime_type: str = "image/png",
    data: bytes = PNG_1X1,
) -> CapturedGalleryOutput:
    staged = store.create_staging_path()
    staged.write_bytes(data)
    return CapturedGalleryOutput(
        idempotency_key=idempotency_key, created_at=datetime(2026, 5, 11, 18, 42, 10, tzinfo=UTC),
        workflow_id="wf", workflow_title="Workflow", job_id="job", control_id="result",
        output_id=kind, node_id="9", widget_title="Result", kind=kind, staged_path=staged,
        source_filename=filename, source_mime_type=mime_type, extension=Path(filename).suffix,
        size_bytes=len(data), width=None, height=None, duration_seconds=None, fps=None,
        generation_settings={"settings": {"Prompt": "test"}},
    )


def _snapshot(kind: str) -> RunSubmissionSnapshot:
    return RunSubmissionSnapshot(
        workflow_id="wf", workflow_title="Workflow", dashboard_version="1",
        output_preferences={"result": OutputPreference(auto_save=True)},
        output_widgets=[GalleryOutputWidgetSnapshot(control_id="result", output_id=kind, node_id="9", widget_title="Result", media_kind=kind)],
        inputs=[GalleryInputSnapshot(input_id="prompt", label="Prompt", control_type="textarea", value="test")],
    )


def _privacy_package() -> WorkflowPackage:
    outputs = [
        WorkflowOutput(id="image", label="Image", node_id="9", type="image", kind="image"),
        WorkflowOutput(id="file", label="File", node_id="10", type="file", kind="file"),
    ]
    return WorkflowPackage(
        metadata=WorkflowMetadata(id="wf", name="Privacy Workflow", version="1"), engine="comfyui", comfyui_graph={},
        inputs=[
            WorkflowInput(id="prompt", label="Prompt", control="textarea", binding=InputBinding(node_id="1", input_name="text")),
            WorkflowInput(id="image", label="Image", control="load_image", binding=InputBinding(node_id="2", input_name="image")),
            WorkflowInput(id="settings", label="Settings", control="string_field", binding=InputBinding(node_id="3", input_name="settings")),
            WorkflowInput(id="credential", label="API Key", control="api_credential", binding=InputBinding(node_id="4", input_name="api_key")),
        ],
        outputs=outputs,
        dashboard=DashboardSchema(version="1", status="configured", sections=[DashboardSection(id="main", title="Main", controls=[
            DashboardControl(id="prompt", type="textarea", label="Prompt", input_id="prompt"),
            DashboardControl(id="image", type="load_image", label="Image", input_id="image"),
            DashboardControl(id="settings", type="string_field", label="Settings", input_id="settings"),
            DashboardControl(id="credential", type="api_credential", label="API Key", input_id="credential"),
            DashboardControl(id="result-image", type="display_image", label="Image", output_id="image"),
            DashboardControl(id="result-file", type="display_file", label="File", output_id="file"),
        ])]),
    )


class _FakeEngineService:
    async def shutdown(self) -> None:
        return None
