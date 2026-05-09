"""Managed ComfyUI updater for upstream GitHub releases."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

import httpx
import websockets
from pydantic import BaseModel, ConfigDict, Field

from app.core.paths import NoofyPaths
from app.engine.diagnostics import DiagnosticsSink
from app.engine.models import ComfyUIVersionMetadata, ProcessActionResult
from app.runtime.environment import CommandRunner, RuntimeEnvironment
from app.runtime.manager import RuntimeManager
from app.runtime.profiles import (
    RuntimeSourceOriginKind,
    RuntimeSourceStatus,
    build_comfyui_source_manifest,
)

UPSTREAM_REPO = "Comfy-Org/ComfyUI"
UPSTREAM_RELEASES_API = f"https://api.github.com/repos/{UPSTREAM_REPO}/releases"
ACTIVE_COMFYUI_FILENAME = "active-comfyui.json"
LOCAL_VALIDATION_FILENAME = "local-validation.json"
UPDATE_METADATA_SCHEMA_VERSION = "0.1.0"
AUTOMATIC_REPAIR_MAX_ATTEMPTS = 2
AUTOMATIC_REPAIR_WINDOW = timedelta(hours=24)


class UpstreamComfyUIRelease(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    tag_name: str = Field(min_length=1)
    name: str | None = None
    draft: bool = False
    prerelease: bool = False
    published_at: str | None = None
    zipball_url: str | None = None
    tarball_url: str | None = None
    html_url: str | None = None
    target_commitish: str | None = None


class LocalComfyUIVersionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str
    available_upstream: bool = False
    installed: bool = False
    active: bool = False
    locally_verified: bool = False
    failed_validation: bool = False
    failed_reason: str | None = None
    source_hash: str | None = None
    commit_sha: str | None = None
    source_path: str | None = None
    env_path: str | None = None
    archive_url: str | None = None
    installed_at: str | None = None
    activated_at: str | None = None
    validated_at: str | None = None
    repair_status: str | None = None
    repair_attempt_count: int = 0
    last_repair_attempt_at: str | None = None
    last_repair_error: str | None = None
    repair_blocked_until: str | None = None
    incompatible: bool = False
    incompatible_reason: str | None = None
    last_successfully_started_at: str | None = None


class ComfyUIVersionOption(BaseModel):
    tag: str
    label: str
    status: str
    available_upstream: bool = False
    installed: bool = False
    active: bool = False
    locally_verified: bool = False
    failed_validation: bool = False
    failed_reason: str | None = None
    source_hash: str | None = None
    commit_sha: str | None = None
    published_at: str | None = None
    repair_status: str | None = None
    repair_attempt_count: int = 0
    last_repair_attempt_at: str | None = None
    last_repair_error: str | None = None
    repair_blocked_until: str | None = None
    incompatible: bool = False
    incompatible_reason: str | None = None


class ComfyUIVersionsResponse(BaseModel):
    updates_allowed: bool
    disabled_reason: str | None = None
    upstream_checked: bool = False
    latest_tag: str | None = None
    current: LocalComfyUIVersionRecord | None = None
    options: list[ComfyUIVersionOption] = Field(default_factory=list)
    release_fetch_error: str | None = None


class ComfyUIUpdateRequest(BaseModel):
    version: str = "latest"


class ComfyUIRebuildRequest(BaseModel):
    version: str = "current"


class ComfyUIUpdateJobStatus(BaseModel):
    job_id: str | None = None
    operation: str = "update"
    phase: str = "idle"
    selected_version: str | None = None
    resolved_tag: str | None = None
    progress_label: str | None = None
    status: str = "idle"
    error: str | None = None
    installed_path: str | None = None
    activated_version: str | None = None
    repair_reason: str | None = None
    repair_attempt_count: int | None = None
    repair_blocked_until: str | None = None
    fallback_version: str | None = None
    incompatible_version: str | None = None


@dataclass(frozen=True)
class ActiveRuntimeSelection:
    repo_dir: Path
    python_executable: str | None
    venv_dir: Path | None
    version_metadata: ComfyUIVersionMetadata


ReleaseFetcher = Callable[[], Awaitable[list[UpstreamComfyUIRelease]]]
ArchiveDownloader = Callable[[str, Path], Awaitable[int]]
SmokeTester = Callable[[Path, Path, LocalComfyUIVersionRecord], Awaitable[None]]


class ComfyUIRepairError(RuntimeError):
    def __init__(self, message: str, *, category: str = "repair_failed") -> None:
        super().__init__(message)
        self.category = category


def resolve_active_runtime_selection(
    paths: NoofyPaths,
    *,
    fallback_repo_dir: Path,
    fallback_python_executable: str | None,
    mode: str,
    developer_override: bool,
) -> ActiveRuntimeSelection:
    if mode != "managed":
        return ActiveRuntimeSelection(
            repo_dir=fallback_repo_dir,
            python_executable=fallback_python_executable,
            venv_dir=None,
            version_metadata=ComfyUIVersionMetadata(source_kind="external"),
        )
    if developer_override:
        return ActiveRuntimeSelection(
            repo_dir=fallback_repo_dir,
            python_executable=fallback_python_executable,
            venv_dir=None,
            version_metadata=ComfyUIVersionMetadata(source_kind="developer_override"),
        )

    active = _read_active_record(paths)
    if active and active.source_path and active.env_path:
        source_path = Path(active.source_path)
        env_path = Path(active.env_path)
        python = _venv_python(env_path)
        return ActiveRuntimeSelection(
            repo_dir=source_path,
            python_executable=python,
            venv_dir=env_path,
            version_metadata=ComfyUIVersionMetadata(
                active_tag=active.tag,
                source_hash=active.source_hash,
                source_kind="installed",
                local_validation_status=(
                    "locally_verified" if active.locally_verified else "installed"
                ),
            ),
        )

    return ActiveRuntimeSelection(
        repo_dir=fallback_repo_dir,
        python_executable=fallback_python_executable,
        venv_dir=None,
        version_metadata=ComfyUIVersionMetadata(source_kind="bundled"),
    )


class ComfyUIUpdateService:
    def __init__(
        self,
        *,
        paths: NoofyPaths,
        runtime_manager: RuntimeManager,
        mode: str,
        developer_override: bool,
        bootstrap_python_executable: str,
        torch_cuda_index_url: str | None,
        torch_cpu_index_url: str,
        log_store: DiagnosticsSink,
        bundled_repo_dir: Path | None = None,
        bundled_python_executable: str | None = None,
        release_fetcher: ReleaseFetcher | None = None,
        archive_downloader: ArchiveDownloader | None = None,
        command_runner: CommandRunner | None = None,
        smoke_tester: SmokeTester | None = None,
    ) -> None:
        self.paths = paths
        self.runtime_manager = runtime_manager
        self.mode = mode
        self.developer_override = developer_override
        self.bootstrap_python_executable = bootstrap_python_executable
        self.torch_cuda_index_url = torch_cuda_index_url
        self.torch_cpu_index_url = torch_cpu_index_url
        self.bundled_repo_dir = bundled_repo_dir
        self.bundled_python_executable = bundled_python_executable
        self.log_store = log_store
        self.release_fetcher = release_fetcher or self._fetch_upstream_releases
        self.archive_downloader = archive_downloader or self._download_archive
        self.command_runner = command_runner
        self.smoke_tester = smoke_tester or self._default_smoke_test
        self._job = ComfyUIUpdateJobStatus()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def versions(self, *, check_upstream: bool = False) -> ComfyUIVersionsResponse:
        allowed, reason = self._updates_allowed()
        releases: list[UpstreamComfyUIRelease] = []
        fetch_error: str | None = None
        if allowed and check_upstream:
            try:
                releases = _stable_sorted_releases(await self.release_fetcher())
            except (
                Exception
            ) as exc:  # pragma: no cover - exercised through API behavior tests.
                fetch_error = str(exc)

        records = self._read_records()
        active = self._active_record()
        if active is not None and active.tag in records:
            active = _merge_active_with_record(active, records[active.tag])
        latest = releases[0].tag_name if releases else None
        options: list[ComfyUIVersionOption] = []
        seen: set[str] = set()
        for release in releases:
            record = records.get(release.tag_name)
            options.append(
                _version_option(
                    release, record, active_tag=active.tag if active else None
                )
            )
            seen.add(release.tag_name)
        for tag, record in sorted(
            records.items(), key=lambda item: _version_sort_key(item[0]), reverse=True
        ):
            if tag in seen:
                continue
            options.append(
                _version_option(None, record, active_tag=active.tag if active else None)
            )

        return ComfyUIVersionsResponse(
            updates_allowed=allowed,
            disabled_reason=reason,
            upstream_checked=allowed and check_upstream,
            latest_tag=latest,
            current=active,
            options=options,
            release_fetch_error=fetch_error,
        )

    async def start_update(
        self, request: ComfyUIUpdateRequest
    ) -> ComfyUIUpdateJobStatus:
        allowed, reason = self._updates_allowed()
        if not allowed:
            self._job = ComfyUIUpdateJobStatus(
                status="blocked", phase="blocked", error=reason
            )
            return self._job
        if self._task is not None and not self._task.done():
            return self._job

        job_id = f"comfyui-update-{uuid4().hex}"
        self._job = ComfyUIUpdateJobStatus(
            job_id=job_id,
            operation="update",
            phase="queued",
            selected_version=request.version,
            progress_label="Queued ComfyUI update.",
            status="running",
        )
        self._task = asyncio.create_task(self._run_update(request.version, job_id))
        return self._job

    async def start_rebuild(
        self, request: ComfyUIRebuildRequest
    ) -> ComfyUIUpdateJobStatus:
        allowed, reason = self._updates_allowed()
        if not allowed:
            self._job = ComfyUIUpdateJobStatus(
                operation="rebuild", status="blocked", phase="blocked", error=reason
            )
            return self._job
        if self._task is not None and not self._task.done():
            return self._job

        job_id = f"comfyui-rebuild-{uuid4().hex}"
        self._job = ComfyUIUpdateJobStatus(
            job_id=job_id,
            operation="rebuild",
            phase="queued",
            selected_version=request.version,
            progress_label="Queued ComfyUI environment rebuild.",
            status="running",
            repair_reason="manual_rebuild",
        )
        self._task = asyncio.create_task(self._run_rebuild(request.version, job_id))
        return self._job

    def update_status(self) -> ComfyUIUpdateJobStatus:
        return self._job

    async def repair_after_start_failure(
        self,
        failed_result: ProcessActionResult,
        *,
        repair_reason: str,
    ) -> ProcessActionResult:
        allowed, reason = self._updates_allowed()
        if not allowed:
            return failed_result
        if not _start_failure_is_repairable(
            failed_result.status, failed_result.comfyui.error
        ):
            return failed_result
        active = self._active_record()
        if active is None or not active.installed:
            return failed_result
        if self._task is not None and not self._task.done():
            return failed_result

        blocked_until = _repair_blocked_until(active)
        if blocked_until is not None:
            active = active.model_copy(
                update={
                    "repair_status": "repair_blocked",
                    "repair_blocked_until": blocked_until,
                }
            )
            self._upsert_record(active)
            self._job = ComfyUIUpdateJobStatus(
                operation="repair",
                phase="repair_blocked",
                selected_version=active.tag,
                resolved_tag=active.tag,
                progress_label="Automatic ComfyUI repair is temporarily blocked for this version.",
                status="blocked",
                error="Automatic repair reached its retry limit. Try a manual update or wait before retrying.",
                repair_reason=repair_reason,
                repair_attempt_count=active.repair_attempt_count,
                repair_blocked_until=blocked_until,
            )
            self.log_store.add(
                "warning",
                "Automatic ComfyUI repair blocked by retry policy",
                "runtime.comfyui_repair",
                details={
                    "tag": active.tag,
                    "blocked_until": blocked_until,
                    "reason": repair_reason,
                },
            )
            return ProcessActionResult(
                status="repair_blocked", comfyui=await self.runtime_manager.status()
            )

        job_id = f"comfyui-repair-{uuid4().hex}"
        attempt = active.repair_attempt_count + 1
        active = active.model_copy(
            update={
                "repair_status": "repair_started",
                "repair_attempt_count": attempt,
                "last_repair_attempt_at": _now_iso(),
                "last_repair_error": None,
                "repair_blocked_until": None,
            }
        )
        self._upsert_record(active)
        self._job = ComfyUIUpdateJobStatus(
            job_id=job_id,
            operation="repair",
            phase="repair_started",
            selected_version=active.tag,
            resolved_tag=active.tag,
            progress_label=f"Repairing managed ComfyUI {active.tag}.",
            status="running",
            repair_reason=repair_reason,
            repair_attempt_count=attempt,
        )
        self.log_store.add(
            "warning",
            "Automatic ComfyUI repair started",
            "runtime.comfyui_repair",
            details={"tag": active.tag, "reason": repair_reason, "attempt": attempt},
        )

        try:
            async with self._lock:
                repaired = await self._repair_record(
                    active, job_id=job_id, repair_reason=repair_reason
                )
                self._set_job(
                    job_id,
                    "activating",
                    active.tag,
                    "Activating repaired ComfyUI runtime.",
                    operation="repair",
                    resolved_tag=active.tag,
                    repair_reason=repair_reason,
                    repair_attempt_count=attempt,
                )
                await self._activate(repaired)
                started = await self.runtime_manager.start()
                if started.status in {"started", "already_running"}:
                    current = self._active_record() or repaired
                    current = _clear_repair_state(
                        current.model_copy(
                            update={"last_successfully_started_at": _now_iso()}
                        )
                    )
                    self._upsert_record(current)
                    self._write_active_record(current)
                    self._set_job(
                        job_id,
                        "completed",
                        active.tag,
                        f"ComfyUI {active.tag} was repaired and started.",
                        operation="repair",
                        status="completed",
                        resolved_tag=active.tag,
                        installed_path=current.source_path,
                        activated_version=active.tag,
                        repair_reason=repair_reason,
                        repair_attempt_count=attempt,
                    )
                    return ProcessActionResult(
                        status="repair_completed_started", comfyui=started.comfyui
                    )

                message = (
                    started.comfyui.error
                    or f"Managed ComfyUI start returned {started.status}"
                )
                failed = repaired.model_copy(
                    update={
                        "repair_status": "startup_failed",
                        "last_repair_error": message,
                    }
                )
                blocked = _repair_blocked_until(failed)
                if blocked is not None:
                    failed = failed.model_copy(update={"repair_blocked_until": blocked})
                self._upsert_record(failed)
                fallback = await self._fallback_after_repair_failure(
                    job_id, active, message, repair_reason
                )
                return fallback
        except ComfyUIRepairError as exc:
            latest = self._read_records().get(active.tag) or active
            updates: dict[str, object] = {
                "repair_status": exc.category,
                "last_repair_error": str(exc),
            }
            if exc.category == "incompatible":
                updates.update(
                    {
                        "incompatible": True,
                        "incompatible_reason": str(exc),
                        "failed_validation": True,
                        "failed_reason": str(exc),
                        "locally_verified": False,
                    }
                )
            failed_record = latest.model_copy(update=updates)
            blocked = _repair_blocked_until(failed_record)
            if blocked is not None:
                failed_record = failed_record.model_copy(
                    update={"repair_blocked_until": blocked}
                )
            self._upsert_record(failed_record)
            fallback = await self._fallback_after_repair_failure(
                job_id, active, str(exc), repair_reason
            )
            if exc.category == "incompatible":
                self._job = self._job.model_copy(
                    update={"incompatible_version": active.tag}
                )
            return fallback
        except Exception as exc:
            latest = self._read_records().get(active.tag) or active
            failed_record = latest.model_copy(
                update={"repair_status": "repair_failed", "last_repair_error": str(exc)}
            )
            blocked = _repair_blocked_until(failed_record)
            if blocked is not None:
                failed_record = failed_record.model_copy(
                    update={"repair_blocked_until": blocked}
                )
            self._upsert_record(failed_record)
            return await self._fallback_after_repair_failure(
                job_id, active, str(exc), repair_reason
            )

    async def _run_update(self, selected_version: str, job_id: str) -> None:
        async with self._lock:
            transaction_dir = (
                self.paths.install_transactions_dir
                / f"install-comfyui-update-{uuid4().hex}"
            )
            try:
                self._set_job(
                    job_id, "resolving", selected_version, "Resolving ComfyUI release."
                )
                releases = await self.release_fetcher()
                release = _resolve_release(selected_version, releases)
                existing = self._read_records().get(release.tag_name)
                if (
                    _record_paths_ready(existing)
                    and existing
                    and existing.locally_verified
                ):
                    existing = _clear_repair_state(existing)
                    self._upsert_record(existing)
                    await self._activate(existing)
                    self._set_job(
                        job_id,
                        "completed",
                        selected_version,
                        "Activated locally verified ComfyUI version.",
                        status="completed",
                        resolved_tag=release.tag_name,
                        installed_path=existing.source_path,
                        activated_version=release.tag_name,
                    )
                    return

                transaction_dir.mkdir(parents=True, exist_ok=False)
                archive_path = transaction_dir / f"{_safe_tag(release.tag_name)}.zip"
                self._set_job(
                    job_id,
                    "downloading",
                    selected_version,
                    f"Downloading ComfyUI {release.tag_name}.",
                    resolved_tag=release.tag_name,
                )
                archive_url = (
                    release.zipball_url
                    or f"https://github.com/{UPSTREAM_REPO}/archive/refs/tags/{release.tag_name}.zip"
                )
                await self.archive_downloader(archive_url, archive_path)

                self._set_job(
                    job_id, "extracting", selected_version, "Extracting ComfyUI source."
                )
                extracted_source = transaction_dir / "source"
                _extract_github_zip(archive_path, extracted_source)
                source_manifest = build_comfyui_source_manifest(
                    extracted_source,
                    comfyui_core_version=release.tag_name,
                    source_origin_kind=RuntimeSourceOriginKind.UPSTREAM_SOURCE_ARCHIVE,
                    source_reference=archive_url,
                    source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
                )
                engine_id = f"comfyui-core-{_safe_tag(release.tag_name)}-{source_manifest.source_hash.removeprefix('sha256:')[:16]}"
                final_source = self.paths.core_engines_dir / engine_id
                final_env = self.paths.core_envs_dir / engine_id / "venv"
                active = self._active_record()
                if final_source.exists() and (
                    active is None or active.source_path != str(final_source)
                ):
                    shutil.rmtree(final_source)
                if final_env.exists() and (
                    active is None or active.env_path != str(final_env)
                ):
                    shutil.rmtree(final_env)
                final_source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(extracted_source, final_source)

                self._set_job(
                    job_id,
                    "installing_dependencies",
                    selected_version,
                    "Preparing a fresh ComfyUI environment.",
                )
                environment = RuntimeEnvironment(
                    repo_dir=final_source,
                    runtime_dir=self.paths.core_envs_dir / engine_id,
                    bootstrap_python_executable=self.bootstrap_python_executable,
                    torch_cuda_index_url=self.torch_cuda_index_url,
                    torch_cpu_index_url=self.torch_cpu_index_url,
                    log_store=self.log_store,
                    logs_dir=self.paths.logs_dir,
                    cache_dir=self.paths.cache_dir,
                    command_runner=self.command_runner,
                    venv_dir_override=final_env,
                )
                bootstrap = await environment.bootstrap()
                if bootstrap.status not in {"prepared", "already_prepared"}:
                    raise RuntimeError(
                        bootstrap.environment.error
                        if bootstrap.environment
                        else bootstrap.status
                    )

                record = LocalComfyUIVersionRecord(
                    tag=release.tag_name,
                    available_upstream=True,
                    installed=True,
                    locally_verified=False,
                    failed_validation=False,
                    source_hash=source_manifest.source_hash,
                    commit_sha=release.target_commitish,
                    source_path=str(final_source),
                    env_path=str(final_env),
                    archive_url=archive_url,
                    installed_at=_now_iso(),
                )
                self._upsert_record(record)

                self._set_job(
                    job_id,
                    "smoke_testing",
                    selected_version,
                    "Validating ComfyUI before activation.",
                )
                await self.smoke_tester(final_source, final_env, record)
                record = _clear_repair_state(
                    record.model_copy(
                        update={"locally_verified": True, "validated_at": _now_iso()}
                    )
                )
                self._upsert_record(record)

                self._set_job(
                    job_id,
                    "activating",
                    selected_version,
                    "Activating validated ComfyUI version.",
                )
                await self._activate(record)
                self._set_job(
                    job_id,
                    "completed",
                    selected_version,
                    f"ComfyUI {release.tag_name} is active.",
                    status="completed",
                    resolved_tag=release.tag_name,
                    installed_path=str(final_source),
                    activated_version=release.tag_name,
                )
                shutil.rmtree(transaction_dir, ignore_errors=True)
            except Exception as exc:
                if transaction_dir.exists():
                    _write_json(
                        transaction_dir / "quarantine.json",
                        {
                            "schema_version": UPDATE_METADATA_SCHEMA_VERSION,
                            "status": "quarantined",
                            "reason": str(exc),
                            "quarantined_at": _now_iso(),
                        },
                    )
                tag = self._job.resolved_tag
                if tag:
                    existing = self._read_records().get(
                        tag
                    ) or LocalComfyUIVersionRecord(tag=tag)
                    self._upsert_record(
                        existing.model_copy(
                            update={
                                "failed_validation": True,
                                "failed_reason": str(exc),
                                "locally_verified": False,
                            }
                        )
                    )
                self._set_job(
                    job_id,
                    "failed",
                    selected_version,
                    "ComfyUI update failed. The current engine was left unchanged.",
                    status="failed",
                    error=str(exc),
                )
                self.log_store.add(
                    "error",
                    "ComfyUI update failed",
                    "runtime.comfyui_update",
                    details={"error": str(exc), "selected_version": selected_version},
                )

    async def _run_rebuild(self, selected_version: str, job_id: str) -> None:
        async with self._lock:
            try:
                self._set_job(
                    job_id,
                    "resolving",
                    selected_version,
                    "Resolving installed ComfyUI runtime to rebuild.",
                    operation="rebuild",
                    repair_reason="manual_rebuild",
                )
                record = self._resolve_rebuild_record(selected_version)
                record = record.model_copy(
                    update={
                        "repair_status": "repair_started",
                        "repair_attempt_count": 0,
                        "last_repair_attempt_at": _now_iso(),
                        "last_repair_error": None,
                        "repair_blocked_until": None,
                    }
                )
                self._upsert_record(record)
                rebuilt = await self._repair_record(
                    record,
                    job_id=job_id,
                    repair_reason="manual_rebuild",
                    materialization_suffix=f"manual-{uuid4().hex[:8]}",
                )
                self._set_job(
                    job_id,
                    "activating",
                    record.tag,
                    "Activating rebuilt ComfyUI environment.",
                    operation="rebuild",
                    resolved_tag=record.tag,
                    repair_reason="manual_rebuild",
                )
                await self._activate(rebuilt)
                self._set_job(
                    job_id,
                    "completed",
                    record.tag,
                    f"ComfyUI {record.tag} environment was rebuilt and validated.",
                    operation="rebuild",
                    status="completed",
                    resolved_tag=record.tag,
                    installed_path=rebuilt.source_path,
                    activated_version=record.tag,
                    repair_reason="manual_rebuild",
                )
            except ComfyUIRepairError as exc:
                tag = self._job.resolved_tag or (
                    None
                    if selected_version in {"current", "latest"}
                    else selected_version
                )
                if tag:
                    existing = self._read_records().get(
                        tag
                    ) or LocalComfyUIVersionRecord(tag=tag)
                    updates: dict[str, object] = {
                        "repair_status": exc.category,
                        "last_repair_error": str(exc),
                    }
                    if exc.category == "incompatible":
                        updates.update(
                            {
                                "incompatible": True,
                                "incompatible_reason": str(exc),
                                "failed_validation": True,
                                "failed_reason": str(exc),
                                "locally_verified": False,
                            }
                        )
                    self._upsert_record(existing.model_copy(update=updates))
                self._set_job(
                    job_id,
                    "failed",
                    selected_version,
                    "ComfyUI environment rebuild failed. The existing engine was left unchanged.",
                    operation="rebuild",
                    status="failed",
                    error=str(exc),
                    incompatible_version=(
                        tag if exc.category == "incompatible" else None
                    ),
                )
            except Exception as exc:
                tag = self._job.resolved_tag or (
                    None
                    if selected_version in {"current", "latest"}
                    else selected_version
                )
                if tag:
                    existing = self._read_records().get(
                        tag
                    ) or LocalComfyUIVersionRecord(tag=tag)
                    self._upsert_record(
                        existing.model_copy(
                            update={
                                "repair_status": "repair_failed",
                                "last_repair_error": str(exc),
                            }
                        )
                    )
                self._set_job(
                    job_id,
                    "failed",
                    selected_version,
                    "ComfyUI environment rebuild failed. The existing engine was left unchanged.",
                    operation="rebuild",
                    status="failed",
                    error=str(exc),
                )

    def _resolve_rebuild_record(
        self, selected_version: str
    ) -> LocalComfyUIVersionRecord:
        active = self._active_record()
        tag = (
            active.tag
            if selected_version in {"current", "latest"} and active is not None
            else selected_version
        )
        if not tag:
            raise RuntimeError("No active ComfyUI version is available to rebuild.")
        record = self._read_records().get(tag)
        if record is None and active is not None and active.tag == tag:
            record = active
        if record is None or not record.installed:
            raise RuntimeError(
                f"ComfyUI version is not installed and cannot be rebuilt: {tag}"
            )
        if not record.source_path:
            raise RuntimeError(f"ComfyUI version has no recorded source path: {tag}")
        return record

    async def _repair_record(
        self,
        record: LocalComfyUIVersionRecord,
        *,
        job_id: str,
        repair_reason: str,
        materialization_suffix: str | None = None,
    ) -> LocalComfyUIVersionRecord:
        transaction_dir = (
            self.paths.install_transactions_dir
            / f"repair-comfyui-{_safe_tag(record.tag)}-{uuid4().hex}"
        )
        transaction_dir.mkdir(parents=True, exist_ok=False)
        try:
            source_dir, source_hash, source_reference = await self._repair_source(
                record, transaction_dir, job_id, repair_reason
            )
            engine_id = f"comfyui-core-{_safe_tag(record.tag)}-{source_hash.removeprefix('sha256:')[:16]}"
            env_id = (
                f"{engine_id}-{materialization_suffix}"
                if materialization_suffix
                else engine_id
            )
            final_source = self.paths.core_engines_dir / engine_id
            final_env = self.paths.core_envs_dir / env_id / "venv"
            staged_env = transaction_dir / "env" / "venv"

            self._set_job(
                job_id,
                "repairing_environment",
                record.tag,
                "Rebuilding a fresh ComfyUI environment.",
                operation="repair",
                resolved_tag=record.tag,
                repair_reason=repair_reason,
                repair_attempt_count=record.repair_attempt_count,
            )
            environment = RuntimeEnvironment(
                repo_dir=source_dir,
                runtime_dir=transaction_dir / "env",
                bootstrap_python_executable=self.bootstrap_python_executable,
                torch_cuda_index_url=self.torch_cuda_index_url,
                torch_cpu_index_url=self.torch_cpu_index_url,
                log_store=self.log_store,
                logs_dir=self.paths.logs_dir,
                cache_dir=self.paths.cache_dir,
                command_runner=self.command_runner,
                venv_dir_override=staged_env,
            )
            bootstrap = await environment.bootstrap()
            if bootstrap.status not in {"prepared", "already_prepared"}:
                raise ComfyUIRepairError(
                    (
                        bootstrap.environment.error
                        if bootstrap.environment and bootstrap.environment.error
                        else bootstrap.status
                    ),
                    category="repair_failed",
                )

            staged_record = record.model_copy(
                update={
                    "source_hash": source_hash,
                    "source_path": str(source_dir),
                    "env_path": str(staged_env),
                    "archive_url": source_reference,
                    "failed_validation": False,
                    "failed_reason": None,
                    "locally_verified": False,
                    "repair_status": "validating",
                }
            )
            self._set_job(
                job_id,
                "smoke_testing",
                record.tag,
                "Validating repaired ComfyUI before activation.",
                operation="repair",
                resolved_tag=record.tag,
                repair_reason=repair_reason,
                repair_attempt_count=record.repair_attempt_count,
            )
            try:
                await self.smoke_tester(source_dir, staged_env, staged_record)
            except Exception as exc:
                if _smoke_failure_is_compatibility(str(exc)):
                    raise ComfyUIRepairError(str(exc), category="incompatible") from exc
                raise ComfyUIRepairError(
                    str(exc), category="validation_failed"
                ) from exc

            if final_source.exists() and final_source != source_dir:
                shutil.rmtree(final_source)
            if not final_source.exists():
                final_source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_dir, final_source)

            if final_env.exists():
                shutil.rmtree(final_env)
            final_env.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staged_env), str(final_env))

            repaired = _clear_repair_state(
                staged_record.model_copy(
                    update={
                        "source_path": str(final_source),
                        "env_path": str(final_env),
                        "installed": True,
                        "locally_verified": True,
                        "validated_at": _now_iso(),
                        "installed_at": staged_record.installed_at or _now_iso(),
                    }
                )
            )
            self._upsert_record(repaired)
            shutil.rmtree(transaction_dir, ignore_errors=True)
            return repaired
        except Exception:
            if transaction_dir.exists():
                _write_json(
                    transaction_dir / "quarantine.json",
                    {
                        "schema_version": UPDATE_METADATA_SCHEMA_VERSION,
                        "status": "quarantined",
                        "reason": "repair_failed",
                        "quarantined_at": _now_iso(),
                    },
                )
            raise

    async def _repair_source(
        self,
        record: LocalComfyUIVersionRecord,
        transaction_dir: Path,
        job_id: str,
        repair_reason: str,
    ) -> tuple[Path, str, str | None]:
        local_source = Path(record.source_path) if record.source_path else None
        if local_source is not None and self._source_matches_record(
            local_source, record
        ):
            return (
                local_source,
                record.source_hash or self._source_hash(local_source, record),
                record.archive_url,
            )

        self._set_job(
            job_id,
            "redownloading_source",
            record.tag,
            "Recovering ComfyUI source from the recorded upstream artifact.",
            operation="repair",
            resolved_tag=record.tag,
            repair_reason=repair_reason,
            repair_attempt_count=record.repair_attempt_count,
        )
        last_error: Exception | None = None
        for archive_url in _archive_recovery_candidates(record):
            archive_path = (
                transaction_dir / f"{_safe_tag(record.tag)}-{uuid4().hex}.zip"
            )
            extracted_source = transaction_dir / f"source-{uuid4().hex}"
            try:
                await self.archive_downloader(archive_url, archive_path)
                _extract_github_zip(archive_path, extracted_source)
                manifest = build_comfyui_source_manifest(
                    extracted_source,
                    comfyui_core_version=record.tag,
                    source_origin_kind=RuntimeSourceOriginKind.UPSTREAM_SOURCE_ARCHIVE,
                    source_reference=archive_url,
                    source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
                )
                if record.source_hash and manifest.source_hash != record.source_hash:
                    last_error = ComfyUIRepairError(
                        f"Recovered source hash mismatch for {record.tag}: expected {record.source_hash}, got {manifest.source_hash}",
                        category="repair_failed",
                    )
                    shutil.rmtree(extracted_source, ignore_errors=True)
                    continue
                return extracted_source, manifest.source_hash, archive_url
            except Exception as exc:
                last_error = exc
                shutil.rmtree(extracted_source, ignore_errors=True)
        raise ComfyUIRepairError(
            str(last_error or "Could not recover ComfyUI source."),
            category="repair_failed",
        )

    def _source_matches_record(
        self, source_dir: Path, record: LocalComfyUIVersionRecord
    ) -> bool:
        if not source_dir.exists() or not (source_dir / "main.py").exists():
            return False
        if not record.source_hash:
            return True
        try:
            return self._source_hash(source_dir, record) == record.source_hash
        except Exception:
            return False

    def _source_hash(self, source_dir: Path, record: LocalComfyUIVersionRecord) -> str:
        manifest = build_comfyui_source_manifest(
            source_dir,
            comfyui_core_version=record.tag,
            source_origin_kind=RuntimeSourceOriginKind.UPSTREAM_SOURCE_ARCHIVE,
            source_reference=record.archive_url or record.tag,
            source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
        )
        return manifest.source_hash

    async def _fallback_after_repair_failure(
        self,
        job_id: str,
        failed_record: LocalComfyUIVersionRecord,
        error: str,
        repair_reason: str,
    ) -> ProcessActionResult:
        previous = self._previous_active_record()
        if (
            previous is not None
            and previous.tag != failed_record.tag
            and _record_paths_ready(previous)
            and previous.locally_verified
        ):
            try:
                await self._activate(previous)
                started = await self.runtime_manager.start()
                if started.status in {"started", "already_running"}:
                    self._set_job(
                        job_id,
                        "failed",
                        failed_record.tag,
                        "Repair failed. The previous locally verified ComfyUI runtime was started.",
                        operation="repair",
                        status="failed",
                        error=error,
                        resolved_tag=failed_record.tag,
                        repair_reason=repair_reason,
                        repair_attempt_count=failed_record.repair_attempt_count,
                        fallback_version=previous.tag,
                    )
                    return ProcessActionResult(
                        status="repair_failed_fallback_active", comfyui=started.comfyui
                    )
            except Exception as fallback_exc:
                self.log_store.add(
                    "error",
                    "Previous ComfyUI fallback failed after repair failure",
                    "runtime.comfyui_repair",
                    details={
                        "failed_tag": failed_record.tag,
                        "fallback_tag": previous.tag,
                        "error": str(fallback_exc),
                    },
                )

        if (
            self.bundled_repo_dir is not None
            and self.bundled_python_executable is not None
        ):
            try:
                environment = RuntimeEnvironment(
                    repo_dir=self.bundled_repo_dir,
                    runtime_dir=self.paths.core_envs_dir / "bundled-comfyui",
                    bootstrap_python_executable=self.bootstrap_python_executable,
                    python_executable_override=self.bundled_python_executable,
                    torch_cuda_index_url=self.torch_cuda_index_url,
                    torch_cpu_index_url=self.torch_cpu_index_url,
                    log_store=self.log_store,
                    logs_dir=self.paths.logs_dir,
                    cache_dir=self.paths.cache_dir,
                    command_runner=self.command_runner,
                )
                self.runtime_manager.reconfigure_managed_runtime(
                    repo_dir=self.bundled_repo_dir,
                    python_executable=self.bundled_python_executable,
                    environment=environment,
                    version_metadata=ComfyUIVersionMetadata(source_kind="bundled"),
                )
                started = await self.runtime_manager.start()
                if started.status in {"started", "already_running"}:
                    self._set_job(
                        job_id,
                        "failed",
                        failed_record.tag,
                        "Repair failed. The bundled ComfyUI runtime was started.",
                        operation="repair",
                        status="failed",
                        error=error,
                        resolved_tag=failed_record.tag,
                        repair_reason=repair_reason,
                        repair_attempt_count=failed_record.repair_attempt_count,
                        fallback_version="bundled",
                    )
                    return ProcessActionResult(
                        status="repair_failed_fallback_active", comfyui=started.comfyui
                    )
            except Exception as fallback_exc:
                self.log_store.add(
                    "error",
                    "Bundled ComfyUI fallback failed after repair failure",
                    "runtime.comfyui_repair",
                    details={
                        "failed_tag": failed_record.tag,
                        "error": str(fallback_exc),
                    },
                )

        self._set_job(
            job_id,
            "failed",
            failed_record.tag,
            "Repair failed and no fallback runtime could be started.",
            operation="repair",
            status="failed",
            error=error,
            resolved_tag=failed_record.tag,
            repair_reason=repair_reason,
            repair_attempt_count=failed_record.repair_attempt_count,
        )
        return ProcessActionResult(
            status="repair_failed_no_fallback",
            comfyui=await self.runtime_manager.status(),
        )

    async def _activate(self, record: LocalComfyUIVersionRecord) -> None:
        if not record.source_path or not record.env_path:
            raise RuntimeError(
                "Cannot activate ComfyUI version without source/env paths."
            )
        if not record.locally_verified:
            raise RuntimeError(
                "Cannot activate a ComfyUI version before local validation passes."
            )
        source_path = Path(record.source_path)
        env_path = Path(record.env_path)
        python = _venv_python(env_path)
        if not (source_path / "main.py").exists():
            raise RuntimeError(f"ComfyUI source is missing main.py: {source_path}")
        if not Path(python).exists():
            raise RuntimeError(f"ComfyUI environment Python is missing: {python}")

        if self.runtime_manager.is_managed_process_running():
            stopped = await self.runtime_manager.stop()
            if stopped.status not in {"stopped", "not_running"}:
                raise RuntimeError(
                    f"Could not stop active ComfyUI before activation: {stopped.status}"
                )

        activated = record.model_copy(
            update={"active": True, "activated_at": _now_iso()}
        )
        self._write_active_record(activated)
        self._mark_active(activated.tag)
        environment = RuntimeEnvironment(
            repo_dir=source_path,
            runtime_dir=env_path.parent,
            bootstrap_python_executable=self.bootstrap_python_executable,
            python_executable_override=python,
            torch_cuda_index_url=self.torch_cuda_index_url,
            torch_cpu_index_url=self.torch_cpu_index_url,
            log_store=self.log_store,
            logs_dir=self.paths.logs_dir,
            cache_dir=self.paths.cache_dir,
            command_runner=self.command_runner,
            venv_dir_override=env_path,
        )
        self.runtime_manager.reconfigure_managed_runtime(
            repo_dir=source_path,
            python_executable=python,
            environment=environment,
            version_metadata=ComfyUIVersionMetadata(
                active_tag=activated.tag,
                source_hash=activated.source_hash,
                source_kind="installed",
                local_validation_status="locally_verified",
            ),
        )

    async def _fetch_upstream_releases(self) -> list[UpstreamComfyUIRelease]:
        payload: list[object] = []
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            for page in range(1, 6):
                response = await client.get(
                    UPSTREAM_RELEASES_API,
                    headers={"Accept": "application/vnd.github+json"},
                    params={"per_page": 100, "page": page},
                )
                response.raise_for_status()
                page_payload = response.json()
                if not isinstance(page_payload, list):
                    raise RuntimeError("GitHub releases response was not a list.")
                payload.extend(page_payload)
                if len(page_payload) < 100:
                    break
        releases = [UpstreamComfyUIRelease.model_validate(item) for item in payload]
        return _stable_sorted_releases(releases)

    async def _download_archive(self, url: str, dest: Path) -> int:
        bytes_written = 0
        async with httpx.AsyncClient(timeout=None, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with dest.open("wb") as file:
                    async for chunk in response.aiter_bytes():
                        file.write(chunk)
                        bytes_written += len(chunk)
        return bytes_written

    async def _default_smoke_test(
        self, source_dir: Path, env_dir: Path, record: LocalComfyUIVersionRecord
    ) -> None:
        smoke_root = (
            self.paths.temp_dir
            / "comfyui-update-smoke"
            / f"{_safe_tag(record.tag)}-{uuid4().hex}"
        )
        smoke_root.mkdir(parents=True, exist_ok=False)
        manager = RuntimeManager(
            mode="managed",
            external_base_url="http://127.0.0.1:8188",
            repo_dir=source_dir,
            python_executable=_venv_python(env_dir),
            startup_timeout_seconds=90,
            health_poll_interval_seconds=0.5,
            log_store=self.log_store,
            managed_base_directory=smoke_root,
            managed_output_directory=smoke_root / "outputs",
            managed_input_directory=smoke_root / "input",
            managed_temp_directory=smoke_root,
            managed_user_directory=smoke_root / "user",
            managed_database_url=f"sqlite:///{(smoke_root / 'user' / 'comfyui.db').as_posix()}",
            python_cache_dir=self.paths.python_cache_dir,
        )
        try:
            started = await manager.start()
            if started.status not in {"started", "already_running"}:
                raise RuntimeError(f"Smoke ComfyUI did not start: {started.status}")
            await _smoke_required_routes(manager.base_url)
            await _smoke_prompt_and_websocket(manager.base_url, manager.ws_url)
            _assert_no_runtime_dirs_in_source(source_dir)
        finally:
            await manager.stop()
            shutil.rmtree(smoke_root, ignore_errors=True)

    def _updates_allowed(self) -> tuple[bool, str | None]:
        if self.mode != "managed":
            return False, "ComfyUI updates are available only in managed mode."
        if self.developer_override:
            return (
                False,
                "ComfyUI updates are disabled while developer path or Python overrides are active.",
            )
        return True, None

    def _active_record(self) -> LocalComfyUIVersionRecord | None:
        return _read_active_record(self.paths)

    def _previous_active_record(self) -> LocalComfyUIVersionRecord | None:
        return _read_previous_active_record(self.paths)

    def _read_records(self) -> dict[str, LocalComfyUIVersionRecord]:
        path = self.paths.core_engines_dir / LOCAL_VALIDATION_FILENAME
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        records = payload.get("records", {})
        if not isinstance(records, dict):
            return {}
        return {
            tag: LocalComfyUIVersionRecord.model_validate(record)
            for tag, record in records.items()
        }

    def _write_records(self, records: dict[str, LocalComfyUIVersionRecord]) -> None:
        _write_json(
            self.paths.core_engines_dir / LOCAL_VALIDATION_FILENAME,
            {
                "schema_version": UPDATE_METADATA_SCHEMA_VERSION,
                "updated_at": _now_iso(),
                "records": {
                    tag: record.model_dump(mode="json")
                    for tag, record in sorted(records.items())
                },
            },
        )

    def _upsert_record(self, record: LocalComfyUIVersionRecord) -> None:
        records = self._read_records()
        records[record.tag] = record
        self._write_records(records)

    def _write_active_record(self, record: LocalComfyUIVersionRecord) -> None:
        active_payload = _read_active_payload(self.paths) or {}
        previous = self._active_record()
        existing_previous = active_payload.get("previous_active")
        previous_payload = (
            existing_previous if isinstance(existing_previous, dict) else None
        )
        if previous is not None and previous.tag != record.tag:
            previous_payload = previous.model_dump(mode="json")
        _write_json(
            self.paths.core_engines_dir / ACTIVE_COMFYUI_FILENAME,
            {
                "schema_version": UPDATE_METADATA_SCHEMA_VERSION,
                "active": record.model_dump(mode="json"),
                "previous_active": previous_payload,
            },
        )

    def _mark_active(self, active_tag: str) -> None:
        records = self._read_records()
        for tag, record in list(records.items()):
            records[tag] = record.model_copy(update={"active": tag == active_tag})
        self._write_records(records)

    def _set_job(
        self,
        job_id: str,
        phase: str,
        selected_version: str | None,
        progress_label: str,
        *,
        operation: str | None = None,
        status: str = "running",
        resolved_tag: str | None = None,
        error: str | None = None,
        installed_path: str | None = None,
        activated_version: str | None = None,
        repair_reason: str | None = None,
        repair_attempt_count: int | None = None,
        repair_blocked_until: str | None = None,
        fallback_version: str | None = None,
        incompatible_version: str | None = None,
    ) -> None:
        self._job = ComfyUIUpdateJobStatus(
            job_id=job_id,
            operation=operation or self._job.operation,
            phase=phase,
            selected_version=selected_version,
            resolved_tag=resolved_tag or self._job.resolved_tag,
            progress_label=progress_label,
            status=status,
            error=error,
            installed_path=installed_path,
            activated_version=activated_version,
            repair_reason=repair_reason or self._job.repair_reason,
            repair_attempt_count=(
                repair_attempt_count
                if repair_attempt_count is not None
                else self._job.repair_attempt_count
            ),
            repair_blocked_until=repair_blocked_until or self._job.repair_blocked_until,
            fallback_version=fallback_version or self._job.fallback_version,
            incompatible_version=incompatible_version or self._job.incompatible_version,
        )


def _read_active_record(paths: NoofyPaths) -> LocalComfyUIVersionRecord | None:
    payload = _read_active_payload(paths)
    if payload is None:
        return None
    active = payload.get("active")
    if not isinstance(active, dict):
        return None
    return LocalComfyUIVersionRecord.model_validate(active)


def _read_previous_active_record(paths: NoofyPaths) -> LocalComfyUIVersionRecord | None:
    payload = _read_active_payload(paths)
    if payload is None:
        return None
    previous = payload.get("previous_active")
    if not isinstance(previous, dict):
        return None
    return LocalComfyUIVersionRecord.model_validate(previous)


def _read_active_payload(paths: NoofyPaths) -> dict[str, object] | None:
    path = paths.core_engines_dir / ACTIVE_COMFYUI_FILENAME
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        return None
    return payload


def _stable_sorted_releases(
    releases: list[UpstreamComfyUIRelease],
) -> list[UpstreamComfyUIRelease]:
    stable = [
        release for release in releases if not release.draft and not release.prerelease
    ]
    return sorted(
        stable, key=lambda release: _version_sort_key(release.tag_name), reverse=True
    )


def _resolve_release(
    selected_version: str, releases: list[UpstreamComfyUIRelease]
) -> UpstreamComfyUIRelease:
    stable = _stable_sorted_releases(releases)
    if not stable:
        raise RuntimeError("No stable ComfyUI releases were found upstream.")
    if selected_version == "latest":
        return stable[0]
    for release in stable:
        if release.tag_name == selected_version:
            return release
    raise RuntimeError(f"ComfyUI release is not available: {selected_version}")


def _clear_repair_state(record: LocalComfyUIVersionRecord) -> LocalComfyUIVersionRecord:
    return record.model_copy(
        update={
            "repair_status": None,
            "repair_attempt_count": 0,
            "last_repair_attempt_at": None,
            "last_repair_error": None,
            "repair_blocked_until": None,
            "incompatible": False,
            "incompatible_reason": None,
            "failed_validation": False,
            "failed_reason": None,
        }
    )


def _merge_active_with_record(
    active: LocalComfyUIVersionRecord,
    record: LocalComfyUIVersionRecord,
) -> LocalComfyUIVersionRecord:
    return active.model_copy(
        update={
            "failed_validation": record.failed_validation,
            "failed_reason": record.failed_reason,
            "repair_status": record.repair_status,
            "repair_attempt_count": record.repair_attempt_count,
            "last_repair_attempt_at": record.last_repair_attempt_at,
            "last_repair_error": record.last_repair_error,
            "repair_blocked_until": record.repair_blocked_until,
            "incompatible": record.incompatible,
            "incompatible_reason": record.incompatible_reason,
            "last_successfully_started_at": record.last_successfully_started_at,
        }
    )


def _start_failure_is_repairable(status: str, error: str | None = None) -> bool:
    if status in {"environment_not_ready", "repo_missing"}:
        return True
    if status != "startup_failed":
        return False
    text = (error or "").lower()
    environment_markers = (
        "no module named",
        "modulenotfounderror",
        "importerror",
        "dll load failed",
        "symbol not found",
        "library not loaded",
        "requirements",
        "dependency",
        "venv",
        "virtual environment",
        "python executable",
        "site-packages",
        "torch",
        "aiohttp",
        "numpy",
        "runtime python",
    )
    return any(marker in text for marker in environment_markers)


def _repair_blocked_until(record: LocalComfyUIVersionRecord) -> str | None:
    last_attempt = _parse_iso(record.last_repair_attempt_at)
    blocked = _parse_iso(record.repair_blocked_until)
    now = datetime.now(UTC)
    if blocked is not None and blocked > now:
        return blocked.isoformat()
    if last_attempt is None or now - last_attempt > AUTOMATIC_REPAIR_WINDOW:
        return None
    if record.repair_attempt_count < AUTOMATIC_REPAIR_MAX_ATTEMPTS:
        return None
    return (last_attempt + AUTOMATIC_REPAIR_WINDOW).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _archive_recovery_candidates(record: LocalComfyUIVersionRecord) -> list[str]:
    candidates: list[str] = []
    if record.commit_sha and re.fullmatch(r"[0-9a-fA-F]{7,40}", record.commit_sha):
        candidates.append(
            f"https://github.com/{UPSTREAM_REPO}/archive/{record.commit_sha}.zip"
        )
    if record.archive_url:
        candidates.append(record.archive_url)
    candidates.append(
        f"https://github.com/{UPSTREAM_REPO}/archive/refs/tags/{record.tag}.zip"
    )
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _smoke_failure_is_compatibility(error: str) -> bool:
    lowered = error.lower()
    compatibility_markers = (
        "smoke route failed",
        "smoke prompt failed",
        "websocket did not report",
        "source contains runtime directories",
        "/prompt",
        "/ws",
        "/queue",
        "/history",
        "/view",
        "/models",
        "/object_info",
        "/system_stats",
    )
    transient_markers = (
        "connection refused",
        "timed out",
        "timeout",
        "network",
        "no route to host",
    )
    return any(marker in lowered for marker in compatibility_markers) and not any(
        marker in lowered for marker in transient_markers
    )


def _version_option(
    release: UpstreamComfyUIRelease | None,
    record: LocalComfyUIVersionRecord | None,
    *,
    active_tag: str | None,
) -> ComfyUIVersionOption:
    tag = release.tag_name if release else (record.tag if record else "")
    active = tag == active_tag or bool(record and record.active)
    installed = bool(record and record.installed)
    locally_verified = bool(record and record.locally_verified)
    failed = bool(record and record.failed_validation)
    incompatible = bool(record and record.incompatible)
    if active:
        status = "Current"
    elif incompatible:
        status = "Incompatible"
    elif record and record.repair_status == "repair_blocked":
        status = "Repair blocked"
    elif locally_verified:
        status = "Locally verified"
    elif failed:
        status = "Failed validation"
    elif installed:
        status = "Installed"
    else:
        status = "Available upstream"
    return ComfyUIVersionOption(
        tag=tag,
        label=f"{tag} ({status})",
        status=status,
        available_upstream=release is not None
        or bool(record and record.available_upstream),
        installed=installed,
        active=active,
        locally_verified=locally_verified,
        failed_validation=failed,
        failed_reason=record.failed_reason if record else None,
        source_hash=record.source_hash if record else None,
        commit_sha=(record.commit_sha if record else None)
        or (release.target_commitish if release else None),
        published_at=release.published_at if release else None,
        repair_status=record.repair_status if record else None,
        repair_attempt_count=record.repair_attempt_count if record else 0,
        last_repair_attempt_at=record.last_repair_attempt_at if record else None,
        last_repair_error=record.last_repair_error if record else None,
        repair_blocked_until=record.repair_blocked_until if record else None,
        incompatible=incompatible,
        incompatible_reason=record.incompatible_reason if record else None,
    )


def _version_sort_key(tag: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r"\d+", tag)]
    return tuple(numbers or [0])


def _safe_tag(tag: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", tag).strip("-") or "unknown"


def _venv_python(env_path: Path) -> str:
    if os.name == "nt":
        return str(env_path / "Scripts" / "python.exe")
    return str(env_path / "bin" / "python")


def _record_paths_ready(record: LocalComfyUIVersionRecord | None) -> bool:
    if record is None or not record.source_path or not record.env_path:
        return False
    return (
        Path(record.source_path).exists()
        and Path(_venv_python(Path(record.env_path))).exists()
    )


def _extract_github_zip(archive_path: Path, dest: Path) -> None:
    raw_dest = dest.parent / "_raw-source"
    shutil.rmtree(raw_dest, ignore_errors=True)
    raw_dest.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            _validate_zip_member(member)
            archive.extract(member, raw_dest)
    roots = [path for path in raw_dest.iterdir() if path.is_dir()]
    source_root = (
        roots[0] if len(roots) == 1 and (roots[0] / "main.py").exists() else raw_dest
    )
    if not (source_root / "main.py").exists():
        raise RuntimeError("Downloaded ComfyUI archive did not contain main.py.")
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(source_root, dest)
    shutil.rmtree(raw_dest, ignore_errors=True)


def _validate_zip_member(member: zipfile.ZipInfo) -> None:
    path = PurePosixPath(member.filename)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RuntimeError(f"Unsafe ComfyUI archive path: {member.filename}")
    mode = member.external_attr >> 16
    if mode & 0o170000 == 0o120000:
        raise RuntimeError(f"ComfyUI archive contains a symlink: {member.filename}")


async def _smoke_required_routes(base_url: str) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        for path in ("/system_stats", "/object_info", "/models", "/queue", "/history"):
            response = await client.get(f"{base_url}{path}")
            if not _required_route_status_usable(path, response.status_code):
                raise RuntimeError(
                    f"ComfyUI smoke route failed: {path} -> {response.status_code}"
                )
        view_response = await client.get(
            f"{base_url}/view",
            params={"filename": "__noofy_missing__.png", "type": "output"},
        )
        if not _required_route_status_usable("/view", view_response.status_code):
            raise RuntimeError(
                f"ComfyUI smoke route failed: /view -> {view_response.status_code}"
            )


def _required_route_status_usable(path: str, status_code: int) -> bool:
    if path == "/view":
        return status_code < 500 and status_code != 405
    return 200 <= status_code < 300


async def _smoke_prompt_and_websocket(base_url: str, ws_url: str) -> None:
    client_id = f"noofy-smoke-{uuid4().hex}"
    prompt = {
        "1": {
            "class_type": "EmptyImage",
            "inputs": {"width": 16, "height": 16, "batch_size": 1, "color": 0},
        },
        "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
    }
    async with websockets.connect(f"{ws_url}?clientId={client_id}") as websocket:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{base_url}/prompt", json={"prompt": prompt, "client_id": client_id}
            )
            if response.status_code >= 400:
                raise RuntimeError(
                    f"ComfyUI smoke prompt failed: {response.status_code} {response.text[:200]}"
                )
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            message = await asyncio.wait_for(websocket.recv(), timeout=5)
            if isinstance(message, bytes):
                continue
            payload = json.loads(message)
            if payload.get("type") == "executing":
                data = payload.get("data")
                if isinstance(data, dict) and data.get("node") is None:
                    return
        raise RuntimeError(
            "ComfyUI smoke WebSocket did not report workflow completion."
        )


def _assert_no_runtime_dirs_in_source(source_dir: Path) -> None:
    forbidden = {
        "models",
        "input",
        "output",
        "temp",
        "custom_nodes",
        "user",
        "__pycache__",
    }
    present = sorted(
        path.name for path in source_dir.iterdir() if path.name in forbidden
    )
    if present:
        raise RuntimeError(
            f"ComfyUI source contains runtime directories after smoke test: {', '.join(present)}"
        )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
