from __future__ import annotations

import base64
import io
import hashlib
import json
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.diagnostics import LogStore
from app.engine.service import EngineService
from app.runtime.capsule_installer import CapsuleInstaller
from app.runtime.dependencies.custom_nodes import (
    CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME,
    CustomNodeWorkspaceMaterializer,
)
from app.runtime.install_state import InstallStateStore
from app.runtime.dependencies.isolation import (
    InstallStatus,
    SmokeStageResult,
    SmokeStageStatus,
    SmokeTestReport,
)
from app.runtime.models.model_store import ModelStore
from app.runtime.node_registry import (
    CustomNodeSourceCache,
    NodeRegistryResolver,
    NoofyNodeRegistry,
)
from app.runtime.profiles import load_runtime_profile_catalog
from app.runtime.runners.supervisor import (
    CORE_RUNNER_FINGERPRINT,
    CORE_RUNNER_ID,
    RunnerDescriptor,
    RunnerKind,
    RunnerStatus,
    RunnerSupervisor,
)
from app.trust import (
    TrustedSignatureKey,
    TrustKeyring,
    TrustSignaturePurpose,
    TrustVerifier,
    canonical_trust_payload_bytes,
    hmac_sha256_signature,
    load_trust_verifier,
    registry_signature_payload,
)
from app.runtime.storage.workspace_preparer import RuntimeWorkspacePreparer
from app.runtime.storage.workspace_store import (
    DependencyEnvManifestStore,
    RunnerWorkspaceManifestStore,
)
from app.workflows.capsule import CapsuleLockLoader
from app.workflows import importer as importer_module
from app.workflows import import_runtime_profile as import_runtime_profile_module
from app.workflows.import_normalization import normalized_display_name
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


@pytest.fixture(autouse=True)
def _supported_import_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_runtime_profile_module, "current_os_name", lambda: "linux")
    monkeypatch.setattr(import_runtime_profile_module, "current_architecture", lambda: "x64")
    monkeypatch.setattr(import_runtime_profile_module, "has_nvidia_gpu", lambda: False)


def _archive_bytes() -> bytes:
    root = Path(__file__).resolve().parents[2]
    return (
        root / "test_workflows" / "exported-workflow-for-testing.noofy"
    ).read_bytes()


def _test_workflow_archive_bytes(filename: str) -> bytes:
    root = Path(__file__).resolve().parents[2]
    return (root / "test_workflows" / filename).read_bytes()


def _small_archive_bytes() -> bytes:
    return _test_workflow_archive_bytes("core_empty_image_smoke.noofy")


def test_normalized_display_name_prefers_top_level_display_over_legacy_metadata_name() -> None:
    assert normalized_display_name(
        {
            "display_name": "Friendly Workflow",
            "metadata": {
                "name": "technical_package_id",
                "version": "1.0.0",
            },
        },
        fallback="technical_package_id",
    ) == "Friendly Workflow"


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
    assert {model.folder for model in package.required_models} == {
        "checkpoints",
        "controlnet",
    }
    assert {model.filename for model in package.required_models} == {
        "DreamShaperXL_Lightning.safetensors",
        "diffusion_pytorch_model_promax.safetensors",
    }
    assert {model.verification_level for model in package.required_models} == {
        "sha256_size"
    }
    assert all(model.identity_verified_by_exporter for model in package.required_models)
    assert all(model.bundled is False for model in package.required_models)
    assert {model.asset_ownership for model in package.required_models} == {
        "external_reference"
    }
    assert package.unresolved_runtime_inputs
    assert package.unresolved_runtime_inputs[0].node_type == "LoadImage"
    assert (
        package.unresolved_runtime_inputs[0].reason == "creator_local_image_not_bundled"
    )
    assert package.dashboard.status == "not_configured"
    assert package.assets.thumbnail == "source-files/assets/thumbnail.png"
    assert package.observed_hardware["observed_peak_ram_mb"] == 6657
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.import_metadata.user_facing_message == "Needs input setup"
    assert package.import_metadata.source_archive_sha256.startswith("sha256:")
    assert ("custom_nodes" in sys.modules) is custom_nodes_was_loaded


def test_noofy_importer_normalizes_single_model_source_url_string() -> None:
    source_url = "https://example.test/upscale.safetensors"
    archive = _archive_bytes_with_capsule_update(
        {
            "models": [
                {
                    "comfyui_folder": "upscale_models",
                    "filename": "upscale.safetensors",
                    "source_urls": source_url,
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                    "verification_level": "sha256_size",
                }
            ]
        },
        archive_bytes=_small_archive_bytes(),
    )

    package = NoofyArchiveImporter(archive).normalize()

    model = package.required_models[0]
    assert model.source_url == source_url
    assert model.source_urls == [source_url]


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
        },
        archive_bytes=_small_archive_bytes(),
    )

    package = NoofyArchiveImporter(
        archive, original_filename="signed.noofy"
    ).normalize()

    assert package.identity is not None
    assert package.identity.trust_level == "registry_locked"
    assert package.identity.signature == "sig:package"
    assert package.identity.signatures[0].key_id == "noofy-registry-2026"
    assert package.identity.signed_registry_metadata is not None
    assert (
        package.identity.signed_registry_metadata.registry_id
        == "noofy-community-registry"
    )


