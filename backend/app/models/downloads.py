from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.diagnostics import DiagnosticsSink
from app.engine.models import ImportModelDownloadProgressItem
from app.models.schemas import (
    ModelDownloadActiveResponse,
    ModelDownloadJobStart,
    ModelDownloadJobStatus,
    ModelDownloadSelection,
    ModelDownloadStartRequest,
)
from app.models.ownership import ModelOwnershipStore
from app.models.paths import ensure_inside, model_key
from app.models.folders import ModelFolderSettingsService
from app.workflows.model_availability import ModelAvailabilityService
from app.workflows.package import RequiredModel, WorkflowPackage

JOB_TTL = timedelta(hours=6)


@dataclass
class _ModelDownloadJob:
    job_id: str
    selections: list[ModelDownloadSelection]
    direct_workflow_id: str | None
    direct_models: list[RequiredModel]
    cancel_event: asyncio.Event
    task: asyncio.Task | None
    status: str
    user_facing_message: str
    running_message: str
    failed_message: str
    completed_message: str
    started_at: datetime
    updated_at: datetime
    total_models: int
    models: dict[str, ImportModelDownloadProgressItem] = field(default_factory=dict)
    current_model_filename: str | None = None
    current_model_index: int | None = None
    speed_bytes_per_second: float | None = None


