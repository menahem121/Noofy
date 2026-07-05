from app.artifacts import ModelVerificationLevel
from app.workflows.model_overrides import (
    WorkflowModelOverride,
    WorkflowModelOverrideStore,
    apply_model_overrides_to_required,
)
from app.workflows.package import WorkflowPackage


def _override(**updates):
    values = {
        "folder": "diffusion_models",
        "source_filename": "model-fp8.safetensors",
        "replacement_filename": "model-fp8-converted-for-mac.safetensors",
        "replacement_sha256": "a" * 64,
        "replacement_size_bytes": 2048,
        "target_dtype": "bf16",
        "origin": "converted",
        "created_at": "2026-07-05T00:00:00+00:00",
    }
    values.update(updates)
    return WorkflowModelOverride(**values)


def _package(workflow_id="wf-1"):
    return WorkflowPackage(
        metadata={"id": workflow_id, "name": "Test", "version": "0.1.0"},
        engine="comfyui",
        required_models=[
            {
                "folder": "diffusion_models",
                "filename": "model-fp8.safetensors",
                "checksum": "sha256:" + "f" * 64,
                "size_bytes": 1024,
                "verification_level": "sha256_size",
            },
            {"folder": "vae", "filename": "vae.safetensors"},
        ],
        comfyui_graph={},
    )


def test_store_round_trip_and_isolation(tmp_path):
    store = WorkflowModelOverrideStore(tmp_path / "model-overrides")
    assert store.overrides_for("wf-1") == []

    store.upsert("wf-1", _override())
    store.upsert("wf-2", _override(replacement_filename="other.safetensors"))

    assert store.overridden_model_keys("wf-1") == {("diffusion_models", "model-fp8.safetensors")}
    assert store.overrides_for("wf-1")[0].replacement_filename == "model-fp8-converted-for-mac.safetensors"
    assert store.overrides_for("wf-2")[0].replacement_filename == "other.safetensors"

    # Upsert replaces the record for the same (folder, source_filename).
    store.upsert("wf-1", _override(replacement_filename="new.safetensors"))
    overrides = store.overrides_for("wf-1")
    assert len(overrides) == 1
    assert overrides[0].replacement_filename == "new.safetensors"

    states = store.list_all()
    assert set(states) == {"wf-1", "wf-2"}

    store.remove("wf-1", "diffusion_models", "model-fp8.safetensors")
    assert store.overrides_for("wf-1") == []
    assert store.overrides_for("wf-2")  # untouched


def test_store_survives_corrupt_file(tmp_path):
    store = WorkflowModelOverrideStore(tmp_path / "model-overrides")
    store.upsert("wf-1", _override())
    path = next((tmp_path / "model-overrides").glob("*.json"))
    path.write_text("{corrupt", encoding="utf-8")
    assert store.overrides_for("wf-1") == []


def test_apply_overrides_swaps_required_model_identity():
    package = _package()
    effective = apply_model_overrides_to_required(package, [_override()])

    swapped = effective.required_models[0]
    assert swapped.filename == "model-fp8-converted-for-mac.safetensors"
    assert swapped.checksum == "sha256:" + "a" * 64
    assert swapped.size_bytes == 2048
    assert swapped.verification_level is ModelVerificationLevel.SHA256_SIZE
    assert swapped.source_urls == []
    assert swapped.source_url is None

    untouched = effective.required_models[1]
    assert untouched.filename == "vae.safetensors"

    # The raw package is never mutated.
    assert package.required_models[0].filename == "model-fp8.safetensors"


def test_apply_overrides_without_hash_downgrades_verification():
    package = _package()
    effective = apply_model_overrides_to_required(
        package,
        [_override(replacement_sha256=None, replacement_size_bytes=None)],
    )
    assert effective.required_models[0].verification_level is ModelVerificationLevel.FILENAME_ONLY


def test_apply_overrides_no_matches_returns_same_package():
    package = _package()
    assert apply_model_overrides_to_required(package, []) is package
    assert (
        apply_model_overrides_to_required(
            package,
            [_override(folder="loras", source_filename="unrelated.safetensors")],
        )
        is package
    )
