"""Tests for DashboardAssetService."""
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.workflows.assets import AssetUploadError, DashboardAssetService


class FakeEngineService:
    def __init__(self) -> None:
        self.workflow_library_service = self

    def list_workflows(self):
        return []

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


PNG_BYTES = _make_png()


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