def test_noofy_importer_reuses_parsed_json_for_trust_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer = NoofyArchiveImporter(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )
    archive_read = importer.archive.read
    json_reads: list[str] = []

    def tracked_read(name_or_info, *args, **kwargs):
        name = (
            name_or_info.filename
            if isinstance(name_or_info, zipfile.ZipInfo)
            else str(name_or_info)
        )
        if name.endswith(".json"):
            json_reads.append(name)
        return archive_read(name_or_info, *args, **kwargs)

    monkeypatch.setattr(importer.archive, "read", tracked_read)

    importer.normalize()
    importer.trust_payload()

    assert json_reads.count("package.json") == 1
    assert json_reads.count("comfyui_graph.json") == 1
    assert json_reads.count("dashboard.json") == 1
    assert json_reads.count("capsule.lock.json") == 1
    assert json_reads.count("export-report.json") == 1


def test_import_store_rejects_imported_noofy_verified_without_valid_signature(
    tmp_path: Path,
) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())

    package = store.preview_archive(
        _archive_bytes_with_package_update(
            {"trust_level": "noofy_verified"},
            archive_bytes=_small_archive_bytes(),
        ),
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


def test_import_store_accepts_imported_noofy_verified_with_trusted_signature(
    tmp_path: Path,
) -> None:
    secret = "test-noofy-verified-secret"
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "noofy_verified"},
        archive_bytes=archive_bytes,
    )
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
        },
        archive_bytes=archive_bytes,
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
            ],
            allow_development_hmac=True,
        ),
    )

    package = store.preview_archive(signed, original_filename="signed-verified.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.identity is not None
    assert package.identity.trust_level == "noofy_verified"
    assert (
        package.import_metadata.developer_details["trust_verification"]["status"]
        == "verified"
    )


def test_import_store_accepts_registry_locked_with_signed_registry_metadata(
    tmp_path: Path,
) -> None:
    secret = "test-registry-secret"
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "registry_locked"},
        archive_bytes=archive_bytes,
    )
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
        },
        archive_bytes=archive_bytes,
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
            ],
            allow_development_hmac=True,
        ),
    )

    package = store.preview_archive(signed, original_filename="registry-locked.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.identity is not None
    assert package.identity.trust_level == "registry_locked"
    trust_verification = package.import_metadata.developer_details["trust_verification"]
    assert trust_verification["status"] == "verified"
    assert trust_verification["evidence_type"] == "signed_registry_metadata"


def test_import_store_accepts_imported_noofy_verified_with_ed25519_signature(
    tmp_path: Path,
) -> None:
    private_key, public_key = _ed25519_keypair()
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "noofy_verified"},
        archive_bytes=archive_bytes,
    )
    package_payload = NoofyArchiveImporter(unsigned).trust_payload()
    signed = _archive_bytes_with_package_update(
        {
            "trust_level": "noofy_verified",
            "signatures": [
                {
                    "key_id": "noofy-ed25519-2026",
                    "algorithm": "ed25519",
                    "value": _ed25519_signature(private_key, package_payload),
                }
            ],
        },
        archive_bytes=archive_bytes,
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        trust_verifier=TrustVerifier(
            [
                TrustedSignatureKey(
                    key_id="noofy-ed25519-2026",
                    algorithm="ed25519",
                    public_key=public_key,
                    purpose=TrustSignaturePurpose.PACKAGE,
                )
            ]
        ),
    )

    package = store.preview_archive(signed, original_filename="ed25519-verified.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.identity is not None
    assert package.identity.trust_level == "noofy_verified"
    trust_verification = package.import_metadata.developer_details["trust_verification"]
    assert trust_verification["status"] == "verified"
    assert trust_verification["algorithm"] == "ed25519"


def test_import_store_accepts_registry_locked_with_ed25519_registry_metadata(
    tmp_path: Path,
) -> None:
    private_key, public_key = _ed25519_keypair()
    snapshot_hash = "sha256:" + "b" * 64
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "registry_locked"},
        archive_bytes=archive_bytes,
    )
    package_payload = NoofyArchiveImporter(unsigned).trust_payload()
    metadata_payload = registry_signature_payload(
        package_payload=package_payload,
        registry_id="noofy-community-registry",
        snapshot_hash=snapshot_hash,
    )
    signed = _archive_bytes_with_package_update(
        {
            "trust_level": "registry_locked",
            "signed_registry_metadata": {
                "registry_id": "noofy-community-registry",
                "snapshot_hash": snapshot_hash,
                "key_id": "registry-ed25519-2026",
                "algorithm": "ed25519",
                "signature": _ed25519_signature(private_key, metadata_payload),
            },
        },
        archive_bytes=archive_bytes,
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        trust_verifier=TrustVerifier(
            [
                TrustedSignatureKey(
                    key_id="registry-ed25519-2026",
                    algorithm="ed25519",
                    public_key=public_key,
                    purpose=TrustSignaturePurpose.REGISTRY,
                )
            ]
        ),
    )

    package = store.preview_archive(signed, original_filename="registry-ed25519.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.identity is not None
    assert package.identity.trust_level == "registry_locked"
    trust_verification = package.import_metadata.developer_details["trust_verification"]
    assert trust_verification["status"] == "verified"
    assert trust_verification["evidence_type"] == "signed_registry_metadata"


def test_import_store_rejects_ed25519_signature_after_payload_tampering(
    tmp_path: Path,
) -> None:
    private_key, public_key = _ed25519_keypair()
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "noofy_verified"},
        archive_bytes=archive_bytes,
    )
    package_payload = NoofyArchiveImporter(unsigned).trust_payload()
    tampered = _archive_bytes_with_package_update(
        {
            "trust_level": "noofy_verified",
            "export_report": {"tampered_after_signing": True},
            "signatures": [
                {
                    "key_id": "noofy-ed25519-2026",
                    "algorithm": "ed25519",
                    "value": _ed25519_signature(private_key, package_payload),
                }
            ],
        },
        archive_bytes=archive_bytes,
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        trust_verifier=TrustVerifier(
            [
                TrustedSignatureKey(
                    key_id="noofy-ed25519-2026",
                    algorithm="ed25519",
                    public_key=public_key,
                    purpose=TrustSignaturePurpose.PACKAGE,
                )
            ]
        ),
    )

    package = store.preview_archive(tampered, original_filename="tampered.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "unsupported"
    assert package.identity is not None
    assert package.identity.trust_level == "unsupported"
    assert (
        package.import_metadata.developer_details["trust_verification"]["status"]
        == "invalid_signature"
    )


def test_import_store_rejects_revoked_expired_and_policy_mismatched_ed25519_keys(
    tmp_path: Path,
) -> None:
    private_key, public_key = _ed25519_keypair()
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "noofy_verified"},
        archive_bytes=archive_bytes,
    )
    package_payload = NoofyArchiveImporter(unsigned).trust_payload()
    now = datetime(2026, 5, 4, tzinfo=UTC)

    for key_update, expected_status in (
        ({"revoked": True}, "revoked_key"),
        ({"expires_at": now - timedelta(seconds=1)}, "expired_key"),
        ({"not_before": now + timedelta(days=1)}, "key_not_yet_valid"),
        ({"policy_versions": ["phase6-future-only"]}, "policy_version_mismatch"),
    ):
        signed = _archive_bytes_with_package_update(
            {
                "trust_level": "noofy_verified",
                "signatures": [
                    {
                        "key_id": "noofy-ed25519-2026",
                        "algorithm": "ed25519",
                        "value": _ed25519_signature(private_key, package_payload),
                    }
                ],
            },
            archive_bytes=archive_bytes,
        )
        store = ImportedWorkflowPackageStore(
            tmp_path / f"packages-{expected_status}",
            log_store=LogStore(),
            trust_verifier=TrustVerifier(
                [
                    TrustedSignatureKey(
                        key_id="noofy-ed25519-2026",
                        algorithm="ed25519",
                        public_key=public_key,
                        purpose=TrustSignaturePurpose.PACKAGE,
                        **key_update,
                    )
                ],
                current_time=now,
            ),
        )

        package = store.preview_archive(
            signed, original_filename=f"{expected_status}.noofy"
        )

        assert package.import_metadata is not None
        assert package.import_metadata.status == "unsupported"
        assert (
            package.import_metadata.developer_details["trust_verification"]["status"]
            == expected_status
        )


