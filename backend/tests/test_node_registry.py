from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.diagnostics import LogStore
from app.runtime.dependencies.isolation import TrustLevel
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
from app.source_policy import SourcePolicy


class FakeFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.urls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.urls.append(url)
        return self.payload


def test_registry_resolves_custom_node_by_node_type_mapping_with_pinned_source() -> (
    None
):
    log_store = LogStore()
    resolver = NodeRegistryResolver(
        registry=_registry(),
        mappings=NodeTypeMappingCatalog(
            node_type_to_package_id={"MagicSampler": "comfyui-magic"}
        ),
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
            manifest_path=Path(
                "/tmp/source-cache/abc123/noofy-custom-node-source-cache-manifest.json"
            ),
        )
    )
    assert cached_lock.source_cache_ref == "abc123/source"
    latest = log_store.list_events().events[-1]
    assert latest.message == "Resolved custom-node source"
    assert latest.details["source_ref"] == resolved.source.source_ref


def test_quarantined_registry_resolution_requires_explicit_opt_in() -> None:
    resolver = NodeRegistryResolver(registry=_registry(), log_store=LogStore())

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
    resolver = NodeRegistryResolver(registry=_registry(), log_store=LogStore())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                package_id="comfyui-magic",
                node_types=["MagicSampler"],
                trust_level=TrustLevel.NOOFY_VERIFIED,
            )
        )

    assert (
        error.value.code is NodeRegistryResolutionErrorCode.POLICY_BLOCKED_TRUST_LEVEL
    )


def test_source_policy_blocks_explicit_source_for_verified_workflow() -> None:
    resolver = NodeRegistryResolver(registry=_registry(), log_store=LogStore())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                package_id="comfyui-magic",
                node_types=["MagicSampler"],
                trust_level=TrustLevel.NOOFY_VERIFIED,
                explicit_source=NodeRegistrySource(
                    source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
                    source_url="https://example.test/comfyui-magic/archive/pinned.zip",
                    source_ref="7b3f5d0a9d508b641f85a7db4fbb7f1c2d3e4f50",
                    source_content_hash="sha256:" + ("1" * 64),
                ),
                source_policy=SourcePolicy(
                    trust_level="noofy_verified",
                    source_policy="noofy_verified_sources_only",
                    package_source_type="noofy_archive_import",
                    automatic_preparation_allowed=True,
                    allowed_source_origins=["noofy-verified"],
                    model_source_trust="hashed",
                ),
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED
    assert error.value.developer_details["allowed_source_origins"] == ["noofy-verified"]


def test_source_policy_blocks_mismatched_registry_snapshot() -> None:
    resolver = NodeRegistryResolver(registry=_registry(), log_store=LogStore())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                package_id="comfyui-magic",
                node_types=["MagicSampler"],
                trust_level=TrustLevel.REGISTRY_LOCKED,
                source_policy=SourcePolicy(
                    trust_level="registry_locked",
                    source_policy="signed_registry_or_pinned_registry_sources",
                    package_source_type="noofy_archive_import",
                    automatic_preparation_allowed=True,
                    allowed_registry_origins=["noofy-test-registry"],
                    allowed_source_origins=["noofy-test-registry"],
                    registry_id="noofy-test-registry",
                    registry_snapshot_hash="sha256:" + ("3" * 64),
                    model_source_trust="hashed",
                ),
            )
        )

    assert (
        error.value.code is NodeRegistryResolutionErrorCode.REGISTRY_SNAPSHOT_MISMATCH
    )
    assert error.value.developer_details[
        "active_registry_snapshot_hash"
    ] == "sha256:" + ("2" * 64)


def test_source_policy_allows_matching_registry_locked_source() -> None:
    resolver = NodeRegistryResolver(registry=_registry(), log_store=LogStore())

    resolved = resolver.resolve(
        CustomNodeSourceResolutionRequest(
            package_id="comfyui-magic",
            node_types=["MagicSampler"],
            trust_level=TrustLevel.REGISTRY_LOCKED,
            source_policy=SourcePolicy(
                trust_level="registry_locked",
                source_policy="signed_registry_or_pinned_registry_sources",
                package_source_type="noofy_archive_import",
                automatic_preparation_allowed=True,
                allowed_registry_origins=["noofy-test-registry"],
                allowed_source_origins=["noofy-test-registry"],
                registry_id="noofy-test-registry",
                registry_snapshot_hash="sha256:" + ("2" * 64),
                model_source_trust="hashed",
            ),
        )
    )

    assert resolved.package_id == "comfyui-magic"
    assert resolved.registry_id == "noofy-test-registry"


