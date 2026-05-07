from __future__ import annotations

from pathlib import Path

import pytest

from app.runtime import phase5e_real_smoke as real_smoke
from app.runtime.isolation import SmokeStageResult, SmokeStageStatus, SmokeTestReport


def _fake_comfyui_source(tmp_path: Path) -> Path:
    source = tmp_path / "ComfyUI"
    source.mkdir()
    (source / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    return source


def _config(tmp_path: Path) -> real_smoke.RealSmokeConfig:
    source = _fake_comfyui_source(tmp_path)
    model_view = source / "models"
    input_dir = source / "input"
    model_view.mkdir()
    input_dir.mkdir()
    (input_dir / "71clYSlmspL._AC_UF1000,1000_QL80_.jpg").write_bytes(b"fake image")
    return real_smoke.RealSmokeConfig(
        comfyui_source_dir=source,
        python_executable=source / "main.py",
        test_workflows_dir=real_smoke.DEFAULT_TEST_WORKFLOWS_DIR,
        work_dir=tmp_path / "phase5e",
        profile_catalog_path=real_smoke.DEFAULT_PROFILE_CATALOG_PATH,
        model_view_dir=model_view,
        input_dir=input_dir,
        startup_timeout_seconds=1,
        health_poll_interval_seconds=0.01,
    )


def _passed_report(*, custom_nodes: bool) -> SmokeTestReport:
    return SmokeTestReport(
        dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
        custom_node_import=SmokeStageResult(
            status=SmokeStageStatus.PASSED if custom_nodes else SmokeStageStatus.SKIPPED
        ),
        runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
        workflow_execution=SmokeStageResult(status=SmokeStageStatus.PASSED),
    )


def test_prompt_load_image_paths_normalize_clipspace_suffix() -> None:
    prompt = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {
                "image": "clipspace/clipspace-painted-masked-1777621230412.png [input]"
            },
        },
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": "71clYSlmspL._AC_UF1000,1000_QL80_.jpg"},
        },
    }

    assert real_smoke._prompt_load_image_paths(prompt) == [
        "71clYSlmspL._AC_UF1000,1000_QL80_.jpg",
        "clipspace/clipspace-painted-masked-1777621230412.png",
    ]


def test_runner_extra_args_disable_custom_nodes_only_for_core_workflows(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "runner"

    assert "--disable-all-custom-nodes" in real_smoke._runner_extra_args(
        workspace,
        has_custom_nodes=False,
    )
    assert "--disable-all-custom-nodes" not in real_smoke._runner_extra_args(
        workspace,
        has_custom_nodes=True,
    )


@pytest.mark.anyio
async def test_async_main_preserves_venv_python_shim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _fake_comfyui_source(tmp_path)
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    shim = venv_bin / "python"
    target = tmp_path / "system-python"
    target.write_text("", encoding="utf-8")
    shim.symlink_to(target)
    captured = None

    async def fake_run_validation(config, scenario_names, *, clean=False):
        nonlocal captured
        captured = config.python_executable
        return {"status": "passed"}

    monkeypatch.setattr(real_smoke, "run_validation", fake_run_validation)

    result = await real_smoke.async_main(
        [
            "--comfyui-source-dir",
            str(source),
            "--python-executable",
            str(shim),
            "--test-workflows-dir",
            str(real_smoke.DEFAULT_TEST_WORKFLOWS_DIR),
            "--work-dir",
            str(tmp_path / "work"),
            "--scenario",
            "core-empty",
        ]
    )

    assert result == 0
    assert captured == shim.absolute()
    assert captured != target.resolve()


@pytest.mark.anyio
async def test_run_validation_summarizes_selected_scenarios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_run_smoke(
        config, scenario, capsule, prepared_workspace, fixture, *, log_store
    ):
        calls.append(scenario.name)
        return _passed_report(custom_nodes=bool(capsule.custom_nodes))

    monkeypatch.setattr(real_smoke, "_run_smoke", fake_run_smoke)

    summary = await real_smoke.run_validation(
        _config(tmp_path),
        ["core-empty", "custom-no-deps"],
        clean=True,
    )

    assert summary["status"] == "passed"
    assert summary["scenario_count"] == 2
    assert summary["passed_count"] == 2
    assert calls == ["core-empty", "custom-no-deps"]
    custom_result = summary["results"][1]
    assert custom_result["copied_input_files"] == [
        "71clYSlmspL._AC_UF1000,1000_QL80_.jpg"
    ]
    assert "Crop Image TargetSize (JPS)" in custom_result["required_node_types"]
