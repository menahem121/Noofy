"""Runtime profile catalog models and resolution helpers.

Runtime profiles are the Phase 5 contract between workflow capsules and the
actual ComfyUI runtime Noofy can prepare. The catalog is app-owned data; it is
not inferred from whatever development checkout happens to exist locally.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.runtime.fingerprints import FINGERPRINT_SCHEMA_VERSION, sha256_fingerprint

RUNTIME_PROFILE_CATALOG_SCHEMA_VERSION = "0.1.0"
COMFYUI_SOURCE_MANIFEST_FILENAME = "noofy-source-manifest.json"
DEFAULT_RUNTIME_PROFILE_CATALOG_PATH = Path(__file__).with_name("profile_catalog.json")
_PROFILE_MANIFEST_HASH_PLACEHOLDER = "sha256:" + ("0" * 64)
_IGNORED_COMFYUI_SOURCE_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "custom_nodes",
    "input",
    "models",
    "output",
    "temp",
    "tests",
    "tests-unit",
    "user",
}


class RuntimeProfileErrorCode(StrEnum):
    MISSING_RUNTIME_PROFILE = "missing_runtime_profile"
    UNSUPPORTED_PROFILE_VARIANT = "unsupported_profile_variant"
    PROFILE_MANIFEST_HASH_MISMATCH = "profile_manifest_hash_mismatch"
    UNSUPPORTED_FINGERPRINT_SCHEMA_VERSION = "unsupported_fingerprint_schema_version"


class RuntimeProfileResolutionError(RuntimeError):
    def __init__(self, code: RuntimeProfileErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeSourceOriginKind(StrEnum):
    UPSTREAM_GIT_TAG = "upstream_git_tag"
    UPSTREAM_SOURCE_ARCHIVE = "upstream_source_archive"
    NOOFY_VENDORED_SNAPSHOT = "noofy_vendored_snapshot"
    DEVELOPMENT_REFERENCE_COPY = "development_reference_copy"


class RuntimeSourceStatus(StrEnum):
    CLEAN_REPRODUCIBLE = "clean_reproducible"
    DEVELOPMENT_ONLY = "development_only"
    DIRTY = "dirty"


class RuntimeLaunchDefaults(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    preview_method: str = "auto"
    vram_mode: str = "auto"
    attention_backend: str = "auto"
    precision_policy: str = "auto"
    extra_model_paths_mode: str = "noofy_managed"
    noofy_environment: dict[str, str] = Field(default_factory=dict)


class RuntimeProfileVariant(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_profile_variant_id: str = Field(min_length=1)
    os: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    gpu_backend_profile: str = Field(min_length=1)
    python_version: str = Field(min_length=1)
    python_build_id: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    torch_wheel_build_tag: str = Field(min_length=1)
    core_dependency_lock_hash: str = Field(min_length=1)
    install_policy_version: str = Field(min_length=1)
    launch_defaults: RuntimeLaunchDefaults = Field(default_factory=RuntimeLaunchDefaults)
    readme_guidance: list[str] = Field(default_factory=list)


class RuntimeProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    runtime_profile_id: str = Field(min_length=1)
    runtime_profile_manifest_hash: str = Field(min_length=1)
    profile_family_id: str = Field(min_length=1)
    comfyui_core_version: str = Field(min_length=1)
    comfyui_core_source_hash: str = Field(min_length=1)
    comfyui_source_origin_kind: RuntimeSourceOriginKind
    comfyui_source_reference: str = Field(min_length=1)
    comfyui_source_manifest_hash: str = Field(min_length=1)
    source_status: RuntimeSourceStatus
    comfyui_frontend_package_name: str = Field(min_length=1)
    comfyui_frontend_version: str = Field(min_length=1)
    profile_signature: str | None = None
    signed_manifest_reference: str | None = None
    variants: list[RuntimeProfileVariant] = Field(min_length=1)

    @field_validator("runtime_profile_manifest_hash")
    @classmethod
    def _validate_manifest_hash_shape(cls, value: str) -> str:
        if not value.startswith("sha256:"):
            raise ValueError("runtime_profile_manifest_hash must use sha256: prefix")
        return value

    def computed_manifest_hash(self) -> str:
        return runtime_profile_manifest_hash(self)


class RuntimeProfileCatalog(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    fingerprint_schema_version: str = Field(min_length=1)
    profiles: list[RuntimeProfile] = Field(min_length=1)

    def profile_by_id(self, runtime_profile_id: str) -> RuntimeProfile | None:
        for profile in self.profiles:
            if profile.runtime_profile_id == runtime_profile_id:
                return profile
        return None

    def validate_integrity(self) -> None:
        if self.fingerprint_schema_version != FINGERPRINT_SCHEMA_VERSION:
            raise RuntimeProfileResolutionError(
                RuntimeProfileErrorCode.UNSUPPORTED_FINGERPRINT_SCHEMA_VERSION,
                "Runtime profile catalog uses an unsupported fingerprint schema version.",
            )
        _ensure_unique(
            [profile.runtime_profile_id for profile in self.profiles],
            "runtime_profile_id",
        )
        for profile in self.profiles:
            validate_runtime_profile_integrity(profile)
            expected = profile.computed_manifest_hash()
            if profile.runtime_profile_manifest_hash != expected:
                raise RuntimeProfileResolutionError(
                    RuntimeProfileErrorCode.PROFILE_MANIFEST_HASH_MISMATCH,
                    "Runtime profile manifest hash does not match catalog contents.",
                )


class RuntimeProfileSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    profile: RuntimeProfile
    variant: RuntimeProfileVariant


class RuntimeSourceManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relative_path: str = Field(min_length=1)
    sha256: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)


class RuntimeSourceManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(min_length=1)
    comfyui_core_version: str = Field(min_length=1)
    source_origin_kind: RuntimeSourceOriginKind
    source_reference: str = Field(min_length=1)
    source_status: RuntimeSourceStatus
    source_hash: str = Field(min_length=1)
    entries: list[RuntimeSourceManifestEntry] = Field(default_factory=list)
    excluded_top_level_entries: list[str] = Field(default_factory=list)


def runtime_profile_manifest_hash(profile: RuntimeProfile) -> str:
    """Hash the profile manifest excluding its self-referential hash field."""
    payload = profile.model_dump(mode="json", exclude={"runtime_profile_manifest_hash"})
    return sha256_fingerprint(
        {
            "schema_version": RUNTIME_PROFILE_CATALOG_SCHEMA_VERSION,
            "kind": "runtime_profile_manifest",
            "profile": payload,
        }
    )


def validate_runtime_profile_integrity(profile: RuntimeProfile) -> None:
    _ensure_unique(
        [variant.runtime_profile_variant_id for variant in profile.variants],
        f"{profile.runtime_profile_id}.runtime_profile_variant_id",
    )
    if (
        profile.source_status is RuntimeSourceStatus.CLEAN_REPRODUCIBLE
        and not profile.profile_signature
        and not profile.signed_manifest_reference
    ):
        raise ValueError("Product runtime profiles require a signature or signed manifest reference.")


def build_runtime_profile(
    *,
    runtime_profile_id: str,
    profile_family_id: str,
    source_manifest: RuntimeSourceManifest,
    comfyui_frontend_package_name: str,
    comfyui_frontend_version: str,
    variants: list[RuntimeProfileVariant],
    profile_signature: str | None = None,
    signed_manifest_reference: str | None = None,
) -> RuntimeProfile:
    profile = RuntimeProfile(
        runtime_profile_id=runtime_profile_id,
        runtime_profile_manifest_hash=_PROFILE_MANIFEST_HASH_PLACEHOLDER,
        profile_family_id=profile_family_id,
        comfyui_core_version=source_manifest.comfyui_core_version,
        comfyui_core_source_hash=source_manifest.source_hash,
        comfyui_source_origin_kind=source_manifest.source_origin_kind,
        comfyui_source_reference=source_manifest.source_reference,
        comfyui_source_manifest_hash=source_manifest.source_hash,
        source_status=source_manifest.source_status,
        comfyui_frontend_package_name=comfyui_frontend_package_name,
        comfyui_frontend_version=comfyui_frontend_version,
        profile_signature=profile_signature,
        signed_manifest_reference=signed_manifest_reference,
        variants=variants,
    )
    validate_runtime_profile_integrity(profile)
    return profile.model_copy(update={"runtime_profile_manifest_hash": profile.computed_manifest_hash()})


def load_runtime_profile_catalog(path: Path) -> RuntimeProfileCatalog:
    with path.open("r", encoding="utf-8") as file:
        catalog = RuntimeProfileCatalog.model_validate(json.load(file))
    catalog.validate_integrity()
    return catalog


def resolve_runtime_profile(
    catalog: RuntimeProfileCatalog,
    *,
    runtime_profile_id: str,
    runtime_profile_variant_id: str | None = None,
    os_name: str | None = None,
    architecture: str | None = None,
    gpu_backend_profile: str | None = None,
) -> RuntimeProfileSelection:
    catalog.validate_integrity()
    profile = catalog.profile_by_id(runtime_profile_id)
    if profile is None:
        raise RuntimeProfileResolutionError(
            RuntimeProfileErrorCode.MISSING_RUNTIME_PROFILE,
            "This workflow needs a runtime profile that is not available in this build.",
        )

    os_name = os_name or current_os_name()
    architecture = architecture or current_architecture()
    candidates = profile.variants
    if runtime_profile_variant_id is not None:
        candidates = [
            variant
            for variant in candidates
            if variant.runtime_profile_variant_id == runtime_profile_variant_id
        ]
    if gpu_backend_profile is not None:
        candidates = [
            variant for variant in candidates if variant.gpu_backend_profile == gpu_backend_profile
        ]

    for variant in candidates:
        if _matches_platform(variant, os_name=os_name, architecture=architecture):
            return RuntimeProfileSelection(profile=profile, variant=variant)

    raise RuntimeProfileResolutionError(
        RuntimeProfileErrorCode.UNSUPPORTED_PROFILE_VARIANT,
        "This workflow's runtime profile is not supported on this computer.",
    )


def current_os_name() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "windows"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def current_architecture() -> str:
    machine = platform.machine().lower()
    return {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "x64",
        "amd64": "x64",
    }.get(machine, machine)


def materialized_core_engine_dir(
    runtime_store_dir: Path,
    *,
    comfyui_core_version: str,
    comfyui_core_source_hash: str,
) -> Path:
    safe_hash = comfyui_core_source_hash.removeprefix("sha256:")[:16]
    return runtime_store_dir / "core-engines" / f"comfyui-core-{comfyui_core_version}-{safe_hash}"


def build_comfyui_source_manifest(
    source_dir: Path,
    *,
    comfyui_core_version: str,
    source_origin_kind: RuntimeSourceOriginKind,
    source_reference: str,
    source_status: RuntimeSourceStatus,
    product_profile: bool = False,
    dirty_tree: bool = False,
) -> RuntimeSourceManifest:
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"Noofy could not find the bundled ComfyUI engine files: {source_dir}")
    if not (source_dir / "main.py").is_file():
        raise ValueError(f"Noofy could not find the bundled ComfyUI engine entrypoint: {source_dir}")

    excluded_top_level_entries = _excluded_top_level_entries(source_dir)
    if product_profile:
        assert_product_profile_source_allowed(
            source_origin_kind=source_origin_kind,
            source_status=source_status,
            ignored_runtime_entries_present=bool(excluded_top_level_entries),
            dirty_tree=dirty_tree,
            source_dir=source_dir,
        )

    entries: list[RuntimeSourceManifestEntry] = []
    for path in _iter_source_files(source_dir):
        relative_path = path.relative_to(source_dir).as_posix()
        entries.append(
            RuntimeSourceManifestEntry(
                relative_path=relative_path,
                sha256=_sha256_file(path),
                size_bytes=path.stat().st_size,
            )
        )
    entries.sort(key=lambda entry: entry.relative_path)
    source_hash = sha256_fingerprint(
        {
            "schema_version": RUNTIME_PROFILE_CATALOG_SCHEMA_VERSION,
            "kind": "comfyui_source_manifest_entries",
            "entries": entries,
        }
    )
    return RuntimeSourceManifest(
        schema_version=RUNTIME_PROFILE_CATALOG_SCHEMA_VERSION,
        comfyui_core_version=comfyui_core_version,
        source_origin_kind=source_origin_kind,
        source_reference=source_reference,
        source_status=source_status,
        source_hash=source_hash,
        entries=entries,
        excluded_top_level_entries=excluded_top_level_entries,
    )


def materialize_core_engine_source(
    source_dir: Path,
    runtime_store_dir: Path,
    *,
    comfyui_core_version: str,
    source_origin_kind: RuntimeSourceOriginKind,
    source_reference: str,
    source_status: RuntimeSourceStatus,
    product_profile: bool = False,
    dirty_tree: bool = False,
) -> tuple[Path, RuntimeSourceManifest]:
    manifest = build_comfyui_source_manifest(
        source_dir,
        comfyui_core_version=comfyui_core_version,
        source_origin_kind=source_origin_kind,
        source_reference=source_reference,
        source_status=source_status,
        product_profile=product_profile,
        dirty_tree=dirty_tree,
    )
    target_dir = materialized_core_engine_dir(
        runtime_store_dir,
        comfyui_core_version=comfyui_core_version,
        comfyui_core_source_hash=manifest.source_hash,
    )
    manifest_path = target_dir / COMFYUI_SOURCE_MANIFEST_FILENAME
    if manifest_path.exists():
        existing = RuntimeSourceManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        if existing != manifest:
            raise ValueError(f"Existing materialized ComfyUI source manifest differs: {target_dir}")
        return target_dir, manifest

    staging_dir = runtime_store_dir / "transactions" / f"core-engine-{uuid4().hex}"
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    try:
        for entry in manifest.entries:
            source_path = source_dir / entry.relative_path
            target_path = staging_dir / entry.relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target_path)
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / COMFYUI_SOURCE_MANIFEST_FILENAME).write_text(
            manifest.model_dump_json(indent=2),
            encoding="utf-8",
        )
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir.replace(target_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return target_dir, manifest


def assert_product_profile_source_allowed(
    *,
    source_origin_kind: RuntimeSourceOriginKind,
    source_status: RuntimeSourceStatus,
    ignored_runtime_entries_present: bool = False,
    dirty_tree: bool = False,
    source_dir: Path | None = None,
) -> None:
    """Reject source identities that cannot back a product runtime profile."""
    if source_origin_kind is RuntimeSourceOriginKind.DEVELOPMENT_REFERENCE_COPY:
        raise ValueError("Product runtime profiles cannot be generated from a development ComfyUI source checkout.")
    if source_dir is not None and source_dir.name == "comfyui" and source_dir.parent.name == "third_party":
        raise ValueError("Product runtime profiles cannot be generated directly from third_party/comfyui.")
    if source_status is not RuntimeSourceStatus.CLEAN_REPRODUCIBLE:
        raise ValueError("Product runtime profiles require a clean reproducible source artifact.")
    if dirty_tree:
        raise ValueError("Product runtime profiles cannot be generated from dirty source trees.")
    if ignored_runtime_entries_present:
        raise ValueError("Product runtime source identity cannot include ignored runtime folders.")


def _matches_platform(
    variant: RuntimeProfileVariant,
    *,
    os_name: str,
    architecture: str,
) -> bool:
    return (
        variant.os in {os_name, "any"}
        and variant.architecture in {architecture, "any"}
    )


def _ensure_unique(values: list[str], field_name: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    if duplicates:
        raise ValueError(f"Duplicate {field_name} values: {', '.join(sorted(duplicates))}")


def _iter_source_files(source_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in source_dir.rglob("*"):
        relative_parts = path.relative_to(source_dir).parts
        if not relative_parts or any(part in _IGNORED_COMFYUI_SOURCE_PARTS for part in relative_parts):
            continue
        if path.is_file() and not path.is_symlink():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(source_dir).as_posix())


def _excluded_top_level_entries(source_dir: Path) -> list[str]:
    return sorted(
        entry.name
        for entry in source_dir.iterdir()
        if entry.name in _IGNORED_COMFYUI_SOURCE_PARTS
    )


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"
