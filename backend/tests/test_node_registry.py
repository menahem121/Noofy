from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.engine.diagnostics import LogStore
from app.runtime.isolation import TrustLevel
from app.runtime.node_registry import (
    CachedCustomNodeSource,
    CustomNodeSourceCache,
    CustomNodeSourceResolutionRequest,
    NodeRegistryEntry,
    NodeRegistryResolutionError,
    NodeRegistryResolutionErrorCode,
    NodeRegistryResolver,
    NodeRegistrySource,
    NodeRegistrySourceKind,
    NodeTypeMappingCatalog,
    NoofyNodeRegistry,
)


class FakeFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.urls.append(url)
        return self.payload


def test_registry_resolves_custom_node_by_node_type_mapping_with_pinned_source() -> None:
    log_store = LogStore()
    resolver = NodeRegistryResolver(
        registry=_registry(),
        mappings=NodeTypeMappingCatalog(node_type_to_package_id={"MagicSampler": "comfyui-magic"}),
        log_store=log_store,
    )

    resolved = resolver.resolve(
        CustomNodeSourceResolutionRequest(
            node_types=["MagicSampler"],
            trust_level=TrustLevel.QUARANTINED_COMMUNITY,
            allow_unverified_community_preparation=True,
        )
    )

    assert resolved.package_id == "comfyui-magic"
    assert resolved.resolution_method == "node_type_mapping"
    assert resolved.source.source_ref == "7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50"
    lock = resolved.to_custom_node_lock()
    assert lock.source_ref == resolved.source.source_ref
    assert lock.source_content_hash == resolved.source.source_content_hash
    cached_lock = resolved.to_custom_node_lock(
        CachedCustomNodeSource(
            source_cache_ref="abc123/source",
            source_dir=Path("/tmp/source-cache/abc123/source"),
            source_content_hash="sha256:" + ("1" * 64),
            manifest_path=Path("/tmp/source-cache/abc123/noofy-custom-node-source-cache-manifest.json"),
        )
    )
    assert cached_lock.source_cache_ref == "abc123/source"
    latest = log_store.list_events().events[-1]
    assert latest.message == "Resolved custom-node source"
    assert latest.details["source_ref"] == resolved.source.source_ref


def test_quarantined_registry_resolution_requires_explicit_opt_in() -> None:
    resolver = NodeRegistryResolver(registry=_registry())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                package_id="comfyui-magic",
                node_types=["MagicSampler"],
                trust_level=TrustLevel.QUARANTINED_COMMUNITY,
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.COMMUNITY_OPT_IN_REQUIRED


def test_registry_rejects_floating_source_refs_before_resolution() -> None:
    with pytest.raises(ValidationError, match="source_ref must be pinned"):
        NodeRegistrySource(
            source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
            source_url="https://example.test/comfyui-magic/archive/main.zip",
            source_ref="main",
            source_content_hash="sha256:" + ("1" * 64),
        )


def test_verified_workflow_cannot_resolve_registry_locked_source() -> None:
    resolver = NodeRegistryResolver(registry=_registry())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                package_id="comfyui-magic",
                node_types=["MagicSampler"],
                trust_level=TrustLevel.NOOFY_VERIFIED,
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.POLICY_BLOCKED_TRUST_LEVEL


def test_registry_reports_ambiguous_node_type_claims_without_preemption() -> None:
    resolver = NodeRegistryResolver(registry=_ambiguous_registry())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                node_types=["SharedNode"],
                trust_level=TrustLevel.QUARANTINED_COMMUNITY,
                allow_unverified_community_preparation=True,
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.AMBIGUOUS_NODE_TYPE
    assert error.value.developer_details["package_ids"] == ["first-pack", "second-pack"]


def test_node_type_mapping_preempts_ambiguous_registry_claims() -> None:
    resolver = NodeRegistryResolver(
        registry=_ambiguous_registry(),
        mappings=NodeTypeMappingCatalog(node_type_to_package_id={"SharedNode": "second-pack"}),
    )

    resolved = resolver.resolve(
        CustomNodeSourceResolutionRequest(
            node_types=["SharedNode"],
            trust_level=TrustLevel.QUARANTINED_COMMUNITY,
            allow_unverified_community_preparation=True,
        )
    )

    assert resolved.package_id == "second-pack"
    assert resolved.resolution_method == "node_type_mapping"


def test_unknown_node_type_is_unsupported_before_download() -> None:
    resolver = NodeRegistryResolver(registry=_registry())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                node_types=["NotInRegistry"],
                trust_level=TrustLevel.QUARANTINED_COMMUNITY,
                allow_unverified_community_preparation=True,
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.UNKNOWN_NODE_TYPE


