import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from app.core.paths import resolve_paths
from app.diagnostics import LogStore
from app.runtime.noofy_runtime.service import (
    CHECKED_RELEASE_FILENAME,
    GitHubRelease,
    GitHubReleaseAsset,
    NoofyRuntimeUpdateService,
    extract_runtime_archive,
    backend_artifact_hash,
    current_runtime_target,
)


@pytest.fixture(autouse=True)
def _supported_target(monkeypatch):
    if sys.platform == "darwin":
        machine = "arm64"
    elif sys.platform == "win32":
        machine = "amd64"
    else:
        machine = "x86_64"
    monkeypatch.setattr("app.runtime.noofy_runtime.service._machine", lambda: machine)


def _runtime_tree(root: Path, *, target: str | None = None, backend_hash: str | None = None) -> None:
    target = target or current_runtime_target()
    python = root / "python" / ("python.exe" if target == "windows-x64" else "bin/python3")
    uv = root / "python" / ("Scripts/uv.exe" if target == "windows-x64" else "bin/uv")
    python.parent.mkdir(parents=True, exist_ok=True)
    uv.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("python", encoding="utf-8")
    uv.write_text("uv", encoding="utf-8")
    (root / "backend" / "app" / "workflows" / "packages").mkdir(parents=True, exist_ok=True)
    (root / "backend" / "app" / "workflows" / "packages" / ".keep").write_text("", encoding="utf-8")
    (root / "backend" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "backend" / "app" / "__main__.py").write_text("", encoding="utf-8")
    (root / "backend" / "app" / "main.py").write_text("", encoding="utf-8")
    (root / "backend" / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n', encoding="utf-8")
    (root / "comfyui").mkdir(parents=True, exist_ok=True)
    (root / "comfyui" / "main.py").write_text("", encoding="utf-8")
    actual_backend_hash = backend_artifact_hash(root / "backend")
    manifest = {
        "schemaVersion": 1,
        "layoutVersion": 1,
        "runtimeId": "noofy-runtime-test",
        "target": target,
        "python": {
            "version": "3.13.1",
            "buildId": "test-python",
            "executable": python.relative_to(root).as_posix(),
            "sha256": _sha256(python),
        },
        "uv": {
            "version": "0.5.0",
            "executable": uv.relative_to(root).as_posix(),
            "sha256": _sha256(uv),
        },
        "backend": {
            "version": "1.2.3",
            "packagedPath": "backend",
            "appPath": "backend/app",
            "pyprojectPath": "backend/pyproject.toml",
            "sha256": backend_hash or actual_backend_hash,
        },
    }
    (root / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _runtime_zip(path: Path, runtime_root: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for file in sorted(runtime_root.rglob("*")):
            if file.is_file():
                archive.write(file, Path("noofy-runtime") / file.relative_to(runtime_root))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release(archive_path: Path) -> GitHubRelease:
    return GitHubRelease(
        tag_name="v1.2.3",
        assets=[
            GitHubReleaseAsset(
                name=f"noofy-runtime-{current_runtime_target()}.zip",
                browser_download_url="https://example.test/noofy-runtime.zip",
                digest=f"sha256:{_sha256(archive_path)}",
                size=archive_path.stat().st_size,
            )
        ],
    )


@pytest.mark.anyio
async def test_status_does_not_fetch_latest_release(tmp_path: Path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    fetch_count = 0

    async def fetcher(repo: str) -> GitHubRelease:
        nonlocal fetch_count
        fetch_count += 1
        raise AssertionError("status must not fetch GitHub")

    service = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=True,
        developer_override=False,
        update_repo="noofy/app",
        bundled_resource_dir=None,
        log_store=LogStore(),
        release_fetcher=fetcher,
    )

    status = service.status()

    assert status.available
    assert fetch_count == 0


@pytest.mark.anyio
async def test_status_ignores_invalid_cached_metadata(tmp_path: Path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    metadata_dir = paths.runtime_store_dir / "noofy-runtime"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / CHECKED_RELEASE_FILENAME).write_text("{bad json", encoding="utf-8")
    (metadata_dir / "pending-runtime.json").write_text(
        json.dumps({"runtime": {"runtime_id": 3}}),
        encoding="utf-8",
    )
    outside = tmp_path / "outside" / "noofy-runtime"
    outside.mkdir(parents=True)
    (metadata_dir / "active-runtime.json").write_text(
        json.dumps(
            {
                "runtime": {
                    "runtime_id": "outside",
                    "tag": "v1.2.3",
                    "target": current_runtime_target(),
                    "runtime_path": str(outside),
                    "manifest_sha256": "0" * 64,
                }
            }
        ),
        encoding="utf-8",
    )
    service = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=True,
        developer_override=False,
        update_repo="noofy/app",
        bundled_resource_dir=None,
        log_store=LogStore(),
    )

    status = service.status()

    assert status.available
    assert status.latest is None
    assert status.pending is None
    assert status.active is None


@pytest.mark.anyio
async def test_check_latest_release_is_explicit(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.zip"
    source = tmp_path / "source" / "noofy-runtime"
    _runtime_tree(source)
    _runtime_zip(archive, source)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()

    service = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=True,
        developer_override=False,
        update_repo="noofy/app",
        bundled_resource_dir=None,
        log_store=LogStore(),
        release_fetcher=lambda repo: _async_release(_release(archive)),
    )

    result = await service.check_latest()

    assert result.status == "checked"
    assert result.latest is not None
    assert result.latest.tag == "v1.2.3"


@pytest.mark.anyio
async def test_stage_validates_without_activating(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.zip"
    source = tmp_path / "source" / "noofy-runtime"
    _runtime_tree(source)
    _runtime_zip(archive, source)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()
    smoke_calls: list[Path] = []

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(archive.read_bytes())
        return dest.stat().st_size

    async def smoke(runtime_root: Path) -> None:
        smoke_calls.append(runtime_root)

    service = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=True,
        developer_override=False,
        update_repo="noofy/app",
        bundled_resource_dir=None,
        log_store=LogStore(),
        release_fetcher=lambda repo: _async_release(_release(archive)),
        archive_downloader=downloader,
        smoke_validator=smoke,
    )

    await service.check_latest()
    status = await service.start_stage_latest()
    while status.status == "running":
        await service._task
        status = service.update_status()

    current = service.status()
    assert status.status == "completed"
    assert status.phase == "ready_to_activate"
    assert current.pending is not None
    assert current.active is None
    assert smoke_calls and smoke_calls[0].is_relative_to(paths.runtime_store_dir / "noofy-runtime" / "runtimes")


@pytest.mark.anyio
async def test_validation_failure_preserves_active_runtime(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.zip"
    source = tmp_path / "source" / "noofy-runtime"
    _runtime_tree(source, backend_hash="0" * 64)
    _runtime_zip(archive, source)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(archive.read_bytes())
        return dest.stat().st_size

    service = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=True,
        developer_override=False,
        update_repo="noofy/app",
        bundled_resource_dir=None,
        log_store=LogStore(),
        release_fetcher=lambda repo: _async_release(_release(archive)),
        archive_downloader=downloader,
        smoke_validator=lambda runtime_root: _async_noop(),
    )

    await service.check_latest()
    status = await service.start_stage_latest()
    while status.status == "running":
        await service._task
        status = service.update_status()

    assert status.status == "failed"
    assert "backend artifact hash mismatch" in (status.error or "")
    assert service.status().active is None
    assert service.status().pending is None


@pytest.mark.anyio
async def test_activate_pending_runtime_writes_active_pointer(tmp_path: Path) -> None:
    archive = tmp_path / "runtime.zip"
    source = tmp_path / "source" / "noofy-runtime"
    bundled = tmp_path / "bundled" / "noofy-runtime"
    _runtime_tree(source)
    _runtime_tree(bundled)
    _runtime_zip(archive, source)
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()

    async def downloader(url: str, dest: Path) -> int:
        dest.write_bytes(archive.read_bytes())
        return dest.stat().st_size

    service = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=True,
        developer_override=False,
        update_repo="noofy/app",
        bundled_resource_dir=bundled,
        log_store=LogStore(),
        release_fetcher=lambda repo: _async_release(_release(archive)),
        archive_downloader=downloader,
        smoke_validator=lambda runtime_root: _async_noop(),
    )

    await service.check_latest()
    stage = await service.start_stage_latest()
    while stage.status == "running":
        await service._task
        stage = service.update_status()
    activated = await service.activate_pending()
    status = service.status()

    assert activated.status == "activated"
    assert status.active is not None
    assert status.pending is None
    assert Path(status.active.runtime_path).is_relative_to(
        paths.runtime_store_dir / "noofy-runtime" / "runtimes"
    )
    assert status.current_runtime_path == str(bundled)
    assert status.current_source == "bundled"


def test_archive_rejects_members_outside_noofy_runtime(tmp_path: Path) -> None:
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("noofy-runtime/runtime-manifest.json", "{}")
        archive.writestr("README.txt", "unexpected")

    with pytest.raises(RuntimeError, match="inside noofy-runtime"):
        extract_runtime_archive(archive_path, tmp_path / "extracted" / "noofy-runtime")


@pytest.mark.anyio
async def test_updates_disabled_for_source_and_developer_override(tmp_path: Path) -> None:
    paths = resolve_paths(env={"NOOFY_DATA_DIR": str(tmp_path / "data")})
    paths.ensure_directories()

    source = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=False,
        developer_override=False,
        update_repo="noofy/app",
        bundled_resource_dir=None,
        log_store=LogStore(),
    )
    override = NoofyRuntimeUpdateService(
        paths=paths,
        packaged_runtime=True,
        developer_override=True,
        update_repo="noofy/app",
        bundled_resource_dir=None,
        log_store=LogStore(),
    )

    assert not source.status().available
    assert not override.status().available


async def _async_release(release: GitHubRelease) -> GitHubRelease:
    return release


async def _async_noop(*args) -> None:
    del args
