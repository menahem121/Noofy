from __future__ import annotations

import base64
import io
import hashlib
import json
import sys
import urllib.error
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
    NodeRegistryEntry,
    NodeRegistryResolver,
    NodeRegistrySource,
    NodeRegistrySourceKind,
    NodeTypeMappingCatalog,
    NoofyNodeRegistry,
)
from app.runtime.profiles import ActiveRuntimeProfileState, load_runtime_profile_catalog
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
    GitHubCustomNodeCandidate,
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


class FakeGitHubCustomNodeUrlResolver:
    def __init__(self, source: NodeRegistrySource) -> None:
        self.source = source
        self.calls: list[tuple[str, str]] = []

    def resolve(self, url: str, *, node_type: str) -> tuple[str, NodeRegistrySource]:
        self.calls.append((node_type, url))
        return f"github-{node_type.casefold()}", self.source


class FakeGitHubCustomNodeSearchResolver(FakeGitHubCustomNodeUrlResolver):
    def __init__(
        self,
        source: NodeRegistrySource,
        candidates: list[GitHubCustomNodeCandidate],
    ) -> None:
        super().__init__(source)
        self.candidates = candidates
        self.search_targets: list[importer_module.GitHubCustomNodeSearchTarget] = []

    def find_candidates(
        self,
        target: importer_module.GitHubCustomNodeSearchTarget,
    ) -> list[GitHubCustomNodeCandidate]:
        self.search_targets.append(target)
        return self.candidates


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


def test_imported_export2noofy_package_preserves_discovery_metadata(tmp_path: Path) -> None:
    archive = _archive_bytes_with_package_update(
        {
            "display_name": "Discovery Workflow",
            "description": "Top-level fallback should match.",
            "author": "Top Author",
            "website": "https://top.example.test",
            "category": "Txt2img",
            "tags": ["top"],
            "metadata": {
                "name": "Discovery Workflow",
                "display_name": "Discovery Workflow",
                "description": "Searchable package description.",
                "author": "Noofy Creator",
                "website": "https://example.test",
                "category": "img2vid",
                "tags": ["video", "starter"],
            },
        }
    )
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())

    package = store.import_archive(archive, original_filename="discovery.noofy")

    assert package.metadata.name == "Discovery Workflow"
    assert package.metadata.display_name == "Discovery Workflow"
    assert package.metadata.description == "Searchable package description."
    assert package.metadata.author == "Noofy Creator"
    assert package.metadata.website == "https://example.test"
    assert package.metadata.category == "img2vid"
    assert package.metadata.tags == ["video", "starter"]


def test_imported_export2noofy_package_preserves_sanitized_comfyui_widget_metadata() -> None:
    archive = _archive_bytes_with_package_update(
        {
            "comfyui_widget_metadata": {
                "schema_version": "future",
                "nodes": {
                    12: {
                        "inputs": {
                            "style": {
                                "options": ["cinematic", "illustration", "cinematic", {"bad": True}],
                                "display_name": " Rendering style ",
                            }
                        }
                    }
                },
            }
        }
    )

    package = NoofyArchiveImporter(archive).normalize()

    assert package.comfyui_widget_metadata == {
        "schema_version": "0.1.0",
        "nodes": {
            "12": {
                "inputs": {
                    "style": {
                        "options": ["cinematic", "illustration"],
                        "display_name": "Rendering style",
                    }
                }
            }
        },
    }


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


def test_raw_comfyui_api_json_import_extracts_required_models_and_source_urls(tmp_path: Path) -> None:
    graph = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "raw_model.safetensors",
                "ckpt_name_source_url": "https://huggingface.co/acme/raw/resolve/main/raw_model.safetensors",
            },
        },
        "9": {"class_type": "SaveImage", "inputs": {"images": ["4", 0]}},
    }

    package = ImportedWorkflowPackageStore(
        tmp_path / "packages", log_store=LogStore()
    ).preview_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="raw-api.json",
    )

    assert package.identity is not None
    assert package.identity.source == "raw_comfyui_json_import"
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert package.import_metadata.developer_details["raw_comfyui_json"]["source_format"] == "api_graph"
    assert package.required_models[0].folder == "checkpoints"
    assert package.required_models[0].filename == "raw_model.safetensors"
    assert package.required_models[0].source_urls == [
        "https://huggingface.co/acme/raw/resolve/main/raw_model.safetensors"
    ]


def test_raw_comfyui_ui_json_import_synthesizes_api_graph_and_models(tmp_path: Path) -> None:
    workflow = {
        "last_node_id": 9,
        "nodes": [
            {
                "id": 4,
                "type": "CheckpointLoaderSimple",
                "inputs": [],
                "widgets_values": ["ui_model.safetensors"],
            },
            {
                "id": 9,
                "type": "SaveImage",
                "inputs": [{"name": "images", "type": "IMAGE", "link": 12}],
                "widgets_values": ["Noofy"],
            },
        ],
        "links": [[12, 4, 0, 9, 0, "IMAGE"]],
    }

    package = ImportedWorkflowPackageStore(
        tmp_path / "packages", log_store=LogStore()
    ).preview_archive(
        json.dumps(workflow).encode("utf-8"),
        original_filename="ui-workflow.json",
    )

    assert package.comfyui_graph["4"] == {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "ui_model.safetensors"},
    }
    assert package.comfyui_graph["9"] == {
        "class_type": "SaveImage",
        "inputs": {"images": ["4", 0], "filename_prefix": "Noofy"},
    }
    assert package.required_models[0].folder == "checkpoints"
    assert package.required_models[0].filename == "ui_model.safetensors"
    assert package.import_metadata is not None
    details = package.import_metadata.developer_details["raw_comfyui_json"]
    assert details["source_format"] == "ui_workflow"
    assert details["synthesized_api_graph"] is True


def test_raw_comfyui_json_import_persists_source_files_and_logs(tmp_path: Path) -> None:
    log_store = LogStore()
    graph = {
        "7": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": "ae.safetensors"},
        }
    }
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)

    package = store.import_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="raw-log.json",
    )

    package_dir = store.package_dir(package)
    assert (package_dir / "source-files" / "raw-comfyui-workflow.json").exists()
    assert json.loads((package_dir / "source-files" / "comfyui_graph.json").read_text()) == graph
    latest = log_store.list_events(limit=1).events[0]
    assert latest.message == "Imported workflow package"
    assert latest.details["raw_comfyui_json"]["source_format"] == "api_graph"


