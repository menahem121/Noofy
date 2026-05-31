from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import model_sources
import app.models.civitai_loras as civitai_loras
import app.workflows.model_availability as availability_module
from app.artifacts import ModelVerificationLevel
from app.diagnostics import LogStore
from app.engine.models import ModelInfo
from app.main import create_app
from app.models.folders import ModelFolderSettingsService, ModelFolderSettingsStore, ModelFolderUpdateRequest
from app.models.ownership import ModelOwnershipStore
from app.models.tags import ModelTagStore
from app.settings.api_keys import ApiKeyMetadataStore, ApiKeyProvider, ApiKeySettingsService, CredentialStoreStatus
from app.workflows.model_availability import ModelAvailabilityService, ModelAvailabilityError
from app.workflows.package import RequiredModel, WorkflowInput, WorkflowMetadata, WorkflowPackage


class FakeCredentialStore:
    def __init__(self) -> None:
        self.secrets: dict[ApiKeyProvider, str] = {}

    def status(self) -> CredentialStoreStatus:
        return CredentialStoreStatus(available=True, status="available")

    def set_secret(self, provider: ApiKeyProvider, secret: str) -> None:
        self.secrets[provider] = secret

    def get_secret(self, provider: ApiKeyProvider) -> str | None:
        return self.secrets.get(provider)

    def delete_secret(self, provider: ApiKeyProvider) -> None:
        self.secrets.pop(provider, None)


class FakeWorkflowLoader:
    def __init__(self, package: WorkflowPackage) -> None:
        self.package = package

    def get_package(self, workflow_id: str) -> WorkflowPackage:
        if workflow_id != self.package.metadata.id:
            raise KeyError(workflow_id)
        return self.package

    def list_packages(self) -> list[WorkflowPackage]:
        return [self.package]


class FakeEngineService:
    def __init__(self, *, package: WorkflowPackage, noofy_root: Path, log_store: LogStore) -> None:
        self.workflow_loader = FakeWorkflowLoader(package)
        self.log_store = log_store
        self.model_availability_service = ModelAvailabilityService(
            model_roots=[noofy_root],
            noofy_models_dir=noofy_root,
            log_store=log_store,
        )

    async def list_available_models(self) -> list[ModelInfo]:
        return []

    async def shutdown(self) -> None:
        return None


def _package(*, checksum: str | None = None, required_models: list[RequiredModel] | None = None) -> WorkflowPackage:
    models = required_models if required_models is not None else [
        RequiredModel(
            folder="checkpoints",
            filename="sdxl-base.safetensors",
            node_id="4",
            node_type="CheckpointLoaderSimple",
            input_name="ckpt_name",
            checksum=checksum,
            size_bytes=12,
            verification_level=ModelVerificationLevel.SHA256_SIZE if checksum else ModelVerificationLevel.FILENAME_SIZE,
        )
    ]
    return WorkflowPackage(
        metadata=WorkflowMetadata(id="wf_lora", name="LoRA workflow", version="0.1.0"),
        engine="comfyui",
        required_models=models,
        comfyui_graph={
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl-base.safetensors"}},
            "12": {
                "class_type": "LoraLoader",
                "inputs": {"model": ["4", 0], "clip": ["4", 1], "lora_name": "None"},
            },
        },
        inputs=[
            WorkflowInput(
                id="base_model",
                label="Base model",
                control="select",
                binding={"node_id": "4", "input_name": "ckpt_name"},
                default="sdxl-base.safetensors",
                validation={},
            ),
            WorkflowInput(
                id="style_lora",
                label="Style LoRA",
                control="lora_loader",
                binding={"node_id": "12", "input_name": "lora_name"},
                default="None",
                validation={"options": ["None"]},
            ),
        ],
    )


def _client(tmp_path: Path, package: WorkflowPackage, *, api_key: str | None = None):
    noofy_root = tmp_path / "Noofy Models"
    noofy_root.mkdir(parents=True)
    folders = ModelFolderSettingsService(
        store=ModelFolderSettingsStore(tmp_path / "settings" / "model-folders.json"),
        default_noofy_models_dir=noofy_root,
    )
    folders.update(ModelFolderUpdateRequest(noofy_models_dir=str(noofy_root)))
    credential_store = FakeCredentialStore()
    api_keys = ApiKeySettingsService(
        metadata_store=ApiKeyMetadataStore(tmp_path / "settings" / "api-keys.json"),
        credential_store=credential_store,
    )
    if api_key:
        api_keys.save_key("civitai", api_key)
    ownership = ModelOwnershipStore(tmp_path / "settings" / "model-ownership.json")
    log_store = LogStore()
    engine = FakeEngineService(package=package, noofy_root=noofy_root, log_store=log_store)
    client = TestClient(
        create_app(
            engine_service=engine,
            api_key_service=api_keys,
            model_folder_service=folders,
            model_tag_store=ModelTagStore(tmp_path / "settings" / "model-tags.json"),
            model_ownership_store=ownership,
        )
    )
    return client, noofy_root, ownership, log_store


