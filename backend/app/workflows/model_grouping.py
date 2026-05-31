"""Group per-node required models into unique physical model files.

The capsule lock and normalized package keep one :class:`RequiredModel` record per
ComfyUI graph node that loads a model, so the same file can appear several times
(e.g. one checkpoint loaded by the diffusion, VAE, and text-encoder nodes). That is
faithful to the graph and needed for runtime input binding, but the user-facing model
summary, import preview, verification, and download plan should each treat a physical
file once.

This module computes a strong file-identity key, groups the per-node records by it,
and attaches the original node references back onto the grouped availability item so
developer details can still show every node that uses the file.

Dedup identity, strongest first:

1. ``comfyui_folder + filename + sha256 + size_bytes`` when the exporter verified
   the file (``sha256_size``).
2. ``comfyui_folder + filename + size_bytes`` when only a size is known. The folder is
   always part of the key so the same blob materialized into different ComfyUI folders
   stays distinct.
3. ``comfyui_folder + filename`` only when the verification level is already
   ``filename_only`` (no hash, no size). This is weak, so a merge of more than one node
   on this key is flagged uncertain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.artifacts import ModelVerificationLevel
from app.engine.models import RequiredModelAvailability, RequiredModelReference
from app.workflows.package import RequiredModel

# Dedup strengths, mirrored by the key shape produced in ``dedup_identity``.
DEDUP_SHA256_SIZE = "sha256_size"
DEDUP_FOLDER_FILENAME_SIZE = "folder_filename_size"
DEDUP_FILENAME_ONLY = "filename_only"


def _normalize_sha256(value: str) -> str:
    return value.split(":", 1)[1] if value.startswith("sha256:") else value


def required_model_reference_id(model: RequiredModel) -> str:
    """Stable per-node identity for a required model.

    Matches the ``requirement_id`` carried on grouped availability items and download
    progress events, so callers can map results back to the package.
    """
    if model.node_id and model.input_name:
        return f"{model.node_id}:{model.input_name}:{model.folder}/{model.filename}"
    return f"{model.folder}/{model.filename}"


def dedup_identity(model: RequiredModel) -> tuple[tuple[object, ...], str]:
    """Return ``(key, strength)`` used to group records by physical file."""
    folder = model.folder
    checksum = model.checksum
    size_bytes = model.size_bytes
    if (
        model.verification_level is ModelVerificationLevel.SHA256_SIZE
        and isinstance(checksum, str)
        and checksum.strip()
        and isinstance(size_bytes, int)
        and size_bytes > 0
    ):
        return (
            (
                DEDUP_SHA256_SIZE,
                folder,
                model.filename,
                _normalize_sha256(checksum),
                size_bytes,
            ),
            DEDUP_SHA256_SIZE,
        )
    if isinstance(size_bytes, int) and size_bytes > 0:
        return (
            (DEDUP_FOLDER_FILENAME_SIZE, folder, model.filename, size_bytes),
            DEDUP_FOLDER_FILENAME_SIZE,
        )
    return (
        (DEDUP_FILENAME_ONLY, folder, model.filename),
        DEDUP_FILENAME_ONLY,
    )


@dataclass
class ModelGroup:
    """A set of per-node records that resolve to the same physical file."""

    key: tuple[object, ...]
    strength: str
    members: list[RequiredModel] = field(default_factory=list)

    @property
    def representative(self) -> RequiredModel:
        """The record used to check availability and download the file once."""
        return self.members[0]

    @property
    def uncertain(self) -> bool:
        """True when >1 node was merged on the weak filename-only key."""
        return self.strength == DEDUP_FILENAME_ONLY and len(self.members) > 1


def group_required_models(models: list[RequiredModel]) -> list[ModelGroup]:
    """Group ``models`` by file identity, preserving first-seen order.

    The first record for each key becomes the group's representative, so the grouped
    availability keeps the same ``requirement_id`` order as the package.
    """
    by_key: dict[tuple[object, ...], ModelGroup] = {}
    ordered: list[ModelGroup] = []
    for model in models:
        key, strength = dedup_identity(model)
        group = by_key.get(key)
        if group is None:
            group = ModelGroup(key=key, strength=strength)
            by_key[key] = group
            ordered.append(group)
        group.members.append(model)
    return ordered


def unique_required_models(models: list[RequiredModel]) -> list[RequiredModel]:
    """Return one representative record for each unique runtime model target."""
    return [group.representative for group in group_required_models(models)]


def total_required_model_size_bytes(models: list[RequiredModel]) -> int:
    """Return the known byte total for unique runtime model targets."""
    return sum(model.size_bytes or 0 for model in unique_required_models(models))


def references_for(group: ModelGroup) -> list[RequiredModelReference]:
    """Every node reference behind a grouped file, in package order."""
    return [
        RequiredModelReference(
            requirement_id=required_model_reference_id(member),
            node_id=member.node_id,
            node_type=member.node_type,
            input_name=member.input_name,
        )
        for member in group.members
    ]


def apply_group_metadata(
    availability: RequiredModelAvailability, group: ModelGroup
) -> RequiredModelAvailability:
    """Attach a group's node references and dedup flags onto an availability item.

    The availability is computed once from the group's representative; this overlays the
    full reference list so a single card can report all nodes that use the file.
    """
    return availability.model_copy(
        update={
            "references": references_for(group),
            "reference_count": len(group.members),
            "dedup_uncertain": group.uncertain,
        }
    )