class ModelDownloadJobService:
    def __init__(
        self,
        *,
        engine_service: object,
        model_folder_service: ModelFolderSettingsService,
        ownership_store: ModelOwnershipStore,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.engine_service = engine_service
        self.model_folder_service = model_folder_service
        self.ownership_store = ownership_store
        self.log_store = log_store
        self._jobs: dict[str, _ModelDownloadJob] = {}

    def start(self, request: ModelDownloadStartRequest) -> ModelDownloadJobStart:
        self._sweep_old_jobs()
        selections = _dedupe_selections(request.selections)
        if not selections:
            raise ValueError("At least one model selection is required.")
        selected = self._selected_requirements(selections)
        total_models = len(selected)
        if total_models == 0:
            raise ValueError("No matching required models were found.")
        job_id = f"model-download-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        job = _ModelDownloadJob(
            job_id=job_id,
            selections=selections,
            direct_workflow_id=None,
            direct_models=[],
            cancel_event=asyncio.Event(),
            task=None,
            status="queued",
            user_facing_message="Model download is queued.",
            running_message="Downloading required models...",
            failed_message="Some downloads failed.",
            completed_message="Model download check finished.",
            started_at=now,
            updated_at=now,
            total_models=total_models,
            models={
                _progress_key(workflow_id, model): ImportModelDownloadProgressItem(
                    requirement_id=model_key(workflow_id, _requirement_id(model)),
                    filename=model.filename,
                    status="queued",
                    status_label="Queued",
                    total_bytes=model.size_bytes,
                )
                for workflow_id, model in selected
            },
        )
        self._jobs[job_id] = job
        job.task = asyncio.create_task(self._run(job_id))
        return ModelDownloadJobStart(
            job_id=job_id,
            status=job.status,
            user_facing_message=job.user_facing_message,
        )

    def start_direct(
        self,
        *,
        workflow_id: str,
        models: list[RequiredModel],
        queued_message: str = "Model download is queued.",
    ) -> ModelDownloadJobStart:
        self._sweep_old_jobs()
        if not models:
            raise ValueError("At least one model is required.")
        job_id = f"model-download-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        job = _ModelDownloadJob(
            job_id=job_id,
            selections=[],
            direct_workflow_id=workflow_id,
            direct_models=models,
            cancel_event=asyncio.Event(),
            task=None,
            status="queued",
            user_facing_message=queued_message,
            running_message="Downloading model...",
            failed_message="The model could not be downloaded.",
            completed_message="Model download finished.",
            started_at=now,
            updated_at=now,
            total_models=len(models),
            models={
                _progress_key(workflow_id, model): ImportModelDownloadProgressItem(
                    requirement_id=model_key(workflow_id, _requirement_id(model)),
                    filename=model.filename,
                    status="queued",
                    status_label="Queued",
                    total_bytes=model.size_bytes,
                )
                for model in models
            },
        )
        self._jobs[job_id] = job
        job.task = asyncio.create_task(self._run(job_id))
        return ModelDownloadJobStart(
            job_id=job_id,
            status=job.status,
            user_facing_message=job.user_facing_message,
        )

    def active(self) -> ModelDownloadActiveResponse:
        self._sweep_old_jobs()
        active_jobs = [job for job in self._jobs.values() if job.status in {"queued", "running"}]
        if not active_jobs:
            return ModelDownloadActiveResponse(job=None)
        latest = max(active_jobs, key=lambda job: job.updated_at)
        return ModelDownloadActiveResponse(job=self._status(latest))

    def status(self, job_id: str) -> ModelDownloadJobStatus:
        self._sweep_old_jobs()
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Unknown model download job: {job_id}")
        return self._status(job)

    def cancel(self, job_id: str) -> ModelDownloadJobStatus:
        job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(f"Unknown model download job: {job_id}")
        if job.status in {"queued", "running"}:
            job.cancel_event.set()
            job.status = "canceled"
            job.user_facing_message = "Canceling model download..."
            job.updated_at = datetime.now(UTC)
        return self._status(job)

    async def _run(self, job_id: str) -> None:
        job = self._jobs[job_id]
        job.status = "running"
        job.user_facing_message = job.running_message
        job.updated_at = datetime.now(UTC)
        grouped = self._selected_packages(job.selections) if not job.direct_models else {}
        failed = False
        completed = 0
        successful_downloads = 0
        last_progress_at = job.updated_at
        last_progress_by_model: dict[str, int] = {}

        def progress_callback(workflow_id: str):
            def _callback(event: dict[str, object]) -> None:
                nonlocal last_progress_at
                now = datetime.now(UTC)
                requirement_id = str(event["requirement_id"])
                filename = str(event["filename"])
                status = str(event["status"])
                bytes_downloaded = event.get("bytes_downloaded")
                total_bytes = event.get("total_bytes")
                message = event.get("message")
                model_index = event.get("model_index")
                progress_key = _progress_key_value(workflow_id, requirement_id)
                if isinstance(model_index, int):
                    job.current_model_index = completed + model_index
                job.current_model_filename = filename
                if isinstance(bytes_downloaded, int):
                    elapsed = max((now - last_progress_at).total_seconds(), 0.001)
                    delta = max(bytes_downloaded - last_progress_by_model.get(progress_key, 0), 0)
                    job.speed_bytes_per_second = delta / elapsed
                    last_progress_at = now
                    last_progress_by_model[progress_key] = bytes_downloaded
                job.models[progress_key] = ImportModelDownloadProgressItem(
                    requirement_id=model_key(workflow_id, requirement_id),
                    filename=filename,
                    status=status,
                    status_label=_download_progress_status_label(status),
                    bytes_downloaded=bytes_downloaded if isinstance(bytes_downloaded, int) else None,
                    total_bytes=total_bytes if isinstance(total_bytes, int) else None,
                    message=str(message) if message else None,
                )
                job.updated_at = now

            return _callback

        try:
            availability_service = self._availability_service()
            if job.direct_models:
                workflow_id = job.direct_workflow_id or "direct-model-download"
                direct_package = WorkflowPackage(
                    metadata={
                        "id": workflow_id,
                        "name": "Direct model download",
                        "version": "0.1.0",
                    },
                    engine="comfyui",
                    required_models=job.direct_models,
                    comfyui_graph={},
                )
                result = await availability_service.download_missing(
                    direct_package,
                    progress_callback=progress_callback(workflow_id),
                    cancel_event=job.cancel_event,
                )
                completed += len(job.direct_models)
                successful_downloads += getattr(
                    result,
                    "downloaded_count",
                    len(job.direct_models) if result.failed_count == 0 else 0,
                )
                failed = failed or result.failed_count > 0
                self._mark_downloaded_models(job.direct_models)
                if result.status == "canceled":
                    job.cancel_event.set()
            for package, models in grouped.values():
                if job.cancel_event.is_set():
                    break
                selected_package = package.model_copy(update={"required_models": models})
                result = await availability_service.download_missing(
                    selected_package,
                    progress_callback=progress_callback(package.metadata.id),
                    cancel_event=job.cancel_event,
                )
                completed += len(selected_package.required_models)
                successful_downloads += getattr(
                    result,
                    "downloaded_count",
                    len(selected_package.required_models) if result.failed_count == 0 else 0,
                )
                failed = failed or result.failed_count > 0
                self._mark_downloaded_models(models)
                if result.status == "canceled":
                    break
            if job.cancel_event.is_set():
                job.status = "canceled"
                job.user_facing_message = "Model download was canceled."
            elif failed:
                if successful_downloads > 0:
                    job.status = "completed_with_errors"
                    job.user_facing_message = "Some downloads failed."
                else:
                    job.status = "failed"
                    job.user_facing_message = job.failed_message
            else:
                job.status = "completed"
                job.user_facing_message = job.completed_message
        except Exception as exc:
            job.status = "failed"
            job.user_facing_message = "The model download failed. The partial download was cleaned up safely."
            if self.log_store is not None:
                self.log_store.add(
                    "warning",
                    "Standalone model download job failed",
                    "models.downloads",
                    details={"job_id": job.job_id, "error": _sanitize_error(exc), "error_type": type(exc).__name__},
                )
        finally:
            job.updated_at = datetime.now(UTC)

    def _selected_requirements(self, selections: list[ModelDownloadSelection]) -> list[tuple[str, RequiredModel]]:
        selected: list[tuple[str, RequiredModel]] = []
        grouped = self._selected_packages(selections)
        for package, models in grouped.values():
            selected.extend((package.metadata.id, model) for model in models)
        return selected

    def _selected_packages(self, selections: list[ModelDownloadSelection]) -> dict[str, tuple[WorkflowPackage, list[RequiredModel]]]:
        workflow_loader = getattr(self.engine_service, "workflow_loader", None)
        if workflow_loader is None:
            raise ValueError("Workflow loader is unavailable.")
        grouped: dict[str, tuple[WorkflowPackage, list[RequiredModel]]] = {}
        for selection in selections:
            package = workflow_loader.get_package(selection.workflow_id)
            matches = [
                model
                for model in package.required_models
                if _requirement_id(model) == selection.requirement_id
            ]
            if not matches:
                continue
            if package.metadata.id not in grouped:
                grouped[package.metadata.id] = (package, [])
            grouped_models = grouped[package.metadata.id][1]
            for model in matches:
                if _requirement_id(model) not in {_requirement_id(item) for item in grouped_models}:
                    grouped_models.append(model)
        return grouped

    def _availability_service(self) -> ModelAvailabilityService:
        availability_service = getattr(self.engine_service, "model_availability_service", None)
        if availability_service is None:
            raise ValueError("Model availability service is unavailable.")
        return availability_service

    def _mark_downloaded_models(self, models: list[RequiredModel]) -> None:
        folder_settings = self.model_folder_service.settings(ensure_folders=True)
        noofy_root = Path(folder_settings.noofy_models_dir).expanduser()
        for model in models:
            target = noofy_root / model.folder / model.filename
            try:
                ensure_inside(target, noofy_root)
            except ValueError:
                continue
            if target.is_file():
                self.ownership_store.mark_downloaded(model_key(model.folder, model.filename))

    def _status(self, job: _ModelDownloadJob) -> ModelDownloadJobStatus:
        items = list(job.models.values())
        known_downloaded = [item.bytes_downloaded for item in items if isinstance(item.bytes_downloaded, int)]
        known_totals = [item.total_bytes for item in items if isinstance(item.total_bytes, int)]
        bytes_downloaded = sum(known_downloaded) if known_downloaded else None
        total_bytes = sum(known_totals) if known_totals else None
        percent = None
        if bytes_downloaded is not None and total_bytes:
            percent = min(100.0, round((bytes_downloaded / total_bytes) * 100, 1))
        return ModelDownloadJobStatus(
            job_id=job.job_id,
            status=job.status,
            user_facing_message=job.user_facing_message,
            current_model_filename=job.current_model_filename,
            current_model_index=job.current_model_index,
            total_models=job.total_models,
            bytes_downloaded=bytes_downloaded,
            total_bytes=total_bytes,
            percent=percent,
            speed_bytes_per_second=job.speed_bytes_per_second,
            models=items,
        )

    def _sweep_old_jobs(self) -> None:
        cutoff = datetime.now(UTC) - JOB_TTL
        for job_id, job in list(self._jobs.items()):
            if job.status not in {"queued", "running"} and job.updated_at < cutoff:
                self._jobs.pop(job_id, None)


