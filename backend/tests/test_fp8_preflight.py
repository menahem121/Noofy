from datetime import datetime
from types import SimpleNamespace

import pytest

from app.diagnostics import LogStore
from app.engine.models import WorkflowValidationResult
from app.runs.orchestrator import RunOrchestrator
from app.runs.queue_service import WorkflowRunQueueService
from app.workflows.fp8_compatibility import (
    FP8_INCOMPATIBLE_MPS_ERROR_CODE,
    Fp8CompatibilityChecker,
    default_mps_execution_active,
)
from app.workflows.package import WorkflowPackage

from tests.fp8_test_utils import write_safetensors


def _package(workflow_id: str = "wf-fp8", filename: str = "model-fp8.safetensors"):
    return WorkflowPackage(
        metadata={"id": workflow_id, "name": "FP8 Workflow", "version": "0.1.0"},
        engine="comfyui",
        required_models=[{"folder": "diffusion_models", "filename": filename}],
        comfyui_graph={},
        dashboard={
            "version": "0.1.0",
            "status": "configured",
            "sections": [
                {
                    "id": "s1",
                    "title": "Main",
                    "controls": [{"id": "c1", "type": "text", "label": "Prompt"}],
                }
            ],
        },
    )


def _checker(
    tmp_path,
    *,
    mps_active=True,
    overridden=frozenset(),
    resolver=None,
    use_existing_converted_model=None,
):
    if resolver is None:
        fp8_file = write_safetensors(
            tmp_path / "diffusion_models" / "model-fp8.safetensors",
            {"blocks.0.weight": ("F8_E4M3", [64, 128])},
        )
        resolver = lambda model: fp8_file  # noqa: E731
    return Fp8CompatibilityChecker(
        resolve_local_model_path=resolver,
        mps_execution_active=lambda: mps_active,
        overridden_model_keys=lambda workflow_id: set(overridden),
        use_existing_converted_model=use_existing_converted_model,
        log_store=LogStore(),
    )


def test_preflight_blocks_fp8_model_on_mps(tmp_path):
    checker = _checker(tmp_path, mps_active=True)
    result = checker.preflight_validation(_package())
    assert result is not None
    assert result.valid is False
    assert result.error_code == FP8_INCOMPATIBLE_MPS_ERROR_CODE
    assert result.error_category == "platform_compatibility"
    fp8_models = result.developer_details["fp8_models"]
    assert fp8_models[0]["folder"] == "diffusion_models"
    assert fp8_models[0]["filename"] == "model-fp8.safetensors"
    assert fp8_models[0]["fp8_dtypes"] == ["F8_E4M3"]


def test_preflight_never_triggers_off_mps(tmp_path):
    checker = _checker(tmp_path, mps_active=False)
    assert checker.preflight_validation(_package()) is None


def test_preflight_skips_overridden_requirements(tmp_path):
    checker = _checker(
        tmp_path,
        overridden={("diffusion_models", "model-fp8.safetensors")},
    )
    assert checker.preflight_validation(_package()) is None


def test_preflight_skips_when_existing_converted_artifact_is_applied(tmp_path):
    applied: list[tuple[str, str, str]] = []

    def use_existing(workflow_id, model, path):
        applied.append((workflow_id, model.folder, model.filename))
        assert path.name == "model-fp8.safetensors"
        return True

    checker = _checker(tmp_path, use_existing_converted_model=use_existing)

    assert checker.preflight_validation(_package()) is None
    assert applied == [("wf-fp8", "diffusion_models", "model-fp8.safetensors")]


def test_preflight_skips_missing_local_files(tmp_path):
    checker = _checker(tmp_path, resolver=lambda model: None)
    assert checker.preflight_validation(_package()) is None


def test_preflight_ignores_non_fp8_files(tmp_path):
    bf16_file = write_safetensors(
        tmp_path / "diffusion_models" / "model-fp8.safetensors",
        {"blocks.0.weight": ("BF16", [64, 128])},
    )
    checker = _checker(tmp_path, resolver=lambda model: bf16_file)
    assert checker.preflight_validation(_package()) is None


@pytest.mark.parametrize(
    ("system", "machine", "vram_mode", "expected"),
    [
        ("Darwin", "arm64", "normal", True),
        ("Darwin", "arm64", "auto", True),
        ("Darwin", "arm64", "cpu", False),
        ("Darwin", "x86_64", "normal", False),
        ("Linux", "x86_64", "normal", False),
        ("Windows", "AMD64", "normal", False),
    ],
)
def test_default_mps_execution_active(monkeypatch, system, machine, vram_mode, expected):
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.machine", lambda: machine)
    assert default_mps_execution_active(lambda: vram_mode) is expected


