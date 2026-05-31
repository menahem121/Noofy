from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.engine.models import JobResult
from app.gallery import (
    GalleryCaptureService,
    GalleryStore,
    OutputPreference,
    RunSubmissionSnapshot,
    GalleryInputSnapshot,
    GalleryOutputWidgetSnapshot,
    build_run_submission_snapshot,
)
from app.main import create_app
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


def test_gallery_store_writes_flat_files_and_persists_favorite(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")

    item = store.save_image(
        _captured_image(
            idempotency_key="job|result|0|file.png",
            workflow_title="Portrait Generator",
            widget_title="Result",
        )
    )

    assert item.image_rel_path.startswith("images/")
    assert "/" not in Path(item.image_rel_path).name
    assert "portrait-generator" in Path(item.image_rel_path).name
    assert item.thumbnail_rel_path is not None
    assert item.thumbnail_rel_path.startswith("thumbnails/")
    assert store.image_path(item.id).exists()
    assert store.image_path(item.id, thumbnail=True).exists()

    updated = store.set_favorite(item.id, True)
    assert updated is not None
    assert updated.favorite is True


def test_gallery_store_is_idempotent(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")

    first = store.save_image(_captured_image(idempotency_key="same-key"))
    second = store.save_image(_captured_image(idempotency_key="same-key"))

    assert second.id == first.id
    assert store.list_items().total == 1


def test_gallery_store_marks_missing_file(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_image(_captured_image(idempotency_key="missing-key"))

    store.image_path(item.id).unlink()

    missing = store.get_item(item.id)
    assert missing is not None
    assert missing.file_state == "missing"


@pytest.mark.anyio
async def test_gallery_capture_saves_only_enabled_matching_outputs(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    capture = GalleryCaptureService(store)
    snapshot = RunSubmissionSnapshot(
        workflow_id="wf-1",
        workflow_title="Portrait Generator",
        dashboard_version="1",
        values={"prompt": "private prompt stays out of filename"},
        output_preferences={
            "result-on": OutputPreference(auto_save=True),
            "result-off": OutputPreference(auto_save=False),
        },
        output_widgets=[
            GalleryOutputWidgetSnapshot(
                control_id="result-on",
                output_id="image-on",
                node_id="9",
                widget_title="Result On",
            ),
            GalleryOutputWidgetSnapshot(
                control_id="result-off",
                output_id="image-off",
                node_id="10",
                widget_title="Result Off",
            ),
        ],
        inputs=[
            GalleryInputSnapshot(input_id="prompt", label="Prompt", control_type="textarea", value="test prompt"),
        ],
    )
    result = JobResult(
        job_id="job-1",
        status="completed",
        outputs=[
            {"node_id": "9", "output": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}},
            {"node_id": "10", "output": {"images": [{"filename": "b.png", "subfolder": "", "type": "output"}]}},
        ],
    )

    async def fetch_output(job_id: str, filename: str, subfolder: str, output_type: str):
        fetch_calls.append((job_id, filename, subfolder, output_type))
        return PNG_1X1, "image/png"

    fetch_calls = []
    saved = await capture.save_completed_job_outputs(result=result, snapshot=snapshot, fetch_output=fetch_output)

    async def fail_if_refetched(job_id: str, filename: str, subfolder: str, output_type: str):
        raise AssertionError("duplicate capture should return existing item without refetching source output")

    repeated = await capture.save_completed_job_outputs(result=result, snapshot=snapshot, fetch_output=fail_if_refetched)

    assert len(saved) == 1
    assert len(repeated) == 1
    assert store.list_items().total == 1
    assert fetch_calls == [("job-1", "a.png", "", "output")]
    item = saved[0]
    assert item.control_id == "result-on"
    assert "private-prompt" not in Path(item.image_rel_path).name
    assert item.generation_settings["settings"]["Prompt"] == "test prompt"


@pytest.mark.anyio
async def test_gallery_capture_skips_failed_jobs(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    capture = GalleryCaptureService(store)
    snapshot = RunSubmissionSnapshot(
        workflow_id="wf-1",
        workflow_title="Workflow",
        dashboard_version="1",
        output_preferences={"result": OutputPreference(auto_save=True)},
        output_widgets=[
            GalleryOutputWidgetSnapshot(
                control_id="result",
                output_id="image",
                node_id="9",
                widget_title="Result",
            )
        ],
    )

    async def fetch_output(job_id: str, filename: str, subfolder: str, output_type: str):
        raise AssertionError("fetch should not be called")

    saved = await capture.save_completed_job_outputs(
        result=JobResult(job_id="job-1", status="failed", outputs=[]),
        snapshot=snapshot,
        fetch_output=fetch_output,
    )

    assert saved == []
    assert store.list_items().total == 0


def test_build_run_submission_snapshot_redacts_sensitive_values_and_paths() -> None:
    package = _privacy_package()

    snapshot = build_run_submission_snapshot(
        package=package,
        inputs={
            "prompt": "a normal prompt",
            "image": "/Users/me/private/input.png",
            "settings": {
                "apiKey": "secret",
                "nested": {
                    "access_token": "also-secret",
                    "url": "https://example.test/file.png?token=abc&name=ok",
                },
            },
        },
        output_preferences_snapshot={"result": OutputPreference(auto_save=True)},
    )

    assert snapshot.values["prompt"] == "a normal prompt"
    assert snapshot.values["image"] == {"filename": "input.png", "redacted": "local_path"}
    assert snapshot.values["settings"]["apiKey"] == "[redacted]"
    assert snapshot.values["settings"]["nested"]["access_token"] == "[redacted]"
    assert snapshot.values["settings"]["nested"]["url"] == "https://example.test/file.png?token=%5Bredacted%5D&name=ok"
    assert snapshot.inputs[1].value == {"filename": "input.png", "redacted": "local_path"}


def test_build_run_submission_snapshot_preserves_audio_output_preferences() -> None:
    package = WorkflowPackage(
        metadata=WorkflowMetadata(id="wf-audio", name="Audio Workflow", version="1"),
        engine="comfyui",
        comfyui_graph={"12": {"class_type": "SaveAudio", "inputs": {}}},
        outputs=[WorkflowOutput(id="audio", label="Audio", node_id="12", type="audio", kind="audio")],
        dashboard=DashboardSchema(
            version="1",
            status="configured",
            sections=[
                DashboardSection(
                    id="main",
                    title="Main",
                    controls=[
                        DashboardControl(id="result-audio", type="display_audio", label="Audio", output_id="audio"),
                    ],
                )
            ],
        ),
    )

    snapshot = build_run_submission_snapshot(
        package=package,
        inputs={},
        output_preferences_snapshot={"result-audio": OutputPreference(auto_save=True)},
    )

    assert snapshot.output_preferences["result-audio"].auto_save is True
    assert snapshot.output_widgets[0].media_kind == "audio"


def test_build_run_submission_snapshot_preserves_video_output_preferences() -> None:
    package = WorkflowPackage(
        metadata=WorkflowMetadata(id="wf-video", name="Video Workflow", version="1"),
        engine="comfyui",
        comfyui_graph={"15": {"class_type": "SaveVideo", "inputs": {}}},
        outputs=[WorkflowOutput(id="video", label="Video", node_id="15", type="video", kind="video")],
        dashboard=DashboardSchema(
            version="1",
            status="configured",
            sections=[
                DashboardSection(
                    id="main",
                    title="Main",
                    controls=[
                        DashboardControl(id="result-video", type="display_video", label="Video", output_id="video"),
                    ],
                )
            ],
        ),
    )

    snapshot = build_run_submission_snapshot(
        package=package,
        inputs={},
        output_preferences_snapshot={"result-video": OutputPreference(auto_save=True)},
    )

    assert snapshot.output_preferences["result-video"].auto_save is True
    assert snapshot.output_widgets[0].media_kind == "video"


@pytest.mark.anyio
async def test_gallery_capture_leaves_audio_outputs_for_future_media_gallery_support(tmp_path: Path) -> None:
    capture = GalleryCaptureService(GalleryStore(tmp_path / "gallery"))
    snapshot = RunSubmissionSnapshot(
        workflow_id="wf-audio",
        workflow_title="Audio Workflow",
        dashboard_version="1",
        output_preferences={"result-audio": OutputPreference(auto_save=True)},
        output_widgets=[
            GalleryOutputWidgetSnapshot(
                control_id="result-audio",
                output_id="audio",
                node_id="12",
                widget_title="Audio",
                media_kind="audio",
            ),
        ],
    )

    async def fail_if_fetched(job_id: str, filename: str, subfolder: str, output_type: str):
        raise AssertionError("image-only Gallery capture must leave audio outputs untouched")

    saved = await capture.save_completed_job_outputs(
        result=JobResult(
            job_id="job-audio",
            status="completed",
            outputs=[{"node_id": "12", "output": {"audio": [{"filename": "speech.wav", "type": "audio", "output_type": "output"}]}}],
        ),
        snapshot=snapshot,
        fetch_output=fail_if_fetched,
    )

    assert saved == []


def test_gallery_delete_keeps_row_when_file_staging_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_image(_captured_image(idempotency_key="delete-failure"))
    image_path = store.image_path(item.id)
    assert image_path is not None

    original_replace = Path.replace

    def fail_image_replace(self: Path, target: Path):
        if self == image_path:
            raise OSError("permission denied")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_image_replace)

    with pytest.raises(ValueError):
        store.delete_item(item.id)

    assert store.get_item(item.id) is not None
    assert image_path.exists()


def test_gallery_favorite_api_rejects_non_boolean_payload(tmp_path: Path) -> None:
    store = GalleryStore(tmp_path / "gallery")
    item = store.save_image(_captured_image(idempotency_key="favorite-api"))

    with TestClient(create_app(engine_service=_FakeEngineService(), gallery_store=store)) as client:
        response = client.put(f"/api/gallery/{item.id}/favorite", json={"favorite": "false"})

    assert response.status_code == 422
    assert store.get_item(item.id).favorite is False


def _captured_image(
    *,
    idempotency_key: str,
    workflow_title: str = "Workflow",
    widget_title: str = "Result",
):
    from datetime import UTC, datetime
    from app.gallery import CapturedGalleryImage

    return CapturedGalleryImage(
        idempotency_key=idempotency_key,
        created_at=datetime(2026, 5, 11, 18, 42, 10, tzinfo=UTC),
        workflow_id="wf-1",
        workflow_title=workflow_title,
        job_id="job-1",
        control_id="result",
        output_id="image",
        node_id="9",
        widget_title=widget_title,
        data=PNG_1X1,
        source_filename="output.png",
        source_mime_type="image/png",
        generation_settings={"settings": {"Prompt": "test"}},
        technical_metadata={},
    )


def _privacy_package() -> WorkflowPackage:
    return WorkflowPackage(
        metadata=WorkflowMetadata(id="wf-privacy", name="Privacy Workflow", version="1"),
        engine="comfyui",
        comfyui_graph={},
        inputs=[
            WorkflowInput(
                id="prompt",
                label="Prompt",
                control="textarea",
                binding=InputBinding(node_id="1", input_name="text"),
            ),
            WorkflowInput(
                id="image",
                label="Input Image",
                control="load_image",
                binding=InputBinding(node_id="2", input_name="image"),
            ),
            WorkflowInput(
                id="settings",
                label="Advanced Settings",
                control="string_field",
                binding=InputBinding(node_id="3", input_name="settings"),
            ),
        ],
        outputs=[WorkflowOutput(id="image-output", label="Image", node_id="9", type="image")],
        dashboard=DashboardSchema(
            version="1",
            status="configured",
            sections=[
                DashboardSection(
                    id="main",
                    title="Main",
                    controls=[
                        DashboardControl(id="prompt-control", type="textarea", label="Prompt", input_id="prompt"),
                        DashboardControl(id="image-control", type="load_image", label="Input Image", input_id="image"),
                        DashboardControl(id="settings-control", type="string_field", label="Advanced Settings", input_id="settings"),
                        DashboardControl(id="result", type="result_image", label="Result", output_id="image-output"),
                    ],
                )
            ],
        ),
    )


class _FakeEngineService:
    async def shutdown(self) -> None:
        return None
