import hashlib
from types import SimpleNamespace

import pytest

from app.artifacts import ModelVerificationLevel
from app.diagnostics import LogStore
from app.workflows.fp8_conversion import (
    ConvertedModelsRegistry,
    Fp8ConversionService,
    _filename_from_download_url,
)
from app.workflows.model_overrides import WorkflowModelOverrideStore
from app.workflows.package import RequiredModel, WorkflowPackage

FP8_NAME = "model-fp8.safetensors"


def _package(workflow_id="wf-fp8"):
    return WorkflowPackage(
        metadata={"id": workflow_id, "name": "FP8 Workflow", "version": "0.1.0"},
        engine="comfyui",
        required_models=[{"folder": "diffusion_models", "filename": FP8_NAME}],
        comfyui_graph={},
    )


class _FakeDownloadService:
    def __init__(self):
        self.calls = []

    def start_direct(self, **kwargs):
        self.calls.append(kwargs)
        return {"job_id": "model-download-123", "status": "queued", "user_facing_message": None}


def _service(tmp_path, download_service):
    models_dir = tmp_path / "Noofy Models"
    (models_dir / "diffusion_models").mkdir(parents=True, exist_ok=True)
    package = _package()
    engine_service = SimpleNamespace(
        workflow_loader=SimpleNamespace(
            get_package=lambda workflow_id: package,
            list_packages=lambda: [package],
        ),
        model_availability_service=SimpleNamespace(
            resolve_local_model_path=lambda model: None,
            noofy_models_dir=str(models_dir),
        ),
    )
    return (
        Fp8ConversionService(
            engine_service=engine_service,
            override_store=WorkflowModelOverrideStore(tmp_path / "model-overrides"),
            registry=ConvertedModelsRegistry(tmp_path / "converted-models.json"),
            ownership_store=SimpleNamespace(),
            model_download_service=download_service,
            log_store=LogStore(),
            subprocess_runner=lambda *args: None,
        ),
        models_dir,
    )


def test_url_filename_validation():
    assert (
        _filename_from_download_url("https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors")
        == "model-bf16.safetensors"
    )
    assert (
        _filename_from_download_url("https://example.com/files/model%20v2.safetensors?download=true")
        == "model v2.safetensors"
    )
    for bad_url in (
        "http://example.com/model.safetensors",  # not https
        "ftp://example.com/model.safetensors",
        "https:///model.safetensors",  # no host
        "https://example.com/",  # no filename
        "https://example.com/readme.txt",  # wrong suffix
        "https://example.com/model.gguf",  # different container needs different loaders
        "https://example.com/model.ckpt",
        "not a url at all",
    ):
        with pytest.raises(ValueError):
            _filename_from_download_url(bad_url)


def test_alternative_download_uses_authoritative_direct_path(tmp_path):
    downloads = _FakeDownloadService()
    service, _ = _service(tmp_path, downloads)

    result = service.start_alternative_download(
        "wf-fp8",
        "diffusion_models",
        FP8_NAME,
        "https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors",
    )
    assert result["job_id"] == "model-download-123"

    call = downloads.calls[0]
    assert call["workflow_id"] == "wf-fp8"
    assert call["explicit_source_urls_authoritative"] is True
    model = call["models"][0]
    assert isinstance(model, RequiredModel)
    assert model.folder == "diffusion_models"
    assert model.filename == "model-bf16.safetensors"
    assert model.source_urls == [
        "https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors"
    ]
    assert model.verification_level is ModelVerificationLevel.FILENAME_ONLY


