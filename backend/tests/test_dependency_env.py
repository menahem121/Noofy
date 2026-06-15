import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.runtime.dependencies.dependency_env import (
    DependencyEnvironmentInstallError,
    DependencyEnvironmentInstallRequest,
    UvDependencyEnvironmentInstaller,
)
from app.runtime.dependencies.dependency_lock import (
    DEFAULT_APPROVED_INDEX_URL,
    DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    DependencyDistributionKind,
    DependencyPolicyErrorCode,
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyRequirement,
    ResolvedDependencyWheel,
    ResolverMetadata,
    with_computed_lock_hash,
)


def _lock_for_cached_wheel(cache_dir: Path) -> ResolvedDependencyLock:
    wheel_bytes = b"wheel bytes"
    wheel_path = cache_dir / "demo-1.0.0-py3-none-any.whl"
    wheel_path.write_bytes(wheel_bytes)
    digest = "sha256:" + hashlib.sha256(wheel_bytes).hexdigest()
    resolver = ResolverMetadata(name="uv", version="0.9.0")
    return with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id="noofy-comfyui-v1-default",
            runtime_profile_variant_id="darwin-arm64-mps",
            runtime_profile_manifest_hash="sha256:" + ("9" * 64),
            install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            resolver=resolver,
            wheels=[
                ResolvedDependencyWheel(
                    name="demo",
                    version="1.0.0",
                    wheel_filename=wheel_path.name,
                    sha256=digest,
                    source_kind=DependencySourceKind.APPROVED_CACHE,
                    approved_cache_ref=wheel_path.name,
                    platform_tags=["py3-none-any"],
                    relationship=DependencyRelationship.DIRECT,
                    requested_by=["custom-node-a"],
                    resolver_name=resolver.name,
                    resolver_version=resolver.version,
                )
            ],
        )
    )


def _v2_source_lock() -> ResolvedDependencyLock:
    runtime_lock = (
        "groundingdino-py==0.4.0 \\\n"
        f"    --hash=sha256:{'a' * 64}\n"
    )
    excludes = [
        "flash-attn",
        "numpy",
        "sageattention",
        "sageattn3",
        "torch",
        "torchaudio",
        "torchvision",
        "triton",
        "xformers",
    ]
    excludes_text = "\n".join(excludes) + "\n"
    build_constraints = (
        "setuptools==82.0.1 \\\n"
        f"    --hash=sha256:{'b' * 64}\n"
    )
    return with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id="noofy-comfyui-v1-default",
            runtime_profile_variant_id="linux-x64-cuda",
            runtime_profile_manifest_hash="sha256:" + ("9" * 64),
            python_version="3.13",
            python_platform="x86_64-unknown-linux-gnu",
            install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            resolver=ResolverMetadata(name="uv", version="0.11.10"),
            requirements=[
                ResolvedDependencyRequirement(
                    name="groundingdino-py",
                    version="0.4.0",
                    hashes=["sha256:" + ("a" * 64)],
                    relationship=DependencyRelationship.DIRECT,
                    distribution_kind=DependencyDistributionKind.SDIST,
                    distribution_filename="groundingdino-py-0.4.0.tar.gz",
                    distribution_url=(
                        "https://files.pythonhosted.org/"
                        "groundingdino-py-0.4.0.tar.gz"
                    ),
                    distribution_sha256="sha256:" + ("a" * 64),
                    source_index_url=DEFAULT_APPROVED_INDEX_URL,
                    build_system_requires=["setuptools>=40.8.0"],
                    legacy_setuptools_build=True,
                    dynamic_build_requirements_possible=True,
                )
            ],
            requirements_lock=runtime_lock,
            requirements_lock_hash=(
                "sha256:" + hashlib.sha256(runtime_lock.encode()).hexdigest()
            ),
            runtime_excludes=excludes,
            runtime_excludes_hash=(
                "sha256:" + hashlib.sha256(excludes_text.encode()).hexdigest()
            ),
            build_constraints=build_constraints,
            build_constraints_hash=(
                "sha256:"
                + hashlib.sha256(build_constraints.encode()).hexdigest()
            ),
            source_distributions=["groundingdino-py"],
            resolution_cutoff="2026-06-15T23:59:59Z",
            approved_index_url=DEFAULT_APPROVED_INDEX_URL,
            build_requirements_complete=False,
        )
    )


