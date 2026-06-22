from app.workflows.widget_metadata import (
    comfyui_widget_metadata_from_object_info,
    merge_comfyui_widget_metadata,
    normalize_comfyui_widget_metadata,
)


def test_normalize_comfyui_widget_metadata_omits_private_media_picker_choices() -> None:
    metadata = normalize_comfyui_widget_metadata(
        {
            "nodes": {
                "1": {
                    "inputs": {
                        "image": {
                            "options": ["creator-private.png", "another-private.png"],
                        }
                    }
                },
                "2": {
                    "inputs": {
                        "model_name": {
                            "options": ["base.safetensors", "refiner.safetensors"],
                        }
                    }
                },
            }
        },
        graph={
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": "creator-private.png"},
            },
            "2": {
                "class_type": "CustomImageModelSelector",
                "inputs": {"model_name": "base.safetensors"},
            },
        },
    )

    assert metadata == {
        "schema_version": "0.1.0",
        "nodes": {
            "2": {
                "inputs": {
                    "model_name": {
                        "options": ["base.safetensors", "refiner.safetensors"],
                    }
                }
            }
        },
    }


def test_normalize_comfyui_widget_metadata_preserves_semantic_input_contracts() -> None:
    metadata = normalize_comfyui_widget_metadata(
        {
            "nodes": {
                "8": {
                    "inputs": {
                        "payload": {
                            "input_type": " COMBO ",
                            "audio_upload": True,
                            "options": ["private.wav"],
                            "tooltip": " Choose audio. ",
                        },
                        "preview": {"input_type": " AUDIO_UI "},
                    }
                }
            }
        },
        graph={
            "8": {
                "class_type": "ArbitraryFutureNode",
                "inputs": {"payload": "", "preview": ""},
            }
        },
    )

    assert metadata == {
        "schema_version": "0.1.0",
        "nodes": {
            "8": {
                "inputs": {
                    "payload": {
                        "input_type": "COMBO",
                        "audio_upload": True,
                        "tooltip": "Choose audio.",
                    },
                    "preview": {"input_type": "AUDIO_UI"},
                }
            }
        },
    }


def test_object_info_snapshot_is_portable_and_merges_exported_labels() -> None:
    graph = {
        "8": {
            "class_type": "ArbitraryFutureNode",
            "inputs": {"payload": "", "preview": "", "style": "cinematic"},
        }
    }
    discovered = comfyui_widget_metadata_from_object_info(
        graph,
        {
            "ArbitraryFutureNode": {
                "input": {
                    "required": {
                        "payload": [
                            ["", "private.wav"],
                            {"audio_upload": True, "tooltip": "Choose audio."},
                        ],
                        "preview": ["AUDIO_UI", {}],
                        "style": [["cinematic", "natural"], {}],
                    }
                },
                "output": ["AUDIO"],
            }
        },
    )
    merged = merge_comfyui_widget_metadata(
        {
            "nodes": {
                "8": {
                    "inputs": {
                        "payload": {"display_name": "Source audio"},
                    }
                }
            }
        },
        discovered,
        graph=graph,
    )

    assert merged == {
        "schema_version": "0.1.0",
        "nodes": {
            "8": {
                "outputs": ["AUDIO"],
                "inputs": {
                    "payload": {
                        "input_type": "COMBO",
                        "input_group": "required",
                        "audio_upload": True,
                        "tooltip": "Choose audio.",
                        "display_name": "Source audio",
                    },
                    "preview": {
                        "input_type": "AUDIO_UI",
                        "input_group": "required",
                    },
                    "style": {
                        "input_type": "COMBO",
                        "input_group": "required",
                        "options": ["cinematic", "natural"],
                    },
                },
            }
        },
    }
