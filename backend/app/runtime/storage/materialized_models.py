from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from app.diagnostics import DiagnosticsSink

MATERIALIZATION_MANIFEST_NAME = ".noofy-materialization.json"
MATERIALIZED_VIEWS_DIRNAME = "views"
MODEL_ASSET_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".h5",
    ".msgpack",
    ".onnx",
    ".pb",
    ".pickle",
    ".pkl",
    ".pt",
    ".pth",
    ".safetensors",
    ".tflite",
}


def record_workflow_runtime_model_bundles(
    materialized_dir: Path,
    *,
    workflow_id: str,
    capsule_fingerprint: str | None = None,
    log_store: DiagnosticsSink | None = None,
) -> list[Path]:
    """Record workflow ownership for custom-node self-installed model bundles.

    Community nodes can download model folders directly under the runner-visible
    materialized model root. Noofy must stamp those folders after observing a
    workflow use them so GC can remove them once no installed workflow still
    references them.
    """
    if not materialized_dir.is_dir():
        return []
    stamped: list[Path] = []
    for bundle_dir in sorted(
        (path for path in materialized_dir.iterdir() if path.is_dir()),
        key=lambda path: path.name.casefold(),
    ):
        if bundle_dir.name == MATERIALIZED_VIEWS_DIRNAME:
            continue
        if not _contains_model_asset(bundle_dir):
            continue
        if _write_workflow_materialization_manifest(
            bundle_dir,
            workflow_id=workflow_id,
            capsule_fingerprint=capsule_fingerprint,
        ):
            stamped.append(bundle_dir)
    if stamped and log_store is not None:
        log_store.add(
            "info",
            "Workflow-installed model bundles recorded",
            "runtime.storage_gc",
            workflow_id=workflow_id,
            details={
                "bundle_paths": [str(path) for path in stamped],
                "capsule_fingerprint": capsule_fingerprint,
            },
        )
    return stamped


def _contains_model_asset(path: Path) -> bool:
    for child in path.rglob("*"):
        if child.is_file() and child.suffix.casefold() in MODEL_ASSET_SUFFIXES:
            return True
    return False


def _write_workflow_materialization_manifest(
    bundle_dir: Path,
    *,
    workflow_id: str,
    capsule_fingerprint: str | None,
) -> bool:
    manifest_path = bundle_dir / MATERIALIZATION_MANIFEST_NAME
    existing = _read_json(manifest_path)
    workflows = _string_set(existing.get("referenced_workflows"))
    capsules = _string_set(existing.get("capsule_fingerprints"))
    changed = workflow_id not in workflows
    workflows.add(workflow_id)
    if capsule_fingerprint:
        changed = changed or capsule_fingerprint not in capsules
        capsules.add(capsule_fingerprint)
    now = datetime.now(UTC).isoformat()
    manifest = {
        **existing,
        "schema_version": existing.get("schema_version") or "2026-06-26",
        "owner": "noofy",
        "artifact_type": "workflow_installed_model_bundle",
        "rebuildable": True,
        "created_by": existing.get("created_by") or "workflow_runtime_observation",
        "referenced_workflows": sorted(workflows),
        "capsule_fingerprints": sorted(capsules),
        "updated_at": now,
    }
    if "created_at" not in manifest:
        manifest["created_at"] = now
    if not changed and existing == manifest:
        return False
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return True


def _string_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if not isinstance(value, Iterable):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def _read_json(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
