import json
from pathlib import Path

import pytest

from app.runtime.isolation import TrustLevel
from app.workflows.capsule import CAPSULE_LOCK_FILENAME, CapsuleLockLoader


def _write_capsule(directory: Path, package_id: str, *, fingerprint: str | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fingerprint = fingerprint or f"phase3-{package_id}-fp"
    payload = {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": package_id,
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "phase3-core",
        },
        "runtime": {
            "dependency_env_fingerprint": "phase3-dep",
            "runner_fingerprint": "phase3-runner",
            "capsule_fingerprint": fingerprint,
            "os": "any",
            "architecture": "any",
            "python_version": "3.11",
            "gpu_backend": "any",
            "dependency_lock_hash": "phase3-deps",
            "runner_workspace_hash": "phase3-workspace",
        },
        "custom_nodes": [],
        "dependencies": {"lock_file": "phase3", "install_policy": "core_only_no_community"},
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }
    (directory / CAPSULE_LOCK_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_loader_reads_bundled_capsule_for_text_to_image_v0() -> None:
    loader = CapsuleLockLoader(Path("app/workflows/packages"))

    lock = loader.get_capsule_lock("text_to_image_v0")

    assert lock.workflow.package_id == "text_to_image_v0"
    assert lock.workflow.trust_level is TrustLevel.NOOFY_VERIFIED
    assert lock.runtime.capsule_fingerprint == "phase3-text_to_image_v0-0.1.0"
    assert [model.filename for model in lock.models] == ["v1-5-pruned-emaonly-fp16.safetensors"]
    assert lock.models[0].sha256 == "e9476a13728cd75d8279f6ec8bad753a66a1957ca375a1464dc63b37db6e3916"


def test_loader_raises_for_unknown_workflow(tmp_path: Path) -> None:
    loader = CapsuleLockLoader(tmp_path)

    with pytest.raises(KeyError):
        loader.get_capsule_lock("missing")


def test_loader_falls_back_to_user_dir_when_bundled_missing(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_capsule(user / "user_only_workflow", "user_only_workflow")

    loader = CapsuleLockLoader(bundled, user_packages_dir=user)

    lock = loader.get_capsule_lock("user_only_workflow")
    assert lock.workflow.package_id == "user_only_workflow"


def test_user_capsule_cannot_shadow_bundled_capsule(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_capsule(bundled / "shared_id", "shared_id", fingerprint="bundled-fp")
    _write_capsule(user / "shared_id", "shared_id", fingerprint="user-fp")

    loader = CapsuleLockLoader(bundled, user_packages_dir=user)

    lock = loader.get_capsule_lock("shared_id")
    assert lock.runtime.capsule_fingerprint == "bundled-fp"


def test_get_bundled_capsule_lock_ignores_user_only_locks(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_capsule(user / "user_only", "user_only", fingerprint="user-fp")

    loader = CapsuleLockLoader(bundled, user_packages_dir=user)

    with pytest.raises(KeyError):
        loader.get_bundled_capsule_lock("user_only")


def test_list_capsule_locks_deduplicates_by_package_id(tmp_path: Path) -> None:
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_capsule(bundled / "alpha", "alpha", fingerprint="bundled-alpha")
    _write_capsule(user / "alpha", "alpha", fingerprint="user-alpha")
    _write_capsule(user / "beta", "beta", fingerprint="user-beta")

    loader = CapsuleLockLoader(bundled, user_packages_dir=user)
    locks = loader.list_capsule_locks()

    by_id = {lock.workflow.package_id: lock for lock in locks}
    assert set(by_id) == {"alpha", "beta"}
    assert by_id["alpha"].runtime.capsule_fingerprint == "bundled-alpha"
