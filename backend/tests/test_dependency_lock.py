import json
from pathlib import Path

import pytest

from app.runtime.dependencies.dependency_lock import (
    DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
    DependencyPolicyError,
    DependencyPolicyErrorCode,
    DependencyRelationship,
    DependencySourceKind,
    ResolvedDependencyLock,
    ResolvedDependencyWheel,
    ResolverMetadata,
    dependency_env_fingerprint_for_resolved_lock,
    inspect_dependency_marker_files,
    merge_resolved_dependency_locks,
    normalize_package_name,
    resolved_dependency_lock_hash,
    validate_dependency_lock_source_policy,
    validate_quarantined_community_lock,
    with_computed_lock_hash,
)
from app.source_policy import SourcePolicy


def _resolver() -> ResolverMetadata:
    return ResolverMetadata(name="uv", version="0.9.0", command="uv pip compile")


def _wheel(
    name: str = "Demo_Package",
    *,
    version: str = "1.0.0",
    sha256: str | None = "sha256:" + ("a" * 64),
    filename: str | None = None,
    relationship: DependencyRelationship = DependencyRelationship.DIRECT,
    requested_by: list[str] | None = None,
    source_kind: DependencySourceKind = DependencySourceKind.INDEX,
    source_index_url: str | None = "https://download.pytorch.org/whl/cu130",
    approved_cache_ref: str | None = None,
    source_distribution: bool = False,
    native_build_required: bool = False,
    install_script: bool = False,
    import_names: list[str] | None = None,
) -> ResolvedDependencyWheel:
    resolver = _resolver()
    return ResolvedDependencyWheel(
        name=name,
        version=version,
        wheel_filename=filename or f"{normalize_package_name(name)}-{version}-py3-none-any.whl",
        sha256=sha256,
        source_kind=source_kind,
        source_index_url=source_index_url,
        approved_cache_ref=approved_cache_ref,
        platform_tags=["py3-none-any"],
        environment_marker=None,
        import_names=import_names or [],
        relationship=relationship,
        requested_by=requested_by or ["custom-node-a"],
        resolver_name=resolver.name,
        resolver_version=resolver.version,
        source_distribution=source_distribution,
        native_build_required=native_build_required,
        install_script=install_script,
    )


def _lock(
    wheels: list[ResolvedDependencyWheel],
    *,
    runtime_profile_id: str = "noofy-comfyui-v1-default",
    runtime_profile_variant_id: str = "darwin-arm64-mps",
    runtime_profile_manifest_hash: str = "sha256:" + ("9" * 64),
    install_policy_version: str = DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
) -> ResolvedDependencyLock:
    return ResolvedDependencyLock(
        runtime_profile_id=runtime_profile_id,
        runtime_profile_variant_id=runtime_profile_variant_id,
        runtime_profile_manifest_hash=runtime_profile_manifest_hash,
        install_policy_version=install_policy_version,
        resolver=_resolver(),
        wheels=wheels,
    )


def _source_policy(
    *,
    allowed_source_origins: list[str] | None = None,
) -> SourcePolicy:
    return SourcePolicy(
        trust_level="quarantined_community",
        source_policy="explicit_opt_in_and_isolated_capsule_required",
        package_source_type="noofy_archive_import",
        automatic_preparation_allowed=True,
        allowed_source_origins=allowed_source_origins or ["explicit-metadata"],
        model_source_trust="hashed",
        community_preparation_opt_in_required=True,
        community_preparation_opted_in=True,
    )


def test_resolved_dependency_lock_hash_is_stable_and_excludes_self_hash() -> None:
    lock = _lock([_wheel()])
    hashed = with_computed_lock_hash(lock)

    assert hashed.lock_hash == resolved_dependency_lock_hash(lock)
    assert resolved_dependency_lock_hash(hashed) == hashed.lock_hash
    assert hashed.lock_hash.startswith("sha256:")


def test_dependency_env_fingerprint_comes_from_resolved_lock_facts() -> None:
    first = dependency_env_fingerprint_for_resolved_lock(
        with_computed_lock_hash(_lock([_wheel("numpy", version="2.0.0")])),
        os_name="darwin",
        architecture="arm64",
        python_build_id="cpython-3.13-noofy-v1",
        torch_wheel_build_tag="torch==2.11.0:macos-arm64-default-mps",
        torch_backend="mps",
    )
    second = dependency_env_fingerprint_for_resolved_lock(
        with_computed_lock_hash(_lock([_wheel("numpy", version="2.1.0")])),
        os_name="darwin",
        architecture="arm64",
        python_build_id="cpython-3.13-noofy-v1",
        torch_wheel_build_tag="torch==2.11.0:macos-arm64-default-mps",
        torch_backend="mps",
    )

    assert first.startswith("sha256:")
    assert first != second


def test_wheel_names_are_normalized() -> None:
    wheel = _wheel("Demo_Package.Name")

    assert wheel.name == "demo-package-name"


def test_wheel_import_names_are_normalized_and_validated() -> None:
    wheel = _wheel(import_names=["demo", "demo.submodule", "demo"])

    assert wheel.import_names == ["demo", "demo.submodule"]

    with pytest.raises(ValueError, match="import_names"):
        _wheel(import_names=["not-valid-name"])


