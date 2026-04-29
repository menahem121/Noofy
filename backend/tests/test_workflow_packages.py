from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.engine.models import ModelInfo
from app.engine.service import EngineService
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


def test_text_to_image_package_loads() -> None:
    packages_dir = Path("app/workflows/packages")
    loader = WorkflowPackageLoader(packages_dir)

    package = loader.get_package("text_to_image_v0")

    assert package.metadata.id == "text_to_image_v0"
    assert package.engine == "comfyui"
    assert package.dashboard.sections


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
        engine_adapter=StubEngineAdapter([]),
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
        engine_adapter=StubEngineAdapter(
            [
                ModelInfo(
                    folder="checkpoints",
                    filename="v1-5-pruned-emaonly-fp16.safetensors",
                )
            ]
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
        engine_adapter=StubEngineAdapter([]),
        runtime_manager=StubRuntimeManager(),
        log_store=log_store,
    )

    result = await service.validate_workflow("text_to_image_v0")
    logs = service.list_logs()

    assert not result.valid
    assert logs.events[-1].level == "warning"
    assert logs.events[-1].message == "Workflow validation failed"
    assert logs.events[-1].details["missing_models"][0]["filename"] == "v1-5-pruned-emaonly-fp16.safetensors"
