from __future__ import annotations

from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.runtime.custom_nodes import CustomNodeWorkspaceMaterializer
from app.runtime.isolation import CapsuleLock, SmokeStageStatus
from app.runtime.profiles import load_runtime_profile_catalog
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.smoke_test import RunnerSmokeTester, SmokeExecutionFixture
from app.runtime.supervisor import RunnerKind
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore
from app.workflows.capsule import CapsuleLockLoader
from app.workflows.importer import (
    ImportedWorkflowPackageStore,
    NoofyArchiveImporter,
    imported_package_capsule_lock,
)


TEST_WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / "test_workflows"
BACKEND_DIR = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self) -> None:
        self.pid = 1701
        self.returncode = None
        self.stdout = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    async def wait(self) -> int:
        return self.returncode or 0


def _archive_bytes(name: str) -> bytes:
    return (TEST_WORKFLOWS_DIR / name).read_bytes()


def _normalize_fixture(name: str):
    return NoofyArchiveImporter(_archive_bytes(name), original_filename=name).normalize()


def _node_types(prompt: dict[str, object]) -> list[str]:
    return sorted(
        {
            str(node["class_type"])
            for node in prompt.values()
            if isinstance(node, dict) and isinstance(node.get("class_type"), str)
        }
    )


def _output_node_ids(prompt: dict[str, object]) -> list[str]:
    return sorted(
        str(node_id)
        for node_id, node in prompt.items()
        if isinstance(node, dict) and node.get("class_type") in {"PreviewImage", "SaveImage"}
    )


def _fixture_from_prompt(name: str, prompt: dict[str, object]) -> SmokeExecutionFixture:
    return SmokeExecutionFixture(
        name=name,
        prompt=prompt,
        required_node_types=_node_types(prompt),
        expected_output_node_count=len(_output_node_ids(prompt)) or None,
        expected_output_node_ids=_output_node_ids(prompt),
        timeout_seconds=5,
    )


def _core_empty_image_fixture() -> SmokeExecutionFixture:
    return SmokeExecutionFixture(
        name="phase5e-core-empty-image",
        prompt={
            "1": {
                "class_type": "EmptyImage",
                "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0},
            },
            "2": {
                "class_type": "PreviewImage",
                "inputs": {"images": ["1", 0]},
            },
        },
        required_node_types=("EmptyImage", "PreviewImage"),
        expected_output_node_count=1,
        expected_output_node_ids=("2",),
        timeout_seconds=5,
    )


def test_phase5e_fixture_directory_contains_expected_archives() -> None:
    fixture_names = {path.name for path in TEST_WORKFLOWS_DIR.glob("*.noofy")}

    assert {
        "core_empty_image_smoke.noofy",
        "core_sd15_txt2img.noofy",
        "core_missing_model.noofy",
        "core_unresolved_load_image.noofy",
        "custom_node_no_deps_success.noofy",
        "custom_node_with_pypi_dep_success.noofy",
        "custom_node_missing_registration_failure.noofy",
        "custom_node_fixture_does_not_exercise_node_failure.noofy",
        "exported-workflow-for-testing.noofy",
    } <= fixture_names
    assert (TEST_WORKFLOWS_DIR / "core_empty_image_smoke.json").exists()
    assert (TEST_WORKFLOWS_DIR / "core_sd15_txt2img.json").exists()


@pytest.mark.parametrize(
    ("archive_name", "model_folders", "custom_node_count", "unresolved_input_count"),
    [
        ("core_empty_image_smoke.noofy", [], 0, 1),
        ("core_sd15_txt2img.noofy", ["checkpoints"], 0, 0),
        ("core_missing_model.noofy", ["checkpoints"], 0, 0),
        ("core_unresolved_load_image.noofy", ["checkpoints"], 0, 1),
        ("custom_node_no_deps_success.noofy", [], 1, 1),
        ("custom_node_with_pypi_dep_success.noofy", [], 1, 1),
        ("custom_node_missing_registration_failure.noofy", [], 1, 1),
        ("custom_node_fixture_does_not_exercise_node_failure.noofy", [], 1, 1),
        ("exported-workflow-for-testing.noofy", ["checkpoints", "controlnet"], 5, 1),
    ],
)
def test_phase5e_noofy_fixtures_normalize_expected_metadata(
    archive_name: str,
    model_folders: list[str],
    custom_node_count: int,
    unresolved_input_count: int,
) -> None:
    package = _normalize_fixture(archive_name)

    assert [model.folder for model in package.required_models] == model_folders
    assert len(package.custom_nodes) == custom_node_count
    assert len(package.unresolved_runtime_inputs) == unresolved_input_count
    assert package.import_metadata is not None
    # M2 routing: needs_input_setup if unresolved inputs OR dashboard is not configured.
    # All test fixtures have not_configured dashboards, so all route to needs_input_setup.
    assert package.import_metadata.status == "needs_input_setup"