def test_lock_rejects_wheel_resolver_metadata_mismatch() -> None:
    resolver = _resolver()
    wheel = _wheel().model_copy(update={"resolver_version": "different"})

    with pytest.raises(ValueError, match="wheel resolver metadata"):
        ResolvedDependencyLock(
            runtime_profile_id="profile",
            runtime_profile_variant_id="variant",
            runtime_profile_manifest_hash="sha256:" + ("9" * 64),
            install_policy_version=DEFAULT_COMMUNITY_INSTALL_POLICY_VERSION,
            resolver=resolver,
            wheels=[wheel],
        )


def test_merge_resolved_dependency_locks_combines_duplicate_wheel_facts() -> None:
    core = _lock(
        [
            _wheel(
                "torch",
                relationship=DependencyRelationship.CORE,
                requested_by=["runtime-profile"],
            )
        ]
    )
    custom = _lock(
        [
            _wheel(
                "Torch",
                relationship=DependencyRelationship.DIRECT,
                requested_by=["custom-node-a"],
            )
        ]
    )

    merged = merge_resolved_dependency_locks(core, [custom])

    assert len(merged.wheels) == 1
    assert merged.wheels[0].relationship is DependencyRelationship.CORE
    assert merged.wheels[0].requested_by == ["custom-node-a", "runtime-profile"]
    assert merged.resolver.name == "noofy-lock-merge"
    assert merged.wheels[0].resolver_name == "noofy-lock-merge"
    assert merged.lock_hash is not None


def test_merge_resolved_dependency_locks_carries_custom_source_policy() -> None:
    policy = _source_policy()
    core = _lock([])
    custom = _lock([_wheel("pillow")]).model_copy(update={"source_policy": policy})

    merged = merge_resolved_dependency_locks(core, [custom])

    assert merged.source_policy == policy
    validate_dependency_lock_source_policy(merged, policy)


def test_dependency_lock_source_policy_validation_blocks_missing_policy() -> None:
    with pytest.raises(DependencyPolicyError) as error:
        validate_dependency_lock_source_policy(_lock([_wheel("pillow")]), _source_policy())

    assert error.value.code is DependencyPolicyErrorCode.SOURCE_POLICY_MISMATCH


def test_dependency_lock_source_policy_validation_blocks_mismatched_policy() -> None:
    expected = _source_policy(allowed_source_origins=["explicit-metadata"])
    stale = _lock([_wheel("pillow")]).model_copy(
        update={"source_policy": _source_policy(allowed_source_origins=["registry-locked"])}
    )

    with pytest.raises(DependencyPolicyError) as error:
        validate_dependency_lock_source_policy(stale, expected)

    assert error.value.code is DependencyPolicyErrorCode.SOURCE_POLICY_MISMATCH


def test_merge_resolved_dependency_locks_blocks_conflicting_versions() -> None:
    core = _lock([_wheel("numpy", version="2.0.0")])
    custom = _lock([_wheel("numpy", version="2.1.0")])

    with pytest.raises(DependencyPolicyError) as error:
        merge_resolved_dependency_locks(core, [custom])

    assert error.value.code is DependencyPolicyErrorCode.CONFLICTING_RESOLUTION


def test_merge_resolved_dependency_locks_blocks_cross_runtime_merge() -> None:
    core = _lock([_wheel("numpy")])
    custom = _lock([_wheel("pillow")], runtime_profile_variant_id="windows-x64-cpu")

    with pytest.raises(DependencyPolicyError) as error:
        merge_resolved_dependency_locks(core, [custom])

    assert error.value.code is DependencyPolicyErrorCode.CROSS_RUNTIME_LOCK_MERGE


def test_policy_accepts_approved_index_wheels_with_hashes() -> None:
    lock = _lock([_wheel()])

    validate_quarantined_community_lock(
        lock,
        approved_index_urls=["https://download.pytorch.org/whl/cu130"],
    )


@pytest.mark.parametrize(
    ("wheel", "code"),
    [
        (_wheel(sha256=None), DependencyPolicyErrorCode.MISSING_HASH),
        (_wheel(filename="demo-1.0.0.tar.gz"), DependencyPolicyErrorCode.SDIST_NOT_ALLOWED),
        (_wheel(source_distribution=True), DependencyPolicyErrorCode.SDIST_NOT_ALLOWED),
        (_wheel(native_build_required=True), DependencyPolicyErrorCode.NATIVE_BUILD_NOT_ALLOWED),
        (_wheel(install_script=True), DependencyPolicyErrorCode.INSTALL_SCRIPT_NOT_ALLOWED),
        (
            _wheel(source_index_url="https://example.invalid/simple"),
            DependencyPolicyErrorCode.UNAPPROVED_SOURCE,
        ),
    ],
)
def test_policy_blocks_unsafe_resolved_wheels(
    wheel: ResolvedDependencyWheel,
    code: DependencyPolicyErrorCode,
) -> None:
    with pytest.raises(DependencyPolicyError) as error:
        validate_quarantined_community_lock(
            _lock([wheel]),
            approved_index_urls=["https://download.pytorch.org/whl/cu130"],
        )

    assert error.value.code is code


