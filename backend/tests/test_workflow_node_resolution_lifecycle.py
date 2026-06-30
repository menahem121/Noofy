import pytest

from app.runtime.capsule_installer import CapsuleInstallError
from app.runtime.node_registry import (
    NodeRegistryResolutionError,
    NodeRegistryResolutionErrorCode,
)
from app.runtime.dependencies.isolation import (
    CapsuleLock,
    InstallState,
    InstallStatus,
    SmokeStageResult,
    SmokeStageStatus,
    SmokeTestReport,
    SmokeTestStatus,
)
from app.runtime.install_state import INSTALL_STATE_SCHEMA_VERSION
from app.runtime.runners.lifecycle_service import WorkflowRunnerLifecycleService
from app.workflows.package import (
    WorkflowImportMetadata,
    WorkflowMetadata,
    WorkflowPackage,
    WorkflowPackageIdentity,
)


@pytest.mark.anyio
async def test_prepare_workflow_auto_resolves_engine_missing_nodes_and_retries_silently() -> None:
    loader = _WorkflowLoader(_package())
    installer = _Installer(mode="resolved")
    store = _ImportedStore(loader, mode="resolved")
    service = _service(loader=loader, installer=installer, imported_store=store)

    result = await service.prepare_workflow("workflow")

    assert result["status"] == "ready"
    assert installer.prepare_calls == 2
    assert store.missing_node_types == ["FutureCoreNode"]
    assert "custom_node_resolution" not in result


@pytest.mark.anyio
async def test_prepare_workflow_exposes_neutral_payload_when_auto_resolution_has_no_candidate() -> None:
    loader = _WorkflowLoader(_package())
    installer = _Installer(mode="fallback")
    store = _ImportedStore(loader, mode="fallback")
    service = _service(loader=loader, installer=installer, imported_store=store)

    result = await service.prepare_workflow("workflow")

    assert installer.prepare_calls == 1
    resolution = result["custom_node_resolution"]
    assert resolution["status"] == "engine_unrecognized_nodes"
    assert resolution["unresolved_node_types"] == ["FutureCoreNode"]
    assert resolution["user_facing_message"] == (
        "This workflow uses nodes that the current engine does not recognize."
    )
    assert "managed ComfyUI engine is too old" in resolution["update_guidance"]


@pytest.mark.anyio
async def test_prepare_workflow_caches_portable_custom_node_source_before_prepare() -> None:
    loader = _WorkflowLoader(_package())
    installer = _Installer(mode="ready")
    store = _ImportedStore(loader, mode="resolved")
    cache = _CustomNodeSourceCache()
    service = _service(
        loader=loader,
        installer=installer,
        imported_store=store,
        custom_node_source_cache=cache,
        capsule=_capsule(
            custom_nodes=[
                {
                    "package_id": "github-example-comfyui-node",
                    "source": "https://codeload.github.com/example/comfyui-node/zip/"
                    + ("a" * 40),
                    "source_ref": "a" * 40,
                    "source_content_hash": "sha256:" + ("b" * 64),
                    "source_archive_subdir": "comfyui-node-" + ("a" * 40),
                    "source_repo_url": "https://github.com/example/comfyui-node",
                    "trust_level": "quarantined_community",
                    "node_types": ["ExampleNode"],
                }
            ]
        ),
    )

    result = await service.prepare_workflow("workflow")

    assert result["status"] == "ready"
    assert installer.prepare_calls == 1
    prepared_capsule = installer.prepared_capsules[0]
    assert prepared_capsule.custom_nodes[0].source_cache_ref == "cached/source"
    assert cache.sources[0].source_url.startswith(
        "https://codeload.github.com/example/comfyui-node/zip/"
    )
    assert cache.source_origins == [["explicit-metadata"]]
    assert cache.source_policies[0].automatic_preparation_allowed is True


@pytest.mark.anyio
async def test_prepare_workflow_reports_portable_custom_node_source_resolution_failure() -> None:
    loader = _WorkflowLoader(_package())
    installer = _Installer(mode="ready")
    store = _ImportedStore(loader, mode="resolved")
    service = _service(
        loader=loader,
        installer=installer,
        imported_store=store,
        custom_node_source_cache=_FailingCustomNodeSourceCache(),
        capsule=_capsule(
            custom_nodes=[
                {
                    "package_id": "github-example-comfyui-node",
                    "source": "https://codeload.github.com/example/comfyui-node/zip/"
                    + ("a" * 40),
                    "source_ref": "a" * 40,
                    "source_content_hash": "sha256:" + ("b" * 64),
                    "trust_level": "quarantined_community",
                    "node_types": ["ExampleNode"],
                }
            ]
        ),
    )

    result = await service.prepare_workflow("workflow")

    assert result["status"] == "failed"
    assert result["last_error"] == "Noofy could not verify a downloaded workflow extension."
    assert result["last_error_code"] == "hash_mismatch"
    assert installer.prepare_calls == 0


