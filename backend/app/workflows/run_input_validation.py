from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from app.engine.models import RunUserFixableError, WorkflowValidationResult
from app.workflows.media_values import (
    MEDIA_LOAD_CONTROLS,
    is_gallery_media_reference,
    is_package_asset_value,
    target_media_kind_for_input,
)
from app.workflows.package import DashboardControl, WorkflowInput, WorkflowPackage

_TEXT_CONTROLS = frozenset({"string_field", "textarea"})
_NUMERIC_CONTROLS = frozenset({"slider", "int_field", "seed_widget"})
_SELECT_CONTROLS = frozenset({"select", "lora_loader"})
_KNOWN_COMFYUI_MISSING_INPUT_MARKERS = (
    "prompt_outputs_failed_validation",
    "nonetype",
    "endswith",
)
_RAW_MARKER_LIMIT = 1200
_RunUserFixableCode = Literal["missing_required_input", "invalid_input_value"]


def validate_run_inputs(
    package: WorkflowPackage,
    submitted_inputs: dict[str, Any],
) -> list[RunUserFixableError]:
    """Validate dashboard-owned run inputs before engine submission."""

    controls_by_input = _dashboard_controls_by_input(package)
    user_errors: list[RunUserFixableError] = []
    for workflow_input in package.inputs:
        if workflow_input.control == "api_credential":
            continue
        control = controls_by_input.get(workflow_input.id)
        required = _input_is_required(workflow_input, control)
        value = _effective_input_value(workflow_input, submitted_inputs)
        if required and _is_missing_required_value(workflow_input, value):
            user_errors.append(
                _user_error(
                    code="missing_required_input",
                    workflow_input=workflow_input,
                    control=control,
                    value=value,
                )
            )
            continue

        invalid_reason = _invalid_value_reason(workflow_input, value)
        if invalid_reason is not None:
            user_errors.append(
                _user_error(
                    code="invalid_input_value",
                    workflow_input=workflow_input,
                    control=control,
                    value=value,
                    invalid_reason=invalid_reason,
                )
            )
    return user_errors


def validation_result_for_user_errors(
    package: WorkflowPackage,
    user_errors: list[RunUserFixableError],
) -> WorkflowValidationResult:
    return WorkflowValidationResult(
        workflow_id=package.metadata.id,
        valid=False,
        errors=[error.message for error in user_errors],
        user_errors=user_errors,
    )


def map_comfyui_submission_validation_error(
    *,
    package: WorkflowPackage,
    submitted_inputs: dict[str, Any],
    status_code: int,
    response_json: Any,
    response_text: str,
) -> RunUserFixableError | None:
    if status_code != 400:
        return None
    combined = _combined_error_text(response_json, response_text).lower()
    if not all(marker in combined for marker in _KNOWN_COMFYUI_MISSING_INPUT_MARKERS):
        return None

    node_id, input_name = _known_comfyui_node_input(response_json, response_text)
    workflow_input = _input_for_node(package, node_id, input_name)
    if workflow_input is None:
        workflow_input = _first_missing_media_input(package, submitted_inputs)
    if workflow_input is None:
        return None
    control = _dashboard_controls_by_input(package).get(workflow_input.id)
    marker_text = _truncate_marker(_combined_error_text(response_json, response_text))
    return _user_error(
        code="missing_required_input",
        workflow_input=workflow_input,
        control=control,
        value=_effective_input_value(workflow_input, submitted_inputs),
        developer_details={
            "node_id": node_id or workflow_input.binding.node_id,
            "input_name": input_name or workflow_input.binding.input_name,
            "engine_error": "prompt_outputs_failed_validation",
            "engine_status_code": status_code,
            "raw_validation_markers": marker_text,
        },
    )


def _dashboard_controls_by_input(package: WorkflowPackage) -> dict[str, DashboardControl]:
    controls: dict[str, DashboardControl] = {}
    for section in package.dashboard.sections:
        for control in section.controls:
            if control.input_id and control.input_id not in controls:
                controls[control.input_id] = control
    return controls


def _input_is_required(workflow_input: WorkflowInput, control: DashboardControl | None) -> bool:
    return workflow_input.validation.get("required") is True or (control.required if control is not None else False)


def _effective_input_value(workflow_input: WorkflowInput, submitted_inputs: dict[str, Any]) -> Any:
    return submitted_inputs[workflow_input.id] if workflow_input.id in submitted_inputs else workflow_input.default


