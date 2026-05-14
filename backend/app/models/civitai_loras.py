from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field

from app.artifacts import ModelVerificationLevel
from app.diagnostics import DiagnosticsSink
from app.models.downloads import ModelDownloadJobService
from app.models.folders import ModelFolderSettingsService
from app.models.paths import model_key
from app.settings.api_keys import ApiKeySettingsService
from app.workflows.model_availability import _fetch_json, _sha256_file
from app.workflows.package import RequiredModel, WorkflowInput, WorkflowPackage

CIVITAI_MODELS_URL = "https://civitai.com/api/v1/models"
CIVITAI_MODEL_VERSION_URL = "https://civitai.com/api/v1/model-versions/{version_id}"
CIVITAI_BY_HASH_URL = "https://civitai.com/api/v1/model-versions/by-hash/{hash}"
HASH_COMPUTE_LIMIT_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
DEFAULT_SORT = "Most Downloaded"
DEFAULT_PERIOD = "AllTime"
BASE_MODEL_OPTIONS = [
    "SD 1.5",
    "SDXL 1.0",
    "Pony",
    "Illustrious",
    "Flux.1 D",
    "SD 3.5",
    "SD 3",
    "SD 2.1",
]
_HASH_CACHE: dict[str, str] = {}


class CivitaiLoraBaseModelCandidate(BaseModel):
    id: str
    label: str
    filename: str | None = None
    folder: str | None = None
    node_id: str | None = None
    input_name: str | None = None
    base_model: str | None = None
    sha256: str | None = Field(default=None, exclude=True)
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    source: str


class CivitaiLoraBaseModelDetection(BaseModel):
    status: Literal["detected", "ambiguous", "unknown"]
    base_model: str | None = None
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    label: str | None = None
    message: str
    candidates: list[CivitaiLoraBaseModelCandidate] = Field(default_factory=list)
    available_base_models: list[str] = Field(default_factory=lambda: BASE_MODEL_OPTIONS.copy())


class CivitaiLoraSearchRequest(BaseModel):
    workflow_id: str
    lora_input_id: str
    input_values: dict[str, Any] = Field(default_factory=dict)
    query: str = ""
    base_model: str | None = None
    clear_base_model_filter: bool = False
    cursor: str | None = None
    limit: int = DEFAULT_SEARCH_LIMIT
    sort: str = DEFAULT_SORT


class CivitaiLoraCard(BaseModel):
    model_id: int
    model_version_id: int
    file_id: int | None = None
    name: str
    creator: str | None = None
    version_name: str | None = None
    base_model: str | None = None
    file_name: str
    file_size_bytes: int | None = None
    download_count: int | None = None
    thumbs_up_count: int | None = None
    rating_count: int | None = None
    trigger_words: list[str] = Field(default_factory=list)
    preview_image_url: str | None = None
    model_page_url: str
    already_downloaded: bool = False


class CivitaiLoraSearchResponse(BaseModel):
    status: Literal["ok", "api_key_required", "access_denied", "rate_limited", "error"]
    user_facing_message: str
    detection: CivitaiLoraBaseModelDetection
    base_model_filter: str | None = None
    used_server_base_model_filter: bool = False
    items: list[CivitaiLoraCard] = Field(default_factory=list)
    next_cursor: str | None = None


class CivitaiLoraDownloadRequest(BaseModel):
    workflow_id: str
    lora_input_id: str
    model_id: int
    model_version_id: int
    file_id: int | None = None
    observed_lora_value: str | None = None


class CivitaiLoraDownloadStart(BaseModel):
    job_id: str
    status: str
    user_facing_message: str
    target_filename: str
    model_key: str
    observed_lora_value: str | None = None


