from .nodes import OptionalAudioInput, OptionalImageInput, OptionalVideoInput


WEB_DIRECTORY = "./web"

NODE_CLASS_MAPPINGS = {
    "OptionalAudioInput": OptionalAudioInput,
    "OptionalImageInput": OptionalImageInput,
    "OptionalVideoInput": OptionalVideoInput,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "OptionalAudioInput": "Optional Audio Input",
    "OptionalImageInput": "Optional Image Input",
    "OptionalVideoInput": "Optional Video Input",
}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