def test_imported_store_persists_discovered_widget_metadata_snapshot(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    package = store.import_archive(
        json.dumps(
            {
                "7": {
                    "class_type": "LoadAudio",
                    "inputs": {"audio": ""},
                }
            }
        ).encode("utf-8"),
        original_filename="raw-audio.json",
    )
    metadata = {
        "schema_version": "0.1.0",
        "nodes": {
            "7": {
                "inputs": {
                    "audio": {"input_type": "COMBO", "audio_upload": True}
                }
            }
        },
    }

    assert store.persist_comfyui_widget_metadata(package, metadata) is True
    assert store.persist_comfyui_widget_metadata(package, metadata) is False

    package_payload = json.loads(
        (store.package_dir(package) / "package.json").read_text(encoding="utf-8")
    )
    assert package_payload["comfyui_widget_metadata"] == metadata


def test_raw_comfyui_ui_json_import_ignores_unreferenced_definition_node_types(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    registry = NoofyNodeRegistry(
        registry_id="raw-json-test-registry",
        entries=[
            _custom_node_registry_entry("magic-pack", ["MagicSampler"]),
        ],
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=log_store,
        node_registry_resolver=NodeRegistryResolver(
            registry=registry,
            mappings=NodeTypeMappingCatalog(
                node_type_to_package_id={"MagicSampler": "magic-pack"}
            ),
            log_store=log_store,
        ),
    )
    workflow = {
        "last_node_id": 3,
        "nodes": [
            {"id": 1, "type": "MagicSampler", "inputs": [], "widgets_values": []},
            {
                "id": 3,
                "type": "SaveImage",
                "inputs": [],
                "widgets_values": ["Noofy"],
            },
        ],
        "links": [],
        "definitions": {
            "group": {
                "nodes": [
                    {
                        "id": 50,
                        "type": "HiddenDefinitionNode",
                        "inputs": [],
                        "widgets_values": [],
                    }
                ]
            }
        },
    }

    package = store.preview_archive(
        json.dumps(workflow).encode("utf-8"),
        original_filename="custom-ui.json",
    )

    assert package.custom_nodes == []
    assert package.import_metadata is not None
    raw_details = package.import_metadata.developer_details["raw_comfyui_json"]
    assert raw_details["api_node_types"] == ["MagicSampler", "SaveImage"]
    assert raw_details["ui_node_types"] == [
        "HiddenDefinitionNode",
        "MagicSampler",
        "SaveImage",
    ]
    assert raw_details["executable_ui_node_types"] == []
    assert raw_details["custom_node_detection"] == {
        "status": "engine_verification_pending",
        "executable_node_types": ["MagicSampler", "SaveImage"],
    }


def test_raw_comfyui_ui_json_import_requires_referenced_definition_node_types(
    tmp_path: Path,
) -> None:
    log_store = LogStore()
    registry = NoofyNodeRegistry(
        registry_id="raw-json-test-registry",
        entries=[
            _custom_node_registry_entry("hidden-pack", ["HiddenDefinitionNode"]),
        ],
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=log_store,
        node_registry_resolver=NodeRegistryResolver(
            registry=registry,
            log_store=log_store,
        ),
    )
    workflow = {
        "last_node_id": 3,
        "nodes": [
            {
                "id": 1,
                "type": "workflow/subgraph-a",
                "inputs": [],
                "widgets_values": [],
            },
            {
                "id": 3,
                "type": "SaveImage",
                "inputs": [],
                "widgets_values": ["Noofy"],
            },
        ],
        "links": [],
        "definitions": {
            "subgraph-a": {
                "nodes": [
                    {
                        "id": 50,
                        "type": "HiddenDefinitionNode",
                        "inputs": [],
                        "widgets_values": [],
                    }
                ]
            }
        },
    }

    package = store.preview_archive(
        json.dumps(workflow).encode("utf-8"),
        original_filename="custom-ui.json",
    )

    assert package.custom_nodes == []
    assert package.import_metadata is not None
    raw_details = package.import_metadata.developer_details["raw_comfyui_json"]
    assert raw_details["api_node_types"] == ["SaveImage"]
    assert raw_details["executable_ui_node_types"] == ["HiddenDefinitionNode"]
    assert raw_details["custom_node_detection"] == {
        "status": "engine_verification_pending",
        "executable_node_types": ["HiddenDefinitionNode", "SaveImage"],
    }


def test_raw_comfyui_json_unknown_node_is_stored_for_engine_verification(
    tmp_path: Path,
) -> None:
    runtime_catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        runtime_profile_catalog_provider=lambda: runtime_catalog,
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
    )
    graph = {"1": {"class_type": "CrossProfileCoreNode", "inputs": {}}}

    package = store.preview_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="cross-profile.json",
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status != "missing_custom_nodes"
    assert package.custom_nodes == []
    raw_details = package.import_metadata.developer_details["raw_comfyui_json"]
    assert raw_details["custom_node_detection"] == {
        "status": "engine_verification_pending",
        "executable_node_types": ["CrossProfileCoreNode"],
    }


def test_raw_comfyui_json_import_resolves_custom_node_source_from_exact_node_type(
    tmp_path: Path,
) -> None:
    source_archive = _source_archive_bytes({"node.py": "NODE_CLASS_MAPPINGS = {}\n"})
    source_digest = hashlib.sha256(source_archive).hexdigest()
    fetcher = FakeSourceFetcher(source_archive)
    log_store = LogStore()
    registry = NoofyNodeRegistry(
        registry_id="raw-json-test-registry",
        entries=[
            _custom_node_registry_entry(
                "magic-pack",
                ["MagicSampler"],
                source_content_hash=f"sha256:{source_digest}",
            )
        ],
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=log_store,
        node_registry_resolver=NodeRegistryResolver(
            registry=registry,
            log_store=log_store,
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=fetcher,
            log_store=log_store,
        ),
    )
    graph = {"1": {"class_type": "MagicSampler", "inputs": {}}}

    preview = store.preview_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="custom-api.json",
        allow_unverified_community_preparation=True,
    )
    package = store.resolve_missing_engine_nodes_automatically(
        preview,
        missing_node_types=["MagicSampler"],
        allow_unverified_community_preparation=True,
    )

    record = package.custom_nodes[0]
    assert fetcher.urls == [
        "https://example.test/magic-pack/archive/pinned.zip",
    ]
    assert record.id == "magic-pack"
    assert record.included is True
    assert record.node_types == ["MagicSampler"]
    assert record.source_cache_ref == f"{source_digest}/source"
    assert package.import_metadata is not None
    assert (
        package.import_metadata.developer_details["source_resolution"]["status"]
        == "resolved"
    )