class CivitaiLoraError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class CivitaiLoraBrowserService:
    def __init__(
        self,
        *,
        engine_service: object,
        api_key_service: ApiKeySettingsService,
        model_folder_service: ModelFolderSettingsService,
        model_download_service: ModelDownloadJobService,
        log_store: DiagnosticsSink | None = None,
        fetch_json=None,
    ) -> None:
        self.engine_service = engine_service
        self.api_key_service = api_key_service
        self.model_folder_service = model_folder_service
        self.model_download_service = model_download_service
        self.log_store = log_store
        self.fetch_json = fetch_json or _fetch_json
        self._hash_cache = _HASH_CACHE

    async def search_loras(self, request: CivitaiLoraSearchRequest) -> CivitaiLoraSearchResponse:
        token = self.api_key_service.get_key("civitai")
        package = self._package(request.workflow_id)
        detection = await self._detect_base_model(package, request, token=token)
        if not token:
            return CivitaiLoraSearchResponse(
                status="api_key_required",
                user_facing_message="Requires a CivitAI API key. Add one in Settings to search and download LoRAs.",
                detection=detection,
            )

        selected_base_model = _selected_base_model(request, detection)
        query = request.query.strip()
        params = {
            "limit": str(_clamp_limit(request.limit)),
            "types": "LORA",
            "sort": request.sort.strip() or DEFAULT_SORT,
            "period": DEFAULT_PERIOD,
            "primaryFileOnly": "true",
        }
        if query:
            params["query"] = query
        if request.cursor:
            params["cursor"] = request.cursor
        server_filter = bool(selected_base_model)
        if selected_base_model:
            params["baseModels"] = selected_base_model

        try:
            data = await self.fetch_json("GET", CIVITAI_MODELS_URL, params, _auth_headers(token))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 400 and selected_base_model:
                server_filter = False
                params.pop("baseModels", None)
                try:
                    data = await self._search_without_server_base_filter(token, params)
                except httpx.HTTPStatusError as retry_exc:
                    return _search_error_response(retry_exc, detection)
            else:
                return _search_error_response(exc, detection)

        items, next_cursor = self._normalize_search_response(
            data,
            base_model_filter=selected_base_model,
            require_client_filter=bool(selected_base_model and not server_filter),
        )
        if self.log_store is not None:
            self.log_store.add(
                "info",
                "CivitAI LoRA search completed",
                "model_sources.civitai",
                workflow_id=request.workflow_id,
                details={
                    "query_present": bool(query),
                    "base_model_filter": selected_base_model,
                    "used_server_base_model_filter": server_filter,
                    "result_count": len(items),
                },
            )
        return CivitaiLoraSearchResponse(
            status="ok",
            user_facing_message="LoRAs matching this base model." if selected_base_model else "LoRAs likely made for this AI model type.",
            detection=detection,
            base_model_filter=selected_base_model,
            used_server_base_model_filter=server_filter,
            items=items,
            next_cursor=next_cursor,
        )

    async def _search_without_server_base_filter(
        self,
        token: str,
        params: dict[str, str],
    ) -> object:
        try:
            return await self.fetch_json("GET", CIVITAI_MODELS_URL, params, _auth_headers(token))
        except httpx.HTTPStatusError as exc:
            raise exc

    async def start_download(self, request: CivitaiLoraDownloadRequest) -> CivitaiLoraDownloadStart:
        token = self.api_key_service.get_key("civitai")
        if not token:
            raise CivitaiLoraError(
                "api_key_required",
                "Requires a CivitAI API key. Add one in Settings to download LoRAs.",
                status_code=401,
            )
        package = self._package(request.workflow_id)
        lora_input = _input_by_id(package, request.lora_input_id)
        version = await self._fetch_model_version(request.model_version_id, token)
        if _int_or_none(version.get("modelId")) != request.model_id:
            raise CivitaiLoraError("invalid_selection", "The selected CivitAI LoRA version did not match the model.")
        model_record = version.get("model")
        if isinstance(model_record, dict) and str(model_record.get("type", "")).casefold() != "lora":
            raise CivitaiLoraError("invalid_selection", "The selected CivitAI model is not a LoRA.")
        file_record = _selected_file(version.get("files"), request.file_id)
        if file_record is None:
            raise CivitaiLoraError("invalid_selection", "No downloadable CivitAI file was available for this LoRA.")
        download_url = file_record.get("downloadUrl")
        if not isinstance(download_url, str) or not download_url:
            download_url = version.get("downloadUrl") if isinstance(version.get("downloadUrl"), str) else None
        if not download_url:
            raise CivitaiLoraError("invalid_selection", "CivitAI did not provide a downloadable file for this LoRA.")

        filename = _safe_lora_filename(str(file_record.get("name") or f"civitai-lora-{request.model_version_id}.safetensors"))
        size_bytes = _civitai_size_bytes(file_record)
        sha256 = _sha_from_mapping(file_record.get("hashes")) if isinstance(file_record.get("hashes"), dict) else None
        if size_bytes is None or size_bytes <= 0:
            raise CivitaiLoraError("missing_size", "CivitAI did not provide a verifiable file size for this LoRA.")
        required = RequiredModel(
            folder="loras",
            filename=filename,
            node_id=lora_input.binding.node_id,
            node_type=_node_type(package, lora_input.binding.node_id),
            input_name=lora_input.binding.input_name,
            source_url=download_url,
            source_urls=[download_url],
            checksum=f"sha256:{sha256}" if sha256 else None,
            model_type="lora",
            size_bytes=size_bytes,
            verification_level=(
                ModelVerificationLevel.SHA256_SIZE
                if sha256
                else ModelVerificationLevel.FILENAME_SIZE
            ),
        )
        started = self.model_download_service.start_direct(
            workflow_id=request.workflow_id,
            models=[required],
            queued_message="CivitAI LoRA download is queued.",
        )
        if self.log_store is not None:
            self.log_store.add(
                "info",
                "CivitAI LoRA download queued",
                "model_sources.civitai",
                workflow_id=request.workflow_id,
                details={
                    "model_id": request.model_id,
                    "model_version_id": request.model_version_id,
                    "file_id": request.file_id,
                    "target_model_key": model_key("loras", filename),
                    "size_bytes": size_bytes,
                    "sha256_present": sha256 is not None,
                },
            )
        return CivitaiLoraDownloadStart(
            job_id=started.job_id,
            status=started.status,
            user_facing_message=started.user_facing_message,
            target_filename=filename,
            model_key=model_key("loras", filename),
            observed_lora_value=request.observed_lora_value,
        )

    async def _fetch_model_version(self, version_id: int, token: str) -> dict[str, Any]:
        url = CIVITAI_MODEL_VERSION_URL.format(version_id=version_id)
        try:
            data = await self.fetch_json("GET", url, {}, _auth_headers(token))
        except httpx.HTTPStatusError as exc:
            raise _download_error(exc) from exc
        if not isinstance(data, dict):
            raise CivitaiLoraError("provider_error", "CivitAI returned an unexpected model version response.", status_code=502)
        return data

    def _package(self, workflow_id: str) -> WorkflowPackage:
        workflow_loader = getattr(self.engine_service, "workflow_loader", None)
        if workflow_loader is None:
            raise CivitaiLoraError("workflow_unavailable", "Workflow package loader is unavailable.", status_code=503)
        try:
            return workflow_loader.get_package(workflow_id)
        except KeyError as exc:
            raise CivitaiLoraError("workflow_not_found", "Workflow was not found.", status_code=404) from exc

    async def _detect_base_model(
        self,
        package: WorkflowPackage,
        request: CivitaiLoraSearchRequest,
        *,
        token: str | None,
    ) -> CivitaiLoraBaseModelDetection:
        try:
            lora_input = _input_by_id(package, request.lora_input_id)
        except CivitaiLoraError:
            return _unknown_detection("We could not detect the AI model type automatically.")
        weak_fallback: list[CivitaiLoraBaseModelCandidate] = []
        for candidates in self._candidate_model_loader_stages(package, lora_input, request.input_values):
            resolved = [await self._identify_candidate(candidate, token=token) for candidate in candidates]
            strong = [
                candidate
                for candidate in resolved
                if candidate.base_model and candidate.confidence in {"high", "medium"}
            ]
            if strong:
                return _detection_from_known_candidates(resolved, strong)
            if not weak_fallback and any(candidate.base_model for candidate in resolved):
                weak_fallback = resolved
        if weak_fallback:
            known = [candidate for candidate in weak_fallback if candidate.base_model]
            return _detection_from_known_candidates(weak_fallback, known, allow_low_confidence=False)
        return CivitaiLoraBaseModelDetection(
            status="unknown",
            base_model=None,
            confidence="unknown",
            label=None,
            message="We could not detect the AI model type automatically.",
            candidates=resolved,
        )

    def _candidate_model_loaders(
        self,
        package: WorkflowPackage,
        lora_input: WorkflowInput,
        input_values: dict[str, Any],
    ) -> list[CivitaiLoraBaseModelCandidate]:
        stages = self._candidate_model_loader_stages(package, lora_input, input_values)
        return stages[0] if stages else []

    def _candidate_model_loader_stages(
        self,
        package: WorkflowPackage,
        lora_input: WorkflowInput,
        input_values: dict[str, Any],
    ) -> list[list[CivitaiLoraBaseModelCandidate]]:
        stages: list[list[CivitaiLoraBaseModelCandidate]] = []
        upstream = _upstream_checkpoint_candidates(package, lora_input, input_values)
        if upstream:
            stages.append(upstream)
        metadata_matches = _required_model_candidates(package, lora_input, input_values)
        if metadata_matches:
            stages.append(metadata_matches)
        all_required = _all_required_base_model_candidates(package, input_values)
        if all_required:
            stages.append(all_required)
        deduped_stages: list[list[CivitaiLoraBaseModelCandidate]] = []
        seen: set[str] = set()
        for stage in stages:
            deduped: list[CivitaiLoraBaseModelCandidate] = []
            for candidate in stage:
                if candidate.id in seen:
                    continue
                seen.add(candidate.id)
                deduped.append(candidate)
            if deduped:
                deduped_stages.append(deduped)
        return deduped_stages

    async def _identify_candidate(
        self,
        candidate: CivitaiLoraBaseModelCandidate,
        *,
        token: str | None,
    ) -> CivitaiLoraBaseModelCandidate:
        sha256 = candidate.sha256
        path = self._local_path_for_candidate(candidate)
        if token and sha256 is None and path is not None:
            sha256 = await self._cached_or_known_hash(path)
        if token and sha256:
            base_model = await self._civitai_base_by_hash(sha256, token)
            if base_model:
                return candidate.model_copy(update={"base_model": base_model, "confidence": "high", "source": "civitai_hash"})
        if path is not None:
            metadata_base = _base_model_from_safetensors_metadata(path)
            if metadata_base:
                return candidate.model_copy(update={"base_model": metadata_base, "confidence": "medium", "source": "safetensors_metadata"})
        heuristic = _base_model_from_filename(candidate.filename or candidate.label)
        if heuristic:
            return candidate.model_copy(update={"base_model": heuristic, "confidence": "low", "source": "filename_heuristic"})
        return candidate

    def _local_path_for_candidate(self, candidate: CivitaiLoraBaseModelCandidate) -> Path | None:
        if not candidate.filename:
            return None
        folder = _folder_for_candidate(candidate)
        for root in self._model_roots():
            path = root / folder / candidate.filename
            try:
                if path.is_file() and _is_relative_to(path.resolve(strict=False), root.resolve(strict=False)):
                    return path
            except OSError:
                continue
            try:
                for match in (root / folder).rglob(candidate.filename):
                    if match.is_file() and _is_relative_to(match.resolve(strict=False), root.resolve(strict=False)):
                        return match
            except OSError:
                continue
        return None

    def _model_roots(self) -> list[Path]:
        availability = getattr(self.engine_service, "model_availability_service", None)
        roots = list(getattr(availability, "model_roots", []) or [])
        if roots:
            return [Path(root).expanduser() for root in roots]
        settings = self.model_folder_service.settings(ensure_folders=False)
        result = [Path(settings.noofy_models_dir).expanduser()]
        if settings.external_comfyui_models_dir:
            result.append(Path(settings.external_comfyui_models_dir).expanduser())
        return result

    async def _cached_or_known_hash(self, path: Path) -> str | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        if stat.st_size > HASH_COMPUTE_LIMIT_BYTES:
            return None
        key = f"{path.resolve(strict=False)}:{stat.st_mtime_ns}:{stat.st_size}"
        cached = self._hash_cache.get(key)
        if cached:
            return cached
        sha256 = await asyncio.to_thread(_sha256_file, path)
        self._hash_cache[key] = sha256
        return sha256

    async def _civitai_base_by_hash(self, sha256: str, token: str) -> str | None:
        url = CIVITAI_BY_HASH_URL.format(hash=sha256.upper())
        try:
            data = await self.fetch_json("GET", url, {}, _auth_headers(token))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            return None
        if not isinstance(data, dict):
            return None
        return _normal_base_model(data.get("baseModel"))

    def _normalize_search_response(
        self,
        data: object,
        *,
        base_model_filter: str | None,
        require_client_filter: bool,
    ) -> tuple[list[CivitaiLoraCard], str | None]:
        if not isinstance(data, dict):
            return [], None
        raw_items = data.get("items")
        if not isinstance(raw_items, list):
            return [], None
        cards: list[CivitaiLoraCard] = []
        for item in raw_items:
            if not isinstance(item, dict) or str(item.get("type", "")).casefold() != "lora":
                continue
            card = self._card_from_model(item, base_model_filter=base_model_filter, require_client_filter=require_client_filter)
            if card is not None:
                cards.append(card)
        metadata = data.get("metadata")
        next_cursor = metadata.get("nextCursor") if isinstance(metadata, dict) else None
        return cards, next_cursor if isinstance(next_cursor, str) and next_cursor else None

    def _card_from_model(
        self,
        item: dict[str, Any],
        *,
        base_model_filter: str | None,
        require_client_filter: bool,
    ) -> CivitaiLoraCard | None:
        versions = item.get("modelVersions")
        if not isinstance(versions, list):
            return None
        selected_version: dict[str, Any] | None = None
        selected_file: dict[str, Any] | None = None
        for version in versions:
            if not isinstance(version, dict):
                continue
            version_base = _normal_base_model(version.get("baseModel"))
            if require_client_filter and base_model_filter and not _base_models_match(version_base, base_model_filter):
                continue
            file_record = _selected_file(version.get("files"), None)
            if file_record is None:
                continue
            selected_version = version
            selected_file = file_record
            break
        if selected_version is None or selected_file is None:
            return None
        model_id = _int_or_none(item.get("id"))
        version_id = _int_or_none(selected_version.get("id"))
        if model_id is None or version_id is None:
            return None
        creator = item.get("creator")
        stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        version_stats = selected_version.get("stats") if isinstance(selected_version.get("stats"), dict) else {}
        filename = _safe_lora_filename(str(selected_file.get("name") or f"civitai-lora-{version_id}.safetensors"))
        return CivitaiLoraCard(
            model_id=model_id,
            model_version_id=version_id,
            file_id=_int_or_none(selected_file.get("id")),
            name=str(item.get("name") or "Untitled LoRA"),
            creator=str(creator.get("username")) if isinstance(creator, dict) and creator.get("username") else None,
            version_name=str(selected_version.get("name")) if selected_version.get("name") else None,
            base_model=_normal_base_model(selected_version.get("baseModel")),
            file_name=filename,
            file_size_bytes=_civitai_size_bytes(selected_file),
            download_count=_int_or_none(version_stats.get("downloadCount")) or _int_or_none(stats.get("downloadCount")),
            thumbs_up_count=_int_or_none(version_stats.get("thumbsUpCount")) or _int_or_none(stats.get("thumbsUpCount")),
            rating_count=_int_or_none(stats.get("thumbsUpCount")),
            trigger_words=_string_list(selected_version.get("trainedWords")),
            preview_image_url=_preview_image_url(selected_version.get("images") or item.get("images")),
            model_page_url=f"https://civitai.com/models/{model_id}?modelVersionId={version_id}",
            already_downloaded=self._downloaded_lora_exists(filename),
        )

    def _downloaded_lora_exists(self, filename: str) -> bool:
        root = Path(self.model_folder_service.settings(ensure_folders=False).noofy_models_dir).expanduser()
        return (root / "loras" / filename).is_file()


