from app.workflows.widget_metadata import normalize_comfyui_widget_metadata


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
