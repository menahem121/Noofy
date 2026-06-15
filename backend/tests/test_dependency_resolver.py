import hashlib
import json
import subprocess
import sys
import urllib.error
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from app.diagnostics import LogStore
from app.runtime.dependencies.dependency_lock import (
    DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    DependencyDistributionKind,
    DependencyPolicyErrorCode,
    DependencyRelationship,
    ResolvedDependencyLock,
    ResolverMetadata,
    with_computed_lock_hash,
)
from app.runtime.dependencies.dependency_resolver import (
    DistributionCandidate,
    DependencyResolutionError,
    DependencyResolutionRequest,
    MaterializedWheel,
    PyPIPackageIndexClient,
    ResolvedRequirement,
    UvDependencyLockResolver,
    _import_names_from_wheel,
    custom_node_dependency_source_dirs,
    parse_uv_compiled_requirements,
)
from app.runtime.runtime_tool_versions import SUPPORTED_UV_VERSION
from app.runtime.storage.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.storage.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)
from app.runtime.dependencies.dependency_env import DependencyEnvironmentInstallRequest
from app.runtime.dependencies.isolation import CapsuleLock, InstallStatus
from app.source_policy import SourcePolicy


class _FakePackageIndexClient:
    def __init__(self, wheel_bytes: bytes) -> None:
        self.wheel_bytes = wheel_bytes
        self.calls: list[ResolvedRequirement] = []

    def materialize_wheel(
        self,
        requirement: ResolvedRequirement,
        *,
        wheel_cache_dir: Path,
    ) -> MaterializedWheel:
        self.calls.append(requirement)
        digest = hashlib.sha256(self.wheel_bytes).hexdigest()
        assert f"sha256:{digest}" in requirement.hashes
        filename = f"{requirement.name}-{requirement.version}-py3-none-any.whl"
        wheel_cache_dir.mkdir(parents=True, exist_ok=True)
        (wheel_cache_dir / filename).write_bytes(self.wheel_bytes)
        return MaterializedWheel(
            name=requirement.name,
            version=requirement.version,
            wheel_filename=filename,
            sha256=f"sha256:{digest}",
            approved_cache_ref=filename,
            source_index_url="https://pypi.org/simple",
            platform_tags=["py3-none-any"],
            import_names=["demo_import"],
        )


class _FakeDependencyEnvInstaller:
    def __init__(self) -> None:
        self.requests: list[DependencyEnvironmentInstallRequest] = []

    def install(self, request: DependencyEnvironmentInstallRequest) -> None:
        self.requests.append(request)
        request.target_dir.mkdir(parents=True)
        (request.target_dir / "install.marker").write_text(
            "installed", encoding="utf-8"
        )


def _wheel_bytes(label: str) -> bytes:
    return (
        b"PK\x05\x06"
        + b"\x00" * 18
        + label.encode("utf-8")
    )