def _selected_base_model(request: CivitaiLoraSearchRequest, detection: CivitaiLoraBaseModelDetection) -> str | None:
    if request.clear_base_model_filter:
        return None
    requested = _normal_base_model(request.base_model)
    if requested:
        return requested
    if detection.status in {"detected", "ambiguous"} and detection.confidence in {"high", "medium"}:
        return detection.base_model
    return None


def _search_error_response(exc: httpx.HTTPStatusError, detection: CivitaiLoraBaseModelDetection) -> CivitaiLoraSearchResponse:
    status_code = exc.response.status_code
    if status_code == 401:
        return CivitaiLoraSearchResponse(
            status="api_key_required",
            user_facing_message="Requires a valid CivitAI API key. Update it in Settings, then try again.",
            detection=detection,
        )
    if status_code == 403:
        return CivitaiLoraSearchResponse(
            status="access_denied",
            user_facing_message="CivitAI denied access for this account.",
            detection=detection,
        )
    if status_code == 429:
        return CivitaiLoraSearchResponse(
            status="rate_limited",
            user_facing_message="CivitAI is rate limiting requests. Try again later.",
            detection=detection,
        )
    return CivitaiLoraSearchResponse(
        status="error",
        user_facing_message="CivitAI search failed. Try again later.",
        detection=detection,
    )


