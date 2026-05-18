import json
from pathlib import Path

import pytest

from app.artifacts import ModelVerificationLevel
from app.diagnostics import LogStore
from app.engine.models import ModelInfo
from app.engine.service import EngineService
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerSupervisor,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class StubEngineAdapter:
    def __init__(self, models: list[ModelInfo]) -> None:
        self.models = models

    async def list_available_models(self) -> list[ModelInfo]:
        return self.models

    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        pass


def _supervisor_with(adapter: StubEngineAdapter) -> RunnerSupervisor:
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url=StubRuntimeManager.base_url,
            ws_url=StubRuntimeManager.ws_url,
            fingerprint=CORE_RUNNER_FINGERPRINT,
        ),
        adapter,
    )
    return supervisor


def test_text_to_image_package_loads() -> None:
    packages_dir = Path("app/workflows/packages")
    loader = WorkflowPackageLoader(packages_dir)

    package = loader.get_package("text_to_image_v0")

    assert package.metadata.id == "text_to_image_v0"
    assert package.engine == "comfyui"
    assert package.dashboard.sections
    assert package.smoke_tests.workflow_execution is not None
    assert package.smoke_tests.workflow_execution.name == "default-core-empty-image"
    assert package.smoke_tests.workflow_execution.required_node_types == ["EmptyImage", "SaveImage"]
    assert package.required_models[0].size_bytes == 2132696762
    assert package.required_models[0].verification_level is ModelVerificationLevel.SHA256_SIZE


