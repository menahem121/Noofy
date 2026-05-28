from __future__ import annotations

import pytest

from app.workflows.import_normalization import (
    ImportNormalizationError,
    detect_unresolved_runtime_inputs,
    normalize_models,
    reject_unsupported_exported_launch_options,
)


def test_normalize_models_preserves_identity_evidence() -> None:
    models = normalize_models(
        {
            "models": [
                {
                    "comfyui_folder": "checkpoints",
                    "filename": "demo.safetensors",
                    "source_urls": ["https://example.test/demo.safetensors"],
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                }
            ]
        }
    )

    assert models[0].checksum == "sha256:" + "a" * 64
    assert models[0].identity_verified_by_exporter is True


def test_normalize_models_accepts_single_source_url_string() -> None:
    url = "https://example.test/demo.safetensors"

    models = normalize_models(
        {
            "models": [
                {
                    "comfyui_folder": "checkpoints",
                    "filename": "demo.safetensors",
                    "source_urls": url,
                    "sha256": "a" * 64,
                    "size_bytes": 123,
                }
            ]
        }
    )

    assert models[0].source_urls == [url]
    assert models[0].source_url == url


def test_detect_unresolved_runtime_inputs_finds_local_load_image_values() -> None:
    unresolved = detect_unresolved_runtime_inputs(
        {
            "1": {
                "class_type": "LoadImage",
                "inputs": {"image": "/Users/local/image.png"},
            },
            "2": {"class_type": "KSampler", "inputs": {}},
        }
    )

    assert len(unresolved) == 1
    assert unresolved[0].reason == "creator_local_image_not_bundled"


def test_reject_unsupported_exported_launch_options_reports_nested_keys() -> None:
    with pytest.raises(ImportNormalizationError, match="runtime.launch_options"):
        reject_unsupported_exported_launch_options(
            {"runtime": {"launch_options": {"listen": "0.0.0.0"}}}
        )