def test_raw_comfyui_json_import_marks_unknown_custom_node_types_missing(
    tmp_path: Path,
) -> None:
    class NoCandidateResolver:
        def find_candidates(self, target: importer_module.GitHubCustomNodeSearchTarget):
            return []

    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_github_resolver=NoCandidateResolver(),
    )
    graph = {"1": {"class_type": "NotInRegistryNode", "inputs": {}}}

    preview = store.preview_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="unknown-custom-node.json",
        allow_unverified_community_preparation=True,
    )
    package = store.resolve_missing_engine_nodes_automatically(
        preview,
        missing_node_types=["NotInRegistryNode"],
        allow_unverified_community_preparation=True,
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status == "engine_unrecognized_nodes"
    source_resolution = package.import_metadata.developer_details["source_resolution"]
    assert source_resolution["reason"] == "github_search_no_candidate"
    assert source_resolution["unresolved_node_types"] == ["NotInRegistryNode"]
    assert package.identity is not None
    assert package.identity.trust_level == "quarantined_community"


def test_raw_comfyui_json_import_marks_ambiguous_custom_node_types_missing(
    tmp_path: Path,
) -> None:
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url="https://codeload.github.com/example/first-pack/zip/" + "a" * 40,
        source_ref="a" * 40,
        source_content_hash="sha256:" + "a" * 64,
    )
    resolver = FakeGitHubCustomNodeSearchResolver(
        source,
        [
            _github_candidate(source, candidate_id="first", repo="first-pack"),
            _github_candidate(source, candidate_id="second", repo="second-pack"),
        ],
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(
                registry_id="ambiguous-test-registry",
                entries=[
                    _custom_node_registry_entry("first-pack", ["SharedNode"]),
                    _custom_node_registry_entry("second-pack", ["SharedNode"]),
                ],
            ),
            log_store=LogStore(),
        ),
        custom_node_github_resolver=resolver,
    )
    graph = {"1": {"class_type": "SharedNode", "inputs": {}}}

    preview = store.preview_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="ambiguous-custom-node.json",
        allow_unverified_community_preparation=True,
    )
    package = store.resolve_missing_engine_nodes_automatically(
        preview,
        missing_node_types=["SharedNode"],
        allow_unverified_community_preparation=True,
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status == "engine_unrecognized_nodes"
    source_resolution = package.import_metadata.developer_details["source_resolution"]
    assert source_resolution["reason"] == "github_search_ambiguous_candidate"
    assert source_resolution["unresolved_node_types"] == ["SharedNode"]


def test_raw_comfyui_json_import_resolves_unresolved_custom_node_from_github_url(
    tmp_path: Path,
) -> None:
    archive = _source_archive_bytes(
        {
            "node.py": (
                "raise RuntimeError('trusted backend must not import custom node')\n"
            ),
        }
    )
    archive_digest = hashlib.sha256(archive).hexdigest()
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url="https://codeload.github.com/example/custom-node/zip/" + "a" * 40,
        source_ref="a" * 40,
        source_content_hash=f"sha256:{archive_digest}",
    )
    fetcher = FakeSourceFetcher(archive)
    resolver = FakeGitHubCustomNodeUrlResolver(source)
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=fetcher,
            log_store=LogStore(),
        ),
        custom_node_github_resolver=resolver,
    )
    graph = {"1": {"class_type": "ManualNode", "inputs": {}}}
    package = store.preview_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="manual-node.json",
        allow_unverified_community_preparation=True,
    )
    package = store.with_engine_unrecognized_nodes(
        package,
        missing_node_types=["ManualNode"],
        reason="engine_unrecognized_node_types",
    )

    resolved = store.resolve_custom_nodes_from_github_urls(
        package,
        urls_by_node_type={"ManualNode": "https://github.com/example/custom-node"},
        allow_unverified_community_preparation=True,
    )

    assert resolver.calls == [
        ("ManualNode", "https://github.com/example/custom-node")
    ]
    assert fetcher.urls == [source.source_url]
    assert resolved.import_metadata is not None
    assert resolved.import_metadata.status == "needs_input_setup"
    assert resolved.import_metadata.developer_details["source_resolution"]["status"] == "resolved"
    record = resolved.custom_nodes[0]
    assert record.included is True
    assert record.node_types == ["ManualNode"]
    assert record.source_ref == "a" * 40
    assert record.source_content_hash == f"sha256:{archive_digest}"
    assert "node" not in sys.modules


def test_raw_comfyui_json_import_keeps_unresolved_github_failures_actionable(
    tmp_path: Path,
) -> None:
    class FailingResolver:
        def resolve(self, url: str, *, node_type: str) -> tuple[str, NodeRegistrySource]:
            raise RuntimeError("bad url")

    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_github_resolver=FailingResolver(),
    )
    graph = {"1": {"class_type": "ManualNode", "inputs": {}}}
    package = store.preview_archive(
        json.dumps(graph).encode("utf-8"),
        original_filename="manual-node.json",
        allow_unverified_community_preparation=True,
    )
    package = store.with_engine_unrecognized_nodes(
        package,
        missing_node_types=["ManualNode"],
        reason="engine_unrecognized_node_types",
    )

    resolved = store.resolve_custom_nodes_from_github_urls(
        package,
        urls_by_node_type={"ManualNode": "https://example.test/not-github"},
        allow_unverified_community_preparation=True,
    )

    assert resolved.import_metadata is not None
    assert resolved.import_metadata.status == "engine_unrecognized_nodes"
    details = resolved.import_metadata.developer_details["source_resolution"]
    assert details["reason"] == "user_github_url_resolution_failed"
    assert details["unresolved_node_types"] == ["ManualNode"]
    assert details["failed_custom_nodes"] == [
        {
            "node_type": "ManualNode",
            "url": "https://example.test/not-github",
            "error": "bad url",
        }
    ]


def test_raw_comfyui_json_import_auto_resolves_high_confidence_github_candidate(
    tmp_path: Path,
) -> None:
    commit_sha = "a" * 40
    archive = _source_archive_bytes(
        {
            f"comfyui-manual-{commit_sha}/node.py": (
                "NODE_CLASS_MAPPINGS = {'ManualNode': object}\n"
            )
        }
    )
    archive_digest = hashlib.sha256(archive).hexdigest()
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url=f"https://codeload.github.com/example/comfyui-manual/zip/{commit_sha}",
        source_ref=commit_sha,
        source_content_hash=f"sha256:{archive_digest}",
        archive_subdir=f"comfyui-manual-{commit_sha}",
        source_repo_url="https://github.com/example/comfyui-manual",
    )
    fetcher = FakeSourceFetcher(archive)
    resolver = FakeGitHubCustomNodeSearchResolver(
        source,
        [_github_candidate(source, repo="comfyui-manual", confidence="high")],
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=fetcher,
            log_store=LogStore(),
        ),
        custom_node_github_resolver=resolver,
    )

    preview = store.preview_archive(
        json.dumps({"1": {"class_type": "ManualNode", "inputs": {}}}).encode("utf-8"),
        original_filename="manual-node.json",
        allow_unverified_community_preparation=True,
    )
    package = store.resolve_missing_engine_nodes_automatically(
        preview,
        missing_node_types=["ManualNode"],
        allow_unverified_community_preparation=True,
    )

    assert resolver.search_targets
    assert fetcher.urls == [source.source_url]
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    record = package.custom_nodes[0]
    assert record.included is True
    assert record.resolution_method == "github_search_auto"
    assert record.source_ref == commit_sha
    assert record.source_repo_url == "https://github.com/example/comfyui-manual"


