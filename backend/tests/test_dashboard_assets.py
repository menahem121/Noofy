"""Tests for DashboardAssetService."""
import struct
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes as api_routes
from app.main import create_app
from app.workflows.assets import AssetUploadError, DashboardAssetService


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


def test_upload_dashboard_asset_route_uses_workflow_path_param(tmp_path: Path) -> None:
    old_service = api_routes._asset_service
    api_routes._asset_service = DashboardAssetService(tmp_path / "assets")
    app = FastAPI()
    app.include_router(api_routes.router, prefix="/api")
    try:
        response = TestClient(app).post(
            "/api/workflows/wf-1/assets/image",
            files={"image": ("img.png", PNG_BYTES, "image/png")},
        )
    finally:
        api_routes._asset_service = old_service

    assert response.status_code == 200
    body = response.json()
    assert body["asset_id"].endswith(".png")
    assert (tmp_path / "assets" / body["asset_id"]).exists()


def test_serve_dashboard_asset_route_returns_file_and_metadata(tmp_path: Path) -> None:
    old_service = api_routes._asset_service
    api_routes._asset_service = DashboardAssetService(tmp_path / "assets")
    stored = api_routes._asset_service.store(PNG_BYTES, "image/png", "input.png")
    app = FastAPI()
    app.include_router(api_routes.router, prefix="/api")
    try:
        client = TestClient(app)
        image_response = client.get(f"/api/assets/{stored['asset_id']}")
        metadata_response = client.get(f"/api/assets/{stored['asset_id']}/metadata")
    finally:
        api_routes._asset_service = old_service

    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content == PNG_BYTES
    assert metadata_response.status_code == 200
    assert metadata_response.json()["original_filename"] == "input.png"


def test_serve_dashboard_asset_route_rejects_bad_asset_id(tmp_path: Path) -> None:
    old_service = api_routes._asset_service
    api_routes._asset_service = DashboardAssetService(tmp_path / "assets")
    app = FastAPI()
    app.include_router(api_routes.router, prefix="/api")
    try:
        response = TestClient(app).get("/api/assets/not-an-image.exe")
    finally:
        api_routes._asset_service = old_service

    assert response.status_code == 400


def test_dashboard_asset_routes_require_bearer_token_when_auth_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOOFY_API_TOKEN", "secret-token")
    old_service = api_routes._asset_service
    api_routes._asset_service = DashboardAssetService(tmp_path / "assets")
    stored = api_routes._asset_service.store(PNG_BYTES, "image/png", "secure.png")
    try:
        with TestClient(create_app()) as client:
            missing = client.get(f"/api/assets/{stored['asset_id']}")
            allowed = client.get(
                f"/api/assets/{stored['asset_id']}",
                headers={"Authorization": "Bearer secret-token"},
            )
    finally:
        api_routes._asset_service = old_service

    assert missing.status_code == 401
    assert allowed.status_code == 200