def _download_error(exc: httpx.HTTPStatusError) -> CivitaiLoraError:
    status_code = exc.response.status_code
    if status_code == 401:
        return CivitaiLoraError("api_key_required", "Requires a valid CivitAI API key. Update it in Settings, then try again.", status_code=401)
    if status_code == 403:
        return CivitaiLoraError("access_denied", "CivitAI denied access to this LoRA.", status_code=403)
    if status_code == 429:
        return CivitaiLoraError("rate_limited", "CivitAI is rate limiting downloads. Try again later.", status_code=429)
    return CivitaiLoraError("provider_error", "CivitAI download lookup failed. Try again later.", status_code=502)


def _detection_from_known_candidates(
    resolved: list[CivitaiLoraBaseModelCandidate],
    known: list[CivitaiLoraBaseModelCandidate],
    *,
    allow_low_confidence: bool = True,
) -> CivitaiLoraBaseModelDetection:
    if len(known) == 1:
        candidate = known[0]
        status = "detected" if allow_low_confidence or candidate.confidence in {"high", "medium"} else "unknown"
        return CivitaiLoraBaseModelDetection(
            status=status,
            base_model=candidate.base_model if status == "detected" else None,
            confidence=candidate.confidence if status == "detected" else "unknown",
            label=candidate.label if status == "detected" else None,
            message=(
                "LoRAs matching this base model."
                if status == "detected"
                else "We could not detect the AI model type automatically."
            ),
            candidates=resolved,
        )
    best = sorted(known, key=_candidate_sort_key)[0]
    return CivitaiLoraBaseModelDetection(
        status="ambiguous",
        base_model=best.base_model,
        confidence=best.confidence,
        label=best.label,
        message="Multiple possible base models were found. The best match is preselected.",
        candidates=resolved,
    )