def test_alternative_download_completion_records_override(tmp_path):
    from tests.fp8_test_utils import write_safetensors

    downloads = _FakeDownloadService()
    service, models_dir = _service(tmp_path, downloads)
    service.start_alternative_download(
        "wf-fp8",
        "diffusion_models",
        FP8_NAME,
        "https://huggingface.co/org/repo/resolve/main/model-bf16.safetensors",
    )
    downloaded_file = write_safetensors(
        models_dir / "diffusion_models" / "model-bf16.safetensors",
        {"blocks.0.weight": ("BF16", [64, 128])},
    )
    downloaded_bytes = downloaded_file.read_bytes()

    on_completed = downloads.calls[0]["on_completed"]
    on_completed(
        RequiredModel(folder="diffusion_models", filename="model-bf16.safetensors")
    )

    overrides = service.override_store.overrides_for("wf-fp8")
    assert len(overrides) == 1
    assert overrides[0].source_filename == FP8_NAME
    assert overrides[0].replacement_filename == "model-bf16.safetensors"
    assert overrides[0].origin == "downloaded"
    assert overrides[0].replacement_sha256 == hashlib.sha256(downloaded_bytes).hexdigest()
    assert overrides[0].replacement_size_bytes == len(downloaded_bytes)
    # The original stays on disk in the download path.


def test_alternative_download_rejects_fp8_replacement(tmp_path):
    from tests.fp8_test_utils import write_safetensors

    downloads = _FakeDownloadService()
    service, models_dir = _service(tmp_path, downloads)
    service.start_alternative_download(
        "wf-fp8",
        "diffusion_models",
        FP8_NAME,
        "https://example.com/models/model-still-fp8.safetensors",
    )
    downloaded_file = write_safetensors(
        models_dir / "diffusion_models" / "model-still-fp8.safetensors",
        {"blocks.0.weight": ("F8_E4M3", [64, 128])},
    )

    on_completed = downloads.calls[0]["on_completed"]
    with pytest.raises(ValueError, match="also an FP8 model"):
        on_completed(
            RequiredModel(folder="diffusion_models", filename="model-still-fp8.safetensors")
        )

    # No override recorded and the useless fp8 download is removed.
    assert service.override_store.overrides_for("wf-fp8") == []
    assert not downloaded_file.exists()


def test_alternative_download_rejects_unreadable_replacement(tmp_path):
    downloads = _FakeDownloadService()
    service, models_dir = _service(tmp_path, downloads)
    service.start_alternative_download(
        "wf-fp8",
        "diffusion_models",
        FP8_NAME,
        "https://example.com/models/not-a-model.safetensors",
    )
    # An HTML error page saved with a .safetensors name must never become an
    # override that routes the graph into a loader failure.
    downloaded_file = models_dir / "diffusion_models" / "not-a-model.safetensors"
    downloaded_file.write_bytes(b"<html>404 Not Found</html>")

    on_completed = downloads.calls[0]["on_completed"]
    with pytest.raises(ValueError, match="not a valid"):
        on_completed(
            RequiredModel(folder="diffusion_models", filename="not-a-model.safetensors")
        )

    assert service.override_store.overrides_for("wf-fp8") == []
    assert not downloaded_file.exists()


def test_alternative_download_refused_off_mps(tmp_path):
    downloads = _FakeDownloadService()
    service, _ = _service(tmp_path, downloads)
    service.mps_execution_active = lambda: False
    with pytest.raises(ValueError, match="Apple Silicon"):
        service.start_alternative_download(
            "wf-fp8",
            "diffusion_models",
            FP8_NAME,
            "https://example.com/models/model-bf16.safetensors",
        )
    assert downloads.calls == []


def test_alternative_download_rejects_same_filename(tmp_path):
    downloads = _FakeDownloadService()
    service, _ = _service(tmp_path, downloads)
    with pytest.raises(ValueError):
        service.start_alternative_download(
            "wf-fp8",
            "diffusion_models",
            FP8_NAME,
            f"https://example.com/models/{FP8_NAME}",
        )
    assert downloads.calls == []


def test_alternative_download_requires_known_requirement(tmp_path):
    downloads = _FakeDownloadService()
    service, _ = _service(tmp_path, downloads)
    with pytest.raises(KeyError):
        service.start_alternative_download(
            "wf-fp8",
            "vae",
            "unrelated.safetensors",
            "https://example.com/models/model-bf16.safetensors",
        )
