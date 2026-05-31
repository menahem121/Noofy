import asyncio
import time
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
    ImportModelVerificationJobStatus,
    RequiredModelAvailability,
    RequiredModelSummary,
    StagedWorkflowImportResponse,
)
from app.history import HistoryService
from app.artifacts import AssetOwnership
from app.workflows.importer import ImportedWorkflowPackageStore, NoofyImportError
from app.workflows.library_service import WorkflowLibraryService
from app.workflows.library import workflow_package_display_name
from app.workflows.model_availability import (
    ModelAvailabilityService,
    VerifyHashMetrics,
)
from app.workflows.package import RequiredModel, WorkflowPackage
from app.workflows.user_state import UserStateService
from app.workflows.verification_dispatch import (
    log_verification_concurrency,
    log_verification_metrics,
    order_by_package,
    run_parallel_model_verification,
)

IMPORT_SESSION_TTL = timedelta(hours=1)


class ImportSessionExpiredError(KeyError):
    """Raised when a staged workflow import session has expired."""


class DuplicateWorkflowIdentityError(RuntimeError):
    """Raised when a duplicate import needs an explicit user decision."""


@dataclass
class _PendingWorkflowImport:
    data: bytes
    original_filename: str | None
    allow_unverified_community_preparation: bool
    package: WorkflowPackage
    created_at: datetime
    updated_at: datetime
    active_download_job_id: str | None = None
    active_verification_job_id: str | None = None
    duplicate_identity: dict[str, object] | None = None


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


@dataclass
class _ImportModelVerificationJob:
    job_id: str
    import_session_id: str
    workflow_id: str
    task: asyncio.Task | None
    status: str
    user_facing_message: str
    started_at: datetime
    updated_at: datetime
    total_models: int
    verified_models: int = 0
    current_model_filename: str | None = None
    current_model_index: int | None = None
    model_summary: RequiredModelSummary | None = None
    models: dict[str, RequiredModelAvailability] | None = None


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


