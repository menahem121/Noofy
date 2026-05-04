from __future__ import annotations

import io
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

from app.engine.diagnostics import LogStore
from app.engine.service import EngineService
from app.runtime.custom_nodes import (
    CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME,
    CustomNodeWorkspaceMaterializer,
)
from app.runtime.node_registry import CustomNodeSourceCache, NodeRegistryResolver, NoofyNodeRegistry
from app.runtime.profiles import load_runtime_profile_catalog
from app.runtime.supervisor import CORE_RUNNER_FINGERPRINT, CORE_RUNNER_ID, RunnerDescriptor, RunnerKind, RunnerSupervisor
from app.trust import (
    TrustedSignatureKey,
    TrustKeyring,
    TrustSignaturePurpose,
    TrustVerifier,
    hmac_sha256_signature,
    load_trust_verifier,
    registry_signature_payload,
)
from app.runtime.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.workspace_store import DependencyEnvManifestStore, RunnerWorkspaceManifestStore
from app.workflows.capsule import CapsuleLockLoader
from app.workflows import importer as importer_module
from app.workflows.importer import (
    ImportedWorkflowPackageStore,
    NoofyArchiveImporter,
    NoofyImportError,
    _normalize_models,
)
from app.workflows.loader import WorkflowPackageLoader
from app.workflows.validator import WorkflowPackageValidator


class StubRuntimeManager:
    base_url = "http://127.0.0.1:8188"
    ws_url = "ws://127.0.0.1:8188/ws"


class StubAdapter:
    def configure_endpoint(self, base_url: str, ws_url: str | None = None) -> None:
        pass

    async def list_available_models(self):
        return []


class FakeSourceFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.urls.append(url)
        return self.payload


def _archive_bytes() -> bytes:
    root = Path(__file__).resolve().parents[2]
    return (root / "test_workflows" / "exported-workflow-for-testing.noofy").read_bytes()


def test_noofy_importer_normalizes_real_export_without_importing_custom_nodes() -> None:
    custom_nodes_was_loaded = "custom_nodes" in sys.modules

    package = NoofyArchiveImporter(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    ).normalize()

    assert package.metadata.id == "unknown__controlnet_two_model_workflow__0.1.0"
    assert package.metadata.name == "controlnet_two_model_workflow"
    assert package.identity is not None
    assert package.identity.publisher_id == "unknown"
    assert package.identity.package_id == "controlnet_two_model_workflow"
    assert package.identity.trust_level == "quarantined_community"
    assert len(package.custom_nodes) == 5
    assert {model.folder for model in package.required_models} == {"checkpoints", "controlnet"}
    assert {
        model.filename for model in package.required_models
    } == {
        "DreamShaperXL_Lightning.safetensors",
        "diffusion_pytorch_model_promax.safetensors",
    }
    assert {model.verification_level for model in package.required_models} == {"sha256_size"}
    assert all(model.identity_verified_by_exporter for model in package.required_models)
    assert all(model.bundled is False for model in package.required_models)
    assert {model.asset_ownership for model in package.required_models} == {"external_reference"}
    assert package.unresolved_runtime_inputs
    assert package.unresolved_runtime_inputs[0].node_type == "LoadImage"
    assert package.unresolved_runtime_inputs[0].reason == "creator_local_image_not_bundled"
    assert package.dashboard.sections[0].title == "Input setup needed"
    assert package.assets.thumbnail == "source-files/assets/thumbnail.png"
    assert package.observed_hardware["observed_peak_ram_mb"] == 6657
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.import_metadata.user_facing_message == "Needs input setup"
    assert package.import_metadata.source_archive_sha256.startswith("sha256:")
    assert ("custom_nodes" in sys.modules) is custom_nodes_was_loaded


def test_noofy_importer_preserves_phase6_signature_metadata() -> None:
    archive = _archive_bytes_with_package_update(
        {
            "trust_level": "registry_locked",
            "signature": "sig:package",
            "signatures": [
                {
                    "key_id": "noofy-registry-2026",
                    "algorithm": "ed25519",
                    "value": "sig:detached",
                }
            ],
            "signed_registry_metadata": {
                "registry_id": "noofy-community-registry",
                "snapshot_hash": "sha256:" + "a" * 64,
                "signature": "sig:registry",
            },
        }
    )

    package = NoofyArchiveImporter(archive, original_filename="signed.noofy").normalize()

    assert package.identity is not None
    assert package.identity.trust_level == "registry_locked"
    assert package.identity.signature == "sig:package"
    assert package.identity.signatures[0].key_id == "noofy-registry-2026"
    assert package.identity.signed_registry_metadata is not None
    assert package.identity.signed_registry_metadata.registry_id == "noofy-community-registry"