def test_source_cache_downloads_verifies_and_extracts_archive_without_mutating_core(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes({"repo/custom_nodes/magic_node.py": "NODE_CLASS_MAPPINGS = {}\n"})
    digest = hashlib.sha256(archive_bytes).hexdigest()
    trusted_core_file = tmp_path / "trusted-core" / "custom_nodes" / "trusted.py"
    trusted_core_file.parent.mkdir(parents=True)
    trusted_core_file.write_text("trusted\n", encoding="utf-8")
    fetcher = FakeFetcher(archive_bytes)
    cache = CustomNodeSourceCache(cache_dir=tmp_path / "source-cache", fetcher=fetcher)

    cached = cache.materialize(
        NodeRegistrySource(
            source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
            source_url="https://example.test/comfyui-magic/archive/7b3f5d0.zip",
            source_ref="7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
            source_content_hash=f"sha256:{digest}",
            archive_subdir="repo",
        )
    )

    assert fetcher.urls == ["https://example.test/comfyui-magic/archive/7b3f5d0.zip"]
    assert cached.source_cache_ref == f"{digest}/source"
    assert (cached.source_dir / "custom_nodes" / "magic_node.py").exists()
    assert trusted_core_file.read_text(encoding="utf-8") == "trusted\n"


def test_source_cache_rejects_hash_mismatch(tmp_path: Path) -> None:
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(_zip_bytes({"repo/node.py": "x = 1\n"})),
    )

    with pytest.raises(NodeRegistryResolutionError) as error:
        cache.materialize(
            NodeRegistrySource(
                source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
                source_url="https://example.test/node.zip",
                source_ref="release-2026-05-03",
                source_content_hash="sha256:" + ("0" * 64),
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.HASH_MISMATCH


def test_source_cache_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes({"../evil.py": "bad\n"})
    digest = hashlib.sha256(archive_bytes).hexdigest()
    cache = CustomNodeSourceCache(cache_dir=tmp_path / "source-cache", fetcher=FakeFetcher(archive_bytes))

    with pytest.raises(NodeRegistryResolutionError) as error:
        cache.materialize(
            NodeRegistrySource(
                source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
                source_url="https://example.test/node.zip",
                source_ref="release-2026-05-03",
                source_content_hash=f"sha256:{digest}",
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.UNSAFE_ARCHIVE_PATH


def test_source_cache_rejects_existing_cache_metadata_mismatch(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes({"node.py": "x = 1\n"})
    digest = hashlib.sha256(archive_bytes).hexdigest()
    target_dir = tmp_path / "source-cache" / digest
    (target_dir / "source").mkdir(parents=True)
    (target_dir / "noofy-custom-node-source-cache-manifest.json").write_text(
        '{"source_ref":"different","source_url":"https://example.test/node.zip",'
        f'"source_content_hash":"sha256:{digest}","source_cache_ref":"{digest}/source"}}',
        encoding="utf-8",
    )
    cache = CustomNodeSourceCache(cache_dir=tmp_path / "source-cache", fetcher=FakeFetcher(archive_bytes))

    with pytest.raises(NodeRegistryResolutionError) as error:
        cache.materialize(
            NodeRegistrySource(
                source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
                source_url="https://example.test/node.zip",
                source_ref="release-2026-05-03",
                source_content_hash=f"sha256:{digest}",
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.HASH_MISMATCH
    assert "source_ref" in error.value.developer_details["mismatches"]


def _registry() -> NoofyNodeRegistry:
    return NoofyNodeRegistry(
        registry_id="noofy-test-registry",
        registry_snapshot_hash="sha256:" + ("2" * 64),
        entries=[
            NodeRegistryEntry(
                package_id="comfyui-magic",
                display_name="ComfyUI Magic",
                trust_level=TrustLevel.REGISTRY_LOCKED,
                node_types=["MagicSampler"],
                sources=[
                    NodeRegistrySource(
                        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
                        source_url="https://example.test/comfyui-magic/archive/7b3f5d0.zip",
                        source_ref="7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                        source_content_hash="sha256:" + ("1" * 64),
                    )
                ],
            )
        ],
    )


def _ambiguous_registry() -> NoofyNodeRegistry:
    return NoofyNodeRegistry(
        registry_id="noofy-test-registry",
        entries=[
            NodeRegistryEntry(
                package_id=package_id,
                trust_level=TrustLevel.REGISTRY_LOCKED,
                node_types=["SharedNode"],
                sources=[
                    NodeRegistrySource(
                        source_kind=NodeRegistrySourceKind.GIT_ZIP_ARCHIVE,
                        source_url=f"https://example.test/{package_id}/archive/7b3f5d0.zip",
                        source_ref="7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                        source_content_hash="sha256:" + ("1" * 64),
                    )
                ],
            )
            for package_id in ("first-pack", "second-pack")
        ],
    )


def _zip_bytes(files: dict[str, str]) -> bytes:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        for path, contents in files.items():
            archive.writestr(path, contents)
    return payload.getvalue()
