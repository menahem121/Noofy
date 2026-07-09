import json
from pathlib import Path

import pytest

from app.runtime.fingerprints import FINGERPRINT_SCHEMA_VERSION
from app.runtime.profiles import (
    ActiveRuntimeProfileState,
    COMFYUI_SOURCE_MANIFEST_FILENAME,
    RuntimeProfileCatalog,
    RuntimeProfileErrorCode,
    RuntimeProfileResolutionError,
    RuntimeSourceOriginKind,
    RuntimeSourceStatus,
    RuntimeProfileVariant,
    assert_product_profile_source_allowed,
    build_comfyui_source_manifest,
    build_runtime_profile,
    load_runtime_profile_catalog,
    materialize_core_engine_source,
    materialized_core_engine_dir,
    resolve_runtime_profile,
)


def test_runtime_profile_catalog_loads_and_validates_manifest_hash() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))

    profile = catalog.profiles[0]
    assert catalog.schema_version == "0.1.0"
    assert catalog.fingerprint_schema_version == FINGERPRINT_SCHEMA_VERSION
    assert profile.runtime_profile_id == "noofy-comfyui-v1-default"
    assert profile.runtime_profile_manifest_hash == profile.computed_manifest_hash()
    assert profile.source_status is RuntimeSourceStatus.CLEAN_REPRODUCIBLE
    assert profile.comfyui_core_version == "v0.27.0"
    assert profile.comfyui_source_origin_kind is RuntimeSourceOriginKind.UPSTREAM_SOURCE_ARCHIVE
    assert profile.signed_manifest_reference is not None
    assert all(
        variant.launch_defaults.preview_method == "auto"
        and variant.launch_defaults.preview_size == 512
        for variant in profile.variants
    )


def test_active_runtime_profile_state_commits_validated_local_source_atomically(
    tmp_path: Path,
) -> None:
    bundled = _fake_clean_comfyui_source(tmp_path / "bundled")
    updated = _fake_clean_comfyui_source(tmp_path / "updated")
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    state = ActiveRuntimeProfileState(base_catalog=catalog, source_dir=bundled)

    pending = state.prepare_local_activation(
        comfyui_core_version="v9.9.9",
        comfyui_core_source_hash="sha256:" + ("9" * 64),
        source_reference="https://example.test/v9.9.9.zip",
        source_dir=updated,
    )

    assert state.source_dir() == bundled
    assert state.catalog() == catalog

    state.activate(pending)

    profile = state.catalog().profiles[0]
    assert state.source_dir() == updated
    assert profile.comfyui_core_version == "v9.9.9"
    assert profile.comfyui_core_source_hash == "sha256:" + ("9" * 64)
    assert profile.runtime_profile_manifest_hash == profile.computed_manifest_hash()
    assert profile.variants == catalog.profiles[0].variants


def test_resolve_runtime_profile_selects_supported_variant() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))

    selection = resolve_runtime_profile(
        catalog,
        runtime_profile_id="noofy-comfyui-v1-default",
        os_name="darwin",
        architecture="arm64",
        gpu_backend_profile="mps",
    )

    assert selection.variant.runtime_profile_variant_id == "darwin-arm64-mps"
    assert selection.variant.python_build_id == "cpython-3.13-noofy-v1"
    assert selection.variant.torch_version == "2.11.0"


def test_resolve_runtime_profile_rejects_macos_intel() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        resolve_runtime_profile(
            catalog,
            runtime_profile_id="noofy-comfyui-v1-default",
            os_name="darwin",
            architecture="x64",
            gpu_backend_profile="cpu",
        )

    assert exc.value.code is RuntimeProfileErrorCode.UNSUPPORTED_PROFILE_VARIANT


def test_resolve_runtime_profile_selects_linux_cuda_variant() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))

    selection = resolve_runtime_profile(
        catalog,
        runtime_profile_id="noofy-comfyui-v1-default",
        os_name="linux",
        architecture="x64",
        gpu_backend_profile="cuda",
    )

    assert selection.variant.runtime_profile_variant_id == "linux-x64-cuda130"
    assert selection.variant.python_build_id == "cpython-3.13-noofy-v1"
    assert selection.variant.torch_version == "2.11.0"
    assert selection.variant.torch_wheel_build_tag == "torch==2.11.0+cu130"


def test_missing_runtime_profile_fails_before_preparation() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        resolve_runtime_profile(
            catalog,
            runtime_profile_id="missing-profile",
            os_name="darwin",
            architecture="arm64",
        )

    assert exc.value.code is RuntimeProfileErrorCode.MISSING_RUNTIME_PROFILE


def test_unsupported_runtime_profile_variant_reports_policy_state() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        resolve_runtime_profile(
            catalog,
            runtime_profile_id="noofy-comfyui-v1-default",
            os_name="linux",
            architecture="arm64",
        )

    assert exc.value.code is RuntimeProfileErrorCode.UNSUPPORTED_PROFILE_VARIANT