def _unknown_detection(message: str) -> CivitaiLoraBaseModelDetection:
    return CivitaiLoraBaseModelDetection(
        status="unknown",
        base_model=None,
        confidence="unknown",
        message=message,
    )


def _input_by_id(package: WorkflowPackage, input_id: str) -> WorkflowInput:
    for workflow_input in package.inputs:
        if workflow_input.id == input_id:
            return workflow_input
    raise CivitaiLoraError("input_not_found", "The selected LoRA widget was not found.", status_code=404)


def _node_type(package: WorkflowPackage, node_id: str) -> str | None:
    node = package.comfyui_graph.get(node_id)
    if isinstance(node, dict):
        value = node.get("class_type")
        return value if isinstance(value, str) else None
    return None


def _upstream_checkpoint_candidates(
    package: WorkflowPackage,
    lora_input: WorkflowInput,
    input_values: dict[str, Any],
) -> list[CivitaiLoraBaseModelCandidate]:
    graph = package.comfyui_graph
    start = graph.get(lora_input.binding.node_id)
    if not isinstance(start, dict):
        return []
    start_inputs = start.get("inputs")
    if not isinstance(start_inputs, dict):
        return []
    linked_model = _linked_node_id(start_inputs.get("model"))
    if not linked_model:
        return []
    visited: set[str] = set()
    result: list[CivitaiLoraBaseModelCandidate] = []

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            return
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if _is_base_model_loader(class_type):
            candidate = _candidate_from_node(package, node_id, class_type, inputs, input_values)
            if candidate is not None:
                result.append(candidate)
            return
        for name in ("model", "unet", "checkpoint"):
            next_node = _linked_node_id(inputs.get(name))
            if next_node:
                visit(next_node)

    visit(linked_model)
    return result