def test_import_store_rejects_registry_locked_when_signed_snapshot_does_not_match_metadata(
    tmp_path: Path,
) -> None:
    private_key, public_key = _ed25519_keypair()
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "registry_locked"},
        archive_bytes=archive_bytes,
    )
    package_payload = NoofyArchiveImporter(unsigned).trust_payload()
    signed_snapshot_payload = registry_signature_payload(
        package_payload=package_payload,
        registry_id="noofy-community-registry",
        snapshot_hash="sha256:" + "b" * 64,
    )
    mismatched = _archive_bytes_with_package_update(
        {
            "trust_level": "registry_locked",
            "signed_registry_metadata": {
                "registry_id": "noofy-community-registry",
                "snapshot_hash": "sha256:" + "c" * 64,
                "key_id": "registry-ed25519-2026",
                "algorithm": "ed25519",
                "signature": _ed25519_signature(private_key, signed_snapshot_payload),
            },
        },
        archive_bytes=archive_bytes,
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        trust_verifier=TrustVerifier(
            [
                TrustedSignatureKey(
                    key_id="registry-ed25519-2026",
                    algorithm="ed25519",
                    public_key=public_key,
                    purpose=TrustSignaturePurpose.REGISTRY,
                )
            ]
        ),
    )

    package = store.preview_archive(
        mismatched, original_filename="registry-mismatch.noofy"
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status == "unsupported"
    trust_verification = package.import_metadata.developer_details["trust_verification"]
    assert trust_verification["status"] == "invalid_signature"
    assert (
        trust_verification["developer_details"]["snapshot_hash"] == "sha256:" + "c" * 64
    )


def test_import_store_rejects_hmac_signature_without_development_policy(
    tmp_path: Path,
) -> None:
    secret = "test-noofy-verified-secret"
    archive_bytes = _small_archive_bytes()
    unsigned = _archive_bytes_with_package_update(
        {"trust_level": "noofy_verified"},
        archive_bytes=archive_bytes,
    )
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
        },
        archive_bytes=archive_bytes,
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

    package = store.preview_archive(
        signed, original_filename="hmac-without-dev-policy.noofy"
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status == "unsupported"
    trust_verification = package.import_metadata.developer_details["trust_verification"]
    assert trust_verification["status"] == "unsupported_algorithm"
    assert (
        trust_verification["developer_details"]["reason"] == "development_hmac_disabled"
    )


@pytest.mark.anyio
async def test_signed_marketplace_archives_import_and_prepare_through_policy_gate(
    tmp_path: Path,
) -> None:
    for trust_level, evidence_type in (
        ("noofy_verified", "package_signature"),
        ("registry_locked", "signed_registry_metadata"),
    ):
        private_key, public_key = _ed25519_keypair()
        unsigned = _archive_bytes_with_package_update(
            {"trust_level": trust_level},
            archive_bytes=_test_workflow_archive_bytes("core_empty_image_smoke.noofy"),
        )
        package_payload = NoofyArchiveImporter(unsigned).trust_payload()
        if trust_level == "noofy_verified":
            signed = _archive_bytes_with_package_update(
                {
                    "trust_level": trust_level,
                    "signatures": [
                        {
                            "key_id": f"{trust_level}-ed25519-2026",
                            "algorithm": "ed25519",
                            "value": _ed25519_signature(private_key, package_payload),
                        }
                    ],
                },
                archive_bytes=_test_workflow_archive_bytes(
                    "core_empty_image_smoke.noofy"
                ),
            )
            purpose = TrustSignaturePurpose.PACKAGE
        else:
            snapshot_hash = "sha256:" + "d" * 64
            registry_payload = registry_signature_payload(
                package_payload=package_payload,
                registry_id="noofy-community-registry",
                snapshot_hash=snapshot_hash,
            )
            signed = _archive_bytes_with_package_update(
                {
                    "trust_level": trust_level,
                    "signed_registry_metadata": {
                        "registry_id": "noofy-community-registry",
                        "snapshot_hash": snapshot_hash,
                        "key_id": f"{trust_level}-ed25519-2026",
                        "algorithm": "ed25519",
                        "signature": _ed25519_signature(private_key, registry_payload),
                    },
                },
                archive_bytes=_test_workflow_archive_bytes(
                    "core_empty_image_smoke.noofy"
                ),
            )
            purpose = TrustSignaturePurpose.REGISTRY

        store_dir = tmp_path / trust_level / "packages"
        store = ImportedWorkflowPackageStore(
            store_dir,
            log_store=LogStore(),
            trust_verifier=TrustVerifier(
                [
                    TrustedSignatureKey(
                        key_id=f"{trust_level}-ed25519-2026",
                        algorithm="ed25519",
                        public_key=public_key,
                        purpose=purpose,
                    )
                ]
            ),
        )

        package = store.import_archive(signed, original_filename=f"{trust_level}.noofy")
        capsule = CapsuleLockLoader(
            Path("missing-bundled"), imported_packages_dir=store_dir
        ).get_capsule_lock(package.metadata.id)
        installer = _installer_for_imported_prepare(tmp_path / trust_level / "runtime")

        state = await installer.prepare(capsule)

        assert package.identity is not None
        assert package.identity.trust_level == trust_level
        assert package.import_metadata is not None
        assert (
            package.import_metadata.developer_details["trust_verification"][
                "evidence_type"
            ]
            == evidence_type
        )
        assert capsule.source_policy is not None
        assert capsule.source_policy.automatic_preparation_allowed is True
        assert state.status is InstallStatus.READY


def test_trust_keyring_loader_uses_file_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    keyring_path = tmp_path / "trusted-keys.json"
    keyring_path.write_text(
        TrustKeyring(
            allow_development_hmac=True,
            keys=[
                TrustedSignatureKey(
                    key_id="noofy-test-key",
                    secret="local-secret",
                    purpose=TrustSignaturePurpose.PACKAGE,
                )
            ],
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    verifier = load_trust_verifier(keyring_path)
    payload = verifier.policy_payload()

    assert payload["trusted_key_count"] == 1
    assert payload["development_hmac_allowed"] is True
    assert payload["trusted_keys"] == [
        {
            "key_id": "noofy-test-key",
            "algorithm": "hmac-sha256",
            "purpose": "package",
            "revoked": False,
            "not_before": None,
            "expires_at": None,
            "policy_versions": [],
        }
    ]
    assert "local-secret" not in json.dumps(payload)


def test_malformed_trust_keyring_logs_warning_and_disables_trusted_claims(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    keyring_path = tmp_path / "trusted-keys.json"
    keyring_path.write_text("{not-json", encoding="utf-8")

    verifier = load_trust_verifier(keyring_path, log_store=log_store)

    assert verifier.policy_payload()["trusted_key_count"] == 0
    latest = log_store.list_events().events[-1]
    assert latest.message == "Trust keyring could not be loaded"
    assert latest.details["path"] == str(keyring_path)


def test_import_store_persists_normalized_package_and_original_source_files(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    package = store.import_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )

    package_dir = (
        tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0"
    )
    assert (package_dir / "package.json").exists()
    assert (package_dir / "capsule.lock.json").exists()
    assert (package_dir / "exported-capsule.lock.json").exists()
    assert (package_dir / "source-archive.noofy").exists()
    assert (package_dir / "source-files" / "package.json").exists()
    assert (
        package_dir
        / "source-files"
        / "custom_nodes"
        / "comfyui-kjnodes"
        / "requirements.txt"
    ).exists()
    import_report = json.loads(
        (package_dir / "import-report.json").read_text(encoding="utf-8")
    )
    assert (
        import_report["runtime_resolution"]["selection_stage"] == "import_time_phase5c"
    )
    assert import_report["runtime_resolution"]["runtime_profile_variant_id"]

    loader = WorkflowPackageLoader(
        Path("missing-bundled"), imported_packages_dir=tmp_path / "packages"
    )
    loaded = loader.get_package(package.metadata.id)
    assert loaded.metadata.name == "controlnet_two_model_workflow"
    assert loaded.custom_nodes[0].included is True
    assert loaded.required_models[0].checksum is not None
    assert loaded.import_metadata is not None
    assert loaded.import_metadata.status == "needs_input_setup"
    assert log_store.list_events().events[-1].message == "Imported workflow package"

    capsule = CapsuleLockLoader(
        Path("missing-bundled"), imported_packages_dir=tmp_path / "packages"
    ).get_capsule_lock(package.metadata.id)
    assert capsule.workflow.package_id == "controlnet_two_model_workflow"
    assert len(capsule.custom_nodes) == 5
    assert capsule.runtime.runtime_profile_manifest_hash.startswith("sha256:")


def test_import_store_rejects_silent_replacement_of_existing_package(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    package = store.import_archive(_archive_bytes(), original_filename="first.noofy")
    package_dir = store.package_dir(package)
    original_archive = (package_dir / "source-archive.noofy").read_bytes()

    with pytest.raises(NoofyImportError, match="already exists"):
        store.import_archive(_archive_bytes(), original_filename="second.noofy")

    assert (package_dir / "source-archive.noofy").read_bytes() == original_archive


def test_import_store_replaces_existing_package_only_when_explicit(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    package = store.import_archive(_archive_bytes(), original_filename="first.noofy")
    package_dir = store.package_dir(package)
    (package_dir / "local-only.txt").write_text("stale", encoding="utf-8")

    replaced = store.import_archive(
        _archive_bytes(),
        original_filename="replacement.noofy",
        duplicate_action="replace",
    )

    assert replaced.metadata.id == package.metadata.id
    assert package_dir.exists()
    assert not (package_dir / "local-only.txt").exists()
    assert json.loads((package_dir / "import-report.json").read_text(encoding="utf-8"))["original_filename"] == "replacement.noofy"


def test_import_store_imports_duplicate_as_honest_local_copy(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    original = store.import_archive(_archive_bytes(), original_filename="first.noofy")
    copied = store.import_archive(
        _archive_bytes(),
        original_filename="copy.noofy",
        duplicate_action="copy",
    )

    assert copied.metadata.id != original.metadata.id
    assert copied.metadata.name.endswith(" Copy")
    assert copied.identity is not None
    assert copied.identity.publisher_id == "local"
    assert copied.identity.trust_level == "quarantined_community"
    assert copied.identity.signature is None
    assert copied.identity.signatures == []
    assert copied.identity.signed_registry_metadata is None
    assert copied.source_policy is not None
    assert copied.source_policy.trust_level == "quarantined_community"
    copy_report = json.loads((store.package_dir(copied) / "import-report.json").read_text(encoding="utf-8"))
    assert copy_report["trust_verification"] == {}
    assert store.package_dir(original).exists()
    assert store.package_dir(copied).exists()


def test_import_store_allows_macos_intel_dashboard_import_without_capsule_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_runtime_profile_module, "current_os_name", lambda: "darwin")
    monkeypatch.setattr(import_runtime_profile_module, "current_architecture", lambda: "x64")
    monkeypatch.setattr(importer_module, "current_os_name", lambda: "darwin")
    monkeypatch.setattr(importer_module, "current_architecture", lambda: "x64")
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    package = store.import_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )

    package_dir = (
        tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0"
    )
    assert (package_dir / "package.json").exists()
    assert not (package_dir / "capsule.lock.json").exists()
    import_report = json.loads(
        (package_dir / "import-report.json").read_text(encoding="utf-8")
    )
    assert import_report["runtime_resolution"] == {
        "architecture": "x64",
        "os": "darwin",
        "reason": "unsupported_local_runtime_platform",
        "selection_stage": "unavailable",
    }
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert log_store.list_events().events[-2].message == (
        "Capsule lock unavailable — no runtime profile for this platform"
    )


def test_import_store_does_not_hide_runtime_catalog_selection_bugs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer_module, "current_os_name", lambda: "darwin")
    monkeypatch.setattr(importer_module, "current_architecture", lambda: "x64")

    def fail_capsule_lock(package):
        cause = import_runtime_profile_module.RuntimeProfileSelectionError(
            "Runtime profile catalog is empty."
        )
        raise importer_module.ImportCapsuleLockError(str(cause)) from cause

    monkeypatch.setattr(
        importer_module,
        "build_imported_package_capsule_lock",
        fail_capsule_lock,
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())

    with pytest.raises(NoofyImportError, match="Runtime profile catalog is empty"):
        store.import_archive(
            _archive_bytes(),
            original_filename="exported-workflow-for-testing.noofy",
        )


@pytest.mark.anyio
async def test_imported_workflow_without_capsule_lock_cannot_validate_or_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(import_runtime_profile_module, "current_os_name", lambda: "darwin")
    monkeypatch.setattr(import_runtime_profile_module, "current_architecture", lambda: "x64")
    monkeypatch.setattr(importer_module, "current_os_name", lambda: "darwin")
    monkeypatch.setattr(importer_module, "current_architecture", lambda: "x64")
    log_store = LogStore()
    packages_dir = tmp_path / "packages"
    store = ImportedWorkflowPackageStore(packages_dir, log_store=log_store)
    package = store.import_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )
    supervisor = RunnerSupervisor()
    supervisor.register_core_runner(
        RunnerDescriptor(
            runner_id=CORE_RUNNER_ID,
            kind=RunnerKind.CORE_COMFYUI,
            base_url="http://127.0.0.1:8188",
            fingerprint=CORE_RUNNER_FINGERPRINT,
            status=RunnerStatus.READY,
        ),
        StubAdapter(),
    )
    service = EngineService(
        workflow_loader=WorkflowPackageLoader(
            Path("missing-bundled"),
            imported_packages_dir=packages_dir,
        ),
        workflow_validator=WorkflowPackageValidator(),
        runner_supervisor=supervisor,
        runtime_manager=StubRuntimeManager(),
        log_store=log_store,
        capsule_loader=CapsuleLockLoader(
            Path("missing-bundled"),
            imported_packages_dir=packages_dir,
        ),
        capsule_installer=_installer_for_imported_prepare(tmp_path / "runtime"),
    )

    validation = await service.validate_workflow(package.metadata.id)
    run_result = await service.run_workflow(package.metadata.id, {}, {})
    status = service.workflow_status(package.metadata.id)

    assert validation.valid is False
    assert "could not resolve a supported managed runtime profile" in validation.errors[0]
    assert run_result.valid is False
    assert "could not resolve a supported managed runtime profile" in run_result.errors[0]
    assert status["can_prepare"] is False
    assert status["install"]["status"] == "unsupported"


def test_import_store_blocks_non_bundled_community_custom_node_without_opt_in(
    tmp_path: Path,
) -> None:
    source_archive = _source_archive_bytes({"node.py": "NODE_CLASS_MAPPINGS = {}\n"})
    archive = _small_archive_with_custom_node(
        {
            "source": "https://example.test/comfyui-jps/archive/pinned.zip",
            "source_ref": "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21",
            "source_content_hash": f"sha256:{hashlib.sha256(source_archive).hexdigest()}",
        }
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())

    package = store.import_archive(archive, original_filename="community.noofy")

    assert package.import_metadata is not None
    assert package.import_metadata.status == "blocked_by_policy"
    assert (
        package.import_metadata.developer_details["source_resolution"]["reason"]
        == "community_opt_in_required"
    )
    assert package.identity is not None
    assert package.identity.trust_level == "unsupported"
    assert package.source_policy is not None
    assert package.source_policy.policy_status == "blocked_by_policy"
    assert package.source_policy.automatic_preparation_allowed is False
    assert package.source_policy.community_preparation_opted_in is False
    package_dir = store.package_dir(package)
    import_report = json.loads(
        (package_dir / "import-report.json").read_text(encoding="utf-8")
    )
    assert import_report["source_resolution"]["status"] == "blocked_by_policy"
    assert import_report["source_policy"]["policy_status"] == "blocked_by_policy"
    capsule = CapsuleLockLoader(
        Path("missing-bundled"), imported_packages_dir=tmp_path / "packages"
    ).get_capsule_lock(package.metadata.id)
    assert capsule.workflow.trust_level == "unsupported"
    assert capsule.trust.level.value == "unsupported"
    assert capsule.source_policy is not None
    assert capsule.source_policy.automatic_preparation_allowed is False


def test_import_store_resolves_opted_in_non_bundled_custom_node_to_cached_lock(
    tmp_path: Path,
) -> None:
    source_archive = _source_archive_bytes(
        {
            "repo-root/node.py": "NODE_CLASS_MAPPINGS = {}\n",
            "repo-root/requirements.txt": "numpy==1.26.4\n",
            "other-root/ignored.py": "ignored\n",
        }
    )
    source_digest = hashlib.sha256(source_archive).hexdigest()
    fetcher = FakeSourceFetcher(source_archive)
    archive = _small_archive_with_custom_node(
        {
            "source": "https://example.test/comfyui-jps/archive/pinned.zip",
            "source_ref": "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21",
            "source_content_hash": f"sha256:{source_digest}",
            "source_archive_subdir": "repo-root",
        }
    )
    source_cache_dir = tmp_path / "custom-node-cache"
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=log_store,
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=log_store,
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=source_cache_dir,
            fetcher=fetcher,
            log_store=log_store,
        ),
    )

    package = store.import_archive(
        archive,
        original_filename="community.noofy",
        allow_unverified_community_preparation=True,
    )

    resolved_node = next(
        node for node in package.custom_nodes if node.id == "comfyui_jps-nodes"
    )
    assert fetcher.urls == ["https://example.test/comfyui-jps/archive/pinned.zip"]
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert (
        package.import_metadata.developer_details["source_resolution"]["status"]
        == "resolved"
    )
    assert package.source_policy is not None
    assert package.source_policy.policy_status == "active"
    assert package.source_policy.automatic_preparation_allowed is True
    assert package.source_policy.community_preparation_opted_in is True
    assert resolved_node.included is True
    assert resolved_node.source_cache_ref == f"{source_digest}/source"
    assert resolved_node.source_ref == "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21"
    assert resolved_node.source_content_hash == f"sha256:{source_digest}"
    assert resolved_node.source_archive_subdir == "repo-root"

    package_dir = store.package_dir(package)
    capsule = CapsuleLockLoader(
        Path("missing-bundled"), imported_packages_dir=tmp_path / "packages"
    ).get_capsule_lock(package.metadata.id)
    cached_lock = next(
        node for node in capsule.custom_nodes if node.package_id == "comfyui_jps-nodes"
    )
    assert capsule.source_policy is not None
    assert capsule.source_policy.automatic_preparation_allowed is True
    assert cached_lock.source_cache_ref == f"{source_digest}/source"
    assert cached_lock.source_ref == "9f9118795d083b8eb5bb7bf9bfa0694f3f332a21"
    assert cached_lock.source_content_hash == f"sha256:{source_digest}"

    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(
            tmp_path / "runner-workspaces"
        ),
        runtime_profile_catalog=load_runtime_profile_catalog(
            Path("app/runtime/profile_catalog.json")
        ),
        custom_node_materializer=CustomNodeWorkspaceMaterializer(),
        custom_node_source_files_dir=package_dir / "source-files",
        custom_node_source_cache_dir=source_cache_dir,
        log_store=LogStore(),
    )
    prepared = preparer.prepare(capsule)
    cached_node_path = (
        prepared.runner_workspace_path
        / "custom_nodes"
        / "comfyui_jps-nodes"
        / "node.py"
    )
    assert cached_node_path.exists()
    assert "NODE_CLASS_MAPPINGS" in cached_node_path.read_text(encoding="utf-8")
    manifest = json.loads(
        (
            prepared.runner_workspace_path / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME
        ).read_text(encoding="utf-8")
    )
    cached_entry = next(
        entry
        for entry in manifest["entries"]
        if entry["custom_node_package_id"] == "comfyui_jps-nodes"
    )
    assert cached_entry["source_kind"] == "noofy_cached_archive"