def test_manifest_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    data = json.loads(Path("app/runtime/profile_catalog.json").read_text(encoding="utf-8"))
    data["profiles"][0]["comfyui_frontend_version"] = "changed-after-signing"
    path = tmp_path / "profile_catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(RuntimeProfileResolutionError) as exc:
        load_runtime_profile_catalog(path)

    assert exc.value.code is RuntimeProfileErrorCode.PROFILE_MANIFEST_HASH_MISMATCH


def test_catalog_rejects_duplicate_profile_and_variant_ids() -> None:
    data = json.loads(Path("app/runtime/profile_catalog.json").read_text(encoding="utf-8"))
    data["profiles"].append(data["profiles"][0])

    with pytest.raises(ValueError, match="Duplicate runtime_profile_id"):
        RuntimeProfileCatalog.model_validate(data).validate_integrity()

    data = json.loads(Path("app/runtime/profile_catalog.json").read_text(encoding="utf-8"))
    data["profiles"][0]["variants"].append(data["profiles"][0]["variants"][0])

    with pytest.raises(ValueError, match="Duplicate noofy-comfyui-v1-default.runtime_profile_variant_id"):
        RuntimeProfileCatalog.model_validate(data).validate_integrity()


def test_product_profile_generation_rejects_development_reference_source() -> None:
    with pytest.raises(ValueError, match="development ComfyUI source checkout"):
        assert_product_profile_source_allowed(
            source_origin_kind=RuntimeSourceOriginKind.DEVELOPMENT_REFERENCE_COPY,
            source_status=RuntimeSourceStatus.DEVELOPMENT_ONLY,
        )


def test_product_profile_generation_rejects_direct_vendored_source_path(tmp_path: Path) -> None:
    source_dir = tmp_path / "third_party" / "comfyui"

    with pytest.raises(ValueError, match="third_party/comfyui"):
        assert_product_profile_source_allowed(
            source_origin_kind=RuntimeSourceOriginKind.NOOFY_VENDORED_SNAPSHOT,
            source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
            source_dir=source_dir,
        )


def test_runtime_profile_builder_hashes_manifest_and_requires_product_signature(tmp_path: Path) -> None:
    source_dir = _fake_clean_comfyui_source(tmp_path / "source")
    source_manifest = build_comfyui_source_manifest(
        source_dir,
        comfyui_core_version="0.3.0",
        source_origin_kind=RuntimeSourceOriginKind.NOOFY_VENDORED_SNAPSHOT,
        source_reference="vendored-test",
        source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
        product_profile=True,
    )
    variant = RuntimeProfileVariant(
        runtime_profile_variant_id="darwin-arm64-mps",
        os="darwin",
        architecture="arm64",
        gpu_backend_profile="mps",
        python_version="3.11",
        python_build_id="cpython-3.11-noofy-product",
        torch_version="2.4.0",
        torch_wheel_build_tag="torch-2.4.0-mps",
        core_dependency_lock_hash="sha256:" + ("a" * 64),
        install_policy_version="core_only_no_community",
        readme_guidance=["Use the pinned product runtime."],
    )

    with pytest.raises(ValueError, match="signature or signed manifest reference"):
        build_runtime_profile(
            runtime_profile_id="noofy-comfyui-product",
            profile_family_id="noofy-comfyui-v1",
            source_manifest=source_manifest,
            comfyui_frontend_package_name="comfyui-frontend-package",
            comfyui_frontend_version="1.0.0",
            variants=[variant],
        )

    profile = build_runtime_profile(
        runtime_profile_id="noofy-comfyui-product",
        profile_family_id="noofy-comfyui-v1",
        source_manifest=source_manifest,
        comfyui_frontend_package_name="comfyui-frontend-package",
        comfyui_frontend_version="1.0.0",
        signed_manifest_reference="noofy-signatures/comfyui-product.json.sig",
        variants=[variant],
    )

    assert profile.runtime_profile_manifest_hash == profile.computed_manifest_hash()
    assert profile.comfyui_core_source_hash == source_manifest.source_hash
    assert profile.signed_manifest_reference == "noofy-signatures/comfyui-product.json.sig"


def test_materialized_core_engine_dir_is_runtime_store_relative() -> None:
    path = materialized_core_engine_dir(
        Path("/tmp/noofy/runtime-store"),
        comfyui_core_version="0.3.0",
        comfyui_core_source_hash="sha256:" + ("a" * 64),
    )

    assert path == Path("/tmp/noofy/runtime-store/core-engines/comfyui-core-0.3.0-aaaaaaaaaaaaaaaa")