def _service(
    *,
    loader: "_WorkflowLoader",
    installer: "_Installer",
    imported_store: "_ImportedStore",
    custom_node_source_cache: object | None = None,
    capsule: CapsuleLock | None = None,
) -> WorkflowRunnerLifecycleService:
    return WorkflowRunnerLifecycleService(
        workflow_loader=loader,
        capsule_loader=_CapsuleLoader(capsule or _capsule()),
        capsule_installer=installer,  # type: ignore[arg-type]
        runner_supervisor=_RunnerSupervisor(),  # type: ignore[arg-type]
        imported_package_store=imported_store,  # type: ignore[arg-type]
        custom_node_source_cache=custom_node_source_cache,  # type: ignore[arg-type]
        log_store=_LogStore(),  # type: ignore[arg-type]
    )


class _WorkflowLoader:
    def __init__(self, package: WorkflowPackage) -> None:
        self.package = package

    def get_package(self, workflow_id: str) -> WorkflowPackage:
        if workflow_id != "workflow":
            raise KeyError(workflow_id)
        return self.package


class _CapsuleLoader:
    def __init__(self, capsule: CapsuleLock) -> None:
        self.capsule = capsule

    def get_bundled_capsule_lock(self, workflow_id: str) -> CapsuleLock:
        if workflow_id != "workflow":
            raise KeyError(workflow_id)
        return self.capsule


class _RunnerSupervisor:
    def begin_workflow_preparation(self, workflow_id: str) -> bool:
        return True

    def end_workflow_preparation(self, workflow_id: str) -> None:
        return None


class _Installer:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode
        self.prepare_calls = 0
        self.prepared_capsules: list[CapsuleLock] = []
        self.install_state_store = _InstallStateStore()

    def get_state(self, capsule_lock: CapsuleLock) -> InstallState:
        return _install_state(InstallStatus.PENDING)

    async def prepare(self, *args, **kwargs) -> InstallState:
        self.prepare_calls += 1
        self.prepared_capsules.append(args[0])
        if self.mode != "ready" and self.prepare_calls == 1:
            raise CapsuleInstallError(
                "This workflow uses nodes that the current engine does not recognize.",
                state=_engine_missing_state(["FutureCoreNode"]),
            )
        return _install_state(InstallStatus.READY, smoke_status=SmokeTestStatus.PASSED)


class _InstallStateStore:
    def update(
        self,
        capsule_fingerprint: str,
        **kwargs,
    ) -> InstallState:
        return _install_state(kwargs["status"]).model_copy(
            update={
                "last_error": kwargs.get("last_error"),
                "last_error_code": kwargs.get("last_error_code"),
            }
        )


class _CustomNodeSourceCache:
    def __init__(self) -> None:
        self.sources = []
        self.source_policies = []
        self.source_origins = []

    def materialize(self, source, *, source_policy=None, source_origins=None):
        self.sources.append(source)
        self.source_policies.append(source_policy)
        self.source_origins.append(source_origins)
        return type("CachedSource", (), {"source_cache_ref": "cached/source"})()


class _FailingCustomNodeSourceCache:
    def materialize(self, source, *, source_policy=None, source_origins=None):
        raise NodeRegistryResolutionError(
            NodeRegistryResolutionErrorCode.HASH_MISMATCH,
            "Noofy could not verify a downloaded workflow extension.",
            developer_details={
                "source_url": source.source_url,
                "expected_sha256": "b" * 64,
                "actual_sha256": "c" * 64,
            },
        )


class _ImportedStore:
    def __init__(self, loader: _WorkflowLoader, *, mode: str) -> None:
        self.loader = loader
        self.mode = mode
        self.missing_node_types: list[str] = []

    def resolve_missing_engine_nodes_automatically(
        self,
        package: WorkflowPackage,
        *,
        missing_node_types: list[str],
        allow_unverified_community_preparation: bool,
    ) -> WorkflowPackage:
        self.missing_node_types = list(missing_node_types)
        if self.mode == "resolved":
            return _package_with_resolution(
                package,
                status="imported",
                source_resolution={"status": "resolved", "resolved_custom_nodes": []},
                auto_attempt=True,
            )
        return _package_with_resolution(
            package,
            status="engine_unrecognized_nodes",
            user_facing_message="This workflow uses nodes that the current engine does not recognize.",
            source_resolution={
                "status": "engine_unrecognized_nodes",
                "mode": "manual_url",
                "reason": "github_search_no_candidate",
                "unresolved_node_types": list(missing_node_types),
                "ambiguous_node_types": [],
                "automatic_resolution_failures": ["No reliable candidate found."],
                "update_guidance": (
                    "This can also happen if your managed ComfyUI engine is too old. "
                    "You can update the engine in Settings, then retry."
                ),
            },
        )

    def persist_custom_node_resolution(self, package: WorkflowPackage) -> WorkflowPackage:
        self.loader.package = package
        return package

    def with_engine_unrecognized_nodes(
        self,
        package: WorkflowPackage,
        *,
        missing_node_types: list[str],
        reason: str,
        automatic_resolution_failures: list[str] | None = None,
    ) -> WorkflowPackage:
        return _package_with_resolution(
            package,
            status="engine_unrecognized_nodes",
            user_facing_message="This workflow uses nodes that the current engine does not recognize.",
            source_resolution={
                "status": "engine_unrecognized_nodes",
                "mode": "manual_url",
                "reason": reason,
                "unresolved_node_types": list(missing_node_types),
                "ambiguous_node_types": [],
                "automatic_resolution_failures": automatic_resolution_failures or [],
                "update_guidance": (
                    "This can also happen if your managed ComfyUI engine is too old. "
                    "You can update the engine in Settings, then retry."
                ),
            },
        )


