from __future__ import annotations

import json
from pathlib import Path

from app.runtime.profiles import (
    DEFAULT_RUNTIME_PROFILE_CATALOG_PATH,
    load_runtime_profile_catalog,
    resolve_runtime_profile,
)
from tools.validation.memory_governor_hardware_validation import (
    RUNNER_MEMORY_PROBE,
    WORKFLOW_ID,
    _empty_image_prompt,
    _write_validation_workflow,
)


def test_validation_workflow_uses_current_linux_cuda_profile(tmp_path: Path) -> None:
    _write_validation_workflow(tmp_path)

    workflow_dir = tmp_path / "workflows" / WORKFLOW_ID
    package = json.loads((workflow_dir / "package.json").read_text(encoding="utf-8"))
    capsule = json.loads((workflow_dir / "capsule.lock.json").read_text(encoding="utf-8"))
    selection = resolve_runtime_profile(
        load_runtime_profile_catalog(DEFAULT_RUNTIME_PROFILE_CATALOG_PATH),
        runtime_profile_id="noofy-comfyui-v1-default",
        runtime_profile_variant_id="linux-x64-cuda130",
    )

    assert package["dashboard"]["status"] == "configured"
    assert [item["id"] for item in package["inputs"]] == [
        "prompt",
        "seed",
        "width",
        "height",
        "batch_size",
    ]
    assert package["comfyui_graph"]["3"]["inputs"]["text"] == "CUDA validation prompt"
    assert package["outputs"] == [
        {
            "id": "image",
            "label": "Image",
            "node_id": "2",
            "type": "image",
        }
    ]
    assert capsule["runtime"]["python_version"] == selection.variant.python_version
    assert capsule["runtime"]["python_build_id"] == selection.variant.python_build_id
    assert (
        capsule["runtime"]["runtime_profile_manifest_hash"]
        == selection.profile.runtime_profile_manifest_hash
    )
    assert capsule["runtime"]["dependency_env_fingerprint"] != capsule["runtime"]["runner_fingerprint"]


def test_validation_allocator_probe_uses_runners_owned_wrapper() -> None:
    assert RUNNER_MEMORY_PROBE.is_file()
    assert RUNNER_MEMORY_PROBE.parts[-3:] == ("runtime", "runners", "runner_memory_probe.py")


def test_validation_smoke_prompt_stays_model_free() -> None:
    assert set(_empty_image_prompt()) == {"1", "2"}
