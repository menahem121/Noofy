from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx

from app.artifacts import AssetOwnership, ModelVerificationLevel
from app.core.config import settings
from app.diagnostics import DiagnosticsSink
from app.engine.models import (
    ModelDownloadSummary,
    RequiredModelAvailability,
    RequiredModelSummary,
)
from app.settings.api_keys import (
    ApiKeyProvider,
    CredentialStoreUnavailable,
    KeyringCredentialStore,
)
from app.workflows.model_grouping import (
    ModelGroup,
    apply_group_metadata,
    group_required_models,
    required_model_reference_id,
)
from app.workflows.model_identity_store import (
    LocalModelIdentityContext,
    LocalModelIdentityStore,
)
from app.workflows.package import RequiredModel, WorkflowPackage

DISK_SPACE_SAFETY_MARGIN_BYTES = 512 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1 << 20
DEFAULT_MODEL_DOWNLOAD_CONCURRENCY = 3
DEFAULT_MODEL_VERIFICATION_CONCURRENCY = 3
# Network/remote filesystem types where parallel full-file hashing tends to hurt
# (high latency, flaky I/O). Verification is clamped to serial on these roots.
NETWORK_VERIFICATION_FILESYSTEM_TYPES = frozenset(
    {
        "nfs",
        "nfs4",
        "cifs",
        "smbfs",
        "smb3",
        "afpfs",
        "ncpfs",
        "9p",
        "sshfs",
    }
)
PROVIDER_SEARCH_LIMIT = 20
HUGGING_FACE_SEARCH_TERM_LIMIT = 6
HUGGING_FACE_REPOS_PER_SEARCH_TERM = 4
HUGGING_FACE_REPO_INSPECTION_LIMIT = 8
PROVIDER_AUTH_REQUIRED_MESSAGE = (
    "This model source requires an API key for the provider account that can access it."
)
ACTIVE_DOWNLOAD_TRANSACTION_STATUSES = {"downloading", "verifying", "placing"}


@dataclass
class VerifyHashMetrics:
    """Thread-safe accumulator for SHA-256 verification work during one job.

    Threaded through ``summarize``/``_cached_sha256_file`` so a verification job
    can report cache effectiveness and how many bytes were actually hashed.
    """

    cache_hits: int = 0
    cache_misses: int = 0
    bytes_hashed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_cache_hit(self) -> None:
        with self._lock:
            self.cache_hits += 1

    def record_cache_miss(self, *, bytes_hashed: int) -> None:
        with self._lock:
            self.cache_misses += 1
            self.bytes_hashed += max(0, bytes_hashed)


class ModelAvailabilityError(RuntimeError):
    """Raised when model availability or downloads cannot be completed."""


class ProviderAuthenticationRequired(ModelAvailabilityError):
    """Raised when a provider reports authentication is required."""


class ProviderAccessDenied(ModelAvailabilityError):
    """Raised when a provider denies access to a model file."""


class ProviderRateLimited(ModelAvailabilityError):
    """Raised when a provider asks Noofy to retry later."""


class ModelDownloadCanceled(ModelAvailabilityError):
    """Raised when a model download job is canceled."""


@dataclass(frozen=True)
class ModelDownloadFailure:
    status: str
    status_label: str
    message: str


@dataclass(frozen=True)
class _PendingModelDownload:
    model: RequiredModel
    model_index: int


@dataclass(frozen=True)
class _ModelDownloadOutcome:
    requirement_id: str
    model_index: int
    downloaded: bool = False
    failure: ModelDownloadFailure | None = None
    canceled: bool = False


@dataclass
class ModelDownloadTransaction:
    download_id: str
    transaction_dir: Path
    part_path: Path
    state_path: Path
    model: RequiredModel

    def write_state(
        self,
        *,
        status: str,
        source_url: str | None = None,
        provider: str | None = None,
        bytes_downloaded: int | None = None,
    ) -> None:
        now = _utc_now_iso()
        previous = _read_json_object(self.state_path)
        started_at = previous.get("started_at") if isinstance(previous.get("started_at"), str) else now
        payload: dict[str, object | None] = {
            "download_id": self.download_id,
            "source_url": _redact_url_secret(source_url) if source_url else None,
            "provider": provider,
            "target_folder": self.model.folder,
            "target_filename": self.model.filename,
            "expected_size": self.model.size_bytes,
            "expected_sha256": _model_sha256(self.model),
            "status": status,
            "started_at": started_at,
            "updated_at": now,
        }
        if bytes_downloaded is not None:
            payload["bytes_downloaded"] = bytes_downloaded
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


ApiKeyResolver = Callable[[ApiKeyProvider], str | None]
ProviderFetchJson = Callable[
    [str, str, dict[str, str], dict[str, str]], Awaitable[object]
]
ModelDownloadProgressCallback = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class ProviderModelCandidate:
    provider: str
    download_url: str
    filename: str
    size_bytes: int | None = None
    sha256: str | None = None
    source_trust: str = "provider_metadata"

    def strength_for(self, model: RequiredModel) -> int:
        expected_sha = _model_sha256(model)
        if expected_sha is not None:
            if self.sha256 is None or self.sha256 != expected_sha:
                return 0
            size_matches = (
                model.size_bytes is not None
                and self.size_bytes is not None
                and self.size_bytes == model.size_bytes
            )
            return 4 if size_matches else 3

        filename_matches = self.filename.casefold() == model.filename.casefold()
        if not filename_matches:
            return 0
        size_matches = (
            model.size_bytes is not None
            and self.size_bytes is not None
            and self.size_bytes == model.size_bytes
        )
        if size_matches:
            return 2
        return 0