def test_import_runtime_profile_prefers_linux_cuda_when_nvidia_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    monkeypatch.setattr(import_runtime_profile_module, "current_os_name", lambda: "linux")
    monkeypatch.setattr(import_runtime_profile_module, "current_architecture", lambda: "x64")
    monkeypatch.setattr(import_runtime_profile_module, "has_nvidia_gpu", lambda: True)

    _, variant = importer_module._select_import_runtime_profile(catalog.profiles)

    assert variant.runtime_profile_variant_id == "linux-x64-cuda130"


def test_import_runtime_profile_falls_back_to_linux_cpu_without_nvidia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    monkeypatch.setattr(import_runtime_profile_module, "current_os_name", lambda: "linux")
    monkeypatch.setattr(import_runtime_profile_module, "current_architecture", lambda: "x64")
    monkeypatch.setattr(import_runtime_profile_module, "has_nvidia_gpu", lambda: False)

    _, variant = importer_module._select_import_runtime_profile(catalog.profiles)

    assert variant.runtime_profile_variant_id == "linux-x64-cpu"


def test_import_runtime_profile_rejects_macos_intel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    monkeypatch.setattr(import_runtime_profile_module, "current_os_name", lambda: "darwin")
    monkeypatch.setattr(import_runtime_profile_module, "current_architecture", lambda: "x64")

    with pytest.raises(NoofyImportError, match="darwin/x64"):
        importer_module._select_import_runtime_profile(catalog.profiles)