def test_raw_comfyui_json_import_auto_resolves_medium_github_candidate(
    tmp_path: Path,
) -> None:
    commit_sha = "b" * 40
    archive = _source_archive_bytes(
        {
            f"comfyui-manual-{commit_sha}/node.py": (
                "NODE_CLASS_MAPPINGS = {'ManualNode': object}\n"
            )
        }
    )
    archive_digest = hashlib.sha256(archive).hexdigest()
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url=f"https://codeload.github.com/example/comfyui-manual/zip/{commit_sha}",
        source_ref=commit_sha,
        source_content_hash=f"sha256:{archive_digest}",
        archive_subdir=f"comfyui-manual-{commit_sha}",
        source_repo_url="https://github.com/example/comfyui-manual",
    )
    fetcher = FakeSourceFetcher(archive)
    resolver = FakeGitHubCustomNodeSearchResolver(
        source,
        [
            _github_candidate(
                source,
                candidate_id="candidate-medium",
                repo="possible-manual-node",
                confidence="medium",
                evidence_score=4,
                name_match="weak",
            )
        ],
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=fetcher,
            log_store=LogStore(),
        ),
        custom_node_github_resolver=resolver,
    )
    preview = store.preview_archive(
        json.dumps({"1": {"class_type": "ManualNode", "inputs": {}}}).encode("utf-8"),
        original_filename="manual-node.json",
        allow_unverified_community_preparation=True,
    )
    package = store.resolve_missing_engine_nodes_automatically(
        preview,
        missing_node_types=["ManualNode"],
        allow_unverified_community_preparation=True,
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert fetcher.urls == [source.source_url]
    record = package.custom_nodes[0]
    assert record.included is True
    assert record.resolution_method == "github_search_auto"


def test_raw_comfyui_json_import_prefers_moss_tts_over_generic_comfyui_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_sha = "d" * 40
    correct_sha = "e" * 40
    wrong_archive = _source_archive_bytes(
        {
            f"avatar-graph-comfyui-{wrong_sha}/nodes.py": (
                "NODE_CLASS_MAPPINGS = {'AvatarGraph': object}\n"
            ),
            f"avatar-graph-comfyui-{wrong_sha}/README.md": (
                "ComfyUI custom nodes for avatar graphs.\n"
            ),
        }
    )
    correct_archive = _source_archive_bytes(
        {
            f"comfyui-moss-tts-{correct_sha}/nodes.py": (
                "NODE_CLASS_MAPPINGS = {'MossTTSModelLoader': object, "
                "'MossTTSGenerate': object}\n"
            ),
            f"comfyui-moss-tts-{correct_sha}/README.md": (
                "ComfyUI custom nodes for MOSS-TTS text to speech.\n"
            ),
        }
    )
    queries: list[str] = []

    def fake_search(query: str) -> list[dict[str, object]]:
        queries.append(query)
        return [
            _github_repo_payload(
                owner="avatechai",
                repo="avatar-graph-comfyui",
                stars=900,
            ),
            _github_repo_payload(
                owner="richservo",
                repo="comfyui-moss-tts",
                stars=4,
            ),
        ]

    def fake_commit(owner: str, repo: str, ref: str | None) -> str:
        return correct_sha if repo == "comfyui-moss-tts" else wrong_sha

    def fake_download(url: str, *, max_bytes: int = 512 * 1024 * 1024) -> bytes:
        return correct_archive if "comfyui-moss-tts" in url else wrong_archive

    monkeypatch.setattr(importer_module, "_github_search_repositories", fake_search)
    monkeypatch.setattr(importer_module, "_github_commit_sha", fake_commit)
    monkeypatch.setattr(importer_module, "_download_url_bytes", fake_download)

    fetcher = FakeSourceFetcher(correct_archive)
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=fetcher,
            log_store=LogStore(),
        ),
        custom_node_github_resolver=importer_module.DefaultGitHubCustomNodeUrlResolver(),
    )

    preview = store.preview_archive(
        json.dumps(_moss_tts_graph()).encode("utf-8"),
        original_filename="comfyui_graph.json",
        allow_unverified_community_preparation=True,
    )
    package = store.resolve_missing_engine_nodes_automatically(
        preview,
        missing_node_types=["MossTTSGenerate", "MossTTSModelLoader"],
        allow_unverified_community_preparation=True,
    )

    assert queries
    assert "comfyui-graph" not in " ".join(queries).casefold()
    assert any("comfyui-moss-tts" in query for query in queries)
    assert fetcher.urls == [
        f"https://codeload.github.com/richservo/comfyui-moss-tts/zip/{correct_sha}"
    ]
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    record = next(
        node for node in package.custom_nodes if node.id == "github-richservo-comfyui-moss-tts"
    )
    assert record.resolution_method == "github_search_auto"
    assert record.source_repo_url == "https://github.com/richservo/comfyui-moss-tts"


def test_raw_comfyui_json_import_does_not_recommend_generic_comfyui_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_sha = "f" * 40
    wrong_archive = _source_archive_bytes(
        {
            f"avatar-graph-comfyui-{wrong_sha}/nodes.py": (
                "NODE_CLASS_MAPPINGS = {'AvatarGraph': object}\n"
            ),
            f"avatar-graph-comfyui-{wrong_sha}/README.md": (
                "ComfyUI custom nodes for avatar graphs.\n"
            ),
        }
    )

    monkeypatch.setattr(
        importer_module,
        "_github_search_repositories",
        lambda query: [
            _github_repo_payload(
                owner="avatechai",
                repo="avatar-graph-comfyui",
                stars=900,
            )
        ],
    )
    monkeypatch.setattr(
        importer_module,
        "_github_commit_sha",
        lambda owner, repo, ref: wrong_sha,
    )
    monkeypatch.setattr(
        importer_module,
        "_download_url_bytes",
        lambda url, *, max_bytes=512 * 1024 * 1024: wrong_archive,
    )

    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=LogStore(),
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=FakeSourceFetcher(wrong_archive),
            log_store=LogStore(),
        ),
        custom_node_github_resolver=importer_module.DefaultGitHubCustomNodeUrlResolver(),
    )

    preview = store.preview_archive(
        json.dumps(_moss_tts_graph()).encode("utf-8"),
        original_filename="comfyui_graph.json",
        allow_unverified_community_preparation=True,
    )
    package = store.resolve_missing_engine_nodes_automatically(
        preview,
        missing_node_types=["MossTTSGenerate", "MossTTSModelLoader"],
        allow_unverified_community_preparation=True,
    )

    assert package.import_metadata is not None
    assert package.import_metadata.status == "engine_unrecognized_nodes"
    details = package.import_metadata.developer_details["source_resolution"]
    assert details["mode"] == "manual_url"
    assert details["candidate"]["repo"] == "avatar-graph-comfyui"
    assert details["candidate"]["confidence"] == "low"
    assert details["candidate"]["specific_match_score"] == 0


