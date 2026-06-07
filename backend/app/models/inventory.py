from __future__ import annotations

import asyncio
import shutil
import stat
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from app.diagnostics import DiagnosticsSink
from app.engine.models import ModelInfo
from app.models.downloads import ModelDownloadJobService
from app.models.imports import ModelImportService
from app.models.schemas import (
    ModelDeleteResponse,
    ModelDownloadActiveResponse,
    ModelDownloadJobStart,
    ModelDownloadJobStatus,
    ModelDownloadReference,
    ModelDownloadSelection,
    ModelDownloadStartRequest,
    ModelImportItemResult,
    ModelImportRequest,
    ModelImportResponse,
    ModelInventoryEntry,
    ModelInventoryFolders,
    ModelInventoryResponse,
    ModelInventorySource,
    ModelInventoryStatus,
    ModelInventorySummary,
    ModelOwnership,
    ModelTag,
    ModelTagAssignmentRequest,
    ModelTagCreateRequest,
    ModelWorkflowReference,
)
from app.models.ownership import ModelOwnershipStore
from app.models.paths import ensure_inside, model_key, parse_model_key
from app.models.tags import ModelTagStore
from app.models.folders import COMFYUI_MODEL_CATEGORIES, ModelFolderSettingsService
from app.workflows.library import workflow_package_display_name


MODEL_ASSET_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".h5",
    ".msgpack",
    ".onnx",
    ".pb",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
    ".safetensors",
    ".tflite",
}
ENGINE_VISIBLE_MODELS_TIMEOUT_SECONDS = 1.5


