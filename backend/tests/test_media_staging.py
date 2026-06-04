from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.runs.media_staging as media_staging_module
from app.diagnostics import LogStore
from app.gallery import CapturedGalleryOutput, GalleryStore
from app.runtime.runners.supervisor import RunnerDescriptor, RunnerKind, RunnerStatus
from app.runs.media_staging import MediaInputStagingError, MediaInputStagingResolver
from app.workflows.package import InputBinding, WorkflowInput, WorkflowMetadata, WorkflowPackage


def test_gallery_media_reference_is_staged_once_but_applied_to_each_input(tmp_path: Path) -> None:
    gallery = GalleryStore(tmp_path / "gallery")
    item = _gallery_item(gallery, kind="image", filename="source.png", mime_type="image/png", data=b"image")
    log_store = LogStore()
    resolver = _resolver(tmp_path, gallery, log_store)
    package = _package([
        WorkflowInput(id="image_a", label="Image A", control="load_image", binding=InputBinding(node_id="1", input_name="image")),
        WorkflowInput(id="image_b", label="Image B", control="load_image", binding=InputBinding(node_id="2", input_name="image")),
    ])

    result = resolver.stage_media_inputs(
        package=package,
        inputs={"image_a": _reference(item), "image_b": _reference(item)},
        runner=_runner(tmp_path),
        adapter=_Adapter(tmp_path / "input"),
        job_id="job-1",
    )

    assert result.inputs["image_a"] == result.inputs["image_b"]
    assert result.inputs["image_a"].startswith("staging/job-1_gallery_")
    assert len(result.staged_files) == 1
    assert result.staged_files[0].exists()
    events = log_store.list_events(job_id="job-1").events
    assert [event.details["input_id"] for event in events if event.message == "Staged Gallery media input"] == ["image_a", "image_b"]


@pytest.mark.parametrize(
    ("control", "kind", "filename", "mime_type"),
    [
        ("load_video", "video", "clip.mp4", "video/mp4"),
        ("load_audio", "audio", "speech.wav", "audio/wav"),
        ("load_3d", "3d", "mesh.glb", "model/gltf-binary"),
    ],
)
def test_gallery_media_reference_stages_each_supported_picker_kind(
    tmp_path: Path,
    control: str,
    kind: str,
    filename: str,
    mime_type: str,
) -> None:
    gallery = GalleryStore(tmp_path / "gallery")
    item = _gallery_item(gallery, kind=kind, filename=filename, mime_type=mime_type, data=b"media")
    resolver = _resolver(tmp_path, gallery)

    result = resolver.stage_media_inputs(
        package=_package([WorkflowInput(id="media", label="Media", control=control, binding=InputBinding(node_id="1", input_name="media"))]),
        inputs={"media": _reference(item, kind=kind)},
        runner=_runner(tmp_path),
        adapter=_Adapter(tmp_path / "input"),
        job_id=f"job-{kind}",
    )

    assert result.inputs["media"].startswith(f"staging/job-{kind}_gallery_")
    assert result.staged_files[0].read_bytes() == b"media"


def test_gallery_media_reference_validation_blocks_wrong_kind(tmp_path: Path) -> None:
    gallery = GalleryStore(tmp_path / "gallery")
    item = _gallery_item(gallery, kind="video", filename="clip.mp4", mime_type="video/mp4", data=b"video")
    resolver = _resolver(tmp_path, gallery)

    with pytest.raises(MediaInputStagingError, match="not compatible"):
        resolver.stage_media_inputs(
            package=_package([WorkflowInput(id="image", label="Image", control="load_image", binding=InputBinding(node_id="1", input_name="image"))]),
            inputs={"image": _reference(item, kind="video")},
            runner=_runner(tmp_path),
            adapter=_Adapter(tmp_path / "input"),
            job_id="job-kind",
        )


def test_gallery_media_reference_validation_blocks_malformed_reference(tmp_path: Path) -> None:
    resolver = _resolver(tmp_path, GalleryStore(tmp_path / "gallery"))

    with pytest.raises(MediaInputStagingError, match="reference is invalid"):
        resolver.stage_media_inputs(
            package=_package([WorkflowInput(id="image", label="Image", control="load_image", binding=InputBinding(node_id="1", input_name="image"))]),
            inputs={"image": {"source": "gallery", "kind": "image", "filename": "missing-id.png"}},
            runner=_runner(tmp_path),
            adapter=_Adapter(tmp_path / "input"),
            job_id="job-invalid-reference",
        )