def json_bytes(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def test_parse_uv_compiled_requirements_handles_hash_continuations() -> None:
    parsed = parse_uv_compiled_requirements("""
# generated
demo==1.0.0 \\
    --hash=sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \\
    --hash=sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
transitive==2.0.0 ; python_version >= "3.12" \\
    --hash=sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
""")

    assert parsed[0].name == "demo"
    assert parsed[0].hashes == [
        "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    ]
    assert parsed[1].environment_marker == 'python_version >= "3.12"'


def test_uv_resolver_generates_noofy_lock_and_materializes_wheels(
    tmp_path: Path,
) -> None:
    wheel_bytes = b"wheel bytes"
    digest = hashlib.sha256(wheel_bytes).hexdigest()
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text("demo>=1\n", encoding="utf-8")
    (custom_node / "setup.py").write_text("", encoding="utf-8")
    (custom_node / "setup.py").unlink()
    commands: list[list[str]] = []

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="uv 0.11.10\n", stderr=""
            )
        assert command[:3] == ["uv", "pip", "compile"]
        output_path = Path(command[command.index("--output-file") + 1])
        output_path.write_text(
            f"demo==1.0.0 \\\n    --hash=sha256:{digest}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    custom_nodes_was_loaded = "custom_nodes" in sys.modules
    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        package_index_client=_FakePackageIndexClient(wheel_bytes),
        command_runner=runner,
        log_store=LogStore(),
    )

    lock = resolver.resolve(
        DependencyResolutionRequest(
            source_dirs=custom_node_dependency_source_dirs(tmp_path / "source-files"),
            runtime_profile_id="noofy-comfyui-v1-default",
            runtime_profile_variant_id="darwin-arm64-mps",
            runtime_profile_manifest_hash="sha256:" + ("9" * 64),
            install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            python_version="3.13",
            python_platform="aarch64-apple-darwin",
            workflow_id="workflow",
            source_policy=SourcePolicy(
                trust_level="quarantined_community",
                source_policy="explicit_opt_in_and_isolated_capsule_required",
                package_source_type="noofy_archive_import",
                automatic_preparation_allowed=True,
                allowed_source_origins=["explicit-metadata"],
                model_source_trust="hashed",
                community_preparation_opt_in_required=True,
                community_preparation_opted_in=True,
            ),
        )
    )

    assert lock.lock_hash is not None
    assert lock.source_policy is not None
    assert lock.source_policy.trust_level == "quarantined_community"
    assert lock.resolver.name == "uv"
    assert lock.resolver.version == "0.11.10"
    assert lock.requirements[0].relationship is DependencyRelationship.DIRECT
    assert lock.requirements[0].import_names == ["demo_import"]
    assert (tmp_path / "wheel-cache" / "demo-1.0.0-py3-none-any.whl").exists()
    assert "--generate-hashes" in commands[1]
    assert "--only-binary" not in commands[1]
    assert "--excludes" in commands[1]
    excludes_path = Path(commands[1][commands[1].index("--excludes") + 1])
    assert excludes_path.is_absolute()
    assert ("custom_nodes" in sys.modules) is custom_nodes_was_loaded


def test_uv_resolver_blocks_setup_py_marker_before_running_uv(tmp_path: Path) -> None:
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "setup.py").write_text(
        "from setuptools import setup\n", encoding="utf-8"
    )

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("uv must not run for setup.py dependency extraction")

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        package_index_client=_FakePackageIndexClient(b"wheel bytes"),
        command_runner=runner,
        log_store=LogStore(),
    )

    with pytest.raises(DependencyResolutionError) as error:
        resolver.resolve(
            DependencyResolutionRequest(
                source_dirs=custom_node_dependency_source_dirs(
                    tmp_path / "source-files"
                ),
                runtime_profile_id="noofy-comfyui-v1-default",
                runtime_profile_variant_id="darwin-arm64-mps",
                runtime_profile_manifest_hash="sha256:" + ("9" * 64),
                install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
                python_version="3.13",
                python_platform="aarch64-apple-darwin",
                workflow_id="workflow",
            )
        )

    assert error.value.code is DependencyPolicyErrorCode.PROJECT_CODE_EXECUTION_REQUIRED


