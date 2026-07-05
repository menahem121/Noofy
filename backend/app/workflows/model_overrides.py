"""Per-installed-workflow local model overrides.

An override reroutes one required model of one installed workflow to a
replacement file on this machine (an fp8 checkpoint dequantized for Apple
Silicon, or a user-downloaded compatible variant). Overrides live in app data
and are never written into the portable workflow package, so exports keep the
original requirement.

Two read paths consume overrides:
- ``apply_model_overrides_to_required`` swaps required-model entries so
  availability/validation/inventory treat the replacement as the requirement.
- ``apply_model_overrides_to_graph`` patches the runtime-submitted prompt
  graph (a per-run deepcopy) so loader nodes receive the replacement filename.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.artifacts import ModelVerificationLevel
from app.workflows.fp8_compatibility import FP8_GRAPH_WEIGHT_DTYPE_VALUES
from app.workflows.graph_model_selectors import (
    folder_for_selector_input,
    looks_like_model_selector_input,
)
from app.workflows.package import WorkflowPackage


class WorkflowModelOverride(BaseModel):
    folder: str
    source_filename: str
    replacement_filename: str
    replacement_sha256: str | None = None
    replacement_size_bytes: int | None = None
    target_dtype: str | None = None
    origin: str = "converted"  # "converted" | "downloaded"
    created_at: str = ""


class WorkflowModelOverrideState(BaseModel):
    schema_version: int = 1
    workflow_id: str
    overrides: list[WorkflowModelOverride] = Field(default_factory=list)


class WorkflowModelOverrideStore:
    """JSON-per-workflow store under app data (workflow-store/model-overrides)."""

    def __init__(self, overrides_dir: Path) -> None:
        self._dir = overrides_dir

    def _path(self, workflow_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in workflow_id)
        return self._dir / f"{safe}.json"

    def get(self, workflow_id: str) -> WorkflowModelOverrideState:
        path = self._path(workflow_id)
        if not path.exists():
            return WorkflowModelOverrideState(workflow_id=workflow_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return WorkflowModelOverrideState.model_validate(data)
        except Exception:
            return WorkflowModelOverrideState(workflow_id=workflow_id)

    def overrides_for(self, workflow_id: str) -> list[WorkflowModelOverride]:
        return list(self.get(workflow_id).overrides)

    def overridden_model_keys(self, workflow_id: str) -> set[tuple[str, str]]:
        return {
            (override.folder, override.source_filename)
            for override in self.get(workflow_id).overrides
        }

    def upsert(self, workflow_id: str, override: WorkflowModelOverride) -> WorkflowModelOverrideState:
        state = self.get(workflow_id)
        kept = [
            existing
            for existing in state.overrides
            if (existing.folder, existing.source_filename)
            != (override.folder, override.source_filename)
        ]
        kept.append(override)
        updated = state.model_copy(update={"overrides": kept})
        self._write(updated)
        return updated

    def remove(self, workflow_id: str, folder: str, source_filename: str) -> WorkflowModelOverrideState:
        state = self.get(workflow_id)
        kept = [
            existing
            for existing in state.overrides
            if (existing.folder, existing.source_filename) != (folder, source_filename)
        ]
        updated = state.model_copy(update={"overrides": kept})
        if kept:
            self._write(updated)
        else:
            try:
                self._path(workflow_id).unlink()
            except FileNotFoundError:
                pass
        return updated

    def delete(self, workflow_id: str) -> None:
        try:
            self._path(workflow_id).unlink()
        except FileNotFoundError:
            return

    def list_all(self) -> dict[str, WorkflowModelOverrideState]:
        states: dict[str, WorkflowModelOverrideState] = {}
        if not self._dir.is_dir():
            return states
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                state = WorkflowModelOverrideState.model_validate(data)
            except Exception:
                continue
            states[state.workflow_id] = state
        return states

    def _write(self, state: WorkflowModelOverrideState) -> None:
        path = self._path(state.workflow_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(state.model_dump(), file, indent=2, sort_keys=True)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


def apply_model_overrides_to_required(
    package: WorkflowPackage,
    overrides: list[WorkflowModelOverride],
) -> WorkflowPackage:
    """Package copy whose overridden required models point at the replacements.

    Local-state reads only (availability, validation, inventory) — exporter
    and package-store paths must keep using the raw package.
    """
    if not overrides:
        return package
    by_source = {
        (override.folder, override.source_filename): override for override in overrides
    }
    changed = False
    required_models = []
    for model in package.required_models:
        override = by_source.get((model.folder, model.filename))
        if override is None:
            required_models.append(model)
            continue
        update: dict[str, Any] = {
            "filename": override.replacement_filename,
            "checksum": (
                f"sha256:{override.replacement_sha256}"
                if override.replacement_sha256
                else None
            ),
            "size_bytes": override.replacement_size_bytes,
        }
        if override.replacement_sha256 and override.replacement_size_bytes:
            update["verification_level"] = ModelVerificationLevel.SHA256_SIZE
        else:
            update["verification_level"] = ModelVerificationLevel.FILENAME_ONLY
        # The replacement is local-only; upstream source URLs describe the
        # original file and must not be used to (re)download the replacement.
        update["source_url"] = None
        update["source_urls"] = []
        required_models.append(model.model_copy(update=update))
        changed = True
    if not changed:
        return package
    return package.model_copy(update={"required_models": required_models})


@dataclass
class GraphOverridePatchReport:
    replaced: list[dict[str, str]] = field(default_factory=list)
    weight_dtype_patched: list[dict[str, str]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.replaced or self.weight_dtype_patched)

    def as_details(self) -> dict[str, Any]:
        return {
            "replaced": self.replaced,
            "weight_dtype_patched": self.weight_dtype_patched,
        }


def apply_model_overrides_to_graph(
    graph: dict[str, Any],
    package: WorkflowPackage,
    overrides: list[WorkflowModelOverride],
    *,
    force_default_weight_dtype: bool,
) -> GraphOverridePatchReport:
    """Patch a runtime prompt graph in place; the caller owns the graph copy.

    Selector targeting, most specific first:
    1. the (node_id, input_name) binding carried by the matching required model;
    2. loader tables mapping (node_type, input_name) to the override's folder;
    3. generic selector heuristic, only when the filename maps to exactly one
       override.
    """
    report = GraphOverridePatchReport()
    by_source = {
        (override.folder, override.source_filename): override for override in overrides
    }
    bound_targets: dict[tuple[str, str], WorkflowModelOverride] = {}
    for model in package.required_models:
        override = by_source.get((model.folder, model.filename))
        if override is not None and model.node_id and model.input_name:
            bound_targets[(model.node_id, model.input_name)] = override
    by_filename: dict[str, list[WorkflowModelOverride]] = {}
    for override in overrides:
        by_filename.setdefault(override.source_filename, []).append(override)

    for raw_node_id, node in graph.items():
        node_id = str(raw_node_id)
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        node_type = str(node.get("class_type") or "")
        for raw_input_name, value in list(inputs.items()):
            input_name = str(raw_input_name)
            if (
                force_default_weight_dtype
                and input_name == "weight_dtype"
                and value in FP8_GRAPH_WEIGHT_DTYPE_VALUES
            ):
                inputs[raw_input_name] = "default"
                report.weight_dtype_patched.append(
                    {"node_id": node_id, "node_type": node_type, "from": str(value)}
                )
                continue
            if not isinstance(value, str):
                continue
            candidates = by_filename.get(value)
            if not candidates:
                continue
            override = bound_targets.get((node_id, input_name))
            if override is None:
                mapped_folder = folder_for_selector_input(node_type, input_name)
                if mapped_folder is not None:
                    # Known loader input: only an override for its folder may
                    # apply — never fall back to the generic filename match.
                    folder_matches = [
                        candidate
                        for candidate in candidates
                        if candidate.folder == mapped_folder
                    ]
                    if len(folder_matches) == 1:
                        override = folder_matches[0]
                elif len(candidates) == 1 and looks_like_model_selector_input(
                    node_type, input_name
                ):
                    override = candidates[0]
            if override is None:
                continue
            inputs[raw_input_name] = override.replacement_filename
            report.replaced.append(
                {
                    "node_id": node_id,
                    "node_type": node_type,
                    "input_name": input_name,
                    "from": override.source_filename,
                    "to": override.replacement_filename,
                }
            )
    return report
