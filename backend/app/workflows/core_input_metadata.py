"""App-owned input contracts for bundled ComfyUI media loaders.

These definitions let Dashboard Builder classify stable core upload nodes before
the managed ComfyUI runtime is available. Custom-node contracts still come from
portable package metadata or an isolated runner's ``object_info`` snapshot.
"""

from __future__ import annotations


_BUNDLED_INPUT_KINDS: dict[tuple[str, str], str] = {
    ("LoadImage", "image"): "image_input",
    ("LoadImageMask", "image"): "image_input",
    ("LoadImageOutput", "image"): "image_input",
    ("LoadAudio", "audio"): "audio_input",
    ("LoadVideo", "file"): "video_input",
    ("Load3D", "model_file"): "three_d_input",
    ("Load3DAnimation", "model_file"): "three_d_input",
}


def bundled_comfyui_input_kind(node_type: str, input_name: str) -> str | None:
    return _BUNDLED_INPUT_KINDS.get((node_type, input_name))