def test_uv_dependency_installer_writes_lock_requirements_and_runs_uv(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "wheel-cache"
    cache_dir.mkdir()
    lock = _lock_for_cached_wheel(cache_dir)
    commands: list[list[str]] = []

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        assert cwd == tmp_path / "stage"
        assert env["UV_NO_PROGRESS"] == "1"
        assert env["UV_NO_PYTHON_DOWNLOADS"] == "1"
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=cache_dir,
        command_runner=runner,
        log_store=LogStore(),
    )

    installer.install(
        DependencyEnvironmentInstallRequest(
            lock=lock,
            target_dir=tmp_path / "stage",
            python_version="3.13",
            workflow_id="workflow",
        )
    )

    requirements = (tmp_path / "stage" / "requirements.hashes.txt").read_text(
        encoding="utf-8"
    )
    assert "demo==1.0.0 --hash=sha256:" in requirements
    assert (tmp_path / "stage" / "noofy-dependency-lock.json").exists()
    assert commands[0][:4] == ["uv", "venv", "--python", "3.13"]
    assert commands[1][:4] == ["uv", "pip", "install", "--python"]
    assert "--require-hashes" in commands[1]
    assert "--only-binary" in commands[1]
    assert "--no-deps" in commands[1]
    assert "--no-index" in commands[1]
    assert "--find-links" in commands[1]


def test_uv_dependency_installer_can_create_env_with_runtime_python_executable(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "wheel-cache"
    cache_dir.mkdir()
    lock = _lock_for_cached_wheel(cache_dir)
    commands: list[list[str]] = []

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=cache_dir,
        command_runner=runner,
        log_store=LogStore(),
    )

    installer.install(
        DependencyEnvironmentInstallRequest(
            lock=lock,
            target_dir=tmp_path / "stage",
            python_version="3.14",
            python_executable="/opt/noofy/runtime/python",
            workflow_id="workflow",
        )
    )

    assert commands[0][:4] == [
        "uv",
        "venv",
        "--python",
        "/opt/noofy/runtime/python",
    ]


def test_uv_dependency_installer_preserves_explicit_lock_hash(tmp_path: Path) -> None:
    cache_dir = tmp_path / "wheel-cache"
    cache_dir.mkdir()
    lock = _lock_for_cached_wheel(cache_dir).model_copy(
        update={"lock_hash": "sha256:" + ("1" * 64)}
    )

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=cache_dir,
        command_runner=runner,
        log_store=LogStore(),
    )

    installer.install(
        DependencyEnvironmentInstallRequest(
            lock=lock,
            target_dir=tmp_path / "stage",
            python_version="3.13",
            workflow_id="workflow",
        )
    )

    assert '"lock_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111"' in (
        tmp_path / "stage" / "noofy-dependency-lock.json"
    ).read_text(
        encoding="utf-8"
    )


def test_uv_dependency_installer_rejects_unmaterialized_index_wheel(
    tmp_path: Path,
) -> None:
    resolver = ResolverMetadata(name="uv", version="0.9.0")
    lock = with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id="noofy-comfyui-v1-default",
            runtime_profile_variant_id="darwin-arm64-mps",
            runtime_profile_manifest_hash="sha256:" + ("9" * 64),
            install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            resolver=resolver,
            wheels=[
                ResolvedDependencyWheel(
                    name="demo",
                    version="1.0.0",
                    wheel_filename="demo-1.0.0-py3-none-any.whl",
                    sha256="sha256:" + ("a" * 64),
                    source_kind=DependencySourceKind.INDEX,
                    source_index_url="https://pypi.org/simple",
                    platform_tags=["py3-none-any"],
                    relationship=DependencyRelationship.DIRECT,
                    resolver_name=resolver.name,
                    resolver_version=resolver.version,
                )
            ],
        )
    )
    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=tmp_path / "wheel-cache", log_store=LogStore()
    )

    with pytest.raises(DependencyEnvironmentInstallError) as error:
        installer.install(
            DependencyEnvironmentInstallRequest(
                lock=lock,
                target_dir=tmp_path / "stage",
                python_version="3.13",
                workflow_id="workflow",
            )
        )

    assert error.value.code is DependencyPolicyErrorCode.UNAPPROVED_SOURCE
    assert not (tmp_path / "stage").exists()


