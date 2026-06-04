"""Transactional updater for packaged Noofy runtime archives."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.paths import NoofyPaths
from app.diagnostics import DiagnosticsSink

NOOFY_RUNTIME_UPDATE_SCHEMA_VERSION = "0.1.0"
NOOFY_RUNTIME_STORE_DIRNAME = "noofy-runtime"
ACTIVE_RUNTIME_FILENAME = "active-runtime.json"
PENDING_RUNTIME_FILENAME = "pending-runtime.json"
CHECKED_RELEASE_FILENAME = "checked-release.json"
RUNTIME_MANIFEST_NAME = "runtime-manifest.json"


class GitHubReleaseAsset(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    browser_download_url: str
    digest: str | None = None
    size: int | None = None


class GitHubRelease(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tag_name: str
    name: str | None = None
    draft: bool = False
    prerelease: bool = False
    published_at: str | None = None
    html_url: str | None = None
    assets: list[GitHubReleaseAsset] = Field(default_factory=list)


class NoofyRuntimeReleaseInfo(BaseModel):
    tag: str
    name: str | None = None
    published_at: str | None = None
    html_url: str | None = None
    asset_name: str
    asset_url: str
    asset_sha256: str
    asset_size: int | None = None
    checked_at: str


class NoofyRuntimeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_id: str
    tag: str
    target: str
    runtime_path: str
    manifest_sha256: str
    backend_sha256: str | None = None
    python_version: str | None = None
    uv_version: str | None = None
    asset_name: str | None = None
    asset_url: str | None = None
    asset_sha256: str | None = None
    staged_at: str | None = None
    activated_at: str | None = None


class NoofyRuntimeStatusResponse(BaseModel):
    available: bool
    disabled_reason: str | None = None
    packaged_runtime: bool
    developer_override: bool = False
    update_repo: str | None = None
    target: str | None = None
    current_version: str | None = None
    current_runtime_id: str | None = None
    current_runtime_path: str | None = None
    current_source: str = "unknown"
    latest: NoofyRuntimeReleaseInfo | None = None
    pending: NoofyRuntimeRecord | None = None
    active: NoofyRuntimeRecord | None = None


class NoofyRuntimeCheckResult(BaseModel):
    status: str
    latest: NoofyRuntimeReleaseInfo | None = None
    disabled_reason: str | None = None


class NoofyRuntimeUpdateJobStatus(BaseModel):
    job_id: str | None = None
    phase: str = "idle"
    status: str = "idle"
    progress_label: str | None = None
    latest_version: str | None = None
    staged_runtime_id: str | None = None
    error: str | None = None


class NoofyRuntimeActivateResult(BaseModel):
    status: str
    active: NoofyRuntimeRecord | None = None
    disabled_reason: str | None = None
    error: str | None = None


ReleaseFetcher = Callable[[str], Awaitable[GitHubRelease]]
ArchiveDownloader = Callable[[str, Path], Awaitable[int]]
SmokeValidator = Callable[[Path], Awaitable[None]]


@dataclass(frozen=True)
class RuntimeManifestValidation:
    manifest: dict[str, Any]
    manifest_sha256: str
    backend_sha256: str | None


class NoofyRuntimeUpdateService:
    def __init__(
        self,
        *,
        paths: NoofyPaths,
        packaged_runtime: bool,
        developer_override: bool,
        update_repo: str | None,
        bundled_resource_dir: Path | None,
        log_store: DiagnosticsSink,
        release_fetcher: ReleaseFetcher | None = None,
        archive_downloader: ArchiveDownloader | None = None,
        smoke_validator: SmokeValidator | None = None,
    ) -> None:
        self.paths = paths
        self.packaged_runtime = packaged_runtime
        self.developer_override = developer_override
        self.update_repo = update_repo.strip() if update_repo else None
        self.bundled_resource_dir = bundled_resource_dir
        self.log_store = log_store
        self.release_fetcher = release_fetcher or fetch_latest_release
        self.archive_downloader = archive_downloader or download_archive
        self.smoke_validator = smoke_validator or self._smoke_validate_runtime
        self._job = NoofyRuntimeUpdateJobStatus()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def status(self) -> NoofyRuntimeStatusResponse:
        available, reason = self._updates_allowed()
        active = self._read_record(self._active_file())
        pending = self._read_record(self._pending_file())
        latest = self._read_latest()
        current_root = self._running_runtime_root()
        current_manifest = (
            _read_json(current_root / RUNTIME_MANIFEST_NAME) if current_root else None
        )
        current_version = _manifest_version(current_manifest) if current_manifest else None
        current_runtime_id = (
            str(current_manifest.get("runtimeId"))
            if isinstance(current_manifest, dict) and current_manifest.get("runtimeId")
            else None
        )
        current_source = (
            "active" if active and current_root == Path(active.runtime_path) else "bundled"
        )
        return NoofyRuntimeStatusResponse(
            available=available,
            disabled_reason=reason,
            packaged_runtime=self.packaged_runtime,
            developer_override=self.developer_override,
            update_repo=self.update_repo,
            target=_current_target_or_none(),
            current_version=current_version,
            current_runtime_id=current_runtime_id,
            current_runtime_path=str(current_root) if current_root else None,
            current_source=current_source,
            latest=latest,
            pending=pending,
            active=active,
        )

    async def check_latest(self) -> NoofyRuntimeCheckResult:
        available, reason = self._updates_allowed()
        if not available:
            return NoofyRuntimeCheckResult(status="blocked", disabled_reason=reason)
        assert self.update_repo is not None
        release = await self.release_fetcher(self.update_repo)
        if release.draft or release.prerelease:
            raise RuntimeError(
                "Latest Noofy runtime release is not a stable published release."
            )
        target = current_runtime_target()
        expected_name = f"noofy-runtime-{target}.zip"
        matches = [asset for asset in release.assets if asset.name == expected_name]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected exactly one GitHub release asset named {expected_name}."
            )
        asset = matches[0]
        asset_sha256 = _asset_sha256(asset.digest)
        info = NoofyRuntimeReleaseInfo(
            tag=release.tag_name,
            name=release.name,
            published_at=release.published_at,
            html_url=release.html_url,
            asset_name=asset.name,
            asset_url=asset.browser_download_url,
            asset_sha256=asset_sha256,
            asset_size=asset.size,
            checked_at=now_iso(),
        )
        _write_json(self._checked_release_file(), info.model_dump(mode="json"))
        self.log_store.add(
            "info",
            "Noofy runtime update check completed",
            "runtime.noofy_update",
            details={"tag": info.tag, "asset": info.asset_name},
        )
        return NoofyRuntimeCheckResult(status="checked", latest=info)

    def update_status(self) -> NoofyRuntimeUpdateJobStatus:
        return self._job

    async def start_stage_latest(self) -> NoofyRuntimeUpdateJobStatus:
        available, reason = self._updates_allowed()
        if not available:
            self._job = NoofyRuntimeUpdateJobStatus(
                phase="blocked", status="blocked", error=reason
            )
            return self._job
        if self._task is not None and not self._task.done():
            return self._job
        latest = self._read_latest()
        if latest is None:
            self._job = NoofyRuntimeUpdateJobStatus(
                phase="failed",
                status="failed",
                error="Check for updates before downloading a Noofy runtime update.",
            )
            return self._job
        job_id = f"noofy-runtime-update-{uuid4().hex}"
        self._job = NoofyRuntimeUpdateJobStatus(
            job_id=job_id,
            phase="queued",
            status="running",
            latest_version=latest.tag,
            progress_label="Queued Noofy runtime update.",
        )
        self._task = asyncio.create_task(self._stage_latest(latest, job_id))
        return self._job

    async def activate_pending(self) -> NoofyRuntimeActivateResult:
        available, reason = self._updates_allowed()
        if not available:
            return NoofyRuntimeActivateResult(status="blocked", disabled_reason=reason)
        pending = self._read_record(self._pending_file())
        if pending is None:
            return NoofyRuntimeActivateResult(
                status="failed",
                error="No validated Noofy runtime update is ready to activate.",
            )
        runtime_path = Path(pending.runtime_path)
        try:
            validation = validate_runtime_root(runtime_path, current_runtime_target())
            _assert_runtime_inside_store(runtime_path, self._runtimes_dir())
        except Exception as exc:
            self.log_store.add(
                "error",
                "Pending Noofy runtime activation failed validation",
                "runtime.noofy_update",
                details={"error": str(exc), "runtime_path": str(runtime_path)},
            )
            return NoofyRuntimeActivateResult(status="failed", error=str(exc))

        active = pending.model_copy(
            update={
                "manifest_sha256": validation.manifest_sha256,
                "backend_sha256": validation.backend_sha256,
                "activated_at": now_iso(),
            }
        )
        _write_json(self._active_file(), _record_payload(active))
        self._pending_file().unlink(missing_ok=True)
        self.log_store.add(
            "info",
            "Noofy runtime update activated for next launch",
            "runtime.noofy_update",
            details={"runtime_id": active.runtime_id, "tag": active.tag},
        )
        return NoofyRuntimeActivateResult(status="activated", active=active)

    async def _stage_latest(self, latest: NoofyRuntimeReleaseInfo, job_id: str) -> None:
        async with self._lock:
            transaction_dir = (
                self.paths.install_transactions_dir / f"install-noofy-runtime-{uuid4().hex}"
            )
            try:
                transaction_dir.mkdir(parents=True, exist_ok=False)
                archive_path = transaction_dir / latest.asset_name
                self._set_job(
                    job_id,
                    "downloading",
                    "Downloading Noofy runtime update.",
                    latest_version=latest.tag,
                )
                await self.archive_downloader(latest.asset_url, archive_path)
                self._set_job(
                    job_id,
                    "verifying",
                    "Verifying downloaded Noofy runtime archive.",
                )
                _assert_sha256(archive_path, latest.asset_sha256, "Noofy runtime archive")

                extracted_root = transaction_dir / "extracted" / "noofy-runtime"
                self._set_job(job_id, "staging", "Extracting Noofy runtime update.")
                extract_runtime_archive(archive_path, extracted_root)
                validation = validate_runtime_root(extracted_root, current_runtime_target())

                runtime_id = str(
                    validation.manifest.get("runtimeId")
                    or f"noofy-runtime-{safe_tag(latest.tag)}"
                )
                final_root = self._runtimes_dir() / safe_tag(runtime_id) / "noofy-runtime"
                active = self._read_record(self._active_file())
                if final_root.exists() and (
                    active is None or Path(active.runtime_path) != final_root
                ):
                    shutil.rmtree(final_root)
                final_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(extracted_root, final_root)

                self._set_job(job_id, "validating", "Validating staged Noofy runtime.")
                validation = validate_runtime_root(final_root, current_runtime_target())
                await self.smoke_validator(final_root)
                record = NoofyRuntimeRecord(
                    runtime_id=runtime_id,
                    tag=latest.tag,
                    target=current_runtime_target(),
                    runtime_path=str(final_root),
                    manifest_sha256=validation.manifest_sha256,
                    backend_sha256=validation.backend_sha256,
                    python_version=_nested_str(validation.manifest, "python", "version"),
                    uv_version=_nested_str(validation.manifest, "uv", "version"),
                    asset_name=latest.asset_name,
                    asset_url=latest.asset_url,
                    asset_sha256=latest.asset_sha256,
                    staged_at=now_iso(),
                )
                _write_json(self._pending_file(), _record_payload(record))
                shutil.rmtree(transaction_dir, ignore_errors=True)
                self._set_job(
                    job_id,
                    "ready_to_activate",
                    "Noofy runtime update was downloaded and validated.",
                    status="completed",
                    latest_version=latest.tag,
                    staged_runtime_id=runtime_id,
                )
                self.log_store.add(
                    "info",
                    "Noofy runtime update staged and validated",
                    "runtime.noofy_update",
                    details={"runtime_id": runtime_id, "tag": latest.tag},
                )
            except Exception as exc:
                if transaction_dir.exists():
                    _write_json(
                        transaction_dir / "quarantine.json",
                        {
                            "schema_version": NOOFY_RUNTIME_UPDATE_SCHEMA_VERSION,
                            "status": "quarantined",
                            "reason": str(exc),
                            "quarantined_at": now_iso(),
                        },
                    )
                self._set_job(
                    job_id,
                    "failed",
                    "Noofy runtime update failed. The current runtime was left unchanged.",
                    status="failed",
                    latest_version=latest.tag,
                    error=str(exc),
                )
                self.log_store.add(
                    "error",
                    "Noofy runtime update failed",
                    "runtime.noofy_update",
                    details={"error": str(exc), "tag": latest.tag},
                )

    async def _smoke_validate_runtime(self, runtime_root: Path) -> None:
        manifest = _read_json(runtime_root / RUNTIME_MANIFEST_NAME)
        python = runtime_root / str(manifest["python"]["executable"])
        backend_dir = runtime_root / "backend"
        temp_data_dir = self.paths.install_transactions_dir / f"smoke-noofy-runtime-{uuid4().hex}"
        env = _packaged_smoke_env(runtime_root, temp_data_dir)
        process = await asyncio.create_subprocess_exec(
            str(python),
            "-m",
            "app",
            "--port",
            "0",
            "--log-level",
            "warning",
            cwd=str(backend_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        try:
            line = await asyncio.wait_for(process.stdout.readline(), timeout=20)
            text = line.decode("utf-8", errors="replace").strip()
            prefix = "NOOFY_BACKEND_API_BASE_URL="
            if not text.startswith(prefix):
                stderr = await _read_available_stderr(process)
                raise RuntimeError(f"Staged backend did not report its API URL. {stderr}")
            api_base = text.removeprefix(prefix)
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{api_base}/paths")
                response.raise_for_status()
        finally:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            shutil.rmtree(temp_data_dir, ignore_errors=True)

    def _updates_allowed(self) -> tuple[bool, str | None]:
        if not self.packaged_runtime:
            return False, "Noofy runtime updates are available only in packaged app builds."
        if self.developer_override:
            return False, "Noofy runtime updates are disabled while developer runtime overrides are active."
        if not self.update_repo:
            return False, "Noofy runtime updates are not configured for this build."
        if not _valid_repo_name(self.update_repo):
            return False, "Noofy runtime update repository is invalid."
        return True, None

    def _set_job(
        self,
        job_id: str,
        phase: str,
        label: str,
        *,
        status: str = "running",
        latest_version: str | None = None,
        staged_runtime_id: str | None = None,
        error: str | None = None,
    ) -> None:
        self._job = NoofyRuntimeUpdateJobStatus(
            job_id=job_id,
            phase=phase,
            status=status,
            progress_label=label,
            latest_version=latest_version or self._job.latest_version,
            staged_runtime_id=staged_runtime_id,
            error=error,
        )

    def _store_dir(self) -> Path:
        return self.paths.runtime_store_dir / NOOFY_RUNTIME_STORE_DIRNAME

    def _runtimes_dir(self) -> Path:
        return self._store_dir() / "runtimes"

    def _active_file(self) -> Path:
        return self._store_dir() / ACTIVE_RUNTIME_FILENAME

    def _pending_file(self) -> Path:
        return self._store_dir() / PENDING_RUNTIME_FILENAME

    def _checked_release_file(self) -> Path:
        return self._store_dir() / CHECKED_RELEASE_FILENAME

    def _read_latest(self) -> NoofyRuntimeReleaseInfo | None:
        path = self._checked_release_file()
        if not path.exists():
            return None
        try:
            latest = NoofyRuntimeReleaseInfo.model_validate(_read_json(path))
            expected_asset = f"noofy-runtime-{current_runtime_target()}.zip"
            if latest.asset_name != expected_asset:
                raise RuntimeError(
                    f"cached asset {latest.asset_name} does not match {expected_asset}"
                )
            return latest
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Ignoring invalid cached Noofy runtime release metadata",
                "runtime.noofy_update",
                details={"path": str(path), "error": str(exc)},
            )
            return None

    def _read_record(self, path: Path) -> NoofyRuntimeRecord | None:
        if not path.exists():
            return None
        try:
            payload = _read_json(path)
            record = payload.get("runtime") if isinstance(payload, dict) else None
            if not isinstance(record, dict):
                return None
            runtime_record = NoofyRuntimeRecord.model_validate(record)
            self._validate_record_pointer(runtime_record)
            return runtime_record
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Ignoring invalid Noofy runtime metadata",
                "runtime.noofy_update",
                details={"path": str(path), "error": str(exc)},
            )
            return None

    def _validate_record_pointer(self, record: NoofyRuntimeRecord) -> None:
        if record.target != current_runtime_target():
            raise RuntimeError(
                "Noofy runtime metadata target mismatch: "
                f"expected {current_runtime_target()}, found {record.target}."
            )
        runtime_path = Path(record.runtime_path)
        _assert_runtime_inside_store(runtime_path, self._runtimes_dir())
        if not runtime_path.is_dir():
            raise RuntimeError(f"Noofy runtime path is missing: {runtime_path}")

    def _running_runtime_root(self) -> Path | None:
        if self.bundled_resource_dir is None:
            return None
        if (self.bundled_resource_dir / RUNTIME_MANIFEST_NAME).is_file():
            return self.bundled_resource_dir
        root = self.bundled_resource_dir / "noofy-runtime"
        return root if root.exists() else None


async def fetch_latest_release(repo: str) -> GitHubRelease:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(
            url, headers={"Accept": "application/vnd.github+json"}
        )
        response.raise_for_status()
        return GitHubRelease.model_validate(response.json())


async def download_archive(url: str, dest: Path) -> int:
    bytes_written = 0
    async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as file:
                async for chunk in response.aiter_bytes():
                    file.write(chunk)
                    bytes_written += len(chunk)
    return bytes_written


def extract_runtime_archive(archive_path: Path, dest: Path) -> None:
    raw_dest = dest.parent / "_raw-runtime"
    shutil.rmtree(raw_dest, ignore_errors=True)
    raw_dest.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            validate_zip_member(member)
            archive.extract(member, raw_dest)
    source = raw_dest / "noofy-runtime"
    if not source.is_dir():
        raise RuntimeError(
            "Downloaded Noofy runtime archive must contain a top-level noofy-runtime directory."
        )
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(source, dest)
    shutil.rmtree(raw_dest, ignore_errors=True)


def validate_zip_member(member: zipfile.ZipInfo) -> None:
    path = PurePosixPath(member.filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"Unsafe Noofy runtime archive path: {member.filename}")
    if not path.parts or path.parts[0] != "noofy-runtime":
        raise RuntimeError(
            f"Noofy runtime archive member must be inside noofy-runtime/: {member.filename}"
        )
    mode = member.external_attr >> 16
    if mode & 0o170000 == 0o120000:
        raise RuntimeError(f"Noofy runtime archive contains a symlink: {member.filename}")


def validate_runtime_root(runtime_root: Path, target: str) -> RuntimeManifestValidation:
    manifest_path = runtime_root / RUNTIME_MANIFEST_NAME
    if not manifest_path.is_file():
        raise RuntimeError(f"Noofy runtime manifest is missing: {manifest_path}")
    manifest = _read_json(manifest_path)
    if manifest.get("schemaVersion") != 1:
        raise RuntimeError("Noofy runtime manifest schemaVersion must be 1.")
    if manifest.get("layoutVersion") != 1:
        raise RuntimeError("Noofy runtime manifest layoutVersion must be 1.")
    if manifest.get("target") != target:
        raise RuntimeError(
            f"Noofy runtime target mismatch: expected {target}, found {manifest.get('target')}."
        )

    python_path = _manifest_file(
        runtime_root, manifest, ("python", "executable"), "Packaged Python"
    )
    uv_path = _manifest_file(runtime_root, manifest, ("uv", "executable"), "Packaged uv")
    _assert_sha256(
        python_path, _nested_str(manifest, "python", "sha256"), "Packaged Python"
    )
    _assert_sha256(uv_path, _nested_str(manifest, "uv", "sha256"), "Packaged uv")

    backend = manifest.get("backend")
    if not isinstance(backend, dict):
        raise RuntimeError("Noofy runtime manifest is missing backend metadata.")
    if (
        backend.get("packagedPath") != "backend"
        or backend.get("appPath") != "backend/app"
        or backend.get("pyprojectPath") != "backend/pyproject.toml"
    ):
        raise RuntimeError("Noofy runtime manifest backend paths do not match the runtime layout.")

    for path, label in (
        (runtime_root / "backend" / "app" / "__main__.py", "backend module entrypoint"),
        (runtime_root / "backend" / "pyproject.toml", "backend metadata"),
        (
            runtime_root / "backend" / "app" / "workflows" / "packages",
            "bundled starter workflow packages",
        ),
        (runtime_root / "comfyui" / "main.py", "bundled ComfyUI entrypoint"),
    ):
        if not path.exists():
            raise RuntimeError(f"Noofy runtime is missing its {label}: {path}")

    actual_backend_hash = backend_artifact_hash(runtime_root / "backend")
    expected_backend_hash = backend.get("sha256")
    if expected_backend_hash and expected_backend_hash != actual_backend_hash:
        raise RuntimeError(
            f"Noofy runtime backend artifact hash mismatch: expected {expected_backend_hash}, got {actual_backend_hash}."
        )
    return RuntimeManifestValidation(
        manifest=manifest,
        manifest_sha256=sha256_file(manifest_path),
        backend_sha256=actual_backend_hash,
    )


def backend_artifact_hash(backend_root: Path) -> str:
    files = [backend_root / "pyproject.toml", *walk_files(backend_root / "app")]
    digest = hashlib.sha256()
    for file_path in sorted(files):
        relative_path = file_path.relative_to(backend_root).as_posix()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(file_path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def walk_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for entry in root.iterdir():
        if (
            entry.name == "__pycache__"
            or entry.name == ".DS_Store"
            or entry.name.endswith(".pyc")
        ):
            continue
        if entry.is_dir():
            files.extend(walk_files(entry))
        elif entry.is_file():
            files.append(entry)
    return files


def current_runtime_target() -> str:
    if sys.platform == "darwin" and _machine() == "arm64":
        return "macos-arm64"
    if sys.platform == "win32" and _machine() in {"amd64", "x86_64"}:
        return "windows-x64"
    if sys.platform.startswith("linux") and _machine() in {"amd64", "x86_64"}:
        return "linux-x64"
    raise RuntimeError(
        f"Unsupported packaged Noofy runtime target: platform={sys.platform}, machine={_machine()}."
    )


def _current_target_or_none() -> str | None:
    try:
        return current_runtime_target()
    except RuntimeError:
        return None


def _machine() -> str:
    import platform

    return platform.machine().lower()


def _packaged_smoke_env(runtime_root: Path, data_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "COMFYUI_BASE_URL",
        "COMFYUI_MANAGED_HOST",
        "COMFYUI_MANAGED_PORT",
        "COMFYUI_REPO_DIR",
        "COMFYUI_PYTHON_EXECUTABLE",
        "COMFYUI_RUNTIME_MODE",
        "COMFYUI_WS_URL",
        "CONDA_PREFIX",
        "NOOFY_BACKEND_DIR",
        "NOOFY_BACKEND_PYTHON",
        "NOOFY_BACKEND_SIDECAR",
        "NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES",
        "NOOFY_FORCE_PACKAGED_BACKEND",
        "NOOFY_PACKAGED_RUNTIME_DIR",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ):
        env.pop(key, None)
    manifest = _read_json(runtime_root / RUNTIME_MANIFEST_NAME)
    env["COMFYUI_RUNTIME_MODE"] = "managed"
    env["COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE"] = str(
        runtime_root / str(manifest["python"]["executable"])
    )
    env["NOOFY_UV_EXECUTABLE"] = str(runtime_root / str(manifest["uv"]["executable"]))
    env["NOOFY_BUNDLED_RESOURCE_DIR"] = str(runtime_root.parent)
    env["NOOFY_BUNDLED_COMFYUI_DIR"] = str(runtime_root / "comfyui")
    env["NOOFY_BUNDLED_WORKFLOWS_DIR"] = str(
        runtime_root / "backend" / "app" / "workflows" / "packages"
    )
    env["NOOFY_DATA_DIR"] = str(data_dir)
    env["PYTHONNOUSERSITE"] = "1"
    return env


async def _read_available_stderr(process: asyncio.subprocess.Process) -> str:
    if process.stderr is None:
        return ""
    try:
        data = await asyncio.wait_for(process.stderr.read(4096), timeout=0.1)
    except asyncio.TimeoutError:
        return ""
    return data.decode("utf-8", errors="replace")


def _manifest_file(runtime_root: Path, manifest: dict[str, Any], path_keys: tuple[str, str], label: str) -> Path:
    relative = _nested_str(manifest, *path_keys)
    if not relative:
        raise RuntimeError(f"{label} path is missing from Noofy runtime manifest.")
    relative_path = Path(relative)
    if relative_path.is_absolute() or any(part == ".." for part in relative_path.parts):
        raise RuntimeError(f"{label} path must stay inside the Noofy runtime root: {relative}")
    path = runtime_root / relative_path
    if not path.is_file():
        raise RuntimeError(f"{label} is missing: {path}")
    return path


def _nested_str(payload: dict[str, Any], *keys: str) -> str | None:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value if isinstance(value, str) else None


def _manifest_version(manifest: dict[str, Any] | None) -> str | None:
    if not isinstance(manifest, dict):
        return None
    backend = manifest.get("backend")
    if isinstance(backend, dict) and isinstance(backend.get("version"), str):
        return backend["version"]
    if isinstance(manifest.get("runtimeId"), str):
        return manifest["runtimeId"]
    return None


def _asset_sha256(digest: str | None) -> str:
    if not digest or not digest.startswith("sha256:"):
        raise RuntimeError("Noofy runtime update asset is missing a GitHub SHA-256 digest.")
    value = digest.removeprefix("sha256:").lower()
    if not re.fullmatch(r"[a-f0-9]{64}", value):
        raise RuntimeError("Noofy runtime update asset SHA-256 digest is invalid.")
    return value


def _assert_sha256(path: Path, expected: str | None, label: str) -> None:
    if not expected or not re.fullmatch(r"[a-f0-9]{64}", expected.lower()):
        raise RuntimeError(f"{label} SHA-256 is missing or invalid.")
    actual = sha256_file(path)
    if actual != expected.lower():
        raise RuntimeError(f"{label} checksum mismatch: expected {expected.lower()}, got {actual}.")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object in {path}.")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _record_payload(record: NoofyRuntimeRecord) -> dict[str, Any]:
    return {
        "schema_version": NOOFY_RUNTIME_UPDATE_SCHEMA_VERSION,
        "updated_at": now_iso(),
        "runtime": record.model_dump(mode="json"),
    }


def _assert_runtime_inside_store(runtime_path: Path, runtimes_dir: Path) -> None:
    runtime_real = runtime_path.resolve(strict=False)
    store_real = runtimes_dir.resolve(strict=False)
    try:
        runtime_real.relative_to(store_real)
    except ValueError as exc:
        raise RuntimeError("Noofy runtime activation path is outside app-managed runtime storage.") from exc


def _valid_repo_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value))


def safe_tag(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", tag).strip("-") or "unknown"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
