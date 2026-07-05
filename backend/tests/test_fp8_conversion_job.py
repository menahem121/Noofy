import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.diagnostics import LogStore
from app.workflows import fp8_conversion
from app.workflows.fp8_conversion import (
    ConvertedModelRecord,
    ConvertedModelsRegistry,
    Fp8ConversionConflictError,
    Fp8ConversionService,
)
from app.workflows.model_overrides import WorkflowModelOverrideStore
from app.workflows.package import WorkflowPackage

FP8_NAME = "model-fp8.safetensors"
CONVERTED_NAME = "model-fp8-converted-for-mac.safetensors"


class _FakeOwnershipStore:
    def __init__(self, origins=None):
        self.origins = dict(origins or {})
        self.downloaded: list[str] = []
        self.forgotten: list[str] = []

    def origin_for_model(self, model_key):
        return self.origins.get(model_key)

    def mark_downloaded(self, model_key):
        self.downloaded.append(model_key)
        self.origins[model_key] = "downloaded"

    def forget_model(self, model_key):
        self.forgotten.append(model_key)
        self.origins.pop(model_key, None)


class _FakeInventory:
    def __init__(self, models_dir: Path):
        self.models_dir = models_dir
        self.deleted: list[str] = []

    def delete_model(self, model_key):
        folder, filename = model_key.split("/", 1)
        (self.models_dir / folder / filename).unlink()
        self.deleted.append(model_key)


def _package(workflow_id="wf-fp8"):
    return WorkflowPackage(
        metadata={"id": workflow_id, "name": "FP8 Workflow", "version": "0.1.0"},
        engine="comfyui",
        required_models=[{"folder": "diffusion_models", "filename": FP8_NAME}],
        comfyui_graph={},
    )


def _completing_runner(output_payloads=None, block_event: asyncio.Event | None = None):
    calls: list[list[str]] = []

    async def runner(command, on_progress, cancel_event):
        calls.append(command)
        if block_event is not None:
            await block_event.wait()
        if cancel_event.is_set():
            return {"phase": "canceled"}
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_bytes(b"converted-bf16-bytes")
        on_progress({"phase": "converting", "done": 1, "total": 2})
        on_progress({"phase": "converting", "done": 2, "total": 2})
        return output_payloads or {
            "phase": "complete",
            "output_size": output_path.stat().st_size,
            "target_dtype": "bf16",
            "fp8_tensors_converted": 3,
        }

    runner.calls = calls
    return runner


def _service(tmp_path, *, packages=None, ownership=None, runner=None):
    models_dir = tmp_path / "Noofy Models"
    source = models_dir / "diffusion_models" / FP8_NAME
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"fp8-source-bytes")
    packages = packages if packages is not None else [_package()]
    engine_service = SimpleNamespace(
        workflow_loader=SimpleNamespace(
            get_package=lambda workflow_id: next(p for p in packages if p.metadata.id == workflow_id),
            list_packages=lambda: list(packages),
        ),
        model_availability_service=SimpleNamespace(
            resolve_local_model_path=lambda model: source if source.exists() else None,
            noofy_models_dir=str(models_dir),
        ),
    )
    ownership = ownership or _FakeOwnershipStore(
        {f"diffusion_models/{FP8_NAME}": "downloaded"}
    )
    inventory = _FakeInventory(models_dir)
    service = Fp8ConversionService(
        engine_service=engine_service,
        override_store=WorkflowModelOverrideStore(tmp_path / "model-overrides"),
        registry=ConvertedModelsRegistry(tmp_path / "converted-models.json"),
        ownership_store=ownership,
        model_inventory_service=inventory,
        log_store=LogStore(),
        subprocess_runner=runner or _completing_runner(),
    )
    return service, source, models_dir, ownership, inventory


@pytest.mark.anyio
async def test_conversion_job_lifecycle(tmp_path):
    service, source, models_dir, ownership, inventory = _service(tmp_path)
    started = service.start("wf-fp8", "diffusion_models", FP8_NAME)
    job = service._jobs[started.job_id]
    await job.task

    status = service.status("wf-fp8", started.job_id)
    assert status.status == "completed"
    assert status.percent == 100.0
    assert status.converted_filename == CONVERTED_NAME
    assert status.target_dtype == "bf16"
    assert (models_dir / "diffusion_models" / CONVERTED_NAME).read_bytes() == b"converted-bf16-bytes"

    overrides = service.override_store.overrides_for("wf-fp8")
    assert len(overrides) == 1
    assert overrides[0].replacement_filename == CONVERTED_NAME
    assert overrides[0].origin == "converted"
    expected_sha = hashlib.sha256(b"converted-bf16-bytes").hexdigest()
    assert overrides[0].replacement_sha256 == expected_sha

    records = service.registry.records()
    assert len(records) == 1
    assert records[0].source_sha256 == hashlib.sha256(b"fp8-source-bytes").hexdigest()
    assert f"diffusion_models/{CONVERTED_NAME}" in ownership.downloaded

    # Original was Noofy-owned and unreferenced elsewhere -> removed.
    assert status.source_removed is True
    assert inventory.deleted == [f"diffusion_models/{FP8_NAME}"]
    assert not source.exists()