def test_uv_dependency_installer_reports_command_failure_without_traceback(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "wheel-cache"
    cache_dir.mkdir()
    lock = _lock_for_cached_wheel(cache_dir)

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 2, stdout="", stderr="resolver failed\nTraceback should not leak"
        )

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=cache_dir,
        command_runner=runner,
        log_store=LogStore(),
    )

    with pytest.raises(DependencyEnvironmentInstallError) as error:
        installer.install(
            DependencyEnvironmentInstallRequest(
                lock=lock,
                target_dir=tmp_path / "stage",
                python_version="3.13",
                workflow_id="workflow",
            )
        )

    assert (
        str(error.value) == "Dependency environment installer failed: resolver failed"
    )


def test_uv_dependency_installer_reports_uv_error_line_after_context(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "wheel-cache"
    cache_dir.mkdir()
    lock = _lock_for_cached_wheel(cache_dir)

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            2,
            stdout="",
            stderr=(
                "Using Python 3.13.13 environment at: venv\n"
                "error: In `--require-hashes` mode, all requirements must be pinned"
            ),
        )

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=cache_dir,
        command_runner=runner,
        log_store=LogStore(),
    )

    with pytest.raises(DependencyEnvironmentInstallError) as error:
        installer.install(
            DependencyEnvironmentInstallRequest(
                lock=lock,
                target_dir=tmp_path / "stage",
                python_version="3.13",
                workflow_id="workflow",
            )
        )

    assert str(error.value) == (
        "Dependency environment installer failed: error: In `--require-hashes` mode, "
        "all requirements must be pinned"
    )


def test_v2_installer_allows_source_build_only_in_transaction(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        environments.append(env)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    transaction_root = tmp_path / "transaction"
    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=tmp_path / "legacy-wheel-cache",
        command_runner=runner,
        log_store=LogStore(),
    )
    installer.install(
        DependencyEnvironmentInstallRequest(
            lock=_v2_source_lock(),
            target_dir=transaction_root / "dependency-env",
            transaction_root=transaction_root,
            python_version="3.13",
            workflow_id="workflow",
        )
    )

    install = commands[1]
    assert "--require-hashes" in install
    assert "--no-deps" in install
    assert "--excludes" in install
    assert "--build-constraints" in install
    assert "--only-binary" not in install
    assert "--no-index" not in install
    assert "--find-links" not in install
    assert "--strict" not in install
    assert Path(environments[1]["UV_CACHE_DIR"]).is_relative_to(transaction_root)
    assert Path(environments[1]["TMPDIR"]).is_relative_to(transaction_root)


def test_v2_installer_classifies_source_build_failure(tmp_path: Path) -> None:
    calls = 0

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="Building groundingdino-py\nerror: build backend failed",
        )

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=tmp_path / "legacy-wheel-cache",
        command_runner=runner,
        log_store=LogStore(),
    )
    with pytest.raises(DependencyEnvironmentInstallError) as error:
        installer.install(
            DependencyEnvironmentInstallRequest(
                lock=_v2_source_lock(),
                target_dir=tmp_path / "transaction" / "dependency-env",
                transaction_root=tmp_path / "transaction",
                python_version="3.13",
                workflow_id="workflow",
            )
        )

    assert (
        error.value.code
        is DependencyPolicyErrorCode.DEPENDENCY_SOURCE_BUILD_FAILED
    )
    assert "build backend failed" in (error.value.output or "")


