import subprocess
import sys

from app.runtime.fingerprints import (
    canonical_json_bytes,
    capsule_fingerprint,
    dependency_env_fingerprint,
    runner_workspace_fingerprint,
    sha256_fingerprint,
)
from app.runtime.dependencies.isolation import CustomNodeLock, ModelLock, TrustLevel, TrustMetadata


def _model(model_id: str, sha_char: str) -> ModelLock:
    return ModelLock(
        id=model_id,
        sha256=sha_char * 64,
        size_bytes=123,
        source_urls=["https://example.invalid/model.safetensors"],
        comfyui_folder="checkpoints",
        filename=f"{model_id}.safetensors",
    )


def test_canonical_json_is_stable_for_key_order() -> None:
    left = {"b": [2, 1], "a": {"z": True, "m": None}}
    right = {"a": {"m": None, "z": True}, "b": [2, 1]}

    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_fingerprint(left) == sha256_fingerprint(right)


def test_dependency_env_fingerprint_changes_when_identity_changes() -> None:
    base = dependency_env_fingerprint(
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        os_name="darwin",
        architecture="arm64",
        python_build_id="cpython-3.11-noofy-1",
        torch_wheel_build_tag="torch-mps",
        torch_backend="mps",
        dependency_lock_hash="sha256:" + ("a" * 64),
        native_dependency_constraints={"metal": "required"},
        install_policy_version="1",
    )
    changed = dependency_env_fingerprint(
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        os_name="darwin",
        architecture="arm64",
        python_build_id="cpython-3.11-noofy-1",
        torch_wheel_build_tag="torch-cpu",
        torch_backend="cpu",
        dependency_lock_hash="sha256:" + ("a" * 64),
        native_dependency_constraints={"metal": "required"},
        install_policy_version="1",
    )

    assert base.startswith("sha256:")
    assert base != changed


def test_runner_workspace_fingerprint_depends_on_dependency_env() -> None:
    first = runner_workspace_fingerprint(
        dependency_env_fingerprint="sha256:" + ("a" * 64),
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        comfyui_source_hash="sha256:" + ("b" * 64),
        comfyui_frontend_version="development-reference",
        enabled_custom_node_manifest_hash="sha256:" + ("c" * 64),
        launch_config_hash="sha256:" + ("d" * 64),
        model_view_hash="sha256:" + ("e" * 64),
    )
    second = runner_workspace_fingerprint(
        dependency_env_fingerprint="sha256:" + ("f" * 64),
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        comfyui_source_hash="sha256:" + ("b" * 64),
        comfyui_frontend_version="development-reference",
        enabled_custom_node_manifest_hash="sha256:" + ("c" * 64),
        launch_config_hash="sha256:" + ("d" * 64),
        model_view_hash="sha256:" + ("e" * 64),
    )

    assert first != second


def test_fingerprints_include_runtime_profile_identity() -> None:
    base = dependency_env_fingerprint(
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        os_name="darwin",
        architecture="arm64",
        python_build_id="cpython-3.11-noofy-1",
        torch_wheel_build_tag="torch-mps",
        torch_backend="mps",
        dependency_lock_hash="sha256:" + ("a" * 64),
        native_dependency_constraints={},
        install_policy_version="1",
    )
    changed_profile = dependency_env_fingerprint(
        runtime_profile_id="noofy-comfyui-v1-next",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        runtime_profile_variant_id="darwin-arm64-mps-dev",
        os_name="darwin",
        architecture="arm64",
        python_build_id="cpython-3.11-noofy-1",
        torch_wheel_build_tag="torch-mps",
        torch_backend="mps",
        dependency_lock_hash="sha256:" + ("a" * 64),
        native_dependency_constraints={},
        install_policy_version="1",
    )

    assert base != changed_profile


def test_dependency_env_fingerprint_is_stable_across_process_restarts() -> None:
    script = """
from app.runtime.fingerprints import dependency_env_fingerprint
print(dependency_env_fingerprint(
    runtime_profile_id='noofy-comfyui-v1-default',
    runtime_profile_manifest_hash='sha256:' + ('9' * 64),
    runtime_profile_variant_id='darwin-arm64-mps-dev',
    os_name='darwin',
    architecture='arm64',
    python_build_id='cpython-3.11-noofy-1',
    torch_wheel_build_tag='torch-mps',
    torch_backend='mps',
    dependency_lock_hash='sha256:' + ('a' * 64),
    native_dependency_constraints={'metal': 'required'},
    install_policy_version='1',
))
"""

    first = subprocess.check_output([sys.executable, "-c", script], text=True).strip()
    second = subprocess.check_output([sys.executable, "-c", script], text=True).strip()

    assert first == second
    assert first.startswith("sha256:")


def test_capsule_fingerprint_sorts_model_and_custom_node_inputs() -> None:
    trust = TrustMetadata(level=TrustLevel.NOOFY_VERIFIED, publisher="Noofy")
    node_a = CustomNodeLock(
        package_id="alpha",
        source="https://example.invalid/alpha.git",
        commit="abc",
        trust_level=TrustLevel.NOOFY_VERIFIED,
    )
    node_b = CustomNodeLock(
        package_id="beta",
        source="https://example.invalid/beta.git",
        commit="def",
        trust_level=TrustLevel.NOOFY_VERIFIED,
    )

    first = capsule_fingerprint(
        workflow_package_hash="sha256:" + ("1" * 64),
        graph_hash="sha256:" + ("2" * 64),
        dashboard_schema_hash="sha256:" + ("3" * 64),
        model_requirements=[_model("b", "b"), _model("a", "a")],
        custom_nodes=[node_b, node_a],
        trust=trust,
        runner_fingerprint="sha256:" + ("4" * 64),
    )
    second = capsule_fingerprint(
        workflow_package_hash="sha256:" + ("1" * 64),
        graph_hash="sha256:" + ("2" * 64),
        dashboard_schema_hash="sha256:" + ("3" * 64),
        model_requirements=[_model("a", "a"), _model("b", "b")],
        custom_nodes=[node_a, node_b],
        trust=trust,
        runner_fingerprint="sha256:" + ("4" * 64),
    )

    assert first == second