def _requirement_id(model: RequiredModel) -> str:
    if model.node_id and model.input_name:
        return f"{model.node_id}:{model.input_name}:{model.folder}/{model.filename}"
    return f"{model.folder}/{model.filename}"


def _progress_key(workflow_id: str, model: RequiredModel) -> str:
    return _progress_key_value(workflow_id, _requirement_id(model))


def _progress_key_value(workflow_id: str, requirement_id: str) -> str:
    return f"{workflow_id}:{requirement_id}"


def _dedupe_selections(selections: list[ModelDownloadSelection]) -> list[ModelDownloadSelection]:
    cleaned: list[ModelDownloadSelection] = []
    seen: set[tuple[str, str]] = set()
    for selection in selections:
        key = (selection.workflow_id, selection.requirement_id)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(selection)
    return cleaned


def _download_progress_status_label(status: str) -> str:
    return {
        "pending": "Pending",
        "queued": "Queued",
        "running": "Running",
        "downloading": "Downloading",
        "verifying": "Verifying",
        "succeeded": "Downloaded",
        "completed": "Completed",
        "download_failed": "Download failed",
        "verification_failed": "Verification failed",
        "authentication_required": "Authentication required",
        "access_denied": "Access denied",
        "rate_limited": "Rate limited",
        "hash_mismatch": "Hash mismatch",
        "not_enough_disk_space": "Not enough disk space",
        "needs_manual_download": "Needs manual download",
        "failed": "Failed",
        "canceled": "Canceled",
    }.get(status, status.replace("_", " ").title())


def _sanitize_error(exc: Exception) -> str:
    message = str(exc)[:240]
    message = message.replace("[redacted]", "<redacted>")
    message = re.sub(r"(?i)(token|api[_-]?key|key|signature|auth)=([^&\s]+)", r"\1 <redacted>", message)
    message = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer <redacted>", message)
    return message or "Model download failed."
