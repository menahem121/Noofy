"""Tests for DashboardAssetService."""
import json
import struct
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.workflows.assets as assets_module
from app.diagnostics import LogStore
from app.gallery import CapturedGalleryOutput, GalleryStore
from app.main import create_app
from app.workflows.assets import AssetUploadError, DashboardAssetService
from app.workflows.package import InputBinding, WorkflowInput, WorkflowMetadata, WorkflowPackage


class FakeEngineService:
    def __init__(self, package: WorkflowPackage | None = None) -> None:
        self.workflow_library_service = self
        self.workflow_loader = self
        self.package = package
        self.default_asset: tuple[Path, dict[str, object]] | None = None
        self.default_asset_error: Exception | None = None

    def list_workflows(self):
        return []

    def get_package(self, workflow_id: str) -> WorkflowPackage:
        if self.package is not None and self.package.metadata.id == workflow_id:
            return self.package
        raise KeyError(f"Workflow '{workflow_id}' was not found.")

    def workflow_default_asset(
        self,
        workflow_id: str,
        input_id: str,
    ) -> tuple[Path, dict[str, object]]:
        if self.default_asset_error is not None:
            raise self.default_asset_error
        if self.default_asset is None:
            raise KeyError(f"Workflow input '{input_id}' was not found.")
        return self.default_asset

    async def shutdown(self) -> None:
        return None


class IconInUseEngineService(FakeEngineService):
    def __init__(self, icon_id: str) -> None:
        super().__init__()
        self.icon_id = icon_id

    def list_workflows(self):
        return [{"name": "Cleanup Flow", "icon": self.icon_id}]


def _make_png(width: int = 1, height: int = 1) -> bytes:
    """Minimal valid 1x1 white PNG."""
    import zlib
    raw_row = b"\x00" + (b"\xff\xff\xff" * width)
    compressed = zlib.compress(raw_row * height)
    def chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return length + tag + data + crc
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat_data = compressed
    return (
        signature
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", idat_data)
        + chunk(b"IEND", b"")
    )


def _png_from_pixels(width: int, height: int, pixels: list[tuple[int, int, int, int]]) -> bytes:
    image = Image.new("RGBA", (width, height))
    image.putdata(pixels)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


PNG_BYTES = _make_png()
WAV_BYTES = b"RIFF" + (36).to_bytes(4, "little") + b"WAVEfmt " + b"\x10\x00\x00\x00\x01\x00\x01\x00\x40\x1f\x00\x00\x80>\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
MP4_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp41"
WEBM_BYTES = b"\x1aE\xdf\xa3\x9fB\x86\x81\x01"
EMBEDDED_GLTF_BYTES = json.dumps(
    {
        "asset": {"version": "2.0"},
        "buffers": [{"uri": "data:application/octet-stream;base64,AA==", "byteLength": 1}],
    }
).encode()


