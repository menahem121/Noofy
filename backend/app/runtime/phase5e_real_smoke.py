from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from app.engine.diagnostics import LogStore
from app.runtime.custom_nodes import CustomNodeWorkspaceMaterializer
from app.runtime.isolation import CapsuleLock, SmokeStageStatus, SmokeTestReport
from app.runtime.profiles import load_runtime_profile_catalog
from app.runtime.runner_process import RunnerLaunchSpec, RunnerProcessSupervisor
from app.runtime.smoke_test import RunnerSmokeTester, SmokeExecutionFixture
from app.runtime.supervisor import RunnerKind
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore
from app.workflows.importer import ImportedWorkflowPackageStore, NoofyArchiveImporter, imported_package_capsule_lock
from app.workflows.package import WorkflowPackage

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_WORKFLOWS_DIR = REPO_ROOT / "test_workflows"
DEFAULT_PROFILE_CATALOG_PATH = BACKEND_ROOT / "app/runtime/profile_catalog.json"
DEFAULT_WORK_DIR = Path("/tmp/noofy-phase5e-real-smoke")


@dataclass(frozen=True)
class RealSmokeScenario:
    name: str
    archive_name: str
    use_package_graph: bool
    timeout_seconds: float
    needs_model_view: bool = False
    needs_input_files: bool = False


SCENARIOS: dict[str, RealSmokeScenario] = {
    "core-empty": RealSmokeScenario(
        name="core-empty",
        archive_name="core_empty_image_smoke.noofy",
        use_package_graph=False,
        timeout_seconds=120,
    ),
    "core-sd15": RealSmokeScenario(
        name="core-sd15",
        archive_name="core_sd15_txt2img.noofy",
        use_package_graph=True,
        timeout_seconds=240,
        needs_model_view=True,
    ),
    "custom-no-deps": RealSmokeScenario(
        name="custom-no-deps",
        archive_name="custom_node_no_deps_success.noofy",
        use_package_graph=True,
        timeout_seconds=120,
        needs_input_files=True,
    ),
    "custom-with-deps": RealSmokeScenario(
        name="custom-with-deps",
        archive_name="custom_node_with_pypi_dep_success.noofy",
        use_package_graph=True,
        timeout_seconds=180,
        needs_input_files=True,
    ),
    "controlnet-two-model": RealSmokeScenario(
        name="controlnet-two-model",
        archive_name="exported-workflow-for-testing.noofy",
        use_package_graph=True,
        timeout_seconds=420,
        needs_model_view=True,
        needs_input_files=True,
    ),
}


@dataclass(frozen=True)
class RealSmokeConfig:
    comfyui_source_dir: Path
    python_executable: Path
    test_workflows_dir: Path
    work_dir: Path
    profile_catalog_path: Path
    model_view_dir: Path | None
    input_dir: Path | None
    startup_timeout_seconds: float
    health_poll_interval_seconds: float


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Noofy Phase 5e real staged ComfyUI smoke validation."
    )
    parser.add_argument(
        "--comfyui-source-dir",
        type=Path,
        required=True,
        help="ComfyUI source checkout used to stage runner workspaces.",
    )
    parser.add_argument(
        "--python-executable",
        type=Path,
        required=True,
        help="Python executable with ComfyUI runtime dependencies installed.",
    )
    parser.add_argument(
        "--test-workflows-dir",
        type=Path,
        default=DEFAULT_TEST_WORKFLOWS_DIR,
        help="Directory containing Phase 5e .noofy fixtures.",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="Scratch directory for imported packages, env manifests, and runner workspaces.",
    )
    parser.add_argument(
        "--profile-catalog",
        type=Path,
        default=DEFAULT_PROFILE_CATALOG_PATH,
        help="Runtime profile catalog used for custom-node workspace materialization.",
    )
    parser.add_argument(
        "--model-view-dir",
        type=Path,
        default=None,
        help="Model view exposed to staged runners. Defaults to <comfyui-source-dir>/models.",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Input image directory used to satisfy LoadImage fixtures. Defaults to <comfyui-source-dir>/input.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIOS),
        help="Scenario to run. May be provided multiple times. Defaults to all scenarios.",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=240,
        help="Seconds to wait for each staged ComfyUI runner to become healthy.",
    )
    parser.add_argument(
        "--health-poll-interval",
        type=float,
        default=0.5,
        help="Seconds between runner health checks.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the work directory before running validation.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path to write the validation summary JSON.",
    )
    return parser.parse_args(argv)


