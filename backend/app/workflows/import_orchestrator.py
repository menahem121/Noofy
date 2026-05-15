import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.diagnostics import DiagnosticsSink
from app.engine.models import (
    ImportModelDownloadJobStart,
    ImportModelDownloadJobStatus,
    ImportModelDownloadProgressItem,
    StagedWorkflowImportResponse,
)
from app.history import HistoryService
from app.workflows.importer import ImportedWorkflowPackageStore, NoofyImportError
from app.workflows.library_service import WorkflowLibraryService
from app.workflows.model_availability import ModelAvailabilityService
from app.workflows.package import WorkflowPackage

IMPORT_SESSION_TTL = timedelta(hours=1)


class ImportSessionExpiredError(KeyError):
    """Raised when a staged workflow import session has expired."""


@dataclass
class _PendingWorkflowImport:
    data: bytes
    original_filename: str | None
    allow_unverified_community_preparation: bool
    package: WorkflowPackage
    created_at: datetime
    updated_at: datetime
    active_download_job_id: str | None = None


@dataclass
class _ImportModelDownloadJob:
    job_id: str
    import_session_id: str
    workflow_id: str
    cancel_event: asyncio.Event
    task: asyncio.Task | None
    status: str
    user_facing_message: str
    started_at: datetime
    updated_at: datetime
    total_models: int
    model_summary: Any | None = None
    models: dict[str, ImportModelDownloadProgressItem] | None = None
    current_model_filename: str | None = None
    current_model_index: int | None = None
    bytes_downloaded: int | None = None
    total_bytes: int | None = None
    speed_bytes_per_second: float | None = None


def _download_progress_status_label(status: str) -> str:
    return {
        "queued": "Queued",
        "downloading": "Downloading",
        "verifying": "Verifying",
        "completed": "Completed",
        "failed": "Failed",
        "canceled": "Canceled",
    }.get(status, status.replace("_", " ").title())