class _RefuseEnqueueQueueService(WorkflowRunQueueService):
    def enqueue(self, *args, **kwargs):
        raise AssertionError("run was enqueued despite the FP8 preflight block")


def _orchestrator(package: WorkflowPackage, fp8_block: WorkflowValidationResult):
    return RunOrchestrator(
        workflow_loader=SimpleNamespace(get_package=lambda workflow_id: package),
        runner_supervisor=SimpleNamespace(),
        log_store=LogStore(),
        memory_observer=None,
        job_workflows={},
        job_started_at={},
        job_run_requests={},
        job_memory_profile_fingerprints={},
        job_memory_signatures={},
        job_run_snapshots={},
        memory_retry_roots={},
        workflow_run_queue_service=_RefuseEnqueueQueueService(),
        validate_package=None,
        unavailable_package_reason=lambda pkg: None,
        apply_input_bindings=lambda pkg, inputs: {},
        ensure_workflow_runner=None,
        workflow_run_memory_decision=lambda **kwargs: None,
        evict_idle_runners=None,
        memory_status_payload=lambda **kwargs: {},
        record_memory_metric=lambda name: None,
        start_memory_sampling=lambda **kwargs: None,
        fp8_preflight_check=lambda pkg: fp8_block,
    )


@pytest.mark.anyio
async def test_run_endpoint_blocks_before_enqueue():
    package = _package()
    fp8_block = WorkflowValidationResult(
        workflow_id=package.metadata.id,
        valid=False,
        errors=["This workflow uses an FP8 model that is not supported on Apple Silicon."],
        error_category="platform_compatibility",
        error_code=FP8_INCOMPATIBLE_MPS_ERROR_CODE,
        developer_details={"fp8_models": []},
    )
    orchestrator = _orchestrator(package, fp8_block)
    started = datetime.now()
    result = await orchestrator.enqueue_workflow_run(package.metadata.id, {}, {})
    assert (datetime.now() - started).total_seconds() < 5
    assert isinstance(result, WorkflowValidationResult)
    assert result.error_code == FP8_INCOMPATIBLE_MPS_ERROR_CODE


@pytest.mark.anyio
async def test_run_endpoint_enqueues_when_no_fp8_block():
    package = _package()
    enqueued: list[str] = []

    class _RecordingQueueService(WorkflowRunQueueService):
        def enqueue(self, *args, **kwargs):
            enqueued.append(kwargs.get("workflow_id", "queued"))
            return super().enqueue(*args, **kwargs)

    orchestrator = _orchestrator(package, None)
    orchestrator.fp8_preflight_check = lambda pkg: None
    orchestrator.workflow_run_queue_service = _RecordingQueueService()
    result = await orchestrator.enqueue_workflow_run(package.metadata.id, {}, {})
    assert enqueued, "run should have been queued when no FP8 block applies"
    assert not isinstance(result, WorkflowValidationResult) or result.valid


@pytest.mark.anyio
async def test_run_preflight_checks_input_bound_package_so_bypassed_loras_are_ignored():
    package = WorkflowPackage(
        metadata={"id": "wf-lora", "name": "Lora Workflow", "version": "0.1.0"},
        engine="comfyui",
        required_models=[
            {"folder": "diffusion_models", "filename": "base.safetensors"},
            {
                "folder": "loras",
                "filename": "style-fp8.safetensors",
                "node_id": "12",
                "input_name": "lora_name",
                "model_type": "lora",
            },
        ],
        comfyui_graph={
            "12": {
                "class_type": "LoraLoader",
                "inputs": {"lora_name": "style-fp8.safetensors", "strength_model": 1.0},
            },
        },
        inputs=[
            {
                "id": "style_lora",
                "label": "Style LoRA",
                "control": "select",
                "binding": {"node_id": "12", "input_name": "lora_name"},
            }
        ],
        dashboard={
            "version": "0.1.0",
            "status": "configured",
            "sections": [
                {
                    "id": "s1",
                    "title": "Main",
                    "controls": [{"id": "c1", "type": "select", "label": "Style LoRA", "input_id": "style_lora"}],
                }
            ],
        },
    )
    seen: list[WorkflowPackage] = []
    orchestrator = _orchestrator(package, None)
    orchestrator.fp8_preflight_check = lambda pkg: (seen.append(pkg), None)[1]
    orchestrator.workflow_run_queue_service = WorkflowRunQueueService()

    await orchestrator.enqueue_workflow_run(package.metadata.id, {"style_lora": "None"}, {})

    assert seen, "fp8 preflight should have run"
    checked_filenames = [model.filename for model in seen[0].required_models]
    assert "style-fp8.safetensors" not in checked_filenames
    assert "base.safetensors" in checked_filenames