class _LogStore:
    def add(self, *args, **kwargs) -> None:
        return None


def _package() -> WorkflowPackage:
    return WorkflowPackage(
        metadata=WorkflowMetadata(
            id="workflow",
            name="Workflow",
            version="0.1.0",
            description="",
            author="local",
        ),
        identity=WorkflowPackageIdentity(
            publisher_id="local",
            package_id="workflow",
            version="0.1.0",
            trust_level="quarantined_community",
            source="raw_comfyui_json_import",
        ),
        engine="comfyui",
        comfyui_graph={"1": {"class_type": "FutureCoreNode", "inputs": {}}},
        import_metadata=WorkflowImportMetadata(status="imported"),
    )


def _package_with_resolution(
    package: WorkflowPackage,
    *,
    status: str,
    source_resolution: dict[str, object],
    user_facing_message: str = "Imported",
    auto_attempt: bool = False,
) -> WorkflowPackage:
    assert package.import_metadata is not None
    developer_details = dict(package.import_metadata.developer_details)
    developer_details["source_resolution"] = source_resolution
    if auto_attempt:
        developer_details["engine_node_auto_resolution"] = {
            "status": "attempted",
            "method": "test",
            "node_types": ["FutureCoreNode"],
        }
    return package.model_copy(
        update={
            "import_metadata": package.import_metadata.model_copy(
                update={
                    "status": status,
                    "user_facing_message": user_facing_message,
                    "developer_details": developer_details,
                }
            )
        }
    )


def _engine_missing_state(missing_node_types: list[str]) -> InstallState:
    return _install_state(
        InstallStatus.FAILED,
        smoke_status=SmokeTestStatus.FAILED,
        smoke_report=SmokeTestReport(
            dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
            custom_node_import=SmokeStageResult(status=SmokeStageStatus.SKIPPED),
            runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
            workflow_execution=SmokeStageResult(
                status=SmokeStageStatus.FAILED,
                message="This workflow uses nodes that the current engine does not recognize.",
                details={
                    "reason": "engine_unrecognized_node_types",
                    "missing_node_types": missing_node_types,
                },
            ),
        ),
    )


def _install_state(
    status: InstallStatus,
    *,
    smoke_status: SmokeTestStatus = SmokeTestStatus.NOT_RUN,
    smoke_report: SmokeTestReport | None = None,
) -> InstallState:
    return InstallState(
        schema_version=INSTALL_STATE_SCHEMA_VERSION,
        capsule_fingerprint="sha256:" + ("1" * 64),
        status=status,
        smoke_test_status=smoke_status,
        smoke_test_report=smoke_report or SmokeTestReport(),
    )


def _capsule(custom_nodes: list[dict[str, object]] | None = None) -> CapsuleLock:
    return CapsuleLock.model_validate(
        {
            "schema_version": "0.1.0",
            "workflow": {
                "publisher_id": "local",
                "package_id": "workflow",
                "version": "0.1.0",
                "trust_level": "quarantined_community",
                "source": "raw_comfyui_json_import",
            },
            "engine": {
                "type": "comfyui",
                "comfyui_version": "v0.0.0",
                "core_source_hash": "sha256:" + ("2" * 64),
            },
            "runtime": {
                "runtime_profile_id": "profile",
                "runtime_profile_variant_id": "variant",
                "runtime_profile_manifest_hash": "sha256:" + ("3" * 64),
                "runtime_profile_catalog_version": "0.1.0",
                "fingerprint_schema_version": "0.1.0",
                "dependency_env_fingerprint": "sha256:" + ("4" * 64),
                "runner_fingerprint": "sha256:" + ("5" * 64),
                "capsule_fingerprint": "sha256:" + ("1" * 64),
                "os": "linux",
                "architecture": "x64",
                "python_version": "3.12",
                "python_build_id": "python",
                "gpu_backend": "cpu",
                "dependency_lock_hash": "sha256:" + ("6" * 64),
                "runner_workspace_hash": "sha256:" + ("7" * 64),
            },
            "custom_nodes": custom_nodes or [],
            "dependencies": {
                "lock_file": "community-runtime.lock",
                "install_policy": "quarantined-community-v1",
            },
            "models": [],
            "trust": {"level": "quarantined_community", "publisher": "local"},
        }
    )