def _is_missing_required_value(workflow_input: WorkflowInput, value: Any) -> bool:
    if value is None:
        return True
    if workflow_input.control in _TEXT_CONTROLS:
        return isinstance(value, str) and value.strip() == ""
    if workflow_input.control in MEDIA_LOAD_CONTROLS:
        return _is_empty_media_value(value)
    if workflow_input.control in _SELECT_CONTROLS:
        return isinstance(value, str) and value.strip() == ""
    if workflow_input.control in _NUMERIC_CONTROLS:
        return value == "" or not _finite_number(value)
    if isinstance(value, str):
        return value.strip() == ""
    return False


def _is_empty_media_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, dict):
        if value.get("source") in {"gallery", "package_asset"}:
            return False
        return not (
            is_gallery_media_reference(value)
            or is_package_asset_value(value)
            or bool(value.get("asset_id"))
            or bool(value.get("filename"))
        )
    return True


def _invalid_value_reason(workflow_input: WorkflowInput, value: Any) -> str | None:
    if value is None or value == "":
        return None
    if workflow_input.control in MEDIA_LOAD_CONTROLS and isinstance(value, dict):
        if value.get("source") == "gallery" and not is_gallery_media_reference(value):
            return "invalid_media_reference"
        if value.get("source") == "package_asset" and not is_package_asset_value(value):
            return "invalid_media_reference"
        return None
    if workflow_input.control in MEDIA_LOAD_CONTROLS and isinstance(value, str):
        return None if value.strip() else "invalid_media_reference"
    if workflow_input.control in _SELECT_CONTROLS:
        options = _validation_options(workflow_input)
        if options and str(value) not in options:
            return "outside_options"
    if workflow_input.control in _NUMERIC_CONTROLS:
        number = _number_value(value)
        if number is None:
            return "not_finite"
        minimum = workflow_input.validation.get("min")
        maximum = workflow_input.validation.get("max")
        if isinstance(minimum, int | float) and number < minimum:
            return "below_min"
        if isinstance(maximum, int | float) and number > maximum:
            return "above_max"
    return None


def _validation_options(workflow_input: WorkflowInput) -> set[str]:
    raw_options = workflow_input.validation.get("options")
    if not isinstance(raw_options, list):
        return set()
    options: set[str] = set()
    for option in raw_options:
        if isinstance(option, str):
            options.add(option)
        elif isinstance(option, dict):
            value = option.get("value")
            if isinstance(value, str):
                options.add(value)
    return options


def _finite_number(value: Any) -> bool:
    return _number_value(value) is not None