def test_imported_real_archive_can_materialize_custom_node_workspace(
    tmp_path: Path,
) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    package = store.import_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
        allow_unverified_community_preparation=True,
    )
    package_dir = (
        tmp_path / "packages" / "unknown" / "controlnet_two_model_workflow" / "0.1.0"
    )
    capsule = CapsuleLockLoader(
        Path("missing-bundled"), imported_packages_dir=tmp_path / "packages"
    ).get_capsule_lock(package.metadata.id)
    preparer = RuntimeWorkspacePreparer(
        dependency_env_store=DependencyEnvManifestStore(tmp_path / "envs"),
        runner_workspace_store=RunnerWorkspaceManifestStore(
            tmp_path / "runner-workspaces"
        ),
        runtime_profile_catalog=load_runtime_profile_catalog(
            Path("app/runtime/profile_catalog.json")
        ),
        custom_node_materializer=CustomNodeWorkspaceMaterializer(),
        custom_node_source_files_dir=package_dir / "source-files",
        log_store=LogStore(),
    )

    prepared = preparer.prepare(capsule)

    assert (
        prepared.runner_workspace_path
        / "custom_nodes"
        / "comfyui-kjnodes"
        / "requirements.txt"
    ).exists()
    assert (
        prepared.runner_workspace_path
        / "custom_nodes"
        / "comfyui_controlnet_aux"
        / "requirements.txt"
    ).exists()
    manifest_path = (
        prepared.runner_workspace_path / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME
    )
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 5