def test_registry_reports_ambiguous_node_type_claims_without_preemption() -> None:
    resolver = NodeRegistryResolver(
        registry=_ambiguous_registry(), log_store=LogStore()
    )

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
        mappings=NodeTypeMappingCatalog(
            node_type_to_package_id={"SharedNode": "second-pack"}
        ),
        log_store=LogStore(),
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
    resolver = NodeRegistryResolver(registry=_registry(), log_store=LogStore())

    with pytest.raises(NodeRegistryResolutionError) as error:
        resolver.resolve(
            CustomNodeSourceResolutionRequest(
                node_types=["NotInRegistry"],
                trust_level=TrustLevel.QUARANTINED_COMMUNITY,
                allow_unverified_community_preparation=True,
            )
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.UNKNOWN_NODE_TYPE


def test_source_cache_downloads_verifies_and_extracts_archive_without_mutating_core(
    tmp_path: Path,
) -> None:
    archive_bytes = _zip_bytes(
        {
            "repo/custom_nodes/magic_node.py": "NODE_CLASS_MAPPINGS = {}\n",
            "repo/models/birefnet.py": "MODEL = 'BiRefNet'\n",
            "repo/input/example.txt": "input\n",
        }
    )
    digest = hashlib.sha256(archive_bytes).hexdigest()
    trusted_core_file = tmp_path / "trusted-core" / "custom_nodes" / "trusted.py"
    trusted_core_file.parent.mkdir(parents=True)
    trusted_core_file.write_text("trusted\n", encoding="utf-8")
    fetcher = FakeFetcher(archive_bytes)
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache", fetcher=fetcher, log_store=LogStore()
    )

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
    assert (cached.source_dir / "models" / "birefnet.py").exists()
    assert (cached.source_dir / "input" / "example.txt").exists()
    assert trusted_core_file.read_text(encoding="utf-8") == "trusted\n"


def test_source_cache_streams_archive_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_bytes = _zip_bytes({"models/model.bin": "streamed-content"})
    digest = hashlib.sha256(archive_bytes).hexdigest()
    calls: list[int] = []
    from app.runtime import node_registry

    original_copy_stream_limited = node_registry.copy_stream_limited

    def track_stream_copy(source, destination, *, max_bytes: int, chunk_bytes=1024 * 1024):
        calls.append(max_bytes)
        return original_copy_stream_limited(
            source,
            destination,
            max_bytes=max_bytes,
            chunk_bytes=3,
        )

    monkeypatch.setattr(node_registry, "copy_stream_limited", track_stream_copy)
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(archive_bytes),
        log_store=LogStore(),
    )

    cached = cache.materialize(
        NodeRegistrySource(
            source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
            source_url="https://example.test/node.zip",
            source_ref="release-2026-05-03",
            source_content_hash=f"sha256:{digest}",
        )
    )

    assert calls
    assert (cached.source_dir / "models" / "model.bin").read_text(
        encoding="utf-8"
    ) == "streamed-content"


def test_source_cache_rejects_hash_mismatch(tmp_path: Path) -> None:
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(_zip_bytes({"repo/node.py": "x = 1\n"})),
        log_store=LogStore(),
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


def test_source_cache_blocks_download_when_source_policy_origin_does_not_match(
    tmp_path: Path,
) -> None:
    fetcher = FakeFetcher(_zip_bytes({"node.py": "x = 1\n"}))
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache", fetcher=fetcher, log_store=LogStore()
    )

    with pytest.raises(NodeRegistryResolutionError) as error:
        cache.materialize(
            NodeRegistrySource(
                source_kind=NodeRegistrySourceKind.HTTPS_ZIP_ARCHIVE,
                source_url="https://example.test/node.zip",
                source_ref="release-2026-05-03",
                source_content_hash="sha256:" + ("1" * 64),
            ),
            source_policy=SourcePolicy(
                trust_level="noofy_verified",
                source_policy="noofy_verified_sources_only",
                package_source_type="noofy_archive_import",
                automatic_preparation_allowed=True,
                allowed_source_origins=["noofy-verified"],
                model_source_trust="hashed",
            ),
            source_origins=["explicit-metadata"],
        )

    assert error.value.code is NodeRegistryResolutionErrorCode.SOURCE_POLICY_BLOCKED
    assert fetcher.urls == []


def test_source_cache_rejects_archive_path_traversal(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes({"../evil.py": "bad\n"})
    digest = hashlib.sha256(archive_bytes).hexdigest()
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(archive_bytes),
        log_store=LogStore(),
    )

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
    assert error.value.developer_details["boundary"] == "archive_member_path"


def test_source_cache_rejects_case_insensitive_archive_path_collision(
    tmp_path: Path,
) -> None:
    archive_bytes = _zip_bytes(
        {
            "models/Foo.py": "first\n",
            "models/foo.py": "second\n",
        }
    )
    digest = hashlib.sha256(archive_bytes).hexdigest()
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(archive_bytes),
        log_store=LogStore(),
    )

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
    assert error.value.developer_details["boundary"] == "archive_member_path"
    assert error.value.developer_details["reason"] == "collision"


@pytest.mark.parametrize(
    "archive_path",
    [
        "models//birefnet.py",
        "models/./birefnet.py",
        "models\\birefnet.py",
    ],
)
def test_source_cache_rejects_invalid_archive_path_shape(
    tmp_path: Path,
    archive_path: str,
) -> None:
    archive_bytes = _zip_bytes({archive_path: "bad\n"})
    digest = hashlib.sha256(archive_bytes).hexdigest()
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(archive_bytes),
        log_store=LogStore(),
    )

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
    assert error.value.developer_details["boundary"] == "archive_member_path"


def test_source_cache_rejects_file_directory_archive_collision(tmp_path: Path) -> None:
    archive_bytes = _zip_bytes(
        {
            "models": "file\n",
            "models/birefnet.py": "nested\n",
        }
    )
    digest = hashlib.sha256(archive_bytes).hexdigest()
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(archive_bytes),
        log_store=LogStore(),
    )

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
    assert error.value.developer_details["reason"] == "collision"


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
    cache = CustomNodeSourceCache(
        cache_dir=tmp_path / "source-cache",
        fetcher=FakeFetcher(archive_bytes),
        log_store=LogStore(),
    )

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
