"""Phase 5c custom-node workspace materialization.

The trusted backend treats custom-node source as data. It may copy validated
bundled source into an isolated runner workspace, but it must not import the
modules or run their setup code.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.runtime.fingerprints import sha256_fingerprint
from app.runtime.isolation import CapsuleLock, CustomNodeLock, TrustLevel
from app.runtime.profiles import RuntimeProfileSelection

CORE_NODE_MANIFEST_SCHEMA_VERSION = "0.1.0"
CUSTOM_NODE_WORKSPACE_MANIFEST_SCHEMA_VERSION = "0.1.0"
DEFAULT_CORE_NODE_MANIFEST_PATH = Path(__file__).with_name("core_node_manifest.json")
CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME = "noofy-custom-node-workspace-manifest.json"
MAX_CUSTOM_NODE_FILES = 20_000
MAX_CUSTOM_NODE_BYTES = 512 * 1024 * 1024
_DEPENDENCY_MARKER_FILENAMES = {"requirements.txt", "pyproject.toml", "setup.py"}
_PROTECTED_CUSTOM_NODE_FOLDER_NAMES = {
    "",
    ".",
    "..",
    ".git",
    "__pycache__",
    "input",
    "models",
    "output",
    "temp",
    "user",
}


class CustomNodeMaterializationErrorCode(StrEnum):
    CASE_INSENSITIVE_PATH_COLLISION = "case_insensitive_path_collision"
    CUSTOM_NODE_SOURCE_NOT_BUNDLED = "custom_node_source_not_bundled"
    MISSING_BUNDLED_SOURCE = "missing_bundled_source"
    OVERSIZED_SOURCE = "oversized_source"
    PATH_TRAVERSAL = "path_traversal"
    PROTECTED_PATH_SHADOWING = "protected_path_shadowing"
    SYMLINK_ESCAPE = "symlink_escape"
    UNKNOWN_NODE_TYPE = "unknown_node_type"
    UNSUPPORTED_SOURCE_KIND = "unsupported_source_kind"


class CustomNodeMaterializationError(RuntimeError):
    def __init__(self, code: CustomNodeMaterializationErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class CoreNodeManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default=CORE_NODE_MANIFEST_SCHEMA_VERSION, min_length=1)
    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_variant_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    node_types: list[str] = Field(default_factory=list)

    @field_validator("node_types")
    @classmethod
    def _sort_unique_node_types(cls, value: list[str]) -> list[str]:
        return sorted(set(value))

    @property
    def manifest_hash(self) -> str:
        return sha256_fingerprint(
            {
                "kind": "core_node_manifest",
                "schema_version": self.schema_version,
                "runtime_profile_id": self.runtime_profile_id,
                "runtime_profile_variant_id": self.runtime_profile_variant_id,
                "runtime_profile_manifest_hash": self.runtime_profile_manifest_hash,
                "node_types": self.node_types,
            }
        )


class CoreNodeManifestCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default=CORE_NODE_MANIFEST_SCHEMA_VERSION, min_length=1)
    manifests: list[CoreNodeManifest] = Field(default_factory=list)

    def get(
        self,
        *,
        runtime_profile_id: str,
        runtime_profile_variant_id: str,
        runtime_profile_manifest_hash: str,
    ) -> CoreNodeManifest:
        for manifest in self.manifests:
            if (
                manifest.runtime_profile_id == runtime_profile_id
                and manifest.runtime_profile_variant_id == runtime_profile_variant_id
                and manifest.runtime_profile_manifest_hash == runtime_profile_manifest_hash
            ):
                return manifest
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.UNKNOWN_NODE_TYPE,
            f"No pinned core-node manifest exists for runtime profile variant {runtime_profile_variant_id}.",
        )


class CustomNodeSourceKind(StrEnum):
    BUNDLED_ARCHIVE = "bundled_archive"


class CustomNodeWorkspaceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    custom_node_package_id: str = Field(min_length=1)
    source_kind: CustomNodeSourceKind
    source_ref: str = Field(min_length=1)
    source_content_hash: str = Field(min_length=1)
    materialized_relative_path: str = Field(min_length=1)
    import_order_index: int = Field(ge=0)
    dependency_marker_hashes: dict[str, str] = Field(default_factory=dict)
    package_trust_level: TrustLevel
    policy_flags: dict[str, bool] = Field(default_factory=dict)
    node_types: list[str] = Field(default_factory=list)

    @field_validator("materialized_relative_path")
    @classmethod
    def _validate_materialized_path(cls, value: str) -> str:
        _safe_relative_posix_path(value, allow_nested=True)
        return value


class CustomNodeWorkspaceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(default=CUSTOM_NODE_WORKSPACE_MANIFEST_SCHEMA_VERSION, min_length=1)
    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_variant_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    core_node_manifest_hash: str = Field(min_length=1)
    graph_node_types: list[str] = Field(default_factory=list)
    entries: list[CustomNodeWorkspaceEntry] = Field(default_factory=list)
    manifest_hash: str | None = None

    @field_validator("graph_node_types")
    @classmethod
    def _sort_unique_graph_node_types(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


def with_computed_custom_node_workspace_hash(
    manifest: CustomNodeWorkspaceManifest,
) -> CustomNodeWorkspaceManifest:
    return manifest.model_copy(update={"manifest_hash": custom_node_workspace_manifest_hash(manifest)})


def custom_node_workspace_manifest_hash(manifest: CustomNodeWorkspaceManifest) -> str:
    return sha256_fingerprint(
        manifest.model_dump(mode="json", exclude_none=True, exclude={"manifest_hash"})
    )


def load_core_node_manifest_catalog(path: Path = DEFAULT_CORE_NODE_MANIFEST_PATH) -> CoreNodeManifestCatalog:
    with path.open("r", encoding="utf-8") as file:
        return CoreNodeManifestCatalog.model_validate(json.load(file))


def validate_custom_node_source_relative_paths(relative_paths: list[str]) -> None:
    """Validate archive/source path names before filesystem materialization."""
    seen_casefolded: set[str] = set()
    for relative_path in relative_paths:
        _safe_relative_posix_path(relative_path, allow_nested=True)
        casefolded = relative_path.casefold()
        if casefolded in seen_casefolded:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.CASE_INSENSITIVE_PATH_COLLISION,
                f"Bundled custom-node source has a case-insensitive path collision: {relative_path}",
            )
        seen_casefolded.add(casefolded)


class CustomNodeWorkspaceMaterializer:
    def __init__(
        self,
        *,
        core_node_manifest_catalog: CoreNodeManifestCatalog | None = None,
        max_files: int = MAX_CUSTOM_NODE_FILES,
        max_bytes: int = MAX_CUSTOM_NODE_BYTES,
    ) -> None:
        self.core_node_manifest_catalog = core_node_manifest_catalog or load_core_node_manifest_catalog()
        self.max_files = max_files
        self.max_bytes = max_bytes

    def build_manifest(
        self,
        *,
        capsule_lock: CapsuleLock,
        source_files_dir: Path | None,
        profile_selection: RuntimeProfileSelection | None = None,
    ) -> CustomNodeWorkspaceManifest:
        core_manifest = self.core_node_manifest_catalog.get(
            runtime_profile_id=capsule_lock.runtime.runtime_profile_id,
            runtime_profile_variant_id=capsule_lock.runtime.runtime_profile_variant_id,
            runtime_profile_manifest_hash=capsule_lock.runtime.runtime_profile_manifest_hash,
        )
        graph_node_types = _graph_node_types(source_files_dir)
        required_custom_nodes = _required_custom_nodes(
            capsule_lock.custom_nodes,
            graph_node_types=graph_node_types,
            core_node_types=set(core_manifest.node_types),
        )
        if required_custom_nodes and source_files_dir is None:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.CUSTOM_NODE_SOURCE_NOT_BUNDLED,
                "Workflow requires bundled custom nodes, but no imported source files are available.",
            )

        entries = [
            self._entry_for_custom_node(
                custom_node,
                import_order_index=index,
                source_files_dir=source_files_dir,
                package_trust_level=capsule_lock.trust.level,
            )
            for index, custom_node in enumerate(required_custom_nodes)
        ]
        return with_computed_custom_node_workspace_hash(
            CustomNodeWorkspaceManifest(
                runtime_profile_id=capsule_lock.runtime.runtime_profile_id,
                runtime_profile_variant_id=capsule_lock.runtime.runtime_profile_variant_id,
                runtime_profile_manifest_hash=capsule_lock.runtime.runtime_profile_manifest_hash,
                core_node_manifest_hash=core_manifest.manifest_hash,
                graph_node_types=graph_node_types,
                entries=entries,
            )
        )

    def materialize(
        self,
        *,
        manifest: CustomNodeWorkspaceManifest,
        source_files_dir: Path | None,
        runner_workspace_dir: Path,
    ) -> None:
        custom_nodes_target = runner_workspace_dir / "custom_nodes"
        custom_nodes_target.mkdir(parents=True, exist_ok=True)
        for entry in manifest.entries:
            if source_files_dir is None:
                raise CustomNodeMaterializationError(
                    CustomNodeMaterializationErrorCode.CUSTOM_NODE_SOURCE_NOT_BUNDLED,
                    "Workflow requires bundled custom nodes, but no imported source files are available.",
                )
            source_dir = _source_dir_for_entry(source_files_dir, entry)
            target_dir = runner_workspace_dir / entry.materialized_relative_path
            _copy_validated_tree(
                source_dir,
                target_dir,
                max_files=self.max_files,
                max_bytes=self.max_bytes,
            )

        manifest_path = runner_workspace_dir / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    def _entry_for_custom_node(
        self,
        custom_node: CustomNodeLock,
        *,
        import_order_index: int,
        source_files_dir: Path | None,
        package_trust_level: TrustLevel,
    ) -> CustomNodeWorkspaceEntry:
        source_dir = _find_custom_node_source_dir(source_files_dir, custom_node) if source_files_dir is not None else None
        if source_dir is None or not source_dir.is_dir():
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.MISSING_BUNDLED_SOURCE,
                f"Bundled source for custom node {custom_node.package_id} was not found.",
            )
        folder_name = _safe_relative_posix_path(source_dir.name, allow_nested=False)
        source_content_hash, dependency_marker_hashes = _source_tree_facts(
            source_dir,
            max_files=self.max_files,
            max_bytes=self.max_bytes,
        )
        return CustomNodeWorkspaceEntry(
            custom_node_package_id=custom_node.package_id,
            source_kind=CustomNodeSourceKind.BUNDLED_ARCHIVE,
            source_ref=custom_node.source,
            source_content_hash=source_content_hash,
            materialized_relative_path=f"custom_nodes/{folder_name}",
            import_order_index=import_order_index,
            dependency_marker_hashes=dependency_marker_hashes,
            package_trust_level=package_trust_level,
            policy_flags={
                "has_install_py": (source_dir / "install.py").exists() or (source_dir / "setup.py").exists(),
            },
            node_types=sorted(custom_node.node_types),
        )


def _required_custom_nodes(
    custom_nodes: list[CustomNodeLock],
    *,
    graph_node_types: list[str],
    core_node_types: set[str],
) -> list[CustomNodeLock]:
    custom_node_type_map: dict[str, CustomNodeLock] = {}
    for custom_node in custom_nodes:
        for node_type in custom_node.node_types:
            custom_node_type_map[node_type] = custom_node

    required_by_id: dict[str, CustomNodeLock] = {}
    for node_type in graph_node_types:
        if node_type in core_node_types:
            continue
        custom_node = custom_node_type_map.get(node_type)
        if custom_node is None:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.UNKNOWN_NODE_TYPE,
                f"Workflow graph uses unknown non-core node type: {node_type}",
            )
        required_by_id[custom_node.package_id] = custom_node

    required_custom_nodes = sorted(required_by_id.values(), key=lambda item: (item.package_id, item.source))
    for custom_node in required_custom_nodes:
        if _custom_node_source_kind(custom_node) is not CustomNodeSourceKind.BUNDLED_ARCHIVE:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.UNSUPPORTED_SOURCE_KIND,
                f"Custom node {custom_node.package_id} does not use bundled archive source.",
            )
    return required_custom_nodes


def _custom_node_source_kind(custom_node: CustomNodeLock) -> CustomNodeSourceKind | None:
    if (
        custom_node.source == "bundled_archive"
        or custom_node.source == "bundled_from_creator_machine"
        or custom_node.source.startswith("bundled_archive:")
    ):
        return CustomNodeSourceKind.BUNDLED_ARCHIVE
    return None


def _graph_node_types(source_files_dir: Path | None) -> list[str]:
    if source_files_dir is None:
        return []
    graph_path = source_files_dir / "comfyui_graph.json"
    if not graph_path.exists():
        return []
    with graph_path.open("r", encoding="utf-8") as file:
        graph = json.load(file)
    if not isinstance(graph, dict):
        return []
    node_types: set[str] = set()
    for node in graph.values():
        if isinstance(node, dict) and isinstance(node.get("class_type"), str):
            node_types.add(node["class_type"])
    return sorted(node_types)


def _source_tree_facts(
    source_dir: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[str, dict[str, str]]:
    entries: list[dict[str, Any]] = []
    dependency_marker_hashes: dict[str, str] = {}
    total_bytes = 0
    seen_casefolded: set[str] = set()
    file_count = 0
    for path in sorted(source_dir.rglob("*"), key=lambda item: item.relative_to(source_dir).as_posix()):
        relative_path = path.relative_to(source_dir).as_posix()
        _validate_materialized_source_path(path, source_dir, relative_path, seen_casefolded)
        if path.is_dir():
            continue
        file_count += 1
        if file_count > max_files:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.OVERSIZED_SOURCE,
                "Bundled custom-node source contains too many files.",
            )
        size_bytes = path.stat().st_size
        total_bytes += size_bytes
        if total_bytes > max_bytes:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.OVERSIZED_SOURCE,
                "Bundled custom-node source is too large.",
            )
        digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({"relative_path": relative_path, "sha256": digest, "size_bytes": size_bytes})
        if Path(relative_path).name in _DEPENDENCY_MARKER_FILENAMES:
            dependency_marker_hashes[relative_path] = digest
    return sha256_fingerprint({"kind": "custom_node_source_tree", "entries": entries}), dependency_marker_hashes


def _copy_validated_tree(
    source_dir: Path,
    target_dir: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> None:
    _source_tree_facts(source_dir, max_files=max_files, max_bytes=max_bytes)
    if target_dir.exists() or target_dir.is_symlink():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(source_dir.rglob("*"), key=lambda item: item.relative_to(source_dir).as_posix()):
        relative = path.relative_to(source_dir)
        target = target_dir / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _validate_materialized_source_path(
    path: Path,
    source_dir: Path,
    relative_path: str,
    seen_casefolded: set[str],
) -> None:
    _safe_relative_posix_path(relative_path, allow_nested=True)
    casefolded = relative_path.casefold()
    if casefolded in seen_casefolded:
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.CASE_INSENSITIVE_PATH_COLLISION,
            f"Bundled custom-node source has a case-insensitive path collision: {relative_path}",
        )
    seen_casefolded.add(casefolded)

    if path.is_symlink():
        resolved = path.resolve()
        root = source_dir.resolve()
        if root not in resolved.parents and resolved != root:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.SYMLINK_ESCAPE,
                f"Bundled custom-node source contains an escaping symlink: {relative_path}",
            )
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.SYMLINK_ESCAPE,
            f"Bundled custom-node source contains an unsupported symlink: {relative_path}",
        )


def _source_dir_for_entry(source_files_dir: Path, entry: CustomNodeWorkspaceEntry) -> Path:
    relative = entry.materialized_relative_path.removeprefix("custom_nodes/")
    return _custom_node_source_dir(source_files_dir, relative)


def _custom_node_source_dir(source_files_dir: Path | None, folder_name: str) -> Path:
    assert source_files_dir is not None
    return source_files_dir / "custom_nodes" / folder_name


def _find_custom_node_source_dir(source_files_dir: Path | None, custom_node: CustomNodeLock) -> Path | None:
    if source_files_dir is None:
        return None
    explicit_name = _custom_node_source_name_from_source(custom_node.source)
    custom_nodes_dir = source_files_dir / "custom_nodes"
    if explicit_name is not None:
        explicit_path = custom_nodes_dir / explicit_name
        if explicit_path.is_dir():
            return explicit_path
    if not custom_nodes_dir.is_dir():
        return None
    wanted = _normalized_source_dir_name(custom_node.package_id)
    for candidate in sorted(custom_nodes_dir.iterdir(), key=lambda item: item.name.casefold()):
        if candidate.is_dir() and _normalized_source_dir_name(candidate.name) == wanted:
            return candidate
    return None


def _custom_node_source_name_from_source(source: str) -> str | None:
    if source.startswith("bundled_archive:"):
        return _safe_relative_posix_path(source.split(":", 1)[1], allow_nested=False)
    return None


def _normalized_source_dir_name(value: str) -> str:
    return value.replace("_", "-").casefold()


def _safe_relative_posix_path(value: str, *, allow_nested: bool) -> str:
    if "\\" in value:
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Path must use POSIX separators only: {value}",
        )
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Path is not a safe relative path: {value}",
        )
    if not allow_nested and len(path.parts) != 1:
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Custom-node folder name must not be nested: {value}",
        )
    if path.parts[0] in _PROTECTED_CUSTOM_NODE_FOLDER_NAMES:
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PROTECTED_PATH_SHADOWING,
            f"Custom-node source shadows a protected runtime path: {value}",
        )
    return value