def test_v2_installer_inventory_records_distribution_origin(
    tmp_path: Path,
) -> None:
    calls = 0

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            dist_info = (
                cwd
                / "venv"
                / "lib"
                / "python3.13"
                / "site-packages"
                / "groundingdino_py-0.4.0.dist-info"
            )
            dist_info.mkdir(parents=True)
            (dist_info / "METADATA").write_text(
                "Name: groundingdino-py\nVersion: 0.4.0\n\n",
                encoding="utf-8",
            )
            (dist_info / "top_level.txt").write_text(
                "groundingdino\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    target_dir = tmp_path / "transaction" / "dependency-env"
    UvDependencyEnvironmentInstaller(
        wheel_cache_dir=tmp_path / "legacy-wheel-cache",
        command_runner=runner,
        log_store=LogStore(),
    ).install(
        DependencyEnvironmentInstallRequest(
            lock=_v2_source_lock(),
            target_dir=target_dir,
            transaction_root=tmp_path / "transaction",
            python_version="3.13",
            workflow_id="workflow",
        )
    )

    inventory = json.loads(
        (target_dir / "installed-dependencies.json").read_text(encoding="utf-8")
    )
    assert inventory["schema_version"] == "0.2.0"
    assert inventory["distributions"] == [
        {
            "distribution_filename": "groundingdino-py-0.4.0.tar.gz",
            "distribution_kind": "sdist",
            "distribution_sha256": "sha256:" + ("a" * 64),
            "distribution_url": (
                "https://files.pythonhosted.org/"
                "groundingdino-py-0.4.0.tar.gz"
            ),
            "import_names": ["groundingdino"],
            "name": "groundingdino-py",
            "source_index_url": DEFAULT_APPROVED_INDEX_URL,
            "version": "0.4.0",
        }
    ]


def test_v2_installer_classifies_dynamic_build_requirement_failure(
    tmp_path: Path,
) -> None:
    calls = 0

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr=(
                "Failed to resolve requirements from build-system.requires "
                "returned by get_requires_for_build_wheel"
            ),
        )

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=tmp_path / "legacy-wheel-cache",
        command_runner=runner,
        log_store=LogStore(),
    )
    with pytest.raises(DependencyEnvironmentInstallError) as error:
        installer.install(
            DependencyEnvironmentInstallRequest(
                lock=_v2_source_lock(),
                target_dir=tmp_path / "transaction" / "dependency-env",
                transaction_root=tmp_path / "transaction",
                python_version="3.13",
                workflow_id="workflow",
            )
        )

    assert (
        error.value.code
        is DependencyPolicyErrorCode.DEPENDENCY_BUILD_REQUIREMENTS_FAILED
    )
    assert "get_requires_for_build_wheel" in (error.value.output or "")


def test_v2_installer_rejects_protected_package_in_overlay(
    tmp_path: Path,
) -> None:
    calls = 0

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        if calls == 2:
            dist_info = (
                cwd
                / "venv"
                / "lib"
                / "python3.13"
                / "site-packages"
                / "numpy-2.0.0.dist-info"
            )
            dist_info.mkdir(parents=True)
            (dist_info / "METADATA").write_text(
                "Name: numpy\nVersion: 2.0.0\n\n", encoding="utf-8"
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    installer = UvDependencyEnvironmentInstaller(
        wheel_cache_dir=tmp_path / "legacy-wheel-cache",
        command_runner=runner,
        log_store=LogStore(),
    )
    with pytest.raises(DependencyEnvironmentInstallError) as error:
        installer.install(
            DependencyEnvironmentInstallRequest(
                lock=_v2_source_lock(),
                target_dir=tmp_path / "transaction" / "dependency-env",
                transaction_root=tmp_path / "transaction",
                python_version="3.13",
                workflow_id="workflow",
            )
        )

    assert (
        error.value.code
        is DependencyPolicyErrorCode.DEPENDENCY_OVERLAY_VALIDATION_FAILED
    )