def test_pypi_materializer_selects_target_platform_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mac_bytes = _wheel_bytes("mac")
    linux_bytes = _wheel_bytes("linux")
    mac_digest = hashlib.sha256(mac_bytes).hexdigest()
    linux_digest = hashlib.sha256(linux_bytes).hexdigest()
    files = {
        "https://files.example/contourpy-mac.whl": mac_bytes,
        "https://files.example/contourpy-linux.whl": linux_bytes,
    }
    metadata = {
        "urls": [
            {
                "filename": "contourpy-1.3.3-cp313-cp313-macosx_11_0_arm64.whl",
                "url": "https://files.example/contourpy-mac.whl",
                "digests": {"sha256": mac_digest},
            },
            {
                "filename": "contourpy-1.3.3-cp313-cp313-manylinux_2_17_x86_64.whl",
                "url": "https://files.example/contourpy-linux.whl",
                "digests": {"sha256": linux_digest},
            },
        ]
    }

    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            return self.payload if size < 0 else self.payload[:size]

    def urlopen(url: str, timeout: int):
        if url.endswith("/json"):
            return Response(json_bytes(metadata))
        return Response(files[url])

    monkeypatch.setattr(
        "app.runtime.dependencies.dependency_resolver.urllib.request.urlopen",
        urlopen,
    )

    wheel = PyPIPackageIndexClient(base_url="https://example.invalid/pypi").materialize_wheel(
        ResolvedRequirement(
            name="contourpy",
            version="1.3.3",
            hashes=[f"sha256:{mac_digest}", f"sha256:{linux_digest}"],
            python_version="3.13",
            python_platform="x86_64-unknown-linux-gnu",
        ),
        wheel_cache_dir=tmp_path,
    )

    assert wheel.wheel_filename == "contourpy-1.3.3-cp313-cp313-manylinux_2_17_x86_64.whl"
    assert wheel.sha256 == f"sha256:{linux_digest}"


def test_pypi_materializer_accepts_older_cp_abi3_wheel_for_target_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel_bytes = _wheel_bytes("abi3")
    digest = hashlib.sha256(wheel_bytes).hexdigest()
    metadata = {
        "urls": [
            {
                "filename": "opencv_python_headless-4.13.0.92-cp37-abi3-manylinux_2_17_x86_64.whl",
                "url": "https://files.example/opencv.whl",
                "digests": {"sha256": digest},
            },
        ]
    }

    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            return self.payload if size < 0 else self.payload[:size]

    def urlopen(url: str, timeout: int):
        if url.endswith("/json"):
            return Response(json_bytes(metadata))
        return Response(wheel_bytes)

    monkeypatch.setattr(
        "app.runtime.dependencies.dependency_resolver.urllib.request.urlopen",
        urlopen,
    )

    wheel = PyPIPackageIndexClient(base_url="https://example.invalid/pypi").materialize_wheel(
        ResolvedRequirement(
            name="opencv-python-headless",
            version="4.13.0.92",
            hashes=[f"sha256:{digest}"],
            python_version="3.13",
            python_platform="x86_64-unknown-linux-gnu",
        ),
        wheel_cache_dir=tmp_path,
    )

    assert wheel.wheel_filename == "opencv_python_headless-4.13.0.92-cp37-abi3-manylinux_2_17_x86_64.whl"


