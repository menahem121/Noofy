import asyncio
import inspect
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.diagnostics import DiagnosticsSink
from app.engine.models import (
    RequiredModelAvailability,
    RequiredModelSummary,
    WorkflowModelVerificationJobStatus,
)
from app.history import HistoryService
from app.trust import workflow_source_policy, workflow_trust_payload
from app.workflows.exporter import stored_comfyui_graph_file
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.library import (
    WorkflowLibraryStore,
    WorkflowMetadataUpdate,
    workflow_package_display_name,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.model_availability import (
    ModelAvailabilityService,
    VerifyHashMetrics,
)
from app.workflows.package import RequiredModel, WorkflowPackage
from app.workflows.verification_dispatch import (
    log_verification_concurrency,
    log_verification_metrics,
    order_by_package,
    run_parallel_model_verification,
)
from app.workflows.store_paths import (
    assert_path_within,
    mutable_package_dir,
    safe_store_segment,
)


@dataclass
class _WorkflowModelVerificationJob:
    job_id: str
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


class WorkflowLibraryService:
    """Workflow library listing, details, metadata management, and model availability."""

    def __init__(
        self,
        workflow_loader: WorkflowPackageLoader,
        model_availability_service: ModelAvailabilityService,
        log_store: DiagnosticsSink,
        workflow_library_store: WorkflowLibraryStore | None = None,
        imported_package_store: ImportedWorkflowPackageStore | None = None,
        history_service: HistoryService | None = None,
    ) -> None:
        self.workflow_loader = workflow_loader
        self.model_availability_service = model_availability_service
        self.log_store = log_store
        self.workflow_library_store = workflow_library_store
        self.imported_package_store = imported_package_store
        self.history_service = history_service
        self._model_verification_jobs: dict[str, _WorkflowModelVerificationJob] = {}
        self._active_model_verification_by_workflow: dict[str, str] = {}

    def list_workflows(self) -> list[dict[str, object]]:
        return [
            self.workflow_summary(package)
            for package in self.workflow_loader.list_packages()
        ]

    def record_workflow_opened(self, workflow_id: str) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        record = (
            self.workflow_library_store.record_workflow_opened(workflow_id)
            if self.workflow_library_store is not None
            else None
        )
        self.log_store.add(
            "info",
            "Workflow opened from library",
            "workflow.library",
            workflow_id=workflow_id,
            details={"last_opened": record.last_opened_at if record is not None else None},
        )
        return {
            "workflow_id": workflow_id,
            "last_opened": record.last_opened_at if record is not None else None,
            "workflow": self.workflow_summary(package),
        }

    def workflow_details(self, workflow_id: str) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        summary = self.workflow_summary(package)
        model_summary = None
        try:
            model_summary = self.model_availability_service.summarize(package)
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Workflow details model summary unavailable",
                "engine.service",
                workflow_id=workflow_id,
                details={"error": str(exc)},
            )

        if model_summary is not None:
            models = [
                {
                    "name": model.filename,
                    "type": model.model_type or model.folder,
                    "size_bytes": model.size_bytes,
                    "status": model.status,
                    "status_label": model.status_label,
                    "folder": model.folder,
                    "source_path": model.source_path,
                }
                for model in model_summary.models
            ]
        else:
            models = [
                {
                    "name": model.filename,
                    "type": model.model_type or model.folder,
                    "size_bytes": model.size_bytes,
                    "status": "unknown",
                    "status_label": "Unknown",
                    "folder": model.folder,
                    "source_path": None,
                }
                for model in package.required_models
            ]

        metadata = self._library_metadata(package)
        return {
            **summary,
            "overview": {
                "display_name": metadata["display_name"],
                "description": metadata["description"],
                "author": metadata["author"],
                "website": metadata["website"],
                "source": summary["source_label"],
                "version": package.metadata.version,
            },
            "models_used": models,
            "run_history": self._run_history_summary(package),
            "organization": {
                "display_name": metadata["display_name"],
                "category": metadata["category"],
                "tags": metadata["tags"],
                "icon": metadata["icon"],
            },
            "advanced": {
                "package_id": summary["package_id"],
                "engine": package.engine,
                "trust_level": package.identity.trust_level if package.identity else "noofy_verified",
                "trust_label": package.identity.trust_level.replace("_", " ").title() if package.identity else "Noofy Verified",
                "can_export_noofy": summary["can_export_noofy"],
                "can_export_comfyui_json": True,
                "can_remove": summary["can_remove"],
            },
        }

    def workflow_package_payload(self, workflow_id: str) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        payload = package.model_dump()
        metadata = self._library_metadata(package)
        display_name = metadata["display_name"]
        payload["display_name"] = display_name
        package_metadata = payload.get("metadata")
        if isinstance(package_metadata, dict):
            package_metadata["display_name"] = display_name
            package_metadata["name"] = display_name
        return payload

    def update_workflow_metadata(
        self,
        workflow_id: str,
        update: WorkflowMetadataUpdate,
    ) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        metadata = (
            self.workflow_library_store.update_metadata(workflow_id, update)
            if self.workflow_library_store is not None
            else update
        )
        package_dir = self._mutable_package_dir(package)
        if package_dir is not None and package_dir.exists():
            self._update_internal_package_metadata(package_dir, update)
        self.log_store.add(
            "info",
            "Workflow library metadata updated",
            "workflow.library",
            workflow_id=workflow_id,
            details={
                "fields": sorted(update.model_dump(exclude_unset=True).keys()),
                "mutable_package_updated": package_dir is not None and package_dir.exists(),
            },
        )
        return {
            "workflow_id": workflow_id,
            "metadata": metadata.model_dump(mode="json", exclude_none=True)
            if hasattr(metadata, "model_dump")
            else update.model_dump(mode="json", exclude_none=True),
            "workflow": self.workflow_summary(self.workflow_loader.get_package(workflow_id)),
        }

    def remove_workflow(self, workflow_id: str) -> dict[str, object]:
        package = self.workflow_loader.get_package(workflow_id)
        package_dir = self._mutable_package_dir(package)
        if package_dir is None or not package_dir.exists() or not self._can_remove_workflow(package):
            raise ValueError("Native Noofy workflows cannot be removed.")
        root_dir = (
            self.imported_package_store.root_dir
            if self.imported_package_store is not None
            else settings.paths.workflow_packages_store_dir
        )
        assert_path_within(root_dir, package_dir, purpose="remove workflow")
        if package_dir.is_symlink() or not package_dir.is_dir():
            raise ValueError("Workflow package path is not a removable directory.")
        workflow_snapshot = self.workflow_summary(package)
        shutil.rmtree(package_dir)
        if self.workflow_library_store is not None:
            self.workflow_library_store.remove_workflow(workflow_id)
        if self.history_service is not None:
            self.history_service.record_workflow_removed(workflow_snapshot)
        self.log_store.add(
            "info",
            "Workflow removed from library",
            "workflow.library",
            workflow_id=workflow_id,
            details={"package_dir": str(package_dir)},
        )
        return {"workflow_id": workflow_id, "removed": True}

    def export_workflow_comfyui_graph(self, workflow_id: str) -> tuple[bytes, str]:
        package = self.workflow_loader.get_package(workflow_id)
        package_dir = self._mutable_package_dir(package)
        if package_dir is not None:
            graph_file = stored_comfyui_graph_file(package_dir)
            if graph_file.exists():
                return graph_file.read_bytes(), f"{safe_store_segment(workflow_id)}.comfyui.json"
        payload = json.dumps(package.comfyui_graph, indent=2, sort_keys=True).encode("utf-8")
        return payload, f"{safe_store_segment(workflow_id)}.comfyui.json"

    def model_availability_summary(self, workflow_id: str) -> RequiredModelSummary:
        return self.model_availability_service.summarize(
            self.workflow_loader.get_package(workflow_id)
        )

    def start_model_verification(self, workflow_id: str) -> WorkflowModelVerificationJobStatus:
        package = self.workflow_loader.get_package(workflow_id)
        active_job_id = self._active_model_verification_by_workflow.get(workflow_id)
        if active_job_id:
            active_job = self._model_verification_jobs.get(active_job_id)
            if active_job and active_job.status in {"queued", "running"}:
                return self._model_verification_job_status(active_job)

        job_id = f"workflow-model-verification-{uuid.uuid4().hex}"
        now = datetime.now(UTC)
        checking_summary = self._checking_model_summary(package)
        job = _WorkflowModelVerificationJob(
            job_id=job_id,
            workflow_id=workflow_id,
            task=None,
            status="queued",
            user_facing_message="Model verification is queued.",
            started_at=now,
            updated_at=now,
            total_models=len(package.required_models),
            model_summary=checking_summary,
            models={item.requirement_id: item for item in checking_summary.models},
        )
        self._model_verification_jobs[job_id] = job
        self._active_model_verification_by_workflow[workflow_id] = job_id
        self.log_store.add(
            "info",
            "Workflow model verification queued",
            "workflow.models",
            workflow_id=workflow_id,
            details={"job_id": job_id, "total_models": job.total_models},
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._run_model_verification_job_sync(job_id)
        else:
            job.task = loop.create_task(self._run_model_verification_job(job_id))
        return self._model_verification_job_status(job)

    def model_verification_status(
        self,
        workflow_id: str,
        job_id: str,
    ) -> WorkflowModelVerificationJobStatus:
        job = self._model_verification_jobs.get(job_id)
        if job is None or job.workflow_id != workflow_id:
            raise KeyError(f"Unknown model verification job: {job_id}")
        return self._model_verification_job_status(job)

    def model_availability_summary_for_package(
        self,
        package: WorkflowPackage,
        *,
        fast: bool = False,
        verify_hashes: bool = False,
        metrics: VerifyHashMetrics | None = None,
    ) -> RequiredModelSummary:
        return self._summarize_models(
            package,
            fast=fast,
            verify_hashes=verify_hashes,
            metrics=metrics,
        )

    def workflow_summary(self, package: WorkflowPackage) -> dict[str, object]:
        status = _effective_workflow_status(package)
        user_facing_status = _effective_workflow_status_label(package, status)
        metadata = self._library_metadata(package)
        model_counts = self._model_count_summary(package)
        missing_model_count = model_counts["missing_model_count"]
        needs_setup = _dashboard_needs_setup(package) or status in {
            "cannot_prepare_automatically",
            "blocked_by_policy",
            "unsupported",
        }
        return {
            "id": package.metadata.id,
            "name": metadata["display_name"],
            "display_name": metadata["display_name"],
            "version": package.metadata.version,
            "icon": metadata["icon"],
            "source_label": self._source_label(package),
            "main_model": self._main_model_summary(package),
            "description": metadata["description"],
            "category": metadata["category"],
            "last_opened": self._last_opened(package),
            "tags": metadata["tags"],
            "missing_model_count": missing_model_count,
            "needs_setup": needs_setup,
            "can_remove": self._can_remove_workflow(package),
            "can_export_noofy": True,
            "can_export_comfyui_json": True,
            "publisher_id": package.identity.publisher_id if package.identity else package.metadata.author,
            "package_id": package.identity.package_id if package.identity else package.metadata.id,
            "trust_level": package.identity.trust_level if package.identity else "noofy_verified",
            "trust": workflow_trust_payload(package),
            "source_policy": (
                package.source_policy.model_dump(mode="json")
                if package.source_policy is not None
                else workflow_source_policy(package).model_dump(mode="json")
            ),
            "status": status,
            "status_label": user_facing_status,
            "dashboard_status": package.dashboard.status,
            "dashboard_ready": not _dashboard_needs_setup(package),
            "unresolved_input_count": len(package.unresolved_runtime_inputs),
            "custom_node_count": len(package.custom_nodes),
            "required_model_count": len(package.required_models),
        }

    def _library_metadata(self, package: WorkflowPackage) -> dict[str, object]:
        stored = (
            self.workflow_library_store.metadata(package.metadata.id)
            if self.workflow_library_store is not None
            else None
        )
        display_name = workflow_package_display_name(package, stored)
        description = (
            stored.description
            if stored is not None and stored.description is not None
            else package.metadata.description
        )
        author = (
            stored.author
            if stored is not None and stored.author is not None
            else package.metadata.author
        )
        website = (
            stored.website
            if stored is not None and stored.website is not None
            else package.metadata.website
        )
        category = (
            stored.category
            if stored is not None and stored.category is not None
            else package.metadata.category
        ) or self._infer_workflow_category(package)
        tags = (
            stored.tags
            if stored is not None and stored.tags is not None
            else package.metadata.tags
        )
        icon = (
            stored.icon
            if stored is not None and stored.icon is not None
            else package.metadata.icon
        ) or self._infer_workflow_icon(category)
        return {
            "display_name": display_name,
            "description": description or "",
            "author": author or "",
            "website": website or "",
            "category": category,
            "tags": tags,
            "icon": icon,
        }

    def _run_history_summary(self, package: WorkflowPackage) -> dict[str, object]:
        if self.workflow_library_store is None:
            return {
                "last_run_status": None,
                "last_started_at": None,
                "last_finished_at": None,
                "last_duration_seconds": None,
                "average_duration_seconds": None,
                "last_error": None,
                "run_count": 0,
            }
        return self.workflow_library_store.run_history_summary(package.metadata.id).model_dump(mode="json")

    def _last_opened(self, package: WorkflowPackage) -> str | None:
        if self.workflow_library_store is None:
            return None
        return self.workflow_library_store.workflow_last_opened(package.metadata.id)

    def _source_label(self, package: WorkflowPackage) -> str:
        if package.import_metadata is not None:
            return "Imported"
        if package.identity and package.identity.source == "user_created":
            return "Created by me"
        return "Native Noofy"

    def _main_model_summary(self, package: WorkflowPackage) -> dict[str, object] | None:
        if not package.required_models:
            return {"name": "No model detected", "type": None, "size_bytes": None}
        if len(package.required_models) > 1:
            checkpoint = next(
                (
                    model for model in package.required_models
                    if _is_primary_model_type(model.model_type, model.folder)
                ),
                None,
            )
            selected = checkpoint or max(
                package.required_models,
                key=lambda model: model.size_bytes or 0,
            )
            if selected.size_bytes is None and checkpoint is None:
                return {"name": "Multiple models", "type": None, "size_bytes": None}
        else:
            selected = package.required_models[0]
        return {
            "name": selected.filename,
            "type": selected.model_type or selected.folder,
            "size_bytes": selected.size_bytes,
        }

    def _model_count_summary(self, package: WorkflowPackage) -> dict[str, object]:
        if not package.required_models:
            return {"missing_model_count": 0, "ready_to_run": True}
        try:
            summary = self._summarize_models(package, fast=True)
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Workflow list model summary unavailable",
                "engine.service",
                workflow_id=package.metadata.id,
                details={"error": str(exc)},
            )
            return {
                "missing_model_count": len(package.required_models),
                "ready_to_run": False,
            }
        return {
            "missing_model_count": summary.missing_count + summary.needs_manual_download_count,
            "ready_to_run": summary.ready_to_run,
        }

    def _summarize_models(
        self,
        package: WorkflowPackage,
        *,
        fast: bool = False,
        verify_hashes: bool = False,
        metrics: VerifyHashMetrics | None = None,
    ) -> RequiredModelSummary:
        summarize = self.model_availability_service.summarize
        if not fast:
            return summarize(package)
        try:
            parameters = inspect.signature(summarize).parameters
        except (TypeError, ValueError):
            return summarize(package)
        if "deep_search" not in parameters or "verify_hashes" not in parameters:
            return summarize(package)
        kwargs: dict[str, object] = {"deep_search": False, "verify_hashes": verify_hashes}
        if "metrics" in parameters:
            kwargs["metrics"] = metrics
        return summarize(package, **kwargs)

    async def _run_model_verification_job(self, job_id: str) -> None:
        job = self._model_verification_jobs[job_id]
        package = self.workflow_loader.get_package(job.workflow_id)
        self._begin_model_verification_job(job)
        metrics = VerifyHashMetrics()
        models = list(package.required_models)
        concurrency, downgrade_reason = (
            self.model_availability_service.select_verification_concurrency(len(models))
        )
        log_verification_concurrency(
            self.log_store,
            workflow_id=job.workflow_id,
            model_count=len(models),
            selected_concurrency=concurrency,
            downgrade_reason=downgrade_reason,
        )
        started_at = time.monotonic()
        try:
            await run_parallel_model_verification(
                models,
                verify=lambda model: self._verify_workflow_model(package, model, metrics),
                on_start=lambda index, model: self._record_model_verification_progress(
                    job, index, model
                ),
                on_result=lambda availability: self._record_verified_model(
                    job, package, availability
                ),
                concurrency=concurrency,
            )
            self._finish_model_verification_job(job)
            log_verification_metrics(
                self.log_store,
                workflow_id=job.workflow_id,
                duration_ms=int((time.monotonic() - started_at) * 1000),
                model_count=len(models),
                metrics=metrics,
                selected_concurrency=concurrency,
                downgrade_reason=downgrade_reason,
            )
        except Exception as exc:
            self._fail_model_verification_job(job, exc)

    def _run_model_verification_job_sync(self, job_id: str) -> None:
        job = self._model_verification_jobs[job_id]
        package = self.workflow_loader.get_package(job.workflow_id)
        self._begin_model_verification_job(job)
        try:
            for index, model in enumerate(package.required_models, start=1):
                self._record_model_verification_progress(job, index, model)
                availability = self._verify_workflow_model(package, model)
                self._record_verified_model(job, package, availability)
            self._finish_model_verification_job(job)
        except Exception as exc:
            self._fail_model_verification_job(job, exc)

    def _begin_model_verification_job(self, job: _WorkflowModelVerificationJob) -> None:
        job.status = "running"
        job.user_facing_message = "Verifying local model files..."
        job.updated_at = datetime.now(UTC)
        self.log_store.add(
            "info",
            "Workflow model verification started",
            "workflow.models",
            workflow_id=job.workflow_id,
            details={"job_id": job.job_id, "total_models": job.total_models},
        )

    def _record_model_verification_progress(
        self,
        job: _WorkflowModelVerificationJob,
        index: int,
        model: RequiredModel,
    ) -> None:
        job.current_model_index = index
        job.current_model_filename = model.filename
        job.updated_at = datetime.now(UTC)

    def _record_verified_model(
        self,
        job: _WorkflowModelVerificationJob,
        package: WorkflowPackage,
        availability: RequiredModelAvailability,
    ) -> None:
        if job.models is None:
            job.models = {}
        job.models[availability.requirement_id] = availability
        job.verified_models = len([item for item in job.models.values() if item.status != "checking"])
        job.model_summary = self._model_summary_from_availability(
            package,
            list(job.models.values()),
        )
        job.updated_at = datetime.now(UTC)

    def _finish_model_verification_job(self, job: _WorkflowModelVerificationJob) -> None:
        job.status = "completed"
        job.user_facing_message = "Model verification finished."
        job.current_model_filename = None
        job.current_model_index = None
        job.updated_at = datetime.now(UTC)
        if self._active_model_verification_by_workflow.get(job.workflow_id) == job.job_id:
            self._active_model_verification_by_workflow.pop(job.workflow_id, None)
        self.log_store.add(
            "info",
            "Workflow model verification completed",
            "workflow.models",
            workflow_id=job.workflow_id,
            details={
                "job_id": job.job_id,
                "verified_models": job.verified_models,
                "ready_to_run": job.model_summary.ready_to_run if job.model_summary else None,
            },
        )

    def _fail_model_verification_job(
        self,
        job: _WorkflowModelVerificationJob,
        exc: Exception,
    ) -> None:
        job.status = "failed"
        job.user_facing_message = "Model verification failed. Try again or use a different model file."
        job.updated_at = datetime.now(UTC)
        if self._active_model_verification_by_workflow.get(job.workflow_id) == job.job_id:
            self._active_model_verification_by_workflow.pop(job.workflow_id, None)
        self.log_store.add(
            "warning",
            "Workflow model verification failed",
            "workflow.models",
            workflow_id=job.workflow_id,
            details={"job_id": job.job_id, "error": str(exc)},
        )

    def _verify_workflow_model(
        self,
        package: WorkflowPackage,
        model: RequiredModel,
        metrics: VerifyHashMetrics | None = None,
    ) -> RequiredModelAvailability:
        single_model_package = package.model_copy(update={"required_models": [model]})
        # ``metrics`` is only forwarded when present so the call stays compatible with
        # availability services (e.g. test stubs) whose ``summarize`` predates the kwarg.
        kwargs: dict[str, object] = {"deep_search": True, "verify_hashes": True}
        if metrics is not None:
            kwargs["metrics"] = metrics
        return self.model_availability_service.summarize(
            single_model_package,
            **kwargs,
        ).models[0]

    def _checking_model_summary(self, package: WorkflowPackage) -> RequiredModelSummary:
        current = self._summarize_models(package, fast=True, verify_hashes=False)
        return self._model_summary_from_availability(
            package,
            [
                model.model_copy(
                    update={
                        "status": "checking",
                        "status_label": "Checking",
                        "message": "Verifying local model...",
                    }
                )
                if model.status == "possible_match"
                else model
                for model in current.models
            ],
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
            ready_to_run=len(models) == available_count,
            models=models,
        )

    def _model_verification_job_status(
        self,
        job: _WorkflowModelVerificationJob,
    ) -> WorkflowModelVerificationJobStatus:
        percent = None
        if job.total_models:
            percent = min(100.0, (job.verified_models / job.total_models) * 100)
        return WorkflowModelVerificationJobStatus(
            job_id=job.job_id,
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

    def _infer_workflow_category(self, package: WorkflowPackage) -> str:
        name = f"{workflow_package_display_name(package)} {package.metadata.description}".casefold()
        combined = f"{name} {self._graph_keyword_text(package.comfyui_graph)}"
        if "upscale" in combined or "esrgan" in combined:
            return "Upscaling"
        if "inpaint" in combined:
            return "Inpainting"
        if "outpaint" in combined:
            return "Outpainting"
        if "canny" in combined or "lineart" in combined:
            return "Canny / Line Control"
        if "depth" in combined:
            return "Depth Control"
        if "pose" in combined or "openpose" in combined:
            return "Pose Control"
        if "background" in combined and "remove" in combined:
            return "Background Removal"
        if "background" in combined:
            return "Background Replacement"
        if "restore" in combined or "restoration" in combined:
            return "Restoration"
        if any(input_def.control.startswith("load_image") for input_def in package.inputs):
            return "Img2img"
        return "Txt2img"

    def _graph_keyword_text(self, graph: dict[str, Any]) -> str:
        parts: list[str] = []
        for node in graph.values():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type")
            if isinstance(class_type, str):
                parts.append(class_type)
            title = node.get("_meta", {}).get("title") if isinstance(node.get("_meta"), dict) else None
            if isinstance(title, str):
                parts.append(title)
        return " ".join(parts).casefold()

    def _infer_workflow_icon(self, category: str) -> str:
        if category in {"Upscaling", "Restoration"}:
            return "maximize"
        if "Control" in category:
            return "sliders"
        if "Background" in category:
            return "image"
        return "sparkles"

    def _can_remove_workflow(self, package: WorkflowPackage) -> bool:
        return package.import_metadata is not None and self._mutable_package_dir(package) is not None

    def _mutable_package_dir(self, package: WorkflowPackage) -> Path | None:
        if package.identity is None:
            return None
        root_dir = (
            self.imported_package_store.root_dir
            if self.imported_package_store is not None
            else settings.paths.workflow_packages_store_dir
        )
        candidate = mutable_package_dir(root_dir, package)
        if candidate is not None and candidate.exists():
            return candidate
        return None

    def _update_internal_package_metadata(
        self,
        package_dir: Path,
        update: WorkflowMetadataUpdate,
    ) -> None:
        package_file = package_dir / "package.json"
        if not package_file.exists():
            return
        root_dir = (
            self.imported_package_store.root_dir
            if self.imported_package_store is not None
            else settings.paths.workflow_packages_store_dir
        )
        assert_path_within(root_dir, package_file, purpose="update workflow metadata")
        data = json.loads(package_file.read_text(encoding="utf-8"))
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        patch = update.model_dump(mode="json", exclude_unset=True)
        for key, value in patch.items():
            if value is None:
                continue
            if key == "display_name":
                if not isinstance(value, str):
                    continue
                value = value.strip()
                if not value:
                    raise ValueError("Workflow name cannot be empty.")
                metadata["display_name"] = value
                metadata["name"] = value
                data["display_name"] = value
                continue
            if isinstance(value, str):
                value = value.strip()
            metadata[key] = value
            if key == "description":
                data["description"] = value
            elif key == "author":
                data["author"] = value
            elif key == "website":
                data["website"] = value
            elif key == "category":
                data["category"] = value
            elif key == "tags":
                data["tags"] = value
            elif key == "icon":
                data["icon"] = value
        data["metadata"] = metadata
        tmp = package_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(package_file)


def _is_primary_model_type(model_type: str | None, folder: str | None) -> bool:
    value = f"{model_type or ''} {folder or ''}".casefold()
    return any(token in value for token in ("checkpoint", "diffusion", "unet", "ckpt"))


def _dashboard_needs_setup(package: WorkflowPackage) -> bool:
    return (
        package.dashboard.status != "configured"
        or bool(package.unresolved_runtime_inputs)
        or not any(section.controls for section in package.dashboard.sections)
    )


def _effective_workflow_status(package: WorkflowPackage) -> str:
    raw_status = package.import_metadata.status if package.import_metadata else "installed"
    if raw_status == "needs_input_setup" and not _dashboard_needs_setup(package):
        return "imported"
    return raw_status


def _effective_workflow_status_label(package: WorkflowPackage, status: str) -> str:
    if status == "imported":
        return "Imported"
    if status == "needs_input_setup":
        return "Needs input setup"
    if package.import_metadata is not None:
        return package.import_metadata.user_facing_message
    return "Installed"