def _candidate_from_node(
    package: WorkflowPackage,
    node_id: str,
    class_type: str,
    inputs: dict[str, Any],
    input_values: dict[str, Any],
) -> CivitaiLoraBaseModelCandidate | None:
    input_names = ("ckpt_name", "unet_name", "model_name", "diffusion_model_name", "checkpoint")
    for input_name in input_names:
        value = _bound_input_value(package, node_id, input_name, input_values)
        if value is None:
            value = inputs.get(input_name)
        if isinstance(value, str) and value.strip():
            filename = Path(value).name
            required = _matching_required_model(package, node_id, input_name, filename)
            return CivitaiLoraBaseModelCandidate(
                id=f"{node_id}:{input_name}:{filename}",
                label=filename,
                filename=filename,
                folder=required.folder if required else None,
                node_id=node_id,
                input_name=input_name,
                sha256=_model_sha256(required) if required else None,
                source=f"upstream_graph:{class_type}",
            )
    return None


def _required_model_candidates(
    package: WorkflowPackage,
    lora_input: WorkflowInput,
    input_values: dict[str, Any],
) -> list[CivitaiLoraBaseModelCandidate]:
    result: list[CivitaiLoraBaseModelCandidate] = []
    for model in package.required_models:
        if not _looks_like_base_model(model):
            continue
        if model.node_id == lora_input.binding.node_id:
            continue
        value = _bound_input_value(package, model.node_id or "", model.input_name or "", input_values)
        filename = Path(str(value)).name if isinstance(value, str) and value.strip() else model.filename
        result.append(
            CivitaiLoraBaseModelCandidate(
                id=f"{model.node_id or 'required'}:{model.input_name or 'model'}:{filename}",
                label=filename,
                filename=filename,
                folder=model.folder,
                node_id=model.node_id,
                input_name=model.input_name,
                sha256=_model_sha256(model),
                source="required_model_metadata",
            )
        )
    return result