def test_loader_enriches_weak_package_model_identity_from_capsule_lock(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "weak_identity"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        json.dumps(
            {
                "metadata": {"id": "weak_identity", "name": "Weak Identity", "version": "0.1.0"},
                "engine": "comfyui",
                "required_models": [
                    {
                        "folder": "checkpoints",
                        "filename": "demo.safetensors",
                        "source_url": "https://example.test/demo.safetensors",
                    }
                ],
                "comfyui_graph": {},
            }
        ),
        encoding="utf-8",
    )
    (package_dir / "capsule.lock.json").write_text(
        json.dumps(
            {
                "models": [
                    {
                        "comfyui_folder": "checkpoints",
                        "filename": "demo.safetensors",
                        "sha256": "a" * 64,
                        "size_bytes": 123,
                        "source_urls": ["https://example.test/demo.safetensors"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package("weak_identity")

    model = package.required_models[0]
    assert model.checksum == f"sha256:{'a' * 64}"
    assert model.size_bytes == 123
    assert model.verification_level is ModelVerificationLevel.SHA256_SIZE


def test_bundled_package_model_identity_is_not_weaker_than_capsule_lock() -> None:
    packages_dir = Path("app/workflows/packages")
    for capsule_file in packages_dir.glob("*/capsule.lock.json"):
        package_file = capsule_file.parent / "package.json"
        package_data = json.loads(package_file.read_text(encoding="utf-8"))
        capsule_data = json.loads(capsule_file.read_text(encoding="utf-8"))
        package_models = {
            (model.get("folder"), model.get("filename")): model
            for model in package_data.get("required_models", [])
            if isinstance(model, dict)
        }
        for locked in capsule_data.get("models", []):
            key = (locked.get("comfyui_folder"), locked.get("filename"))
            package_model = package_models.get(key)
            assert package_model is not None, f"{package_file} is missing locked model {key}"
            expected_checksum = f"sha256:{locked['sha256']}"
            assert package_model.get("checksum") == expected_checksum
            assert package_model.get("size_bytes") == locked.get("size_bytes")
            assert package_model.get("verification_level") == ModelVerificationLevel.SHA256_SIZE.value


def test_engine_service_workflow_summary_includes_phase6_trust_metadata() -> None:
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(StubEngineAdapter([])),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
    )

    summary = service.list_workflows()[0]

    assert summary["trust_level"] == "noofy_verified"
    assert summary["trust"]["label"] == "Noofy Verified"
    assert summary["trust"]["source_policy"] == "noofy_verified_sources_only"
    assert summary["trust"]["requires_explicit_opt_in"] is False


def test_workflow_package_can_declare_execution_smoke_fixture(tmp_path: Path) -> None:
    package_dir = tmp_path / "packages" / "fixture_workflow"
    package_dir.mkdir(parents=True)
    (package_dir / "package.json").write_text(
        """
        {
          "metadata": {
            "id": "fixture_workflow",
            "name": "Fixture workflow",
            "version": "0.1.0"
          },
          "engine": "comfyui",
          "comfyui_graph": {},
          "dashboard": {"version": "0.1.0", "sections": []},
          "smoke_tests": {
            "workflow_execution": {
              "name": "tiny-noop",
              "prompt": {
                "1": {"class_type": "NoOp", "inputs": {}}
              },
              "required_node_types": ["NoOp"],
              "expected_output_node_count": 1,
              "expected_output_node_ids": ["1"],
              "timeout_seconds": 5
            }
          }
        }
        """,
        encoding="utf-8",
    )

    package = WorkflowPackageLoader(tmp_path / "packages").get_package("fixture_workflow")

    assert package.smoke_tests.workflow_execution is not None
    assert package.smoke_tests.workflow_execution.name == "tiny-noop"
    assert package.smoke_tests.workflow_execution.required_node_types == ["NoOp"]
    assert package.smoke_tests.workflow_execution.expected_output_node_count == 1
    assert package.smoke_tests.workflow_execution.expected_output_node_ids == ["1"]
    assert package.smoke_tests.workflow_execution.timeout_seconds == 5


def test_validator_reports_missing_model() -> None:
    packages_dir = Path("app/workflows/packages")
    package = WorkflowPackageLoader(packages_dir).get_package("text_to_image_v0")

    validator = WorkflowPackageValidator()
    structure = validator.validate_structure(package)
    missing_models = validator.validate_models(package, available_models=set())
    result = validator.combine(package, structure, missing_models)

    assert not result.valid
    assert result.missing_models[0].filename == "v1-5-pruned-emaonly-fp16.safetensors"


def test_input_bindings_are_applied() -> None:
    package = WorkflowPackageLoader(Path("app/workflows/packages")).get_package("text_to_image_v0")
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(StubEngineAdapter([])),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
    )

    graph = service._apply_input_bindings(
        package,
        {
            "prompt": "test prompt",
            "seed": 123,
            "width": 768,
            "height": 640,
        },
    )

    assert graph["6"]["inputs"]["text"] == "test prompt"
    assert graph["3"]["inputs"]["seed"] == 123
    assert graph["5"]["inputs"]["width"] == 768
    assert graph["5"]["inputs"]["height"] == 640


@pytest.mark.anyio
async def test_engine_service_validates_models_from_adapter() -> None:
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(
            StubEngineAdapter(
                [
                    ModelInfo(
                        folder="checkpoints",
                        filename="v1-5-pruned-emaonly-fp16.safetensors",
                    )
                ]
            )
        ),
        runtime_manager=StubRuntimeManager(),
        log_store=LogStore(),
    )

    result = await service.validate_workflow("text_to_image_v0")

    assert result.valid
    assert result.missing_models == []


@pytest.mark.anyio
async def test_engine_service_logs_validation_failure() -> None:
    log_store = LogStore()
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(Path("app/workflows/packages")),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=_supervisor_with(StubEngineAdapter([])),
        runtime_manager=StubRuntimeManager(),
        log_store=log_store,
    )

    result = await service.validate_workflow("text_to_image_v0")
    logs = service.list_logs()

    assert not result.valid
    assert logs.events[-1].level == "warning"
    assert logs.events[-1].message == "Workflow validation failed"
    assert logs.events[-1].details["missing_models"][0]["filename"] == "v1-5-pruned-emaonly-fp16.safetensors"
