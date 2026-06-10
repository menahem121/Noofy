"""Launch-default wiring for isolated workflow runner launch specs."""

from pathlib import Path

import pytest

from app.runtime.dependencies.isolation import CapsuleLock
from app.runtime.runners.lifecycle_service import (
    _effective_launch_diagnostics,
    _workflow_runner_launch_spec,
)


class _FakeRuntimeManager:
    python_executable = "/fake/python"
    managed_host = "127.0.0.1"
    environment = None


def _capsule_data(
    *,
    gpu_backend: str = "mps",
    vram_mode: str = "auto",
    attention_backend: str = "auto",
    precision_policy: str = "auto",
    noofy_environment: dict[str, str] | None = None,
) -> dict:
    return {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "demo-workflow",
            "version": "1.0.0",
            "trust_level": "noofy_verified",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "sha256:" + ("a" * 64),
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "sha256:" + ("b" * 64),
            "runner_fingerprint": "sha256:" + ("c" * 64),
            "capsule_fingerprint": "sha256:" + ("d" * 64),
            "vram_mode": vram_mode,
            "attention_backend": attention_backend,
            "precision_policy": precision_policy,
            "noofy_environment": noofy_environment or {},
            "os": "darwin",
            "architecture": "arm64",
            "python_version": "3.13",
            "python_build_id": "cpython-3.13-noofy-v1",
            "gpu_backend": gpu_backend,
            "dependency_lock_hash": "sha256:" + ("e" * 64),
            "runner_workspace_hash": "sha256:" + ("f" * 64),
        },
        "custom_nodes": [],
        "dependencies": {
            "lock_file": "community-runtime.lock",
            "install_policy": "core_only_no_community",
        },
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }


def _launch_spec(tmp_path: Path, capsule_data: dict):
    capsule_lock = CapsuleLock.model_validate(capsule_data)
    return _workflow_runner_launch_spec(
        capsule_lock,
        dependency_env_path=tmp_path / "dependency-env",
        runner_workspace_path=tmp_path / "runner-workspace",
        runtime_manager=_FakeRuntimeManager(),
    )


def test_legacy_capsule_lock_without_launch_default_fields_still_launches(
    tmp_path: Path,
) -> None:
    """Capsule locks written before launch defaults existed must keep working."""
    data = _capsule_data()
    for legacy_missing in (
        "vram_mode",
        "attention_backend",
        "precision_policy",
        "noofy_environment",
    ):
        del data["runtime"][legacy_missing]

    spec = _launch_spec(tmp_path, data)

    assert "--use-pytorch-cross-attention" not in spec.extra_args
    assert "--cpu" not in spec.extra_args
    assert spec.env["NOOFY_WORKFLOW_ID"] == "demo-workflow"


def test_launch_spec_defaults_emit_no_vram_attention_or_precision_flags(
    tmp_path: Path,
) -> None:
    spec = _launch_spec(tmp_path, _capsule_data())

    assert "--preview-method" in spec.extra_args
    for flag in (
        "--use-pytorch-cross-attention",
        "--cpu",
        "--highvram",
        "--lowvram",
        "--novram",
    ):
        assert flag not in spec.extra_args


def test_launch_spec_emits_attention_flag_for_pytorch_sdpa_profile(
    tmp_path: Path,
) -> None:
    spec = _launch_spec(tmp_path, _capsule_data(attention_backend="pytorch_sdpa"))

    assert "--use-pytorch-cross-attention" in spec.extra_args


def test_launch_spec_forces_cpu_flag_for_cpu_backend_profile(tmp_path: Path) -> None:
    spec = _launch_spec(tmp_path, _capsule_data(gpu_backend="cpu"))

    assert spec.extra_args.count("--cpu") == 1

    # vram_mode "cpu" plus gpu_backend "cpu" must not duplicate the flag.
    spec = _launch_spec(
        tmp_path, _capsule_data(gpu_backend="cpu", vram_mode="cpu")
    )
    assert spec.extra_args.count("--cpu") == 1


def test_launch_spec_rejects_non_auto_precision_policy(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported ComfyUI precision policy"):
        _launch_spec(tmp_path, _capsule_data(precision_policy="force_fp16"))


def test_launch_spec_merges_profile_environment_without_overriding_noofy_keys(
    tmp_path: Path,
) -> None:
    spec = _launch_spec(
        tmp_path,
        _capsule_data(
            noofy_environment={
                "PYTORCH_ENABLE_MPS_FALLBACK": "1",
                "NOOFY_WORKFLOW_ID": "spoofed",
            }
        ),
    )

    assert spec.env["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"
    assert spec.env["NOOFY_WORKFLOW_ID"] == "demo-workflow"


def test_effective_launch_diagnostics_record_runtime_behavior(tmp_path: Path) -> None:
    capsule_lock = CapsuleLock.model_validate(
        _capsule_data(attention_backend="pytorch_sdpa")
    )
    spec = _workflow_runner_launch_spec(
        capsule_lock,
        dependency_env_path=tmp_path / "dependency-env",
        runner_workspace_path=tmp_path / "runner-workspace",
        runtime_manager=_FakeRuntimeManager(),
    )

    details = _effective_launch_diagnostics(capsule_lock, spec)

    assert details["runtime_profile_id"] == "noofy-comfyui-v1-default"
    assert details["runtime_profile_variant_id"] == "darwin-arm64-mps"
    assert details["comfyui_version"] == "milestone-1"
    assert details["python_version"] == "3.13"
    assert details["gpu_backend"] == "mps"
    assert details["attention_backend"] == "pytorch_sdpa"
    assert details["effective_attention"] == "pytorch_sdpa"
    assert "--use-pytorch-cross-attention" in details["launch_args"]
    assert details["vram_mode"] == "auto"
    assert details["precision_policy"] == "auto"
