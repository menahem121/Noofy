from __future__ import annotations

from typing import Any

from app.workflows.library import workflow_package_display_name
from app.workflows.package import WorkflowPackage


def infer_workflow_category(package: WorkflowPackage) -> str:
    name = f"{workflow_package_display_name(package)} {package.metadata.description}".casefold()
    combined = f"{name} {_graph_keyword_text(package.comfyui_graph)}"
    if "upscale" in combined or "esrgan" in combined:
        return "Upscaling"
    if "inpaint" in combined:
        return "Inpainting"
    if "outpaint" in combined:
        return "Outpainting"
    if "canny" in combined or "lineart" in combined:
        return "Canny / Line Control"
    if "depth" in combined:
        return "Depth Control"
    if "pose" in combined or "openpose" in combined:
        return "Pose Control"
    if "background" in combined and "remove" in combined:
        return "Background Removal"
    if "background" in combined:
        return "Background Replacement"
    if "restore" in combined or "restoration" in combined:
        return "Restoration"
    media_type_category = _infer_media_workflow_type_category(package)
    if media_type_category is not None:
        return media_type_category
    if any(input_def.control.startswith("load_image") for input_def in package.inputs):
        return "Img2img"
    return "Txt2img"


def _infer_media_workflow_type_category(package: WorkflowPackage) -> str | None:
    output_kinds = {
        (output.kind or output.type).casefold()
        for output in package.outputs
        if output.kind or output.type
    }
    input_controls = {input_def.control.casefold() for input_def in package.inputs}
    unresolved_input_kinds = {
        unresolved.expected_kind.casefold()
        for unresolved in package.unresolved_runtime_inputs
        if unresolved.expected_kind
    }
    has_image_input = (
        any(control.startswith("load_image") for control in input_controls)
        or "image" in unresolved_input_kinds
    )
    has_audio_input = "load_audio" in input_controls or "audio" in unresolved_input_kinds
    has_video_input = "load_video" in input_controls or "video" in unresolved_input_kinds

    if "audio" in output_kinds:
        if has_audio_input:
            return "audio2audio"
        return "txt2audio"
    if "video" in output_kinds:
        if has_video_input:
            return "vid2vid"
        if has_image_input:
            return "img2vid"
        return "txt2vid"
    if "3d" in output_kinds:
        if has_image_input:
            return "imgTo3D"
        return "txtTo3D"
    if "text" in output_kinds:
        if has_image_input:
            return "img2text"
        if has_audio_input:
            return "audio2txt"
        return "txt2txt"
    return None


def _graph_keyword_text(graph: dict[str, Any]) -> str:
    parts: list[str] = []
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        class_type = node.get("class_type")
        if isinstance(class_type, str):
            parts.append(class_type)
        meta = node.get("_meta")
        if isinstance(meta, dict):
            title = meta.get("title")
            if isinstance(title, str):
                parts.append(title)
    return " ".join(parts).casefold()