class ProviderModelResolver:
    def __init__(
        self,
        *,
        api_key_resolver: ApiKeyResolver | None = None,
        fetch_json: ProviderFetchJson | None = None,
        log_store: DiagnosticsSink | None = None,
    ) -> None:
        self.api_key_resolver = api_key_resolver or _default_api_key_resolver
        self.fetch_json = fetch_json or _fetch_json
        self.log_store = log_store

    async def resolve(self, model: RequiredModel) -> list[str]:
        if not _provider_resolvable(model):
            self._record_provider_step(
                model,
                provider="provider",
                step="skipped",
                candidates=[],
                note="insufficient package metadata for provider resolution",
            )
            return []
        candidates: list[ProviderModelCandidate] = []
        if _model_sha256(model) is not None:
            civitai_by_hash = await self._search_civitai_by_hash(model)
            self._record_provider_step(
                model,
                provider="civitai",
                step="by_hash",
                candidates=civitai_by_hash,
            )
            candidates.extend(civitai_by_hash)

        try:
            hf = await self._search_hugging_face(model)
        except (ProviderAuthenticationRequired, ProviderRateLimited):
            selected = _reliable_candidates(model, candidates)
            if selected:
                return [candidate.download_url for candidate in selected]
            raise
        self._record_provider_step(
            model,
            provider="hugging_face",
            step="model_search",
            candidates=hf,
        )
        candidates.extend(hf)
        selected = _reliable_candidates(model, candidates)
        if selected:
            return [candidate.download_url for candidate in selected]

        civitai_query = await self._search_civitai_query(model)
        self._record_provider_step(
            model,
            provider="civitai",
            step="query_search",
            candidates=civitai_query,
        )
        candidates.extend(civitai_query)

        selected = _reliable_candidates(model, candidates)
        if selected:
            return [candidate.download_url for candidate in selected]
        self._record_unresolved(model, candidates)
        return []

    async def _search_hugging_face(
        self, model: RequiredModel
    ) -> list[ProviderModelCandidate]:
        token = self._api_key("hugging_face")
        headers = _auth_headers(token)
        url = "https://huggingface.co/api/models"
        repo_ids: list[str] = []
        seen_repo_ids: set[str] = set()
        lightweight_candidates: list[ProviderModelCandidate] = []
        search_terms = _hugging_face_search_terms(model)
        inspected_repos = 0
        metadata_missing_repos = 0
        metadata_error_repos = 0
        for search_term in search_terms:
            try:
                data = await self.fetch_json(
                    "GET",
                    url,
                    {
                        "search": search_term,
                        "full": "true",
                        "limit": str(PROVIDER_SEARCH_LIMIT),
                    },
                    headers,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 403}:
                    raise ProviderAuthenticationRequired(
                        "A Hugging Face API key with access is needed for this model."
                    ) from exc
                if exc.response.status_code == 429:
                    raise ProviderRateLimited(
                        "Hugging Face rate limit reached; try again later."
                    ) from exc
                continue
            if not isinstance(data, list):
                continue
            added_for_term = 0
            for repo in data:
                if not isinstance(repo, dict):
                    continue
                repo_id = repo.get("modelId") or repo.get("id")
                if not isinstance(repo_id, str) or not repo_id:
                    continue
                lightweight_candidates.extend(
                    _hugging_face_candidates_from_repo_record(model, repo_id, repo)
                )
                if repo_id in seen_repo_ids:
                    continue
                if added_for_term >= HUGGING_FACE_REPOS_PER_SEARCH_TERM:
                    continue
                if len(repo_ids) >= HUGGING_FACE_REPO_INSPECTION_LIMIT:
                    continue
                repo_ids.append(repo_id)
                seen_repo_ids.add(repo_id)
                added_for_term += 1

        candidates = list(lightweight_candidates)
        for repo_id in repo_ids[:HUGGING_FACE_REPO_INSPECTION_LIMIT]:
            inspected_repos += 1
            try:
                repo_data = await self.fetch_json(
                    "GET",
                    _hugging_face_api_model_url(repo_id),
                    {"blobs": "true"},
                    headers,
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {401, 403}:
                    raise ProviderAuthenticationRequired(
                        "A Hugging Face API key with access is needed for this model."
                    ) from exc
                if exc.response.status_code == 429:
                    raise ProviderRateLimited(
                        "Hugging Face rate limit reached; try again later."
                    ) from exc
                metadata_error_repos += 1
                continue
            if not isinstance(repo_data, dict):
                metadata_missing_repos += 1
                continue
            repo_candidates = _hugging_face_candidates_from_repo_record(
                model, repo_id, repo_data
            )
            if not repo_candidates:
                metadata_missing_repos += 1
            candidates.extend(repo_candidates)

        if self.log_store is not None:
            self.log_store.add(
                "info",
                "Hugging Face repo metadata inspection completed",
                "workflow.models",
                details={
                    "filename": model.filename,
                    "folder": model.folder,
                    "search_term_count": len(search_terms),
                    "candidate_repo_count": len(repo_ids),
                    "inspected_repo_count": inspected_repos,
                    "inspection_limit": HUGGING_FACE_REPO_INSPECTION_LIMIT,
                    "metadata_missing_repo_count": metadata_missing_repos,
                    "metadata_error_repo_count": metadata_error_repos,
                },
            )
        return _dedupe_provider_candidates(candidates)

    async def _search_civitai_by_hash(
        self, model: RequiredModel
    ) -> list[ProviderModelCandidate]:
        expected_sha = _model_sha256(model)
        if expected_sha is None:
            return []
        token = self._api_key("civitai")
        headers = _auth_headers(token)
        url = f"https://civitai.com/api/v1/model-versions/by-hash/{expected_sha}"
        try:
            data = await self.fetch_json("GET", url, {}, headers)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ProviderAuthenticationRequired(
                    "A Civitai API key with access is needed for this model."
                ) from exc
            if exc.response.status_code == 404:
                self._record_provider_status(
                    model,
                    provider="civitai",
                    step="by_hash",
                    status="not_found",
                )
                return []
            if exc.response.status_code == 429:
                raise ProviderRateLimited(
                    "Civitai rate limit reached; try again later."
                ) from exc
            return []
        if not isinstance(data, dict):
            return []
        files = data.get("files")
        if not isinstance(files, list):
            return []
        candidates: list[ProviderModelCandidate] = []
        for file_record in files:
            candidate = _civitai_file_candidate(model, file_record)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def _search_civitai_query(
        self, model: RequiredModel
    ) -> list[ProviderModelCandidate]:
        token = self._api_key("civitai")
        headers = _auth_headers(token)
        url = "https://civitai.com/api/v1/models"
        try:
            data = await self.fetch_json(
                "GET",
                url,
                {"query": model.filename, "limit": str(PROVIDER_SEARCH_LIMIT)},
                headers,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ProviderAuthenticationRequired(
                    "A Civitai API key with access is needed for this model."
                ) from exc
            if exc.response.status_code == 429:
                raise ProviderRateLimited(
                    "Civitai rate limit reached; try again later."
                ) from exc
            return []
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        candidates: list[ProviderModelCandidate] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            versions = item.get("modelVersions")
            if not isinstance(versions, list):
                continue
            for version in versions:
                if not isinstance(version, dict):
                    continue
                files = version.get("files")
                if not isinstance(files, list):
                    continue
                for file_record in files:
                    candidate = _civitai_file_candidate(model, file_record)
                    if candidate is not None:
                        candidates.append(candidate)
        return candidates

    def _api_key(self, provider: ApiKeyProvider) -> str | None:
        return self.api_key_resolver(provider)

    def sanitize_message(self, message: str) -> str:
        redacted = _redact_common_secret_patterns(message)
        for provider in ("hugging_face", "civitai"):
            token = self._api_key(provider)
            if token:
                redacted = redacted.replace(token, "[redacted]")
        return redacted

    def auth_headers_for_provider(self, provider: str) -> dict[str, str]:
        if provider not in {"hugging_face", "civitai"}:
            return {}
        return _auth_headers(self._api_key(provider))  # type: ignore[arg-type]

    def _record_unresolved(
        self, model: RequiredModel, candidates: list[ProviderModelCandidate]
    ) -> None:
        if self.log_store is None or not candidates:
            return
        self.log_store.add(
            "info",
            "Provider model search returned no reliable match",
            "workflow.models",
            details={
                "folder": model.folder,
                "filename": model.filename,
                "candidate_count": len(candidates),
            },
        )

    def _record_provider_step(
        self,
        model: RequiredModel,
        *,
        provider: str,
        step: str,
        candidates: list[ProviderModelCandidate],
        note: str | None = None,
    ) -> None:
        if self.log_store is None:
            return
        reliable = _reliable_candidates(model, candidates)
        self.log_store.add(
            "info",
            "Provider model resolver step completed",
            "workflow.models",
            details={
                "provider": provider,
                "step": step,
                "folder": model.folder,
                "filename": model.filename,
                "expected_size_present": model.size_bytes is not None,
                "expected_sha256_present": _model_sha256(model) is not None,
                "candidate_count": len(candidates),
                "reliable_candidate_count": len(reliable),
                "missing_size_metadata_count": sum(
                    candidate.size_bytes is None for candidate in candidates
                ),
                "missing_sha256_metadata_count": sum(
                    candidate.sha256 is None for candidate in candidates
                ),
                **({"note": note} if note else {}),
            },
        )

    def _record_provider_status(
        self,
        model: RequiredModel,
        *,
        provider: str,
        step: str,
        status: str,
    ) -> None:
        if self.log_store is None:
            return
        self.log_store.add(
            "info",
            "Provider model resolver status",
            "workflow.models",
            details={
                "provider": provider,
                "step": step,
                "status": status,
                "folder": model.folder,
                "filename": model.filename,
                "expected_sha256_present": _model_sha256(model) is not None,
            },
        )


class ModelAvailabilityService:
    def __init__(
        self,
        *,
        model_roots: list[Path],
        noofy_models_dir: Path,
        log_store: DiagnosticsSink,
        provider_resolver: ProviderModelResolver | None = None,
        local_model_identity_store: LocalModelIdentityStore | None = None,
        max_parallel_downloads: int = DEFAULT_MODEL_DOWNLOAD_CONCURRENCY,
    ) -> None:
        self.model_roots = model_roots
        self.noofy_models_dir = noofy_models_dir
        self.log_store = log_store
        self.provider_resolver = provider_resolver or ProviderModelResolver(
            log_store=log_store
        )
        self.local_model_identity_store = local_model_identity_store
        self.max_parallel_downloads = max(1, max_parallel_downloads)

    def configure_model_roots(
        self,
        *,
        model_roots: list[Path],
        noofy_models_dir: Path,
    ) -> None:
        self.model_roots = model_roots
        self.noofy_models_dir = noofy_models_dir

    def select_verification_concurrency(self, model_count: int) -> tuple[int, str]:
        """Pick a safe concurrency for parallel model verification.

        Returns ``(effective_concurrency, downgrade_reason)`` where the reason is one of
        ``"none"``, ``"single_model"``, ``"config_override"``, ``"network_fs"``, or
        ``"rotational"``. Parallel full-file hashing helps on SSD/NVMe but can hurt on
        slow rotational, removable, or network mounts, so the effective value is clamped.
        """
        if model_count <= 1:
            return 1, "single_model"
        try:
            configured = int(settings.model_verification_max_concurrency)
        except (TypeError, ValueError):
            configured = DEFAULT_MODEL_VERIFICATION_CONCURRENCY
        if configured <= 1:
            return 1, "config_override"
        downgrade_reason = _verification_filesystem_downgrade_reason(self._safe_model_roots())
        if downgrade_reason is not None:
            return 1, downgrade_reason
        cpu_cap = os.cpu_count() or 1
        effective = max(1, min(configured, cpu_cap, model_count))
        return effective, "none"

    def cleanup_interrupted_downloads(self) -> int:
        downloads_dir = self.noofy_models_dir / ".downloads"
        if not downloads_dir.is_dir():
            return 0
        try:
            self._ensure_path_inside_noofy_models(downloads_dir)
        except ModelAvailabilityError:
            return 0
        cleaned = 0
        for transaction_dir in downloads_dir.iterdir():
            if not transaction_dir.is_dir():
                continue
            state = _read_json_object(transaction_dir / "download-state.json")
            if state.get("status") not in ACTIVE_DOWNLOAD_TRANSACTION_STATUSES:
                continue
            shutil.rmtree(transaction_dir, ignore_errors=True)
            cleaned += 1
        if cleaned:
            self.log_store.add(
                "info",
                "Cleaned interrupted model download transactions",
                "workflow.models",
                details={"cleaned_count": cleaned},
            )
        return cleaned

    def summarize(
        self,
        package: WorkflowPackage,
        *,
        deep_search: bool = True,
        verify_hashes: bool = True,
        metrics: VerifyHashMetrics | None = None,
    ) -> RequiredModelSummary:
        models = [
            self._availability_for_group(
                group,
                deep_search=deep_search,
                verify_hashes=verify_hashes,
                metrics=metrics,
            )
            for group in group_required_models(package.required_models)
        ]
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

    async def download_missing(
        self,
        package: WorkflowPackage,
        *,
        progress_callback: ModelDownloadProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> ModelDownloadSummary:
        # Group by physical file so the same blob is checked, resolved, and downloaded
        # once even when several graph nodes reference it. ``summarize`` groups in the
        # same order, so the grouped availabilities line up with these groups.
        groups = group_required_models(package.required_models)
        before = self.summarize(package)
        downloaded_count = 0
        failed_count = 0
        failures: dict[str, ModelDownloadFailure] = {}
        canceled = False
        missing_groups = [
            group
            for group, availability in zip(groups, before.models, strict=True)
            if availability.status == "missing"
        ]
        downloadable_models = [
            _PendingModelDownload(
                model=group.representative,
                model_index=model_index,
            )
            for model_index, group in enumerate(missing_groups, start=1)
        ]
        total_models = len(downloadable_models)

        outcomes = await self._download_missing_models(
            package,
            downloadable_models,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
        )
        for outcome in outcomes:
            if outcome.failure is not None:
                failures[outcome.requirement_id] = outcome.failure
            if outcome.canceled:
                canceled = True
            elif outcome.failure is not None:
                failed_count += 1
            if outcome.downloaded:
                downloaded_count += 1

        after = self.summarize(package)
        if failures:
            after = after.model_copy(
                update={
                    "models": [
                        item.model_copy(
                            update=failures[item.requirement_id].__dict__
                        )
                        if item.requirement_id in failures
                        else item
                        for item in after.models
                    ]
                }
            )

        return ModelDownloadSummary(
            workflow_id=package.metadata.id,
            status=(
                "canceled"
                if canceled
                else ("completed_with_errors" if failed_count else "completed")
            ),
            user_facing_message=(
                "Model download was canceled. Completed downloads were kept and the partial download was cleaned up safely."
                if canceled
                else (
                    "Some downloads failed."
                    if failed_count
                    else "Model download check finished."
                )
            ),
            downloaded_count=downloaded_count,
            failed_count=failed_count,
            model_summary=after,
        )

    async def _download_missing_models(
        self,
        package: WorkflowPackage,
        items: list[_PendingModelDownload],
        *,
        progress_callback: ModelDownloadProgressCallback | None,
        cancel_event: asyncio.Event | None,
    ) -> list[_ModelDownloadOutcome]:
        if not items:
            return []

        total_models = len(items)
        preflight_failure = self._parallel_download_preflight_failure(items)
        if preflight_failure is not None:
            outcomes: list[_ModelDownloadOutcome] = []
            for item in items:
                if _model_needs_download_disk_preflight(item.model):
                    outcomes.append(
                        self._failed_download_outcome(
                            item,
                            total_models=total_models,
                            progress_callback=progress_callback,
                            failure=preflight_failure,
                        )
                    )
                    continue
                outcomes.append(
                    await self._download_missing_model(
                        package,
                        item,
                        total_models=total_models,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                )
            return outcomes

        concurrency = min(self.max_parallel_downloads, total_models)
        self.log_store.add(
            "info",
            "Starting required model downloads",
            "workflow.models",
            workflow_id=package.metadata.id,
            details={
                "model_count": total_models,
                "max_parallel_downloads": concurrency,
            },
        )
        outcomes: list[_ModelDownloadOutcome] = []
        completed_indices: set[int] = set()
        target_locks = {
            _model_download_target_key(item.model): asyncio.Lock()
            for item in items
        }
        next_item_index = 0

        async def worker() -> None:
            nonlocal next_item_index
            while next_item_index < len(items):
                if cancel_event is not None and cancel_event.is_set():
                    return
                item = items[next_item_index]
                next_item_index += 1
                async with target_locks[_model_download_target_key(item.model)]:
                    outcome = await self._download_missing_model(
                        package,
                        item,
                        total_models=total_models,
                        progress_callback=progress_callback,
                        cancel_event=cancel_event,
                    )
                completed_indices.add(item.model_index)
                outcomes.append(outcome)
                if outcome.canceled and cancel_event is not None:
                    cancel_event.set()

        workers = [asyncio.create_task(worker()) for _ in range(concurrency)]
        try:
            await asyncio.gather(*workers)
        finally:
            for task in workers:
                if not task.done():
                    task.cancel()

        if cancel_event is not None and cancel_event.is_set():
            for item in items:
                if item.model_index in completed_indices:
                    continue
                outcomes.append(
                    self._canceled_download_outcome(
                        item,
                        total_models=total_models,
                        progress_callback=progress_callback,
                    )
                )
        return outcomes

    async def _download_missing_model(
        self,
        package: WorkflowPackage,
        item: _PendingModelDownload,
        *,
        total_models: int,
        progress_callback: ModelDownloadProgressCallback | None,
        cancel_event: asyncio.Event | None,
    ) -> _ModelDownloadOutcome:
        model = item.model
        requirement_id = _requirement_id(model)
        if cancel_event is not None and cancel_event.is_set():
            return self._canceled_download_outcome(
                item,
                total_models=total_models,
                progress_callback=progress_callback,
            )

        _emit_model_download_progress(
            progress_callback,
            model=model,
            status="downloading",
            model_index=item.model_index,
            total_models=total_models,
            bytes_downloaded=0,
            total_bytes=model.size_bytes,
        )
        try:
            downloaded = await self._download_model(
                model,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
                model_index=item.model_index,
                total_models=total_models,
            )
            if downloaded:
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="succeeded",
                    model_index=item.model_index,
                    total_models=total_models,
                    bytes_downloaded=model.size_bytes,
                    total_bytes=model.size_bytes,
                )
                return _ModelDownloadOutcome(
                    requirement_id=requirement_id,
                    model_index=item.model_index,
                    downloaded=downloaded,
                )

            current = self._availability_for(model, deep_search=False, verify_hashes=True)
            if current.status == "available":
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="succeeded",
                    model_index=item.model_index,
                    total_models=total_models,
                    bytes_downloaded=model.size_bytes,
                    total_bytes=model.size_bytes,
                )
                return _ModelDownloadOutcome(
                    requirement_id=requirement_id,
                    model_index=item.model_index,
                )

            failure = _needs_manual_download_failure()
            _emit_model_download_progress(
                progress_callback,
                model=model,
                status=failure.status,
                model_index=item.model_index,
                total_models=total_models,
                message=failure.message,
            )
            return _ModelDownloadOutcome(
                requirement_id=requirement_id,
                model_index=item.model_index,
                failure=failure,
            )
        except ProviderAuthenticationRequired as exc:
            failure = ModelDownloadFailure(
                status="authentication_required",
                status_label="Authentication required",
                message=(
                    self.provider_resolver.sanitize_message(str(exc))
                    or PROVIDER_AUTH_REQUIRED_MESSAGE
                )
                + " The partial download was cleaned up safely. You can retry after updating settings, continue importing, or cancel.",
            )
            self.log_store.add(
                "warning",
                "Required model provider authentication needed",
                "workflow.models",
                workflow_id=package.metadata.id,
                details={
                    "folder": model.folder,
                    "filename": model.filename,
                },
            )
            return self._failed_download_outcome(
                item,
                total_models=total_models,
                progress_callback=progress_callback,
                failure=failure,
            )
        except ProviderAccessDenied as exc:
            failure = ModelDownloadFailure(
                status="access_denied",
                status_label="Access denied",
                message=self.provider_resolver.sanitize_message(str(exc))
                + " The partial download was cleaned up safely.",
            )
            self.log_store.add(
                "warning",
                "Required model provider access denied",
                "workflow.models",
                workflow_id=package.metadata.id,
                details={
                    "folder": model.folder,
                    "filename": model.filename,
                },
            )
            return self._failed_download_outcome(
                item,
                total_models=total_models,
                progress_callback=progress_callback,
                failure=failure,
            )
        except ProviderRateLimited as exc:
            failure = ModelDownloadFailure(
                status="rate_limited",
                status_label="Rate limited",
                message=self.provider_resolver.sanitize_message(str(exc))
                + " The partial download was cleaned up safely. You can retry later, continue importing, or cancel.",
            )
            self.log_store.add(
                "warning",
                "Required model provider rate limit reached",
                "workflow.models",
                workflow_id=package.metadata.id,
                details={
                    "folder": model.folder,
                    "filename": model.filename,
                },
            )
            return self._failed_download_outcome(
                item,
                total_models=total_models,
                progress_callback=progress_callback,
                failure=failure,
            )
        except ModelDownloadCanceled:
            return self._canceled_download_outcome(
                item,
                total_models=total_models,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            safe_error = self.provider_resolver.sanitize_message(str(exc))
            failure = _download_failure_for_error(safe_error)
            self.log_store.add(
                "warning",
                "Required model download failed",
                "workflow.models",
                workflow_id=package.metadata.id,
                details={
                    "folder": model.folder,
                    "filename": model.filename,
                    "error": safe_error,
                },
            )
            return self._failed_download_outcome(
                item,
                total_models=total_models,
                progress_callback=progress_callback,
                failure=failure,
            )

    def _parallel_download_preflight_failure(
        self,
        items: list[_PendingModelDownload],
    ) -> ModelDownloadFailure | None:
        if len(items) <= 1:
            return None
        required_bytes_by_target: dict[tuple[str, str], int] = {}
        for item in items:
            model = item.model
            if not _model_needs_download_disk_preflight(model):
                continue
            target_key = _model_download_target_key(model)
            required_bytes_by_target[target_key] = max(
                required_bytes_by_target.get(target_key, 0),
                model.size_bytes,
            )
        if not required_bytes_by_target:
            return None
        try:
            self._validate_owned_model_root()
            self._ensure_disk_space(sum(required_bytes_by_target.values()))
        except Exception as exc:
            safe_error = self.provider_resolver.sanitize_message(str(exc))
            self.log_store.add(
                "warning",
                "Required model download preflight failed",
                "workflow.models",
                details={
                    "model_count": len(items),
                    "required_bytes": sum(required_bytes_by_target.values()),
                    "error": safe_error,
                },
            )
            return _download_failure_for_error(safe_error)
        return None

    def _failed_download_outcome(
        self,
        item: _PendingModelDownload,
        *,
        total_models: int,
        progress_callback: ModelDownloadProgressCallback | None,
        failure: ModelDownloadFailure,
    ) -> _ModelDownloadOutcome:
        _emit_model_download_progress(
            progress_callback,
            model=item.model,
            status=failure.status,
            model_index=item.model_index,
            total_models=total_models,
            message=failure.message,
        )
        return _ModelDownloadOutcome(
            requirement_id=_requirement_id(item.model),
            model_index=item.model_index,
            failure=failure,
        )

    def _canceled_download_outcome(
        self,
        item: _PendingModelDownload,
        *,
        total_models: int,
        progress_callback: ModelDownloadProgressCallback | None,
    ) -> _ModelDownloadOutcome:
        failure = _canceled_download_failure()
        _emit_model_download_progress(
            progress_callback,
            model=item.model,
            status="canceled",
            model_index=item.model_index,
            total_models=total_models,
            message="Download canceled.",
        )
        return _ModelDownloadOutcome(
            requirement_id=_requirement_id(item.model),
            model_index=item.model_index,
            failure=failure,
            canceled=True,
        )

    def _availability_for_group(
        self,
        group: ModelGroup,
        *,
        deep_search: bool,
        verify_hashes: bool,
        metrics: VerifyHashMetrics | None = None,
    ) -> RequiredModelAvailability:
        """Check one physical file (via the group's representative) and tag references.

        Availability is identical for every node that loads the same file, so it is
        computed once and the full node-reference list is overlaid for the UI.
        """
        availability = self._availability_for(
            group.representative,
            deep_search=deep_search,
            verify_hashes=verify_hashes,
            metrics=metrics,
        )
        return apply_group_metadata(availability, group)

    def _availability_for(
        self,
        model: RequiredModel,
        *,
        deep_search: bool,
        verify_hashes: bool,
        metrics: VerifyHashMetrics | None = None,
    ) -> RequiredModelAvailability:
        source_urls = _source_urls(model)
        candidates = self._local_candidates(model, deep_search=deep_search)
        source_availability = (
            "known"
            if source_urls
            else ("resolvable" if _provider_resolvable(model) else "unknown")
        )
        base = {
            "requirement_id": _requirement_id(model),
            "node_id": model.node_id,
            "node_type": model.node_type,
            "input_name": model.input_name,
            "filename": model.filename,
            "model_type": model.model_type,
            "folder": model.folder,
            "verification_level": model.verification_level,
            "size_bytes": model.size_bytes,
            "source_urls": [_redact_url_secret(url) for url in source_urls],
            "source_availability": source_availability,
        }
        for candidate, root in candidates:
            status = self._candidate_status(
                model,
                candidate,
                root=root,
                verify_hashes=verify_hashes,
                metrics=metrics,
            )
            if status == "available":
                # The status check above already hashed (and cached) this file when
                # needed, so this lookup is a cache hit; pass no metrics to avoid
                # double-counting the same file.
                matched_sha256 = (
                    self._cached_sha256_file(candidate, root=root)
                    if verify_hashes
                    and model.verification_level is not ModelVerificationLevel.FILENAME_ONLY
                    else None
                )
                return RequiredModelAvailability(
                    **base,
                    status="available",
                    status_label="Available",
                    asset_ownership=self._ownership_for_root(root),
                    source_path=str(candidate),
                    matched_root=str(root),
                    matched_sha256=matched_sha256,
                    matched_size_bytes=candidate.stat().st_size,
                )

        if candidates:
            candidate, root = candidates[0]
            return RequiredModelAvailability(
                **base,
                status="possible_match",
                status_label="Possible match",
                asset_ownership=self._ownership_for_root(root),
                source_path=str(candidate),
                matched_root=str(root),
                matched_size_bytes=candidate.stat().st_size,
                message="A local file with this name was found, but Noofy needs stronger verification before using it.",
            )

        if (source_urls or _provider_resolvable(model)) and model.size_bytes is not None:
            return RequiredModelAvailability(
                **base,
                status="missing",
                status_label="Missing",
                asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
                message="Noofy can try to resolve and download this model before the workflow runs.",
            )
        return RequiredModelAvailability(
            **base,
            status="needs_manual_download",
            status_label="Needs manual download",
            asset_ownership=AssetOwnership.EXTERNAL_REFERENCE,
            message="Noofy does not have enough source information to download this model automatically.",
        )

    def _candidate_status(
        self,
        model: RequiredModel,
        path: Path,
        *,
        root: Path,
        verify_hashes: bool = True,
        metrics: VerifyHashMetrics | None = None,
    ) -> str:
        if not path.is_file():
            return "missing"
        size = path.stat().st_size
        if model.verification_level is ModelVerificationLevel.SHA256_SIZE:
            if model.size_bytes is None or model.checksum is None:
                return "possible_match"
            if size != model.size_bytes:
                return "possible_match"
            if not verify_hashes:
                return "possible_match"
            return "available" if self._cached_sha256_file(path, root=root, metrics=metrics) == _normalize_sha256(model.checksum) else "possible_match"
        if model.verification_level is ModelVerificationLevel.FILENAME_SIZE:
            return "available" if model.size_bytes is not None and size == model.size_bytes else "possible_match"
        return "possible_match"

    def _local_candidates(
        self,
        model: RequiredModel,
        *,
        deep_search: bool = True,
    ) -> list[tuple[Path, Path]]:
        candidates: list[tuple[Path, Path]] = []
        seen: set[Path] = set()
        for root in self._safe_model_roots():
            try:
                expected = _safe_join_model_path(root, model.folder, model.filename)
            except ModelAvailabilityError:
                continue
            if expected.is_file():
                resolved = expected.resolve(strict=False)
                if resolved not in seen:
                    candidates.append((expected, root))
                    seen.add(resolved)
            if not deep_search:
                continue
            try:
                search_root = _safe_join_model_folder(root, model.folder)
                if not search_root.is_dir():
                    continue
                for candidate in search_root.rglob(model.filename):
                    if candidate.name.endswith(".part") or not candidate.is_file():
                        continue
                    resolved = candidate.resolve(strict=False)
                    if resolved in seen:
                        continue
                    candidates.append((candidate, root))
                    seen.add(resolved)
            except OSError:
                continue
        return candidates

    async def _download_model(
        self,
        model: RequiredModel,
        *,
        progress_callback: ModelDownloadProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        model_index: int | None = None,
        total_models: int | None = None,
    ) -> bool:
        if cancel_event is not None and cancel_event.is_set():
            raise ModelDownloadCanceled("Download canceled.")
        if model.verification_level is ModelVerificationLevel.FILENAME_ONLY:
            return False
        if model.size_bytes is None or model.size_bytes <= 0:
            raise ModelAvailabilityError("Noofy needs a known file size before downloading this model.")
        urls = _prioritized_source_urls(_source_urls(model))
        if not urls:
            self.log_store.add(
                "info",
                "Required model has no explicit source URLs",
                "workflow.models",
                details={
                    "folder": model.folder,
                    "filename": model.filename,
                    "provider_resolvable": _provider_resolvable(model),
                    "expected_size_present": model.size_bytes is not None,
                    "expected_sha256_present": _model_sha256(model) is not None,
                },
            )
            urls = await self.provider_resolver.resolve(model)
        if not urls:
            self.log_store.add(
                "info",
                "Required model provider resolution found no reliable automatic source",
                "workflow.models",
                details={
                    "folder": model.folder,
                    "filename": model.filename,
                    "expected_size_present": model.size_bytes is not None,
                    "expected_sha256_present": _model_sha256(model) is not None,
                },
            )
            return False
        self._validate_owned_model_root()
        self._ensure_disk_space(model.size_bytes)
        final_path = _safe_join_model_path(self.noofy_models_dir, model.folder, model.filename)
        if final_path.exists():
            current = self._availability_for(model, deep_search=False, verify_hashes=True)
            if current.status == "available":
                return False
            raise ModelAvailabilityError(
                "A different file already exists at the target model location; Noofy will not overwrite it."
            )
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_path_inside_noofy_models(final_path)

        downloaded_sha256 = await self._download_verified_with_fallback(
            urls,
            model,
            final_path,
            progress_callback=progress_callback,
            cancel_event=cancel_event,
            model_index=model_index,
            total_models=total_models,
        )
        self.log_store.add(
            "info",
            "Required model downloaded",
            "workflow.models",
            details={
                "folder": model.folder,
                "filename": model.filename,
                "size_bytes": final_path.stat().st_size,
                "sha256": f"sha256:{downloaded_sha256}"
                if downloaded_sha256
                else None,
            },
        )
        return True

    def _begin_download_transaction(self, model: RequiredModel) -> ModelDownloadTransaction:
        downloads_dir = self.noofy_models_dir / ".downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_path_inside_noofy_models(downloads_dir)
        download_id = uuid.uuid4().hex
        transaction_dir = downloads_dir / download_id
        transaction_dir.mkdir(mode=0o700)
        self._ensure_path_inside_noofy_models(transaction_dir)
        transaction = ModelDownloadTransaction(
            download_id=download_id,
            transaction_dir=transaction_dir,
            part_path=transaction_dir / f"{Path(model.filename).name}.part",
            state_path=transaction_dir / "download-state.json",
            model=model,
        )
        transaction.write_state(status="downloading")
        return transaction

    async def _download_verified_with_fallback(
        self,
        urls: list[str],
        model: RequiredModel,
        final_path: Path,
        *,
        progress_callback: ModelDownloadProgressCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        model_index: int | None = None,
        total_models: int | None = None,
    ) -> str | None:
        last_error: Exception | None = None
        for index, url in enumerate(urls):
            transaction = self._begin_download_transaction(model)
            try:
                provider = _provider_from_url(url)
                transaction.write_state(
                    status="downloading",
                    source_url=url,
                    provider=provider,
                    bytes_downloaded=transaction.part_path.stat().st_size
                    if transaction.part_path.exists()
                    else 0,
                )

                def stream_progress(
                    bytes_downloaded: int,
                    total_bytes: int | None,
                ) -> None:
                    transaction.write_state(
                        status="downloading",
                        source_url=url,
                        provider=provider,
                        bytes_downloaded=bytes_downloaded,
                    )
                    _emit_model_download_progress(
                        progress_callback,
                        model=model,
                        status="downloading",
                        model_index=model_index,
                        total_models=total_models,
                        bytes_downloaded=bytes_downloaded,
                        total_bytes=total_bytes or model.size_bytes,
                    )

                headers = self.provider_resolver.auth_headers_for_provider(provider)
                if progress_callback is None and cancel_event is None:
                    await _stream_with_optional_headers(
                        url,
                        transaction.part_path,
                        headers=headers,
                    )
                else:
                    await _stream_with_optional_headers(
                        url,
                        transaction.part_path,
                        headers=headers,
                        progress_callback=stream_progress,
                        cancel_event=cancel_event,
                    )
                transaction.write_state(
                    status="downloading",
                    source_url=url,
                    provider=provider,
                    bytes_downloaded=transaction.part_path.stat().st_size,
                )
                transaction.write_state(
                    status="verifying",
                    bytes_downloaded=transaction.part_path.stat().st_size,
                )
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="verifying",
                    model_index=model_index,
                    total_models=total_models,
                    bytes_downloaded=transaction.part_path.stat().st_size,
                    total_bytes=model.size_bytes,
                )
                if cancel_event is not None and cancel_event.is_set():
                    raise ModelDownloadCanceled("Download canceled.")
                part_sha256 = self._verify_download(model, transaction.part_path)
                transaction.write_state(
                    status="placing",
                    bytes_downloaded=transaction.part_path.stat().st_size,
                )
                self._ensure_path_inside_noofy_models(final_path)
                os.replace(transaction.part_path, final_path)
                self._ensure_path_inside_noofy_models(final_path)
                try:
                    final_sha256 = self._verify_download(
                        model,
                        final_path,
                        known_sha256=part_sha256,
                    )
                    if final_sha256:
                        self._remember_cached_sha256(
                            final_path,
                            root=self.noofy_models_dir,
                            sha256=final_sha256,
                        )
                except Exception:
                    if final_path.exists():
                        final_path.unlink()
                    raise
                return final_sha256
            except ModelDownloadCanceled:
                if transaction.part_path.exists():
                    transaction.part_path.unlink()
                raise
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if transaction.part_path.exists():
                    transaction.part_path.unlink()
                status_code = exc.response.status_code
                if status_code == 401:
                    last_error = ProviderAuthenticationRequired(
                        "A provider API key is required or the saved key is invalid."
                    )
                    if index == len(urls) - 1:
                        raise last_error from exc
                    continue
                if status_code == 403:
                    last_error = ProviderAccessDenied(
                        "The provider denied access to this model file."
                    )
                    if index == len(urls) - 1:
                        raise last_error from exc
                    continue
                if status_code == 429:
                    last_error = ProviderRateLimited(
                        "The provider is rate limiting downloads; try again later."
                    )
                    if index == len(urls) - 1:
                        raise last_error from exc
                    continue
            except Exception as exc:
                last_error = exc
                if transaction.part_path.exists():
                    transaction.part_path.unlink()
            finally:
                self._cleanup_transaction(transaction.transaction_dir)
        if isinstance(
            last_error,
            (ProviderAuthenticationRequired, ProviderAccessDenied, ProviderRateLimited),
        ):
            raise last_error
        raise ModelAvailabilityError(f"All model sources failed: {last_error}")

    def _cleanup_transaction(self, transaction_dir: Path) -> None:
        if not transaction_dir.exists():
            return
        try:
            self._ensure_path_inside_noofy_models(transaction_dir)
        except ModelAvailabilityError:
            return
        shutil.rmtree(transaction_dir, ignore_errors=True)

    def _verify_download(
        self,
        model: RequiredModel,
        path: Path,
        *,
        known_sha256: str | None = None,
    ) -> str | None:
        size = path.stat().st_size
        if model.size_bytes is not None and size != model.size_bytes:
            raise ModelAvailabilityError(
                f"Downloaded model size mismatch: expected {model.size_bytes}, got {size}."
            )
        if model.checksum is not None:
            actual = known_sha256 or _sha256_file(path)
            expected = _normalize_sha256(model.checksum)
            if actual != expected:
                raise ModelAvailabilityError(
                    f"Downloaded model hash mismatch: expected {expected}, got {actual}."
                )
            return actual
        return known_sha256

    def _cached_sha256_file(
        self,
        path: Path,
        *,
        root: Path,
        metrics: VerifyHashMetrics | None = None,
    ) -> str:
        context = self._local_identity_context(path, root=root)
        if self.local_model_identity_store is not None:
            try:
                cached = self.local_model_identity_store.get_valid_hash(path, context)
            except Exception as exc:
                self.log_store.add(
                    "warning",
                    "Local model hash cache lookup failed",
                    "workflow.models.cache",
                    details={
                        "path": str(path),
                        "root_type": context.root_type,
                        "relative_path": context.relative_path,
                        "error": str(exc),
                    },
                )
                cached = None
            if cached:
                if metrics is not None:
                    metrics.record_cache_hit()
                return cached
        self.log_store.add(
            "info",
            "Computing local model SHA-256",
            "workflow.models.cache",
            details={
                "path": str(path),
                "root_type": context.root_type,
                "relative_path": context.relative_path,
            },
        )
        sha256 = _sha256_file(path)
        if metrics is not None:
            try:
                hashed_bytes = path.stat().st_size
            except OSError:
                hashed_bytes = 0
            metrics.record_cache_miss(bytes_hashed=hashed_bytes)
        self._remember_cached_sha256(path, root=root, sha256=sha256)
        return sha256

    def _remember_cached_sha256(self, path: Path, *, root: Path, sha256: str) -> None:
        if self.local_model_identity_store is None:
            return
        context = self._local_identity_context(path, root=root)
        try:
            self.local_model_identity_store.remember_hash(path, context, sha256)
        except Exception as exc:
            self.log_store.add(
                "warning",
                "Local model hash cache store failed",
                "workflow.models.cache",
                details={
                    "path": str(path),
                    "root_type": context.root_type,
                    "relative_path": context.relative_path,
                    "error": str(exc),
                },
            )

    def _local_identity_context(
        self,
        path: Path,
        *,
        root: Path,
    ) -> LocalModelIdentityContext:
        root_resolved = root.expanduser().resolve(strict=False)
        path_resolved = path.expanduser().resolve(strict=False)
        try:
            relative_path = path_resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            relative_path = Path(path.name).as_posix()
        noofy_root = self.noofy_models_dir.expanduser().resolve(strict=False)
        root_type = (
            "noofy_models"
            if root_resolved == noofy_root
            else "external_comfyui_models"
        )
        return LocalModelIdentityContext(
            root_type=root_type,
            root_identifier=str(root_resolved),
            relative_path=relative_path,
        )

    def _ensure_disk_space(self, required_bytes: int) -> None:
        root = self.noofy_models_dir
        probe = root if root.exists() else _nearest_existing_parent(root)
        if probe is None:
            raise ModelAvailabilityError("Noofy cannot check disk space for the configured Models folder.")
        free = shutil.disk_usage(probe).free
        needed = required_bytes + DISK_SPACE_SAFETY_MARGIN_BYTES
        if free < needed:
            raise ModelAvailabilityError(
                "Not enough free disk space in the configured Noofy Models folder location."
            )

    def _validate_owned_model_root(self) -> None:
        resolved = self.noofy_models_dir.resolve(strict=False)
        if _disallowed_model_root(resolved):
            raise ModelAvailabilityError("Noofy Models cannot be inside the bundled ComfyUI source folder.")

    def _ensure_path_inside_noofy_models(self, path: Path) -> None:
        root = self.noofy_models_dir.resolve(strict=False)
        target = path.resolve(strict=False)
        if target == root or _is_relative_to(target, root):
            return
        raise ModelAvailabilityError(
            "Downloaded models must be stored inside the configured Noofy Models folder."
        )

    def _safe_model_roots(self) -> list[Path]:
        roots: list[Path] = []
        for root in self.model_roots:
            resolved = root.expanduser().resolve(strict=False)
            if _disallowed_model_root(resolved):
                continue
            roots.append(resolved)
        return roots

    def _ownership_for_root(self, root: Path) -> AssetOwnership:
        try:
            if root.resolve(strict=False) == self.noofy_models_dir.resolve(strict=False):
                return AssetOwnership.NOOFY_DOWNLOADED
        except OSError:
            pass
        return AssetOwnership.USER_LOCAL


async def _stream_url(
    url: str,
    part_path: Path,
    *,
    headers: dict[str, str] | None = None,
    progress_callback: Callable[[int, int | None], None] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            total_bytes = _int_or_none(response.headers.get("content-length"))
            downloaded = 0
            if progress_callback is not None:
                progress_callback(downloaded, total_bytes)
            with part_path.open("wb") as file:
                async for chunk in response.aiter_bytes(chunk_size=DOWNLOAD_CHUNK_BYTES):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ModelDownloadCanceled("Download canceled.")
                    file.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total_bytes)


async def _stream_with_optional_headers(
    url: str,
    part_path: Path,
    *,
    headers: dict[str, str] | None = None,
    progress_callback: Callable[[int, int | None], None] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    kwargs: dict[str, object] = {}
    if headers:
        kwargs["headers"] = headers
    if progress_callback is not None:
        kwargs["progress_callback"] = progress_callback
    if cancel_event is not None:
        kwargs["cancel_event"] = cancel_event
    try:
        await _stream_url(url, part_path, **kwargs)
    except TypeError as exc:
        if "headers" not in str(exc):
            raise
        kwargs.pop("headers", None)
        await _stream_url(
            url,
            part_path,
            **kwargs,
        )


def _source_urls(model: RequiredModel) -> list[str]:
    urls = list(model.source_urls)
    if model.source_url and model.source_url not in urls:
        urls.append(model.source_url)
    return [_normalize_source_url(url.strip()) for url in urls if url.strip()]


def _normalize_source_url(url: str) -> str:
    parsed = urlparse(url)
    if "huggingface.co" not in parsed.netloc.casefold():
        return url
    path_parts = [unquote(part) for part in parsed.path.strip("/").split("/") if part]
    try:
        marker_index = path_parts.index("blob")
    except ValueError:
        return url
    if marker_index < 2 or marker_index + 2 >= len(path_parts):
        return url
    repo_id = "/".join(path_parts[:marker_index])
    revision = path_parts[marker_index + 1]
    rfilename = "/".join(path_parts[marker_index + 2 :])
    return _hugging_face_resolve_url(repo_id, rfilename, revision=revision)


def _prioritized_source_urls(urls: list[str]) -> list[str]:
    def priority(url: str) -> tuple[int, str]:
        host = urlparse(url).netloc.casefold()
        if "huggingface.co" in host:
            return (0, url)
        if "civitai.com" in host:
            return (1, url)
        return (2, url)

    return sorted(urls, key=priority)


async def _fetch_json(
    method: str,
    url: str,
    params: dict[str, str],
    headers: dict[str, str],
) -> object:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        response = await client.request(method, url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()


def _auth_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _download_failure_for_error(error: str) -> ModelDownloadFailure:
    base_suffix = (
        " The partial download was cleaned up safely. You can retry the download, "
        "continue importing with the workflow marked not ready, or cancel the import."
    )
    normalized = error.casefold()
    if "not enough free disk space" in normalized:
        return ModelDownloadFailure(
            status="not_enough_disk_space",
            status_label="Not enough disk space",
            message=(
                "Not enough free disk space in the configured Noofy Models folder location."
                + base_suffix
            ),
        )
    if "hash mismatch" in normalized:
        return ModelDownloadFailure(
            status="verification_failed",
            status_label="Verification failed",
            message=(
                "The downloaded model did not match the expected identity check."
                + base_suffix
            ),
        )
    if "size mismatch" in normalized:
        return ModelDownloadFailure(
            status="verification_failed",
            status_label="Verification failed",
            message=(
                "The downloaded model did not match the expected file size."
                + base_suffix
            ),
        )
    if "noofy models folder" in normalized:
        return ModelDownloadFailure(
            status="download_failed",
            status_label="Download failed",
            message=(
                "Noofy could not safely place the model in the configured Noofy Models folder."
                + base_suffix
            ),
        )
    return ModelDownloadFailure(
        status="download_failed",
        status_label="Download failed",
        message="The model download failed." + base_suffix,
    )


def _needs_manual_download_failure() -> ModelDownloadFailure:
    return ModelDownloadFailure(
        status="needs_manual_download",
        status_label="Needs manual download",
        message=(
            "Noofy could not find a reliable automatic download source for this model. "
            "The partial download was cleaned up safely. You can continue importing with "
            "the workflow marked not ready, or cancel the import."
        ),
    )


def _canceled_download_failure() -> ModelDownloadFailure:
    return ModelDownloadFailure(
        status="canceled",
        status_label="Canceled",
        message=(
            "Download canceled. Completed downloads were kept and the partial download "
            "was cleaned up safely. You can retry the download, continue importing with "
            "the workflow marked not ready, or cancel the import."
        ),
    )


def _emit_model_download_progress(
    progress_callback: ModelDownloadProgressCallback | None,
    *,
    model: RequiredModel,
    status: str,
    model_index: int | None,
    total_models: int | None,
    bytes_downloaded: int | None = None,
    total_bytes: int | None = None,
    message: str | None = None,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        {
            "requirement_id": _requirement_id(model),
            "filename": model.filename,
            "status": status,
            "model_index": model_index,
            "total_models": total_models,
            "bytes_downloaded": bytes_downloaded,
            "total_bytes": total_bytes,
            "message": message,
        }
    )


def _provider_from_url(url: str) -> str:
    host = urlparse(url).netloc.casefold()
    if "huggingface.co" in host:
        return "hugging_face"
    if "civitai.com" in host:
        return "civitai"
    return "source_url"


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _redact_common_secret_patterns(message: str) -> str:
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+",
        r"\1[redacted]",
        message,
    )
    redacted = re.sub(
        r"(?i)(bearer\s+)[^\s,;]+",
        r"\1[redacted]",
        redacted,
    )
    return re.sub(
        r"(?i)([?&](?:api_key|apikey|access_token|token|auth)=)[^&#\s]+",
        r"\1[redacted]",
        redacted,
    )


def _redact_url_secret(url: str) -> str:
    return _redact_common_secret_patterns(url)


def _default_api_key_resolver(provider: ApiKeyProvider) -> str | None:
    try:
        return KeyringCredentialStore().get_secret(provider)
    except CredentialStoreUnavailable:
        return None


def _provider_resolvable(model: RequiredModel) -> bool:
    return (
        model.verification_level is not ModelVerificationLevel.FILENAME_ONLY
        and bool(model.filename)
        and model.size_bytes is not None
        and model.size_bytes > 0
    )


def _reliable_candidates(
    model: RequiredModel, candidates: list[ProviderModelCandidate]
) -> list[ProviderModelCandidate]:
    reliable = [
        candidate
        for candidate in candidates
        if candidate.strength_for(model) >= 2
    ]
    return sorted(
        reliable,
        key=lambda candidate: (
            -candidate.strength_for(model),
            0 if candidate.provider == "hugging_face" else 1,
            candidate.download_url,
        ),
    )


def _dedupe_provider_candidates(
    candidates: list[ProviderModelCandidate],
) -> list[ProviderModelCandidate]:
    deduped: dict[tuple[str, str, str], ProviderModelCandidate] = {}
    for candidate in candidates:
        key = (candidate.provider, candidate.download_url, candidate.filename.casefold())
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = candidate
            continue
        deduped[key] = ProviderModelCandidate(
            provider=candidate.provider,
            download_url=candidate.download_url,
            filename=candidate.filename,
            size_bytes=candidate.size_bytes if candidate.size_bytes is not None else existing.size_bytes,
            sha256=candidate.sha256 if candidate.sha256 is not None else existing.sha256,
            source_trust=candidate.source_trust,
        )
    return list(deduped.values())


def _hugging_face_search_terms(model: RequiredModel) -> list[str]:
    filename = Path(model.filename).name
    stem = Path(filename).stem
    tokens = [
        token
        for token in re.split(r"[^a-zA-Z0-9]+", stem.casefold())
        if token
    ]
    stop_tokens = {
        "safetensors",
        "ckpt",
        "pt",
        "bin",
        "pruned",
        "emaonly",
        "fp16",
        "fp32",
        "model",
    }
    useful_tokens = [token for token in tokens if token not in stop_tokens]
    terms = [filename, stem]
    if useful_tokens:
        terms.append(" ".join(useful_tokens[:5]))
        terms.append("-".join(useful_tokens[:5]))
    if "v1" in tokens and "5" in tokens:
        terms.extend(["stable diffusion v1 5", "stable-diffusion-v1-5"])
    if "sd15" in tokens or "sd1" in tokens:
        terms.extend(["stable diffusion 1.5", "stable-diffusion-v1-5"])
    if _model_sha256(model) is not None and _looks_like_generic_provider_filename(filename, tokens):
        terms.extend(_hugging_face_context_search_terms(model, useful_tokens))
    unique: list[str] = []
    for term in terms:
        term = term.strip(" ._-")
        if not term or term in unique:
            continue
        unique.append(term)
        if len(unique) >= HUGGING_FACE_SEARCH_TERM_LIMIT:
            break
    return unique


def _looks_like_generic_provider_filename(filename: str, tokens: list[str]) -> bool:
    stem = Path(filename).stem.casefold()
    return (
        stem == "model"
        or stem.startswith("model.")
        or stem.startswith("model_")
        or stem.startswith("model-")
        or stem == "pytorch_model"
        or stem.startswith("pytorch_model.")
        or stem.startswith("pytorch_model_")
        or stem.startswith("pytorch_model-")
        or stem == "diffusion_pytorch_model"
        or stem.startswith("diffusion_pytorch_model.")
        or stem.startswith("diffusion_pytorch_model_")
        or stem.startswith("diffusion_pytorch_model-")
        or {"diffusion", "pytorch", "model"}.issubset(set(tokens))
    )


def _hugging_face_context_search_terms(
    model: RequiredModel, useful_filename_tokens: list[str]
) -> list[str]:
    context_stop_tokens = {
        "checkpoint",
        "checkpoints",
        "model",
        "models",
        "safetensors",
        "bin",
    }
    filename_stop_tokens = {
        "diffusion",
        "pytorch",
        "model",
        "safetensors",
        "bin",
    }
    context_tokens: list[str] = []
    for value in (model.model_type, model.folder):
        if not value:
            continue
        for token in re.split(r"[^a-zA-Z0-9]+", value.casefold()):
            if token and token not in context_stop_tokens and token not in context_tokens:
                context_tokens.append(token)
    distinct_filename_tokens = [
        token for token in useful_filename_tokens if token not in filename_stop_tokens
    ]
    terms: list[str] = []
    for context in context_tokens:
        terms.append(context)
        if distinct_filename_tokens:
            terms.append(" ".join([context, *distinct_filename_tokens[:3]]))
    return terms


def _hugging_face_api_model_url(repo_id: str) -> str:
    repo = "/".join(quote(part, safe="") for part in repo_id.split("/"))
    return f"https://huggingface.co/api/models/{repo}"


def _hugging_face_candidates_from_repo_record(
    model: RequiredModel, repo_id: str, repo: dict[str, object]
) -> list[ProviderModelCandidate]:
    siblings = repo.get("siblings")
    if not isinstance(siblings, list):
        return []
    expected_sha = _model_sha256(model)
    candidates: list[ProviderModelCandidate] = []
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        rfilename = sibling.get("rfilename") or sibling.get("path")
        if not isinstance(rfilename, str):
            continue
        size = _hugging_face_file_size(sibling)
        sha256 = _hugging_face_file_sha256(sibling)
        if expected_sha is not None:
            if sha256 != expected_sha:
                continue
        elif Path(rfilename).name.casefold() != model.filename.casefold():
            continue
        candidates.append(
            ProviderModelCandidate(
                provider="hugging_face",
                download_url=_hugging_face_resolve_url(repo_id, rfilename),
                filename=Path(rfilename).name,
                size_bytes=size,
                sha256=sha256,
            )
        )
    return candidates


def _hugging_face_file_size(file_record: dict[str, object]) -> int | None:
    lfs = file_record.get("lfs")
    if isinstance(lfs, dict):
        size = _int_or_none(lfs.get("size"))
        if size is not None:
            return size
    return _int_or_none(file_record.get("size"))


def _hugging_face_file_sha256(file_record: dict[str, object]) -> str | None:
    lfs = file_record.get("lfs")
    if isinstance(lfs, dict):
        sha = _sha_from_mapping(lfs)
        if sha is not None:
            return sha
        oid = lfs.get("oid")
        if isinstance(oid, str):
            normalized = oid.removeprefix("sha256:").casefold()
            if len(normalized) == 64 and all(ch in "0123456789abcdef" for ch in normalized):
                return normalized
    return _sha_from_mapping(file_record)


def _hugging_face_resolve_url(
    repo_id: str, rfilename: str, *, revision: str = "main"
) -> str:
    repo = "/".join(quote(part, safe="") for part in repo_id.split("/"))
    resolved_revision = quote(revision, safe="")
    file_path = "/".join(quote(part, safe="") for part in rfilename.split("/"))
    return f"https://huggingface.co/{repo}/resolve/{resolved_revision}/{file_path}"


def _civitai_file_candidate(
    model: RequiredModel, file_record: object
) -> ProviderModelCandidate | None:
    if not isinstance(file_record, dict):
        return None
    name = file_record.get("name")
    download_url = file_record.get("downloadUrl")
    if not isinstance(name, str):
        return None
    if not isinstance(download_url, str) or not download_url:
        return None
    hashes = file_record.get("hashes")
    sha256 = _sha_from_mapping(hashes) if isinstance(hashes, dict) else _sha_from_mapping(file_record)
    expected_sha = _model_sha256(model)
    if expected_sha is not None:
        if sha256 != expected_sha:
            return None
    elif Path(name).name.casefold() != model.filename.casefold():
        return None
    size = _int_or_none(file_record.get("size"))
    if size is None:
        size_kb = file_record.get("sizeKB")
        if isinstance(size_kb, int | float):
            size = int(size_kb * 1024)
    return ProviderModelCandidate(
        provider="civitai",
        download_url=download_url,
        filename=Path(name).name,
        size_bytes=size,
        sha256=sha256,
    )


def _sha_from_mapping(data: dict[str, object]) -> str | None:
    for key in ("sha256", "SHA256", "sha_256"):
        value = data.get(key)
        if isinstance(value, str):
            normalized = value.removeprefix("sha256:").casefold()
            if len(normalized) == 64 and all(ch in "0123456789abcdef" for ch in normalized):
                return normalized
    return None


def _model_sha256(model: RequiredModel) -> str | None:
    if model.checksum is None:
        return None
    return _normalize_sha256(model.checksum).casefold()


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _requirement_id(model: RequiredModel) -> str:
    return required_model_reference_id(model)


def requirement_id_for(model: RequiredModel) -> str:
    """Public, stable identity for a required model.

    Matches the ``requirement_id`` produced on ``RequiredModelAvailability`` so callers
    can normalize completion-ordered results back to the package's model order.
    """
    return _requirement_id(model)


def _model_download_target_key(model: RequiredModel) -> tuple[str, str]:
    return (model.folder, model.filename.replace("\\", "/"))


def _model_needs_download_disk_preflight(model: RequiredModel) -> bool:
    return (
        model.verification_level is not ModelVerificationLevel.FILENAME_ONLY
        and model.size_bytes is not None
        and model.size_bytes > 0
        and bool(_source_urls(model) or _provider_resolvable(model))
    )


def _normalize_sha256(value: str) -> str:
    return value.split(":", 1)[1] if value.startswith("sha256:") else value


def _sha256_file(path: Path) -> str:
    # ``hashlib.file_digest`` (Python 3.11+) is a C-level read/hash loop that uses a
    # larger internal buffer and releases the GIL while hashing, so it is faster than a
    # hand-rolled loop and scales better when several files hash concurrently. Output is
    # byte-for-byte identical to hashlib.sha256 over the file.
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _verification_filesystem_downgrade_reason(roots: list[Path]) -> str | None:
    """Best-effort, never-raising probe of model roots.

    Returns a downgrade reason (``"network_fs"`` or ``"rotational"``) if any root sits on
    a slow/network/rotational filesystem, else ``None``. Network roots take priority and
    short-circuit. Detection is platform-limited (Linux ``/proc/mounts`` + ``/sys/block``);
    anything unknown or erroring falls through to ``None`` (no downgrade).
    """
    fallback: str | None = None
    for root in roots:
        try:
            reason = _filesystem_slow_reason(root)
        except Exception:
            reason = None
        if reason == "network_fs":
            return "network_fs"
        if reason is not None and fallback is None:
            fallback = reason
    return fallback


def _filesystem_slow_reason(path: Path) -> str | None:
    mounts = _read_linux_mounts()
    if not mounts:
        return None
    target = path.expanduser().resolve(strict=False)
    device, fstype = _mount_for_path(target, mounts)
    if fstype is None:
        return None
    normalized_fstype = fstype.split(".", 1)[-1] if fstype.startswith("fuse.") else fstype
    if (
        normalized_fstype in NETWORK_VERIFICATION_FILESYSTEM_TYPES
        or fstype in NETWORK_VERIFICATION_FILESYSTEM_TYPES
    ):
        return "network_fs"
    if device and _device_is_rotational(device):
        return "rotational"
    return None


def _read_linux_mounts() -> list[tuple[str, str, str]]:
    try:
        raw = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        entries.append(
            (_unescape_mount_field(parts[0]), _unescape_mount_field(parts[1]), parts[2])
        )
    return entries


def _mount_for_path(
    target: Path, mounts: list[tuple[str, str, str]]
) -> tuple[str | None, str | None]:
    best_device: str | None = None
    best_fstype: str | None = None
    best_len = -1
    for device, mount_point, fstype in mounts:
        try:
            mount_path = Path(mount_point)
        except (TypeError, ValueError):
            continue
        if (target == mount_path or _is_relative_to(target, mount_path)) and len(
            mount_point
        ) > best_len:
            best_len = len(mount_point)
            best_device = device
            best_fstype = fstype
    return best_device, best_fstype


def _unescape_mount_field(value: str) -> str:
    if "\\" not in value:
        return value
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _device_is_rotational(device: str) -> bool:
    if not device.startswith("/dev/"):
        return False
    name = os.path.basename(device)
    # Try the device as named, then its parent block device (strip a trailing
    # partition suffix like sda1 -> sda or nvme0n1p1 -> nvme0n1).
    candidates = [name]
    stripped = re.sub(r"p?\d+$", "", name)
    if stripped and stripped != name:
        candidates.append(stripped)
    for candidate in candidates:
        rotational_path = Path("/sys/block") / candidate / "queue" / "rotational"
        try:
            if rotational_path.exists():
                return rotational_path.read_text(encoding="utf-8").strip() == "1"
        except OSError:
            continue
    return False


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists():
        if current.parent == current:
            return None
        current = current.parent
    return current


def _safe_join_model_path(root: Path, folder: str, filename: str) -> Path:
    folder_parts = _safe_relative_parts(folder, field_name="folder", allow_nested=True)
    filename_parts = _safe_relative_parts(filename, field_name="filename", allow_nested=False)
    return root.joinpath(*folder_parts, *filename_parts)


def _safe_join_model_folder(root: Path, folder: str) -> Path:
    folder_parts = _safe_relative_parts(folder, field_name="folder", allow_nested=True)
    return root.joinpath(*folder_parts)


def _safe_relative_parts(
    value: str, *, field_name: str, allow_nested: bool
) -> tuple[str, ...]:
    if "\\" in value:
        raise ModelAvailabilityError(
            f"Unsafe {field_name}: path traversal is not allowed."
        )
    path = Path(value)
    if path.is_absolute():
        raise ModelAvailabilityError(
            f"Unsafe {field_name}: absolute paths are not allowed."
        )
    parts = path.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ModelAvailabilityError(
            f"Unsafe {field_name}: path traversal is not allowed."
        )
    if not allow_nested and len(parts) != 1:
        raise ModelAvailabilityError(
            f"Unsafe {field_name}: nested paths are not allowed."
        )
    return parts


def _disallowed_model_root(path: Path) -> bool:
    comfyui_root = settings.comfyui_repo_dir.resolve(strict=False)
    third_party_root = (Path.cwd() / "third_party" / "comfyui").resolve(strict=False)
    return _is_relative_to(path, comfyui_root) or _is_relative_to(path, third_party_root)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