def _all_required_base_model_candidates(
    package: WorkflowPackage,
    input_values: dict[str, Any],
) -> list[CivitaiLoraBaseModelCandidate]:
    result: list[CivitaiLoraBaseModelCandidate] = []
    for model in package.required_models:
        if not _looks_like_base_model(model):
            continue
        value = _bound_input_value(package, model.node_id or "", model.input_name or "", input_values)
        filename = Path(str(value)).name if isinstance(value, str) and value.strip() else model.filename
        result.append(
            CivitaiLoraBaseModelCandidate(
                id=f"{model.node_id or 'required'}:{model.input_name or 'model'}:{filename}",
                label=filename,
                filename=filename,
                folder=model.folder,
                node_id=model.node_id,
                input_name=model.input_name,
                sha256=_model_sha256(model),
                source="required_model_metadata",
            )
        )
    return result


def _bound_input_value(package: WorkflowPackage, node_id: str, input_name: str, input_values: dict[str, Any]) -> Any:
    if not node_id or not input_name:
        return None
    for workflow_input in package.inputs:
        if workflow_input.binding.node_id == node_id and workflow_input.binding.input_name == input_name:
            return input_values.get(workflow_input.id, workflow_input.default)
    return None


def _linked_node_id(value: Any) -> str | None:
    if isinstance(value, list) and value and isinstance(value[0], str):
        return value[0]
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    return None


def _is_base_model_loader(class_type: str) -> bool:
    normalized = class_type.casefold()
    return any(term in normalized for term in ("checkpointloader", "unetloader", "diffusionmodelloader"))


def _looks_like_base_model(model: RequiredModel) -> bool:
    text = f"{model.folder} {model.model_type or ''} {model.node_type or ''}".casefold()
    return any(term in text for term in ("checkpoint", "diffusion", "unet"))


def _matching_required_model(
    package: WorkflowPackage,
    node_id: str,
    input_name: str,
    filename: str,
) -> RequiredModel | None:
    for model in package.required_models:
        if model.node_id == node_id and model.input_name == input_name:
            return model
    for model in package.required_models:
        if model.filename.casefold() == filename.casefold():
            return model
    return None


def _model_sha256(model: RequiredModel | None) -> str | None:
    if model is None or model.checksum is None:
        return None
    value = model.checksum.removeprefix("sha256:").casefold()
    if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value):
        return value
    return None


