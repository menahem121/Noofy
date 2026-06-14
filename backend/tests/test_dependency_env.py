import hashlib
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
    DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    DependencyPolicyErrorCode,
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
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
        uv_cache_dir=tmp_path / "uv-cache",
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