@pytest.mark.parametrize(
    ("filename", "url"),
    [
        ("../demo-1.0.0.tar.gz", "https://files.pythonhosted.org/demo-1.0.0.tar.gz"),
        ("demo-1.0.0.tar.gz", "https://packages.example/demo-1.0.0.tar.gz"),
    ],
)
def test_pypi_distribution_inspection_rejects_unsafe_registry_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    url: str,
) -> None:
    digest = "a" * 64
    metadata = {
        "urls": [
            {
                "filename": filename,
                "url": url,
                "packagetype": "sdist",
                "digests": {"sha256": digest},
            },
        ]
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            payload = json_bytes(metadata)
            return payload if size < 0 else payload[:size]

    monkeypatch.setattr(
        "app.runtime.dependencies.dependency_resolver.urllib.request.urlopen",
        lambda request_url, timeout: Response(),
    )

    client = PyPIPackageIndexClient()
    with pytest.raises(DependencyResolutionError) as error:
        client.inspect_distribution(
            ResolvedRequirement(
                name="demo",
                version="1.0.0",
                hashes=[f"sha256:{digest}"],
                python_version="3.13",
                python_platform="x86_64-unknown-linux-gnu",
            ),
            work_dir=tmp_path,
        )

    assert error.value.code is DependencyPolicyErrorCode.UNAPPROVED_SOURCE
    assert not (tmp_path / "source-archives").exists()


def test_import_names_from_wheel_falls_back_to_top_level_packages(
    tmp_path: Path,
) -> None:
    wheel_path = tmp_path / "markdown_it_py-4.2.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel_path, "w") as wheel:
        wheel.writestr("markdown_it/__init__.py", "")
        wheel.writestr("markdown_it/main.py", "")
        wheel.writestr("markdown_it_py-4.2.0.dist-info/METADATA", "")
        wheel.writestr("../../../bin/markdown-it", "")

    assert _import_names_from_wheel(wheel_path) == ["markdown_it"]


def test_workspace_preparer_can_resolve_missing_lock_from_custom_node_sources(
    tmp_path: Path,
) -> None:
    lock = with_computed_lock_hash(
        ResolvedDependencyLock(
            runtime_profile_id="noofy-comfyui-v1-default",
            runtime_profile_variant_id="darwin-arm64-mps-dev",
            runtime_profile_manifest_hash="sha256:" + ("9" * 64),
            install_policy_version="core_only_no_community",
            resolver=ResolverMetadata(name="uv", version="0.9.0"),
            wheels=[],
        )
    )
    data = _capsule_data(lock.lock_hash)
    capsule = CapsuleLock.model_validate(data)
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text("demo>=1\n", encoding="utf-8")

    class FakeResolver:
        def resolve(
            self, request: DependencyResolutionRequest
        ) -> ResolvedDependencyLock:
            assert request.source_dirs == [custom_node]
            return lock

    installer = _FakeDependencyEnvInstaller()
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(
            tmp_path / "runner-workspaces"
        ),
        dependency_env_installer=installer,
        dependency_lock_resolver=FakeResolver(),
        custom_node_source_files_dir=tmp_path / "source-files",
        dependency_transactions_dir=tmp_path / "transactions",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    assert (
        prepared.dependency_env_manifest.status is InstallStatus.CHECKING_COMPATIBILITY
    )
    assert installer.requests[0].lock == lock
    assert (prepared.dependency_env_path / "install.marker").exists()


def _capsule_data(dependency_lock_hash: str) -> dict:
    return {
        "schema_version": "0.1.0",
        "workflow": {
            "publisher_id": "noofy",
            "package_id": "test_workflow",
            "version": "0.1.0",
            "trust_level": "noofy_verified",
            "source": "bundled",
        },
        "engine": {
            "type": "comfyui",
            "comfyui_version": "milestone-1",
            "core_source_hash": "sha256:" + ("a" * 64),
        },
        "runtime": {
            "runtime_profile_id": "noofy-comfyui-v1-default",
            "runtime_profile_variant_id": "darwin-arm64-mps-dev",
            "runtime_profile_manifest_hash": "sha256:" + ("9" * 64),
            "runtime_profile_catalog_version": "0.1.0",
            "fingerprint_schema_version": "0.1.0",
            "dependency_env_fingerprint": "sha256:" + ("b" * 64),
            "runner_fingerprint": "sha256:" + ("c" * 64),
            "capsule_fingerprint": "sha256:" + ("d" * 64),
            "os": "darwin",
            "architecture": "arm64",
            "python_version": "3.11",
            "python_build_id": "cpython-3.11-noofy-dev",
            "gpu_backend": "mps",
            "dependency_lock_hash": dependency_lock_hash,
            "runner_workspace_hash": "sha256:" + ("f" * 64),
        },
        "custom_nodes": [],
        "dependencies": {
            "lock_file": "phase5b",
            "install_policy": "core_only_no_community",
        },
        "models": [],
        "trust": {"level": "noofy_verified", "publisher": "Noofy"},
    }


def _quarantined_source_policy() -> SourcePolicy:
    return SourcePolicy(
        trust_level="quarantined_community",
        source_policy="explicit_opt_in_and_isolated_capsule_required",
        package_source_type="noofy_archive_import",
        automatic_preparation_allowed=True,
        allowed_source_origins=["explicit-metadata"],
        model_source_trust="hashed",
        community_preparation_opt_in_required=True,
        community_preparation_opted_in=True,
    )


def _resolution_request(tmp_path: Path) -> DependencyResolutionRequest:
    return DependencyResolutionRequest(
        source_dirs=custom_node_dependency_source_dirs(tmp_path / "source-files"),
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_variant_id="darwin-arm64-mps",
        runtime_profile_manifest_hash="sha256:" + ("9" * 64),
        install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
        python_version="3.13",
        python_platform="aarch64-apple-darwin",
        workflow_id="workflow",
        source_policy=_quarantined_source_policy(),
    )


def test_uv_resolver_strips_unsupported_accelerators_and_core_packages(
    tmp_path: Path,
) -> None:
    wheel_bytes = b"wheel bytes"
    digest = hashlib.sha256(wheel_bytes).hexdigest()
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text(
        "xformers>=0.0.20\ntorch==2.0.1\ndemo>=1\n", encoding="utf-8"
    )
    compiled_inputs: list[str] = []

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="uv 0.11.10\n", stderr=""
            )
        assert command[:3] == ["uv", "pip", "compile"]
        compiled_inputs.append(Path(command[3]).read_text(encoding="utf-8"))
        output_path = Path(command[command.index("--output-file") + 1])
        output_path.write_text(
            f"demo==1.0.0 \\\n    --hash=sha256:{digest}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    log_store = LogStore()
    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        package_index_client=_FakePackageIndexClient(wheel_bytes),
        command_runner=runner,
        log_store=log_store,
    )

    lock = resolver.resolve(_resolution_request(tmp_path))

    assert [requirement.name for requirement in lock.requirements] == ["demo"]
    assert compiled_inputs == ["demo>=1\n"]

    events = [
        event
        for event in log_store.list_events(limit=50).events
        if "Skipped custom-node dependencies" in event.message
    ]
    assert len(events) == 1
    ignored = events[0].details["ignored_dependencies"]
    by_name_reason = {(entry["name"], entry["reason"]) for entry in ignored}
    assert ("xformers", "unsupported_accelerator") in by_name_reason
    assert ("torch", "provided_by_core_runtime") in by_name_reason
    direct_torch = [
        entry
        for entry in ignored
        if entry["name"] == "torch" and entry["source_file"] is not None
    ]
    assert direct_torch