def test_source_manifest_excludes_local_runtime_and_test_only_paths(tmp_path: Path) -> None:
    source_dir = _fake_comfyui_source(tmp_path / "source")
    (source_dir / "models" / "model.bin").write_text("local model", encoding="utf-8")
    (source_dir / "custom_nodes" / "node.py").write_text("community code", encoding="utf-8")
    (source_dir / "tests" / "test_runtime.py").write_text("test", encoding="utf-8")

    manifest = build_comfyui_source_manifest(
        source_dir,
        comfyui_core_version="0.3.0",
        source_origin_kind=RuntimeSourceOriginKind.DEVELOPMENT_REFERENCE_COPY,
        source_reference="third_party/comfyui",
        source_status=RuntimeSourceStatus.DEVELOPMENT_ONLY,
    )

    assert [entry.relative_path for entry in manifest.entries] == ["comfy/__init__.py", "main.py"]
    assert manifest.excluded_top_level_entries == ["custom_nodes", "models", "tests"]
    assert not any(str(tmp_path) in entry.relative_path for entry in manifest.entries)


def test_source_manifest_hash_is_stable_across_file_creation_order(tmp_path: Path) -> None:
    first_source = _fake_comfyui_source(tmp_path / "first")
    second_source = tmp_path / "second"
    (second_source / "comfy").mkdir(parents=True)
    (second_source / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    (second_source / "comfy" / "__init__.py").write_text("", encoding="utf-8")

    first = build_comfyui_source_manifest(
        first_source,
        comfyui_core_version="0.3.0",
        source_origin_kind=RuntimeSourceOriginKind.NOOFY_VENDORED_SNAPSHOT,
        source_reference="vendored-test",
        source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
    )
    second = build_comfyui_source_manifest(
        second_source,
        comfyui_core_version="0.3.0",
        source_origin_kind=RuntimeSourceOriginKind.NOOFY_VENDORED_SNAPSHOT,
        source_reference="vendored-test",
        source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
    )

    assert first.source_hash == second.source_hash


def test_product_source_manifest_rejects_dirty_or_ignored_runtime_folders(tmp_path: Path) -> None:
    source_dir = _fake_comfyui_source(tmp_path / "source")

    with pytest.raises(ValueError, match="dirty"):
        build_comfyui_source_manifest(
            source_dir,
            comfyui_core_version="0.3.0",
            source_origin_kind=RuntimeSourceOriginKind.NOOFY_VENDORED_SNAPSHOT,
            source_reference="vendored-test",
            source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
            product_profile=True,
            dirty_tree=True,
        )

    with pytest.raises(ValueError, match="ignored runtime folders"):
        build_comfyui_source_manifest(
            source_dir,
            comfyui_core_version="0.3.0",
            source_origin_kind=RuntimeSourceOriginKind.NOOFY_VENDORED_SNAPSHOT,
            source_reference="vendored-test",
            source_status=RuntimeSourceStatus.CLEAN_REPRODUCIBLE,
            product_profile=True,
        )


def test_materialize_core_engine_source_copies_manifest_entries_only(tmp_path: Path) -> None:
    source_dir = _fake_comfyui_source(tmp_path / "source")
    (source_dir / "models" / "model.bin").write_text("local model", encoding="utf-8")
    runtime_store_dir = tmp_path / "runtime-store"

    target_dir, manifest = materialize_core_engine_source(
        source_dir,
        runtime_store_dir,
        comfyui_core_version="0.3.0",
        source_origin_kind=RuntimeSourceOriginKind.DEVELOPMENT_REFERENCE_COPY,
        source_reference="third_party/comfyui",
        source_status=RuntimeSourceStatus.DEVELOPMENT_ONLY,
    )

    assert target_dir.parent == runtime_store_dir / "core-engines"
    assert (target_dir / "main.py").exists()
    assert (target_dir / "comfy" / "__init__.py").exists()
    assert not (target_dir / "models").exists()
    assert (target_dir / COMFYUI_SOURCE_MANIFEST_FILENAME).exists()
    assert manifest.source_hash.removeprefix("sha256:")[:16] in target_dir.name


def _fake_comfyui_source(source_dir: Path) -> Path:
    (source_dir / "comfy").mkdir(parents=True)
    (source_dir / "custom_nodes").mkdir()
    (source_dir / "models").mkdir()
    (source_dir / "tests").mkdir()
    (source_dir / "comfy" / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    return source_dir


def _fake_clean_comfyui_source(source_dir: Path) -> Path:
    (source_dir / "comfy").mkdir(parents=True)
    (source_dir / "comfy" / "__init__.py").write_text("", encoding="utf-8")
    (source_dir / "main.py").write_text("print('fake comfyui')\n", encoding="utf-8")
    return source_dir


def test_duplicate_runtime_data_files_stay_in_sync() -> None:
    """Both copies of app-owned runtime data files must stay byte-identical.

    Production loads app/runtime/profiles/profile_catalog.json; the
    app/runtime/ copy is referenced by validation tooling and tests. A drift
    between copies would make tooling validate a different runtime than the
    product ships.
    """
    for canonical, copy in (
        (
            Path("app/runtime/profiles/profile_catalog.json"),
            Path("app/runtime/profile_catalog.json"),
        ),
    ):
        assert json.loads(canonical.read_text(encoding="utf-8")) == json.loads(
            copy.read_text(encoding="utf-8")
        ), f"{canonical} and {copy} have drifted apart"