async def run_validation(
    config: RealSmokeConfig,
    scenario_names: list[str],
    *,
    clean: bool = False,
) -> dict[str, Any]:
    _validate_config(config)
    if clean and config.work_dir.exists():
        shutil.rmtree(config.work_dir)
    config.work_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for scenario_name in scenario_names:
        scenario = SCENARIOS[scenario_name]
        results.append(await run_scenario(config, scenario))

    passed = all(result["passed"] for result in results)
    return {
        "schema_version": "0.1.0",
        "status": "passed" if passed else "failed",
        "scenario_count": len(results),
        "passed_count": sum(1 for result in results if result["passed"]),
        "failed_count": sum(1 for result in results if not result["passed"]),
        "config": {
            "comfyui_source_dir": str(config.comfyui_source_dir),
            "python_executable": str(config.python_executable),
            "test_workflows_dir": str(config.test_workflows_dir),
            "work_dir": str(config.work_dir),
            "profile_catalog_path": str(config.profile_catalog_path),
            "model_view_dir": str(config.model_view_dir) if config.model_view_dir else None,
            "input_dir": str(config.input_dir) if config.input_dir else None,
        },
        "results": results,
    }


async def run_scenario(config: RealSmokeConfig, scenario: RealSmokeScenario) -> dict[str, Any]:
    archive_path = config.test_workflows_dir / scenario.archive_name
    package = _load_package(archive_path)
    capsule = imported_package_capsule_lock(package)
    store = _import_package(config, scenario)
    package_dir = store.package_dir(package)
    prompt = package.comfyui_graph if scenario.use_package_graph else _core_empty_prompt()
    fixture = SmokeExecutionFixture(
        name=f"phase5e-{scenario.name}",
        prompt=prompt,
        required_node_types=_prompt_node_types(prompt),
        expected_output_node_count=len(_output_node_ids(prompt)),
        expected_output_node_ids=_output_node_ids(prompt),
        timeout_seconds=scenario.timeout_seconds,
    )

    prepared = _prepare_workspace(
        config=config,
        capsule=capsule,
        package=package,
        package_dir=package_dir,
        needs_model_view=scenario.needs_model_view,
    )
    copied_inputs = _copy_prompt_input_files(prompt, config.input_dir, prepared.runner_workspace_path / "input")
    report = await _run_smoke(config, scenario, capsule, prepared, fixture)
    passed = _report_passed(report, capsule)
    return {
        "name": scenario.name,
        "archive": scenario.archive_name,
        "workflow_id": package.metadata.id,
        "passed": passed,
        "runner_workspace_path": str(prepared.runner_workspace_path),
        "dependency_env_path": str(prepared.dependency_env_path),
        "copied_input_files": copied_inputs,
        "required_node_types": list(fixture.required_node_types),
        "expected_output_node_ids": list(fixture.expected_output_node_ids),
        "smoke_report": report.model_dump(mode="json"),
    }


def _validate_config(config: RealSmokeConfig) -> None:
    required_paths = {
        "comfyui_source_dir": config.comfyui_source_dir,
        "python_executable": config.python_executable,
        "test_workflows_dir": config.test_workflows_dir,
        "profile_catalog_path": config.profile_catalog_path,
    }
    for label, path in required_paths.items():
        if not path.exists():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if config.model_view_dir is not None and not config.model_view_dir.exists():
        raise FileNotFoundError(f"model_view_dir does not exist: {config.model_view_dir}")
    if config.input_dir is not None and not config.input_dir.exists():
        raise FileNotFoundError(f"input_dir does not exist: {config.input_dir}")


def _load_package(archive_path: Path) -> WorkflowPackage:
    return NoofyArchiveImporter(
        archive_path.read_bytes(),
        original_filename=archive_path.name,
    ).normalize()


def _import_package(config: RealSmokeConfig, scenario: RealSmokeScenario) -> ImportedWorkflowPackageStore:
    archive_path = config.test_workflows_dir / scenario.archive_name
    store = ImportedWorkflowPackageStore(config.work_dir / "packages")
    store.import_archive(
        archive_path.read_bytes(),
        original_filename=scenario.archive_name,
        allow_unverified_community_preparation=True,
    )
    return store


def _prepare_workspace(
    *,
    config: RealSmokeConfig,
    capsule: CapsuleLock,
    package: WorkflowPackage,
    package_dir: Path,
    needs_model_view: bool,
):
    model_view_dir = config.model_view_dir if needs_model_view else None
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(config.work_dir / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(config.work_dir / "runner-workspaces"),
        comfyui_source_dir=config.comfyui_source_dir,
        model_view_dir=model_view_dir,
        runtime_profile_catalog=load_runtime_profile_catalog(config.profile_catalog_path),
        custom_node_materializer=CustomNodeWorkspaceMaterializer() if package.custom_nodes else None,
        custom_node_source_files_dir=package_dir / "source-files",
        log_store=LogStore(),
    )
    return preparer.prepare(capsule)


