import inspect
import json
import shutil
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.diagnostics import DiagnosticsSink
from app.engine.models import RequiredModelSummary
from app.history import HistoryService
from app.trust import workflow_source_policy, workflow_trust_payload
from app.workflows.exporter import stored_comfyui_graph_file
from app.workflows.importer import ImportedWorkflowPackageStore
from app.workflows.library import WorkflowLibraryStore, WorkflowMetadataUpdate
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.model_availability import ModelAvailabilityService
from app.workflows.package import WorkflowPackage
from app.workflows.store_paths import (
    assert_path_within,
    mutable_package_dir,
    safe_store_segment,
)


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

    def list_workflows(self) -> list[dict[str, object]]:
        return [
            self.workflow_summary(package)
            for package in self.workflow_loader.list_packages()
        ]

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
                "description": metadata["description"],
                "author": metadata["author"],
                "website": metadata["website"],
                "source": summary["source_label"],
                "version": package.metadata.version,
            },
            "models_used": models,
            "run_history": self._run_history_summary(package),
            "organization": {
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
        return self.workflow_loader.get_package(workflow_id).model_dump()

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

    def model_availability_summary_for_package(
        self,
        package: WorkflowPackage,
        *,
        fast: bool = False,
        verify_hashes: bool = False,
    ) -> RequiredModelSummary:
        return self._summarize_models(
            package,
            fast=fast,
            verify_hashes=verify_hashes,
        )

    def workflow_summary(self, package: WorkflowPackage) -> dict[str, object]:
        status = _effective_workflow_status(package)
        user_facing_status = _effective_workflow_status_label(package, status)
        metadata = self._library_metadata(package)
        history = self._run_history_summary(package)
        model_counts = self._model_count_summary(package)
        missing_model_count = model_counts["missing_model_count"]
        needs_setup = _dashboard_needs_setup(package) or status in {
            "cannot_prepare_automatically",
            "blocked_by_policy",
            "unsupported",
        }
        return {
            "id": package.metadata.id,
            "name": package.metadata.name,
            "version": package.metadata.version,
            "icon": metadata["icon"],
            "source_label": self._source_label(package),
            "main_model": self._main_model_summary(package),
            "description": metadata["description"],
            "category": metadata["category"],
            "last_opened": history["last_started_at"],
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
        return summarize(package, deep_search=False, verify_hashes=verify_hashes)

    def _infer_workflow_category(self, package: WorkflowPackage) -> str:
        name = f"{package.metadata.name} {package.metadata.description}".casefold()
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