def test_gallery_media_reference_validation_blocks_incompatible_extension(tmp_path: Path) -> None:
    gallery = GalleryStore(tmp_path / "gallery")
    item = _gallery_item(gallery, kind="3d", filename="model.dae", mime_type="model/vnd.collada+xml", data=b"model")
    resolver = _resolver(tmp_path, gallery)

    with pytest.raises(MediaInputStagingError, match="not supported"):
        resolver.stage_media_inputs(
            package=_package([WorkflowInput(id="model", label="Model", control="load_3d", binding=InputBinding(node_id="1", input_name="model"))]),
            inputs={"model": _reference(item, kind="3d")},
            runner=_runner(tmp_path),
            adapter=_Adapter(tmp_path / "input"),
            job_id="job-ext",
        )


def test_gallery_media_reference_validation_blocks_missing_file_and_cleans_prior_stage(tmp_path: Path) -> None:
    gallery = GalleryStore(tmp_path / "gallery")
    good = _gallery_item(gallery, kind="image", filename="source.png", mime_type="image/png", data=b"image")
    missing = _gallery_item(gallery, idempotency_key="missing", kind="image", filename="missing.png", mime_type="image/png", data=b"gone")
    gallery.content_path(missing.id).unlink()
    resolver = _resolver(tmp_path, gallery)

    with pytest.raises(MediaInputStagingError, match="file is missing"):
        resolver.stage_media_inputs(
            package=_package([
                WorkflowInput(id="good", label="Good", control="load_image", binding=InputBinding(node_id="1", input_name="image")),
                WorkflowInput(id="missing", label="Missing", control="load_image", binding=InputBinding(node_id="2", input_name="image")),
            ]),
            inputs={"good": _reference(good), "missing": _reference(missing)},
            runner=_runner(tmp_path),
            adapter=_Adapter(tmp_path / "input"),
            job_id="job-missing",
        )

    assert not list((tmp_path / "input" / "staging").glob("job-missing*"))


def test_uploaded_asset_value_still_stages_through_dashboard_asset_path(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    asset_id = "12345678-1234-1234-1234-123456789abc.png"
    (assets_dir / asset_id).write_bytes(b"uploaded")
    resolver = MediaInputStagingResolver(dashboard_assets_dir=assets_dir, gallery_store=None, log_store=None)

    result = resolver.stage_media_inputs(
        package=_package([WorkflowInput(id="image", label="Image", control="load_image", binding=InputBinding(node_id="1", input_name="image"))]),
        inputs={"image": asset_id},
        runner=_runner(tmp_path),
        adapter=_Adapter(tmp_path / "input"),
        job_id="job-upload",
    )

    assert result.inputs["image"] == f"staging/job-upload_{asset_id}"
    assert result.staged_files[0].read_bytes() == b"uploaded"


def test_package_asset_default_stages_from_imported_source_files(tmp_path: Path) -> None:
    package_dir = tmp_path / "store" / "unknown" / "wf" / "1"
    (package_dir / "package.json").parent.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"metadata":{"id":"wf","name":"Workflow","version":"1"},"engine":"comfyui","comfyui_graph":{}}',
        encoding="utf-8",
    )
    source_asset = package_dir / "source-files" / "assets" / "input-defaults" / "default.png"
    source_asset.parent.mkdir(parents=True)
    source_asset.write_bytes(b"packaged")
    resolver = MediaInputStagingResolver(
        dashboard_assets_dir=tmp_path / "assets",
        gallery_store=None,
        log_store=None,
        package_search_roots=[tmp_path / "store"],
    )
    package = _package([
        WorkflowInput(
            id="image",
            label="Image",
            control="load_image",
            binding=InputBinding(node_id="1", input_name="image"),
        )
    ])

    result = resolver.stage_media_inputs(
        package=package,
        inputs={
            "image": {
                "source": "package_asset",
                "asset_id": "input-defaults/default.png",
                "kind": "image",
                "content_type": "image/png",
            }
        },
        runner=_runner(tmp_path),
        adapter=_Adapter(tmp_path / "input"),
        job_id="job-package",
    )

    assert result.inputs["image"] == "staging/job-package_package_default.png"
    assert result.staged_files[0].read_bytes() == b"packaged"


def test_package_asset_default_blocks_mismatched_integrity(tmp_path: Path) -> None:
    package_dir = tmp_path / "store" / "unknown" / "wf" / "1"
    (package_dir / "package.json").parent.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        '{"metadata":{"id":"wf","name":"Workflow","version":"1"},"engine":"comfyui","comfyui_graph":{}}',
        encoding="utf-8",
    )
    source_asset = package_dir / "source-files" / "assets" / "input-defaults" / "default.png"
    source_asset.parent.mkdir(parents=True)
    source_asset.write_bytes(b"changed")
    resolver = MediaInputStagingResolver(
        dashboard_assets_dir=tmp_path / "assets",
        gallery_store=None,
        log_store=None,
        package_search_roots=[tmp_path / "store"],
    )

    with pytest.raises(MediaInputStagingError, match="integrity"):
        resolver.stage_media_inputs(
            package=_package([
                WorkflowInput(
                    id="image",
                    label="Image",
                    control="load_image",
                    binding=InputBinding(node_id="1", input_name="image"),
                )
            ]),
            inputs={
                "image": {
                    "source": "package_asset",
                    "asset_id": "input-defaults/default.png",
                    "kind": "image",
                    "content_type": "image/png",
                    "size_bytes": 8,
                    "sha256": "sha256:" + "0" * 64,
                }
            },
            runner=_runner(tmp_path),
            adapter=_Adapter(tmp_path / "input"),
            job_id="job-package-bad",
        )