@pytest.mark.anyio
async def test_conversion_failure_reports_error_code(tmp_path):
    async def failing_runner(command, on_progress, cancel_event):
        return {"phase": "error", "code": "no_fp8_tensors", "message": "boom"}

    service, _, models_dir, _, _ = _service(tmp_path, runner=failing_runner)
    started = service.start("wf-fp8", "diffusion_models", FP8_NAME)
    await service._jobs[started.job_id].task

    status = service.status("wf-fp8", started.job_id)
    assert status.status == "failed"
    assert status.error_code == "no_fp8_tensors"
    assert service.override_store.overrides_for("wf-fp8") == []
    assert not (models_dir / "diffusion_models" / f"{CONVERTED_NAME}.part").exists()


@pytest.mark.anyio
async def test_conversion_cancel_removes_partial_output(tmp_path):
    block = asyncio.Event()

    async def blocking_runner(command, on_progress, cancel_event):
        await cancel_event.wait()
        output_path = Path(command[command.index("--output") + 1])
        output_path.write_bytes(b"partial")
        block.set()
        return {"phase": "canceled"}

    service, _, models_dir, _, _ = _service(tmp_path, runner=blocking_runner)
    started = service.start("wf-fp8", "diffusion_models", FP8_NAME)
    await asyncio.sleep(0)
    service.cancel("wf-fp8", started.job_id)
    await service._jobs[started.job_id].task

    status = service.status("wf-fp8", started.job_id)
    assert status.status == "canceled"
    assert not (models_dir / "diffusion_models" / f"{CONVERTED_NAME}.part").exists()
    assert not (models_dir / "diffusion_models" / CONVERTED_NAME).exists()
    assert service.override_store.overrides_for("wf-fp8") == []


@pytest.mark.anyio
async def test_conversion_fails_without_disk_space(tmp_path, monkeypatch):
    service, _, _, _, _ = _service(tmp_path)
    monkeypatch.setattr(
        fp8_conversion.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=1, total=1, used=0),
    )
    started = service.start("wf-fp8", "diffusion_models", FP8_NAME)
    await service._jobs[started.job_id].task

    status = service.status("wf-fp8", started.job_id)
    assert status.status == "failed"
    assert status.error_code == "not_enough_disk_space"


@pytest.mark.anyio
async def test_existing_artifact_is_reused_without_reconversion(tmp_path):
    runner = _completing_runner()
    service, source, models_dir, _, _ = _service(tmp_path, runner=runner)
    converted = models_dir / "diffusion_models" / CONVERTED_NAME
    converted.write_bytes(b"previously-converted")
    service.registry.add(
        ConvertedModelRecord(
            source_sha256=hashlib.sha256(b"fp8-source-bytes").hexdigest(),
            source_filename=FP8_NAME,
            source_folder="diffusion_models",
            converted_filename=CONVERTED_NAME,
            converted_sha256=hashlib.sha256(b"previously-converted").hexdigest(),
            converted_size_bytes=len(b"previously-converted"),
            target_dtype="bf16",
            workflow_id="wf-other",
            created_at="2026-07-01T00:00:00+00:00",
        )
    )

    started = service.start("wf-fp8", "diffusion_models", FP8_NAME)
    await service._jobs[started.job_id].task

    status = service.status("wf-fp8", started.job_id)
    assert status.status == "completed"
    assert runner.calls == []  # no subprocess run
    assert converted.read_bytes() == b"previously-converted"
    overrides = service.override_store.overrides_for("wf-fp8")
    assert overrides[0].replacement_filename == CONVERTED_NAME


@pytest.mark.anyio
async def test_concurrent_conversion_for_same_file_conflicts(tmp_path):
    block = asyncio.Event()

    async def blocking_runner(command, on_progress, cancel_event):
        await block.wait()
        Path(command[command.index("--output") + 1]).write_bytes(b"x")
        return {"phase": "complete", "target_dtype": "bf16"}

    service, _, _, _, _ = _service(tmp_path, runner=blocking_runner)
    started = service.start("wf-fp8", "diffusion_models", FP8_NAME)
    await asyncio.sleep(0)
    with pytest.raises(Fp8ConversionConflictError) as excinfo:
        service.start("wf-fp8", "diffusion_models", FP8_NAME)
    assert excinfo.value.job_id == started.job_id
    block.set()
    await service._jobs[started.job_id].task


def test_startup_sweep_removes_orphaned_partials(tmp_path):
    models_dir = tmp_path / "Noofy Models"
    orphan = models_dir / "diffusion_models" / f"{CONVERTED_NAME}.part"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    _service(tmp_path)
    assert not orphan.exists()


def test_registry_round_trip(tmp_path):
    registry = ConvertedModelsRegistry(tmp_path / "converted-models.json")
    record = ConvertedModelRecord(
        source_sha256="a" * 64,
        source_filename=FP8_NAME,
        source_folder="diffusion_models",
        converted_filename=CONVERTED_NAME,
        converted_sha256="b" * 64,
        converted_size_bytes=10,
        target_dtype="bf16",
        workflow_id="wf-1",
        created_at="2026-07-05T00:00:00+00:00",
    )
    registry.add(record)
    assert registry.find_by_source("a" * 64) == record
    assert registry.find_by_source("c" * 64) is None
    data = json.loads((tmp_path / "converted-models.json").read_text())
    assert data["records"][0]["ownership"] == "noofy_converted"


def test_conversion_refused_off_mps(tmp_path):
    service, _, _, _, _ = _service(tmp_path)
    service.mps_execution_active = lambda: False
    with pytest.raises(ValueError, match="Apple Silicon"):
        service.start("wf-fp8", "diffusion_models", FP8_NAME)
    assert service._jobs == {}
