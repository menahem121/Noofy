"""Content-addressed model store with transactional downloads.

Models are addressed by sha256 in `<model_store>/blobs/sha256/<hash>/blob`
and exposed to runners through `<materialized>/<comfyui_folder>/<filename>`
links. A failed download or a hash mismatch must never leave a partial blob,
ref, or materialized link behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Protocol
from urllib.parse import urlparse

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.diagnostics import DiagnosticsSink
from app.runtime.fingerprints import sha256_fingerprint
from app.runtime.dependencies.isolation import InstalledModelReference, ModelLock
from app.source_policy import ModelSourceTrust, SourcePolicy

REF_SCHEMA_VERSION = "0.1.0"
MODEL_VIEW_SCHEMA_VERSION = "0.1.0"
WINDOWS_MAX_MATERIALIZED_PATH_CHARS = 240


async def http_streaming_downloader(url: str, dest: Path) -> int:
    """Default downloader that streams `url` to `dest` via httpx.

    Imported lazily so that tests which inject their own downloader do not
    pay the import cost or require a network stack.
    """
    import httpx

    bytes_written = 0
    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            with dest.open("wb") as file:
                async for chunk in response.aiter_bytes(chunk_size=1 << 20):
                    file.write(chunk)
                    bytes_written += len(chunk)
    return bytes_written


class ModelDownloadError(RuntimeError):
    """Raised when a download cannot complete or its hash does not match."""


class LocalModelCandidateError(ModelDownloadError):
    """Raised when a filename+size local model candidate cannot be reused."""


class ModelSourcePolicyError(ModelDownloadError):
    """Raised when model materialization is blocked by source policy."""


class AsyncDownloader(Protocol):
    async def download(self, url: str, *, dest: Path) -> int:
        """Stream `url` to `dest` and return total bytes written."""


DownloadFn = Callable[[str, Path], Awaitable[int]]


@dataclass(frozen=True)
class ModelMaterialization:
    model_id: str
    sha256: str
    blob_path: Path
    materialized_path: Path
    size_bytes: int
    reused_existing_blob: bool
    materialization_strategy: str = "symlink"
    materialized_file_verified: bool = True


@dataclass(frozen=True)
class ModelViewMaterialization:
    view_fingerprint: str
    view_path: Path
    model_references: list[InstalledModelReference]
    final_view_path: Path | None = None

    @property
    def is_staged(self) -> bool:
        return (
            self.final_view_path is not None and self.view_path != self.final_view_path
        )


@dataclass(frozen=True)
class LocalModelRequirement:
    requirement_id: str
    comfyui_folder: str
    filename: str
    size_bytes: int


@dataclass(frozen=True)
class ResolvedLocalModel:
    requirement: LocalModelRequirement
    source_path: Path
    sha256: str


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_sha256(value: str) -> str:
    """Accept both `sha256:<hex>` and bare `<hex>` and return the hex part."""
    if value.startswith("sha256:"):
        return value.split(":", 1)[1]
    return value


def _safe_relative_parts(value: str, *, field_name: str) -> tuple[str, ...]:
    if "\\" in value:
        raise ModelDownloadError(f"Unsafe {field_name}: path traversal is not allowed")
    path = Path(value)
    if path.is_absolute():
        raise ModelDownloadError(f"Unsafe {field_name}: absolute paths are not allowed")
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ModelDownloadError(f"Unsafe {field_name}: path traversal is not allowed")
    return parts


class ModelStore:
    """Persist model blobs by content hash and project them into the model view.

    The store guarantees:
    - A blob lives at `<blobs>/sha256/<hash>/blob` only after its bytes have
      been downloaded and verified.
    - A ref json at `<refs>/<id>.json` is written only after the blob exists.
    - A materialized link/copy at `<materialized>/<folder>/<filename>` is
      created only after the ref exists.
    - On any failure during download or verification, the transaction
      directory and any half-written artifacts for that model are removed.
    """

    def __init__(
        self,
        *,
        blobs_dir: Path,
        refs_dir: Path,
        materialized_dir: Path,
        transactions_dir: Path,
        log_store: DiagnosticsSink,
        downloader: DownloadFn | None = None,
        local_model_roots: list[Path] | None = None,
        owned_model_root: Path | None = None,
        symlink_capability: bool | None = None,
    ) -> None:
        self.blobs_dir = blobs_dir
        self.refs_dir = refs_dir
        self.materialized_dir = materialized_dir
        self.transactions_dir = transactions_dir
        self.log_store = log_store
        self._downloader = downloader
        self.local_model_roots = local_model_roots or []
        self.owned_model_root = owned_model_root
        self._symlink_capability = symlink_capability
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def materialize(self, model_lock: ModelLock) -> ModelMaterialization:
        """Ensure `model_lock` is downloaded, verified, and materialized.

        Idempotent: if the blob and the materialized link already exist, no
        network IO happens and `reused_existing_blob` is True.
        """
        sha256 = _normalize_sha256(model_lock.sha256)
        blob_path = self._blob_path(sha256)
        self._materialized_path(
            model_lock
        )  # Validate lock-derived output path before IO.

        if blob_path.exists():
            self._verify_existing_blob(model_lock, blob_path, sha256)
            self._write_ref(model_lock, sha256, blob_path)
            self._materialize_owned_model(model_lock, blob_path)
            materialized, strategy = self._materialize(model_lock, blob_path)
            self.log_store.add(
                "info",
                "Model already present in store",
                "model.store",
                details={"model_id": model_lock.id, "sha256": sha256},
            )
            return ModelMaterialization(
                model_id=model_lock.id,
                sha256=sha256,
                blob_path=blob_path,
                materialized_path=materialized,
                size_bytes=blob_path.stat().st_size,
                reused_existing_blob=True,
                materialization_strategy=strategy,
            )

        if not model_lock.source_urls:
            raise ModelDownloadError(
                f"No source URLs available to download model {model_lock.id}"
            )

        txn_dir = self._open_transaction(model_lock.id)
        download_target = txn_dir / "blob"
        try:
            bytes_written = await self._download_with_fallback(
                model_lock.source_urls, download_target
            )
            if bytes_written != model_lock.size_bytes and model_lock.size_bytes > 0:
                raise ModelDownloadError(
                    f"Size mismatch for {model_lock.id}: "
                    f"expected {model_lock.size_bytes}, got {bytes_written}"
                )

            actual_sha256 = _sha256_file(download_target)
            if actual_sha256 != sha256:
                raise ModelDownloadError(
                    f"Hash mismatch for {model_lock.id}: "
                    f"expected {sha256}, got {actual_sha256}"
                )

            self._commit_blob(download_target, blob_path)
            self._write_ref(model_lock, sha256, blob_path)
            self._materialize_owned_model(model_lock, blob_path)
            materialized, strategy = self._materialize(model_lock, blob_path)
        except BaseException as exc:
            self.log_store.add(
                "error",
                "Model download failed",
                "model.store",
                details={
                    "model_id": model_lock.id,
                    "sha256": sha256,
                    "error": str(exc),
                },
            )
            raise
        finally:
            self._cleanup_transaction(txn_dir)

        self.log_store.add(
            "info",
            "Model downloaded and verified",
            "model.store",
            details={
                "model_id": model_lock.id,
                "sha256": sha256,
                "size_bytes": bytes_written,
            },
        )
        return ModelMaterialization(
            model_id=model_lock.id,
            sha256=sha256,
            blob_path=blob_path,
            materialized_path=materialized,
            size_bytes=bytes_written,
            reused_existing_blob=False,
            materialization_strategy=strategy,
        )

    async def materialize_model_view(
        self,
        *,
        view_id: str,
        model_locks: list[ModelLock],
        local_model_requirements: list[LocalModelRequirement] | None = None,
        staged_views_dir: Path | None = None,
        staged_blobs_dir: Path | None = None,
        source_policy: SourcePolicy | None = None,
    ) -> ModelViewMaterialization:
        """Create a per-view ComfyUI model tree from content-addressed blobs."""
        self._validate_model_source_policy(
            source_policy,
            model_locks=model_locks,
            local_model_requirements=local_model_requirements or [],
        )
        resolved_local_models = self._resolve_local_models(
            local_model_requirements or []
        )
        view_fingerprint = model_view_fingerprint(
            view_id=view_id,
            model_locks=model_locks,
            local_models=resolved_local_models,
        )
        final_view_path = self._model_view_dir(view_fingerprint)
        view_path = final_view_path
        if staged_views_dir is not None:
            view_path = (
                staged_views_dir / f"model-view-{_safe_fingerprint(view_fingerprint)}"
            )
            if view_path.exists():
                shutil.rmtree(view_path)
        refs: list[InstalledModelReference] = []
        seen_targets: dict[tuple[str, str], str] = {}
        for model_lock in model_locks:
            key = (model_lock.comfyui_folder.casefold(), model_lock.filename.casefold())
            existing = seen_targets.get(key)
            sha256 = _normalize_sha256(model_lock.sha256)
            if existing is not None and existing != sha256:
                raise ModelDownloadError(
                    _model_view_conflict_message(
                        model_lock.comfyui_folder, model_lock.filename
                    )
                )
            seen_targets[key] = sha256
        for local_model in resolved_local_models:
            key = (
                local_model.requirement.comfyui_folder.casefold(),
                local_model.requirement.filename.casefold(),
            )
            existing = seen_targets.get(key)
            if existing is not None and existing != local_model.sha256:
                raise ModelDownloadError(
                    _model_view_conflict_message(
                        local_model.requirement.comfyui_folder,
                        local_model.requirement.filename,
                    )
                )
            seen_targets[key] = local_model.sha256

        for model_lock in model_locks:
            blob_path, sha256, reused_existing_blob = await self._ensure_blob(
                model_lock,
                transactions_dir=staged_blobs_dir,
            )
            owned_model_path = self._materialize_owned_model(model_lock, blob_path)
            target = self._model_view_path(view_path, model_lock)
            strategy = self._materialize_link_or_copy(blob_path, target)
            verified = self._verify_materialized_file(
                target, sha256, model_lock.size_bytes
            )
            if not verified:
                raise ModelDownloadError(
                    "Materialized model view file failed verification for "
                    f"{model_lock.comfyui_folder}/{model_lock.filename}."
                )
            refs.append(
                InstalledModelReference(
                    requirement_id=model_lock.id,
                    comfyui_folder=model_lock.comfyui_folder,
                    filename=model_lock.filename,
                    verification_level=ModelVerificationLevel.SHA256_SIZE,
                    asset_ownership=AssetOwnership.NOOFY_DOWNLOADED,
                    model_id=model_lock.id,
                    sha256=f"sha256:{sha256}",
                    size_bytes=model_lock.size_bytes,
                    store_ref=str(self._ref_path(model_lock.id)),
                    blob_path=str(blob_path),
                    source_path=str(owned_model_path) if owned_model_path else None,
                    materialized_path=str(target),
                    materialization_strategy=strategy,
                    materialized_file_verified=verified,
                )
            )
            self.log_store.add(
                "info",
                "Model materialized into view",
                "model.store",
                details={
                    "model_id": model_lock.id,
                    "sha256": sha256,
                    "view_fingerprint": view_fingerprint,
                    "strategy": strategy,
                    "reused_existing_blob": reused_existing_blob,
                },
            )

        for local_model in resolved_local_models:
            requirement = local_model.requirement
            target = self._model_view_requirement_path(
                view_path,
                comfyui_folder=requirement.comfyui_folder,
                filename=requirement.filename,
            )
            strategy = self._materialize_link_or_copy(local_model.source_path, target)
            verified = self._verify_materialized_file(
                target, local_model.sha256, requirement.size_bytes
            )
            if not verified:
                raise ModelDownloadError(
                    "Materialized local model view file failed verification for "
                    f"{requirement.comfyui_folder}/{requirement.filename}."
                )
            refs.append(
                InstalledModelReference(
                    requirement_id=requirement.requirement_id,
                    comfyui_folder=requirement.comfyui_folder,
                    filename=requirement.filename,
                    verification_level=ModelVerificationLevel.FILENAME_SIZE,
                    asset_ownership=AssetOwnership.USER_LOCAL,
                    model_id=requirement.requirement_id,
                    sha256=f"sha256:{local_model.sha256}",
                    size_bytes=requirement.size_bytes,
                    source_path=str(local_model.source_path),
                    materialized_path=str(target),
                    materialization_strategy=strategy,
                    materialized_file_verified=verified,
                )
            )
            self.log_store.add(
                "info",
                "Local model candidate materialized into view",
                "model.store",
                details={
                    "model_id": requirement.requirement_id,
                    "sha256": local_model.sha256,
                    "view_fingerprint": view_fingerprint,
                    "strategy": strategy,
                    "verification_level": ModelVerificationLevel.FILENAME_SIZE.value,
                    "asset_ownership": AssetOwnership.USER_LOCAL.value,
                },
            )

        self._write_model_view_manifest(view_path, view_fingerprint, refs)
        return ModelViewMaterialization(
            view_fingerprint=view_fingerprint,
            view_path=view_path,
            model_references=refs,
            final_view_path=final_view_path,
        )

    def promote_model_view(
        self, materialization: ModelViewMaterialization
    ) -> ModelViewMaterialization:
        """Promote a staged model view into the canonical materialized view path."""
        final_view_path = materialization.final_view_path or self._model_view_dir(
            materialization.view_fingerprint
        )
        if materialization.view_path == final_view_path:
            return ModelViewMaterialization(
                view_fingerprint=materialization.view_fingerprint,
                view_path=final_view_path,
                model_references=materialization.model_references,
                final_view_path=final_view_path,
            )

        with self._lock:
            if final_view_path.exists():
                shutil.rmtree(materialization.view_path, ignore_errors=True)
            else:
                final_view_path.parent.mkdir(parents=True, exist_ok=True)
                materialization.view_path.replace(final_view_path)
            promoted_refs = _references_for_promoted_view(
                materialization.model_references,
                old_view_path=materialization.view_path,
                new_view_path=final_view_path,
            )
            self._write_model_view_manifest(
                final_view_path, materialization.view_fingerprint, promoted_refs
            )

        return ModelViewMaterialization(
            view_fingerprint=materialization.view_fingerprint,
            view_path=final_view_path,
            model_references=promoted_refs,
            final_view_path=final_view_path,
        )

    def has_blob(self, sha256: str) -> bool:
        return self._blob_path(_normalize_sha256(sha256)).exists()

    def is_materialized(self, model_lock: ModelLock) -> bool:
        return self._materialized_path(model_lock).exists()

    def sweep_orphan_materialized_links(self) -> int:
        """Remove materialized links/files whose recorded model blob no longer exists."""
        views_dir = self.materialized_dir / "views"
        if not views_dir.exists():
            return 0
        removed = 0
        for manifest_path in sorted(views_dir.glob("model-view-*/manifest.json")):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for reference in data.get("model_references", []):
                if not isinstance(reference, dict):
                    continue
                blob_path_value = reference.get("blob_path")
                materialized_path_value = reference.get("materialized_path")
                if not isinstance(blob_path_value, str) or not isinstance(
                    materialized_path_value, str
                ):
                    continue
                blob_path = Path(blob_path_value)
                materialized_path = Path(materialized_path_value)
                if blob_path.exists() or not (
                    materialized_path.exists() or materialized_path.is_symlink()
                ):
                    continue
                try:
                    materialized_path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    # ------------------------------------------------------------------
    # Internal: paths
    # ------------------------------------------------------------------

    def _blob_path(self, sha256: str) -> Path:
        return self.blobs_dir / sha256 / "blob"

    def _model_view_dir(self, view_fingerprint: str) -> Path:
        return (
            self.materialized_dir
            / "views"
            / f"model-view-{_safe_fingerprint(view_fingerprint)}"
        )

    def _ref_path(self, model_id: str) -> Path:
        safe = model_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.refs_dir / f"{safe}.json"

    def _materialized_path(self, model_lock: ModelLock) -> Path:
        folder_parts = _safe_relative_parts(
            model_lock.comfyui_folder, field_name="comfyui_folder"
        )
        filename_parts = _safe_relative_parts(
            model_lock.filename, field_name="filename"
        )
        return self.materialized_dir.joinpath(*folder_parts, *filename_parts)

    def _model_view_path(self, view_path: Path, model_lock: ModelLock) -> Path:
        return self._model_view_requirement_path(
            view_path,
            comfyui_folder=model_lock.comfyui_folder,
            filename=model_lock.filename,
        )

    def _model_view_requirement_path(
        self, view_path: Path, *, comfyui_folder: str, filename: str
    ) -> Path:
        folder_parts = _safe_relative_parts(comfyui_folder, field_name="comfyui_folder")
        filename_parts = _safe_relative_parts(filename, field_name="filename")
        return view_path.joinpath(*folder_parts, *filename_parts)

    # ------------------------------------------------------------------
    # Internal: transactions
    # ------------------------------------------------------------------

    def _open_transaction(
        self, model_id: str, *, transactions_dir: Path | None = None
    ) -> Path:
        root_dir = transactions_dir or self.transactions_dir
        root_dir.mkdir(parents=True, exist_ok=True)
        safe_id = model_id.replace("/", "_")
        txn_dir = root_dir / f"model-{safe_id}-{uuid.uuid4().hex[:8]}"
        txn_dir.mkdir(parents=True, exist_ok=False)
        return txn_dir

    def _cleanup_transaction(self, txn_dir: Path) -> None:
        if txn_dir.exists():
            shutil.rmtree(txn_dir, ignore_errors=True)

    def _commit_blob(self, source: Path, blob_path: Path) -> None:
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            os.replace(source, blob_path)

    def _verify_existing_blob(
        self, model_lock: ModelLock, blob_path: Path, sha256: str
    ) -> None:
        size = blob_path.stat().st_size
        if model_lock.size_bytes > 0 and size != model_lock.size_bytes:
            raise ModelDownloadError(
                f"Stored blob size for {model_lock.id} does not match lock "
                f"(expected {model_lock.size_bytes}, got {size})"
            )
        actual = _sha256_file(blob_path)
        if actual != sha256:
            raise ModelDownloadError(
                f"Stored blob for {model_lock.id} is corrupt "
                f"(expected sha256 {sha256}, got {actual})"
            )

    # ------------------------------------------------------------------
    # Internal: download dispatch
    # ------------------------------------------------------------------

    async def _download_with_fallback(self, urls: list[str], dest: Path) -> int:
        last_error: Exception | None = None
        for url in urls:
            try:
                return await self._invoke_downloader(url, dest)
            except Exception as exc:
                last_error = exc
                self.log_store.add(
                    "warning",
                    "Model source URL failed; trying next mirror",
                    "model.store",
                    details={"url": url, "error": str(exc)},
                )
                if dest.exists():
                    dest.unlink()
        raise ModelDownloadError(
            f"All source URLs failed for model download: {last_error}"
        )

    async def _invoke_downloader(self, url: str, dest: Path) -> int:
        if self._downloader is None:
            raise ModelDownloadError(
                "ModelStore was constructed without a downloader; cannot fetch model bytes"
            )
        return await self._downloader(url, dest)

    # ------------------------------------------------------------------
    # Internal: ref + materialize
    # ------------------------------------------------------------------

    async def _ensure_blob(
        self,
        model_lock: ModelLock,
        *,
        transactions_dir: Path | None = None,
    ) -> tuple[Path, str, bool]:
        sha256 = _normalize_sha256(model_lock.sha256)
        blob_path = self._blob_path(sha256)
        if blob_path.exists():
            self._verify_existing_blob(model_lock, blob_path, sha256)
            self._write_ref(model_lock, sha256, blob_path)
            return blob_path, sha256, True

        if not model_lock.source_urls:
            raise ModelDownloadError(
                f"No source URLs available to download model {model_lock.id}"
            )

        txn_dir = self._open_transaction(
            model_lock.id, transactions_dir=transactions_dir
        )
        download_target = txn_dir / "blob"
        try:
            bytes_written = await self._download_with_fallback(
                model_lock.source_urls, download_target
            )
            if bytes_written != model_lock.size_bytes and model_lock.size_bytes > 0:
                raise ModelDownloadError(
                    f"Size mismatch for {model_lock.id}: "
                    f"expected {model_lock.size_bytes}, got {bytes_written}"
                )
            actual_sha256 = _sha256_file(download_target)
            if actual_sha256 != sha256:
                raise ModelDownloadError(
                    f"Hash mismatch for {model_lock.id}: "
                    f"expected {sha256}, got {actual_sha256}"
                )
            self._commit_blob(download_target, blob_path)
            self._write_ref(model_lock, sha256, blob_path)
            return blob_path, sha256, False
        except BaseException as exc:
            self.log_store.add(
                "error",
                "Model download failed",
                "model.store",
                details={
                    "model_id": model_lock.id,
                    "sha256": sha256,
                    "error": str(exc),
                },
            )
            raise
        finally:
            self._cleanup_transaction(txn_dir)

    def _write_ref(self, model_lock: ModelLock, sha256: str, blob_path: Path) -> None:
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        ref = {
            "schema_version": REF_SCHEMA_VERSION,
            "model_id": model_lock.id,
            "sha256": sha256,
            "size_bytes": blob_path.stat().st_size,
            "comfyui_folder": model_lock.comfyui_folder,
            "filename": model_lock.filename,
            "blob_path": str(blob_path),
        }
        ref_path = self._ref_path(model_lock.id)
        tmp_path = ref_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(ref, indent=2), encoding="utf-8")
        tmp_path.replace(ref_path)

    def _materialize(self, model_lock: ModelLock, blob_path: Path) -> tuple[Path, str]:
        target = self._materialized_path(model_lock)
        strategy = self._materialize_link_or_copy(blob_path, target)
        return target, strategy

    def _materialize_owned_model(self, model_lock: ModelLock, blob_path: Path) -> Path | None:
        if self.owned_model_root is None:
            return None
        target = self._owned_model_path(model_lock)
        if target.exists() or target.is_symlink():
            return target
        self._materialize_link_or_copy(blob_path, target)
        return target

    def _owned_model_path(self, model_lock: ModelLock) -> Path:
        folder_parts = _safe_relative_parts(
            model_lock.comfyui_folder, field_name="comfyui_folder"
        )
        filename_parts = _safe_relative_parts(
            model_lock.filename, field_name="filename"
        )
        if self.owned_model_root is None:
            raise ModelDownloadError("No owned model root is configured.")
        return self.owned_model_root.joinpath(*folder_parts, *filename_parts)

    def _materialize_link_or_copy(self, blob_path: Path, target: Path) -> str:
        tmp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            _validate_materialized_target_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            strategy = self._try_materialize(blob_path, tmp_target)
            os.replace(tmp_target, target)
        except BaseException as exc:
            if tmp_target.exists() or tmp_target.is_symlink():
                tmp_target.unlink()
            self.log_store.add(
                "error",
                "Model materialization failed",
                "model.store",
                details={
                    "source_path": str(blob_path),
                    "target_path": str(target),
                    "error": str(exc),
                },
            )
            raise
        return strategy

    def _try_materialize(self, blob_path: Path, tmp_target: Path) -> str:
        try:
            try:
                os.link(blob_path, tmp_target)
                return "hardlink"
            except OSError as exc:
                self.log_store.add(
                    "info",
                    "Model hardlink materialization unavailable; trying symlink",
                    "model.store",
                    details={"error": str(exc)},
                )
            if self._can_symlink():
                try:
                    os.symlink(blob_path, tmp_target)
                    return "symlink"
                except OSError as exc:
                    self.log_store.add(
                        "info",
                        "Model symlink materialization unavailable; copying",
                        "model.store",
                        details={"error": str(exc)},
                    )
            shutil.copy2(blob_path, tmp_target)
            return "copy"
        except BaseException:
            if tmp_target.exists() or tmp_target.is_symlink():
                tmp_target.unlink()
            raise

    def _can_symlink(self) -> bool:
        if self._symlink_capability is None:
            self._symlink_capability = (
                True
                if sys.platform != "win32"
                else probe_symlink_capability(
                    self.transactions_dir, log_store=self.log_store
                )
            )
        return self._symlink_capability

    def _verify_materialized_file(
        self, path: Path, sha256: str, size_bytes: int
    ) -> bool:
        if size_bytes > 0 and path.stat().st_size != size_bytes:
            return False
        return _sha256_file(path) == sha256

    def _resolve_local_models(
        self, requirements: list[LocalModelRequirement]
    ) -> list[ResolvedLocalModel]:
        resolved: list[ResolvedLocalModel] = []
        for requirement in requirements:
            source_path = self._find_local_candidate(requirement)
            sha256 = _sha256_file(source_path)
            self.log_store.add(
                "info",
                "Reusing local model candidate",
                "model.store",
                details={
                    "model_id": requirement.requirement_id,
                    "comfyui_folder": requirement.comfyui_folder,
                    "filename": requirement.filename,
                    "size_bytes": requirement.size_bytes,
                    "sha256": sha256,
                    "verification_level": ModelVerificationLevel.FILENAME_SIZE.value,
                    "asset_ownership": AssetOwnership.USER_LOCAL.value,
                },
            )
            resolved.append(
                ResolvedLocalModel(
                    requirement=requirement,
                    source_path=source_path,
                    sha256=sha256,
                )
            )
        return resolved

    def _find_local_candidate(self, requirement: LocalModelRequirement) -> Path:
        folder_parts = _safe_relative_parts(
            requirement.comfyui_folder, field_name="comfyui_folder"
        )
        filename_parts = _safe_relative_parts(
            requirement.filename, field_name="filename"
        )
        checked: list[str] = []
        for root in self.local_model_roots:
            candidate = root.joinpath(*folder_parts, *filename_parts)
            checked.append(str(candidate))
            if not candidate.is_file():
                continue
            size = candidate.stat().st_size
            if size != requirement.size_bytes:
                raise LocalModelCandidateError(
                    f"Local model candidate size mismatch for {requirement.requirement_id}: "
                    f"expected {requirement.size_bytes}, got {size}"
                )
            return candidate
        raise LocalModelCandidateError(
            f"No local model candidate found for {requirement.requirement_id}; checked {checked}"
        )

    def _validate_model_source_policy(
        self,
        source_policy: SourcePolicy | None,
        *,
        model_locks: list[ModelLock],
        local_model_requirements: list[LocalModelRequirement],
    ) -> None:
        if source_policy is None:
            return
        if not source_policy.automatic_preparation_allowed:
            raise ModelSourcePolicyError(
                "Model preparation is blocked by the workflow source policy."
            )
        allowed_origins = set(source_policy.allowed_model_origins)
        if model_locks and source_policy.model_source_trust not in {
            ModelSourceTrust.HASHED,
            ModelSourceTrust.MIXED,
        }:
            raise ModelSourcePolicyError(
                "Model download is blocked because the workflow policy does not allow hash-verified model sources."
            )
        if local_model_requirements and source_policy.model_source_trust not in {
            ModelSourceTrust.FILENAME_SIZE,
            ModelSourceTrust.MIXED,
        }:
            raise ModelSourcePolicyError(
                "Local model reuse is blocked because the workflow policy requires hash-verified model sources."
            )
        if not allowed_origins:
            return
        for model_lock in model_locks:
            origins = _model_lock_origins(model_lock)
            if origins.isdisjoint(allowed_origins):
                raise ModelSourcePolicyError(
                    "Model download is blocked because no model source URL matches the workflow source policy."
                )
        if local_model_requirements and "user-local" not in allowed_origins:
            raise ModelSourcePolicyError(
                "Local model reuse is blocked by the workflow model source policy."
            )

    def _write_model_view_manifest(
        self,
        view_path: Path,
        view_fingerprint: str,
        refs: list[InstalledModelReference],
    ) -> None:
        view_path.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": MODEL_VIEW_SCHEMA_VERSION,
            "view_fingerprint": view_fingerprint,
            "model_references": [
                ref.model_dump(mode="json", exclude_none=True) for ref in refs
            ],
        }
        target = view_path / "manifest.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        tmp.replace(target)


def probe_symlink_capability(
    probe_dir: Path, *, log_store: DiagnosticsSink | None = None
) -> bool:
    probe_dir.mkdir(parents=True, exist_ok=True)
    source = probe_dir / f"symlink-probe-source-{uuid.uuid4().hex}"
    link = probe_dir / f"symlink-probe-link-{uuid.uuid4().hex}"
    try:
        source.write_text("probe", encoding="utf-8")
        os.symlink(source, link)
        return True
    except OSError as exc:
        if log_store is not None:
            log_store.add(
                "info",
                "Model symlink capability probe failed; copy fallback will be used",
                "model.store",
                details={"error": str(exc)},
            )
        return False
    finally:
        if link.exists() or link.is_symlink():
            link.unlink()
        if source.exists():
            source.unlink()


def model_view_fingerprint(
    *,
    view_id: str,
    model_locks: list[ModelLock],
    local_models: list[ResolvedLocalModel] | None = None,
) -> str:
    local_models = local_models or []
    return sha256_fingerprint(
        {
            "schema_version": MODEL_VIEW_SCHEMA_VERSION,
            "kind": "model_view",
            "view_id": view_id,
            "models": sorted(
                [
                    {
                        "id": model.id,
                        "sha256": _normalize_sha256(model.sha256),
                        "size_bytes": model.size_bytes,
                        "comfyui_folder": model.comfyui_folder,
                        "filename": model.filename,
                    }
                    for model in model_locks
                ],
                key=lambda item: (
                    item["comfyui_folder"],
                    item["filename"],
                    item["sha256"],
                ),
            ),
            "local_models": sorted(
                [
                    {
                        "id": model.requirement.requirement_id,
                        "sha256": model.sha256,
                        "size_bytes": model.requirement.size_bytes,
                        "comfyui_folder": model.requirement.comfyui_folder,
                        "filename": model.requirement.filename,
                        "verification_level": ModelVerificationLevel.FILENAME_SIZE.value,
                    }
                    for model in local_models
                ],
                key=lambda item: (
                    item["comfyui_folder"],
                    item["filename"],
                    item["sha256"],
                ),
            ),
        }
    )


def _references_for_promoted_view(
    refs: list[InstalledModelReference],
    *,
    old_view_path: Path,
    new_view_path: Path,
) -> list[InstalledModelReference]:
    promoted: list[InstalledModelReference] = []
    for ref in refs:
        try:
            relative_path = Path(ref.materialized_path).relative_to(old_view_path)
            materialized_path = str(new_view_path / relative_path)
        except ValueError:
            materialized_path = ref.materialized_path
        promoted.append(ref.model_copy(update={"materialized_path": materialized_path}))
    return promoted


def _safe_fingerprint(fingerprint: str) -> str:
    return (
        fingerprint.replace("sha256:", "")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "_")
    )


def _model_view_conflict_message(comfyui_folder: str, filename: str) -> str:
    return f"Model view has conflicting requirements for {comfyui_folder}/{filename}."


def _model_lock_origins(model_lock: ModelLock) -> set[str]:
    origins = {"hashed-download"}
    for url in model_lock.source_urls:
        parsed = urlparse(url)
        if parsed.scheme:
            origins.add(parsed.scheme)
        if parsed.netloc:
            origins.add(parsed.netloc.lower())
    return origins


def _validate_materialized_target_path(target: Path) -> None:
    if (
        sys.platform == "win32"
        and len(str(target)) > WINDOWS_MAX_MATERIALIZED_PATH_CHARS
    ):
        raise ModelDownloadError(
            "Materialized model path is too long for the Windows runtime profile: "
            f"{target}"
        )