def test_missing_uploaded_asset_blocks_run_staging(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    resolver = MediaInputStagingResolver(dashboard_assets_dir=assets_dir, gallery_store=None, log_store=None)

    with pytest.raises(MediaInputStagingError, match="could not be found"):
        resolver.stage_media_inputs(
            package=_package([WorkflowInput(id="image", label="Image", control="load_image", binding=InputBinding(node_id="1", input_name="image"))]),
            inputs={"image": "12345678-1234-1234-1234-123456789abc.png"},
            runner=_runner(tmp_path),
            adapter=_Adapter(tmp_path / "input"),
            job_id="job-missing-upload",
        )


def test_staging_filesystem_error_is_user_facing_and_cleans_prior_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    first_asset_id = "12345678-1234-1234-1234-123456789abc.png"
    second_asset_id = "abcdef12-1234-1234-1234-123456789abc.png"
    (assets_dir / first_asset_id).write_bytes(b"first")
    (assets_dir / second_asset_id).write_bytes(b"second")
    resolver = MediaInputStagingResolver(dashboard_assets_dir=assets_dir, gallery_store=None, log_store=None)
    original_stage = media_staging_module._stage_media_file
    calls = 0

    def fail_second_stage(source: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        original_stage(source, target)

    monkeypatch.setattr(media_staging_module, "_stage_media_file", fail_second_stage)

    with pytest.raises(MediaInputStagingError, match="could not prepare"):
        resolver.stage_media_inputs(
            package=_package([
                WorkflowInput(id="first", label="First", control="load_image", binding=InputBinding(node_id="1", input_name="image")),
                WorkflowInput(id="second", label="Second", control="load_image", binding=InputBinding(node_id="2", input_name="image")),
            ]),
            inputs={"first": first_asset_id, "second": second_asset_id},
            runner=_runner(tmp_path),
            adapter=_Adapter(tmp_path / "input"),
            job_id="job-disk-full",
        )

    assert not list((tmp_path / "input" / "staging").glob("job-disk-full*"))


class _Adapter:
    def __init__(self, input_dir: Path) -> None:
        self.comfyui_input_dir = input_dir


def _resolver(tmp_path: Path, gallery: GalleryStore, log_store: LogStore | None = None) -> MediaInputStagingResolver:
    return MediaInputStagingResolver(dashboard_assets_dir=tmp_path / "assets", gallery_store=gallery, log_store=log_store)


def _runner(tmp_path: Path) -> RunnerDescriptor:
    return RunnerDescriptor(
        runner_id="runner",
        kind=RunnerKind.CORE_COMFYUI,
        base_url="http://127.0.0.1:8188",
        fingerprint="runner",
        status=RunnerStatus.READY,
        runner_workspace_path=str(tmp_path / "runner"),
    )


def _package(inputs: list[WorkflowInput]) -> WorkflowPackage:
    return WorkflowPackage(metadata=WorkflowMetadata(id="wf", name="Workflow", version="1"), engine="comfyui", comfyui_graph={}, inputs=inputs)


def _gallery_item(
    store: GalleryStore,
    *,
    idempotency_key: str = "item",
    kind: str,
    filename: str,
    mime_type: str,
    data: bytes,
):
    staged = store.create_staging_path()
    staged.write_bytes(data)
    return store.save_staged_output(
        CapturedGalleryOutput(
            idempotency_key=idempotency_key,
            created_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
            workflow_id="wf",
            workflow_title="Workflow",
            job_id="job",
            control_id="result",
            output_id=kind,
            node_id="9",
            widget_title="Result",
            kind=kind,
            staged_path=staged,
            source_filename=filename,
            source_mime_type=mime_type,
            extension=Path(filename).suffix,
            size_bytes=len(data),
            width=None,
            height=None,
            duration_seconds=None,
            fps=None,
            generation_settings={},
        )
    )


def _reference(item, *, kind: str | None = None) -> dict[str, object]:
    return {
        "source": "gallery",
        "gallery_item_id": item.id,
        "kind": kind or item.kind,
        "filename": item.filename,
        "extension": item.extension,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
    }