def _search_payload(**overrides):
    payload = {
        "workflow_id": "wf_lora",
        "lora_input_id": "style_lora",
        "input_values": {"base_model": "sdxl-base.safetensors", "style_lora": "None"},
        "query": "cinematic",
    }
    payload.update(overrides)
    return payload


def _model_search_response() -> dict[str, object]:
    return {
        "items": [
            {
                "id": 100,
                "name": "Cinematic SDXL LoRA",
                "type": "LORA",
                "creator": {"username": "maker"},
                "stats": {"downloadCount": 1000, "thumbsUpCount": 70},
                "modelVersions": [
                    {
                        "id": 200,
                        "name": "v1",
                        "baseModel": "SDXL 1.0",
                        "trainedWords": ["cinematic"],
                        "stats": {"downloadCount": 400, "thumbsUpCount": 40},
                        "files": [
                            {
                                "id": 300,
                                "name": "cinematic.safetensors",
                                "type": "Model",
                                "primary": True,
                                "sizeKB": 1,
                                "hashes": {"SHA256": "b" * 64},
                                "downloadUrl": "https://civitai.com/api/download/models/200",
                            }
                        ],
                        "images": [{"url": "https://image.civitai.com/example.jpeg"}],
                    }
                ],
            }
        ],
        "metadata": {"nextCursor": "next"},
    }