def test_policy_verifies_approved_cache_wheel_hash(tmp_path: Path) -> None:
    wheel_path = tmp_path / "demo-1.0.0-py3-none-any.whl"
    wheel_path.write_bytes(b"wheel bytes")
    expected_hash = "sha256:67c0d8f7de19e30c2d5891030a0b37cbfcdd240852b53055c0b28290ad52290b"
    lock = _lock(
        [
            _wheel(
                sha256=expected_hash,
                filename=wheel_path.name,
                source_kind=DependencySourceKind.APPROVED_CACHE,
                source_index_url=None,
                approved_cache_ref=wheel_path.name,
            )
        ]
    )

    validate_quarantined_community_lock(lock, wheel_cache_dir=tmp_path)


def test_policy_blocks_missing_cached_wheel(tmp_path: Path) -> None:
    lock = _lock(
        [
            _wheel(
                source_kind=DependencySourceKind.APPROVED_CACHE,
                source_index_url=None,
                approved_cache_ref="missing.whl",
            )
        ]
    )

    with pytest.raises(DependencyPolicyError) as error:
        validate_quarantined_community_lock(lock, wheel_cache_dir=tmp_path)

    assert error.value.code is DependencyPolicyErrorCode.MISSING_WHEEL


def test_policy_blocks_cached_wheel_hash_mismatch(tmp_path: Path) -> None:
    wheel_path = tmp_path / "demo-1.0.0-py3-none-any.whl"
    wheel_path.write_bytes(b"different bytes")
    lock = _lock(
        [
            _wheel(
                filename=wheel_path.name,
                source_kind=DependencySourceKind.APPROVED_CACHE,
                source_index_url=None,
                approved_cache_ref=wheel_path.name,
            )
        ]
    )

    with pytest.raises(DependencyPolicyError) as error:
        validate_quarantined_community_lock(lock, wheel_cache_dir=tmp_path)

    assert error.value.code is DependencyPolicyErrorCode.HASH_MISMATCH


def test_inspect_dependency_marker_files_reads_normal_markers_without_execution(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text("numpy==2.1.0\n# comment\npillow==11.0.0\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dependencies = ["safetensors==0.5.0"]

[project.optional-dependencies]
test = ["pytest==8.3.0"]
""".strip(),
        encoding="utf-8",
    )

    inspection = inspect_dependency_marker_files(tmp_path)

    assert inspection.supported
    assert [declaration.requirement for declaration in inspection.declarations] == [
        "numpy==2.1.0",
        "pillow==11.0.0",
        "safetensors==0.5.0",
        "pytest==8.3.0",
    ]


def test_inspect_dependency_marker_files_ignores_setup_py_without_execution(
    tmp_path: Path,
) -> None:
    (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup(install_requires=['x'])\n", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("requests==2.32.0\n", encoding="utf-8")

    inspection = inspect_dependency_marker_files(tmp_path)

    assert inspection.supported
    assert [declaration.requirement for declaration in inspection.declarations] == [
        "requests==2.32.0"
    ]


def test_inspect_dependency_marker_files_blocks_dynamic_pyproject_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
dynamic = ["dependencies"]
""".strip(),
        encoding="utf-8",
    )

    inspection = inspect_dependency_marker_files(tmp_path)

    assert not inspection.supported
    assert inspection.findings[0].code is DependencyPolicyErrorCode.PROJECT_CODE_EXECUTION_REQUIRED


def test_inspect_dependency_marker_files_blocks_unsafe_requirement_entries(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "-e ../local-package\n"
        "demo @ https://example.invalid/demo-1.0.0-py3-none-any.whl\n"
        "requests==2.32.0\n",
        encoding="utf-8",
    )

    inspection = inspect_dependency_marker_files(tmp_path)

    assert not inspection.supported
    assert [finding.code for finding in inspection.findings] == [
        DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
        DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION,
    ]
    assert [declaration.requirement for declaration in inspection.declarations] == ["requests==2.32.0"]


@pytest.mark.parametrize(
    "requirement",
    [
        "demo @ git+https://github.com/example/demo.git",
        "demo @ https://example.invalid/demo-1.0.0-py3-none-any.whl",
        "demo @ file:///tmp/demo",
        "demo @ ../demo",
        "--extra-index-url https://example.invalid/simple",
    ],
)
def test_inspect_dependency_marker_files_blocks_unsafe_pyproject_dependencies(
    tmp_path: Path,
    requirement: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'name = "custom-node"\n'
        f"dependencies = [{json.dumps(requirement)}]\n",
        encoding="utf-8",
    )

    inspection = inspect_dependency_marker_files(tmp_path)

    assert not inspection.supported
    assert inspection.declarations == []
    assert inspection.findings[0].code is (
        DependencyPolicyErrorCode.UNSUPPORTED_DEPENDENCY_DECLARATION
    )
    assert inspection.findings[0].source_file == "pyproject.toml"