class ModelInventoryService:
    def __init__(
        self,
        *,
        engine_service: object,
        model_folder_service: ModelFolderSettingsService,
        tag_store: ModelTagStore,
        ownership_store: ModelOwnershipStore,
        log_store: DiagnosticsSink | None = None,
        engine_visible_models_timeout_seconds: float = ENGINE_VISIBLE_MODELS_TIMEOUT_SECONDS,
        excluded_engine_model_roots: list[Path] | None = None,
    ) -> None:
        self.engine_service = engine_service
        self.model_folder_service = model_folder_service
        self.tag_store = tag_store
        self.ownership_store = ownership_store
        self.engine_visible_models_timeout_seconds = max(
            0.0,
            engine_visible_models_timeout_seconds,
        )
        self.excluded_engine_model_roots = [
            root.expanduser().resolve(strict=False)
            for root in excluded_engine_model_roots or []
        ]
        self.import_service = ModelImportService(
            model_folder_service=model_folder_service,
            ownership_store=ownership_store,
            log_store=log_store,
        )
        self.log_store = log_store

    async def inventory(self) -> ModelInventoryResponse:
        folder_settings = self.model_folder_service.settings(ensure_folders=True)
        noofy_root = Path(folder_settings.noofy_models_dir).expanduser()
        external_root = (
            Path(folder_settings.external_comfyui_models_dir).expanduser()
            if folder_settings.external_comfyui_models_dir
            else None
        )
        categories = folder_settings.categories
        entries: dict[str, ModelInventoryEntry] = {}

        self._add_filesystem_root(entries, root=noofy_root, source="noofy", label="Noofy Models", categories=categories)
        if external_root is not None:
            self._add_filesystem_root(
                entries,
                root=external_root,
                source="external_comfyui",
                label="ComfyUI models folder",
                categories=categories,
            )

        await self._add_engine_visible_models(entries)
        self._add_workflow_requirements(entries)

        tags = self.tag_store.list_tags()
        for entry in entries.values():
            entry.tag_ids = self.tag_store.tag_ids_for_model(entry.model_key)

        models = sorted(entries.values(), key=lambda item: (item.status != "missing", item.folder, item.filename.casefold()))
        return ModelInventoryResponse(
            summary=ModelInventorySummary(
                total_count=len(models),
                noofy_count=sum(model.source == "noofy" for model in models),
                external_comfyui_count=sum(model.source == "external_comfyui" for model in models),
                missing_count=sum(model.status == "missing" for model in models),
                total_known_size_bytes=sum(model.size_bytes or 0 for model in models),
                cleanable_size_bytes=sum(model.size_bytes or 0 for model in models if _is_cleanable(model)),
                disk_free_bytes=self._disk_free_bytes(noofy_root),
            ),
            folders=ModelInventoryFolders(
                noofy_models_dir=str(noofy_root),
                external_comfyui_models_dir=str(external_root) if external_root else None,
                categories=categories,
            ),
            tags=tags,
            models=models,
        )

    def import_models(self, request: ModelImportRequest) -> ModelImportResponse:
        return self.import_service.import_models(request)

    def delete_model(self, model_key_value: str) -> ModelDeleteResponse:
        folder, filename = parse_model_key(model_key_value)
        folder_settings = self.model_folder_service.settings(ensure_folders=True)
        if folder not in folder_settings.categories:
            raise ValueError("Only supported Noofy model folders can be managed.")
        noofy_root = Path(folder_settings.noofy_models_dir).expanduser()
        external_root = (
            Path(folder_settings.external_comfyui_models_dir).expanduser()
            if folder_settings.external_comfyui_models_dir
            else None
        )
        target_path = noofy_root / folder / filename
        target_source = "noofy"
        ensure_inside(target_path, noofy_root)
        if target_path.exists():
            origin = self.ownership_store.origin_for_model(model_key_value)
            if origin not in {"downloaded", "imported"}:
                raise ValueError("Noofy can delete only models it imported or downloaded.")
        elif external_root is not None:
            target_path = external_root / folder / filename
            target_source = "external_comfyui"
            ensure_inside(target_path, external_root)
            if not target_path.exists():
                raise FileNotFoundError("This model is not in a configured model folder.")
        else:
            raise FileNotFoundError("This model is not in a configured model folder.")
        if not target_path.is_file():
            raise ValueError("Only model files can be deleted.")
        target_path.unlink()
        self.tag_store.clear_model_tags(model_key_value)
        if target_source == "noofy":
            self.ownership_store.forget_model(model_key_value)
        if self.log_store is not None:
            self.log_store.add(
                "info",
                "Noofy model file deleted",
                "models.inventory",
                details={"model_key": model_key_value, "folder": folder, "source": target_source},
            )
        label = "Noofy Models" if target_source == "noofy" else "ComfyUI models folder"
        return ModelDeleteResponse(model_key=model_key_value, deleted=True, message=f"Model file deleted from {label}.")

    def _disk_free_bytes(self, root: Path) -> int | None:
        """Free space on the disk that holds the Noofy models folder.

        Walks up to the nearest existing ancestor so a not-yet-created folder
        still reports the underlying volume's free space. Returns ``None`` when
        the disk cannot be inspected.
        """
        probe = root
        while not probe.exists():
            if probe.parent == probe:
                return None
            probe = probe.parent
        try:
            return shutil.disk_usage(probe).free
        except OSError:
            if self.log_store is not None:
                self.log_store.add(
                    "warning",
                    "Unable to read free disk space for Noofy models folder",
                    "models.inventory",
                    details={"path": str(root)},
                )
            return None

    def _add_filesystem_root(
        self,
        entries: dict[str, ModelInventoryEntry],
        *,
        root: Path,
        source: ModelInventorySource,
        label: str,
        categories: list[str],
    ) -> None:
        if not root.exists():
            return
        for folder in categories:
            folder_path = root / folder
            if not folder_path.is_dir():
                continue
            for file_path in sorted(path for path in folder_path.rglob("*") if path.is_file()):
                if self._should_ignore_file(file_path):
                    continue
                try:
                    relative_filename = file_path.relative_to(folder_path).as_posix()
                except ValueError:
                    relative_filename = file_path.name
                key = model_key(folder, relative_filename)
                if key in entries and entries[key].source == "noofy":
                    continue
                try:
                    size_bytes = file_path.stat().st_size
                except OSError:
                    size_bytes = None
                ownership, ownership_label = self._ownership_for_file(key, source)
                can_delete = (
                    (source == "noofy" and ownership in {"noofy_downloaded", "noofy_imported"})
                    or source == "external_comfyui"
                ) and self._path_is_inside_root(file_path, root)
                entries[key] = ModelInventoryEntry(
                    model_key=key,
                    filename=relative_filename,
                    folder=folder,
                    model_type=_model_type_for(folder, None),
                    size_bytes=size_bytes,
                    status="ready",
                    status_label="Ready",
                    source=source,
                    source_label=label,
                    ownership=ownership,
                    ownership_label=ownership_label,
                    can_delete=can_delete,
                    delete_unavailable_reason=None if can_delete else _delete_unavailable_reason(source, ownership),
                    path=str(file_path),
                    matched_root=str(root),
                )

    async def _add_engine_visible_models(self, entries: dict[str, ModelInventoryEntry]) -> None:
        list_available = getattr(self.engine_service, "list_available_models", None)
        if not callable(list_available):
            return
        list_available_models = cast(Callable[[], Awaitable[list[object]]], list_available)
        try:
            models = await asyncio.wait_for(
                list_available_models(),
                timeout=self.engine_visible_models_timeout_seconds,
            )
        except TimeoutError:
            if self.log_store is not None:
                self.log_store.add(
                    "warning",
                    "Skipped slow engine-visible model enrichment",
                    "models.inventory",
                    details={
                        "timeout_seconds": self.engine_visible_models_timeout_seconds,
                    },
                )
            return
        except Exception:
            if self.log_store is not None:
                self.log_store.add(
                    "warning",
                    "Skipped unavailable engine-visible model enrichment",
                    "models.inventory",
                )
            return
        for model in models:
            if not isinstance(model, ModelInfo):
                try:
                    model = ModelInfo.model_validate(model)
                except Exception:
                    continue
            key = model_key(model.folder, model.filename)
            if key in entries:
                continue
            if not _is_model_asset_filename(model.filename):
                continue
            engine_visible_file = self._engine_visible_model_file(model)
            if engine_visible_file is None:
                continue
            path, size_bytes = engine_visible_file
            entries[key] = ModelInventoryEntry(
                model_key=key,
                filename=model.filename,
                folder=model.folder,
                model_type=_model_type_for(model.folder, None),
                size_bytes=size_bytes,
                status="ready",
                status_label="Ready",
                source="engine_visible",
                source_label="Visible to engine",
                ownership="engine_reference",
                ownership_label="Engine-visible reference",
                can_delete=False,
                delete_unavailable_reason="Only files inside Noofy Models can be deleted.",
                path=str(path),
            )

    def _engine_visible_model_file(self, model: ModelInfo) -> tuple[Path, int] | None:
        if not model.path:
            return None
        path = Path(model.path).expanduser()
        if not path.is_absolute():
            return None
        if self._is_excluded_engine_model_path(path):
            return None
        try:
            path_stat = path.stat()
        except OSError:
            return None
        if not stat.S_ISREG(path_stat.st_mode):
            return None
        return path, path_stat.st_size

    def _is_excluded_engine_model_path(self, path: Path) -> bool:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            return False
        return any(
            resolved == root or resolved.is_relative_to(root)
            for root in self.excluded_engine_model_roots
        )

    def _add_workflow_requirements(self, entries: dict[str, ModelInventoryEntry]) -> None:
        workflow_loader = getattr(self.engine_service, "workflow_loader", None)
        availability_service = getattr(self.engine_service, "model_availability_service", None)
        if workflow_loader is None or availability_service is None:
            return
        for package in _global_inventory_relevant_workflow_packages(workflow_loader):
            try:
                summary = availability_service.summarize(
                    package,
                    deep_search=False,
                    verify_hashes=False,
                )
            except Exception:
                continue
            for item in summary.models:
                key = model_key(item.folder, item.filename)
                workflow_ref = ModelWorkflowReference(
                    workflow_id=package.metadata.id,
                    workflow_name=workflow_package_display_name(package),
                    requirement_id=item.requirement_id,
                    status=item.status,
                    status_label=item.status_label,
                )
                existing = entries.get(key)
                if (
                    existing is not None
                    and existing.source == "engine_visible"
                    and item.status != "available"
                ):
                    existing = None
                if existing is None:
                    inventory_status = _inventory_status_for_required_status(item.status)
                    never_used = inventory_status == "never_used"
                    existing = ModelInventoryEntry(
                        model_key=key,
                        filename=item.filename,
                        folder=item.folder,
                        model_type=_model_type_for(item.folder, item.model_type),
                        size_bytes=item.size_bytes,
                        status=inventory_status,
                        status_label="Never used" if never_used else item.status_label,
                        source="required_by_workflow",
                        source_label="Required by workflow",
                        ownership="workflow_requirement",
                        ownership_label="Workflow requirement",
                        can_delete=False,
                        delete_unavailable_reason="Missing workflow requirements are not files.",
                        path=item.source_path,
                        matched_root=item.matched_root,
                        verification_level=str(item.verification_level),
                        matched_sha256=item.matched_sha256,
                        source_availability=item.source_availability,
                        message=_NEVER_USED_MESSAGE if never_used else item.message,
                    )
                    entries[key] = existing
                else:
                    if item.status == "possible_match" and existing.status == "ready":
                        # A local file the user already has is referenced by a
                        # workflow but not yet hash-verified. This is benign, so
                        # present it as a neutral "Never used" state rather than a
                        # red "Needs attention" warning that implies user action.
                        existing.status = "never_used"
                        existing.status_label = "Never used"
                        existing.message = _NEVER_USED_MESSAGE
                    if existing.size_bytes is None:
                        existing.size_bytes = item.size_bytes
                    if existing.verification_level is None:
                        existing.verification_level = str(item.verification_level)
                    if existing.source_availability is None:
                        existing.source_availability = item.source_availability
                    if existing.matched_sha256 is None:
                        existing.matched_sha256 = item.matched_sha256
                    if existing.message is None:
                        existing.message = item.message

                existing.workflow_usage.append(workflow_ref)
                if item.status == "missing":
                    existing.downloadable_references.append(
                        ModelDownloadReference(
                            workflow_id=package.metadata.id,
                            workflow_name=workflow_package_display_name(package),
                            requirement_id=item.requirement_id,
                        )
                    )

    def _ownership_for_file(self, key: str, source: ModelInventorySource) -> tuple[ModelOwnership, str]:
        if source == "noofy":
            origin = self.ownership_store.origin_for_model(key)
            if origin == "downloaded":
                return "noofy_downloaded", "Downloaded by Noofy"
            if origin == "imported":
                return "noofy_imported", "Imported into Noofy"
            return "noofy_local", "In Noofy Models"
        return "external_reference", "External reference"

    def _path_is_inside_root(self, path: Path, root: Path) -> bool:
        try:
            ensure_inside(path, root)
        except ValueError:
            return False
        return True

    @staticmethod
    def _should_ignore_file(file_path: Path) -> bool:
        if ".downloads" in file_path.parts or ".imports" in file_path.parts:
            return True
        if file_path.name.startswith("put_"):
            return True
        if not _is_model_asset_filename(file_path.name):
            return True
        return ".tmp-" in file_path.name