def test_import_store_rejects_imported_noofy_verified_without_valid_signature(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())

    package = store.import_archive(
        _archive_bytes_with_package_update({"trust_level": "noofy_verified"}),
        original_filename="unsigned-verified.noofy",
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status == "unsupported"
    assert package.identity is not None
    assert package.identity.trust_level == "unsupported"
    trust_verification = package.import_metadata.developer_details["trust_verification"]
    assert trust_verification["status"] == "missing_signature"
    assert trust_verification["requested_trust_level"] == "noofy_verified"
    assert trust_verification["effective_trust_level"] == "unsupported"


def test_import_store_accepts_imported_noofy_verified_with_trusted_signature(tmp_path: Path) -> None:
    secret = "test-noofy-verified-secret"
    unsigned = _archive_bytes_with_package_update({"trust_level": "noofy_verified"})
    signature = hmac_sha256_signature(
        NoofyArchiveImporter(unsigned).trust_payload(),
        secret,
    )
    signed = _archive_bytes_with_package_update(
        {
            "trust_level": "noofy_verified",
            "signatures": [
                {
                    "key_id": "noofy-test-key",
                    "algorithm": "hmac-sha256",
                    "value": f"hmac-sha256:{signature}",
                }
            ],
        }
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        trust_verifier=TrustVerifier(
            [
                TrustedSignatureKey(
                    key_id="noofy-test-key",
                    secret=secret,
                    purpose=TrustSignaturePurpose.PACKAGE,
                )
            ]
        ),
    )

    package = store.import_archive(signed, original_filename="signed-verified.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.identity is not None
    assert package.identity.trust_level == "noofy_verified"
    assert package.import_metadata.developer_details["trust_verification"]["status"] == "verified"


def test_import_store_accepts_registry_locked_with_signed_registry_metadata(tmp_path: Path) -> None:
    secret = "test-registry-secret"
    unsigned = _archive_bytes_with_package_update({"trust_level": "registry_locked"})
    package_payload = NoofyArchiveImporter(unsigned).trust_payload()
    metadata_payload = registry_signature_payload(
        package_payload=package_payload,
        registry_id="noofy-community-registry",
        snapshot_hash="sha256:" + "b" * 64,
    )
    signed = _archive_bytes_with_package_update(
        {
            "trust_level": "registry_locked",
            "signed_registry_metadata": {
                "registry_id": "noofy-community-registry",
                "snapshot_hash": "sha256:" + "b" * 64,
                "key_id": "registry-test-key",
                "algorithm": "hmac-sha256",
                "signature": hmac_sha256_signature(metadata_payload, secret),
            },
        }
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        trust_verifier=TrustVerifier(
            [
                TrustedSignatureKey(
                    key_id="registry-test-key",
                    secret=secret,
                    purpose=TrustSignaturePurpose.REGISTRY,
                )
            ]
        ),
    )

    package = store.import_archive(signed, original_filename="registry-locked.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.identity is not None
    assert package.identity.trust_level == "registry_locked"
    trust_verification = package.import_metadata.developer_details["trust_verification"]
    assert trust_verification["status"] == "verified"
    assert trust_verification["evidence_type"] == "signed_registry_metadata"


def test_trust_keyring_loader_uses_file_without_exposing_secrets(tmp_path: Path) -> None:
    keyring_path = tmp_path / "trusted-keys.json"
    keyring_path.write_text(
        TrustKeyring(
            keys=[
                TrustedSignatureKey(
                    key_id="noofy-test-key",
                    secret="local-secret",
                    purpose=TrustSignaturePurpose.PACKAGE,
                )
            ]
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    verifier = load_trust_verifier(keyring_path)
    payload = verifier.policy_payload()

    assert payload["trusted_key_count"] == 1
    assert payload["trusted_keys"] == [
        {
            "key_id": "noofy-test-key",
            "algorithm": "hmac-sha256",
            "purpose": "package",
        }
    ]
    assert "local-secret" not in json.dumps(payload)


def test_malformed_trust_keyring_logs_warning_and_disables_trusted_claims(tmp_path: Path) -> None:
    log_store = LogStore()
    keyring_path = tmp_path / "trusted-keys.json"
    keyring_path.write_text("{not-json", encoding="utf-8")

    verifier = load_trust_verifier(keyring_path, log_store=log_store)

    assert verifier.policy_payload()["trusted_key_count"] == 0
    latest = log_store.list_events().events[-1]
    assert latest.message == "Trust keyring could not be loaded"
    assert latest.details["path"] == str(keyring_path)


def test_import_store_persists_normalized_package_and_original_source_files(tmp_path: Path) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    package = store.import_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )

    package_dir = tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0"
    assert (package_dir / "package.json").exists()
    assert (package_dir / "capsule.lock.json").exists()
    assert (package_dir / "exported-capsule.lock.json").exists()
    assert (package_dir / "source-archive.noofy").exists()
    assert (package_dir / "source-files" / "package.json").exists()
    assert (package_dir / "source-files" / "custom_nodes" / "comfyui-kjnodes" / "requirements.txt").exists()
    import_report = json.loads((package_dir / "import-report.json").read_text(encoding="utf-8"))
    assert import_report["runtime_resolution"]["selection_stage"] == "import_time_phase5c"
    assert import_report["runtime_resolution"]["runtime_profile_variant_id"]

    loader = WorkflowPackageLoader(Path("missing-bundled"), imported_packages_dir=tmp_path / "packages")
    loaded = loader.get_package(package.metadata.id)
    assert loaded.metadata.name == "controlnet_two_model_workflow"
    assert loaded.custom_nodes[0].included is True
    assert loaded.required_models[0].checksum is not None
    assert loaded.import_metadata is not None
    assert loaded.import_metadata.status == "needs_input_setup"
    assert log_store.list_events().events[-1].message == "Imported workflow package"

    capsule = CapsuleLockLoader(Path("missing-bundled"), imported_packages_dir=tmp_path / "packages").get_capsule_lock(
        package.metadata.id
    )
    assert capsule.workflow.package_id == "controlnet_two_model_workflow"
    assert len(capsule.custom_nodes) == 5
    assert capsule.runtime.runtime_profile_manifest_hash.startswith("sha256:")


def test_import_store_blocks_non_bundled_community_custom_node_without_opt_in(tmp_path: Path) -> None:
    source_archive = _source_archive_bytes({"node.py": "NODE_CLASS_MAPPINGS = {}\n"})
    archive = _archive_bytes_with_custom_node_update(
        0,
        {
            "included": False,
            "source": "https://example.test/comfyui-jps/archive/pinned.zip",
            "source_ref": "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21",
            "source_content_hash": f"sha256:{hashlib.sha256(source_archive).hexdigest()}",
        },
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())

    package = store.import_archive(archive, original_filename="community.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "blocked_by_policy"
    assert package.import_metadata.developer_details["source_resolution"]["reason"] == "community_opt_in_required"
    assert package.identity is not None
    assert package.identity.trust_level == "unsupported"
    package_dir = tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0"
    import_report = json.loads((package_dir / "import-report.json").read_text(encoding="utf-8"))
    assert import_report["source_resolution"]["status"] == "blocked_by_policy"
    capsule = CapsuleLockLoader(Path("missing-bundled"), imported_packages_dir=tmp_path / "packages").get_capsule_lock(
        package.metadata.id
    )
    assert capsule.workflow.trust_level == "unsupported"
    assert capsule.trust.level.value == "unsupported"


def test_import_store_resolves_opted_in_non_bundled_custom_node_to_cached_lock(tmp_path: Path) -> None:
    source_archive = _source_archive_bytes(
        {
            "repo-root/node.py": "NODE_CLASS_MAPPINGS = {}\n",
            "repo-root/requirements.txt": "numpy==1.26.4\n",
            "other-root/ignored.py": "ignored\n",
        }
    )
    source_digest = hashlib.sha256(source_archive).hexdigest()
    fetcher = FakeSourceFetcher(source_archive)
    archive = _archive_bytes_with_custom_node_update(
        0,
        {
            "included": False,
            "source": "https://example.test/comfyui-jps/archive/pinned.zip",
            "source_ref": "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21",
            "source_content_hash": f"sha256:{source_digest}",
            "source_archive_subdir": "repo-root",
        },
    )
    source_cache_dir = tmp_path / "custom-node-cache"
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(registry=NoofyNodeRegistry(registry_id="empty-test-registry")),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=source_cache_dir,
            fetcher=fetcher,
        ),
    )

    package = store.import_archive(
        archive,
        original_filename="community.noofy",
        allow_unverified_community_preparation=True,
    )

    resolved_node = next(node for node in package.custom_nodes if node.id == "comfyui_jps-nodes")
    assert fetcher.urls == ["https://example.test/comfyui-jps/archive/pinned.zip"]
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.import_metadata.developer_details["source_resolution"]["status"] == "resolved"
    assert resolved_node.included is True
    assert resolved_node.source_cache_ref == f"{source_digest}/source"
    assert resolved_node.source_ref == "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21"
    assert resolved_node.source_content_hash == f"sha256:{source_digest}"
    assert resolved_node.source_archive_subdir == "repo-root"

    package_dir = tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0"
    capsule = CapsuleLockLoader(Path("missing-bundled"), imported_packages_dir=tmp_path / "packages").get_capsule_lock(
        package.metadata.id
    )
    cached_lock = next(node for node in capsule.custom_nodes if node.package_id == "comfyui_jps-nodes")
    assert cached_lock.source_cache_ref == f"{source_digest}/source"
    assert cached_lock.source_ref == "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21"
    assert cached_lock.source_content_hash == f"sha256:{source_digest}"

    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        runtime_profile_catalog=load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json")),
        custom_node_materializer=CustomNodeWorkspaceMaterializer(),
        custom_node_source_files_dir=package_dir / "source-files",
        custom_node_source_cache_dir=source_cache_dir,
    )
    prepared = preparer.prepare(capsule)
    cached_node_path = prepared.runner_workspace_path / "custom_nodes" / "comfyui_jps-nodes" / "node.py"
    assert cached_node_path.exists()
    assert "NODE_CLASS_MAPPINGS" in cached_node_path.read_text(encoding="utf-8")
    manifest = json.loads(
        (prepared.runner_workspace_path / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    cached_entry = next(entry for entry in manifest["entries"] if entry["custom_node_package_id"] == "comfyui_jps-nodes")
    assert cached_entry["source_kind"] == "noofy_cached_archive"


def test_import_runtime_profile_prefers_linux_cuda_when_nvidia_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    monkeypatch.setattr(importer_module, "_current_os_name", lambda: "linux")
    monkeypatch.setattr(importer_module, "_current_architecture", lambda: "x64")
    monkeypatch.setattr(importer_module, "_has_nvidia_gpu", lambda: True)

    _, variant = importer_module._select_import_runtime_profile(catalog.profiles)

    assert variant.runtime_profile_variant_id == "linux-x64-cuda130"


def test_import_runtime_profile_falls_back_to_linux_cpu_without_nvidia(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    monkeypatch.setattr(importer_module, "_current_os_name", lambda: "linux")
    monkeypatch.setattr(importer_module, "_current_architecture", lambda: "x64")
    monkeypatch.setattr(importer_module, "_has_nvidia_gpu", lambda: False)

    _, variant = importer_module._select_import_runtime_profile(catalog.profiles)

    assert variant.runtime_profile_variant_id == "linux-x64-cpu"


def test_imported_real_archive_can_materialize_custom_node_workspace(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages")
    package = store.import_archive(_archive_bytes(), original_filename="exported-workflow-for-testing.noofy")
    package_dir = tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0"
    capsule = CapsuleLockLoader(Path("missing-bundled"), imported_packages_dir=tmp_path / "packages").get_capsule_lock(
        package.metadata.id
    )
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(tmp_path / "runner-workspaces"),
        runtime_profile_catalog=load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json")),
        custom_node_materializer=CustomNodeWorkspaceMaterializer(),
        custom_node_source_files_dir=package_dir / "source-files",
    )

    prepared = preparer.prepare(capsule)

    assert (prepared.runner_workspace_path / "custom_nodes" / "comfyui-kjnodes" / "requirements.txt").exists()
    assert (prepared.runner_workspace_path / "custom_nodes" / "comfyui_controlnet_aux" / "requirements.txt").exists()
    manifest_path = prepared.runner_workspace_path / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 5


def test_import_store_rejects_exported_launch_options(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages")

    with pytest.raises(NoofyImportError, match="unsupported launch options"):
        store.import_archive(
            _archive_bytes_with_capsule_update({"launch_options": {"vram_mode": "highvram"}}),
            original_filename="launch-options.noofy",
        )

    assert not (tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0").exists()


def test_importer_normalizes_untrusted_model_identity_and_ownership_values() -> None:
    models = _normalize_models(
        {
            "models": [
                {
                    "comfyui_folder": "checkpoints",
                    "filename": "model.safetensors",
                    "size_bytes": 123,
                    "verification_level": "creator_claimed_verified",
                    "asset_ownership": "delete_anyway",
                }
            ]
        }
    )

    assert models[0].verification_level == "filename_size"
    assert models[0].asset_ownership == "external_reference"


def test_imported_package_cannot_shadow_bundled_workflow_by_id(tmp_path: Path) -> None:
    bundled_dir = tmp_path / "bundled" / "text_to_image_v0"
    imported_dir = tmp_path / "imported" / "unknown" / "text_to_image_v0" / "0.1.0"
    bundled_dir.mkdir(parents=True)
    imported_dir.mkdir(parents=True)

    package = NoofyArchiveImporter(_archive_bytes()).normalize()
    bundled_package = package.model_copy(
        update={
            "metadata": package.metadata.model_copy(
                update={"id": "text_to_image_v0", "name": "Bundled"}
            )
        }
    )
    imported_package = package.model_copy(
        update={
            "metadata": package.metadata.model_copy(
                update={"id": "text_to_image_v0", "name": "Imported Shadow"}
            )
        }
    )
    (bundled_dir / "package.json").write_text(bundled_package.model_dump_json(), encoding="utf-8")
    (imported_dir / "package.json").write_text(imported_package.model_dump_json(), encoding="utf-8")

    loaded = WorkflowPackageLoader(
        tmp_path / "bundled",
        imported_packages_dir=tmp_path / "imported",
    ).get_package("text_to_image_v0")

    assert loaded.metadata.name == "Bundled"


def test_noofy_importer_rejects_zip_path_traversal() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.txt", "bad")

    with pytest.raises(NoofyImportError, match="unsafe path"):
        NoofyArchiveImporter(payload.getvalue()).normalize()


def test_import_store_logs_failed_import_without_persisting_package(tmp_path: Path) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    with pytest.raises(NoofyImportError, match="unsafe path"):
        store.import_archive(_unsafe_zip_bytes(), original_filename="bad.noofy")

    assert not (tmp_path / "packages" / "_transactions").exists()
    latest = log_store.list_events().events[-1]
    assert latest.message == "Workflow import failed"
    assert latest.level == "warning"
    assert latest.details["original_filename"] == "bad.noofy"


def test_engine_service_imports_real_archive_and_exposes_normalized_package(tmp_path: Path) -> None:
    log_store = LogStore()
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url=StubRuntimeManager.base_url,
            ws_url=StubRuntimeManager.ws_url,
            fingerprint=CORE_RUNNER_FINGERPRINT,
        ),
        StubAdapter(),
    )
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(
            Path("missing-bundled"),
            imported_packages_dir=tmp_path / "packages",
        ),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=log_store,
        imported_package_store=ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store),
    )

    result = service.import_workflow_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )
    package = service.get_workflow_package(result["workflow_id"])
    summaries = service.list_workflows()

    assert result["status"] == "needs_input_setup"
    assert package["metadata"]["name"] == "controlnet_two_model_workflow"
    assert package["unresolved_runtime_inputs"][0]["reason"] == "creator_local_image_not_bundled"
    assert summaries[0]["status"] == "needs_input_setup"
    assert summaries[0]["status_label"] == "Needs input setup"
    assert summaries[0]["unresolved_input_count"] == 1


def _unsafe_zip_bytes() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.txt", "bad")
    return payload.getvalue()


def _archive_bytes_with_capsule_update(update: dict) -> bytes:
    source = io.BytesIO(_archive_bytes())
    payload = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(payload, "w") as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "capsule.lock.json":
                capsule = json.loads(contents.decode("utf-8"))
                capsule.update(update)
                contents = json.dumps(capsule).encode("utf-8")
            rewritten.writestr(info, contents)
    return payload.getvalue()


def _archive_bytes_with_package_update(update: dict) -> bytes:
    source = io.BytesIO(_archive_bytes())
    payload = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(payload, "w") as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "package.json":
                package = json.loads(contents.decode("utf-8"))
                package.update(update)
                contents = json.dumps(package).encode("utf-8")
            rewritten.writestr(info, contents)
    return payload.getvalue()


def _archive_bytes_with_custom_node_update(index: int, update: dict) -> bytes:
    source = io.BytesIO(_archive_bytes())
    payload = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(payload, "w") as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "capsule.lock.json":
                capsule = json.loads(contents.decode("utf-8"))
                custom_nodes = list(capsule["custom_nodes"])
                custom_nodes[index] = {**custom_nodes[index], **update}
                capsule["custom_nodes"] = custom_nodes
                contents = json.dumps(capsule).encode("utf-8")
            rewritten.writestr(info, contents)
    return payload.getvalue()


def _source_archive_bytes(files: dict[str, str]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return payload.getvalue()