def _number_value(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _user_error(
    *,
    code: _RunUserFixableCode,
    workflow_input: WorkflowInput,
    control: DashboardControl | None,
    value: Any,
    invalid_reason: str | None = None,
    developer_details: dict[str, Any] | None = None,
) -> RunUserFixableError:
    input_type = _friendly_input_type(workflow_input)
    title, user_message = _friendly_copy(code, input_type)
    base_details = {
        "node_id": workflow_input.binding.node_id,
        "input_name": workflow_input.binding.input_name,
        "input_id": workflow_input.id,
        "control_id": control.id if control is not None else None,
        "input_type": input_type,
        "control_type": control.type if control is not None else workflow_input.control,
        "required": True,
        "validation": workflow_input.validation,
    }
    if invalid_reason is not None:
        base_details["invalid_reason"] = invalid_reason
    if developer_details:
        base_details.update(developer_details)
    return RunUserFixableError(
        code=code,
        title=title,
        message="A required workflow input is missing." if code == "missing_required_input" else "A workflow input has an invalid value.",
        user_message=user_message,
        control_id=control.id if control is not None else None,
        input_id=workflow_input.id,
        input_type=input_type,
        developer_details=base_details,
    )


def _friendly_input_type(workflow_input: WorkflowInput) -> str:
    if workflow_input.control == "load_image_mask":
        return "mask_image"
    kind = target_media_kind_for_input(workflow_input)
    if kind is not None:
        return kind
    if workflow_input.control == "textarea":
        return "textarea"
    if workflow_input.control == "string_field":
        return "text"
    if workflow_input.control in _SELECT_CONTROLS:
        return "select"
    if workflow_input.control in _NUMERIC_CONTROLS:
        return "number"
    return workflow_input.control


def _friendly_copy(code: str, input_type: str) -> tuple[str, str]:
    if code == "missing_required_input":
        return {
            "image": ("Missing image", "Please add an image before running this workflow."),
            "mask_image": ("Missing mask image", "Please add a mask image before running this workflow."),
            "audio": ("Missing audio", "Please add an audio file before running this workflow."),
            "video": ("Missing video", "Please add a video before running this workflow."),
            "file": ("Missing file", "Please add a file before running this workflow."),
            "3d": ("Missing 3D file", "Please add a 3D file before running this workflow."),
            "select": ("Missing selection", "Please choose an option before running this workflow."),
            "number": ("Missing value", "Please enter a value before running this workflow."),
            "text": ("Missing prompt", "Please enter text before running this workflow."),
            "textarea": ("Missing prompt", "Please enter text before running this workflow."),
        }.get(input_type, ("Missing input", "Please complete the required input before running this workflow."))
    return {
        "select": ("Choose a valid option", "Please choose one of the available options before running this workflow."),
        "number": ("Check value", "Please enter a valid value before running this workflow."),
        "image": ("Check image", "Please choose a valid image before running this workflow."),
        "mask_image": ("Check mask image", "Please choose a valid mask image before running this workflow."),
        "audio": ("Check audio", "Please choose a valid audio file before running this workflow."),
        "video": ("Check video", "Please choose a valid video before running this workflow."),
        "file": ("Check file", "Please choose a valid file before running this workflow."),
        "3d": ("Check 3D file", "Please choose a valid 3D file before running this workflow."),
    }.get(input_type, ("Check input", "Please correct the highlighted input before running this workflow."))


def _combined_error_text(response_json: Any, response_text: str) -> str:
    parts = [response_text or ""]
    try:
        parts.append(json.dumps(response_json, sort_keys=True, default=str))
    except TypeError:
        parts.append(str(response_json))
    return "\n".join(part for part in parts if part)


def _known_comfyui_node_input(response_json: Any, response_text: str) -> tuple[str | None, str | None]:
    if isinstance(response_json, dict):
        node_errors = response_json.get("node_errors")
        if isinstance(node_errors, dict):
            for node_id, node_error in node_errors.items():
                if _node_error_mentions_missing_load_input(node_error):
                    return str(node_id), _node_error_input_name(node_error)
    match = re.search(r"Load(?:Image|ImageMask|Audio|Video|3D).*?input(?:_name)?['\"]?\s*[:=]\s*['\"]([A-Za-z0-9_ -]+)", response_text, re.IGNORECASE | re.DOTALL)
    if match:
        return None, match.group(1)
    return None, None


def _node_error_mentions_missing_load_input(node_error: Any) -> bool:
    text = _combined_error_text(node_error, str(node_error)).lower()
    return "load" in text and "nonetype" in text and "endswith" in text


def _node_error_input_name(node_error: Any) -> str | None:
    if isinstance(node_error, dict):
        for key in ("input_name", "input"):
            value = node_error.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        errors = node_error.get("errors")
        if isinstance(errors, list):
            for error in errors:
                if isinstance(error, dict):
                    value = error.get("input_name") or error.get("input")
                    if isinstance(value, str) and value.strip():
                        return value.strip()
    return None


def _input_for_node(package: WorkflowPackage, node_id: str | None, input_name: str | None) -> WorkflowInput | None:
    if node_id is None and input_name is None:
        return None
    candidates = package.inputs
    if node_id is not None:
        candidates = [workflow_input for workflow_input in candidates if workflow_input.binding.node_id == node_id]
    if input_name is not None:
        named = [
            workflow_input
            for workflow_input in candidates
            if workflow_input.binding.input_name == input_name or workflow_input.id == input_name
        ]
        if named:
            return named[0]
    for workflow_input in candidates:
        if workflow_input.control in MEDIA_LOAD_CONTROLS:
            return workflow_input
    return candidates[0] if candidates else None


def _first_missing_media_input(package: WorkflowPackage, submitted_inputs: dict[str, Any]) -> WorkflowInput | None:
    for workflow_input in package.inputs:
        if workflow_input.control in MEDIA_LOAD_CONTROLS and _is_missing_required_value(
            workflow_input,
            _effective_input_value(workflow_input, submitted_inputs),
        ):
            return workflow_input
    for workflow_input in package.inputs:
        if workflow_input.control in MEDIA_LOAD_CONTROLS:
            return workflow_input
    return None


def _truncate_marker(value: str) -> str:
    return value[:_RAW_MARKER_LIMIT]