async def _run_smoke(
    config: RealSmokeConfig,
    scenario: RealSmokeScenario,
    capsule: CapsuleLock,
    prepared_workspace,
    fixture: SmokeExecutionFixture,
) -> SmokeTestReport:
    smoke_tester = RunnerSmokeTester(
        process_supervisor=RunnerProcessSupervisor(
            startup_timeout_seconds=config.startup_timeout_seconds,
            health_poll_interval_seconds=config.health_poll_interval_seconds,
        ),
        launch_spec_factory=lambda capsule_lock, workspace: RunnerLaunchSpec(
            runner_id=f"phase5e-real-{scenario.name}",
            kind=RunnerKind.ISOLATED_COMFYUI,
            fingerprint=capsule_lock.runtime.runner_fingerprint,
            python_executable=str(config.python_executable),
            working_dir=workspace.runner_workspace_path,
            dependency_env_path=workspace.dependency_env_path,
            runner_workspace_path=workspace.runner_workspace_path,
            extra_args=_runner_extra_args(workspace.runner_workspace_path, has_custom_nodes=bool(capsule.custom_nodes)),
        ),
        execution_fixture=fixture,
    )
    return await smoke_tester.run(capsule, prepared_workspace)


def _runner_extra_args(runner_workspace_path: Path, *, has_custom_nodes: bool) -> list[str]:
    args = [
        "--base-directory",
        str(runner_workspace_path),
        "--disable-auto-launch",
    ]
    if not has_custom_nodes:
        args.append("--disable-all-custom-nodes")
    return args


def _copy_prompt_input_files(
    prompt: dict[str, object],
    input_dir: Path | None,
    target_input_dir: Path,
) -> list[str]:
    image_paths = _prompt_load_image_paths(prompt)
    if not image_paths:
        return []
    if input_dir is None:
        raise FileNotFoundError("Prompt requires input images, but no input directory was provided.")

    copied: list[str] = []
    for relative_name in image_paths:
        source = input_dir / relative_name
        if not source.exists():
            raise FileNotFoundError(f"Prompt input image is missing: {source}")
        target = target_input_dir / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(relative_name)
    return copied


def _prompt_load_image_paths(prompt: dict[str, object]) -> list[str]:
    image_paths: set[str] = set()
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") not in {"LoadImage", "LoadImageMask"}:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        image = inputs.get("image")
        if isinstance(image, str) and image:
            image_paths.add(_normalize_comfyui_input_name(image))
    return sorted(image_paths)


def _normalize_comfyui_input_name(value: str) -> str:
    # ComfyUI exports can annotate clipspace inputs as "name.png [input]".
    cleaned = value.removesuffix(" [input]").strip()
    path = PurePosixPath(cleaned)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe ComfyUI input path: {value}")
    return str(path)


def _core_empty_prompt() -> dict[str, object]:
    return {
        "1": {
            "class_type": "EmptyImage",
            "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0x335577},
        },
        "2": {
            "class_type": "PreviewImage",
            "inputs": {"images": ["1", 0]},
        },
    }


def _prompt_node_types(prompt: dict[str, object]) -> list[str]:
    return sorted(
        {
            str(node["class_type"])
            for node in prompt.values()
            if isinstance(node, dict) and isinstance(node.get("class_type"), str)
        }
    )


def _output_node_ids(prompt: dict[str, object]) -> tuple[str, ...]:
    return tuple(
        sorted(
            str(node_id)
            for node_id, node in prompt.items()
            if isinstance(node, dict) and node.get("class_type") in {"PreviewImage", "SaveImage"}
        )
    )


def _report_passed(report: SmokeTestReport, capsule: CapsuleLock) -> bool:
    required = [
        report.dependency_env.status is SmokeStageStatus.PASSED,
        report.runner_health.status is SmokeStageStatus.PASSED,
        report.workflow_execution.status is SmokeStageStatus.PASSED,
    ]
    if capsule.custom_nodes:
        required.append(report.custom_node_import.status is SmokeStageStatus.PASSED)
    return all(required)


async def async_main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    comfyui_source_dir = args.comfyui_source_dir.resolve()
    model_view_dir = args.model_view_dir.resolve() if args.model_view_dir else comfyui_source_dir / "models"
    input_dir = args.input_dir.resolve() if args.input_dir else comfyui_source_dir / "input"
    config = RealSmokeConfig(
        comfyui_source_dir=comfyui_source_dir,
        # Keep the venv shim path intact. Resolving it can collapse
        # /path/to/venv/bin/python to the system interpreter and lose packages.
        python_executable=args.python_executable.expanduser().absolute(),
        test_workflows_dir=args.test_workflows_dir.resolve(),
        work_dir=args.work_dir.resolve(),
        profile_catalog_path=args.profile_catalog.resolve(),
        model_view_dir=model_view_dir,
        input_dir=input_dir,
        startup_timeout_seconds=args.startup_timeout,
        health_poll_interval_seconds=args.health_poll_interval,
    )
    scenario_names = args.scenario or list(SCENARIOS)
    summary = await run_validation(config, scenario_names, clean=args.clean)
    output = json.dumps(summary, indent=2, sort_keys=True)
    print(output, flush=True)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(output + "\n", encoding="utf-8")
    return 0 if summary["status"] == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    try:
        return asyncio.run(async_main(argv))
    except Exception as exc:
        print(f"Phase 5e real smoke validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