def test_civitai_lora_search_missing_key_returns_safe_state_without_provider_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_fetch(*_args, **_kwargs):
        raise AssertionError("CivitAI should not be called without an API key")

    monkeypatch.setattr(civitai_loras, "_fetch_json", fail_fetch)
    client, *_ = _client(tmp_path, _package())

    with client:
        response = client.post("/api/model-sources/civitai/search-loras", json=_search_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "api_key_required"
    assert body["user_facing_message"].startswith("Requires a CivitAI API key")


def test_civitai_lora_search_uses_verified_backend_params_and_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, str], dict[str, str]]] = []

    async def fake_fetch(method: str, url: str, params: dict[str, str], headers: dict[str, str]) -> object:
        calls.append((method, url, params, headers))
        if "/by-hash/" in url:
            return {"baseModel": "SDXL 1.0"}
        return _model_search_response()

    monkeypatch.setattr(civitai_loras, "_fetch_json", fake_fetch)
    client, *_ = _client(tmp_path, _package(checksum=f"sha256:{'a' * 64}"), api_key="civitai-secret")

    with client:
        response = client.post("/api/model-sources/civitai/search-loras", json=_search_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["base_model_filter"] == "SDXL 1.0"
    assert body["items"][0]["name"] == "Cinematic SDXL LoRA"
    assert body["items"][0]["preview_image_url"].startswith("/api/model-sources/civitai/preview?")
    search_call = [call for call in calls if call[1].endswith("/api/v1/models")][0]
    assert search_call[2]["types"] == "LORA"
    assert search_call[2]["query"] == "cinematic"
    assert search_call[2]["baseModels"] == "SDXL 1.0"
    assert "page" not in search_call[2]
    assert search_call[3]["Authorization"] == "Bearer civitai-secret"
    assert "token" not in str(search_call[2])


def test_civitai_lora_detection_returns_ambiguous_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(method: str, url: str, params: dict[str, str], headers: dict[str, str]) -> object:
        if "/by-hash/" in url:
            return {"baseModel": "SDXL 1.0"}
        return {"items": [], "metadata": {}}

    package = _package(
        required_models=[
            RequiredModel(folder="checkpoints", filename="sdxl-base.safetensors", checksum=f"sha256:{'a' * 64}", size_bytes=1, model_type="checkpoint"),
            RequiredModel(folder="checkpoints", filename="pony-base.safetensors", checksum=f"sha256:{'b' * 64}", size_bytes=1, model_type="checkpoint"),
        ]
    )
    package.comfyui_graph["12"]["inputs"] = {"lora_name": "None"}
    monkeypatch.setattr(civitai_loras, "_fetch_json", fake_fetch)
    client, *_ = _client(tmp_path, package, api_key="civitai-secret")

    with client:
        response = client.post("/api/model-sources/civitai/search-loras", json=_search_payload(input_values={"style_lora": "None"}))

    assert response.status_code == 200
    detection = response.json()["detection"]
    assert detection["status"] == "ambiguous"
    assert len(detection["candidates"]) == 2


def test_civitai_lora_detection_falls_back_to_required_model_metadata_when_graph_is_unresolved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_fetch(method: str, url: str, params: dict[str, str], headers: dict[str, str]) -> object:
        calls.append(url)
        if "/by-hash/" in url:
            return {"baseModel": "Pony"}
        return {"items": [], "metadata": {}}

    package = _package(
        required_models=[
            RequiredModel(
                folder="checkpoints",
                filename="pony-base.safetensors",
                node_id="9",
                node_type="CheckpointLoaderSimple",
                input_name="ckpt_name",
                checksum=f"sha256:{'b' * 64}",
                size_bytes=1,
                model_type="checkpoint",
            )
        ]
    )
    package.comfyui_graph["4"]["inputs"] = {"ckpt_name": "mystery-model.safetensors"}
    monkeypatch.setattr(civitai_loras, "_fetch_json", fake_fetch)
    client, *_ = _client(tmp_path, package, api_key="civitai-secret")

    with client:
        response = client.post("/api/model-sources/civitai/search-loras", json=_search_payload(input_values={"base_model": "mystery-model.safetensors"}))

    assert response.status_code == 200
    body = response.json()
    assert body["detection"]["status"] == "detected"
    assert body["base_model_filter"] == "Pony"
    assert any("/by-hash/" in url and ("B" * 64) in url for url in calls)


def test_civitai_preview_proxy_rejects_non_image_civitai_hosts() -> None:
    with pytest.raises(Exception) as exc_info:
        model_sources._validate_civitai_preview_url("https://civitai.com/api/download/models/123")

    assert getattr(exc_info.value, "status_code", None) == 400


def test_civitai_preview_proxy_rejects_urls_with_userinfo() -> None:
    with pytest.raises(Exception) as exc_info:
        model_sources._validate_civitai_preview_url("https://user:secret@image.civitai.com/example.jpeg")

    assert getattr(exc_info.value, "status_code", None) == 400


@pytest.mark.parametrize(
    ("status_code", "expected_status"),
    [(401, "api_key_required"), (403, "access_denied"), (429, "rate_limited")],
)
def test_civitai_lora_search_maps_provider_errors_to_safe_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    expected_status: str,
) -> None:
    async def fake_fetch(method: str, url: str, params: dict[str, str], headers: dict[str, str]) -> object:
        request = httpx.Request(method, url)
        response = httpx.Response(status_code, request=request, json={"error": "raw provider payload with token=secret"})
        raise httpx.HTTPStatusError("provider failed token=secret", request=request, response=response)

    monkeypatch.setattr(civitai_loras, "_fetch_json", fake_fetch)
    client, _, _, log_store = _client(tmp_path, _package(), api_key="civitai-secret")

    with client:
        response = client.post("/api/model-sources/civitai/search-loras", json=_search_payload())

    assert response.status_code == 200
    assert response.json()["status"] == expected_status
    assert "secret" not in str(log_store.list_events().model_dump(mode="json"))


