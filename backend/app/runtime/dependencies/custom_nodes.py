"""Phase 5c custom-node workspace materialization.

The trusted backend treats custom-node source as data. It may copy validated
bundled source into an isolated runner workspace, but it must not import the
modules or run their setup code.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.archive_safety import (
    MaterializedPathIndex,
    PathSafetyError,
    contained_destination,
    path_is_within,
    safe_relative_posix_path,
)
from app.runtime.fingerprints import sha256_fingerprint
from app.runtime.dependencies.isolation import CapsuleLock, CustomNodeLock, TrustLevel

CUSTOM_NODE_WORKSPACE_MANIFEST_SCHEMA_VERSION = "0.2.0"
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
    CUSTOM_NODE_SOURCE_NOT_CACHED = "custom_node_source_not_cached"
    MISSING_BUNDLED_SOURCE = "missing_bundled_source"
    MISSING_RESOLVED_SOURCE_FACTS = "missing_resolved_source_facts"
    OVERSIZED_SOURCE = "oversized_source"
    PATH_TRAVERSAL = "path_traversal"
    PROTECTED_PATH_SHADOWING = "protected_path_shadowing"
    SYMLINK_ESCAPE = "symlink_escape"
    STAGING_FAILED = "staging_failed"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    UNSUPPORTED_SOURCE_KIND = "unsupported_source_kind"
    WORKSPACE_PROMOTION_FAILED = "workspace_promotion_failed"


class CustomNodeSourceBoundary(StrEnum):
    ARCHIVE_MEMBER_PATH = "archive_member_path"
    CUSTOM_NODE_PACKAGE_FOLDER = "custom_node_package_folder"
    CUSTOM_NODE_INTERNAL_PATH = "custom_node_internal_path"
    RUNNER_WORKSPACE_DESTINATION = "runner_workspace_destination"


@dataclass(frozen=True)
class _StagedCustomNodePackage:
    target_dir: Path
    staging_dir: Path
    backup_dir: Path


class CustomNodeMaterializationError(RuntimeError):
    def __init__(
        self,
        code: CustomNodeMaterializationErrorCode,
        message: str,
        *,
        boundary: CustomNodeSourceBoundary | None = None,
        relative_path: str | None = None,
        reason: str | None = None,
        user_message: str | None = None,
        developer_details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = user_message or message
        self.boundary = boundary
        self.relative_path = relative_path
        self.reason = reason or code.value
        self.developer_details = {
            **(developer_details or {}),
            "boundary": boundary.value if boundary is not None else None,
            "relative_path": relative_path,
            "reason": self.reason,
            "error_code": code.value,
            "technical_message": message,
        }


class CustomNodeSourceKind(StrEnum):
    BUNDLED_ARCHIVE = "bundled_archive"
    NOOFY_CACHED_ARCHIVE = "noofy_cached_archive"


class CustomNodeWorkspaceEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    custom_node_package_id: str = Field(min_length=1)
    source_kind: CustomNodeSourceKind
    source_ref: str = Field(min_length=1)
    source_content_hash: str = Field(min_length=1)
    materialized_relative_path: str = Field(min_length=1)
    source_folder_name: str | None = None
    import_order_index: int = Field(ge=0)
    dependency_marker_hashes: dict[str, str] = Field(default_factory=dict)
    package_trust_level: TrustLevel
    policy_flags: dict[str, bool] = Field(default_factory=dict)
    node_types: list[str] = Field(default_factory=list)

    @field_validator("materialized_relative_path")
    @classmethod
    def _validate_materialized_path(cls, value: str) -> str:
        try:
            return _safe_custom_node_workspace_relative_path(value)
        except CustomNodeMaterializationError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("source_folder_name")
    @classmethod
    def _validate_source_folder_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            return _safe_relative_posix_path(
                value,
                allow_nested=False,
                boundary=CustomNodeSourceBoundary.CUSTOM_NODE_PACKAGE_FOLDER,
            )
        except CustomNodeMaterializationError as exc:
            raise ValueError(str(exc)) from exc


class CustomNodeWorkspaceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["0.2.0"] = CUSTOM_NODE_WORKSPACE_MANIFEST_SCHEMA_VERSION
    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_variant_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    graph_node_types: list[str] = Field(default_factory=list)
    entries: list[CustomNodeWorkspaceEntry] = Field(default_factory=list)
    manifest_hash: str | None = None

    @field_validator("graph_node_types")
    @classmethod
    def _sort_unique_graph_node_types(cls, value: list[str]) -> list[str]:
        return sorted(set(value))

    @model_validator(mode="after")
    def _validate_unique_materialized_paths(self) -> CustomNodeWorkspaceManifest:
        seen: set[str] = set()
        for entry in self.entries:
            casefolded = entry.materialized_relative_path.casefold()
            if casefolded in seen:
                raise ValueError(
                    "Custom-node workspace entries must not use case-insensitively colliding paths."
                )
            seen.add(casefolded)
        return self


def with_computed_custom_node_workspace_hash(
    manifest: CustomNodeWorkspaceManifest,
) -> CustomNodeWorkspaceManifest:
    return manifest.model_copy(update={"manifest_hash": custom_node_workspace_manifest_hash(manifest)})


def custom_node_workspace_manifest_hash(manifest: CustomNodeWorkspaceManifest) -> str:
    return sha256_fingerprint(
        manifest.model_dump(mode="json", exclude_none=True, exclude={"manifest_hash"})
    )


def validate_custom_node_source_relative_paths(relative_paths: list[str]) -> None:
    """Validate archive/source path names before filesystem materialization."""
    path_index = MaterializedPathIndex()
    for relative_path in relative_paths:
        _safe_custom_node_internal_relative_path(
            relative_path,
            boundary=CustomNodeSourceBoundary.ARCHIVE_MEMBER_PATH,
        )
        try:
            path_index.add(relative_path)
        except PathSafetyError as exc:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.CASE_INSENSITIVE_PATH_COLLISION,
                f"Bundled custom-node source has a case-insensitive path collision: {relative_path}",
                boundary=CustomNodeSourceBoundary.ARCHIVE_MEMBER_PATH,
                relative_path=relative_path,
                reason=exc.reason,
                user_message="Noofy could not prepare one workflow extension safely.",
            ) from exc


class CustomNodeWorkspaceMaterializer:
    def __init__(
        self,
        *,
        runtime_profile_catalog_provider: Callable[[], object] | None = None,
        max_files: int = MAX_CUSTOM_NODE_FILES,
        max_bytes: int = MAX_CUSTOM_NODE_BYTES,
    ) -> None:
        self.runtime_profile_catalog_provider = runtime_profile_catalog_provider
        self.max_files = max_files
        self.max_bytes = max_bytes

    def build_manifest(
        self,
        *,
        capsule_lock: CapsuleLock,
        source_files_dir: Path | None,
        cached_source_dirs: dict[str, Path] | None = None,
        profile_selection: object | None = None,
    ) -> CustomNodeWorkspaceManifest:
        graph_node_types = _graph_node_types(source_files_dir)
        required_custom_nodes = _required_custom_nodes(capsule_lock.custom_nodes)
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
                cached_source_dirs=cached_source_dirs or {},
                package_trust_level=capsule_lock.trust.level,
            )
            for index, custom_node in enumerate(required_custom_nodes)
        ]
        _validate_unique_custom_node_package_destinations(entries)
        return with_computed_custom_node_workspace_hash(
            CustomNodeWorkspaceManifest(
                runtime_profile_id=capsule_lock.runtime.runtime_profile_id,
                runtime_profile_variant_id=capsule_lock.runtime.runtime_profile_variant_id,
                runtime_profile_manifest_hash=capsule_lock.runtime.runtime_profile_manifest_hash,
                graph_node_types=graph_node_types,
                entries=entries,
            )
        )

    def materialize(
        self,
        *,
        manifest: CustomNodeWorkspaceManifest,
        source_files_dir: Path | None,
        cached_source_dirs: dict[str, Path] | None = None,
        runner_workspace_dir: Path,
    ) -> None:
        custom_nodes_target = runner_workspace_dir / "custom_nodes"
        _validate_runner_custom_nodes_root(runner_workspace_dir, custom_nodes_target)
        _mkdir(custom_nodes_target, parents=True, exist_ok=True)
        transaction_dir = _create_materialization_transaction_dir(custom_nodes_target)
        staged_packages: list[_StagedCustomNodePackage] = []
        try:
            for package_index, entry in enumerate(manifest.entries):
                if (
                    entry.source_kind is CustomNodeSourceKind.BUNDLED_ARCHIVE
                    and source_files_dir is None
                ):
                    raise CustomNodeMaterializationError(
                        CustomNodeMaterializationErrorCode.CUSTOM_NODE_SOURCE_NOT_BUNDLED,
                        "Workflow requires bundled custom nodes, but no imported source files are available.",
                    )
                source_dir = _source_dir_for_entry(
                    source_files_dir,
                    entry,
                    cached_source_dirs or {},
                )
                target_dir = _runner_workspace_custom_node_destination(
                    runner_workspace_dir,
                    entry.materialized_relative_path,
                )
                staging_dir = transaction_dir / "p" / str(package_index)
                source_content_hash, _ = _stage_validated_source_tree(
                    source_dir,
                    staging_dir,
                    max_files=self.max_files,
                    max_bytes=self.max_bytes,
                )
                if source_content_hash != entry.source_content_hash:
                    raise CustomNodeMaterializationError(
                        CustomNodeMaterializationErrorCode.MISSING_RESOLVED_SOURCE_FACTS,
                        (
                            "Custom-node source changed after its workspace manifest "
                            f"was created: {entry.custom_node_package_id}"
                        ),
                        boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
                        relative_path=entry.source_folder_name or target_dir.name,
                        reason="source_content_hash_mismatch",
                        user_message="Noofy could not prepare one workflow extension safely.",
                        developer_details={
                            "expected_source_content_hash": entry.source_content_hash,
                            "actual_source_content_hash": source_content_hash,
                        },
                    )
                staged_packages.append(
                    _StagedCustomNodePackage(
                        target_dir=target_dir,
                        staging_dir=staging_dir,
                        backup_dir=transaction_dir / "b" / str(package_index),
                    )
                )

            staged_manifest_path = transaction_dir / "m.json"
            with open(_fs_path(staged_manifest_path), "w", encoding="utf-8") as file:
                file.write(manifest.model_dump_json(indent=2))
            _promote_staged_custom_node_workspace(
                staged_packages,
                staged_manifest_path=staged_manifest_path,
                manifest_path=(
                    runner_workspace_dir / CUSTOM_NODE_WORKSPACE_MANIFEST_FILENAME
                ),
                transaction_dir=transaction_dir,
            )
        finally:
            shutil.rmtree(transaction_dir, ignore_errors=True)

    def _entry_for_custom_node(
        self,
        custom_node: CustomNodeLock,
        *,
        import_order_index: int,
        source_files_dir: Path | None,
        cached_source_dirs: dict[str, Path],
        package_trust_level: TrustLevel,
    ) -> CustomNodeWorkspaceEntry:
        source_kind = _custom_node_source_kind(custom_node)
        if source_kind is CustomNodeSourceKind.NOOFY_CACHED_ARCHIVE:
            source_dir = _cached_source_dir_for_custom_node(custom_node, cached_source_dirs)
        else:
            source_dir = _find_custom_node_source_dir(source_files_dir, custom_node) if source_files_dir is not None else None
        if source_dir is None or not source_dir.is_dir():
            code = (
                CustomNodeMaterializationErrorCode.CUSTOM_NODE_SOURCE_NOT_CACHED
                if source_kind is CustomNodeSourceKind.NOOFY_CACHED_ARCHIVE
                else CustomNodeMaterializationErrorCode.MISSING_BUNDLED_SOURCE
            )
            raise CustomNodeMaterializationError(
                code,
                f"Source for custom node {custom_node.package_id} was not found.",
            )
        folder_name, source_folder_name, folder_name_remapped = _custom_node_target_folder_name(
            custom_node,
            source_dir,
            source_kind,
        )
        source_content_hash, dependency_marker_hashes = _source_tree_facts(
            source_dir,
            max_files=self.max_files,
            max_bytes=self.max_bytes,
        )
        return CustomNodeWorkspaceEntry(
            custom_node_package_id=custom_node.package_id,
            source_kind=source_kind or CustomNodeSourceKind.BUNDLED_ARCHIVE,
            source_ref=_custom_node_source_ref(custom_node),
            source_content_hash=source_content_hash,
            materialized_relative_path=f"custom_nodes/{folder_name}",
            source_folder_name=source_folder_name,
            import_order_index=import_order_index,
            dependency_marker_hashes=dependency_marker_hashes,
            package_trust_level=package_trust_level,
            policy_flags={
                "has_install_py": (source_dir / "install.py").exists() or (source_dir / "setup.py").exists(),
                "folder_name_remapped": folder_name_remapped,
            },
            node_types=sorted(custom_node.node_types),
        )


def _required_custom_nodes(
    custom_nodes: list[CustomNodeLock],
) -> list[CustomNodeLock]:
    required_custom_nodes = sorted(
        custom_nodes,
        key=lambda item: (item.package_id, item.source),
    )
    for custom_node in custom_nodes:
        if _custom_node_source_kind(custom_node) is None:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.UNSUPPORTED_SOURCE_KIND,
                f"Custom node {custom_node.package_id} does not use a supported Noofy source.",
            )
    return required_custom_nodes


def _custom_node_source_kind(custom_node: CustomNodeLock) -> CustomNodeSourceKind | None:
    if (
        custom_node.source == "bundled_archive"
        or custom_node.source == "bundled_from_creator_machine"
        or custom_node.source.startswith("bundled_archive:")
    ):
        return CustomNodeSourceKind.BUNDLED_ARCHIVE
    if custom_node.source_cache_ref is not None:
        if custom_node.source_ref is None or custom_node.source_content_hash is None:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.MISSING_RESOLVED_SOURCE_FACTS,
                f"Custom node {custom_node.package_id} is missing pinned source facts.",
            )
        return CustomNodeSourceKind.NOOFY_CACHED_ARCHIVE
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
    return _process_source_tree(
        source_dir,
        destination_dir=None,
        max_files=max_files,
        max_bytes=max_bytes,
    )


def _stage_validated_source_tree(
    source_dir: Path,
    staging_dir: Path,
    *,
    max_files: int,
    max_bytes: int,
) -> tuple[str, dict[str, str]]:
    if staging_dir.exists() or staging_dir.is_symlink():
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Custom-node staging destination already exists: {staging_dir.name}",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path=staging_dir.name,
            reason="staging_destination_exists",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    _mkdir(staging_dir, parents=True)
    return _process_source_tree(
        source_dir,
        destination_dir=staging_dir,
        max_files=max_files,
        max_bytes=max_bytes,
    )


def _process_source_tree(
    source_dir: Path,
    *,
    destination_dir: Path | None,
    max_files: int,
    max_bytes: int,
) -> tuple[str, dict[str, str]]:
    _validate_source_root(source_dir)
    entries: list[dict[str, Any]] = []
    dependency_marker_hashes: dict[str, str] = {}
    total_bytes = 0
    path_index = MaterializedPathIndex()
    file_count = 0

    def process_directory(directory: Path, relative_prefix: PurePosixPath | None) -> None:
        nonlocal file_count, total_bytes
        before = _safe_lstat(directory, relative_path=_relative_label(relative_prefix))
        if not stat.S_ISDIR(before.st_mode):
            _raise_unsupported_source_type(
                _relative_label(relative_prefix),
                reason="source_directory_changed",
            )
        try:
            scanner = os.scandir(directory)
        except OSError as exc:
            _raise_source_changed(_relative_label(relative_prefix), exc)
        with scanner:
            after = _safe_lstat(
                directory,
                relative_path=_relative_label(relative_prefix),
            )
            _require_same_source_object(
                before,
                after,
                relative_path=_relative_label(relative_prefix),
            )
            directory_entries = sorted(scanner, key=lambda item: item.name)

        for directory_entry in directory_entries:
            relative = (
                PurePosixPath(directory_entry.name)
                if relative_prefix is None
                else relative_prefix / directory_entry.name
            )
            relative_path = relative.as_posix()
            _safe_custom_node_internal_relative_path(relative_path)
            source_path = directory / directory_entry.name
            source_stat = _safe_lstat(source_path, relative_path=relative_path)
            is_directory = stat.S_ISDIR(source_stat.st_mode)
            try:
                path_index.add(relative_path, is_directory=is_directory)
            except PathSafetyError as exc:
                raise CustomNodeMaterializationError(
                    CustomNodeMaterializationErrorCode.CASE_INSENSITIVE_PATH_COLLISION,
                    (
                        "Bundled custom-node source has a case-insensitive path "
                        f"collision: {relative_path}"
                    ),
                    boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
                    relative_path=relative_path,
                    reason=exc.reason,
                    user_message="Noofy could not prepare one workflow extension safely.",
                ) from exc

            destination = (
                _contained_custom_node_destination(
                    destination_dir,
                    relative_path,
                )
                if destination_dir is not None
                else None
            )
            if is_directory:
                if destination is not None:
                    _mkdir(destination)
                process_directory(source_path, relative)
                continue
            if stat.S_ISLNK(source_stat.st_mode):
                raise CustomNodeMaterializationError(
                    CustomNodeMaterializationErrorCode.SYMLINK_ESCAPE,
                    (
                        "Bundled custom-node source contains an unsupported "
                        f"symlink: {relative_path}"
                    ),
                    boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
                    relative_path=relative_path,
                    reason="symlink",
                    user_message="Noofy could not prepare one workflow extension safely.",
                )
            if not stat.S_ISREG(source_stat.st_mode):
                _raise_unsupported_source_type(relative_path)

            file_count += 1
            if file_count > max_files:
                raise CustomNodeMaterializationError(
                    CustomNodeMaterializationErrorCode.OVERSIZED_SOURCE,
                    "Bundled custom-node source contains too many files.",
                    boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
                    relative_path=relative_path,
                    reason="oversized",
                    user_message="Noofy could not prepare one workflow extension safely.",
                )
            digest, size_bytes = _hash_and_optionally_copy_source_file(
                source_path,
                source_stat=source_stat,
                destination=destination,
                relative_path=relative_path,
                max_bytes=max_bytes - total_bytes,
            )
            total_bytes += size_bytes
            entries.append(
                {
                    "relative_path": relative_path,
                    "sha256": digest,
                    "size_bytes": size_bytes,
                }
            )
            if Path(relative_path).name in _DEPENDENCY_MARKER_FILENAMES:
                dependency_marker_hashes[relative_path] = digest

    process_directory(source_dir, None)
    return sha256_fingerprint({"kind": "custom_node_source_tree", "entries": entries}), dependency_marker_hashes


def _validate_source_root(source_dir: Path) -> None:
    source_stat = _safe_lstat(source_dir, relative_path=".")
    if stat.S_ISLNK(source_stat.st_mode):
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.SYMLINK_ESCAPE,
            f"Bundled custom-node source root must not be a symlink: {source_dir.name}",
            boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
            relative_path=".",
            reason="symlink",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    if not stat.S_ISDIR(source_stat.st_mode):
        _raise_unsupported_source_type(".", reason="source_root_not_directory")


def _safe_lstat(path: Path, *, relative_path: str) -> os.stat_result:
    try:
        return os.lstat(_fs_path(path))
    except OSError as exc:
        _raise_source_changed(relative_path, exc)


def _require_same_source_object(
    before: os.stat_result,
    after: os.stat_result,
    *,
    relative_path: str,
) -> None:
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or stat.S_IFMT(before.st_mode) != stat.S_IFMT(after.st_mode)
    ):
        _raise_source_changed(relative_path)


def _hash_and_optionally_copy_source_file(
    source_path: Path,
    *,
    source_stat: os.stat_result,
    destination: Path | None,
    relative_path: str,
    max_bytes: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    copied_bytes = 0
    destination_file = None
    try:
        source_file = open(_fs_path(source_path), "rb")
    except OSError as exc:
        _raise_source_changed(relative_path, exc)
    try:
        with source_file:
            opened_stat = os.fstat(source_file.fileno())
            _require_same_source_object(
                source_stat,
                opened_stat,
                relative_path=relative_path,
            )
            if not stat.S_ISREG(opened_stat.st_mode):
                _raise_unsupported_source_type(relative_path)
            if destination is not None:
                destination_file = open(_fs_path(destination), "xb")
            while True:
                chunk = source_file.read(1024 * 1024)
                if not chunk:
                    break
                copied_bytes += len(chunk)
                if copied_bytes > max_bytes:
                    raise CustomNodeMaterializationError(
                        CustomNodeMaterializationErrorCode.OVERSIZED_SOURCE,
                        "Bundled custom-node source is too large.",
                        boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
                        relative_path=relative_path,
                        reason="oversized",
                        user_message="Noofy could not prepare one workflow extension safely.",
                    )
                digest.update(chunk)
                if destination_file is not None:
                    destination_file.write(chunk)
            final_stat = os.fstat(source_file.fileno())
            _require_same_source_object(
                opened_stat,
                final_stat,
                relative_path=relative_path,
            )
            if (
                opened_stat.st_size != final_stat.st_size
                or opened_stat.st_mtime_ns != final_stat.st_mtime_ns
                or copied_bytes != final_stat.st_size
            ):
                _raise_source_changed(relative_path)
    finally:
        if destination_file is not None:
            destination_file.close()
    if destination is not None:
        os.chmod(_fs_path(destination), stat.S_IMODE(source_stat.st_mode))
        _preserve_file_timestamps(
            destination,
            atime_ns=source_stat.st_atime_ns,
            mtime_ns=source_stat.st_mtime_ns,
        )
    return "sha256:" + digest.hexdigest(), copied_bytes


def _preserve_file_timestamps(path: Path, *, atime_ns: int, mtime_ns: int) -> None:
    if os.utime in os.supports_follow_symlinks:
        os.utime(_fs_path(path), ns=(atime_ns, mtime_ns), follow_symlinks=False)
        return
    if path.is_symlink():
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.UNSUPPORTED_FILE_TYPE,
            "Bundled custom-node source materialized to an unsupported symlink.",
            boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
            relative_path=path.name,
            reason="symlink",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    os.utime(_fs_path(path), ns=(atime_ns, mtime_ns))


def _create_materialization_transaction_dir(custom_nodes_target: Path) -> Path:
    for _ in range(100):
        transaction_dir = custom_nodes_target / f".n{uuid4().hex[:8]}"
        try:
            _mkdir(transaction_dir, mode=0o700)
            return transaction_dir
        except FileExistsError:
            continue
    raise CustomNodeMaterializationError(
        CustomNodeMaterializationErrorCode.STAGING_FAILED,
        "Could not create a custom-node materialization staging directory.",
        user_message="Noofy could not prepare one workflow extension safely.",
    )


def _mkdir(
    path: Path,
    mode: int = 0o777,
    *,
    parents: bool = False,
    exist_ok: bool = False,
) -> None:
    if not parents:
        os.mkdir(_fs_path(path), mode)
        return
    existed = os.path.isdir(_fs_path(path))
    current = Path(path.anchor) if path.is_absolute() else Path()
    for part in path.parts[1:] if path.is_absolute() else path.parts:
        current = current / part
        try:
            os.mkdir(_fs_path(current), mode)
        except FileExistsError:
            if not os.path.isdir(_fs_path(current)):
                raise
    if existed and not exist_ok:
        raise FileExistsError(path)


def _replace(source: Path, target: Path) -> None:
    if os.name == "nt" and (len(str(source)) >= 240 or len(str(target)) >= 240):
        os.replace(_fs_path(source), _fs_path(target))
        return
    source.replace(target)


def _fs_path(path: Path) -> str:
    value = str(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    absolute = os.path.abspath(value)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute[2:]
    return "\\\\?\\" + absolute


def _raise_unsupported_source_type(
    relative_path: str,
    *,
    reason: str = "special_file",
) -> None:
    raise CustomNodeMaterializationError(
        CustomNodeMaterializationErrorCode.UNSUPPORTED_FILE_TYPE,
        f"Bundled custom-node source contains an unsupported file type: {relative_path}",
        boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
        relative_path=relative_path,
        reason=reason,
        user_message="Noofy could not prepare one workflow extension safely.",
    )


def _raise_source_changed(
    relative_path: str,
    exc: OSError | None = None,
) -> None:
    error = CustomNodeMaterializationError(
        CustomNodeMaterializationErrorCode.MISSING_RESOLVED_SOURCE_FACTS,
        f"Bundled custom-node source changed while Noofy was reading it: {relative_path}",
        boundary=CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
        relative_path=relative_path,
        reason="source_changed_during_materialization",
        user_message="Noofy could not prepare one workflow extension safely.",
    )
    if exc is None:
        raise error
    raise error from exc


def _relative_label(relative_path: PurePosixPath | None) -> str:
    return relative_path.as_posix() if relative_path is not None else "."


def _promote_staged_custom_node_workspace(
    staged_packages: list[_StagedCustomNodePackage],
    *,
    staged_manifest_path: Path,
    manifest_path: Path,
    transaction_dir: Path,
) -> None:
    promoted: list[_StagedCustomNodePackage] = []
    manifest_backup = transaction_dir / "backups" / manifest_path.name
    manifest_promoted = False
    try:
        for package in staged_packages:
            _validate_existing_package_destination(package.target_dir)
            _mkdir(package.backup_dir.parent, parents=True, exist_ok=True)
            if package.target_dir.exists():
                _replace(package.target_dir, package.backup_dir)
            promoted.append(package)
            _replace(package.staging_dir, package.target_dir)

        if manifest_path.is_symlink() or (
            manifest_path.exists() and not manifest_path.is_file()
        ):
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
                "Custom-node workspace manifest destination is unsafe.",
                boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
                relative_path=manifest_path.name,
                reason="manifest_destination_unsafe",
                user_message="Noofy could not prepare one workflow extension safely.",
            )
        _mkdir(manifest_backup.parent, parents=True, exist_ok=True)
        if manifest_path.exists():
            _replace(manifest_path, manifest_backup)
        _replace(staged_manifest_path, manifest_path)
        manifest_promoted = True
    except Exception as exc:
        if manifest_promoted and manifest_path.exists():
            manifest_path.unlink()
        if manifest_backup.exists():
            _replace(manifest_backup, manifest_path)
        for package in reversed(promoted):
            if package.target_dir.exists():
                shutil.rmtree(package.target_dir)
            if package.backup_dir.exists():
                _replace(package.backup_dir, package.target_dir)
        if isinstance(exc, CustomNodeMaterializationError):
            raise
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.WORKSPACE_PROMOTION_FAILED,
            "Custom-node workspace replacement could not be completed.",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path="custom_nodes",
            reason="atomic_promotion_failed",
            user_message="Noofy could not prepare one workflow extension safely.",
            developer_details={"exception_type": type(exc).__name__},
        ) from exc


def _validate_existing_package_destination(target_dir: Path) -> None:
    if target_dir.is_symlink():
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Custom-node workspace destination must not be a symlink: {target_dir.name}",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path=target_dir.name,
            reason="destination_symlink",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    if target_dir.exists() and not target_dir.is_dir():
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Custom-node workspace destination is not a directory: {target_dir.name}",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path=target_dir.name,
            reason="destination_not_directory",
            user_message="Noofy could not prepare one workflow extension safely.",
        )


def _source_dir_for_entry(
    source_files_dir: Path | None,
    entry: CustomNodeWorkspaceEntry,
    cached_source_dirs: dict[str, Path],
) -> Path:
    if entry.source_kind is CustomNodeSourceKind.NOOFY_CACHED_ARCHIVE:
        source_dir = cached_source_dirs.get(entry.custom_node_package_id)
        if source_dir is None:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.CUSTOM_NODE_SOURCE_NOT_CACHED,
                f"Cached source for custom node {entry.custom_node_package_id} was not found.",
            )
        return source_dir
    source_folder_name = entry.source_folder_name or entry.materialized_relative_path.removeprefix(
        "custom_nodes/"
    )
    return _custom_node_source_dir(source_files_dir, source_folder_name)


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
        return _safe_relative_posix_path(
            source.split(":", 1)[1],
            allow_nested=False,
            boundary=CustomNodeSourceBoundary.CUSTOM_NODE_PACKAGE_FOLDER,
        )
    return None


def _cached_source_dir_for_custom_node(
    custom_node: CustomNodeLock,
    cached_source_dirs: dict[str, Path],
) -> Path | None:
    return cached_source_dirs.get(custom_node.package_id)


def _custom_node_target_folder_name(
    custom_node: CustomNodeLock,
    source_dir: Path,
    source_kind: CustomNodeSourceKind | None,
) -> tuple[str, str | None, bool]:
    if source_kind is CustomNodeSourceKind.NOOFY_CACHED_ARCHIVE:
        return _safe_custom_node_package_folder_name(custom_node.package_id), None, False

    source_folder_name = _safe_relative_posix_path(
        source_dir.name,
        allow_nested=False,
        boundary=CustomNodeSourceBoundary.CUSTOM_NODE_PACKAGE_FOLDER,
    )
    try:
        return (
            _safe_custom_node_package_folder_name(source_folder_name),
            source_folder_name,
            False,
        )
    except CustomNodeMaterializationError as source_error:
        try:
            fallback_name = _safe_custom_node_package_folder_name(custom_node.package_id)
        except CustomNodeMaterializationError:
            raise source_error
        return fallback_name, source_folder_name, fallback_name != source_folder_name


def _custom_node_source_ref(custom_node: CustomNodeLock) -> str:
    if custom_node.source_cache_ref is not None and custom_node.source_ref is not None:
        return custom_node.source_ref
    return custom_node.source


def _normalized_source_dir_name(value: str) -> str:
    return value.replace("_", "-").casefold()


def _safe_relative_posix_path(
    value: str,
    *,
    allow_nested: bool,
    boundary: CustomNodeSourceBoundary,
) -> str:
    try:
        return safe_relative_posix_path(value, allow_nested=allow_nested)
    except PathSafetyError as exc:
        message = (
            f"Custom-node folder name must not be nested: {value}"
            if exc.reason == "nested_path"
            else f"Path is not a safe relative path: {value}"
        )
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            message,
            boundary=boundary,
            relative_path=value,
            reason=(
                "nested_package_folder"
                if exc.reason == "nested_path"
                else exc.reason
            ),
            user_message="Noofy could not prepare one workflow extension safely.",
        ) from exc


def _safe_custom_node_package_folder_name(value: str) -> str:
    value = _safe_relative_posix_path(
        value,
        allow_nested=False,
        boundary=CustomNodeSourceBoundary.CUSTOM_NODE_PACKAGE_FOLDER,
    )
    if value.casefold() in _PROTECTED_CUSTOM_NODE_FOLDER_NAMES:
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PROTECTED_PATH_SHADOWING,
            f"Custom-node package folder uses a protected runtime name: {value}",
            boundary=CustomNodeSourceBoundary.CUSTOM_NODE_PACKAGE_FOLDER,
            relative_path=value,
            reason="protected_package_folder",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    return value


def _safe_custom_node_internal_relative_path(
    value: str,
    *,
    boundary: CustomNodeSourceBoundary = CustomNodeSourceBoundary.CUSTOM_NODE_INTERNAL_PATH,
) -> str:
    return _safe_relative_posix_path(
        value,
        allow_nested=True,
        boundary=boundary,
    )


def _safe_custom_node_workspace_relative_path(value: str) -> str:
    value = _safe_relative_posix_path(
        value,
        allow_nested=True,
        boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
    )
    parts = PurePosixPath(value).parts
    if len(parts) != 2 or parts[0] != "custom_nodes":
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Custom-node workspace path must be custom_nodes/<package-folder>: {value}",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path=value,
            reason="invalid_workspace_path",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    _safe_custom_node_package_folder_name(parts[1])
    return value


def _validate_unique_custom_node_package_destinations(
    entries: list[CustomNodeWorkspaceEntry],
) -> None:
    seen: set[str] = set()
    for entry in entries:
        casefolded = entry.materialized_relative_path.casefold()
        if casefolded in seen:
            raise CustomNodeMaterializationError(
                CustomNodeMaterializationErrorCode.CASE_INSENSITIVE_PATH_COLLISION,
                "Custom-node packages have case-insensitively colliding workspace destinations.",
                boundary=CustomNodeSourceBoundary.CUSTOM_NODE_PACKAGE_FOLDER,
                relative_path=entry.materialized_relative_path,
                reason="collision",
                user_message="Noofy could not prepare one workflow extension safely.",
            )
        seen.add(casefolded)


def _validate_runner_custom_nodes_root(
    runner_workspace_dir: Path,
    custom_nodes_target: Path,
) -> None:
    if runner_workspace_dir.is_symlink():
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            "Runner workspace destination must not be a symlink.",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path=".",
            reason="destination_symlink",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    if custom_nodes_target.is_symlink():
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            "Runner custom_nodes destination must not be a symlink.",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path="custom_nodes",
            reason="destination_symlink",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    workspace_root = runner_workspace_dir.resolve()
    custom_nodes_root = custom_nodes_target.resolve(strict=False)
    if not path_is_within(workspace_root, custom_nodes_root):
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            "Runner custom_nodes destination escapes the runner workspace.",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path="custom_nodes",
            reason="destination_escape",
            user_message="Noofy could not prepare one workflow extension safely.",
        )


def _runner_workspace_custom_node_destination(
    runner_workspace_dir: Path,
    materialized_relative_path: str,
) -> Path:
    safe_relative_path = _safe_custom_node_workspace_relative_path(
        materialized_relative_path
    )
    custom_nodes_root = (runner_workspace_dir / "custom_nodes").resolve(strict=False)
    destination = runner_workspace_dir / safe_relative_path
    resolved_destination = destination.resolve(strict=False)
    if (
        not path_is_within(custom_nodes_root, resolved_destination)
        or resolved_destination == custom_nodes_root
    ):
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Custom-node workspace destination escapes its package root: {materialized_relative_path}",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path=materialized_relative_path,
            reason="destination_escape",
            user_message="Noofy could not prepare one workflow extension safely.",
        )
    return destination


def _contained_custom_node_destination(
    package_target_dir: Path,
    internal_relative_path: str,
) -> Path:
    safe_relative_path = _safe_custom_node_internal_relative_path(
        internal_relative_path
    )
    try:
        return contained_destination(package_target_dir, safe_relative_path)
    except PathSafetyError as exc:
        raise CustomNodeMaterializationError(
            CustomNodeMaterializationErrorCode.PATH_TRAVERSAL,
            f"Custom-node internal destination escapes its package root: {internal_relative_path}",
            boundary=CustomNodeSourceBoundary.RUNNER_WORKSPACE_DESTINATION,
            relative_path=internal_relative_path,
            reason=exc.reason,
            user_message="Noofy could not prepare one workflow extension safely.",
        ) from exc