def _is_cleanable(model: ModelInventoryEntry) -> bool:
    """A model whose storage Noofy can safely reclaim.

    Only counts Noofy-owned files that Noofy downloaded or imported (so they are
    safe to delete) and that no installed workflow currently uses. External
    ComfyUI folder models and user-owned local files are never counted because
    Noofy must not delete them.
    """
    return (
        model.source == "noofy"
        and model.can_delete
        and model.ownership in {"noofy_downloaded", "noofy_imported"}
        and not model.workflow_usage
    )


def _is_model_asset_filename(filename: str) -> bool:
    return Path(filename).suffix.casefold() in MODEL_ASSET_SUFFIXES


def _model_type_for(folder: str, model_type: str | None) -> str:
    if model_type:
        return model_type
    if folder in {"checkpoints", "diffusion_models", "unet"}:
        return "checkpoint"
    if folder == "loras":
        return "lora"
    if folder == "upscale_models":
        return "upscaler"
    if folder == "embeddings":
        return "embedding"
    return folder if folder in COMFYUI_MODEL_CATEGORIES else "other"


_NEVER_USED_MESSAGE = (
    "This model is saved on your computer but hasn't been used in a workflow yet. "
    "Noofy will check it automatically the first time a workflow needs it."
)


def _inventory_status_for_required_status(status: str) -> ModelInventoryStatus:
    if status == "available":
        return "ready"
    if status == "missing":
        return "missing"
    if status == "possible_match":
        # A local file matches by name but Noofy has not hash-verified it yet.
        # Nothing is wrong and no user action is needed: verification happens
        # automatically the first time a workflow that needs it is opened.
        return "never_used"
    return "needs_attention"