def test_civitai_lora_download_uses_model_download_job_and_marks_ownership(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"downloaded lora"
    sha256 = hashlib.sha256(payload).hexdigest()

    async def fake_fetch(method: str, url: str, params: dict[str, str], headers: dict[str, str]) -> object:
        assert headers["Authorization"] == "Bearer civitai-secret"
        return {
            "id": 200,
            "modelId": 100,
            "model": {"name": "Cinematic SDXL LoRA", "type": "LORA"},
            "files": [
                {
                    "id": 300,
                    "name": "cinematic.safetensors",
                    "type": "Model",
                    "primary": True,
                    "sizeKB": len(payload) / 1024,
                    "hashes": {"SHA256": sha256},
                    "downloadUrl": "https://civitai.com/api/download/models/200",
                }
            ],
        }

    async def fake_stream(url: str, part_path: Path, **_kwargs) -> None:
        assert url == "https://civitai.com/api/download/models/200"
        part_path.write_bytes(payload)

    monkeypatch.setattr(civitai_loras, "_fetch_json", fake_fetch)
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)
    client, noofy_root, ownership, _ = _client(tmp_path, _package(), api_key="civitai-secret")

    with client:
        start = client.post(
            "/api/model-sources/civitai/download",
            json={
                "workflow_id": "wf_lora",
                "lora_input_id": "style_lora",
                "model_id": 100,
                "model_version_id": 200,
                "file_id": 300,
                "observed_lora_value": "None",
            },
        )
        assert start.status_code == 200
        status = _wait_for_download(client, start.json()["job_id"])

    assert status["status"] == "completed"
    assert (noofy_root / "loras" / "cinematic.safetensors").read_bytes() == payload
    assert ownership.origin_for_model("loras/cinematic.safetensors") == "downloaded"
    assert not list((noofy_root / ".downloads").glob("**/*.part"))


def test_civitai_lora_download_hash_mismatch_cleans_partial_and_final_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = b"tampered lora"

    async def fake_fetch(method: str, url: str, params: dict[str, str], headers: dict[str, str]) -> object:
        return {
            "id": 200,
            "modelId": 100,
            "model": {"name": "Cinematic SDXL LoRA", "type": "LORA"},
            "files": [
                {
                    "id": 300,
                    "name": "cinematic.safetensors",
                    "type": "Model",
                    "primary": True,
                    "sizeKB": len(payload) / 1024,
                    "hashes": {"SHA256": "0" * 64},
                    "downloadUrl": "https://civitai.com/api/download/models/200",
                }
            ],
        }

    async def fake_stream(url: str, part_path: Path, **_kwargs) -> None:
        part_path.write_bytes(payload)

    monkeypatch.setattr(civitai_loras, "_fetch_json", fake_fetch)
    monkeypatch.setattr(availability_module, "_stream_url", fake_stream)
    client, noofy_root, _, _ = _client(tmp_path, _package(), api_key="civitai-secret")

    with client:
        start = client.post(
            "/api/model-sources/civitai/download",
            json={
                "workflow_id": "wf_lora",
                "lora_input_id": "style_lora",
                "model_id": 100,
                "model_version_id": 200,
                "file_id": 300,
            },
        )
        status = _wait_for_download(client, start.json()["job_id"])

    assert status["status"] == "failed"
    assert not (noofy_root / "loras" / "cinematic.safetensors").exists()
    assert not list((noofy_root / ".downloads").glob("**/*"))


def test_civitai_lora_download_disk_space_failure_stops_before_streaming(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(method: str, url: str, params: dict[str, str], headers: dict[str, str]) -> object:
        return {
            "id": 200,
            "modelId": 100,
            "model": {"name": "Cinematic SDXL LoRA", "type": "LORA"},
            "files": [
                {
                    "id": 300,
                    "name": "cinematic.safetensors",
                    "type": "Model",
                    "primary": True,
                    "sizeKB": 1,
                    "hashes": {"SHA256": "0" * 64},
                    "downloadUrl": "https://civitai.com/api/download/models/200",
                }
            ],
        }

    def fail_disk_space(self, required_bytes: int) -> None:
        raise ModelAvailabilityError("Not enough free disk space in the configured Noofy Models folder location.")

    monkeypatch.setattr(civitai_loras, "_fetch_json", fake_fetch)
    monkeypatch.setattr(availability_module.ModelAvailabilityService, "_ensure_disk_space", fail_disk_space)
    monkeypatch.setattr(availability_module, "_stream_url", lambda *_args, **_kwargs: pytest.fail("download should not start"))
    client, *_ = _client(tmp_path, _package(), api_key="civitai-secret")

    with client:
        start = client.post(
            "/api/model-sources/civitai/download",
            json={
                "workflow_id": "wf_lora",
                "lora_input_id": "style_lora",
                "model_id": 100,
                "model_version_id": 200,
                "file_id": 300,
            },
        )
        status = _wait_for_download(client, start.json()["job_id"])

    assert status["status"] == "failed"
    assert "not enough free disk space" in str(status["models"][0]["message"]).casefold()


def _wait_for_download(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(40):
        response = client.get(f"/api/models/downloads/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "failed", "canceled"}:
            return body
        time.sleep(0.05)
    raise AssertionError("download job did not finish")
