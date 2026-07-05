"""FP8 → 16-bit model conversion jobs for Apple Silicon compatibility.

The conversion itself runs as a subprocess inside the managed ComfyUI venv
(the only environment with torch — see ``fp8_convert_script.py``). This
service owns job state (queued/converting/completed/... polled by the
frontend), the derived-artifact registry, the per-workflow model override
that reroutes the graph to the converted file, and the safe removal of the
original fp8 source.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import threading
import urllib.parse
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.artifacts import ModelVerificationLevel
from app.diagnostics import DiagnosticsSink
from app.engine.models import RequiredModelSummary
from app.models.paths import ensure_inside, model_key
from app.workflows.fp8_compatibility import (
    inspect_safetensors_fp8_header,
    read_safetensors_header,
)
from app.workflows.model_overrides import (
    WorkflowModelOverride,
    WorkflowModelOverrideStore,
)
from app.workflows.package import RequiredModel

CONVERTED_FOR_MAC_SUFFIX = "-converted-for-mac"
_LOG_SOURCE = "workflows.fp8_compat"
# Loader compatibility is only guaranteed when the replacement keeps the same
# container format the graph's loader nodes already accept (a .gguf variant,
# for example, needs different loader nodes entirely). `.sft` is the same
# safetensors container under a shorter name.
_ALTERNATIVE_MODEL_SUFFIXES = frozenset({".safetensors", ".sft"})

# on_progress receives each JSON progress payload from the convert script;
# the returned dict is the final payload ({"phase": "complete", ...} or
# {"phase": "error", ...}). Implementations must honor the cancel event by
# terminating the work and returning a {"phase": "canceled"} payload.
ConversionSubprocessRunner = Callable[
    [list[str], Callable[[dict], None], asyncio.Event],
    Awaitable[dict],
]


class ConvertedModelRecord(BaseModel):
    source_sha256: str
    source_filename: str
    source_folder: str
    converted_filename: str
    converted_sha256: str
    converted_size_bytes: int
    target_dtype: str
    workflow_id: str
    created_at: str
    ownership: str = "noofy_converted"


class ConvertedModelsRegistry:
    """Provenance registry for locally converted models (derived artifacts)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def find_by_source(self, source_sha256: str) -> ConvertedModelRecord | None:
        for record in self.records():
            if record.source_sha256 == source_sha256:
                return record
        return None

    def records(self) -> list[ConvertedModelRecord]:
        with self._lock:
            return self._read_unlocked()

    def add(self, record: ConvertedModelRecord) -> None:
        with self._lock:
            records = [
                existing
                for existing in self._read_unlocked()
                if existing.source_sha256 != record.source_sha256
            ]
            records.append(record)
            self._write_unlocked(records)

    def _read_unlocked(self) -> list[ConvertedModelRecord]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [ConvertedModelRecord.model_validate(item) for item in data.get("records", [])]
        except Exception:
            return []

    def _write_unlocked(self, records: list[ConvertedModelRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "records": [record.model_dump() for record in records],
        }
        tmp = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class Fp8ConversionJobStatus(BaseModel):
    job_id: str
    workflow_id: str
    folder: str
    filename: str
    status: str  # queued | converting | finalizing | completed | failed | canceled
    percent: float | None = None
    user_facing_message: str | None = None
    error_code: str | None = None
    converted_filename: str | None = None
    target_dtype: str | None = None
    source_removed: bool | None = None
    source_removal_skipped_reason: str | None = None
    model_summary: RequiredModelSummary | None = None


class Fp8ConversionJobStart(BaseModel):
    job_id: str
    status: str
    user_facing_message: str | None = None


class Fp8ConversionConflictError(RuntimeError):
    def __init__(self, job_id: str) -> None:
        super().__init__("A conversion for this model is already running.")
        self.job_id = job_id


@dataclass
class _Fp8ConversionJob:
    job_id: str
    workflow_id: str
    folder: str
    filename: str
    cancel_event: asyncio.Event
    task: asyncio.Task | None
    status: str
    percent: float | None = None
    user_facing_message: str | None = "Conversion is queued."
    error_code: str | None = None
    converted_filename: str | None = None
    target_dtype: str | None = None
    source_removed: bool | None = None
    source_removal_skipped_reason: str | None = None
    model_summary: RequiredModelSummary | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class Fp8ConversionService:
    def __init__(
        self,
        *,
        engine_service: object,
        override_store: WorkflowModelOverrideStore,
        registry: ConvertedModelsRegistry,
        ownership_store: object,
        model_inventory_service: object | None = None,
        model_download_service: object | None = None,
        log_store: DiagnosticsSink | None = None,
        runtime_python_executable: Callable[[], str | None] | None = None,
        subprocess_runner: ConversionSubprocessRunner | None = None,
        convert_script_path: Path | None = None,
        mps_execution_active: Callable[[], bool] | None = None,
    ) -> None:
        self.engine_service = engine_service
        self.override_store = override_store
        self.registry = registry
        self.ownership_store = ownership_store
        self.model_inventory_service = model_inventory_service
        self.model_download_service = model_download_service
        self.log_store = log_store
        # The whole flow is Apple Silicon/MPS-specific; the API entry points
        # refuse on other platforms even when called directly (None = ungated,
        # for tests and embedded use).
        self.mps_execution_active = mps_execution_active
        self.runtime_python_executable = runtime_python_executable
        # Bound methods get a fresh object per attribute access, so remember
        # whether we run the real subprocess (which must never fall back to a
        # system python) instead of comparing method identities later.
        self._uses_default_runner = subprocess_runner is None
        self.subprocess_runner = subprocess_runner or self._run_convert_subprocess
        self.convert_script_path = convert_script_path or (
            Path(__file__).parent / "fp8_convert_script.py"
        )
        self._jobs: dict[str, _Fp8ConversionJob] = {}
        self._sweep_orphaned_partials()

    # ---- public API -------------------------------------------------------

    def start(self, workflow_id: str, folder: str, filename: str) -> Fp8ConversionJobStart:
        self._ensure_mps_platform()
        model = self._required_model(workflow_id, folder, filename)
        source_path = self._resolve_source_path(model)
        if source_path is None:
            raise FileNotFoundError(f"Model file not found: {folder}/{filename}")
        for job in self._jobs.values():
            if (
                job.folder == folder
                and job.filename == filename
                and job.status in {"queued", "converting", "finalizing"}
            ):
                raise Fp8ConversionConflictError(job.job_id)
        job_id = f"fp8-conversion-{uuid.uuid4().hex}"
        job = _Fp8ConversionJob(
            job_id=job_id,
            workflow_id=workflow_id,
            folder=folder,
            filename=filename,
            cancel_event=asyncio.Event(),
            task=None,
            status="queued",
        )
        self._jobs[job_id] = job
        self._log(
            "info",
            "User chose FP8 conversion",
            workflow_id=workflow_id,
            details={"folder": folder, "filename": filename, "job_id": job_id},
        )
        job.task = asyncio.create_task(self._run_job(job_id, source_path))
        return Fp8ConversionJobStart(
            job_id=job_id,
            status=job.status,
            user_facing_message=job.user_facing_message,
        )

    def status(self, workflow_id: str, job_id: str) -> Fp8ConversionJobStatus:
        job = self._jobs.get(job_id)
        if job is None or job.workflow_id != workflow_id:
            raise KeyError(f"Unknown FP8 conversion job: {job_id}")
        return self._status(job)

    def cancel(self, workflow_id: str, job_id: str) -> Fp8ConversionJobStatus:
        job = self._jobs.get(job_id)
        if job is None or job.workflow_id != workflow_id:
            raise KeyError(f"Unknown FP8 conversion job: {job_id}")
        if job.status in {"queued", "converting", "finalizing"}:
            job.cancel_event.set()
            job.user_facing_message = "Canceling conversion..."
        return self._status(job)

    def record_dismissal(self, workflow_id: str, folder: str, filename: str) -> None:
        self._log(
            "info",
            "User dismissed FP8 compatibility popup",
            workflow_id=workflow_id,
            details={"folder": folder, "filename": filename},
        )

    def start_alternative_download(
        self,
        workflow_id: str,
        folder: str,
        filename: str,
        url: str,
    ) -> object:
        """Download a user-provided compatible variant through the normal safe
        download path, then record a workflow-local override pointing at it."""
        self._ensure_mps_platform()
        self._required_model(workflow_id, folder, filename)
        replacement_filename = _filename_from_download_url(url)
        if replacement_filename == filename:
            raise ValueError(
                "The link points to a file with the same name as the FP8 model. "
                "Use a link to a different variant (for example FP16, BF16, or FP4)."
            )
        download_service = self.model_download_service
        start_direct = getattr(download_service, "start_direct", None)
        if not callable(start_direct):
            raise RuntimeError("Model downloads are unavailable.")
        model = RequiredModel(
            folder=folder,
            filename=replacement_filename,
            source_urls=[url],
            verification_level=ModelVerificationLevel.FILENAME_ONLY,
        )
        self._log(
            "info",
            "User chose alternative model download",
            workflow_id=workflow_id,
            details={
                "folder": folder,
                "filename": filename,
                "replacement_filename": replacement_filename,
                "url_host": urllib.parse.urlsplit(url).hostname,
            },
        )

        def on_completed(downloaded: RequiredModel) -> None:
            self._record_downloaded_override(workflow_id, folder, filename, downloaded)

        return start_direct(
            workflow_id=workflow_id,
            models=[model],
            queued_message="Downloading the compatible model...",
            explicit_source_urls_authoritative=True,
            on_completed=on_completed,
        )

    def _reject_incompatible_replacement(self, workflow_id: str, replacement_path: Path) -> None:
        """A replacement must be a readable safetensors file and must not be
        fp8 itself — a bad override would satisfy the preflight gate while
        still failing at load or crashing on MPS."""
        if not replacement_path.is_file():
            return
        header = read_safetensors_header(replacement_path)
        if header is None:
            # Authoritative direct downloads adopt any bytes the URL served;
            # an HTML error page saved as .safetensors must never route the
            # graph into a loader failure.
            self._discard_replacement(
                workflow_id,
                replacement_path,
                "Rejected alternative model download because it is not a valid safetensors file",
                {"filename": replacement_path.name},
            )
            raise ValueError(
                "The downloaded file is not a valid .safetensors model. "
                "Check that the link points directly to the model file."
            )
        inspection = inspect_safetensors_fp8_header(header)
        if not inspection.has_incompatible_fp8:
            return
        self._discard_replacement(
            workflow_id,
            replacement_path,
            "Rejected alternative model download because it is also FP8",
            {
                "filename": replacement_path.name,
                "fp8_dtypes": list(inspection.fp8_dtypes),
                "quant_format": inspection.quant_format,
            },
        )
        raise ValueError(
            "The downloaded model is also an FP8 model, which Apple Silicon cannot run. "
            "Use an FP16, BF16, or FP4 variant instead."
        )

    def _discard_replacement(
        self,
        workflow_id: str,
        replacement_path: Path,
        message: str,
        details: dict,
    ) -> None:
        _unlink_quietly(replacement_path)
        forget = getattr(self.ownership_store, "forget_model", None)
        if callable(forget):
            forget(model_key(replacement_path.parent.name, replacement_path.name))
        self._log("warning", message, workflow_id=workflow_id, details=details)

    def _record_downloaded_override(
        self,
        workflow_id: str,
        folder: str,
        source_filename: str,
        downloaded: RequiredModel,
    ) -> None:
        replacement_path = self._noofy_models_dir() / folder / downloaded.filename
        self._reject_incompatible_replacement(workflow_id, replacement_path)
        replacement_sha256 = None
        if downloaded.checksum:
            replacement_sha256 = downloaded.checksum.removeprefix("sha256:").casefold()
        replacement_size = downloaded.size_bytes
        if replacement_path.is_file():
            if replacement_sha256 is None:
                replacement_sha256 = _sha256_file(replacement_path)
            if replacement_size is None:
                replacement_size = replacement_path.stat().st_size
        self.override_store.upsert(
            workflow_id,
            WorkflowModelOverride(
                folder=folder,
                source_filename=source_filename,
                replacement_filename=downloaded.filename,
                replacement_sha256=replacement_sha256,
                replacement_size_bytes=replacement_size,
                target_dtype=None,
                origin="downloaded",
                created_at=datetime.now(UTC).isoformat(),
            ),
        )
        self._log(
            "info",
            "Local workflow model override applied",
            workflow_id=workflow_id,
            details={
                "folder": folder,
                "source_filename": source_filename,
                "replacement_filename": downloaded.filename,
                "origin": "downloaded",
            },
        )

    # ---- job execution ----------------------------------------------------

    async def _run_job(self, job_id: str, source_path: Path) -> None:
        job = self._jobs[job_id]
        try:
            await self._convert_and_finalize(job, source_path)
        except asyncio.CancelledError:
            job.status = "canceled"
            job.user_facing_message = "Conversion canceled."
            raise
        except Exception as exc:
            job.status = "failed"
            job.error_code = job.error_code or "conversion_failed"
            job.user_facing_message = "The model could not be converted."
            self._log(
                "error",
                "FP8 model conversion failed",
                workflow_id=job.workflow_id,
                details={
                    "job_id": job.job_id,
                    "folder": job.folder,
                    "filename": job.filename,
                    "error": str(exc),
                    "error_code": job.error_code,
                },
            )

    async def _convert_and_finalize(self, job: _Fp8ConversionJob, source_path: Path) -> None:
        job.status = "converting"
        job.user_facing_message = "Preparing conversion..."
        source_stat = source_path.stat()
        source_sha256 = await asyncio.to_thread(_sha256_file, source_path)
        if job.cancel_event.is_set():
            self._mark_canceled(job)
            return

        converted_filename = _converted_filename(job.filename)
        target_dir = self._noofy_models_dir() / job.folder
        ensure_inside(target_dir / converted_filename, self._noofy_models_dir())
        existing = self.registry.find_by_source(source_sha256)
        reused = False
        if existing is not None and (target_dir / existing.converted_filename).is_file():
            converted_filename = existing.converted_filename
            converted_sha256 = existing.converted_sha256
            converted_size = existing.converted_size_bytes
            target_dtype = existing.target_dtype
            reused = True
            self._log(
                "info",
                "Reusing existing converted model artifact",
                workflow_id=job.workflow_id,
                details={
                    "job_id": job.job_id,
                    "source_sha256": source_sha256,
                    "converted_filename": converted_filename,
                },
            )
        else:
            final_path = target_dir / converted_filename
            part_path = final_path.with_name(final_path.name + ".part")
            target_dir.mkdir(parents=True, exist_ok=True)
            self._check_disk_space(job, source_stat.st_size, target_dir)
            job.user_facing_message = "Converting model for Apple Silicon..."
            final_payload = await self._execute_conversion(job, source_path, part_path)
            phase = final_payload.get("phase")
            if phase == "canceled" or job.cancel_event.is_set():
                _unlink_quietly(part_path)
                self._mark_canceled(job)
                return
            if phase != "complete":
                _unlink_quietly(part_path)
                job.error_code = str(final_payload.get("code") or "conversion_failed")
                raise RuntimeError(str(final_payload.get("message") or "Conversion failed."))
            part_path.replace(final_path)
            job.status = "finalizing"
            job.user_facing_message = "Verifying converted model..."
            converted_sha256 = await asyncio.to_thread(_sha256_file, final_path)
            converted_size = final_path.stat().st_size
            target_dtype = str(final_payload.get("target_dtype") or "bf16")
            self.registry.add(
                ConvertedModelRecord(
                    source_sha256=source_sha256,
                    source_filename=job.filename,
                    source_folder=job.folder,
                    converted_filename=converted_filename,
                    converted_sha256=converted_sha256,
                    converted_size_bytes=converted_size,
                    target_dtype=target_dtype,
                    workflow_id=job.workflow_id,
                    created_at=datetime.now(UTC).isoformat(),
                )
            )

        job.converted_filename = converted_filename
        job.target_dtype = target_dtype
        mark_downloaded = getattr(self.ownership_store, "mark_downloaded", None)
        if callable(mark_downloaded):
            mark_downloaded(model_key(job.folder, converted_filename))
        self.override_store.upsert(
            job.workflow_id,
            WorkflowModelOverride(
                folder=job.folder,
                source_filename=job.filename,
                replacement_filename=converted_filename,
                replacement_sha256=converted_sha256,
                replacement_size_bytes=converted_size,
                target_dtype=target_dtype,
                origin="converted",
                created_at=datetime.now(UTC).isoformat(),
            ),
        )
        self._log(
            "info",
            "Local workflow model override applied",
            workflow_id=job.workflow_id,
            details={
                "folder": job.folder,
                "source_filename": job.filename,
                "replacement_filename": converted_filename,
                "origin": "converted",
                "reused_existing_artifact": reused,
            },
        )

        removed, skipped_reason = self._delete_source_if_safe(
            job,
            source_path=source_path,
            source_sha256=source_sha256,
            source_stat=source_stat,
        )
        job.source_removed = removed
        job.source_removal_skipped_reason = skipped_reason

        job.model_summary = self._refresh_model_summary(job.workflow_id)
        job.percent = 100.0
        job.status = "completed"
        job.user_facing_message = "Model converted for Apple Silicon."
        self._log(
            "info",
            "FP8 model conversion completed",
            workflow_id=job.workflow_id,
            details={
                "job_id": job.job_id,
                "folder": job.folder,
                "filename": job.filename,
                "converted_filename": converted_filename,
                "target_dtype": target_dtype,
                "source_removed": removed,
                "source_removal_skipped_reason": skipped_reason,
                "reused_existing_artifact": reused,
            },
        )

    async def _execute_conversion(
        self,
        job: _Fp8ConversionJob,
        source_path: Path,
        part_path: Path,
    ) -> dict:
        python_executable = (
            self.runtime_python_executable() if self.runtime_python_executable else None
        )
        if self._uses_default_runner and not python_executable:
            # Conversion needs torch and must only ever run inside the managed
            # ComfyUI venv — never a system python.
            job.error_code = "runtime_unavailable"
            raise RuntimeError("The managed ComfyUI runtime is not available for conversion.")
        command = [
            str(python_executable) if python_executable else "managed-python-unavailable",
            str(self.convert_script_path),
            "--input",
            str(source_path),
            "--output",
            str(part_path),
            "--target-dtype",
            "auto",
        ]

        def on_progress(payload: dict) -> None:
            done = payload.get("done")
            total = payload.get("total")
            if isinstance(done, int) and isinstance(total, int) and total > 0:
                job.percent = round(min(done / total, 1.0) * 100.0, 1)

        self._log(
            "info",
            "FP8 model conversion started",
            workflow_id=job.workflow_id,
            details={
                "job_id": job.job_id,
                "folder": job.folder,
                "filename": job.filename,
                "source_size_bytes": source_path.stat().st_size,
            },
        )
        return await self.subprocess_runner(command, on_progress, job.cancel_event)

    async def _run_convert_subprocess(
        self,
        command: list[str],
        on_progress: Callable[[dict], None],
        cancel_event: asyncio.Event,
    ) -> dict:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        final_payload: dict = {}

        async def read_stdout() -> None:
            nonlocal final_payload
            assert process.stdout is not None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8").strip())
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("phase") in {"complete", "error"}:
                    final_payload = payload
                else:
                    on_progress(payload)

        stderr_chunks: list[bytes] = []

        async def drain_stderr() -> None:
            # Drain concurrently: torch import warnings alone can fill the
            # pipe buffer and block the child if stderr is read only at exit.
            assert process.stderr is not None
            while True:
                chunk = await process.stderr.read(64 * 1024)
                if not chunk:
                    break
                stderr_chunks.append(chunk)
                if len(stderr_chunks) > 64:
                    del stderr_chunks[0]

        async def watch_cancel() -> None:
            await cancel_event.wait()
            try:
                process.kill()
            except ProcessLookupError:
                pass

        cancel_watch = asyncio.create_task(watch_cancel())
        stderr_drain = asyncio.create_task(drain_stderr())
        try:
            await read_stdout()
            await process.wait()
            await stderr_drain
        finally:
            cancel_watch.cancel()
            stderr_drain.cancel()
        if cancel_event.is_set():
            return {"phase": "canceled"}
        if final_payload:
            return final_payload
        stderr_output = b"".join(stderr_chunks)
        return {
            "phase": "error",
            "code": "conversion_failed",
            "message": stderr_output.decode("utf-8", errors="replace")[-2000:]
            or f"Conversion process exited with code {process.returncode}.",
        }

    # ---- source removal ---------------------------------------------------

    def _delete_source_if_safe(
        self,
        job: _Fp8ConversionJob,
        *,
        source_path: Path,
        source_sha256: str,
        source_stat: object,
    ) -> tuple[bool, str | None]:
        key = model_key(job.folder, job.filename)
        origin = None
        origin_for_model = getattr(self.ownership_store, "origin_for_model", None)
        if callable(origin_for_model):
            origin = origin_for_model(key)
        if origin not in ("imported", "downloaded"):
            return self._skip_source_removal(job, "user_owned")

        # Ownership records are keyed by folder/filename only; make sure the
        # file we are about to delete is still the one we converted.
        try:
            current_stat = source_path.stat()
        except OSError:
            return self._skip_source_removal(job, "source_missing")
        stat_changed = (
            current_stat.st_size != getattr(source_stat, "st_size", None)
            or current_stat.st_mtime_ns != getattr(source_stat, "st_mtime_ns", None)
        )
        if stat_changed and _sha256_file(source_path) != source_sha256:
            return self._skip_source_removal(job, "file_changed_since_conversion")

        referenced_by = self._other_workflows_requiring(job)
        if referenced_by:
            return self._skip_source_removal(job, {"referenced_by": referenced_by})

        delete_model = getattr(self.model_inventory_service, "delete_model", None)
        if not callable(delete_model):
            return self._skip_source_removal(job, "deletion_unavailable")
        try:
            delete_model(key)
        except Exception as exc:
            return self._skip_source_removal(job, f"delete_failed: {exc}")
        self._log(
            "info",
            "Original FP8 model removed after conversion",
            workflow_id=job.workflow_id,
            details={"folder": job.folder, "filename": job.filename},
        )
        return True, None

    def _skip_source_removal(
        self,
        job: _Fp8ConversionJob,
        reason: object,
    ) -> tuple[bool, str | None]:
        self._log(
            "info",
            "Skipped removing original FP8 model",
            workflow_id=job.workflow_id,
            details={"folder": job.folder, "filename": job.filename, "reason": reason},
        )
        return False, reason if isinstance(reason, str) else json.dumps(reason)

    def _other_workflows_requiring(self, job: _Fp8ConversionJob) -> list[str]:
        workflow_loader = getattr(self.engine_service, "workflow_loader", None)
        if workflow_loader is None:
            return []
        referencing: list[str] = []
        try:
            packages = workflow_loader.list_packages()
        except Exception:
            return []
        for package in packages:
            if package.metadata.id == job.workflow_id:
                continue
            requires = any(
                model.folder == job.folder and model.filename == job.filename
                for model in package.required_models
            )
            if not requires:
                continue
            if self._workflow_has_usable_override(package.metadata.id, job.folder, job.filename):
                continue
            referencing.append(package.metadata.id)
        return referencing

    def _workflow_has_usable_override(self, workflow_id: str, folder: str, filename: str) -> bool:
        """Another workflow only stops needing the fp8 source once its own
        override exists AND the replacement file is actually present."""
        for override in self.override_store.overrides_for(workflow_id):
            if (override.folder, override.source_filename) != (folder, filename):
                continue
            replacement = self._noofy_models_dir() / folder / override.replacement_filename
            if replacement.is_file():
                return True
        return False

    # ---- helpers ----------------------------------------------------------

    def _ensure_mps_platform(self) -> None:
        if self.mps_execution_active is not None and not self.mps_execution_active():
            raise ValueError(
                "FP8 model conversion is only available on Apple Silicon Macs "
                "running the MPS backend."
            )

    def _required_model(self, workflow_id: str, folder: str, filename: str) -> RequiredModel:
        workflow_loader = getattr(self.engine_service, "workflow_loader", None)
        if workflow_loader is None:
            raise RuntimeError("Workflow loader is unavailable.")
        package = workflow_loader.get_package(workflow_id)
        for model in package.required_models:
            if model.folder == folder and model.filename == filename:
                return model
        raise KeyError(f"Workflow does not require model: {folder}/{filename}")

    def _resolve_source_path(self, model: RequiredModel) -> Path | None:
        availability = getattr(self.engine_service, "model_availability_service", None)
        resolver = getattr(availability, "resolve_local_model_path", None)
        if not callable(resolver):
            return None
        return resolver(model)

    def _noofy_models_dir(self) -> Path:
        availability = getattr(self.engine_service, "model_availability_service", None)
        return Path(getattr(availability, "noofy_models_dir"))

    def _check_disk_space(self, job: _Fp8ConversionJob, source_size: int, target_dir: Path) -> None:
        try:
            free_bytes = shutil.disk_usage(target_dir).free
        except OSError:
            return
        if free_bytes < source_size * 2.2:
            job.error_code = "not_enough_disk_space"
            raise RuntimeError(
                "Not enough free disk space to convert this model. "
                f"About {source_size * 2.2 / 1e9:.1f} GB is needed."
            )

    def _refresh_model_summary(self, workflow_id: str) -> RequiredModelSummary | None:
        summary_provider = getattr(self.engine_service, "model_availability_summary", None)
        if not callable(summary_provider):
            return None
        try:
            return summary_provider(workflow_id)
        except Exception:
            return None

    def _mark_canceled(self, job: _Fp8ConversionJob) -> None:
        job.status = "canceled"
        job.user_facing_message = "Conversion canceled."
        self._log(
            "info",
            "FP8 model conversion canceled",
            workflow_id=job.workflow_id,
            details={"job_id": job.job_id, "folder": job.folder, "filename": job.filename},
        )

    def _sweep_orphaned_partials(self) -> None:
        try:
            models_dir = self._noofy_models_dir()
        except Exception:
            return
        if not models_dir.is_dir():
            return
        try:
            for partial in models_dir.rglob(f"*{CONVERTED_FOR_MAC_SUFFIX}*.part"):
                _unlink_quietly(partial)
        except OSError:
            return

    def _status(self, job: _Fp8ConversionJob) -> Fp8ConversionJobStatus:
        return Fp8ConversionJobStatus(
            job_id=job.job_id,
            workflow_id=job.workflow_id,
            folder=job.folder,
            filename=job.filename,
            status=job.status,
            percent=job.percent,
            user_facing_message=job.user_facing_message,
            error_code=job.error_code,
            converted_filename=job.converted_filename,
            target_dtype=job.target_dtype,
            source_removed=job.source_removed,
            source_removal_skipped_reason=job.source_removal_skipped_reason,
            model_summary=job.model_summary,
        )

    def _log(self, level: str, message: str, *, workflow_id: str, details: dict) -> None:
        if self.log_store is None:
            return
        self.log_store.add(level, message, _LOG_SOURCE, workflow_id=workflow_id, details=details)


def _converted_filename(filename: str) -> str:
    path = Path(filename)
    return f"{path.stem}{CONVERTED_FOR_MAC_SUFFIX}{path.suffix}"


def _filename_from_download_url(url: str) -> str:
    """Validate a user-provided model link and derive the target filename."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("Enter a valid https:// download link.")
    filename = urllib.parse.unquote(Path(parts.path).name)
    if not filename or Path(filename).suffix.casefold() not in _ALTERNATIVE_MODEL_SUFFIXES:
        raise ValueError("The link must point directly to a .safetensors model file.")
    if Path(filename).name != filename or filename.startswith("."):
        raise ValueError("The link points to an invalid model filename.")
    return filename


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
