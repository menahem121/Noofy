from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from app.engine.diagnostics import DiagnosticsSink
from app.model_inventory_schemas import ModelImportItemResult, ModelImportRequest, ModelImportResponse
from app.model_ownership import ModelOwnershipStore
from app.model_paths import ensure_inside, model_key
from app.settings.model_folders import ModelFolderSettingsService


class ModelImportService:
    def __init__(
        self,
        *,
        model_folder_service: ModelFolderSettingsService,
        ownership_store: ModelOwnershipStore,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.model_folder_service = model_folder_service
        self.ownership_store = ownership_store
        self.log_store = log_store

    def import_models(self, request: ModelImportRequest) -> ModelImportResponse:
        folder_settings = self.model_folder_service.settings(ensure_folders=True)
        categories = set(folder_settings.categories)
        if request.folder not in categories:
            raise ValueError(f"Unsupported model folder: {request.folder}")
        if not request.source_paths:
            raise ValueError("At least one source path is required.")

        noofy_root = Path(folder_settings.noofy_models_dir).expanduser()
        target_dir = noofy_root / request.folder
        ensure_inside(target_dir, noofy_root)
        target_dir.mkdir(parents=True, exist_ok=True)

        results: list[ModelImportItemResult] = []
        for source_value in request.source_paths:
            source_path = Path(source_value).expanduser()
            try:
                result = self._copy_model_file(
                    source_path=source_path,
                    target_dir=target_dir,
                    noofy_root=noofy_root,
                    folder=request.folder,
                    overwrite=request.overwrite,
                )
            except Exception as exc:
                result = ModelImportItemResult(
                    source_path=str(source_path),
                    filename=source_path.name or None,
                    status="failed",
                    message=str(exc),
                )
            results.append(result)

        imported_count = sum(item.status in {"imported", "already_in_place"} for item in results)
        failed_count = sum(item.status == "failed" for item in results)
        if self.log_store is not None:
            self.log_store.add(
                "info" if failed_count == 0 else "warning",
                "Model import finished",
                "models.inventory",
                details={
                    "folder": request.folder,
                    "imported_count": imported_count,
                    "failed_count": failed_count,
                },
            )
        return ModelImportResponse(
            status="completed_with_errors" if failed_count else "completed",
            imported_count=imported_count,
            failed_count=failed_count,
            models=results,
        )

    def _copy_model_file(
        self,
        *,
        source_path: Path,
        target_dir: Path,
        noofy_root: Path,
        folder: str,
        overwrite: bool,
    ) -> ModelImportItemResult:
        if not source_path.is_file():
            raise ValueError("Source file does not exist.")
        target_path = target_dir / source_path.name
        ensure_inside(target_path, noofy_root)
        if source_path.resolve(strict=True) == target_path.resolve(strict=False):
            self.ownership_store.mark_imported(model_key(folder, source_path.name))
            return ModelImportItemResult(
                source_path=str(source_path),
                filename=source_path.name,
                target_path=str(target_path),
                status="already_in_place",
                message="File is already in the Noofy Models folder.",
            )
        if target_path.exists():
            if target_path.is_dir():
                raise ValueError("A folder with this model filename already exists.")
            if not overwrite:
                raise ValueError("A model with this filename already exists in the selected folder.")

        transaction_dir = noofy_root / ".imports" / uuid.uuid4().hex
        ensure_inside(transaction_dir, noofy_root)
        transaction_dir.mkdir(parents=True, exist_ok=False)
        tmp_path = transaction_dir / source_path.name
        try:
            shutil.copy2(source_path, tmp_path)
            tmp_path.replace(target_path)
        finally:
            shutil.rmtree(transaction_dir, ignore_errors=True)

        self.ownership_store.mark_imported(model_key(folder, source_path.name))
        return ModelImportItemResult(
            source_path=str(source_path),
            filename=source_path.name,
            target_path=str(target_path),
            status="imported",
        )
