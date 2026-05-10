from __future__ import annotations

from pathlib import Path

from app.runtime.profiles import load_runtime_profile_catalog
from app.workflows.import_capsule_lock import launch_config_hash, model_id
from app.workflows.package import RequiredModel


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