def test_uv_resolver_skips_uv_when_only_unsupported_dependencies(
    tmp_path: Path,
) -> None:
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text(
        "xformers\nflash_attn>=2\nsageattention\n", encoding="utf-8"
    )
    commands: list[list[str]] = []

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="uv 0.11.10\n", stderr=""
            )
        raise AssertionError("uv pip compile must not run for fully ignored input")

    log_store = LogStore()
    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        package_index_client=_FakePackageIndexClient(b"wheel bytes"),
        command_runner=runner,
        log_store=log_store,
    )

    lock = resolver.resolve(_resolution_request(tmp_path))

    assert lock.requirements == []
    assert commands == [["uv", "--version"]]
    events = [
        event
        for event in log_store.list_events(limit=50).events
        if "Skipped custom-node dependencies" in event.message
    ]
    assert len(events) == 1
    names = {entry["name"] for entry in events[0].details["ignored_dependencies"]}
    assert names == {"xformers", "flash-attn", "sageattention"}


def test_uv_resolver_allows_sdist_with_pinned_build_constraints(
    tmp_path: Path,
) -> None:
    digest = "a" * 64
    build_digest = "b" * 64
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text(
        "groundingdino-py>=0.4.0\n", encoding="utf-8"
    )
    commands: list[list[str]] = []
    inspection_count = 0

    class SourceIndex:
        def inspect_distribution(
            self,
            requirement: ResolvedRequirement,
            *,
            work_dir: Path,
        ) -> DistributionCandidate:
            nonlocal inspection_count
            inspection_count += 1
            return DistributionCandidate(
                kind=DependencyDistributionKind.SDIST,
                filename="groundingdino-py-0.4.0.tar.gz",
                url="https://files.pythonhosted.org/groundingdino-py-0.4.0.tar.gz",
                sha256=f"sha256:{digest}",
                source_index_url="https://pypi.org/simple",
                import_names=[],
                build_system_requires=["setuptools>=40.8.0"],
                legacy_setuptools_build=True,
                dynamic_build_requirements_possible=True,
            )

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        transaction_root = cwd.parent if cwd.name == "dependency-resolution" else cwd
        assert Path(env["UV_CACHE_DIR"]).is_relative_to(transaction_root)
        assert Path(env["TMPDIR"]).is_relative_to(transaction_root)
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"uv {SUPPORTED_UV_VERSION}\n", stderr=""
            )
        output_path = Path(command[command.index("--output-file") + 1])
        input_path = Path(command[3])
        if input_path.name == "build-requirements.in":
            output_path.write_text(
                "setuptools==82.0.1 \\\n"
                f"    --hash=sha256:{build_digest}\n",
                encoding="utf-8",
            )
        else:
            output_path.write_text(
                "groundingdino-py==0.4.0 \\\n"
                f"    --hash=sha256:{digest}\n",
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        package_index_client=SourceIndex(),
        command_runner=runner,
        log_store=LogStore(),
    )
    transaction_dir = tmp_path / "transaction" / "dependency-resolution"
    lock = resolver.resolve(
        replace(_resolution_request(tmp_path), transaction_dir=transaction_dir)
    )

    grounding = lock.requirements[0]
    assert grounding.distribution_kind is DependencyDistributionKind.SDIST
    assert grounding.legacy_setuptools_build is True
    assert grounding.build_system_requires == ["setuptools>=40.8.0"]
    assert lock.source_distributions == ["groundingdino-py"]
    assert lock.python_version == "3.13"
    assert lock.python_platform == "aarch64-apple-darwin"
    assert "setuptools==82.0.1" in (lock.build_constraints or "")
    assert inspection_count == 1
    distribution_diagnostics = json.loads(
        (transaction_dir / "diagnostics" / "source-distributions.json").read_text(
            encoding="utf-8"
        )
    )
    assert distribution_diagnostics["packages"][0]["name"] == "groundingdino-py"
    assert distribution_diagnostics["packages"][0]["distribution_kind"] == "sdist"
    runtime_compile_commands = [
        command
        for command in commands
        if command[:3] == ["uv", "pip", "compile"]
        and Path(command[3]).name == "requirements.in"
    ]
    assert any("--build-constraints" in command for command in runtime_compile_commands)
    assert all("--only-binary" not in command for command in runtime_compile_commands)