def _delete_unavailable_reason(source: ModelInventorySource, ownership: ModelOwnership) -> str:
    if source == "noofy" and ownership == "noofy_local":
        return "Only models imported or downloaded by Noofy can be deleted."
    return "Only files inside Noofy Models or the configured ComfyUI models folder can be deleted."


def _global_inventory_relevant_workflow_packages(workflow_loader: object) -> list[object]:
    list_with_sources = getattr(workflow_loader, "list_packages_with_sources", None)
    if callable(list_with_sources):
        return [
            package
            for package, source in list_with_sources()
            if source in {"user", "imported"} or getattr(package, "import_metadata", None) is not None
        ]
    return []


__all__ = [
    "ModelDeleteResponse",
    "ModelDownloadActiveResponse",
    "ModelDownloadJobService",
    "ModelDownloadJobStart",
    "ModelDownloadJobStatus",
    "ModelDownloadReference",
    "ModelDownloadSelection",
    "ModelDownloadStartRequest",
    "ModelImportItemResult",
    "ModelImportRequest",
    "ModelImportResponse",
    "ModelInventoryEntry",
    "ModelInventoryFolders",
    "ModelInventoryResponse",
    "ModelInventoryService",
    "ModelInventorySource",
    "ModelInventoryStatus",
    "ModelInventorySummary",
    "ModelOwnership",
    "ModelOwnershipStore",
    "ModelTag",
    "ModelTagAssignmentRequest",
    "ModelTagCreateRequest",
    "ModelTagStore",
    "ModelWorkflowReference",
    "model_key",
]