def test_import_store_rejects_exported_launch_options(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())

    with pytest.raises(NoofyImportError, match="unsupported launch options"):
        store.import_archive(
            _archive_bytes_with_capsule_update(
                {"launch_options": {"vram_mode": "highvram"}},
                archive_bytes=_small_archive_bytes(),
            ),
            original_filename="launch-options.noofy",
        )

    assert not (
        tmp_path / "packages" / "unknown" / "core_empty_image_smoke" / "0.1.0"
    ).exists()


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
    (bundled_dir / "package.json").write_text(
        bundled_package.model_dump_json(), encoding="utf-8"
    )
    (imported_dir / "package.json").write_text(
        imported_package.model_dump_json(), encoding="utf-8"
    )

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


def test_import_store_logs_failed_import_without_persisting_package(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    with pytest.raises(NoofyImportError, match="unsafe path"):
        store.import_archive(_unsafe_zip_bytes(), original_filename="bad.noofy")

    assert not (tmp_path / "packages" / "_transactions").exists()
    latest = log_store.list_events().events[-1]
    assert latest.message == "Workflow import failed"
    assert latest.level == "warning"
    assert latest.details["original_filename"] == "bad.noofy"


def test_engine_service_imports_real_archive_and_exposes_normalized_package(
    tmp_path: Path,
) -> None:
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
        imported_package_store=ImportedWorkflowPackageStore(
            tmp_path / "packages", log_store=log_store
        ),
    )

    result = service.import_workflow_archive(
        _archive_bytes(),
        original_filename="exported-workflow-for-testing.noofy",
    )
    package = service.get_workflow_package(result["workflow_id"])
    summaries = service.list_workflows()

    assert result["status"] == "needs_input_setup"
    assert package["metadata"]["name"] == "controlnet_two_model_workflow"
    assert (
        package["unresolved_runtime_inputs"][0]["reason"]
        == "creator_local_image_not_bundled"
    )
    assert summaries[0]["status"] == "needs_input_setup"
    assert summaries[0]["status_label"] == "Needs input setup"
    assert summaries[0]["unresolved_input_count"] == 1
    assert summaries[0]["source_policy"]["trust_level"] == "quarantined_community"
    assert summaries[0]["source_policy"]["automatic_preparation_allowed"] is False


