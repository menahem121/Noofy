"""Shared heuristics for locating model-selector inputs in ComfyUI prompt graphs."""

from __future__ import annotations

from app.workflows.import_normalization import KNOWN_GRAPH_MODEL_SELECTOR_INPUTS

MODEL_SELECTOR_INPUT_NAMES = frozenset(
    {
        "ckpt_name",
        "clip_name",
        "clip_name1",
        "clip_name2",
        "clip_name3",
        "control_net_name",
        "diffusion_model_name",
        "lora_name",
        "model_name",
        "style_model_name",
        "unet_name",
        "vae_name",
    }
)


def looks_like_model_selector_input(node_type: str, input_name: str) -> bool:
    lowered_input = input_name.casefold()
    if lowered_input in MODEL_SELECTOR_INPUT_NAMES:
        return True
    return lowered_input.endswith("_name") and "loader" in node_type.casefold()


def folder_for_selector_input(node_type: str, input_name: str) -> str | None:
    mapped = KNOWN_GRAPH_MODEL_SELECTOR_INPUTS.get((node_type, input_name))
    return mapped[0] if mapped is not None else None