def _folder_for_candidate(candidate: CivitaiLoraBaseModelCandidate) -> str:
    if candidate.folder:
        return candidate.folder
    input_name = (candidate.input_name or "").casefold()
    source = candidate.source.casefold()
    if "unet" in input_name or "diffusion" in input_name or "unetloader" in source:
        return "diffusion_models"
    return "checkpoints"


def _base_model_from_safetensors_metadata(path: Path) -> str | None:
    try:
        with path.open("rb") as file:
            header_length = int.from_bytes(file.read(8), "little")
            if header_length <= 0 or header_length > 16 * 1024 * 1024:
                return None
            header = json.loads(file.read(header_length).decode("utf-8"))
    except Exception:
        return None
    metadata = header.get("__metadata__") if isinstance(header, dict) else None
    if not isinstance(metadata, dict):
        return None
    for key in ("modelspec.architecture", "modelspec.title", "ss_base_model_version", "base_model", "baseModel"):
        value = metadata.get(key)
        if isinstance(value, str):
            mapped = _base_model_from_filename(value)
            if mapped:
                return mapped
    return None


def _base_model_from_filename(value: str | None) -> str | None:
    if not value:
        return None
    text = value.casefold()
    if "flux" in text:
        return "Flux.1 D"
    if "pony" in text:
        return "Pony"
    if "illustrious" in text:
        return "Illustrious"
    if "sdxl" in text or "xl" in text:
        return "SDXL 1.0"
    if "sd3.5" in text or "sd_3.5" in text or "stable-diffusion-3.5" in text:
        return "SD 3.5"
    if "sd3" in text or "stable-diffusion-3" in text:
        return "SD 3"
    if "sd2.1" in text or "stable-diffusion-2.1" in text:
        return "SD 2.1"
    if "sd15" in text or "sd1.5" in text or "1-5" in text or "v1-5" in text or "stable-diffusion-v1-5" in text:
        return "SD 1.5"
    return None


def _normal_base_model(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    lower = normalized.casefold()
    for option in BASE_MODEL_OPTIONS:
        if lower == option.casefold():
            return option
    inferred = _base_model_from_filename(normalized)
    return inferred or normalized


def _base_models_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return _normal_base_model(left) == _normal_base_model(right)


def _candidate_sort_key(candidate: CivitaiLoraBaseModelCandidate) -> tuple[int, str]:
    weights = {"high": 0, "medium": 1, "low": 2, "unknown": 3}
    return (weights.get(candidate.confidence, 3), candidate.label)


def _auth_headers(token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def _selected_file(files: Any, file_id: int | None) -> dict[str, Any] | None:
    if not isinstance(files, list):
        return None
    candidates = [file for file in files if isinstance(file, dict)]
    if file_id is not None:
        for file in candidates:
            if _int_or_none(file.get("id")) == file_id:
                return file
    for file in candidates:
        if file.get("primary") is True and str(file.get("type", "")).casefold() == "model":
            return file
    for file in candidates:
        if str(file.get("type", "")).casefold() == "model":
            return file
    return candidates[0] if candidates else None


def _civitai_size_bytes(file_record: dict[str, Any]) -> int | None:
    size = _int_or_none(file_record.get("size"))
    if size is not None:
        return size
    size_kb = file_record.get("sizeKB")
    if isinstance(size_kb, int | float):
        return int(size_kb * 1024)
    return None


def _safe_lora_filename(name: str) -> str:
    filename = Path(name).name.strip()
    filename = re.sub(r"[^A-Za-z0-9._+() -]+", "_", filename)
    filename = filename.strip(". ")
    return filename or "civitai-lora.safetensors"


def _sha_from_mapping(data: dict[str, Any] | None) -> str | None:
    if not isinstance(data, dict):
        return None
    for key in ("SHA256", "sha256", "sha_256"):
        value = data.get(key)
        if isinstance(value, str):
            normalized = value.removeprefix("sha256:").casefold()
            if len(normalized) == 64 and all(ch in "0123456789abcdef" for ch in normalized):
                return normalized
    return None


def _preview_image_url(images: Any) -> str | None:
    if not isinstance(images, list):
        return None
    for image in images:
        if isinstance(image, dict) and isinstance(image.get("url"), str):
            return f"/api/model-sources/civitai/preview?url={quote(image['url'], safe='')}"
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _clamp_limit(value: int) -> int:
    return max(1, min(MAX_SEARCH_LIMIT, value))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
