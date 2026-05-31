from __future__ import annotations

from app.artifacts import ModelVerificationLevel
from app.workflows.model_grouping import (
    DEDUP_FILENAME_ONLY,
    DEDUP_FOLDER_FILENAME_SIZE,
    DEDUP_SHA256_SIZE,
    apply_group_metadata,
    dedup_identity,
    group_required_models,
    total_required_model_size_bytes,
    unique_required_models,
)
from app.workflows.package import RequiredModel


def _model(
    *,
    node_id: str,
    node_type: str,
    folder: str = "checkpoints",
    filename: str = "model.safetensors",
    checksum: str | None = None,
    size_bytes: int | None = None,
    level: ModelVerificationLevel = ModelVerificationLevel.SHA256_SIZE,
) -> RequiredModel:
    return RequiredModel(
        folder=folder,
        filename=filename,
        node_id=node_id,
        node_type=node_type,
        input_name="ckpt_name",
        checksum=checksum,
        size_bytes=size_bytes,
        verification_level=level,
    )


def test_same_file_across_nodes_collapses_to_one_group() -> None:
    sha = "sha256:28606c5b"
    models = [
        _model(node_id="221", node_type="LTXVAudioVAELoader", checksum=sha, size_bytes=29_000),
        _model(node_id="236", node_type="CheckpointLoaderSimple", checksum=sha, size_bytes=29_000),
        _model(node_id="243", node_type="LTXAVTextEncoderLoader", checksum=sha, size_bytes=29_000),
    ]

    groups = group_required_models(models)

    assert len(groups) == 1
    group = groups[0]
    assert len(group.members) == 3
    assert group.strength == DEDUP_SHA256_SIZE
    assert group.uncertain is False
    # First-seen node is the representative used for the single check/download.
    assert group.representative.node_id == "221"
    assert unique_required_models(models) == [models[0]]
    assert total_required_model_size_bytes(models) == 29_000


def test_distinct_files_stay_separate() -> None:
    models = [
        _model(node_id="1", node_type="CheckpointLoaderSimple", checksum="sha256:aaa", size_bytes=10),
        _model(
            node_id="2",
            node_type="LoraLoaderModelOnly",
            folder="loras",
            filename="lora.safetensors",
            checksum="sha256:bbb",
            size_bytes=20,
        ),
    ]

    assert len(group_required_models(models)) == 2


def test_same_filename_different_folder_is_not_merged() -> None:
    # A blob materialized into two ComfyUI folders is two distinct targets.
    models = [
        _model(node_id="1", node_type="A", folder="checkpoints", checksum="sha256:aaa", size_bytes=10),
        _model(node_id="2", node_type="B", folder="diffusion_models", checksum="sha256:aaa", size_bytes=10),
    ]

    groups = group_required_models(models)

    assert len(groups) == 2


def test_same_blob_with_different_target_filenames_is_not_merged() -> None:
    # Downloads are materialized at folder/filename, so each target path is required.
    models = [
        _model(node_id="1", node_type="A", filename="first.safetensors", checksum="sha256:aaa", size_bytes=10),
        _model(node_id="2", node_type="B", filename="second.safetensors", checksum="sha256:aaa", size_bytes=10),
    ]

    assert len(group_required_models(models)) == 2


def test_dedup_identity_priority_order() -> None:
    sha_model = _model(node_id="1", node_type="A", checksum="sha256:abc", size_bytes=99)
    size_model = _model(
        node_id="2",
        node_type="B",
        size_bytes=99,
        level=ModelVerificationLevel.FILENAME_SIZE,
    )
    name_model = _model(
        node_id="3",
        node_type="C",
        level=ModelVerificationLevel.FILENAME_ONLY,
    )

    assert dedup_identity(sha_model)[1] == DEDUP_SHA256_SIZE
    assert dedup_identity(sha_model)[0] == (
        DEDUP_SHA256_SIZE,
        "checkpoints",
        "model.safetensors",
        "abc",
        99,
    )
    assert dedup_identity(size_model)[1] == DEDUP_FOLDER_FILENAME_SIZE
    assert dedup_identity(name_model)[1] == DEDUP_FILENAME_ONLY


def test_filename_only_merge_is_flagged_uncertain() -> None:
    models = [
        _model(node_id="1", node_type="A", level=ModelVerificationLevel.FILENAME_ONLY),
        _model(node_id="2", node_type="B", level=ModelVerificationLevel.FILENAME_ONLY),
    ]

    group = group_required_models(models)[0]

    assert group.strength == DEDUP_FILENAME_ONLY
    assert group.uncertain is True


def test_apply_group_metadata_attaches_references() -> None:
    from app.engine.models import RequiredModelAvailability

    sha = "sha256:abc"
    models = [
        _model(node_id="221", node_type="LTXVAudioVAELoader", checksum=sha, size_bytes=10),
        _model(node_id="236", node_type="CheckpointLoaderSimple", checksum=sha, size_bytes=10),
    ]
    group = group_required_models(models)[0]
    base = RequiredModelAvailability(
        requirement_id="221:ckpt_name:checkpoints/model.safetensors",
        filename="model.safetensors",
        folder="checkpoints",
        verification_level=ModelVerificationLevel.SHA256_SIZE,
        status="available",
        status_label="Available",
        asset_ownership="external_reference",
    )

    enriched = apply_group_metadata(base, group)

    assert enriched.reference_count == 2
    assert [reference.node_id for reference in enriched.references] == ["221", "236"]
    assert enriched.references[1].node_type == "CheckpointLoaderSimple"
    assert enriched.dedup_uncertain is False