def test_pypi_distribution_inspection_classifies_metadata_network_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.runtime.dependencies.dependency_resolver.urllib.request.urlopen",
        lambda url, timeout: (_ for _ in ()).throw(
            urllib.error.URLError("network unavailable")
        ),
    )

    with pytest.raises(DependencyResolutionError) as error:
        PyPIPackageIndexClient().inspect_distribution(
            ResolvedRequirement(
                name="demo",
                version="1.0.0",
                hashes=["sha256:" + ("a" * 64)],
                python_version="3.13",
                python_platform="x86_64-unknown-linux-gnu",
            ),
            work_dir=tmp_path,
        )

    assert (
        error.value.code
        is DependencyPolicyErrorCode.DEPENDENCY_RESOLUTION_FAILED
    )
    assert "network unavailable" in str(error.value)


def test_uv_resolver_blocks_torch_as_a_declared_build_requirement(
    tmp_path: Path,
) -> None:
    digest = "a" * 64
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text("native-demo==1.0.0\n", encoding="utf-8")

    class SourceIndex:
        def inspect_distribution(
            self,
            requirement: ResolvedRequirement,
            *,
            work_dir: Path,
        ) -> DistributionCandidate:
            return DistributionCandidate(
                kind=DependencyDistributionKind.SDIST,
                filename="native-demo-1.0.0.tar.gz",
                url="https://files.pythonhosted.org/native-demo-1.0.0.tar.gz",
                sha256=f"sha256:{digest}",
                source_index_url="https://pypi.org/simple",
                import_names=[],
                build_system_requires=["setuptools", "torch>=2"],
                legacy_setuptools_build=False,
                dynamic_build_requirements_possible=True,
            )

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"uv {SUPPORTED_UV_VERSION}\n", stderr=""
            )
        output_path = Path(command[command.index("--output-file") + 1])
        output_path.write_text(
            "native-demo==1.0.0 \\\n"
            f"    --hash=sha256:{digest}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        package_index_client=SourceIndex(),
        command_runner=runner,
        log_store=LogStore(),
    )

    with pytest.raises(DependencyResolutionError) as error:
        resolver.resolve(_resolution_request(tmp_path))

    assert (
        error.value.code
        is DependencyPolicyErrorCode.DEPENDENCY_BUILD_POLICY_BLOCKED
    )


