import hashlib
import json
from types import SimpleNamespace

from app.diagnostics import LogStore
from app.workflows.fp8_conversion import (
    ConvertedModelsRegistry,
    Fp8ConversionService,
    _Fp8ConversionJob,
)
from app.workflows.model_overrides import (
    WorkflowModelOverride,
    WorkflowModelOverrideStore,
)
from app.workflows.package import WorkflowPackage

import asyncio

FP8_NAME = "model-fp8.safetensors"
CONVERTED_NAME = "model-fp8-converted-for-mac.safetensors"
MODEL_KEY = f"diffusion_models/{FP8_NAME}"


class _FakeOwnershipStore:
    def __init__(self, origins=None):
        self.origins = dict(origins or {})

    def origin_for_model(self, model_key):
        return self.origins.get(model_key)

    def mark_downloaded(self, model_key):
        self.origins[model_key] = "downloaded"

    def forget_model(self, model_key):
        self.origins.pop(model_key, None)


class _FakeInventory:
    def __init__(self, models_dir):
        self.models_dir = models_dir
        self.deleted = []

    def delete_model(self, model_key):
        folder, filename = model_key.split("/", 1)
        (self.models_dir / folder / filename).unlink()
        self.deleted.append(model_key)


def _package(workflow_id, required=True):
    return WorkflowPackage(
        metadata={"id": workflow_id, "name": workflow_id, "version": "0.1.0"},
        engine="comfyui",
        required_models=(
            [{"folder": "diffusion_models", "filename": FP8_NAME}] if required else []
        ),
        comfyui_graph={},
    )


def _setup(tmp_path, *, origin="downloaded", packages=None):
    models_dir = tmp_path / "Noofy Models"
    source = models_dir / "diffusion_models" / FP8_NAME
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fp8-source-bytes")
    packages = packages if packages is not None else [_package("wf-fp8")]
    engine_service = SimpleNamespace(
        workflow_loader=SimpleNamespace(
            get_package=lambda workflow_id: next(p for p in packages if p.metadata.id == workflow_id),
            list_packages=lambda: list(packages),
        ),
        model_availability_service=SimpleNamespace(
            resolve_local_model_path=lambda model: source,
            noofy_models_dir=str(models_dir),
        ),
    )
    ownership = _FakeOwnershipStore({MODEL_KEY: origin} if origin else {})
    inventory = _FakeInventory(models_dir)
    override_store = WorkflowModelOverrideStore(tmp_path / "model-overrides")
    service = Fp8ConversionService(
        engine_service=engine_service,
        override_store=override_store,
        registry=ConvertedModelsRegistry(tmp_path / "converted-models.json"),
        ownership_store=ownership,
        model_inventory_service=inventory,
        log_store=LogStore(),
        subprocess_runner=lambda *args: None,  # never used in these tests
    )
    job = _Fp8ConversionJob(
        job_id="job-1",
        workflow_id="wf-fp8",
        folder="diffusion_models",
        filename=FP8_NAME,
        cancel_event=asyncio.Event(),
        task=None,
        status="finalizing",
    )
    return service, job, source, inventory


def _delete(service, job, source):
    return service._delete_source_if_safe(
        job,
        source_path=source,
        source_sha256=hashlib.sha256(b"fp8-source-bytes").hexdigest(),
        source_stat=source.stat(),
    )


def test_noofy_owned_unreferenced_source_is_deleted(tmp_path):
    service, job, source, inventory = _setup(tmp_path)
    removed, reason = _delete(service, job, source)
    assert removed is True
    assert reason is None
    assert inventory.deleted == [MODEL_KEY]
    assert not source.exists()


def test_user_owned_source_is_never_deleted(tmp_path):
    service, job, source, inventory = _setup(tmp_path, origin=None)
    removed, reason = _delete(service, job, source)
    assert removed is False
    assert reason == "user_owned"
    assert source.exists()
    assert inventory.deleted == []


def test_replaced_file_is_not_deleted(tmp_path):
    service, job, source, inventory = _setup(tmp_path)
    stat_before = source.stat()
    source.write_bytes(b"a completely different file that replaced the original!")
    removed, reason = service._delete_source_if_safe(
        job,
        source_path=source,
        source_sha256=hashlib.sha256(b"fp8-source-bytes").hexdigest(),
        source_stat=stat_before,
    )
    assert removed is False
    assert reason == "file_changed_since_conversion"
    assert source.exists()


def test_source_referenced_by_other_workflow_is_kept(tmp_path):
    packages = [_package("wf-fp8"), _package("wf-other")]
    service, job, source, inventory = _setup(tmp_path, packages=packages)
    removed, reason = _delete(service, job, source)
    assert removed is False
    assert json.loads(reason) == {"referenced_by": ["wf-other"]}
    assert source.exists()


def test_source_deleted_once_other_workflow_has_usable_override(tmp_path):
    packages = [_package("wf-fp8"), _package("wf-other")]
    service, job, source, inventory = _setup(tmp_path, packages=packages)
    service.override_store.upsert(
        "wf-other",
        WorkflowModelOverride(
            folder="diffusion_models",
            source_filename=FP8_NAME,
            replacement_filename=CONVERTED_NAME,
            origin="converted",
        ),
    )
    # The override alone is not enough: its replacement file must exist.
    removed, reason = _delete(service, job, source)
    assert removed is False
    assert json.loads(reason) == {"referenced_by": ["wf-other"]}

    (source.parent / CONVERTED_NAME).write_bytes(b"converted")
    removed, reason = _delete(service, job, source)
    assert removed is True
    assert reason is None
    assert inventory.deleted == [MODEL_KEY]


def test_deletion_skipped_when_inventory_unavailable(tmp_path):
    service, job, source, _ = _setup(tmp_path)
    service.model_inventory_service = None
    removed, reason = _delete(service, job, source)
    assert removed is False
    assert reason == "deletion_unavailable"
    assert source.exists()