class WorkflowImportOrchestrator:
    """Stateful orchestrator for staged workflow import and per-import model downloads."""

    def __init__(
        self,
        imported_package_store: ImportedWorkflowPackageStore,
        workflow_library_service: WorkflowLibraryService,
        model_availability_service: ModelAvailabilityService,
        log_store: DiagnosticsSink,
        model_ownership_store: object | None = None,
        history_service: HistoryService | None = None,
    ) -> None:
        self.imported_package_store = imported_package_store
        self.workflow_library_service = workflow_library_service
        self.model_availability_service = model_availability_service
        self.log_store = log_store
        self.model_ownership_store = model_ownership_store
        self.history_service = history_service
        self._pending_workflow_imports: dict[str, _PendingWorkflowImport] = {}
        self._import_model_download_jobs: dict[str, _ImportModelDownloadJob] = {}

    def import_workflow_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ) -> dict[str, object]:
        try:
            package = self.imported_package_store.import_archive(
                data,
                original_filename=original_filename,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
            )
        except Exception as exc:
            if self.history_service is not None:
                self.history_service.record_import_failed(filename=original_filename, error=str(exc))
            raise
        status = package.import_metadata.status if package.import_metadata else "imported"
        message = package.import_metadata.user_facing_message if package.import_metadata else "Imported"
        response = {
            "workflow_id": package.metadata.id,
            "status": status,
            "user_facing_message": message,
            "workflow": self.workflow_library_service.workflow_summary(package),
            "required_model_count": len(package.required_models),
            "custom_node_count": len(package.custom_nodes),
            "unresolved_input_count": len(package.unresolved_runtime_inputs),
        }
        if self.history_service is not None:
            self.history_service.record_workflow_imported(response["workflow"])
        return response

    def preview_workflow_import(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
    ) -> StagedWorkflowImportResponse:
        self._cleanup_expired_import_sessions()
        package = self.imported_package_store.preview_archive(
            data,
            original_filename=original_filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
        )
        model_summary = self.workflow_library_service.model_availability_summary_for_package(
            package,
            fast=True,
        )
        if not package.required_models:
            committed = self.import_workflow_archive(
                data,
                original_filename=original_filename,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
            )
            return StagedWorkflowImportResponse(
                import_session_id=None,
                model_summary=None,
                **committed,
            )
        session_id = f"import-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        self._pending_workflow_imports[session_id] = _PendingWorkflowImport(
            data=data,
            original_filename=original_filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
            package=package,
            created_at=now,
            updated_at=now,
        )
        status = package.import_metadata.status if package.import_metadata else "imported"
        message = package.import_metadata.user_facing_message if package.import_metadata else "Imported"
        self.log_store.add(
            "info",
            "Workflow import preview created",
            "workflow.import",
            workflow_id=package.metadata.id,
            details={
                "import_session_id": session_id,
                "required_model_count": len(package.required_models),
            },
        )
        return StagedWorkflowImportResponse(
            import_session_id=session_id,
            workflow_id=package.metadata.id,
            status=status,
            user_facing_message=message,
            workflow=self.workflow_library_service.workflow_summary(package),
            required_model_count=len(package.required_models),
            custom_node_count=len(package.custom_nodes),
            unresolved_input_count=len(package.unresolved_runtime_inputs),
            model_summary=model_summary,
        )

    def start_missing_model_download_for_import(
        self, import_session_id: str
    ) -> ImportModelDownloadJobStart:
        pending = self._pending_import_or_raise(import_session_id)
        if pending.active_download_job_id is not None:
            active = self._import_model_download_jobs.get(pending.active_download_job_id)
            if active is not None and active.status in {"queued", "running"}:
                return ImportModelDownloadJobStart(
                    job_id=active.job_id,
                    import_session_id=import_session_id,
                    workflow_id=pending.package.metadata.id,
                    status=active.status,
                    user_facing_message=active.user_facing_message,
                )

        before = self.workflow_library_service.model_availability_summary_for_package(
            pending.package,
            fast=True,
        )
        missing_models = [model for model in before.models if model.status == "missing"]
        job_id = f"model-download-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        job = _ImportModelDownloadJob(
            job_id=job_id,
            import_session_id=import_session_id,
            workflow_id=pending.package.metadata.id,
            cancel_event=asyncio.Event(),
            task=None,
            status="queued",
            user_facing_message="Model download is queued.",
            started_at=now,
            updated_at=now,
            total_models=len(missing_models),
            model_summary=before,
            models={
                item.requirement_id: ImportModelDownloadProgressItem(
                    requirement_id=item.requirement_id,
                    filename=item.filename,
                    status="queued",
                    status_label="Queued",
                    total_bytes=item.size_bytes,
                )
                for item in missing_models
            },
        )
        self._import_model_download_jobs[job_id] = job
        pending.active_download_job_id = job_id
        pending.updated_at = now
        job.task = asyncio.create_task(self._run_import_model_download_job(job_id))
        return ImportModelDownloadJobStart(
            job_id=job_id,
            import_session_id=import_session_id,
            workflow_id=pending.package.metadata.id,
            status=job.status,
            user_facing_message=job.user_facing_message,
        )

    def import_model_download_status(
        self, import_session_id: str, job_id: str
    ) -> ImportModelDownloadJobStatus:
        self._pending_import_or_raise(import_session_id)
        job = self._import_model_download_jobs.get(job_id)
        if job is None or job.import_session_id != import_session_id:
            raise KeyError(f"Unknown model download job: {job_id}")
        return self._import_download_job_status(job)

    def cancel_import_model_download_job(
        self, import_session_id: str, job_id: str
    ) -> ImportModelDownloadJobStatus:
        self._pending_import_or_raise(import_session_id)
        job = self._import_model_download_jobs.get(job_id)
        if job is None or job.import_session_id != import_session_id:
            raise KeyError(f"Unknown model download job: {job_id}")
        if job.status in {"queued", "running"}:
            job.cancel_event.set()
            job.status = "canceled"
            job.user_facing_message = "Canceling model download..."
            job.updated_at = datetime.now(UTC)
        return self._import_download_job_status(job)

    def commit_workflow_import(self, import_session_id: str) -> StagedWorkflowImportResponse:
        pending = self._pending_import_or_raise(import_session_id)
        if self._has_active_import_download(pending):
            raise RuntimeError("Model download is still running. Cancel it or wait for it to finish before continuing.")
        started_at = datetime.now(UTC)
        self._pending_workflow_imports.pop(import_session_id, None)
        model_summary = self.workflow_library_service.model_availability_summary_for_package(
            pending.package,
            fast=True,
        )
        committed = self.import_workflow_archive(
            pending.data,
            original_filename=pending.original_filename,
            allow_unverified_community_preparation=pending.allow_unverified_community_preparation,
        )
        finished_at = datetime.now(UTC)
        self.log_store.add(
            "info",
            "Staged workflow import committed",
            "workflow.import",
            workflow_id=pending.package.metadata.id,
            details={
                "import_session_id": import_session_id,
                "duration_ms": round((finished_at - started_at).total_seconds() * 1000, 1),
                "reused_preview_package": True,
            },
        )
        return StagedWorkflowImportResponse(
            import_session_id=None,
            model_summary=model_summary,
            **committed,
        )

    def cancel_workflow_import(self, import_session_id: str) -> dict[str, object]:
        try:
            pending = self._pending_import_or_raise(import_session_id)
        except ImportSessionExpiredError:
            raise
        except KeyError:
            pending = None
        if pending is not None and pending.active_download_job_id is not None:
            job = self._import_model_download_jobs.get(pending.active_download_job_id)
            if job is not None and job.status in {"queued", "running"}:
                job.cancel_event.set()
        removed = self._pending_workflow_imports.pop(import_session_id, None)
        return {
            "import_session_id": import_session_id,
            "status": "canceled" if removed is not None else "not_found",
        }

    async def _run_import_model_download_job(self, job_id: str) -> None:
        job = self._import_model_download_jobs[job_id]
        pending = self._pending_workflow_imports.get(job.import_session_id)
        if pending is None:
            job.status = "failed"
            job.user_facing_message = "The import session expired. Import the workflow again."
            job.updated_at = datetime.now(UTC)
            return
        job.status = "running"
        job.user_facing_message = "Downloading required models..."
        job.updated_at = datetime.now(UTC)
        pending.updated_at = job.updated_at
        last_progress_at = job.updated_at
        last_progress_bytes = 0

        def progress_callback(event: dict[str, object]) -> None:
            nonlocal last_progress_at, last_progress_bytes
            now = datetime.now(UTC)
            requirement_id = str(event["requirement_id"])
            filename = str(event["filename"])
            status = str(event["status"])
            bytes_downloaded = event.get("bytes_downloaded")
            total_bytes = event.get("total_bytes")
            model_index = event.get("model_index")
            message = event.get("message")
            if isinstance(model_index, int):
                job.current_model_index = model_index
            job.current_model_filename = filename
            if isinstance(bytes_downloaded, int):
                elapsed = max((now - last_progress_at).total_seconds(), 0.001)
                delta = max(bytes_downloaded - last_progress_bytes, 0)
                job.speed_bytes_per_second = delta / elapsed
                last_progress_bytes = bytes_downloaded
                last_progress_at = now
                job.bytes_downloaded = bytes_downloaded
            if isinstance(total_bytes, int):
                job.total_bytes = total_bytes
            label = _download_progress_status_label(status)
            if job.models is None:
                job.models = {}
            job.models[requirement_id] = ImportModelDownloadProgressItem(
                requirement_id=requirement_id,
                filename=filename,
                status=status,  # type: ignore[arg-type]
                status_label=label,
                bytes_downloaded=bytes_downloaded if isinstance(bytes_downloaded, int) else None,
                total_bytes=total_bytes if isinstance(total_bytes, int) else None,
                message=str(message) if message else None,
            )
            job.updated_at = now
            pending.updated_at = now

        try:
            result = await self.model_availability_service.download_missing(
                pending.package,
                progress_callback=progress_callback,
                cancel_event=job.cancel_event,
            )
            job.model_summary = result.model_summary
            if result.status == "canceled" or job.cancel_event.is_set():
                job.status = "canceled"
                job.user_facing_message = result.user_facing_message
            elif result.failed_count:
                job.status = "failed"
                job.user_facing_message = result.user_facing_message
            else:
                job.status = "completed"
                job.user_facing_message = result.user_facing_message
                self._mark_import_downloads_as_noofy_downloaded(pending.package)
        except Exception:
            job.status = "failed"
            job.user_facing_message = "The model download failed. The partial download was cleaned up safely."
            job.model_summary = self.workflow_library_service.model_availability_summary_for_package(pending.package)
            self.log_store.add(
                "warning",
                "Import model download job failed",
                "workflow.models",
                workflow_id=pending.package.metadata.id,
                details={"job_id": job.job_id},
            )
        finally:
            now = datetime.now(UTC)
            job.updated_at = now
            pending.updated_at = now
            if pending.active_download_job_id == job.job_id:
                pending.active_download_job_id = None

    def _mark_import_downloads_as_noofy_downloaded(self, package: WorkflowPackage) -> None:
        if self.model_ownership_store is None:
            return
        noofy_models_dir = getattr(self.model_availability_service, "noofy_models_dir", None)
        if noofy_models_dir is None:
            return
        root = Path(noofy_models_dir)
        for model in package.required_models:
            target = root / model.folder / model.filename
            try:
                resolved = target.resolve(strict=False)
                root_resolved = root.resolve(strict=False)
            except OSError:
                continue
            if resolved != root_resolved and root_resolved not in resolved.parents:
                continue
            if target.is_file():
                filename = model.filename.replace("\\", "/")
                self.model_ownership_store.mark_downloaded(f"{model.folder}/{filename}")  # type: ignore[union-attr]

    def _import_download_job_status(
        self, job: _ImportModelDownloadJob
    ) -> ImportModelDownloadJobStatus:
        percent = None
        if job.bytes_downloaded is not None and job.total_bytes:
            percent = min(100.0, round((job.bytes_downloaded / job.total_bytes) * 100, 1))
        return ImportModelDownloadJobStatus(
            job_id=job.job_id,
            import_session_id=job.import_session_id,
            workflow_id=job.workflow_id,
            status=job.status,  # type: ignore[arg-type]
            user_facing_message=job.user_facing_message,
            current_model_filename=job.current_model_filename,
            current_model_index=job.current_model_index,
            total_models=job.total_models,
            bytes_downloaded=job.bytes_downloaded,
            total_bytes=job.total_bytes,
            percent=percent,
            speed_bytes_per_second=job.speed_bytes_per_second,
            models=list((job.models or {}).values()),
            model_summary=job.model_summary,
        )

    def _pending_import_or_raise(self, import_session_id: str) -> _PendingWorkflowImport:
        expired = self._cleanup_expired_import_sessions()
        if import_session_id in expired:
            raise ImportSessionExpiredError(
                "The import session expired. Please import the workflow again."
            )
        pending = self._pending_workflow_imports.get(import_session_id)
        if pending is None:
            raise KeyError(f"Unknown workflow import session: {import_session_id}")
        pending.updated_at = datetime.now(UTC)
        return pending

    def _cleanup_expired_import_sessions(self) -> set[str]:
        now = datetime.now(UTC)
        expired: list[str] = []
        for session_id, pending in self._pending_workflow_imports.items():
            if self._has_active_import_download(pending):
                pending.updated_at = now
                continue
            if now - pending.updated_at > IMPORT_SESSION_TTL:
                expired.append(session_id)
        for session_id in expired:
            pending = self._pending_workflow_imports.pop(session_id)
            self.log_store.add(
                "info",
                "Expired workflow import preview session removed",
                "workflow.import",
                workflow_id=pending.package.metadata.id,
                details={"import_session_id": session_id},
            )
        return set(expired)

    def _has_active_import_download(self, pending: _PendingWorkflowImport) -> bool:
        if pending.active_download_job_id is None:
            return False
        job = self._import_model_download_jobs.get(pending.active_download_job_id)
        return job is not None and job.status in {"queued", "running"}
