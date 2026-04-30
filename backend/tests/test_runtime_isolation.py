import pytest
from pydantic import ValidationError

from app.runtime.isolation import (
    CapsuleLock,
    DependencyEnvManifest,
    InstallState,
    InstallStatus,
    RunnerWorkspaceManifest,
    SmokeTestStatus,
    TrustLevel,
)


def _valid_capsule_lock_data() -> dict:
    return {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "starter_text_to_image",
            "version": "1.0.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
            "signature": "sig",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "1.0.0",
            "core_source_hash": "sha256:core",
        },
        "runtime": {
            "dependency_env_fingerprint": "sha256:dep",
            "runner_fingerprint": "sha256:runner",
            "capsule_fingerprint": "sha256:capsule",
            "os": "darwin",
            "architecture": "arm64",
            "python_version": "3.11",
            "gpu_backend": "apple_mps",
            "dependency_lock_hash": "sha256:deps",
            "runner_workspace_hash": "sha256:workspace",
        },
        "custom_nodes": [],
        "dependencies": {
            "lock_file": "requirements.lock.json",
            "install_policy": "wheels_only_hash_required",
        },
        "models": [
            {
                "id": "model-id",
                "sha256": "sha256:" + ("a" * 64),
                "size_bytes": 123,
                "source_urls": ["https://example.invalid/model.safetensors"],
                "comfyui_folder": "checkpoints",
                "filename": "model.safetensors",
            }
        ],
        "hardware_observations": {
            "observed_peak_vram_mb": 8192,
            "observed_peak_ram_mb": 16384,
            "tested_resolution": "1024x1024",
            "tested_batch_size": 1,
            "gpu_name": "Apple M-series",
            "os": "macOS",
            "backend": "mps",
            "precision": "fp16",
            "recommended_vram_mb": 10240,
            "recommended_ram_mb": 20480,
        },
        "trust": {
            "level": "noofy_verified",
            "publisher": "Noofy",
            "signatures": ["sig"],
        },
    }


def test_capsule_lock_accepts_valid_data() -> None:
    lock = CapsuleLock.model_validate(_valid_capsule_lock_data())

    assert lock.workflow.publisher_id == "noofy"
    assert lock.workflow.trust_level is TrustLevel.NOOFY_VERIFIED
    assert lock.runtime.runner_fingerprint == "sha256:runner"
    assert lock.models[0].filename == "model.safetensors"


def test_capsule_lock_rejects_mutable_install_state_fields() -> None:
    data = _valid_capsule_lock_data()
    data["install"] = {"status": "ready"}

    with pytest.raises(ValidationError):
        CapsuleLock.model_validate(data)


def test_capsule_lock_rejects_invalid_model_hash() -> None:
    data = _valid_capsule_lock_data()
    data["models"][0]["sha256"] = "sha256:not-a-real-hash"

    with pytest.raises(ValidationError):
        CapsuleLock.model_validate(data)


def test_capsule_lock_rejects_model_path_traversal() -> None:
    data = _valid_capsule_lock_data()
    data["models"][0]["filename"] = "../escape.safetensors"

    with pytest.raises(ValidationError):
        CapsuleLock.model_validate(data)


def test_capsule_lock_rejects_zero_byte_model_records() -> None:
    data = _valid_capsule_lock_data()
    data["models"][0]["size_bytes"] = 0

    with pytest.raises(ValidationError):
        CapsuleLock.model_validate(data)


def test_capsule_lock_is_immutable_after_validation() -> None:
    lock = CapsuleLock.model_validate(_valid_capsule_lock_data())

    with pytest.raises(ValidationError):
        lock.schema_version = "0.2.0"


def test_install_state_is_mutable_local_state() -> None:
    state = InstallState(
        schema_version="0.1.0",
        capsule_fingerprint="sha256:capsule",
        status=InstallStatus.PREPARING,
        smoke_test_status=SmokeTestStatus.NOT_RUN,
    )

    state.status = InstallStatus.READY
    state.dependency_env_path = "runtime-store/envs/dep-env-sha256dep"
    state.runner_workspace_path = "runtime-store/runner-workspaces/runner-workspace-sha256runner"

    assert state.status is InstallStatus.READY
    assert state.dependency_env_path is not None
    assert state.runner_workspace_path is not None


def test_install_state_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        InstallState.model_validate(
            {
                "schema_version": "0.1.0",
                "capsule_fingerprint": "sha256:capsule",
                "status": "ready",
                "unexpected": True,
            }
        )


def test_dependency_env_manifest_validates_required_identity() -> None:
    manifest = DependencyEnvManifest(
        schema_version="0.1.0",
        fingerprint="sha256:dep",
        python_version="3.11",
        python_build_id="cpython-3.11-noofy-1",
        os="darwin",
        architecture="arm64",
        gpu_backend="apple_mps",
        dependency_lock_hash="sha256:deps",
        install_policy_version="1",
        status=InstallStatus.READY,
        smoke_test_status=SmokeTestStatus.PASSED,
    )

    assert manifest.fingerprint == "sha256:dep"
    assert manifest.status is InstallStatus.READY


def test_runner_workspace_manifest_links_dependency_env() -> None:
    manifest = RunnerWorkspaceManifest(
        schema_version="0.1.0",
        fingerprint="sha256:runner",
        dependency_env_fingerprint="sha256:dep",
        comfyui_version="1.0.0",
        comfyui_source_hash="sha256:core",
        enabled_custom_node_hash="sha256:nodes",
        launch_config_hash="sha256:launch",
        model_view_hash="sha256:models",
        status=InstallStatus.READY,
        smoke_test_status=SmokeTestStatus.PASSED,
    )

    assert manifest.dependency_env_fingerprint == "sha256:dep"
    assert manifest.model_view_hash == "sha256:models"