def _unsafe_zip_bytes() -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("../evil.txt", "bad")
    return payload.getvalue()


def _archive_bytes_with_capsule_update(
    update: dict, *, archive_bytes: bytes | None = None
) -> bytes:
    source = io.BytesIO(archive_bytes or _archive_bytes())
    payload = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        payload, "w"
    ) as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "capsule.lock.json":
                capsule = json.loads(contents.decode("utf-8"))
                capsule.update(update)
                contents = json.dumps(capsule).encode("utf-8")
            rewritten.writestr(info, contents)
    return payload.getvalue()


def _archive_bytes_with_package_update(
    update: dict, *, archive_bytes: bytes | None = None
) -> bytes:
    source = io.BytesIO(archive_bytes or _archive_bytes())
    payload = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        payload, "w"
    ) as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "package.json":
                package = json.loads(contents.decode("utf-8"))
                package.update(update)
                contents = json.dumps(package).encode("utf-8")
            rewritten.writestr(info, contents)
    return payload.getvalue()


def _small_archive_with_custom_node(update: dict) -> bytes:
    source = io.BytesIO(_small_archive_bytes())
    payload = io.BytesIO()
    custom_node = {
        "folder_name": "ComfyUI_JPS-Nodes",
        "has_install_py": False,
        "id": "comfyui_jps-nodes",
        "included": False,
        "node_types": ["Get Image Size (JPS)"],
        "requirements_files": [],
        "sha256_manifest": None,
        "source": "unknown",
        **update,
    }
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        payload, "w"
    ) as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "capsule.lock.json":
                capsule = json.loads(contents.decode("utf-8"))
                capsule["custom_nodes"] = [custom_node]
                contents = json.dumps(capsule).encode("utf-8")
            elif info.filename == "comfyui_graph.json":
                graph = json.loads(contents.decode("utf-8"))
                graph["100"] = {
                    "_meta": {"title": "Get Image Size (JPS)"},
                    "class_type": "Get Image Size (JPS)",
                    "inputs": {"image": ["1", 0]},
                }
                contents = json.dumps(graph).encode("utf-8")
            rewritten.writestr(info, contents)
    return payload.getvalue()


