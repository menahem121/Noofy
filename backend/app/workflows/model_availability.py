from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

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
from app.workflows.package import RequiredModel, WorkflowPackage

DISK_SPACE_SAFETY_MARGIN_BYTES = 512 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1 << 20
PROVIDER_SEARCH_LIMIT = 20
HUGGING_FACE_SEARCH_TERM_LIMIT = 6
HUGGING_FACE_REPOS_PER_SEARCH_TERM = 4
HUGGING_FACE_REPO_INSPECTION_LIMIT = 8
PROVIDER_AUTH_REQUIRED_MESSAGE = (
    "This model source requires an API key for the provider account that can access it."
)
ACTIVE_DOWNLOAD_TRANSACTION_STATUSES = {"downloading", "verifying", "placing"}


class ModelAvailabilityError(RuntimeError):
    """Raised when model availability or downloads cannot be completed."""


class ProviderAuthenticationRequired(ModelAvailabilityError):
    """Raised when a provider reports authentication is required."""


class ProviderRateLimited(ModelAvailabilityError):
    """Raised when a provider asks Noofy to retry later."""


class ModelDownloadCanceled(ModelAvailabilityError):
    """Raised when a model download job is canceled."""


@dataclass(frozen=True)
class ModelDownloadFailure:
    status: str
    status_label: str
    message: str


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
        filename_matches = self.filename.casefold() == model.filename.casefold()
        if not filename_matches:
            return 0
        expected_sha = _model_sha256(model)
        if expected_sha is not None and self.sha256 is not None and self.sha256 != expected_sha:
            return 0
        sha_matches = expected_sha is not None and self.sha256 == expected_sha
        size_matches = (
            model.size_bytes is not None
            and self.size_bytes is not None
            and self.size_bytes == model.size_bytes
        )
        if sha_matches and size_matches:
            return 4
        if sha_matches:
            return 3
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
        hf = await self._search_hugging_face(model)
        self._record_provider_step(
            model,
            provider="hugging_face",
            step="model_search",
            candidates=hf,
        )
        reliable_hf = _reliable_candidates(model, hf)
        if reliable_hf and reliable_hf[0].strength_for(model) >= 4:
            return [candidate.download_url for candidate in reliable_hf]
        if reliable_hf and _model_sha256(model) is None:
            return [candidate.download_url for candidate in reliable_hf]
        candidates.extend(hf)

        if _model_sha256(model) is not None:
            civitai_by_hash = await self._search_civitai_by_hash(model)
            self._record_provider_step(
                model,
                provider="civitai",
                step="by_hash",
                candidates=civitai_by_hash,
            )
            candidates.extend(civitai_by_hash)
            reliable_civitai_by_hash = _reliable_candidates(model, civitai_by_hash)
            if (
                reliable_civitai_by_hash
                and reliable_civitai_by_hash[0].strength_for(model) >= 4
            ):
                return [candidate.download_url for candidate in reliable_civitai_by_hash]

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
    ) -> None:
        self.model_roots = model_roots
        self.noofy_models_dir = noofy_models_dir
        self.log_store = log_store
        self.provider_resolver = provider_resolver or ProviderModelResolver(
            log_store=log_store
        )

    def configure_model_roots(
        self,
        *,
        model_roots: list[Path],
        noofy_models_dir: Path,
    ) -> None:
        self.model_roots = model_roots
        self.noofy_models_dir = noofy_models_dir

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

    def summarize(self, package: WorkflowPackage) -> RequiredModelSummary:
        models = [self._availability_for(model) for model in package.required_models]
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
        before = self.summarize(package)
        downloaded_count = 0
        failed_count = 0
        failures: dict[str, ModelDownloadFailure] = {}
        canceled = False
        downloadable_models = [
            (model, availability)
            for model, availability in zip(package.required_models, before.models, strict=True)
            if availability.status == "missing"
        ]
        total_models = len(downloadable_models)

        for model_index, (model, availability) in enumerate(downloadable_models, start=1):
            if cancel_event is not None and cancel_event.is_set():
                failures[_requirement_id(model)] = _canceled_download_failure()
                canceled = True
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="canceled",
                    model_index=model_index,
                    total_models=total_models,
                    message="Download canceled.",
                )
                break
            _emit_model_download_progress(
                progress_callback,
                model=model,
                status="downloading",
                model_index=model_index,
                total_models=total_models,
                bytes_downloaded=0,
                total_bytes=model.size_bytes,
            )
            try:
                downloaded = await self._download_model(
                    model,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                    model_index=model_index,
                    total_models=total_models,
                )
                if downloaded:
                    downloaded_count += 1
                    _emit_model_download_progress(
                        progress_callback,
                        model=model,
                        status="completed",
                        model_index=model_index,
                        total_models=total_models,
                        bytes_downloaded=model.size_bytes,
                        total_bytes=model.size_bytes,
                    )
                else:
                    failed_count += 1
                    failures[_requirement_id(model)] = _needs_manual_download_failure()
                    _emit_model_download_progress(
                        progress_callback,
                        model=model,
                        status="failed",
                        model_index=model_index,
                        total_models=total_models,
                        message=failures[_requirement_id(model)].status_label,
                    )
            except ProviderAuthenticationRequired as exc:
                failed_count += 1
                failures[_requirement_id(model)] = ModelDownloadFailure(
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
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="failed",
                    model_index=model_index,
                    total_models=total_models,
                    message=failures[_requirement_id(model)].status_label,
                )
            except ProviderRateLimited as exc:
                failed_count += 1
                failures[_requirement_id(model)] = ModelDownloadFailure(
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
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="failed",
                    model_index=model_index,
                    total_models=total_models,
                    message=failures[_requirement_id(model)].status_label,
                )
            except ModelDownloadCanceled:
                failures[_requirement_id(model)] = _canceled_download_failure()
                canceled = True
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="canceled",
                    model_index=model_index,
                    total_models=total_models,
                    message="Download canceled.",
                )
                break
            except Exception as exc:
                failed_count += 1
                safe_error = self.provider_resolver.sanitize_message(str(exc))
                failures[_requirement_id(model)] = _download_failure_for_error(safe_error)
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
                _emit_model_download_progress(
                    progress_callback,
                    model=model,
                    status="failed",
                    model_index=model_index,
                    total_models=total_models,
                    message=failures[_requirement_id(model)].status_label,
                )

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
                "Some models could not be downloaded."
                if failed_count
                else "Model download check finished."
                )
            ),
            downloaded_count=downloaded_count,
            failed_count=failed_count,
            model_summary=after,
        )

    def _availability_for(self, model: RequiredModel) -> RequiredModelAvailability:
        source_urls = _source_urls(model)
        candidates = self._local_candidates(model)
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
            status = self._candidate_status(model, candidate)
            if status == "available":
                return RequiredModelAvailability(
                    **base,
                    status="available",
                    status_label="Available",
                    asset_ownership=self._ownership_for_root(root),
                    source_path=str(candidate),
                    matched_root=str(root),
                    matched_sha256=_sha256_file(candidate)
                    if model.verification_level is not ModelVerificationLevel.FILENAME_ONLY
                    else None,
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

    def _candidate_status(self, model: RequiredModel, path: Path) -> str:
        if not path.is_file():
            return "missing"
        size = path.stat().st_size
        if model.verification_level is ModelVerificationLevel.SHA256_SIZE:
            if model.size_bytes is None or model.checksum is None:
                return "possible_match"
            if size != model.size_bytes:
                return "possible_match"
            return "available" if _sha256_file(path) == _normalize_sha256(model.checksum) else "possible_match"
        if model.verification_level is ModelVerificationLevel.FILENAME_SIZE:
            return "available" if model.size_bytes is not None and size == model.size_bytes else "possible_match"
        return "possible_match"

    def _local_candidates(self, model: RequiredModel) -> list[tuple[Path, Path]]:
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
            try:
                for candidate in root.rglob(model.filename):
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
            current = self._availability_for(model)
            if current.status == "available":
                return False
            raise ModelAvailabilityError(
                f"A different file already exists at {final_path}; Noofy will not overwrite it."
            )
        final_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_path_inside_noofy_models(final_path)

        await self._download_verified_with_fallback(
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
                "sha256": f"sha256:{_sha256_file(final_path)}",
                "target_path": str(final_path),
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
    ) -> None:
        last_error: Exception | None = None
        for url in urls:
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

                if progress_callback is None and cancel_event is None:
                    await _stream_url(url, transaction.part_path)
                else:
                    await _stream_url(
                        url,
                        transaction.part_path,
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
                self._verify_download(model, transaction.part_path)
                transaction.write_state(
                    status="placing",
                    bytes_downloaded=transaction.part_path.stat().st_size,
                )
                self._ensure_path_inside_noofy_models(final_path)
                os.replace(transaction.part_path, final_path)
                self._ensure_path_inside_noofy_models(final_path)
                try:
                    self._verify_download(model, final_path)
                except Exception:
                    if final_path.exists():
                        final_path.unlink()
                    raise
                return
            except ModelDownloadCanceled:
                if transaction.part_path.exists():
                    transaction.part_path.unlink()
                raise
            except Exception as exc:
                last_error = exc
                if transaction.part_path.exists():
                    transaction.part_path.unlink()
            finally:
                self._cleanup_transaction(transaction.transaction_dir)
        raise ModelAvailabilityError(f"All model sources failed: {last_error}")

    def _cleanup_transaction(self, transaction_dir: Path) -> None:
        if not transaction_dir.exists():
            return
        try:
            self._ensure_path_inside_noofy_models(transaction_dir)
        except ModelAvailabilityError:
            return
        shutil.rmtree(transaction_dir, ignore_errors=True)

    def _verify_download(self, model: RequiredModel, path: Path) -> None:
        size = path.stat().st_size
        if model.size_bytes is not None and size != model.size_bytes:
            raise ModelAvailabilityError(
                f"Downloaded model size mismatch: expected {model.size_bytes}, got {size}."
            )
        if model.checksum is not None:
            actual = _sha256_file(path)
            expected = _normalize_sha256(model.checksum)
            if actual != expected:
                raise ModelAvailabilityError(
                    f"Downloaded model hash mismatch: expected {expected}, got {actual}."
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
    progress_callback: Callable[[int, int | None], None] | None = None,
    cancel_event: asyncio.Event | None = None,
) -> None:
    async with httpx.AsyncClient(follow_redirects=True, timeout=None) as client:
        async with client.stream("GET", url) as response:
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


def _source_urls(model: RequiredModel) -> list[str]:
    urls = list(model.source_urls)
    if model.source_url and model.source_url not in urls:
        urls.append(model.source_url)
    return [url for url in urls if url.strip()]


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
            status="hash_mismatch",
            status_label="Hash mismatch",
            message=(
                "The downloaded model did not match the expected identity check."
                + base_suffix
            ),
        )
    if "size mismatch" in normalized:
        return ModelDownloadFailure(
            status="download_failed",
            status_label="Download failed",
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
    unique: list[str] = []
    for term in terms:
        term = term.strip(" ._-")
        if not term or term in unique:
            continue
        unique.append(term)
        if len(unique) >= HUGGING_FACE_SEARCH_TERM_LIMIT:
            break
    return unique


def _hugging_face_api_model_url(repo_id: str) -> str:
    repo = "/".join(quote(part, safe="") for part in repo_id.split("/"))
    return f"https://huggingface.co/api/models/{repo}"


def _hugging_face_candidates_from_repo_record(
    model: RequiredModel, repo_id: str, repo: dict[str, object]
) -> list[ProviderModelCandidate]:
    siblings = repo.get("siblings")
    if not isinstance(siblings, list):
        return []
    candidates: list[ProviderModelCandidate] = []
    for sibling in siblings:
        if not isinstance(sibling, dict):
            continue
        rfilename = sibling.get("rfilename") or sibling.get("path")
        if not isinstance(rfilename, str):
            continue
        if Path(rfilename).name.casefold() != model.filename.casefold():
            continue
        size = _hugging_face_file_size(sibling)
        sha256 = _hugging_face_file_sha256(sibling)
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


def _hugging_face_resolve_url(repo_id: str, rfilename: str) -> str:
    repo = "/".join(quote(part, safe="") for part in repo_id.split("/"))
    file_path = "/".join(quote(part, safe="") for part in rfilename.split("/"))
    return f"https://huggingface.co/{repo}/resolve/main/{file_path}"


def _civitai_file_candidate(
    model: RequiredModel, file_record: object
) -> ProviderModelCandidate | None:
    if not isinstance(file_record, dict):
        return None
    name = file_record.get("name")
    download_url = file_record.get("downloadUrl")
    if not isinstance(name, str) or Path(name).name.casefold() != model.filename.casefold():
        return None
    if not isinstance(download_url, str) or not download_url:
        return None
    hashes = file_record.get("hashes")
    sha256 = _sha_from_mapping(hashes) if isinstance(hashes, dict) else _sha_from_mapping(file_record)
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
    if model.node_id and model.input_name:
        return f"{model.node_id}:{model.input_name}:{model.folder}/{model.filename}"
    return f"{model.folder}/{model.filename}"


def _normalize_sha256(value: str) -> str:
    return value.split(":", 1)[1] if value.startswith("sha256:") else value


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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
