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

from app.engine.diagnostics import LogStore
from app.runtime.isolation import ModelLock

REF_SCHEMA_VERSION = "0.1.0"


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
        log_store: LogStore | None = None,
        downloader: DownloadFn | None = None,
    ) -> None:
        self.blobs_dir = blobs_dir
        self.refs_dir = refs_dir
        self.materialized_dir = materialized_dir
        self.transactions_dir = transactions_dir
        self.log_store = log_store or LogStore()
        self._downloader = downloader
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
        self._materialized_path(model_lock)  # Validate lock-derived output path before IO.

        if blob_path.exists():
            self._verify_existing_blob(model_lock, blob_path, sha256)
            self._write_ref(model_lock, sha256, blob_path)
            materialized = self._materialize(model_lock, blob_path)
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
            )

        if not model_lock.source_urls:
            raise ModelDownloadError(
                f"No source URLs available to download model {model_lock.id}"
            )

        txn_dir = self._open_transaction(model_lock.id)
        download_target = txn_dir / "blob"
        try:
            bytes_written = await self._download_with_fallback(model_lock.source_urls, download_target)
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
            materialized = self._materialize(model_lock, blob_path)
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
        )

    def has_blob(self, sha256: str) -> bool:
        return self._blob_path(_normalize_sha256(sha256)).exists()

    def is_materialized(self, model_lock: ModelLock) -> bool:
        return self._materialized_path(model_lock).exists()

    # ------------------------------------------------------------------
    # Internal: paths
    # ------------------------------------------------------------------

    def _blob_path(self, sha256: str) -> Path:
        return self.blobs_dir / sha256 / "blob"

    def _ref_path(self, model_id: str) -> Path:
        safe = model_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.refs_dir / f"{safe}.json"

    def _materialized_path(self, model_lock: ModelLock) -> Path:
        folder_parts = _safe_relative_parts(model_lock.comfyui_folder, field_name="comfyui_folder")
        filename_parts = _safe_relative_parts(model_lock.filename, field_name="filename")
        return self.materialized_dir.joinpath(*folder_parts, *filename_parts)

    # ------------------------------------------------------------------
    # Internal: transactions
    # ------------------------------------------------------------------

    def _open_transaction(self, model_id: str) -> Path:
        self.transactions_dir.mkdir(parents=True, exist_ok=True)
        safe_id = model_id.replace("/", "_")
        txn_dir = self.transactions_dir / f"model-{safe_id}-{uuid.uuid4().hex[:8]}"
        txn_dir.mkdir(parents=True, exist_ok=False)
        return txn_dir

    def _cleanup_transaction(self, txn_dir: Path) -> None:
        if txn_dir.exists():
            shutil.rmtree(txn_dir, ignore_errors=True)

    def _commit_blob(self, source: Path, blob_path: Path) -> None:
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            os.replace(source, blob_path)

    def _verify_existing_blob(self, model_lock: ModelLock, blob_path: Path, sha256: str) -> None:
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

    def _materialize(self, model_lock: ModelLock, blob_path: Path) -> Path:
        target = self._materialized_path(model_lock)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        if sys.platform == "win32":
            shutil.copy2(blob_path, tmp_target)
        else:
            os.symlink(blob_path, tmp_target)
        try:
            os.replace(tmp_target, target)
        except BaseException:
            if tmp_target.exists() or tmp_target.is_symlink():
                tmp_target.unlink()
            raise
        return target
