"""Tests for WorkflowPackageLoader bundled + user workflow merging."""

import json
from pathlib import Path

from app.workflows.loader import WorkflowPackageLoader

# Minimal valid package JSON structure.
_MINIMAL_GRAPH = {"1": {"class_type": "KSampler", "inputs": {}}}
_MINIMAL_DASHBOARD = {"version": "1", "sections": []}


def _write_package(directory: Path, package_id: str, name: str | None = None) -> Path:
    package_dir = directory / package_id
    package_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "metadata": {
            "id": package_id,
            "name": name or package_id,
            "version": "1.0.0",
        },
        "engine": "comfyui",
        "comfyui_graph": _MINIMAL_GRAPH,
        "dashboard": _MINIMAL_DASHBOARD,
    }
    package_file = package_dir / "package.json"
    package_file.write_text(json.dumps(data), encoding="utf-8")
    return package_file


def test_loader_returns_bundled_packages_when_no_user_dir(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_package(bundled, "wf_a")
    _write_package(bundled, "wf_b")

    loader = WorkflowPackageLoader(bundled)

    packages = loader.list_packages()
    ids = [p.metadata.id for p in packages]
    assert ids == ["wf_a", "wf_b"]


def test_loader_merges_user_and_bundled(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_package(bundled, "wf_a")
    _write_package(user, "wf_c", name="User C")

    loader = WorkflowPackageLoader(bundled, user_packages_dir=user)

    packages = loader.list_packages()
    ids = [p.metadata.id for p in packages]
    assert ids == ["wf_a", "wf_c"]


def test_user_workflow_does_not_silently_override_bundled_by_id(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_package(bundled, "wf_a", name="Bundled A")
    _write_package(user, "wf_a", name="User Override A")

    loader = WorkflowPackageLoader(bundled, user_packages_dir=user)

    package = loader.get_package("wf_a")
    assert package.metadata.name == "Bundled A"


def test_development_loader_can_allow_user_overrides(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_package(bundled, "wf_a", name="Bundled A")
    _write_package(user, "wf_a", name="User Override A")

    loader = WorkflowPackageLoader(bundled, user_packages_dir=user, allow_user_overrides=True)

    package = loader.get_package("wf_a")
    assert package.metadata.name == "User Override A"


def test_loader_returns_empty_when_dirs_missing(tmp_path: Path) -> None:
    loader = WorkflowPackageLoader(tmp_path / "missing", user_packages_dir=tmp_path / "also_missing")

    assert loader.list_packages() == []


def test_user_dir_none_is_backward_compatible(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    _write_package(bundled, "wf_x")

    loader = WorkflowPackageLoader(bundled, user_packages_dir=None)

    assert len(loader.list_packages()) == 1
    assert loader.get_package("wf_x").metadata.id == "wf_x"
