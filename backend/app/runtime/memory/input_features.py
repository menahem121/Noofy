"""Conservative semantic extraction for model-bearing workflow selections."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

MEMORY_SIGNATURE_SCHEMA_VERSION = "0.1.0"


@dataclass(frozen=True)
class ModelBearingSelection:
    kind: str
    selection: str
    node_id: str
    node_type: str
    input_name: str
    source: str
    binding_source: str
    strength_model: float | None = None
    strength_clip: float | None = None

    @property
    def active(self) -> bool:
        strengths = [
            strength
            for strength in (self.strength_model, self.strength_clip)
            if strength is not None
        ]
        return not strengths or any(strength != 0 for strength in strengths)

    @property
    def effective_strength(self) -> float:
        strengths = [
            abs(strength)
            for strength in (self.strength_model, self.strength_clip)
            if strength is not None
        ]
        return max(strengths, default=1.0)

    def diagnostic_details(self) -> dict[str, Any]:
        details: dict[str, Any] = {
            "kind": self.kind,
            "selection": self.selection,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "input_name": self.input_name,
            "source": self.source,
            "binding_source": self.binding_source,
        }
        if self.kind == "lora":
            details.update(
                {
                    "active": self.active,
                    "effective_strength": self.effective_strength,
                    "strength_model": self.strength_model,
                    "strength_clip": self.strength_clip,
                }
            )
        return details

    def profile_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "selection": self.selection,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "input_name": self.input_name,
            "strength_model": self.strength_model,
            "strength_clip": self.strength_clip,
        }


@dataclass(frozen=True)
class ModelSelectionFeatures:
    selections: list[ModelBearingSelection] = field(default_factory=list)

    @property
    def selected_models(self) -> list[ModelBearingSelection]:
        return [selection for selection in self.selections if selection.kind != "lora"]

    @property
    def unique_selected_models(self) -> list[ModelBearingSelection]:
        unique: list[ModelBearingSelection] = []
        seen: set[tuple[str, str]] = set()
        for selection in self.selected_models:
            key = (selection.kind, selection.selection)
            if key in seen:
                continue
            seen.add(key)
            unique.append(selection)
        return unique

    @property
    def selected_loras(self) -> list[ModelBearingSelection]:
        return [selection for selection in self.selections if selection.kind == "lora"]

    @property
    def active_loras(self) -> list[ModelBearingSelection]:
        return [selection for selection in self.selected_loras if selection.active]

    @property
    def selected_model_count(self) -> int:
        return len(self.unique_selected_models)

    @property
    def selected_model_kinds(self) -> list[str]:
        return sorted({selection.kind for selection in self.selected_models})

    @property
    def lora_count(self) -> int:
        return len(self.active_loras)

    @property
    def lora_strength_total(self) -> float:
        return sum(selection.effective_strength for selection in self.active_loras)

    @property
    def empty(self) -> bool:
        return not self.selections

    def diagnostic_details(self) -> dict[str, Any]:
        return {
            "selected_model_count": self.selected_model_count,
            "selected_model_reference_count": len(self.selected_models),
            "selected_model_kinds": self.selected_model_kinds,
            "selected_models": [
                selection.diagnostic_details() for selection in self.selected_models
            ],
            "lora_count": self.lora_count,
            "lora_strength_total": self.lora_strength_total,
            "selected_loras": [
                selection.diagnostic_details() for selection in self.selected_loras
            ],
        }

    def profile_payload(self) -> list[dict[str, Any]]:
        return [
            selection.profile_payload()
            for selection in sorted(
                self.selections,
                key=lambda item: (
                    item.node_id,
                    item.input_name,
                    item.kind,
                    item.selection,
                ),
            )
        ]


@dataclass(frozen=True)
class MemorySignatureSet:
    process_compatibility_signature: str | None = None
    model_residency_signature: str | None = None
    execution_profile_signature: str | None = None
    process_compatibility_payload: dict[str, Any] = field(default_factory=dict)
    model_residency_payload: dict[str, Any] = field(default_factory=dict)
    execution_profile_payload: dict[str, Any] = field(default_factory=dict)

    def signature_fields(self) -> dict[str, str | None]:
        return {
            "process_compatibility_signature": self.process_compatibility_signature,
            "model_residency_signature": self.model_residency_signature,
            "execution_profile_signature": self.execution_profile_signature,
        }

    def diagnostic_details(self) -> dict[str, Any]:
        return {
            **self.signature_fields(),
            "payloads": {
                "process_compatibility": self.process_compatibility_payload,
                "model_residency": self.model_residency_payload,
                "execution_profile": self.execution_profile_payload,
            },
        }


def build_memory_signature_set(
    *,
    runner_process_compatibility_key: str | None,
    model_selections: ModelSelectionFeatures,
    execution_profile: Mapping[str, Any],
) -> MemorySignatureSet:
    process_payload = _process_compatibility_payload(runner_process_compatibility_key)
    model_payload = _model_residency_payload(model_selections)
    execution_payload = _execution_profile_payload(execution_profile)
    return MemorySignatureSet(
        process_compatibility_signature=_signature_for_payload(process_payload)
        if process_payload
        else None,
        model_residency_signature=_signature_for_payload(model_payload)
        if model_payload
        else None,
        execution_profile_signature=_signature_for_payload(execution_payload)
        if execution_payload
        else None,
        process_compatibility_payload=process_payload,
        model_residency_payload=model_payload,
        execution_profile_payload=execution_payload,
    )


def extract_model_selection_features(
    package: Any,
    submitted_inputs: Mapping[str, Any],
) -> ModelSelectionFeatures:
    """Extract coarse model-residency signals without claiming exact memory use."""
    resolved_inputs, binding_sources, considered_bindings = _resolved_graph_inputs(
        package,
        submitted_inputs,
    )
    required_by_binding = _required_models_by_binding(package)
    selections: list[ModelBearingSelection] = []
    seen: set[tuple[str, str, str, str]] = set()

    for node_id, node in (getattr(package, "comfyui_graph", {}) or {}).items():
        if not isinstance(node, dict):
            continue
        node_id = str(node_id)
        node_type = str(node.get("class_type") or "")
        node_inputs = resolved_inputs.get(node_id, {})
        for input_name, value in node_inputs.items():
            input_name = str(input_name)
            required_model = required_by_binding.get((node_id, input_name))
            kind = _model_selection_kind(
                node_type=node_type,
                input_name=input_name,
                required_model=required_model,
            )
            if kind is None:
                continue
            for selection_value in _selection_values(value):
                selection = _selection_record(
                    kind=kind,
                    selection=selection_value,
                    node_id=node_id,
                    node_type=node_type,
                    input_name=input_name,
                    source=binding_sources.get(
                        (node_id, input_name),
                        f"graph:{node_id}.{input_name}",
                    ),
                    binding_source=_binding_source_for(
                        binding_sources.get((node_id, input_name))
                    ),
                    node_inputs=node_inputs,
                )
                key = (node_id, input_name, kind, selection.selection)
                if key not in seen:
                    seen.add(key)
                    selections.append(selection)

    for index, required_model in enumerate(
        getattr(package, "required_models", []) or []
    ):
        node_id = str(getattr(required_model, "node_id", "") or "")
        input_name = str(getattr(required_model, "input_name", "") or "")
        if node_id and input_name and (node_id, input_name) in considered_bindings:
            continue
        kind = _model_selection_kind(
            node_type=str(getattr(required_model, "node_type", "") or ""),
            input_name=input_name,
            required_model=required_model,
        )
        filename = _selection_label(getattr(required_model, "filename", None))
        if kind is None or filename is None:
            continue
        if any(
            selection.kind == kind and selection.selection == filename
            for selection in selections
        ):
            continue
        key = (node_id, input_name, kind, filename)
        if key in seen:
            continue
        seen.add(key)
        selections.append(
            ModelBearingSelection(
                kind=kind,
                selection=filename,
                node_id=node_id,
                node_type=str(getattr(required_model, "node_type", "") or ""),
                input_name=input_name,
                source=f"required_model:{index}",
                binding_source="required_models",
            )
        )

    return ModelSelectionFeatures(selections=selections)


def _process_compatibility_payload(
    runner_process_compatibility_key: str | None,
) -> dict[str, Any]:
    if not runner_process_compatibility_key:
        return {}
    return {
        "schema_version": MEMORY_SIGNATURE_SCHEMA_VERSION,
        "runner_process_compatibility_key": runner_process_compatibility_key,
    }


def _model_residency_payload(
    model_selections: ModelSelectionFeatures,
) -> dict[str, Any]:
    if model_selections.empty:
        return {}
    models = sorted(
        [
            {
                "kind": selection.kind,
                "selection": selection.selection,
            }
            for selection in model_selections.unique_selected_models
        ],
        key=lambda item: (item["kind"], item["selection"]),
    )
    loras = sorted(
        [
            {
                "kind": selection.kind,
                "selection": selection.selection,
                "strength_model": selection.strength_model,
                "strength_clip": selection.strength_clip,
                "active": selection.active,
            }
            for selection in model_selections.selected_loras
        ],
        key=lambda item: (
            item["kind"],
            item["selection"],
            item["strength_model"] if item["strength_model"] is not None else -1,
            item["strength_clip"] if item["strength_clip"] is not None else -1,
        ),
    )
    return {
        "schema_version": MEMORY_SIGNATURE_SCHEMA_VERSION,
        "selected_models": models,
        "selected_loras": loras,
    }


def _execution_profile_payload(
    execution_profile: Mapping[str, Any],
) -> dict[str, Any]:
    fields = {
        key: execution_profile.get(key)
        for key in [
            "resolution_width",
            "resolution_height",
            "batch_size",
            "frame_count",
            "effective_batch_size",
            "workflow_type",
            "precision",
            "vram_mode",
        ]
        if execution_profile.get(key) is not None
    }
    if not fields:
        return {}
    return {
        "schema_version": MEMORY_SIGNATURE_SCHEMA_VERSION,
        **fields,
    }


def _signature_for_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _resolved_graph_inputs(
    package: Any,
    submitted_inputs: Mapping[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[tuple[str, str], str],
    set[tuple[str, str]],
]:
    resolved: dict[str, dict[str, Any]] = {}
    sources: dict[tuple[str, str], str] = {}
    considered_bindings: set[tuple[str, str]] = set()
    for node_id, node in (getattr(package, "comfyui_graph", {}) or {}).items():
        if not isinstance(node, dict):
            continue
        node_inputs = node.get("inputs")
        if not isinstance(node_inputs, dict):
            continue
        node_id = str(node_id)
        resolved[node_id] = dict(node_inputs)
        for input_name in node_inputs:
            binding = (node_id, str(input_name))
            considered_bindings.add(binding)
            sources[binding] = f"graph:{node_id}.{input_name}"

    for workflow_input, binding_source in _iter_exposed_inputs(package):
        binding = getattr(workflow_input, "binding", None)
        node_id = str(getattr(binding, "node_id", "") or "")
        input_name = str(getattr(binding, "input_name", "") or "")
        if not node_id or not input_name:
            continue
        considered_bindings.add((node_id, input_name))
        if node_id not in resolved:
            continue
        input_id = str(getattr(workflow_input, "id", "") or "")
        if input_id in submitted_inputs:
            value = submitted_inputs[input_id]
            source = f"input:{input_id}"
        elif getattr(workflow_input, "default", None) is not None:
            value = workflow_input.default
            source = f"default:{input_id}"
        elif input_name in resolved[node_id]:
            continue
        else:
            continue
        resolved[node_id][input_name] = value
        sources[(node_id, input_name)] = f"{source}|{binding_source}"

    return resolved, sources, considered_bindings


def _iter_exposed_inputs(package: Any):
    seen: set[tuple[str, str, str]] = set()
    collections = [
        (getattr(package, "inputs", []) or [], "workflow.inputs"),
        (
            getattr(getattr(package, "dashboard", None), "inputs", []) or [],
            "dashboard.inputs",
        ),
    ]
    for workflow_inputs, source in collections:
        for workflow_input in workflow_inputs:
            binding = getattr(workflow_input, "binding", None)
            key = (
                str(getattr(workflow_input, "id", "") or ""),
                str(getattr(binding, "node_id", "") or ""),
                str(getattr(binding, "input_name", "") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            yield workflow_input, source


def _required_models_by_binding(package: Any) -> dict[tuple[str, str], Any]:
    result: dict[tuple[str, str], Any] = {}
    for required_model in getattr(package, "required_models", []) or []:
        node_id = str(getattr(required_model, "node_id", "") or "")
        input_name = str(getattr(required_model, "input_name", "") or "")
        if node_id and input_name:
            result.setdefault((node_id, input_name), required_model)
    return result


def _selection_record(
    *,
    kind: str,
    selection: str,
    node_id: str,
    node_type: str,
    input_name: str,
    source: str,
    binding_source: str,
    node_inputs: Mapping[str, Any],
) -> ModelBearingSelection:
    return ModelBearingSelection(
        kind=kind,
        selection=selection,
        node_id=node_id,
        node_type=node_type,
        input_name=input_name,
        source=_value_source_for(source),
        binding_source=binding_source,
        strength_model=_optional_float(node_inputs.get("strength_model"))
        if kind == "lora"
        else None,
        strength_clip=_optional_float(node_inputs.get("strength_clip"))
        if kind == "lora"
        else None,
    )


def _model_selection_kind(
    *,
    node_type: str,
    input_name: str,
    required_model: Any | None,
) -> str | None:
    normalized_node = _normalize_name(node_type)
    normalized_input = _normalize_name(input_name)
    required_hint = _normalize_name(
        " ".join(
            [
                str(getattr(required_model, "folder", "") or ""),
                str(getattr(required_model, "model_type", "") or ""),
                str(getattr(required_model, "node_type", "") or ""),
            ]
        )
    )
    hints = " ".join([normalized_node, normalized_input, required_hint])

    if "lora" in hints:
        return "lora"
    if "controlnet" in hints or "control_net" in hints or "t2i_adapter" in hints:
        return "controlnet"
    if "ipadapter" in hints or "ip_adapter" in hints:
        return "ipadapter"
    if "refiner" in hints:
        return "refiner"
    if "vae" in hints:
        return "vae"
    if _looks_like_encoder_selector(normalized_input, hints):
        return "encoder"
    if "checkpoint" in hints or "ckpt" in hints:
        return "checkpoint"
    if _looks_like_generic_model_selector(normalized_input, normalized_node, required_hint):
        return "model"
    return None


def _looks_like_encoder_selector(input_name: str, hints: str) -> bool:
    return (
        input_name.startswith(("clip_name", "encoder_name", "text_encoder"))
        or input_name in {"clip", "encoder", "tokenizer_name"}
        or (
            any(token in hints for token in ("cliploader", "encoderloader", "text_encoder"))
            and input_name.endswith("_name")
        )
    )


def _looks_like_generic_model_selector(
    input_name: str,
    node_type: str,
    required_hint: str,
) -> bool:
    if input_name in {
        "model",
        "model_file",
        "model_name",
        "unet_name",
        "diffusion_model_name",
    }:
        return bool(required_hint) or any(
            token in node_type
            for token in ("loader", "model", "unet", "diffusion", "upscale")
        )
    return input_name.endswith("_model_name")


def _selection_values(value: Any) -> list[str]:
    if isinstance(value, str):
        label = _selection_label(value)
        return [label] if label is not None else []
    if isinstance(value, list) and not _looks_like_graph_link(value):
        return [
            label
            for item in value
            if (label := _selection_label(item)) is not None
        ]
    return []


def _selection_label(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.casefold() in {
        "none",
        "null",
        "disabled",
        "off",
        "no_lora",
    }:
        return None
    return normalized.replace("\\", "/").rsplit("/", maxsplit=1)[-1]


def _looks_like_graph_link(value: list[Any]) -> bool:
    return (
        len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _normalize_name(value: Any) -> str:
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(ch for ch in normalized if ch.isalnum() or ch == "_")


def _binding_source_for(source: str | None) -> str:
    if source is None or "|" not in source:
        return "graph"
    return source.rsplit("|", maxsplit=1)[-1]


def _value_source_for(source: str) -> str:
    return source.split("|", maxsplit=1)[0]
