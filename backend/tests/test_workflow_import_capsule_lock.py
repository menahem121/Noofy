from __future__ import annotations

from pathlib import Path

from app.runtime.profiles import load_runtime_profile_catalog
from app.workflows.import_capsule_lock import launch_config_hash, model_id, model_locks_from_package
from app.workflows.package import RequiredModel, WorkflowMetadata, WorkflowPackage


def test_model_id_prefers_source_urls() -> None:
    model = RequiredModel(
        folder="checkpoints",
        filename="demo.safetensors",
        source_url="https://fallback.test/model.safetensors",
        source_urls=["https://primary.test/model.safetensors"],
    )

    assert model_id(model) == "https://primary.test/model.safetensors"


def test_launch_config_hash_includes_custom_node_set() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    variant = catalog.profiles[0].variants[0]

    first = launch_config_hash("comfyui", variant, "sha256:" + "a" * 64)
    second = launch_config_hash("comfyui", variant, "sha256:" + "b" * 64)

    assert first.startswith("sha256:")
    assert first != second


def test_launch_config_hash_includes_preview_size() -> None:
    catalog = load_runtime_profile_catalog(Path("app/runtime/profile_catalog.json"))
    variant = catalog.profiles[0].variants[0]
    smaller_preview_variant = variant.model_copy(
        deep=True,
        update={
            "launch_defaults": variant.launch_defaults.model_copy(
                update={"preview_size": 256}
            )
        },
    )

    first = launch_config_hash("comfyui", variant, "sha256:" + "a" * 64)
    second = launch_config_hash(
        "comfyui",
        smaller_preview_variant,
        "sha256:" + "a" * 64,
    )

    assert first != second


def test_model_locks_from_package_collapses_shared_model_references() -> None:
    def _node(node_id: str) -> RequiredModel:
        return RequiredModel(
            folder="checkpoints",
            filename="demo.safetensors",
            node_id=node_id,
            input_name="ckpt_name",
            checksum="sha256:" + "a" * 64,
            size_bytes=10,
            source_urls=["https://example.test/demo.safetensors"],
        )

    package = WorkflowPackage(
        metadata=WorkflowMetadata(id="shared-model", name="Shared Model", version="0.1.0"),
        engine="comfyui",
        required_models=[_node("1"), _node("2")],
        comfyui_graph={},
    )

    locks = model_locks_from_package(package)

    assert len(locks) == 1
    assert locks[0].filename == "demo.safetensors"