def test_github_commit_resolution_uses_atom_feed_when_api_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit_sha = "a" * 40
    requested_urls: list[str] = []
    atom_payload = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:github.com,2008:Grit::Commit/{commit_sha}</id>
  </entry>
</feed>
""".encode()

    class AtomResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, size: int = -1) -> bytes:
            return atom_payload[:size] if size >= 0 else atom_payload

    def fake_urlopen(request, timeout: int):
        url = request.full_url
        requested_urls.append(url)
        if "api.github.com" in url:
            raise urllib.error.HTTPError(url, 504, "Gateway Timeout", None, None)
        return AtomResponse()

    monkeypatch.setattr(importer_module.urllib.request, "urlopen", fake_urlopen)

    resolved = importer_module._github_commit_sha(
        "kijai",
        "ComfyUI-KJNodes",
        "main",
    )

    assert resolved == commit_sha
    assert requested_urls == [
        "https://api.github.com/repos/kijai/ComfyUI-KJNodes/commits/main",
        "https://github.com/kijai/ComfyUI-KJNodes/commits/main.atom",
    ]


def test_github_search_stops_after_verified_exact_popular_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit_sha = "b" * 40
    archive = _source_archive_bytes(
        {
            f"ComfyUI-KJNodes-{commit_sha}/nodes.py": (
                "NODE_CLASS_MAPPINGS = {'ColorMatchV2': object}\n"
            ),
            f"ComfyUI-KJNodes-{commit_sha}/README.md": (
                "ComfyUI custom nodes including ColorMatchV2.\n"
            ),
        }
    )
    commit_requests: list[tuple[str, str]] = []

    monkeypatch.setattr(
        importer_module,
        "_github_search_repositories",
        lambda query: [
            _github_repo_payload(
                owner="kijai",
                repo="ComfyUI-KJNodes",
                stars=2_700,
            ),
            _github_repo_payload(
                owner="fork-owner",
                repo="ComfyUI-KJNodes",
                stars=0,
            ),
        ],
    )

    def fake_commit(owner: str, repo: str, ref: str | None) -> str:
        commit_requests.append((owner, repo))
        return commit_sha

    monkeypatch.setattr(importer_module, "_github_commit_sha", fake_commit)
    monkeypatch.setattr(
        importer_module,
        "_download_url_bytes",
        lambda url, *, max_bytes=512 * 1024 * 1024: archive,
    )
    target = importer_module.GitHubCustomNodeSearchTarget(
        package_id="comfyui-kjnodes",
        names=("ComfyUI-KJNodes",),
        node_types=("ColorMatchV2",),
        node_titles=("ColorMatchV2",),
        family_terms=("color match",),
    )

    candidates = importer_module.DefaultGitHubCustomNodeUrlResolver().find_candidates(
        target
    )

    assert [candidate.html_url for candidate in candidates] == [
        "https://github.com/kijai/ComfyUI-KJNodes"
    ]
    assert candidates[0].confidence == "high"
    assert commit_requests == [("kijai", "ComfyUI-KJNodes")]


def test_noofy_importer_streams_source_file_extraction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    original_copy_stream_limited = importer_module.copy_stream_limited

    def track_stream_copy(source, destination, *, max_bytes: int, chunk_bytes=1024 * 1024):
        calls.append(max_bytes)
        return original_copy_stream_limited(
            source,
            destination,
            max_bytes=max_bytes,
            chunk_bytes=5,
        )

    monkeypatch.setattr(
        importer_module,
        "copy_stream_limited",
        track_stream_copy,
    )
    importer = NoofyArchiveImporter(_archive_bytes())
    target_dir = tmp_path / "source-files"

    importer.extract_source_files(target_dir)

    assert calls
    assert (target_dir / "custom_nodes").is_dir()


def test_noofy_importer_preserves_declared_non_image_output_kinds() -> None:
    archive = _archive_bytes_with_dashboard_update(
        {
            "version": "0.1.0",
            "status": "not_configured",
            "inputs": [],
            "outputs": [
                {"id": "audio-144", "label": "Audio Output", "node_id": "144", "type": "audio", "kind": "audio"},
                {"id": "video-144", "label": "Video Output", "node_id": "144", "type": "video", "kind": "video"},
                {"id": "3d-144", "label": "3D Output", "node_id": "144", "type": "3d", "kind": "3d"},
                {"id": "text-144", "label": "Text Output", "node_id": "144", "type": "text", "kind": "text"},
                {"id": "file-144", "label": "File Output", "node_id": "144", "type": "file", "kind": "file"},
            ],
            "sections": [],
        }
    )

    package = NoofyArchiveImporter(
        archive,
        original_filename="media-outputs.noofy",
    ).normalize()

    assert [output.kind for output in package.outputs] == ["audio", "video", "3d", "text", "file"]
    assert package.dashboard.status == "not_configured"


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


def test_noofy_importer_backfills_model_source_url_from_editable_workflow() -> None:
    source_url = (
        "https://huggingface.co/example/repo/resolve/main/"
        "upscale_models/upscale.safetensors"
    )
    archive = _archive_bytes_with_comfyui_workflow_update(
        {
            "nodes": [
                {
                    "id": 12,
                    "type": "UpscaleModelLoader",
                    "widgets_values": ["upscale.safetensors"],
                    "properties": {
                        "models": [
                            {
                                "directory": "upscale_models",
                                "name": "upscale.safetensors",
                                "url": source_url,
                            }
                        ]
                    },
                }
            ]
        },
        archive_bytes=_archive_bytes_with_capsule_update(
            {
                "models": [
                    {
                        "comfyui_folder": "upscale_models",
                        "filename": "upscale.safetensors",
                        "source_urls": [],
                        "sha256": "a" * 64,
                        "size_bytes": 123,
                        "verification_level": "sha256_size",
                    }
                ]
            },
            archive_bytes=_small_archive_bytes(),
        ),
    )

    package = NoofyArchiveImporter(archive).normalize()

    model = package.required_models[0]
    assert model.source_url == source_url
    assert model.source_urls == [source_url]


def test_noofy_importer_adds_missing_model_from_editable_workflow_metadata() -> None:
    source_url = (
        "https://huggingface.co/example/repo/resolve/main/"
        "background_removal/birefnet.safetensors"
    )
    archive = _archive_bytes_with_comfyui_workflow_update(
        {
            "nodes": [
                {
                    "id": 82,
                    "type": "LoadBackgroundRemovalModel",
                    "widgets_values": ["birefnet.safetensors"],
                    "properties": {
                        "models": [
                            {
                                "directory": "background_removal",
                                "name": "birefnet.safetensors",
                                "url": source_url,
                            }
                        ]
                    },
                }
            ]
        },
        archive_bytes=_archive_bytes_with_graph_update(
            {
                "88:85:82": {
                    "class_type": "LoadBackgroundRemovalModel",
                    "inputs": {"bg_removal_name": "birefnet.safetensors"},
                }
            },
            archive_bytes=_archive_bytes_with_capsule_update(
                {"models": []},
                archive_bytes=_small_archive_bytes(),
            ),
        ),
    )

    package = NoofyArchiveImporter(archive).normalize()

    model = package.required_models[0]
    assert model.folder == "background_removal"
    assert model.filename == "birefnet.safetensors"
    assert model.node_id == "88:85:82"
    assert model.input_name == "bg_removal_name"
    assert model.source_urls == [source_url]


def test_noofy_importer_adds_graph_only_model_without_source_for_provider_fallback() -> None:
    archive = _archive_bytes_with_graph_update(
        {
            "14": {
                "class_type": "CLIPLoader",
                "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors"},
            }
        },
        archive_bytes=_archive_bytes_with_capsule_update(
            {"models": []},
            archive_bytes=_small_archive_bytes(),
        ),
    )

    package = NoofyArchiveImporter(archive).normalize()

    model = package.required_models[0]
    assert model.folder == "text_encoders"
    assert model.filename == "qwen_2.5_vl_7b_fp8_scaled.safetensors"
    assert model.model_type == "text_encoder"
    assert model.node_id == "14"
    assert model.node_type == "CLIPLoader"
    assert model.input_name == "clip_name"
    assert model.source_urls == []
    assert model.verification_level == "filename_only"


def test_noofy_importer_prunes_stale_capsule_model_from_editable_workflow() -> None:
    archive = _archive_bytes_with_comfyui_workflow_update(
        {
            "nodes": [
                {
                    "id": 70,
                    "type": "UNETLoader",
                    "widgets_values": ["active.safetensors", "default"],
                    "properties": {
                        "models": [
                            {
                                "directory": "diffusion_models",
                                "name": "stale.safetensors",
                                "url": "https://example.test/stale.safetensors",
                            }
                        ]
                    },
                }
            ]
        },
        archive_bytes=_archive_bytes_with_graph_update(
            {
                "75:70": {
                    "class_type": "UNETLoader",
                    "inputs": {"unet_name": "active.safetensors"},
                }
            },
            archive_bytes=_archive_bytes_with_capsule_update(
                {
                    "models": [
                        {
                            "comfyui_folder": "diffusion_models",
                            "filename": "active.safetensors",
                            "sha256": "a" * 64,
                            "size_bytes": 123,
                        },
                        {
                            "comfyui_folder": "diffusion_models",
                            "filename": "stale.safetensors",
                            "source_urls": [
                                "https://example.test/stale.safetensors"
                            ],
                            "sha256": "b" * 64,
                            "size_bytes": 456,
                        },
                    ]
                },
                archive_bytes=_small_archive_bytes(),
            ),
        ),
    )

    package = NoofyArchiveImporter(archive).normalize()

    assert [(model.folder, model.filename) for model in package.required_models] == [
        ("diffusion_models", "active.safetensors")
    ]


def test_noofy_importer_does_not_duplicate_cliploader_text_encoder_from_capsule() -> None:
    archive = _archive_bytes_with_graph_update(
        {
            "22:2": {
                "class_type": "CLIPLoader",
                "inputs": {
                    "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                    "device": "default",
                    "type": "longcat_image",
                },
            }
        },
        archive_bytes=_archive_bytes_with_capsule_update(
            {
                "models": [
                    {
                        "comfyui_folder": "text_encoders",
                        "filename": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
                        "input_name": "clip_name",
                        "model_type": "text_encoder",
                        "node_id": "22:2",
                        "node_type": "CLIPLoader",
                        "sha256": "cb5636d852a0ea6a9075ab1bef496c0db7aef13c02350571e388aea959c5c0b4",
                        "size_bytes": 9384670680,
                    }
                ]
            },
            archive_bytes=_small_archive_bytes(),
        ),
    )

    package = NoofyArchiveImporter(archive).normalize()

    assert [
        (model.folder, model.filename, model.verification_level)
        for model in package.required_models
    ] == [
        (
            "text_encoders",
            "qwen_2.5_vl_7b_fp8_scaled.safetensors",
            "sha256_size",
        )
    ]


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


def test_import_store_persists_prepared_package_model_identity(tmp_path: Path) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    archive = _archive_bytes()
    prepared = store.preview_archive(archive)
    prepared.required_models[0].checksum = "sha256:" + ("a" * 64)
    prepared.required_models[0].size_bytes = 987654
    prepared.required_models[0].identity_verified_by_exporter = False

    imported = store.import_prepared_archive(archive, package=prepared)

    loaded = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    ).get_package(imported.metadata.id)
    assert loaded.required_models[0].checksum == "sha256:" + ("a" * 64)
    assert loaded.required_models[0].size_bytes == 987654
    assert loaded.required_models[0].identity_verified_by_exporter is False


def test_import_store_persists_updated_model_identity_and_capsule_lock(
    tmp_path: Path,
) -> None:
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=LogStore())
    imported = store.import_archive(_archive_bytes())
    updated_checksum = "sha256:" + ("b" * 64)
    target = imported.required_models[0]
    package_file = store.package_dir(imported) / "package.json"
    package_payload = json.loads(package_file.read_text(encoding="utf-8"))
    package_payload["future_metadata"] = {"preserve": True}
    package_file.write_text(json.dumps(package_payload), encoding="utf-8")
    for model in imported.required_models:
        if (model.folder, model.filename) == (target.folder, target.filename):
            model.checksum = updated_checksum
            model.size_bytes = 123456
            model.identity_verified_by_exporter = False

    store.persist_model_identities(imported)

    loaded = WorkflowPackageLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    ).get_package(imported.metadata.id)
    matching = [
        model
        for model in loaded.required_models
        if (model.folder, model.filename) == (target.folder, target.filename)
    ]
    assert {model.checksum for model in matching} == {updated_checksum}
    assert {model.size_bytes for model in matching} == {123456}
    capsule = CapsuleLockLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    ).get_capsule_lock(imported.metadata.id)
    locked = next(
        model
        for model in capsule.models
        if (model.comfyui_folder, model.filename) == (target.folder, target.filename)
    )
    assert locked.sha256 == updated_checksum
    assert locked.size_bytes == 123456
    persisted_payload = json.loads(package_file.read_text(encoding="utf-8"))
    assert persisted_payload["future_metadata"] == {"preserve": True}


def test_import_store_refreshes_stale_runtime_capsule_lock(tmp_path: Path) -> None:
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(tmp_path / "packages", log_store=log_store)
    imported = store.import_archive(_archive_bytes())
    capsule_file = store.package_dir(imported) / "capsule.lock.json"
    stale = json.loads(capsule_file.read_text(encoding="utf-8"))
    stale["runtime"]["runtime_profile_manifest_hash"] = "sha256:" + ("0" * 64)
    stale["runtime"]["runner_fingerprint"] = "sha256:" + ("1" * 64)
    capsule_file.write_text(json.dumps(stale), encoding="utf-8")

    refreshed = store.refresh_capsule_lock(imported)

    assert refreshed is not None
    assert refreshed.runtime.runtime_profile_manifest_hash != stale["runtime"][
        "runtime_profile_manifest_hash"
    ]
    assert refreshed.runtime.runner_fingerprint != stale["runtime"]["runner_fingerprint"]
    persisted = CapsuleLockLoader(
        Path("missing-bundled"),
        imported_packages_dir=tmp_path / "packages",
    ).get_capsule_lock(imported.metadata.id)
    assert persisted == refreshed
    assert log_store.list_events().events[-1].message == (
        "Refreshed imported workflow runtime capsule"
    )


def test_import_store_refreshes_capsule_for_active_managed_comfyui(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "bundled"
    bundled.mkdir()
    (bundled / "main.py").write_text("", encoding="utf-8")
    updated = tmp_path / "updated"
    updated.mkdir()
    (updated / "main.py").write_text("", encoding="utf-8")
    state = ActiveRuntimeProfileState(
        base_catalog=load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json")),
        source_dir=bundled,
    )
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=LogStore(),
        runtime_profile_catalog_provider=state.catalog,
    )
    imported = store.import_archive(_archive_bytes())
    original = store.refresh_capsule_lock(imported)
    assert original is not None

    state.activate(
        state.prepare_local_activation(
            comfyui_core_version="v9.9.9",
            comfyui_core_source_hash="sha256:" + ("9" * 64),
            source_reference="https://example.test/v9.9.9.zip",
            source_dir=updated,
        )
    )
    refreshed = store.refresh_capsule_lock(imported)

    assert refreshed is not None
    assert refreshed.engine.comfyui_version == "v9.9.9"
    assert refreshed.engine.core_source_hash == "sha256:" + ("9" * 64)
    assert refreshed.runtime.runtime_profile_manifest_hash != (
        original.runtime.runtime_profile_manifest_hash
    )
    assert refreshed.runtime.dependency_env_fingerprint != (
        original.runtime.dependency_env_fingerprint
    )
    assert refreshed.runtime.runner_fingerprint != original.runtime.runner_fingerprint
    assert refreshed.runtime.capsule_fingerprint != original.runtime.capsule_fingerprint


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


def test_import_store_recovers_missing_bundled_custom_node_source_from_registry(
    tmp_path: Path,
) -> None:
    source_archive = _source_archive_bytes(
        {"repo-root/node.py": "NODE_CLASS_MAPPINGS = {'Get Image Size (JPS)': object}\n"}
    )
    source_digest = hashlib.sha256(source_archive).hexdigest()
    fetcher = FakeSourceFetcher(source_archive)
    archive = _small_archive_with_custom_node(
        {
            "included": True,
            "source": "source-files/custom_nodes/ComfyUI_JPS-Nodes",
            "source_ref": None,
            "source_content_hash": None,
            "source_archive_subdir": None,
        }
    )
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url="https://example.test/comfyui-jps/archive/pinned.zip",
        source_ref="9f9118795d083b8eb5bb7bf9bfa0694f3f332a21",
        source_content_hash=f"sha256:{source_digest}",
        archive_subdir="repo-root",
    )
    registry = NoofyNodeRegistry(
        registry_id="test-registry",
        entries=[
            NodeRegistryEntry(
                package_id="comfyui_jps-nodes",
                display_name="ComfyUI JPS Nodes",
                sources=[source],
                node_types=("Get Image Size (JPS)",),
            )
        ],
    )
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=log_store,
        node_registry_resolver=NodeRegistryResolver(
            registry=registry,
            log_store=log_store,
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=fetcher,
            log_store=log_store,
        ),
    )

    package = store.import_archive(
        archive,
        original_filename="missing-bundled.noofy",
        allow_unverified_community_preparation=True,
    )

    assert fetcher.urls == [source.source_url]
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    record = next(
        node for node in package.custom_nodes if node.id == "comfyui_jps-nodes"
    )
    assert record.included is True
    assert record.resolution_method == "registry_metadata"
    assert record.source_cache_ref == f"{source_digest}/source"
    assert record.source_archive_subdir == "repo-root"


def test_import_store_auto_resolves_medium_github_candidate_for_missing_bundled_source(
    tmp_path: Path,
) -> None:
    commit_sha = "c" * 40
    source_archive = _source_archive_bytes(
        {
            f"comfyui-jps-{commit_sha}/node.py": (
                "NODE_CLASS_MAPPINGS = {'Get Image Size (JPS)': object}\n"
            )
        }
    )
    source_digest = hashlib.sha256(source_archive).hexdigest()
    source = NodeRegistrySource(
        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
        source_url=f"https://codeload.github.com/example/comfyui-jps/zip/{commit_sha}",
        source_ref=commit_sha,
        source_content_hash=f"sha256:{source_digest}",
        archive_subdir=f"comfyui-jps-{commit_sha}",
        source_repo_url="https://github.com/example/comfyui-jps",
    )
    fetcher = FakeSourceFetcher(source_archive)
    resolver = FakeGitHubCustomNodeSearchResolver(
        source,
        [
            _github_candidate(
                source,
                candidate_id="candidate-jps",
                repo="possible-jps-nodes",
                confidence="medium",
                evidence_score=4,
                name_match="weak",
            )
        ],
    )
    log_store = LogStore()
    store = ImportedWorkflowPackageStore(
        tmp_path / "packages",
        log_store=log_store,
        node_registry_resolver=NodeRegistryResolver(
            registry=NoofyNodeRegistry(registry_id="empty-test-registry"),
            log_store=log_store,
        ),
        custom_node_source_cache=CustomNodeSourceCache(
            cache_dir=tmp_path / "custom-node-cache",
            fetcher=fetcher,
            log_store=log_store,
        ),
        custom_node_github_resolver=resolver,
    )
    archive = _small_archive_with_custom_node(
        {
            "included": True,
            "source": "source-files/custom_nodes/ComfyUI_JPS-Nodes",
            "source_ref": None,
            "source_content_hash": None,
            "source_archive_subdir": None,
        }
    )

    package = store.import_archive(
        archive,
        original_filename="missing-bundled.noofy",
        allow_unverified_community_preparation=True,
    )

    assert fetcher.urls == [source.source_url]
    assert package.import_metadata is not None
    assert package.import_metadata.status == "needs_input_setup"
    assert all(node.id != "comfyui_jps-nodes" for node in package.custom_nodes)
    record = next(node for node in package.custom_nodes if node.id == "github-example-comfyui-jps")
    assert record.included is True
    assert record.resolution_method == "github_search_auto"
    assert record.source_cache_ref == f"{source_digest}/source"


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


def test_noofy_importer_rejects_missing_packaged_default_asset() -> None:
    payload = _source_archive_bytes(
        {
            "package.json": json.dumps(
                {
                    "publisher_id": "creator",
                    "package_id": "asset-default",
                    "version": "0.1.0",
                    "display_name": "Asset default",
                    "trust_level": "public_unverified",
                }
            ),
            "comfyui_graph.json": json.dumps(
                {
                    "1": {
                        "class_type": "LoadImage",
                        "inputs": {"image": "__noofy_runtime_image_input_required__"},
                    }
                }
            ),
            "dashboard.json": json.dumps(
                {
                    "version": "0.1.0",
                    "status": "not_configured",
                    "inputs": [
                        {
                            "id": "input-1-image",
                            "label": "Image",
                            "control": "load_image",
                            "binding": {"node_id": "1", "input_name": "image"},
                            "default": {
                                "source": "package_asset",
                                "asset_id": "input-defaults/missing.png",
                                "kind": "image",
                                "content_type": "image/png",
                            },
                            "default_pinned": True,
                            "validation": {},
                        }
                    ],
                    "outputs": [],
                    "sections": [],
                }
            ),
            "capsule.lock.json": json.dumps({"models": [], "custom_nodes": []}),
            "export-report.json": json.dumps({}),
        }
    )

    with pytest.raises(NoofyImportError, match="missing from the archive"):
        NoofyArchiveImporter(payload).normalize()


def test_noofy_importer_rejects_mismatched_packaged_default_asset_hash() -> None:
    payload = _source_archive_bytes(
        {
            "package.json": json.dumps(
                {
                    "publisher_id": "creator",
                    "package_id": "asset-default",
                    "version": "0.1.0",
                    "display_name": "Asset default",
                    "trust_level": "public_unverified",
                }
            ),
            "comfyui_graph.json": json.dumps(
                {
                    "1": {
                        "class_type": "LoadImage",
                        "inputs": {"image": "__noofy_runtime_image_input_required__"},
                    }
                }
            ),
            "dashboard.json": json.dumps(
                {
                    "version": "0.1.0",
                    "status": "not_configured",
                    "inputs": [
                        {
                            "id": "input-1-image",
                            "label": "Image",
                            "control": "load_image",
                            "binding": {"node_id": "1", "input_name": "image"},
                            "default": {
                                "source": "package_asset",
                                "asset_id": "input-defaults/default.png",
                                "kind": "image",
                                "content_type": "image/png",
                                "size_bytes": 7,
                                "sha256": "sha256:" + "0" * 64,
                            },
                            "default_pinned": True,
                            "validation": {},
                        }
                    ],
                    "outputs": [],
                    "sections": [],
                }
            ),
            "assets/input-defaults/default.png": "changed",
            "capsule.lock.json": json.dumps({"models": [], "custom_nodes": []}),
            "export-report.json": json.dumps({}),
        }
    )

    with pytest.raises(NoofyImportError, match="mismatched content"):
        NoofyArchiveImporter(payload).normalize()


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


def _archive_bytes_with_comfyui_workflow_update(
    comfyui_workflow: dict, *, archive_bytes: bytes | None = None
) -> bytes:
    source = io.BytesIO(archive_bytes or _archive_bytes())
    payload = io.BytesIO()
    wrote_workflow = False
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        payload, "w"
    ) as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "comfyui_workflow.json":
                contents = json.dumps(comfyui_workflow).encode("utf-8")
                wrote_workflow = True
            rewritten.writestr(info, contents)
        if not wrote_workflow:
            rewritten.writestr(
                "comfyui_workflow.json",
                json.dumps(comfyui_workflow).encode("utf-8"),
            )
    return payload.getvalue()


def _archive_bytes_with_graph_update(
    graph: dict, *, archive_bytes: bytes | None = None
) -> bytes:
    source = io.BytesIO(archive_bytes or _archive_bytes())
    payload = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        payload, "w"
    ) as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "comfyui_graph.json":
                contents = json.dumps(graph).encode("utf-8")
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


def _archive_bytes_with_dashboard_update(
    dashboard: dict, *, archive_bytes: bytes | None = None
) -> bytes:
    source = io.BytesIO(archive_bytes or _archive_bytes())
    payload = io.BytesIO()
    with zipfile.ZipFile(source, "r") as original, zipfile.ZipFile(
        payload, "w"
    ) as rewritten:
        for info in original.infolist():
            contents = original.read(info)
            if info.filename == "dashboard.json":
                contents = json.dumps(dashboard).encode("utf-8")
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


def _github_candidate(
    source: NodeRegistrySource,
    *,
    candidate_id: str = "candidate-1",
    repo: str = "comfyui-missing",
    confidence: str = "high",
    evidence_score: int = 10,
    name_match: str = "exact",
) -> GitHubCustomNodeCandidate:
    return GitHubCustomNodeCandidate(
        candidate_id=candidate_id,
        owner="example",
        repo=repo,
        html_url=source.source_repo_url or "https://github.com/example/comfyui-missing",
        description="Missing custom nodes",
        stargazers_count=42,
        updated_at="2026-06-01T00:00:00Z",
        source=source,
        evidence=("NODE_CLASS_MAPPINGS found in Python source",),
        evidence_score=evidence_score,
        name_match=name_match,
        confidence=confidence,
    )


def _moss_tts_graph() -> dict[str, object]:
    return {
        "1": {
            "_meta": {"title": "MOSS-TTS Model Loader"},
            "class_type": "MossTTSModelLoader",
            "inputs": {
                "codec_local_path": "",
                "local_model_path": "",
                "model_variant": "MOSS-TTS (Delay 8B)",
            },
        },
        "2": {
            "_meta": {"title": "MOSS-TTS Generate"},
            "class_type": "MossTTSGenerate",
            "inputs": {
                "moss_pipe": ["1", 0],
                "text": "I hope you like Noofy.",
            },
        },
        "3": {
            "_meta": {"title": "Preview Audio"},
            "class_type": "PreviewAudio",
            "inputs": {"audio": ["2", 0]},
        },
    }


def _github_repo_payload(
    *,
    owner: str,
    repo: str,
    stars: int,
) -> dict[str, object]:
    return {
        "full_name": f"{owner}/{repo}",
        "owner": {"login": owner},
        "name": repo,
        "html_url": f"https://github.com/{owner}/{repo}",
        "description": f"{repo} custom nodes",
        "stargazers_count": stars,
        "updated_at": "2026-06-01T00:00:00Z",
        "default_branch": "main",
    }


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


def _custom_node_registry_entry(
    package_id: str,
    node_types: list[str],
    *,
    source_content_hash: str | None = None,
) -> NodeRegistryEntry:
    return NodeRegistryEntry(
        package_id=package_id,
        trust_level="registry_locked",
        node_types=node_types,
        sources=[
            NodeRegistrySource(
                source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
                source_url=f"https://example.test/{package_id}/archive/pinned.zip",
                source_ref="7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                source_content_hash=source_content_hash or "sha256:" + ("1" * 64),
            )
        ],
    )