def _requirement_id(model: RequiredModel) -> str:
    if model.node_id and model.input_name:
        return f"{model.node_id}:{model.input_name}:{model.folder}/{model.filename}"
    return f"{model.folder}/{model.filename}"


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
        user_state_service: UserStateService | None = None,
    ) -> None:
        self.imported_package_store = imported_package_store
        self.workflow_library_service = workflow_library_service
        self.model_availability_service = model_availability_service
        self.log_store = log_store
        self.model_ownership_store = model_ownership_store
        self.history_service = history_service
        self.user_state_service = user_state_service
        self._pending_workflow_imports: dict[str, _PendingWorkflowImport] = {}
        self._import_model_download_jobs: dict[str, _ImportModelDownloadJob] = {}
        self._import_model_verification_jobs: dict[str, _ImportModelVerificationJob] = {}

    def import_workflow_archive(
        self,
        data: bytes,
        *,
        original_filename: str | None = None,
        allow_unverified_community_preparation: bool = False,
        duplicate_action: str | None = None,
    ) -> dict[str, object]:
        try:
            package = self.imported_package_store.import_archive(
                data,
                original_filename=original_filename,
                allow_unverified_community_preparation=allow_unverified_community_preparation,
                duplicate_action=duplicate_action,
            )
        except Exception as exc:
            if self.history_service is not None:
                self.history_service.record_import_failed(filename=original_filename, error=str(exc))
            raise
        if duplicate_action == "replace" and self.user_state_service is not None:
            self.user_state_service.delete(package.metadata.id)
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
        duplicate_identity = self._duplicate_identity_payload(package)
        if not package.required_models and duplicate_identity is None:
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
        model_summary = self._checking_model_summary(package) if package.required_models else None
        session_id = f"import-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        self._pending_workflow_imports[session_id] = _PendingWorkflowImport(
            data=data,
            original_filename=original_filename,
            allow_unverified_community_preparation=allow_unverified_community_preparation,
            package=package,
            created_at=now,
            updated_at=now,
            duplicate_identity=duplicate_identity,
        )
        if package.required_models:
            self._start_model_verification_for_import(session_id)
        status = package.import_metadata.status if package.import_metadata else "imported"
        message = package.import_metadata.user_facing_message if package.import_metadata else "Imported"
        if duplicate_identity is not None:
            status = "duplicate_identity"
            message = "This workflow is already in Noofy. Choose how to import it."
        self.log_store.add(
            "info",
            "Workflow import preview created",
            "workflow.import",
            workflow_id=package.metadata.id,
            details={
                "import_session_id": session_id,
                "required_model_count": len(package.required_models),
                "duplicate_identity": duplicate_identity is not None,
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
            duplicate_identity=duplicate_identity,
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
            verify_hashes=True,
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

    def import_model_verification_status(
        self,
        import_session_id: str,
    ) -> ImportModelVerificationJobStatus:
        pending = self._pending_import_or_raise(import_session_id)
        job_id = pending.active_verification_job_id
        if job_id is None:
            candidates = [
                job for job in self._import_model_verification_jobs.values()
                if job.import_session_id == import_session_id
            ]
            if not candidates:
                raise KeyError(f"Unknown model verification job for import session: {import_session_id}")
            job = max(candidates, key=lambda candidate: candidate.updated_at)
        else:
            job = self._import_model_verification_jobs.get(job_id)
            if job is None:
                raise KeyError(f"Unknown model verification job: {job_id}")
        return self._import_verification_job_status(job)

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

    def commit_workflow_import(
        self,
        import_session_id: str,
        *,
        duplicate_action: str | None = None,
    ) -> StagedWorkflowImportResponse:
        pending = self._pending_import_or_raise(import_session_id)
        if self._has_active_import_download(pending):
            raise RuntimeError("Model download is still running. Cancel it or wait for it to finish before continuing.")
        if pending.duplicate_identity is not None and duplicate_action not in {"replace", "copy"}:
            raise DuplicateWorkflowIdentityError(
                "This workflow is already in Noofy. Choose Replace existing workflow, Import as copy, or Cancel import."
            )
        started_at = datetime.now(UTC)
        self._pending_workflow_imports.pop(import_session_id, None)
        model_summary = self.workflow_library_service.model_availability_summary_for_package(
            pending.package,
            fast=True,
            verify_hashes=True,
        )
        committed = self.import_workflow_archive(
            pending.data,
            original_filename=pending.original_filename,
            allow_unverified_community_preparation=pending.allow_unverified_community_preparation,
            duplicate_action=duplicate_action,
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
                "duplicate_action": duplicate_action,
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

    def _start_model_verification_for_import(self, import_session_id: str) -> None:
        pending = self._pending_import_or_raise(import_session_id)
        job_id = f"model-verification-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        job = _ImportModelVerificationJob(
            job_id=job_id,
            import_session_id=import_session_id,
            workflow_id=pending.package.metadata.id,
            task=None,
            status="queued",
            user_facing_message="Model verification is queued.",
            started_at=now,
            updated_at=now,
            total_models=len(pending.package.required_models),
            model_summary=self._checking_model_summary(pending.package),
            models={
                item.requirement_id: item
                for item in self._checking_model_summary(pending.package).models
            },
        )
        self._import_model_verification_jobs[job_id] = job
        pending.active_verification_job_id = job_id
        pending.updated_at = now
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._run_model_verification_job_sync(job_id)
        else:
            job.task = loop.create_task(self._run_model_verification_job(job_id))

    async def _run_model_verification_job(self, job_id: str) -> None:
        job = self._import_model_verification_jobs[job_id]
        pending = self._pending_workflow_imports.get(job.import_session_id)
        if pending is None:
            job.status = "failed"
            job.user_facing_message = "The import session expired. Import the workflow again."
            job.updated_at = datetime.now(UTC)
            return
        self._begin_model_verification_job(job, pending)
        metrics = VerifyHashMetrics()
        models = list(pending.package.required_models)
        concurrency, downgrade_reason = (
            self.model_availability_service.select_verification_concurrency(len(models))
        )
        log_verification_concurrency(
            self.log_store,
            workflow_id=pending.package.metadata.id,
            model_count=len(models),
            selected_concurrency=concurrency,
            downgrade_reason=downgrade_reason,
        )
        started_at = time.monotonic()
        try:
            await run_parallel_model_verification(
                models,
                verify=lambda model: self._verify_import_model(
                    pending.package, model, metrics
                ),
                on_start=lambda index, model: self._record_model_verification_progress(
                    job, pending, index, model
                ),
                on_result=lambda availability: self._record_verified_import_model(
                    job, pending, availability
                ),
                concurrency=concurrency,
            )
            self._finish_model_verification_job(job, pending)
            log_verification_metrics(
                self.log_store,
                workflow_id=pending.package.metadata.id,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                model_count=len(models),
                metrics=metrics,
                selected_concurrency=concurrency,
                downgrade_reason=downgrade_reason,
            )
        except Exception as exc:
            self._fail_model_verification_job(job, pending, exc)

    def _run_model_verification_job_sync(self, job_id: str) -> None:
        job = self._import_model_verification_jobs[job_id]
        pending = self._pending_workflow_imports.get(job.import_session_id)
        if pending is None:
            job.status = "failed"
            job.user_facing_message = "The import session expired. Import the workflow again."
            job.updated_at = datetime.now(UTC)
            return
        self._begin_model_verification_job(job, pending)
        try:
            for index, model in enumerate(pending.package.required_models, start=1):
                self._record_model_verification_progress(job, pending, index, model)
                availability = self._verify_import_model(pending.package, model)
                self._record_verified_import_model(job, pending, availability)
            self._finish_model_verification_job(job, pending)
        except Exception as exc:
            self._fail_model_verification_job(job, pending, exc)

    def _begin_model_verification_job(
        self,
        job: _ImportModelVerificationJob,
        pending: _PendingWorkflowImport,
    ) -> None:
        now = datetime.now(UTC)
        job.status = "running"
        job.user_facing_message = "Verifying local model files..."
        job.updated_at = now
        pending.updated_at = now

    def _record_model_verification_progress(
        self,
        job: _ImportModelVerificationJob,
        pending: _PendingWorkflowImport,
        index: int,
        model: RequiredModel,
    ) -> None:
        now = datetime.now(UTC)
        job.current_model_index = index
        job.current_model_filename = model.filename
        job.updated_at = now
        pending.updated_at = now

    def _record_verified_import_model(
        self,
        job: _ImportModelVerificationJob,
        pending: _PendingWorkflowImport,
        availability: RequiredModelAvailability,
    ) -> None:
        now = datetime.now(UTC)
        if job.models is None:
            job.models = {}
        job.models[availability.requirement_id] = availability
        job.verified_models = len(
            [
                item for item in job.models.values()
                if item.status != "checking"
            ]
        )
        job.model_summary = self._model_summary_from_availability(
            pending.package,
            list(job.models.values()),
        )
        job.updated_at = now
        pending.updated_at = now

    def _finish_model_verification_job(
        self,
        job: _ImportModelVerificationJob,
        pending: _PendingWorkflowImport,
    ) -> None:
        now = datetime.now(UTC)
        job.status = "completed"
        job.user_facing_message = "Model verification finished."
        job.current_model_filename = None
        job.current_model_index = None
        job.updated_at = now
        pending.updated_at = now
        if pending.active_verification_job_id == job.job_id:
            pending.active_verification_job_id = None

    def _fail_model_verification_job(
        self,
        job: _ImportModelVerificationJob,
        pending: _PendingWorkflowImport,
        exc: Exception,
    ) -> None:
        now = datetime.now(UTC)
        job.status = "failed"
        job.user_facing_message = "Model verification failed. You can retry by importing the workflow again."
        job.updated_at = now
        pending.updated_at = now
        if pending.active_verification_job_id == job.job_id:
            pending.active_verification_job_id = None
        self.log_store.add(
            "warning",
            "Import model verification failed",
            "workflow.models",
            workflow_id=pending.package.metadata.id,
            details={"job_id": job.job_id, "error": str(exc)},
        )

    def _verify_import_model(
        self,
        package: WorkflowPackage,
        model: RequiredModel,
        metrics: VerifyHashMetrics | None = None,
    ) -> RequiredModelAvailability:
        single_model_package = package.model_copy(update={"required_models": [model]})
        summary = self.workflow_library_service.model_availability_summary_for_package(
            single_model_package,
            fast=True,
            verify_hashes=True,
            metrics=metrics,
        )
        return summary.models[0]

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
        last_progress_by_model: dict[str, int] = {}

        def progress_callback(event: dict[str, object]) -> None:
            nonlocal last_progress_at
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
                delta = max(bytes_downloaded - last_progress_by_model.get(requirement_id, 0), 0)
                job.speed_bytes_per_second = delta / elapsed
                last_progress_by_model[requirement_id] = bytes_downloaded
                last_progress_at = now
                job.bytes_downloaded = (job.bytes_downloaded or 0) + delta
            if isinstance(total_bytes, int):
                known_total = (
                    job.models.get(requirement_id).total_bytes
                    if job.models and requirement_id in job.models
                    else None
                )
                if not isinstance(known_total, int):
                    job.total_bytes = (job.total_bytes or 0) + total_bytes
            label = _download_progress_status_label(status)
            if job.models is None:
                job.models = {}
            previous = job.models.get(requirement_id)
            previous_bytes = (
                previous.bytes_downloaded
                if previous is not None and isinstance(previous.bytes_downloaded, int)
                else None
            )
            previous_total = (
                previous.total_bytes
                if previous is not None and isinstance(previous.total_bytes, int)
                else None
            )
            job.models[requirement_id] = ImportModelDownloadProgressItem(
                requirement_id=requirement_id,
                filename=filename,
                status=status,  # type: ignore[arg-type]
                status_label=label,
                bytes_downloaded=bytes_downloaded if isinstance(bytes_downloaded, int) else previous_bytes,
                total_bytes=total_bytes if isinstance(total_bytes, int) else previous_total,
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
                job.status = "completed_with_errors" if result.downloaded_count > 0 else "failed"
                job.user_facing_message = "Some downloads failed."
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
        items = list((job.models or {}).values())
        known_downloaded = [
            item.bytes_downloaded
            for item in items
            if isinstance(item.bytes_downloaded, int)
        ]
        known_totals = [
            item.total_bytes
            for item in items
            if isinstance(item.total_bytes, int)
        ]
        bytes_downloaded = sum(known_downloaded) if known_downloaded else job.bytes_downloaded
        total_bytes = sum(known_totals) if known_totals else job.total_bytes
        percent = None
        if bytes_downloaded is not None and total_bytes:
            percent = min(100.0, round((bytes_downloaded / total_bytes) * 100, 1))
        return ImportModelDownloadJobStatus(
            job_id=job.job_id,
            import_session_id=job.import_session_id,
            workflow_id=job.workflow_id,
            status=job.status,  # type: ignore[arg-type]
            user_facing_message=job.user_facing_message,
            current_model_filename=job.current_model_filename,
            current_model_index=job.current_model_index,
            total_models=job.total_models,
            bytes_downloaded=bytes_downloaded,
            total_bytes=total_bytes,
            percent=percent,
            speed_bytes_per_second=job.speed_bytes_per_second,
            models=items,
            model_summary=job.model_summary,
        )

    def _import_verification_job_status(
        self,
        job: _ImportModelVerificationJob,
    ) -> ImportModelVerificationJobStatus:
        percent = None
        if job.total_models:
            percent = min(100.0, round((job.verified_models / job.total_models) * 100, 1))
        return ImportModelVerificationJobStatus(
            job_id=job.job_id,
            import_session_id=job.import_session_id,
            workflow_id=job.workflow_id,
            status=job.status,  # type: ignore[arg-type]
            user_facing_message=job.user_facing_message,
            current_model_filename=job.current_model_filename,
            current_model_index=job.current_model_index,
            total_models=job.total_models,
            verified_models=job.verified_models,
            percent=percent,
            # Prefer the package-ordered summary so the list stays deterministic under
            # parallel (completion-ordered) verification.
            models=(
                list(job.model_summary.models)
                if job.model_summary is not None
                else list((job.models or {}).values())
            ),
            model_summary=job.model_summary,
        )

    def _checking_model_summary(self, package: WorkflowPackage) -> RequiredModelSummary:
        return self._model_summary_from_availability(
            package,
            [self._checking_model_availability(model) for model in package.required_models],
        )

    def _duplicate_identity_payload(self, package: WorkflowPackage) -> dict[str, object] | None:
        has_identity = getattr(self.imported_package_store, "has_package_identity", None)
        if callable(has_identity):
            exists = bool(has_identity(package))
        else:
            package_dir = getattr(self.imported_package_store, "package_dir", lambda _package: None)(package)
            exists = bool(package_dir is not None and package_dir.exists())
        if not exists:
            return None

        try:
            existing_package = self.workflow_library_service.workflow_loader.get_package(package.metadata.id)
            existing_workflow = self.workflow_library_service.workflow_summary(existing_package)
        except Exception:
            existing_workflow = {
                "id": package.metadata.id,
                "name": workflow_package_display_name(package),
                "display_name": workflow_package_display_name(package),
                "version": package.metadata.version,
            }

        return {
            "status": "conflict",
            "user_facing_message": "A workflow with this identity already exists in Noofy.",
            "existing_workflow": existing_workflow,
            "incoming_workflow": self.workflow_library_service.workflow_summary(package),
            "actions": ["replace", "copy", "cancel"],
        }

    def _checking_model_availability(self, model: RequiredModel) -> RequiredModelAvailability:
        source_urls = list(getattr(model, "source_urls", []) or [])
        if not source_urls and getattr(model, "source_url", None):
            source_urls = [str(model.source_url)]
        return RequiredModelAvailability(
            requirement_id=_requirement_id(model),
            node_id=model.node_id,
            node_type=model.node_type,
            input_name=model.input_name,
            filename=model.filename,
            model_type=model.model_type,
            folder=model.folder,
            verification_level=model.verification_level,
            size_bytes=model.size_bytes,
            source_urls=source_urls,
            source_availability="known" if source_urls else "unknown",
            status="checking",
            status_label="Checking",
            asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
            message="Noofy is checking whether this model is already available locally.",
        )

    def _model_summary_from_availability(
        self,
        package: WorkflowPackage,
        models: list[RequiredModelAvailability],
    ) -> RequiredModelSummary:
        models = order_by_package(package, models)
        available_count = sum(model.status == "available" for model in models)
        possible_count = sum(model.status == "possible_match" for model in models)
        missing_count = sum(model.status == "missing" for model in models)
        manual_count = sum(model.status == "needs_manual_download" for model in models)
        return RequiredModelSummary(
            workflow_id=package.metadata.id,
            total_count=len(models),
            available_count=available_count,
            possible_match_count=possible_count,
            missing_count=missing_count,
            needs_manual_download_count=manual_count,
            ready_to_run=bool(models) and len(models) == available_count,
            models=models,
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