def test_phase5e_wrapped_noofy_archive_imports_without_macos_metadata(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages")

    package = store.import_archive(
        _archive_bytes("core_missing_model.noofy"),
        original_filename="core_missing_model.noofy",
    )

    package_dir = tmp_path / "packages" / "unknown" / "core_missing_model" / "0.1.0"
    assert package.metadata.id == "unknown__core_missing_model__0.1.0"
    assert (package_dir / "source-files" / "package.json").exists()
    assert not (package_dir / "source-files" / "core_missing_model" / "package.json").exists()
    assert not (package_dir / "source-files" / "__MACOSX").exists()


@pytest.mark.parametrize(
    ("archive_name", "expected_folders"),
    [
        ("custom_node_no_deps_success.noofy", {"ComfyUI_JPS-Nodes"}),
        ("custom_node_with_pypi_dep_success.noofy", {"comfyui-kjnodes"}),
        ("exported-workflow-for-testing.noofy", {
            "ComfyUI_JPS-Nodes",
            "comfyui-image-blender",
            "comfyui-inpaint-nodes",
            "comfyui-kjnodes",
            "comfyui_controlnet_aux",
        }),
    ],
)
def test_phase5e_custom_node_fixtures_materialize_only_into_runner_workspace(
    tmp_path: Path,
    archive_name: str,
    expected_folders: set[str],
) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages")
    package = store.import_archive(
        _archive_bytes(archive_name),
        original_filename=archive_name,
        allow_unverified_community_preparation=True,
    )
    package_dir = store.package_dir(package)
    capsule = CapsuleLockLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    ).get_capsule_lock(package.metadata.id)
    trusted_core = tmp_path / "trusted-core"
    trusted_core_custom_nodes = trusted_core / "custom_nodes"
    trusted_core_custom_nodes.mkdir(parents=True)
    (trusted_core_custom_nodes / "trusted.py").write_text("x = 1\n", encoding="utf-8")
    (trusted_core / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (trusted_core / "folder_paths.py").write_text("# fake\n", encoding="utf-8")

    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        comfyui_source_dir=trusted_core,
        runtime_profile_catalog=load_runtime_profile_catalog(BACKEND_DIR / "app/runtime/profile_catalog.json"),
        custom_node_materializer=CustomNodeWorkspaceMaterializer(),
        custom_node_source_files_dir=package_dir / "source-files",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    materialized = {
        path.name for path in (prepared.runner_workspace_path / "custom_nodes").iterdir()
    }
    assert expected_folders <= materialized
    assert (trusted_core_custom_nodes / "trusted.py").exists()


@pytest.mark.anyio
async def test_phase5e_core_empty_image_smoke_fixture_passes_minimal_execution(tmp_path: Path) -> None:
    report = await _run_fake_smoke(
        imported_package_capsule_lock(_normalize_fixture("core_empty_image_smoke.noofy")),
        tmp_path,
        execution_fixture=_core_empty_image_fixture(),
        registered_node_types={"EmptyImage", "PreviewImage"},
        output_node_ids=["2"],
    )

    assert report.workflow_execution.status is SmokeStageStatus.PASSED
    assert report.custom_node_import.status is SmokeStageStatus.SKIPPED


@pytest.mark.anyio
async def test_phase5e_custom_node_fixture_exercises_declared_node(tmp_path: Path) -> None:
    package = _normalize_fixture("custom_node_no_deps_success.noofy")
    capsule = imported_package_capsule_lock(package)
    fixture = _fixture_from_prompt("custom-node-no-deps", package.comfyui_graph)

    report = await _run_fake_smoke(
        capsule,
        tmp_path,
        execution_fixture=fixture,
        registered_node_types=set(fixture.required_node_types),
        output_node_ids=["3"],
    )

    assert report.custom_node_import.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.details["exercised_custom_node_types"] == [
        "Crop Image TargetSize (JPS)"
    ]


@pytest.mark.anyio
async def test_phase5e_custom_node_fixture_missing_registration_fails_before_execution(
    tmp_path: Path,
) -> None:
    package = _normalize_fixture("custom_node_missing_registration_failure.noofy")

    report = await _run_fake_smoke(
        imported_package_capsule_lock(package),
        tmp_path,
        execution_fixture=_fixture_from_prompt("missing-registration", package.comfyui_graph),
        registered_node_types={"LoadImage", "Crop Image TargetSize (JPS)", "SaveImage"},
        output_node_ids=["3"],
    )

    assert report.custom_node_import.status is SmokeStageStatus.FAILED
    assert report.custom_node_import.details["missing_node_types"] == ["ExpectedNode"]
    assert report.workflow_execution.status is SmokeStageStatus.BLOCKED


@pytest.mark.anyio
async def test_phase5e_custom_node_fixture_that_skips_declared_node_fails_depth_check(
    tmp_path: Path,
) -> None:
    package = _normalize_fixture("custom_node_fixture_does_not_exercise_node_failure.noofy")
    fixture = SmokeExecutionFixture(
        name="does-not-exercise-custom-node",
        prompt={
            "1": {
                "class_type": "EmptyImage",
                "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0},
            },
            "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
        },
        required_node_types=("EmptyImage", "PreviewImage"),
        expected_output_node_ids=("2",),
        timeout_seconds=5,
    )

    report = await _run_fake_smoke(
        imported_package_capsule_lock(package),
        tmp_path,
        execution_fixture=fixture,
        registered_node_types={"EmptyImage", "PreviewImage", "Crop Image TargetSize (JPS)"},
        output_node_ids=["2"],
    )

    assert report.custom_node_import.status is SmokeStageStatus.PASSED
    assert report.workflow_execution.status is SmokeStageStatus.FAILED
    assert report.workflow_execution.details["declared_custom_node_types"] == [
        "Crop Image TargetSize (JPS)"
    ]


def test_phase5e_controlnet_fixture_tracks_two_model_folders() -> None:
    package = _normalize_fixture("exported-workflow-for-testing.noofy")
    capsule = imported_package_capsule_lock(package)

    assert {(model.folder, model.filename) for model in package.required_models} == {
        ("checkpoints", "DreamShaperXL_Lightning.safetensors"),
        ("controlnet", "diffusion_pytorch_model_promax.safetensors"),
    }
    assert {(model.comfyui_folder, model.filename) for model in capsule.models} == {
        ("checkpoints", "DreamShaperXL_Lightning.safetensors"),
        ("controlnet", "diffusion_pytorch_model_promax.safetensors"),
    }


async def _run_fake_smoke(
    capsule: CapsuleLock,
    tmp_path: Path,
    *,
    execution_fixture: SmokeExecutionFixture,
    registered_node_types: set[str],
    output_node_ids: list[str],
):
    process = FakeProcess()

    async def process_factory(command: list[str], **kwargs):
        return process

    async def healthy(base_url: str):
        return True, None

    async def object_info(base_url: str):
        return {node_type: {} for node_type in registered_node_types}

    async def prompt_executor(base_url: str, prompt: dict[str, object], timeout_seconds: float):
        return {
            "prompt_id": "phase5e-fixture",
            "output_node_count": len(output_node_ids),
            "output_node_ids": output_node_ids,
        }

    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        log_store=LogStore(),
    )
    prepared = preparer.prepare(capsule)
    smoke_tester = RunnerSmokeTester(
        process_supervisor=RunnerProcessSupervisor(
            process_factory=process_factory,
            health_check=healthy,
            startup_timeout_seconds=0.1,
            health_poll_interval_seconds=0.001,
        ),
        launch_spec_factory=lambda capsule_lock, prepared_workspace: RunnerLaunchSpec(
            runner_id="phase5e-fixture",
            kind=RunnerKind.ISOLATED_COMFYUI,
            fingerprint=capsule_lock.runtime.runner_fingerprint,
            python_executable="/opt/noofy/python",
            working_dir=prepared_workspace.runner_workspace_path,
            dependency_env_path=prepared_workspace.dependency_env_path,
            runner_workspace_path=prepared_workspace.runner_workspace_path,
            port=9191,
        ),
        execution_fixture=execution_fixture,
        object_info_fetcher=object_info,
        prompt_executor=prompt_executor,
    )

    return await smoke_tester.run(capsule, prepared)