def _ed25519_keypair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=Encoding.Raw,
        format=PublicFormat.Raw,
    )
    return private_key, "ed25519:" + base64.b64encode(public_key).decode("ascii")


def _ed25519_signature(private_key: Ed25519PrivateKey, payload: dict) -> str:
    signature = private_key.sign(canonical_trust_payload_bytes(payload))
    return "ed25519:" + base64.b64encode(signature).decode("ascii")


def _installer_for_imported_prepare(root: Path) -> CapsuleInstaller:
    log_store = LogStore()

    async def downloader(url: str, dest: Path) -> int:
        raise AssertionError(
            f"model downloads are not expected for this fixture: {url}"
        )

    source_dir = root / "ComfyUI-source"
    source_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()

    async def smoke_test(capsule_lock, prepared_workspace) -> SmokeTestReport:
        return SmokeTestReport(
            dependency_env=SmokeStageResult(status=SmokeStageStatus.PASSED),
            custom_node_import=SmokeStageResult(status=SmokeStageStatus.SKIPPED),
            runner_health=SmokeStageResult(status=SmokeStageStatus.PASSED),
            workflow_execution=SmokeStageResult(status=SmokeStageStatus.PASSED),
        )

    return CapsuleInstaller(
        install_state_store=InstallStateStore(root / "install-state"),
        model_store=ModelStore(
            blobs_dir=root / "blobs",
            refs_dir=root / "refs",
            materialized_dir=root / "materialized",
            transactions_dir=root / "transactions",
            downloader=downloader,
            log_store=log_store,
        ),
        workspace_preparer=RuntimeWorkspacePreparer(
            dependency_env_store=DependencyEnvManifestStore(root / "envs"),
            runner_workspace_store=RunnerWorkspaceManifestStore(
                root / "runner-workspaces"
            ),
            comfyui_source_dir=source_dir,
            log_store=log_store,
        ),
        workspace_smoke_test=smoke_test,
        log_store=log_store,
    )


def _source_archive_bytes(files: dict[str, str]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for name, contents in files.items():
            archive.writestr(name, contents)
    return payload.getvalue()