def test_uv_resolver_fails_when_sdist_selection_does_not_stabilize(
    tmp_path: Path,
) -> None:
    digest = "a" * 64
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text("changing-demo>=1\n", encoding="utf-8")
    compile_count = 0

    class SourceIndex:
        def inspect_distribution(
            self,
            requirement: ResolvedRequirement,
            *,
            work_dir: Path,
        ) -> DistributionCandidate:
            return DistributionCandidate(
                kind=DependencyDistributionKind.SDIST,
                filename=f"{requirement.name}-{requirement.version}.tar.gz",
                url=(
                    "https://files.pythonhosted.org/"
                    f"{requirement.name}-{requirement.version}.tar.gz"
                ),
                sha256=f"sha256:{digest}",
                source_index_url="https://pypi.org/simple",
                import_names=[],
                build_system_requires=[],
                legacy_setuptools_build=True,
                dynamic_build_requirements_possible=True,
            )

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        nonlocal compile_count
        if command == ["uv", "--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"uv {SUPPORTED_UV_VERSION}\n", stderr=""
            )
        compile_count += 1
        output_path = Path(command[command.index("--output-file") + 1])
        output_path.write_text(
            f"changing-demo==1.0.{compile_count} \\\n"
            f"    --hash=sha256:{digest}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        package_index_client=SourceIndex(),
        command_runner=runner,
        log_store=LogStore(),
    )

    with pytest.raises(DependencyResolutionError) as error:
        resolver.resolve(_resolution_request(tmp_path))

    assert (
        error.value.code
        is DependencyPolicyErrorCode.DEPENDENCY_BUILD_RESOLUTION_UNSTABLE
    )
    assert compile_count == 4


def test_uv_resolver_rejects_unpinned_bundled_uv(tmp_path: Path) -> None:
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text("demo>=1\n", encoding="utf-8")

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command, 0, stdout="uv 0.11.9\n", stderr=""
        )

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        command_runner=runner,
        log_store=LogStore(),
    )
    with pytest.raises(DependencyResolutionError) as error:
        resolver.resolve(_resolution_request(tmp_path))

    assert error.value.code is DependencyPolicyErrorCode.UNSUPPORTED_UV_VERSION


def test_uv_resolver_rejects_unapproved_index_before_running_uv(
    tmp_path: Path,
) -> None:
    custom_node = tmp_path / "source-files" / "custom_nodes" / "node-a"
    custom_node.mkdir(parents=True)
    (custom_node / "requirements.txt").write_text("demo>=1\n", encoding="utf-8")

    def runner(
        command: list[str], *, cwd: Path, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        raise AssertionError("uv must not run for an unapproved index")

    resolver = UvDependencyLockResolver(
        wheel_cache_dir=tmp_path / "wheel-cache",
        work_dir=tmp_path / "transactions",
        command_runner=runner,
        log_store=LogStore(),
    )
    request = _resolution_request(tmp_path)

    with pytest.raises(DependencyResolutionError) as error:
        resolver.resolve(
            replace(
                request,
                approved_index_url="https://packages.example/simple",
            )
        )

    assert error.value.code is DependencyPolicyErrorCode.UNAPPROVED_SOURCE