def test_store_png_returns_asset_id(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    result = svc.store(PNG_BYTES, "image/png", "test.png")
    assert "asset_id" in result
    assert result["asset_id"].endswith(".png")
    assert result["original_filename"] == "test.png"


def test_stored_file_exists(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    result = svc.store(PNG_BYTES, "image/png", "img.png")
    path = svc.asset_path(result["asset_id"])
    assert path.exists()
    assert path.read_bytes() == PNG_BYTES


def test_reject_oversized_file(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    big = b"x" * (25 * 1024 * 1024 + 1)
    with pytest.raises(AssetUploadError, match="25 MB"):
        svc.store(big, "image/png", "big.png")


def test_reject_disallowed_mime_type(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    with pytest.raises(AssetUploadError, match="not allowed"):
        svc.store(PNG_BYTES, "image/tiff", "img.tiff")


def test_reject_non_image_bytes_with_image_mime(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    with pytest.raises(AssetUploadError, match="valid image"):
        svc.store(b"definitely not an image", "image/png", "fake.png")


def test_reject_image_mime_that_does_not_match_bytes(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    with pytest.raises(AssetUploadError, match="does not match"):
        svc.store(PNG_BYTES, "image/jpeg", "fake.jpg")


def test_asset_path_blocks_traversal(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    with pytest.raises(ValueError, match="Invalid asset_id"):
        svc.asset_path("../../etc/passwd.png")


def test_asset_path_blocks_unknown_extension(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    with pytest.raises(ValueError, match="Invalid asset_id"):
        svc.asset_path("abc123.exe")


def test_content_type_inferred_from_ext(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    result = svc.store(PNG_BYTES, "image/png", "img.png")
    assert svc.content_type(result["asset_id"]) == "image/png"


def test_creates_asset_dir(tmp_path: Path) -> None:
    asset_dir = tmp_path / "nested" / "assets"
    svc = DashboardAssetService(asset_dir)
    svc.store(PNG_BYTES, "image/png", "img.png")
    assert asset_dir.exists()


def test_metadata_returns_original_filename(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    result = svc.store(PNG_BYTES, "image/png", "portrait.png")
    assert svc.metadata(result["asset_id"]) == {
        "asset_id": result["asset_id"],
        "original_filename": "portrait.png",
        "content_type": "image/png",
    }


def test_store_masked_image_inverts_alpha_and_preserves_rgb(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    source = svc.store(
        _png_from_pixels(2, 1, [(10, 20, 30, 255), (40, 50, 60, 255)]),
        "image/png",
        "source.png",
    )
    mask = _png_from_pixels(2, 1, [(0, 0, 0, 255), (0, 0, 0, 0)])

    result = svc.store_masked_image(
        source_asset_id=source["asset_id"],
        mask_data=mask,
        mask_content_type="image/png",
    )

    with Image.open(svc.asset_path(result["asset_id"])) as image:
        assert list(image.convert("RGBA").get_flattened_data()) == [(10, 20, 30, 0), (40, 50, 60, 255)]
    metadata = svc.metadata(result["asset_id"])
    assert metadata["has_mask"] is True
    assert metadata["source_asset_id"] == source["asset_id"]


def test_store_masked_image_blank_mask_outputs_opaque_alpha(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    source = svc.store(
        _png_from_pixels(1, 1, [(12, 34, 56, 255)]),
        "image/png",
        "source.png",
    )
    blank_mask = _png_from_pixels(1, 1, [(0, 0, 0, 0)])

    result = svc.store_masked_image(
        source_asset_id=source["asset_id"],
        mask_data=blank_mask,
        mask_content_type="image/png",
    )

    with Image.open(svc.asset_path(result["asset_id"])) as image:
        assert image.convert("RGBA").getpixel((0, 0)) == (12, 34, 56, 255)


def test_store_masked_image_preserves_soft_mask_alpha(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    source = svc.store(
        _png_from_pixels(1, 1, [(90, 80, 70, 255)]),
        "image/png",
        "source.png",
    )
    soft_mask = _png_from_pixels(1, 1, [(0, 0, 0, 128)])

    result = svc.store_masked_image(
        source_asset_id=source["asset_id"],
        mask_data=soft_mask,
        mask_content_type="image/png",
    )

    with Image.open(svc.asset_path(result["asset_id"])) as image:
        assert image.convert("RGBA").getpixel((0, 0)) == (90, 80, 70, 127)


def test_store_masked_image_ignores_source_transparency_without_submitted_mask(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    source = svc.store(
        _png_from_pixels(1, 1, [(5, 6, 7, 0)]),
        "image/png",
        "transparent-source.png",
    )
    blank_mask = _png_from_pixels(1, 1, [(0, 0, 0, 0)])

    result = svc.store_masked_image(
        source_asset_id=source["asset_id"],
        mask_data=blank_mask,
        mask_content_type="image/png",
    )

    with Image.open(svc.asset_path(result["asset_id"])) as image:
        assert image.convert("RGBA").getpixel((0, 0)) == (5, 6, 7, 255)


def test_store_masked_image_rejects_dimension_mismatch(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    source = svc.store(
        _png_from_pixels(2, 1, [(1, 2, 3, 255), (4, 5, 6, 255)]),
        "image/png",
        "source.png",
    )
    wrong_size_mask = _png_from_pixels(1, 1, [(0, 0, 0, 255)])

    with pytest.raises(AssetUploadError, match="dimensions"):
        svc.store_masked_image(
            source_asset_id=source["asset_id"],
            mask_data=wrong_size_mask,
            mask_content_type="image/png",
        )


def test_store_masked_image_rejects_missing_or_invalid_source_asset(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    mask = _png_from_pixels(1, 1, [(0, 0, 0, 255)])

    with pytest.raises(ValueError, match="Invalid asset_id"):
        svc.store_masked_image(source_asset_id="../source.png", mask_data=mask, mask_content_type="image/png")

    with pytest.raises(AssetUploadError, match="not found"):
        svc.store_masked_image(
            source_asset_id=f"{uuid.uuid4()}.png",
            mask_data=mask,
            mask_content_type="image/png",
        )


def test_store_audio_stream_returns_metadata(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_audio_stream(BytesIO(WAV_BYTES), "audio/wav", "voice.wav", declared_size=len(WAV_BYTES))

    assert result["asset_id"].endswith(".wav")
    assert result["kind"] == "audio"
    assert result["original_filename"] == "voice.wav"
    assert result["content_type"] == "audio/wav"
    assert result["size"] == len(WAV_BYTES)
    assert result["duration_seconds"] == 0
    assert svc.asset_path(result["asset_id"]).read_bytes() == WAV_BYTES
    assert svc.metadata(result["asset_id"])["kind"] == "audio"


def test_store_three_d_stream_accepts_embedded_gltf_json(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_three_d_stream(BytesIO(EMBEDDED_GLTF_BYTES), "application/json", "scene.gltf")

    assert result["asset_id"].endswith(".gltf")
    assert result["kind"] == "3d"
    assert result["content_type"] == "model/gltf+json"
    assert result["extension"] == ".gltf"
    assert svc.metadata(result["asset_id"])["extension"] == ".gltf"


def test_store_three_d_stream_accepts_spz_splat_model(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_three_d_stream(BytesIO(b"spz-data"), "application/octet-stream", "model.spz")

    assert result["asset_id"].endswith(".spz")
    assert result["kind"] == "3d"
    assert result["content_type"] == "application/octet-stream"
    assert result["extension"] == ".spz"
    assert svc.metadata(result["asset_id"])["extension"] == ".spz"


def test_store_three_d_stream_accepts_usdz_model(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_three_d_stream(BytesIO(b"usdz-data"), "application/octet-stream", "scene.usdz")

    assert result["asset_id"].endswith(".usdz")
    assert result["kind"] == "3d"
    assert result["content_type"] == "model/vnd.usdz+zip"
    assert result["extension"] == ".usdz"
    assert svc.metadata(result["asset_id"])["extension"] == ".usdz"


def test_store_three_d_stream_rejects_nested_external_gltf_uri_and_cleans_upload(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    gltf = json.dumps({"asset": {"version": "2.0"}, "extensions": {"vendor": {"uri": "remote.bin"}}}).encode()

    with pytest.raises(AssetUploadError, match="Multi-file GLTF"):
        svc.store_three_d_stream(BytesIO(gltf), "model/gltf+json", "scene.gltf")

    assert not list((tmp_path / "assets").glob("*.tmp"))
    assert not list((tmp_path / "assets").glob("*.gltf"))


def test_store_three_d_stream_bounds_gltf_json_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    monkeypatch.setattr(assets_module, "MAX_GLTF_JSON_BYTES", 8)

    with pytest.raises(AssetUploadError, match="16 MB"):
        svc.store_three_d_stream(BytesIO(EMBEDDED_GLTF_BYTES), "model/gltf+json", "scene.gltf")

    assert not list((tmp_path / "assets").glob("*.tmp"))


def test_store_audio_stream_sanitizes_original_filename(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_audio_stream(BytesIO(WAV_BYTES), "audio/wav", r"C:\uploads\voice.wav")

    assert result["original_filename"] == "voice.wav"
    assert svc.metadata(result["asset_id"])["original_filename"] == "voice.wav"


def test_store_audio_stream_rejects_mismatched_content(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="does not match"):
        svc.store_audio_stream(BytesIO(WAV_BYTES), "audio/mpeg", "voice.mp3")


def test_store_audio_stream_rejects_mismatched_filename_extension(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="extension '.mp3' does not match"):
        svc.store_audio_stream(BytesIO(WAV_BYTES), "audio/wav", "voice.mp3")


@pytest.mark.parametrize(
    ("filename", "content_type", "data"),
    [
        ("voice.mp3", "audio/mpeg", b"ID3\x04\x00\x00\x00\x00\x00\x00"),
        ("voice.flac", "audio/flac", b"fLaC\x00\x00\x00\x22"),
        ("voice.ogg", "audio/ogg", b"OggS\x00\x02"),
        ("voice.m4a", "audio/mp4", b"\x00\x00\x00\x18ftypM4A "),
    ],
)
def test_store_audio_stream_accepts_supported_audio_headers(
    tmp_path: Path,
    filename: str,
    content_type: str,
    data: bytes,
) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_audio_stream(BytesIO(data), content_type, filename)

    assert result["asset_id"].endswith(Path(filename).suffix)
    assert result["format"] == Path(filename).suffix.removeprefix(".")


def test_store_audio_stream_rejects_declared_file_over_cap(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="100 GB"):
        svc.store_audio_stream(
            BytesIO(WAV_BYTES),
            "audio/wav",
            "voice.wav",
            declared_size=assets_module.MAX_AUDIO_ASSET_BYTES + 1,
        )


def test_store_audio_stream_rejects_insufficient_disk_space(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = DashboardAssetService(tmp_path / "assets")
    monkeypatch.setattr(
        assets_module.shutil,
        "disk_usage",
        lambda _path: assets_module.shutil._ntuple_diskusage(total=1024, used=1024, free=0),
    )

    with pytest.raises(AssetUploadError, match="Not enough free disk space"):
        svc.store_audio_stream(
            BytesIO(WAV_BYTES),
            "audio/wav",
            "voice.wav",
            declared_size=len(WAV_BYTES),
        )


def test_store_audio_stream_records_structured_diagnostics(tmp_path: Path) -> None:
    log_store = LogStore()
    svc = DashboardAssetService(tmp_path / "assets", log_store=log_store)

    result = svc.store_audio_stream(BytesIO(WAV_BYTES), "audio/wav", "voice.wav")
    with pytest.raises(AssetUploadError):
        svc.store_audio_stream(BytesIO(b"broken"), "audio/wav", "broken.wav")

    events = log_store.list_events().events
    assert events[-2].message == "Stored dashboard audio asset"
    assert events[-2].details["asset_id"] == result["asset_id"]
    assert events[-2].details["size"] == len(WAV_BYTES)
    assert events[-1].message == "Dashboard audio asset upload failed"


def test_store_audio_stream_cleans_failed_upload(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="valid audio"):
        svc.store_audio_stream(BytesIO(b"not audio"), "audio/wav", "broken.wav")

    assert not list((tmp_path / "assets").glob("*.tmp"))
    assert not list((tmp_path / "assets").glob("*.wav"))


@pytest.mark.parametrize(
    ("filename", "content_type", "data"),
    [
        ("clip.mp4", "video/mp4", MP4_BYTES),
        ("clip.mp4", "application/mp4", MP4_BYTES),
        ("clip.mov", "video/quicktime", MP4_BYTES),
        ("clip.mov", "video/x-quicktime", MP4_BYTES),
        ("clip.webm", "video/webm", WEBM_BYTES),
        ("clip.webm", "application/webm", WEBM_BYTES),
        ("clip.mkv", "video/x-matroska", WEBM_BYTES),
        ("clip.mkv", "video/matroska", WEBM_BYTES),
        ("clip.mkv", "application/x-matroska", WEBM_BYTES),
    ],
)
def test_store_video_stream_accepts_supported_containers(
    tmp_path: Path,
    filename: str,
    content_type: str,
    data: bytes,
) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_video_stream(BytesIO(data), content_type, filename)

    assert result["asset_id"].endswith(Path(filename).suffix)
    assert result["kind"] == "video"
    assert result["format"] == Path(filename).suffix.removeprefix(".")
    assert result["size"] == len(data)
    assert svc.metadata(result["asset_id"])["kind"] == "video"


def test_store_video_stream_rejects_mismatched_container(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="does not match"):
        svc.store_video_stream(BytesIO(WEBM_BYTES), "video/mp4", "clip.mp4")

    assert not list((tmp_path / "assets").glob("*.tmp"))


def test_store_video_stream_accepts_octet_stream_using_filename(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_video_stream(BytesIO(WEBM_BYTES), "application/octet-stream", "clip.mkv")

    assert result["asset_id"].endswith(".mkv")
    assert result["content_type"] == "video/x-matroska"


def test_store_video_stream_rejects_declared_file_over_cap(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="100 GB"):
        svc.store_video_stream(
            BytesIO(MP4_BYTES),
            "video/mp4",
            "clip.mp4",
            declared_size=assets_module.MAX_VIDEO_ASSET_BYTES + 1,
        )


def test_store_file_stream_accepts_declared_extension_and_mime(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_file_stream(
        BytesIO(b'{"ok": true}'),
        "application/json",
        "settings.json",
        accepted_extensions=[".json"],
        accepted_mime_types=["application/json"],
        declared_size=12,
    )

    assert result["kind"] == "file"
    assert result["asset_id"].endswith(".json")
    assert result["extension"] == ".json"
    assert result["content_type"] == "application/json"
    assert svc.metadata(result["asset_id"])["extension"] == ".json"


def test_store_file_stream_allows_octet_stream_only_with_allowed_extension(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    result = svc.store_file_stream(
        BytesIO(b"tensor"),
        "application/octet-stream",
        "weights.pt",
        accepted_extensions=[".pt"],
        accepted_mime_types=["application/pytorch"],
    )

    assert result["asset_id"].endswith(".pt")


def test_store_file_stream_rejects_octet_stream_for_mime_only_rule(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="not allowed"):
        svc.store_file_stream(
            BytesIO(b"{}"),
            "application/octet-stream",
            "data.json",
            accepted_extensions=[],
            accepted_mime_types=["application/json"],
        )


def test_store_file_stream_rejects_unaccepted_extension(tmp_path: Path) -> None:
    svc = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="extension"):
        svc.store_file_stream(
            BytesIO(b"bad"),
            "application/pdf",
            "report.pdf",
            accepted_extensions=[".txt"],
            accepted_mime_types=[],
        )


def test_workflow_icon_upload_is_resized_and_listed(tmp_path: Path) -> None:
    from PIL import Image

    asset_service = DashboardAssetService(tmp_path / "assets")
    large_icon = _make_png(300, 128)

    result = asset_service.store_workflow_icon(large_icon, "image/png", "large-icon.png")

    assert result["id"].startswith("asset:")
    assert result["kind"] == "custom"
    with Image.open(asset_service.asset_path(result["asset_id"])) as image:
        assert image.width <= 256
        assert image.height <= 256
    assert asset_service.list_workflow_icons()[0]["id"] == result["id"]


def test_workflow_icon_upload_rejects_unsupported_file(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")

    with pytest.raises(AssetUploadError, match="not allowed"):
        asset_service.store_workflow_icon(b"nope", "image/svg+xml", "icon.svg")


def test_workflow_icon_routes_upload_list_and_delete(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    with TestClient(create_app(engine_service=FakeEngineService(), asset_service=asset_service)) as client:
        upload = client.post(
            "/api/workflow-icons",
            files={"image": ("icon.png", PNG_BYTES, "image/png")},
        )
        list_response = client.get("/api/workflow-icons")
        delete = client.delete(f"/api/workflow-icons/{upload.json()['id']}")

    assert upload.status_code == 200
    assert list_response.status_code == 200
    assert list_response.json()["icons"][0]["id"] == upload.json()["id"]
    assert delete.status_code == 200
    assert asset_service.list_workflow_icons() == []


def test_workflow_icon_delete_blocks_icons_used_by_workflows(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    stored = asset_service.store_workflow_icon(PNG_BYTES, "image/png", "icon.png")

    with TestClient(create_app(engine_service=IconInUseEngineService(stored["id"]), asset_service=asset_service)) as client:
        response = client.delete(f"/api/workflow-icons/{stored['id']}")

    assert response.status_code == 409
    assert "Cleanup Flow" in response.json()["detail"]


def test_upload_dashboard_asset_route_uses_workflow_path_param(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        response = client.post(
            "/api/workflows/wf-1/assets/image",
            files={"image": ("img.png", PNG_BYTES, "image/png")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"].endswith(".png")
    assert (tmp_path / "assets" / body["asset_id"]).exists()


def test_workflow_default_asset_route_serves_packaged_media(tmp_path: Path) -> None:
    source = tmp_path / "starter.png"
    source.write_bytes(PNG_BYTES)
    engine_service = FakeEngineService()
    engine_service.default_asset = (
        source,
        {
            "source": "package_asset",
            "asset_id": "input-defaults/starter.png",
            "kind": "image",
            "filename": "starter.png",
            "content_type": "image/png",
            "size_bytes": len(PNG_BYTES),
        },
    )

    with TestClient(create_app(engine_service=engine_service)) as client:
        response = client.get(
            "/api/workflows/wf/inputs/image/default-asset",
            params={"asset_id": "input-defaults/starter.png"},
        )
        range_response = client.get(
            "/api/workflows/wf/inputs/image/default-asset",
            params={"asset_id": "input-defaults/starter.png"},
            headers={"Range": "bytes=0-3"},
        )

    assert response.status_code == 200
    assert response.content == PNG_BYTES
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'inline; filename="starter.png"'
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    assert range_response.status_code == 206
    assert range_response.content == PNG_BYTES[:4]
    assert range_response.headers["content-range"] == f"bytes 0-3/{len(PNG_BYTES)}"


def test_workflow_default_asset_route_rejects_stale_asset_version(tmp_path: Path) -> None:
    source = tmp_path / "starter.png"
    source.write_bytes(PNG_BYTES)
    engine_service = FakeEngineService()
    engine_service.default_asset = (
        source,
        {
            "source": "package_asset",
            "asset_id": "input-defaults/current.png",
            "kind": "image",
        },
    )

    with TestClient(create_app(engine_service=engine_service)) as client:
        response = client.get(
            "/api/workflows/wf/inputs/image/default-asset",
            params={"asset_id": "input-defaults/old.png"},
        )

    assert response.status_code == 404


def test_workflow_default_asset_route_reports_invalid_reference() -> None:
    engine_service = FakeEngineService()
    engine_service.default_asset_error = ValueError("Invalid packaged default.")

    with TestClient(create_app(engine_service=engine_service)) as client:
        response = client.get(
            "/api/workflows/wf/inputs/image/default-asset",
            params={"asset_id": "input-defaults/default.png"},
        )

    assert response.status_code == 422


def test_workflow_default_asset_route_rejects_unknown_input() -> None:
    with TestClient(create_app(engine_service=FakeEngineService())) as client:
        response = client.get(
            "/api/workflows/wf/inputs/missing/default-asset",
            params={"asset_id": "input-defaults/default.png"},
        )

    assert response.status_code == 404


@pytest.mark.parametrize("control", ["load_image", "load_image_mask"])
def test_copy_gallery_image_to_dashboard_asset_route_stages_asset_before_mask_editing(tmp_path: Path, control: str) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    gallery_store = GalleryStore(tmp_path / "gallery")
    gallery_item = _gallery_item(gallery_store, filename="gallery-source.png", mime_type="image/png", data=PNG_BYTES)
    package = _package([
        WorkflowInput(id="input_image", label="Input image", control=control, binding=InputBinding(node_id="1", input_name="image")),
    ])

    with TestClient(
        create_app(
            engine_service=FakeEngineService(package),
            asset_service=asset_service,
            gallery_store=gallery_store,
        )
    ) as client:
        response = client.post(
            "/api/workflows/wf/assets/image/from-gallery",
            json={"input_id": "input_image", "gallery_item_id": gallery_item.id},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"].endswith(".png")
    assert (tmp_path / "assets" / body["asset_id"]).read_bytes() == PNG_BYTES
    metadata = asset_service.metadata(body["asset_id"])
    assert metadata["kind"] == "image"
    assert metadata["source_gallery_item_id"] == gallery_item.id


def test_copy_gallery_image_to_dashboard_asset_route_requires_available_gallery_file(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    gallery_store = GalleryStore(tmp_path / "gallery")
    gallery_item = _gallery_item(gallery_store, filename="gallery-source.png", mime_type="image/png", data=PNG_BYTES)
    gallery_store.content_path(gallery_item.id).unlink()
    package = _package([
        WorkflowInput(id="input_image", label="Input image", control="load_image", binding=InputBinding(node_id="1", input_name="image")),
    ])

    with TestClient(
        create_app(
            engine_service=FakeEngineService(package),
            asset_service=asset_service,
            gallery_store=gallery_store,
        )
    ) as client:
        response = client.post(
            "/api/workflows/wf/assets/image/from-gallery",
            json={"input_id": "input_image", "gallery_item_id": gallery_item.id},
        )

    assert response.status_code == 422
    assert "available image" in response.json()["detail"]


def test_upload_dashboard_audio_asset_route_streams_file(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        response = client.post(
            "/api/workflows/wf-1/assets/audio",
            files={"audio": ("voice.wav", WAV_BYTES, "audio/wav")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"].endswith(".wav")
    assert body["kind"] == "audio"
    assert (tmp_path / "assets" / body["asset_id"]).exists()


def test_upload_dashboard_video_asset_route_streams_file(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        response = client.post(
            "/api/workflows/wf-1/assets/video",
            files={"video": ("clip.mp4", MP4_BYTES, "video/mp4")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"].endswith(".mp4")
    assert body["kind"] == "video"
    assert (tmp_path / "assets" / body["asset_id"]).exists()


def test_upload_dashboard_three_d_asset_route_streams_model(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        response = client.post(
            "/api/workflows/wf-1/assets/3d",
            files={"model": ("scene.gltf", EMBEDDED_GLTF_BYTES, "application/json")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"].endswith(".gltf")
    assert body["kind"] == "3d"
    assert (tmp_path / "assets" / body["asset_id"]).exists()


def test_upload_dashboard_file_asset_route_uses_input_validation(tmp_path: Path) -> None:
    package = WorkflowPackage.model_validate(
        {
            "metadata": {"id": "wf-file", "name": "File Workflow", "version": "1.0.0"},
            "engine": "comfyui",
            "comfyui_graph": {"1": {"class_type": "LoadFile", "inputs": {"file_path": ""}}},
            "inputs": [
                {
                    "id": "source-file",
                    "label": "Source file",
                    "control": "load_file",
                    "binding": {"node_id": "1", "input_name": "file_path"},
                    "validation": {"accepted_extensions": [".json"], "accepted_mime_types": ["application/json"]},
                }
            ],
        }
    )
    asset_service = DashboardAssetService(tmp_path / "assets")
    with TestClient(
        create_app(engine_service=FakeEngineService(package), asset_service=asset_service)
    ) as client:
        accepted = client.post(
            "/api/workflows/wf-file/assets/file",
            data={"input_id": "source-file"},
            files={"file": ("settings.json", b"{}", "application/json")},
        )
        rejected = client.post(
            "/api/workflows/wf-file/assets/file",
            data={"input_id": "source-file"},
            files={"file": ("settings.pdf", b"%PDF", "application/pdf")},
        )

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["kind"] == "file"
    assert body["asset_id"].endswith(".json")
    assert body["extension"] == ".json"
    assert rejected.status_code == 422


def test_serve_dashboard_asset_route_returns_file_and_metadata(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    stored = asset_service.store(PNG_BYTES, "image/png", "input.png")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        image_response = client.get(f"/api/assets/{stored['asset_id']}")
        metadata_response = client.get(f"/api/assets/{stored['asset_id']}/metadata")

    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content == PNG_BYTES
    assert metadata_response.status_code == 200
    assert metadata_response.json()["original_filename"] == "input.png"


def test_serve_dashboard_video_asset_route_supports_range_requests(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    stored = asset_service.store_video_stream(BytesIO(MP4_BYTES), "video/mp4", "clip.mp4")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        response = client.get(
            f"/api/assets/{stored['asset_id']}",
            headers={"Range": "bytes=0-3"},
        )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-3/{len(MP4_BYTES)}"
    assert response.content == MP4_BYTES[:4]


def test_serve_dashboard_asset_route_rejects_bad_asset_id(tmp_path: Path) -> None:
    asset_service = DashboardAssetService(tmp_path / "assets")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        response = client.get("/api/assets/not-an-image.exe")

    assert response.status_code == 400


def test_dashboard_asset_routes_require_bearer_token_when_auth_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")
    asset_service = DashboardAssetService(tmp_path / "assets")
    stored = asset_service.store(PNG_BYTES, "image/png", "secure.png")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        missing = client.get(f"/api/assets/{stored['asset_id']}")
        allowed = client.get(
            f"/api/assets/{stored['asset_id']}",
            headers={"Authorization": "Bearer secret-token"},
        )

    assert missing.status_code == 401
    assert allowed.status_code == 200


def test_dashboard_asset_routes_allow_query_token_for_media_elements(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")
    asset_service = DashboardAssetService(tmp_path / "assets")
    stored = asset_service.store_audio_stream(BytesIO(WAV_BYTES), "audio/wav", "secure.wav")
    with TestClient(
        create_app(engine_service=FakeEngineService(), asset_service=asset_service)
    ) as client:
        response = client.get(f"/api/assets/{stored['asset_id']}?token=secret-token")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == WAV_BYTES


def _package(inputs: list[WorkflowInput]) -> WorkflowPackage:
    return WorkflowPackage(
        metadata=WorkflowMetadata(id="wf", name="Workflow", version="1"),
        engine="comfyui",
        comfyui_graph={},
        inputs=inputs,
    )


def _gallery_item(
    store: GalleryStore,
    *,
    filename: str,
    mime_type: str,
    data: bytes,
):
    staged = store.create_staging_path()
    staged.write_bytes(data)
    return store.save_staged_output(
        CapturedGalleryOutput(
            idempotency_key=f"test|{filename}",
            created_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC),
            workflow_id="wf",
            workflow_title="Workflow",
            job_id="job",
            control_id="result",
            output_id="image",
            node_id="9",
            widget_title="Result",
            kind="image",
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
